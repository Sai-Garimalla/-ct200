import os
import tempfile
import pymupdf
import pytest
from app.services.parser import parse_pdf, format_table_as_markdown

@pytest.fixture
def temp_pdf_path():
    paths = []
    def _create():
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        paths.append(path)
        return path
    yield _create
    for p in paths:
        if os.path.exists(p):
            try:
                os.unlink(p)
            except Exception:
                pass

def test_irregular_case_1_empty_pdf(temp_pdf_path):
    """Irregular Case 1: Empty or scanned PDF with zero extractable text."""
    path = temp_pdf_path()
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)  # blank page
    doc.save(path)
    doc.close()

    file_hash, nodes = parse_pdf(path)
    assert isinstance(file_hash, str)
    assert len(file_hash) == 64
    assert isinstance(nodes, list)
    assert len(nodes) == 0

def test_irregular_case_2_skipped_and_jumping_hierarchy(temp_pdf_path):
    """Irregular Case 2: Sections skip levels (e.g. 1.0 directly to 1.5.3.1).
    Ancestor fallback must link to the nearest known ancestor rather than crashing."""
    path = temp_pdf_path()
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    
    # 1.0 Section
    page.insert_text((50, 50), "1.0 Architecture Overview", fontsize=14, fontname="helv")
    page.insert_text((50, 80), "This section describes the overarching architecture of the subsystem.", fontsize=10)
    
    # Skipped section: 1.5.3.1 (intermediate 1.5 and 1.5.3 do not exist)
    page.insert_text((50, 130), "1.5.3.1 Memory Allocation Limit", fontsize=12, fontname="helv")
    page.insert_text((50, 160), "Maximum buffer size must not exceed 256MB.", fontsize=10)
    
    doc.save(path)
    doc.close()

    file_hash, nodes = parse_pdf(path)
    keys = {n.node_key: n for n in nodes}
    
    assert "1.0" in keys
    assert "1.5.3.1" in keys
    
    # Ancestor fallback: parent should be 1.0 because 1.5.3 and 1.5 don't exist
    assert keys["1.5.3.1"].parent_key == "1.0"
    assert "Maximum buffer size" in keys["1.5.3.1"].content

def test_irregular_case_3_unnumbered_headings_and_embedded_lists(temp_pdf_path):
    """Irregular Case 3: Document with prominent unnumbered text and numbered list items.
    Numbered list items must not create bogus heading keys."""
    path = temp_pdf_path()
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    
    # Title
    page.insert_text((50, 50), "System Safety and Operating Guidelines", fontsize=22, fontname="times-bold")
    
    # Section 1
    page.insert_text((50, 100), "1. Safety Warnings", fontsize=14, fontname="helv")
    page.insert_text((50, 130), "Please review the following warnings carefully:", fontsize=10)
    page.insert_text((70, 160), "1. Do not immerse in water.", fontsize=10)
    page.insert_text((70, 180), "2. Do not operate near flammable gases.", fontsize=10)
    
    # Section 2
    page.insert_text((50, 220), "2. Operating Envelope", fontsize=14, fontname="helv")
    page.insert_text((50, 250), "Temperature range is 10 to 40 degrees Celsius.", fontsize=10)
    
    doc.save(path)
    doc.close()

    file_hash, nodes = parse_pdf(path)
    keys = [n.node_key for n in nodes]
    
    # Main sections should be recognized
    assert "1" in keys
    assert "2" in keys
    
    # The list items 1. and 2. inside section 1 must not overwrite section 1 or section 2
    node_1 = next(n for n in nodes if n.node_key == "1")
    assert "Do not immerse in water" in node_1.content
    assert "Do not operate near flammable gases" in node_1.content

def test_irregular_case_4_ragged_table_formatting():
    """Irregular Case 4: Tables with uneven columns, None cells, and whitespace gaps."""
    raw_table = [
        ["Parameter", "Min", "Max", "Notes"],
        ["Voltage", "3.0V", "6.0V"],              # Missing 4th column
        ["Current", None, "500mA", "Peak load"],   # None cell
        ["", "", "", ""],                          # Empty row (skipped)
        ["Temperature", "10C", "40C", "Operating"]
    ]
    formatted = format_table_as_markdown(raw_table)
    lines = formatted.splitlines()
    assert len(lines) == 5  # header + separator + 3 data rows
    assert "| Parameter | Min | Max | Notes |" in lines[0]
    assert "| --- | --- | --- | --- |" in lines[1]
    assert "Peak load" in formatted

def test_irregular_case_5_duplicate_headings(temp_pdf_path):
    """Irregular Case 5: Duplicate headings in a technical document:
    1. Duplicate numbering (two 1.1 sections) must produce distinct node keys (1.1 and 1.1#2) with correct parent 1.
    2. Identical section titles under different parent chapters (Precautions under 1 and Precautions under 2)
       must produce distinct node keys and link to their correct respective parents (1 and 2)."""
    path = temp_pdf_path()
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)

    # Chapter 1 with 1.1 and duplicate 1.1
    page.insert_text((50, 50), "1. Safety Instructions", fontsize=14, fontname="helv")
    page.insert_text((50, 80), "1.1 General Precautions", fontsize=12, fontname="helv")
    page.insert_text((50, 110), "Do not operate in wet environments.", fontsize=10)
    page.insert_text((50, 140), "1.1 General Precautions", fontsize=12, fontname="helv")
    page.insert_text((50, 170), "Keep device away from open flames.", fontsize=10)

    # Chapter 2 with identical title under 2.1
    page.insert_text((50, 210), "2. Maintenance Guide", fontsize=14, fontname="helv")
    page.insert_text((50, 240), "2.1 General Precautions", fontsize=12, fontname="helv")
    page.insert_text((50, 270), "Disconnect power supply before cleaning the device.", fontsize=10)

    doc.save(path)
    doc.close()

    file_hash, nodes = parse_pdf(path)
    nodes_by_key = {n.node_key: n for n in nodes}

    assert "1" in nodes_by_key
    assert "2" in nodes_by_key
    assert "1.1" in nodes_by_key
    assert "1.1#2" in nodes_by_key
    assert "2.1" in nodes_by_key

    # Assert correct parent relationships
    assert nodes_by_key["1.1"].parent_key == "1"
    assert "wet environments" in nodes_by_key["1.1"].content

    assert nodes_by_key["1.1#2"].parent_key == "1"
    assert "open flames" in nodes_by_key["1.1#2"].content

    assert nodes_by_key["2.1"].parent_key == "2"
    assert "power supply" in nodes_by_key["2.1"].content


