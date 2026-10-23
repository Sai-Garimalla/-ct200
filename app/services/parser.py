import re
import hashlib
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
import pymupdf

# Matches section numbering like "1.", "1.2", "2.1.1.1" with optional trailing dot, followed by title
HEADING_PATTERN = re.compile(r"^(\d+(?:\.\d+)*\.?)\s+(.+?)$")

@dataclass
class ParsedNode:
    node_key: str
    parent_key: str | None
    title: str
    node_type: str  # heading, table, section, title
    level: int
    content: str
    content_hash: str
    effective_content: str = ""
    subtree_hash: str = ""
    cross_references: list[str] = field(default_factory=list)
    page: int = 1

def normalize_text(text: str) -> str:
    # Decompose ligatures like \ufb01 ("fi") and \ufb02 ("fl").
    # Windows CP1252 terminal crashes on \ufb01 unless normalized.
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", text)
    # Convert special dashes and quotes to standard ASCII equivalents
    norm = norm.replace("\u2010", "-").replace("\u2011", "-").replace("\u2012", "-")
    norm = norm.replace("\u2013", "-").replace("\u2014", "-")
    norm = norm.replace("\u2018", "'").replace("\u2019", "'")
    norm = norm.replace("\u201c", '"').replace("\u201d", '"')
    norm = norm.replace("\u00a0", " ")
    # Strip non-printable control characters
    norm = "".join(ch for ch in norm if ch == "\n" or unicodedata.category(ch)[0] != "C")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in norm.splitlines()]
    return "\n".join(line for line in lines if line)

def clean_for_hash(text: str) -> str:
    # Collapse all whitespace runs and linebreaks into single spaces.
    # When section 5.3 is added in v2, subsequent sections shift to new pages;
    # collapsing whitespace prevents line-wrap shifts from causing false diffs.
    norm = normalize_text(text)
    return " ".join(norm.split())

def digest(text: str) -> str:
    # Canonical SHA-256 hash of normalized text
    canonical = clean_for_hash(text)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def is_rect_overlap(r1, r2) -> bool:
    # Check if a text block overlaps with a detected table bbox (>50% area)
    x_overlap = max(0, min(r1[2], r2[2]) - max(r1[0], r2[0]))
    y_overlap = max(0, min(r1[3], r2[3]) - max(r1[1], r2[1]))
    overlap_area = x_overlap * y_overlap
    r1_area = (r1[2] - r1[0]) * (r1[3] - r1[1])
    return (r1_area > 0 and (overlap_area / r1_area) > 0.5)

def format_table_as_markdown(table_data: list[list[str | None]]) -> str:
    # Convert PyMuPDF table matrix to clean GitHub Flavored Markdown
    cleaned_rows: list[list[str]] = []
    for row in table_data:
        cleaned_row = [re.sub(r"\s+", " ", normalize_text(cell or "")).strip() for cell in row]
        if any(cleaned_row):
            cleaned_rows.append(cleaned_row)
    if not cleaned_rows:
        return ""
    num_cols = max(len(r) for r in cleaned_rows)
    padded = [r + [""] * (num_cols - len(r)) for r in cleaned_rows]
    header = padded[0]
    separator = ["---"] * num_cols
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |"
    ]
    for row in padded[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)

def extract_cross_references(text: str) -> list[str]:
    # Extracts cited section numbers like "(see 2.1, 4.3)" or "see section 4.1"
    if not text:
        return []
    refs: list[str] = []
    matches = re.findall(r"(?:see\s+(?:sections?\s+)?|\()([0-9]+(?:\.[0-9]+)*(?:\s*,\s*[0-9]+(?:\.[0-9]+)*)*)", text, flags=re.IGNORECASE)
    for m in matches:
        for part in m.split(","):
            cleaned = part.strip().rstrip(".")
            if cleaned and re.match(r"^[0-9]+(?:\.[0-9]+)*$", cleaned) and cleaned not in refs:
                refs.append(cleaned)
    return refs

def rollup_subtree_content(nodes: list[ParsedNode]) -> None:
    # Aggregates child tables, sub-paragraphs, and child sections into parent headings.
    # Solves the issue where parent heading 4.2 has empty direct content and misses its child table.
    # Also computes subtree_hash so staleness flags parents when any child changes.
    children_map = defaultdict(list)
    for n in nodes:
        if n.parent_key:
            children_map[n.parent_key].append(n)

    # Process nodes bottom-up (higher levels first)
    sorted_nodes = sorted(nodes, key=lambda x: x.level, reverse=True)
    
    for n in sorted_nodes:
        kids = children_map.get(n.node_key, [])
        if not kids:
            n.effective_content = n.content
            n.subtree_hash = n.content_hash
        else:
            accumulated = [n.content] if n.content.strip() else []
            for k in kids:
                if k.effective_content.strip():
                    accumulated.append(k.effective_content.strip())
            n.effective_content = "\n\n".join(accumulated) if accumulated else n.content
            n.subtree_hash = digest(n.effective_content)

def parse_pdf(path: str) -> tuple[str, list[ParsedNode]]:
    # Extracts sections, headings, tables, and paragraphs from the PDF.
    # Disambiguates numbered lists, extracts cross-references, and rolls up child tables.
    doc = pymupdf.open(path)
    nodes: list[ParsedNode] = []
    known_heading_keys: list[str] = []
    heading_counts: dict[str, int] = defaultdict(int)
    table_counts_per_parent: dict[str, int] = defaultdict(int)
    
    current_node: ParsedNode | None = None
    accumulated_full_text: list[str] = []

    # First pass: collect font size distribution to compute adaptive relative thresholds
    all_font_sizes = []
    for p in doc:
        for b in p.get_text("dict").get("blocks", []):
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    all_font_sizes.append(s.get("size", 10.0))
    median_font_size = sorted(all_font_sizes)[len(all_font_sizes) // 2] if all_font_sizes else 10.0
    heading_size_threshold = max(11.8, median_font_size * 1.15)

    for page_num in range(1, len(doc) + 1):
        page = doc[page_num - 1]
        
        # 1. Grab tables on this page first
        tab_finder = page.find_tables()
        table_rects = []
        page_items = []  # sorted by y0 to preserve natural top-to-bottom reading order
        
        for tab in getattr(tab_finder, "tables", []):
            table_rects.append(tab.bbox)
            md_table = format_table_as_markdown(tab.extract())
            if md_table:
                page_items.append((tab.bbox[1], "table", (tab.bbox, md_table)))

        # 2. Extract text blocks with font span details
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])

        for b in blocks:
            bbox = b.get("bbox", (0, 0, 0, 0))
            # Don't duplicate text that's already inside a table
            if any(is_rect_overlap(bbox, tr) for tr in table_rects):
                continue
            if "lines" not in b:
                continue
            page_items.append((bbox[1], "block", b))

        # Sort everything on the page vertically
        page_items.sort(key=lambda item: item[0])

        for _, item_type, data in page_items:
            if item_type == "table":
                _, md_table = data
                accumulated_full_text.append(md_table)
                table_parent = current_node.node_key if current_node else "doc"
                table_counts_per_parent[table_parent] += 1
                t_idx = table_counts_per_parent[table_parent]
                table_key = f"{table_parent}.table.{t_idx}"
                p_level = (current_node.level + 1) if current_node else 1
                
                first_line = md_table.splitlines()[0].replace("|", "").strip() if md_table else "Table"
                table_title = f"Table ({first_line[:40]}...)" if len(first_line) > 40 else f"Table: {first_line}"
                
                table_node = ParsedNode(
                    node_key=table_key,
                    parent_key=current_node.node_key if current_node else None,
                    title=table_title,
                    node_type="table",
                    level=p_level,
                    content=md_table,
                    content_hash=digest(md_table),
                    effective_content=md_table,
                    subtree_hash=digest(md_table),
                    cross_references=extract_cross_references(md_table),
                    page=page_num
                )
                nodes.append(table_node)
                continue

            # Process text blocks
            b = data
            block_lines: list[str] = []
            max_size = 0.0
            is_bold = False

            for line in b["lines"]:
                line_spans = line.get("spans", [])
                line_text = "".join(s.get("text", "") for s in line_spans).strip()
                if not line_text:
                    continue
                block_lines.append(line_text)
                for s in line_spans:
                    font_size = s.get("size", 10.0)
                    font_name = s.get("font", "").lower()
                    flags = s.get("flags", 0)
                    if font_size > max_size:
                        max_size = font_size
                    if "bold" in font_name or (flags & 2 != 0) or (flags & 16 != 0):
                        is_bold = True

            raw_block_text = " ".join(block_lines).strip()
            block_text = normalize_text(raw_block_text)
            if not block_text:
                continue

            accumulated_full_text.append(block_text)

            heading_match = HEADING_PATTERN.match(block_text)
            is_likely_heading = False
            
            if heading_match:
                raw_num = heading_match.group(1)
                num_part = raw_num.rstrip(".")
                title_part = heading_match.group(2).strip()
                
                # Multi-level numbers like 1.1, 2.1, 2.1.1.1 are definitely headings
                if "." in num_part:
                    is_likely_heading = True
                # Single digit numbers (1., 2.) must be bold or exceed adaptive heading size
                elif is_bold or max_size >= heading_size_threshold:
                    if not (title_part.count(":") >= 1 and not is_bold and max_size < heading_size_threshold):
                        is_likely_heading = True

            # Grab document title on page 1
            if page_num == 1 and max_size >= 20.0 and is_bold and not heading_match and not current_node:
                title_key = "title"
                title_node = ParsedNode(
                    node_key=title_key,
                    parent_key=None,
                    title=block_text,
                    node_type="title",
                    level=0,
                    content=block_text,
                    content_hash=digest(block_text),
                    effective_content=block_text,
                    subtree_hash=digest(block_text),
                    cross_references=extract_cross_references(block_text),
                    page=page_num
                )
                nodes.append(title_node)
                current_node = title_node
                continue

            if is_likely_heading and heading_match:
                raw_num = heading_match.group(1)
                sec_number = raw_num.rstrip(".")
                has_trailing_dot = raw_num.endswith(".")
                sec_title = heading_match.group(2).strip()
                level = sec_number.count(".") + 1
                
                # Preserve trailing dot in title if originally present (e.g. "1. Device Overview")
                formatted_title = f"{sec_number}. {sec_title}" if has_trailing_dot else f"{sec_number} {sec_title}"
                
                # Backtrack through known headings to find nearest parent (handles 2.1.1.1 -> 2.1)
                parent_key = None
                parts = sec_number.split(".")
                for cut in range(len(parts) - 1, 0, -1):
                    cand = ".".join(parts[:cut])
                    matched_parent = None
                    for k in reversed(known_heading_keys):
                        base_k = k.split("#")[0]
                        if base_k == cand or base_k == f"{cand}.0":
                            matched_parent = k
                            break
                    if matched_parent:
                        parent_key = matched_parent
                        break

                # Disambiguate duplicate heading numbers if present
                heading_counts[sec_number] += 1
                final_node_key = sec_number if heading_counts[sec_number] == 1 else f"{sec_number}#{heading_counts[sec_number]}"

                new_node = ParsedNode(
                    node_key=final_node_key,
                    parent_key=parent_key,
                    title=formatted_title,
                    node_type="heading",
                    level=level,
                    content="",
                    content_hash=digest(""),
                    effective_content="",
                    subtree_hash=digest(""),
                    cross_references=[],
                    page=page_num
                )
                nodes.append(new_node)
                known_heading_keys.append(final_node_key)
                current_node = new_node
            else:
                # Regular paragraph text
                if current_node is None:
                    current_node = ParsedNode(
                        node_key="preamble",
                        parent_key=None,
                        title="Preamble",
                        node_type="section",
                        level=1,
                        content="",
                        content_hash=digest(""),
                        effective_content="",
                        subtree_hash=digest(""),
                        cross_references=[],
                        page=page_num
                    )
                    nodes.append(current_node)

                # Append lines cleanly
                if current_node.content:
                    if not current_node.content.endswith((".", ":", "!", "?", "\n")):
                        current_node.content += " " + block_text
                    else:
                        current_node.content += "\n" + block_text
                else:
                    current_node.content = block_text
                current_node.content_hash = digest(current_node.content)

                # Extract and merge cross references from paragraph text
                found_refs = extract_cross_references(block_text)
                for r in found_refs:
                    if r not in current_node.cross_references:
                        current_node.cross_references.append(r)

    # Post-processing pass: rollup child content and compute subtree hashes
    rollup_subtree_content(nodes)

    full_text_str = "\n".join(accumulated_full_text)
    overall_hash = digest(full_text_str) if full_text_str else digest("")
    return overall_hash, nodes
