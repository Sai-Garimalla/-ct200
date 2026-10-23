from pathlib import Path
import pytest
from app.services.parser import (
    digest,
    normalize_text,
    clean_for_hash,
    HEADING_PATTERN,
    parse_pdf
)

BASE_DIR = Path(__file__).resolve().parent.parent
PDF_V1 = str(BASE_DIR / "sample_data_ct200_manual.pdf")
PDF_V2 = str(BASE_DIR / "sample_data_ct200_manual_v2.pdf")

def test_heading_numbering_levels():
    m1 = HEADING_PATTERN.match("1.2 Intended Use")
    assert m1 is not None
    assert m1.group(1).rstrip(".") == "1.2"
    assert m1.group(2) == "Intended Use"

    m2 = HEADING_PATTERN.match("2.1.1.1 Battery Life Under Typical Use")
    assert m2 is not None
    assert m2.group(1).rstrip(".").count(".") == 3

def test_hash_is_stable_and_content_sensitive():
    assert digest("same") == digest("same")
    assert digest("same") != digest("different")

def test_normalization_handles_ligatures_and_dashes():
    raw = "Speci\ufb01cation \u2013 CardioTrack CT\u2011200"
    normalized = normalize_text(raw)
    assert "Specification" in normalized
    assert "-" in normalized

def test_clean_for_hash_is_invariant_to_linebreaks():
    t1 = "This is a requirement on line 1\nand line 2."
    t2 = "This is a requirement on line 1 and line 2."
    assert clean_for_hash(t1) == clean_for_hash(t2)
    assert digest(t1) == digest(t2)

def test_sample_v1_pdf_parsing():
    file_hash, nodes = parse_pdf(PDF_V1)
    assert len(file_hash) == 64
    assert len(nodes) >= 25

    keys = {n.node_key for n in nodes}
    for expected in ["1", "1.1", "1.2", "2", "2.1", "2.1.1.1", "2.2", "3", "3.1", "3.2", "3.3", "4", "4.1", "4.2", "4.3", "5", "6", "7", "8"]:
        assert expected in keys, f"Missing section {expected} in parsed nodes"

    node_3_3 = next(n for n in nodes if n.node_key == "3.3")
    assert "Normal: systolic < 120" in node_3_3.content
    assert "Hypertensive Crisis" in node_3_3.content

    node_deep = next(n for n in nodes if n.node_key == "2.1.1.1")
    assert node_deep.parent_key == "2.1"

    table_nodes = [n for n in nodes if n.node_type == "table"]
    assert len(table_nodes) >= 2
    assert any("Parameter" in n.content for n in table_nodes)
    assert any("Code" in n.content and "E1" in n.content for n in table_nodes)

def test_ocr_fallback_handler():
    import pymupdf
    from app.services.parser import extract_page_dict_with_ocr_fallback
    doc = pymupdf.open(PDF_V1)
    page_dict = extract_page_dict_with_ocr_fallback(doc[0])
    assert "blocks" in page_dict
    assert len(page_dict["blocks"]) > 0

