import re
import hashlib
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
import pymupdf

# Matches section numbering like "1.", "1.2", "2.1.1.1", followed by a title
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
    page: int

def normalize_text(text: str) -> str:
    """Cleans up raw PDF text strings. Handles typographic ligatures ('fi', 'fl'),
    standardizes various Unicode hyphens and quotes, and trims trailing whitespace."""
    if not text:
        return ""
    # Decompose ligatures like \ufb01 -> 'fi'
    norm = unicodedata.normalize("NFKD", text)
    # Map special dashes and quotes to standard ASCII equivalents
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
    """Collapses line-breaks and whitespace into single spaces so pagination shifts
    across document revisions do not cause false-positive diff changes."""
    norm = normalize_text(text)
    return " ".join(norm.split())

def digest(text: str) -> str:
    """Computes a SHA-256 hash over the canonical text string."""
    canonical = clean_for_hash(text)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def is_rect_overlap(r1, r2) -> bool:
    """Helper to detect if a text block falls inside an extracted table bounding box."""
    x_overlap = max(0, min(r1[2], r2[2]) - max(r1[0], r2[0]))
    y_overlap = max(0, min(r1[3], r2[3]) - max(r1[1], r2[1]))
    overlap_area = x_overlap * y_overlap
    r1_area = (r1[2] - r1[0]) * (r1[3] - r1[1])
    return (r1_area > 0 and (overlap_area / r1_area) > 0.5)

def format_table_as_markdown(table_data: list[list[str | None]]) -> str:
    """Converts a raw matrix from PyMuPDF table finder into a clean Markdown table."""
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

def parse_pdf(path: str) -> tuple[str, list[ParsedNode]]:
    """Extracts hierarchical sections, headings, tables, and paragraphs from the PDF.
    Uses typography metadata (font size, bold weight) to avoid mistaking numbered
    list items for headings."""
    doc = pymupdf.open(path)
    nodes: list[ParsedNode] = []
    known_heading_keys: list[str] = []
    heading_counts: dict[str, int] = defaultdict(int)
    table_counts_per_parent: dict[str, int] = defaultdict(int)
    
    current_node: ParsedNode | None = None
    accumulated_full_text: list[str] = []

    for page_num in range(1, len(doc) + 1):
        page = doc[page_num - 1]
        
        # Detect any tables on the current page first
        tab_finder = page.find_tables()
        table_rects = []
        page_items = []  # sorted by y0 to preserve natural top-to-bottom reading order
        
        for tab in getattr(tab_finder, "tables", []):
            table_rects.append(tab.bbox)
            md_table = format_table_as_markdown(tab.extract())
            if md_table:
                page_items.append((tab.bbox[1], "table", (tab.bbox, md_table)))

        # Extract text blocks and collect typography details
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])

        for b in blocks:
            bbox = b.get("bbox", (0, 0, 0, 0))
            # Skip blocks that overlap with tables so we don't duplicate text
            if any(is_rect_overlap(bbox, tr) for tr in table_rects):
                continue
            if "lines" not in b:
                continue
            page_items.append((bbox[1], "block", b))

        # Sort both text blocks and tables by vertical coordinate
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
                    # Check if font indicates bold styling
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
                num_part = heading_match.group(1).rstrip(".")
                title_part = heading_match.group(2).strip()
                
                # If there are sub-levels like 1.1, 2.1, 2.1.1.1, it's definitely a heading
                if "." in num_part:
                    is_likely_heading = True
                # If it's a single digit (like '1', '2'), verify it has bold styling or heading size
                # to prevent list items (e.g. '1. Normal: systolic...') from being treated as chapters
                elif is_bold or max_size >= 12.0:
                    if not (title_part.count(":") >= 1 and not is_bold and max_size < 12.0):
                        is_likely_heading = True

            # Detect main document title on the first page
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
                    page=page_num
                )
                nodes.append(title_node)
                current_node = title_node
                continue

            if is_likely_heading and heading_match:
                sec_number = heading_match.group(1).rstrip(".")
                sec_title = heading_match.group(2).strip()
                level = sec_number.count(".") + 1
                
                # Look up parent by walking backwards through known headings for nearest matching ancestor
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

                heading_counts[sec_number] += 1
                final_node_key = sec_number if heading_counts[sec_number] == 1 else f"{sec_number}#{heading_counts[sec_number]}"

                new_node = ParsedNode(
                    node_key=final_node_key,
                    parent_key=parent_key,
                    title=f"{sec_number} {sec_title}",
                    node_type="heading",
                    level=level,
                    content="",
                    content_hash=digest(""),
                    page=page_num
                )
                nodes.append(new_node)
                known_heading_keys.append(final_node_key)
                current_node = new_node
            else:
                # Regular paragraph content
                if current_node is None:
                    current_node = ParsedNode(
                        node_key="preamble",
                        parent_key=None,
                        title="Preamble",
                        node_type="section",
                        level=1,
                        content="",
                        content_hash=digest(""),
                        page=page_num
                    )
                    nodes.append(current_node)

                # Merge paragraph lines cleanly
                if current_node.content:
                    if not current_node.content.endswith((".", ":", "!", "?", "\n")):
                        current_node.content += " " + block_text
                    else:
                        current_node.content += "\n" + block_text
                else:
                    current_node.content = block_text
                current_node.content_hash = digest(current_node.content)

    full_text_str = "\n".join(accumulated_full_text)
    overall_hash = digest(full_text_str) if full_text_str else digest("")
    return overall_hash, nodes
