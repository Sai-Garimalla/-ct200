"""Section Matcher Service

Provides deterministic title, content, and structural matching of document sections
across revisions and versions, handling renumbering, title edits, content modifications,
Unicode normalization differences, and additions/deletions.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional


def normalize_for_matching(text: Optional[str]) -> str:
    """Normalizes string for comparison: NFKD Unicode decomposition (ligatures -> ASCII),
    stripping non-alphanumeric punctuation, lowercasing, and normalizing whitespace.
    """
    if not text:
        return ""
    # Unicode NFKD decomposes ligatures like \ufb01 -> fi
    norm = unicodedata.normalize("NFKD", text)
    # Convert curly quotes and typographic dashes to standard ASCII
    norm = norm.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    norm = norm.replace("–", "-").replace("—", "-")
    # Lowercase and standardize whitespace
    norm = norm.lower()
    norm = re.sub(r"[^\w\s\-\.]", " ", norm)
    return re.sub(r"\s+", " ", norm).strip()


def strip_leading_section_number(title: str) -> str:
    """Strips leading numerical section prefixes like '3.2 ' or '1.1.1 ' from titles
    so that section renumbering does not penalize title similarity comparisons.
    """
    if not title:
        return ""
    # Remove leading numbering like '1. ', '2.1.1 ', '4.2.table.1 ', etc.
    cleaned = re.sub(r"^[0-9]+(\.[0-9]+)*(\.table\.[0-9]+)?[\.\s\:\-]*", "", title.strip())
    return cleaned.strip() or title.strip()


def title_similarity(t1: str, t2: str) -> float:
    """Calculates similarity between two section titles, invariant to leading section numbers
    and Unicode ligatures.
    """
    n1 = normalize_for_matching(strip_leading_section_number(t1))
    n2 = normalize_for_matching(strip_leading_section_number(t2))
    if not n1 and not n2:
        return 1.0
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0
    return SequenceMatcher(None, n1, n2).ratio()


def content_similarity(c1: Optional[str], c2: Optional[str]) -> float:
    """Calculates text content similarity using a combination of token Jaccard similarity
    and SequenceMatcher ratio. Returns 1.0 if both are empty.
    """
    n1 = normalize_for_matching(c1)
    n2 = normalize_for_matching(c2)
    if not n1 and not n2:
        return 1.0
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0

    # Token-level Jaccard similarity
    tokens1 = set(n1.split())
    tokens2 = set(n2.split())
    if not tokens1 and not tokens2:
        return 1.0
    intersection = tokens1 & tokens2
    union = tokens1 | tokens2
    jaccard = len(intersection) / len(union) if union else 0.0

    # For short strings, SequenceMatcher is very accurate
    if len(n1) < 200 and len(n2) < 200:
        seq = SequenceMatcher(None, n1, n2).ratio()
        return 0.5 * jaccard + 0.5 * seq

    return jaccard


@dataclass
class SectionMatch:
    from_key: str
    to_key: str
    from_title: str
    to_title: str
    title_similarity: float
    content_similarity: float
    composite_score: float
    match_type: str  # "exact", "content_modified", "renamed", "renumbered", "renamed_and_modified"
    is_renumbered: bool
    is_modified: bool
    from_node_id: Optional[int] = None
    to_node_id: Optional[int] = None


@dataclass
class MatchReport:
    matches: list[SectionMatch] = field(default_factory=list)
    added_keys: list[str] = field(default_factory=list)
    removed_keys: list[str] = field(default_factory=list)
    unmodified_keys: list[str] = field(default_factory=list)
    modified_keys: list[str] = field(default_factory=list)

    def get_match_for_from_key(self, from_key: str) -> Optional[SectionMatch]:
        for m in self.matches:
            if m.from_key == from_key:
                return m
        return None

    def get_match_for_to_key(self, to_key: str) -> Optional[SectionMatch]:
        for m in self.matches:
            if m.to_key == to_key:
                return m
        return None


def _get_node_attr(node: Any, attr: str, default: Any = "") -> Any:
    if isinstance(node, dict):
        return node.get(attr, default)
    return getattr(node, attr, default)


def match_document_sections(from_nodes: list[Any], to_nodes: list[Any]) -> MatchReport:
    """Matches sections across two document revisions using a multi-pass deterministic
    matching algorithm:
    
    1. Exact Key & Title Matches: Sections sharing identical node_keys and titles.
    2. Exact Key with Modified Content: Same key, title unchanged or slightly edited, content altered.
    3. Renumbered Sections: Section numbers changed (e.g. 2.1 -> 2.2), but title and content match.
    4. Renamed Sections: Section keys match and content is identical/similar, but title was edited.
    5. Unmatched Nodes: Remaining to_nodes are added; remaining from_nodes are removed.
    
    Prevents false matches between unrelated sections with short identical boilerplate.
    """
    report = MatchReport()

    unmatched_from: dict[str, Any] = {}
    for n in from_nodes:
        key = _get_node_attr(n, "node_key")
        unmatched_from[key] = n

    unmatched_to: dict[str, Any] = {}
    for n in to_nodes:
        key = _get_node_attr(n, "node_key")
        unmatched_to[key] = n

    matched_pairs: list[SectionMatch] = []

    # ------------------------------------------------------------------------
    # Pass 1: Identical Key & Strong Title Similarity (>= 0.85)
    # ------------------------------------------------------------------------
    common_keys = sorted(set(unmatched_from.keys()) & set(unmatched_to.keys()))
    for key in common_keys:
        fn = unmatched_from[key]
        tn = unmatched_to[key]
        f_title = _get_node_attr(fn, "title", "")
        t_title = _get_node_attr(tn, "title", "")
        t_sim = title_similarity(f_title, t_title)

        if t_sim >= 0.85:
            f_content = _get_node_attr(fn, "effective_content") or _get_node_attr(fn, "content", "")
            t_content = _get_node_attr(tn, "effective_content") or _get_node_attr(tn, "content", "")
            c_sim = content_similarity(f_content, t_content)

            f_hash = _get_node_attr(fn, "subtree_hash") or _get_node_attr(fn, "content_hash", "")
            t_hash = _get_node_attr(tn, "subtree_hash") or _get_node_attr(tn, "content_hash", "")

            # If hashes match or content is practically identical
            is_modified = (f_hash != t_hash) if (f_hash and t_hash) else (c_sim < 0.98)
            match_type = "content_modified" if is_modified else "exact"

            score = 0.5 * t_sim + 0.5 * c_sim
            match = SectionMatch(
                from_key=key,
                to_key=key,
                from_title=f_title,
                to_title=t_title,
                title_similarity=t_sim,
                content_similarity=c_sim,
                composite_score=score,
                match_type=match_type,
                is_renumbered=False,
                is_modified=is_modified,
                from_node_id=_get_node_attr(fn, "id", None),
                to_node_id=_get_node_attr(tn, "id", None)
            )
            matched_pairs.append(match)
            del unmatched_from[key]
            del unmatched_to[key]

    # ------------------------------------------------------------------------
    # Pass 2: Renumbered Sections (Different Keys, but Identical/High Title & Content)
    # ------------------------------------------------------------------------
    candidate_matches = []
    for f_key, fn in unmatched_from.items():
        f_title = _get_node_attr(fn, "title", "")
        f_content = _get_node_attr(fn, "effective_content") or _get_node_attr(fn, "content", "")
        f_norm_title = normalize_for_matching(strip_leading_section_number(f_title))

        # Do not match empty sections without meaningful titles
        if len(f_norm_title) < 3:
            continue

        for t_key, tn in unmatched_to.items():
            t_title = _get_node_attr(tn, "title", "")
            t_content = _get_node_attr(tn, "effective_content") or _get_node_attr(tn, "content", "")

            t_sim = title_similarity(f_title, t_title)
            # Require strong title similarity to consider renumbering
            if t_sim < 0.85:
                continue

            c_sim = content_similarity(f_content, t_content)
            
            # Avoid matching unrelated empty/very short sections just because titles are generic
            # If both have short text, title similarity must be >= 0.95
            f_len = len(f_content.strip())
            t_len = len(t_content.strip())
            if f_len < 20 and t_len < 20 and t_sim < 0.95:
                continue

            composite = 0.6 * t_sim + 0.4 * c_sim
            if composite >= 0.75:
                candidate_matches.append((composite, f_key, t_key, t_sim, c_sim, fn, tn))

    # Sort descending by composite score, tie-break by key for determinism
    candidate_matches.sort(key=lambda x: (x[0], x[3], x[4], x[1], x[2]), reverse=True)

    assigned_from = set()
    assigned_to = set()
    for comp, f_key, t_key, t_sim, c_sim, fn, tn in candidate_matches:
        if f_key in assigned_from or t_key in assigned_to:
            continue
        if f_key not in unmatched_from or t_key not in unmatched_to:
            continue

        f_hash = _get_node_attr(fn, "subtree_hash") or _get_node_attr(fn, "content_hash", "")
        t_hash = _get_node_attr(tn, "subtree_hash") or _get_node_attr(tn, "content_hash", "")
        is_mod = (f_hash != t_hash) if (f_hash and t_hash) else (c_sim < 0.98)

        match = SectionMatch(
            from_key=f_key,
            to_key=t_key,
            from_title=_get_node_attr(fn, "title", ""),
            to_title=_get_node_attr(tn, "title", ""),
            title_similarity=t_sim,
            content_similarity=c_sim,
            composite_score=comp,
            match_type="renumbered" if not is_mod else "renumbered_and_modified",
            is_renumbered=True,
            is_modified=is_mod,
            from_node_id=_get_node_attr(fn, "id", None),
            to_node_id=_get_node_attr(tn, "id", None)
        )
        matched_pairs.append(match)
        assigned_from.add(f_key)
        assigned_to.add(t_key)

    for k in assigned_from:
        del unmatched_from[k]
    for k in assigned_to:
        del unmatched_to[k]

    # ------------------------------------------------------------------------
    # Pass 3: Renamed Sections with Same Key or High Content Overlap
    # ------------------------------------------------------------------------
    # If same key remains in both, check if content is substantial and matches
    remaining_common = sorted(set(unmatched_from.keys()) & set(unmatched_to.keys()))
    for key in remaining_common:
        fn = unmatched_from[key]
        tn = unmatched_to[key]
        f_content = _get_node_attr(fn, "effective_content") or _get_node_attr(fn, "content", "")
        t_content = _get_node_attr(tn, "effective_content") or _get_node_attr(tn, "content", "")

        # Require at least 25 characters of content to avoid matching empty nodes as renamed
        if len(f_content.strip()) >= 25 and len(t_content.strip()) >= 25:
            c_sim = content_similarity(f_content, t_content)
            f_title = _get_node_attr(fn, "title", "")
            t_title = _get_node_attr(tn, "title", "")
            t_sim = title_similarity(f_title, t_title)

            if c_sim >= 0.70:
                match = SectionMatch(
                    from_key=key,
                    to_key=key,
                    from_title=f_title,
                    to_title=t_title,
                    title_similarity=t_sim,
                    content_similarity=c_sim,
                    composite_score=0.3 * t_sim + 0.7 * c_sim,
                    match_type="renamed" if c_sim >= 0.98 else "renamed_and_modified",
                    is_renumbered=False,
                    is_modified=(c_sim < 0.98),
                    from_node_id=_get_node_attr(fn, "id", None),
                    to_node_id=_get_node_attr(tn, "id", None)
                )
                matched_pairs.append(match)
                del unmatched_from[key]
                del unmatched_to[key]

    # ------------------------------------------------------------------------
    # Pass 4: Populate Unmodified, Modified, Added, Removed Lists
    # ------------------------------------------------------------------------
    matched_pairs.sort(key=lambda m: (m.from_key, m.to_key))
    report.matches = matched_pairs

    report.unmodified_keys = sorted([m.from_key for m in matched_pairs if not m.is_modified and not m.is_renumbered])
    report.modified_keys = sorted([m.from_key for m in matched_pairs if m.is_modified or m.is_renumbered])
    report.added_keys = sorted(list(unmatched_to.keys()))
    report.removed_keys = sorted(list(unmatched_from.keys()))

    return report
