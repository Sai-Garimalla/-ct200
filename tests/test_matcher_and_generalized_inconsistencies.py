import pytest
from app.models import DocumentNode
from app.services.matcher import (
    title_similarity,
    content_similarity,
    normalize_for_matching,
    match_document_sections,
    strip_leading_section_number,
)
from app.services.inconsistency_detector import (
    detect_document_inconsistencies,
    extract_timing_statements_from_node,
    are_actions_equivalent,
)


# ============================================================================
# Section Matching Unit Tests (Addressing Item 3 Edge Cases)
# ============================================================================

def test_matcher_identical_titles_different_content():
    """Identical title in both versions, but content modified in v2."""
    v1 = [DocumentNode(id=1, node_key="3.2", title="Inflation Dynamics", content="Target pressure is 180 mmHg.")]
    v2 = [DocumentNode(id=2, node_key="3.2", title="Inflation Dynamics", content="Target pressure updated to 190 mmHg.")]
    report = match_document_sections(v1, v2)
    assert len(report.matches) == 1
    m = report.matches[0]
    assert m.from_key == "3.2" and m.to_key == "3.2"
    assert m.is_modified is True
    assert m.match_type == "content_modified"
    assert "3.2" in report.modified_keys


def test_matcher_slightly_changed_titles():
    """Title slightly rephrased across revisions while content remains identical."""
    v1 = [DocumentNode(id=1, node_key="2.1", title="2.1 General Specifications", content="Dimensions: 140 x 110 x 65 mm.")]
    v2 = [DocumentNode(id=2, node_key="2.1", title="2.1 General Device Specifications", content="Dimensions: 140 x 110 x 65 mm.")]
    report = match_document_sections(v1, v2)
    assert len(report.matches) == 1
    m = report.matches[0]
    assert m.from_key == "2.1" and m.to_key == "2.1"
    assert m.title_similarity >= 0.85
    assert m.content_similarity >= 0.99
    assert m.is_modified is False


def test_matcher_renumbered_sections():
    """Section number shifted (e.g. 2.1 became 2.2), but title and content are identical."""
    v1 = [DocumentNode(id=1, node_key="2.1", title="2.1 General Specifications", content="Operating temperature 10 to 40 C.")]
    v2 = [
        DocumentNode(id=2, node_key="2.1", title="2.1 Preliminary Setup", content="Unpack the monitor carefully."),
        DocumentNode(id=3, node_key="2.2", title="2.2 General Specifications", content="Operating temperature 10 to 40 C.")
    ]
    report = match_document_sections(v1, v2)
    m_spec = report.get_match_for_from_key("2.1")
    assert m_spec is not None
    assert m_spec.to_key == "2.2"
    assert m_spec.is_renumbered is True
    assert "2.1" in report.added_keys  # The new Preliminary Setup section in v2


def test_matcher_identical_text_under_different_headings_not_falsely_matched():
    """Two completely unrelated sections with identical short boilerplate disclaimer text."""
    v1 = [DocumentNode(id=1, node_key="1.2", title="1.2 Safety Warnings", content="Do not immerse device in water.")]
    v2 = [
        DocumentNode(id=2, node_key="1.2", title="1.2 Safety Warnings", content="Updated comprehensive warning."),
        DocumentNode(id=3, node_key="6.3", title="6.3 Cleaning and Maintenance", content="Do not immerse device in water.")
    ]
    report = match_document_sections(v1, v2)
    m = report.get_match_for_from_key("1.2")
    assert m is not None
    assert m.to_key == "1.2"
    assert m.to_key != "6.3"  # Should NOT match to 6.3 despite identical short text


def test_matcher_empty_or_short_sections_disambiguation():
    """Empty chapter headings should be matched by title, not collapsed into arbitrary empty nodes."""
    v1 = [
        DocumentNode(id=1, node_key="1", title="1. Device Overview", content=""),
        DocumentNode(id=2, node_key="4", title="4. Alarms and Safety Behavior", content="")
    ]
    v2 = [
        DocumentNode(id=3, node_key="1", title="1. Device Overview", content=""),
        DocumentNode(id=4, node_key="4", title="4. Alarms and Safety Behavior", content="")
    ]
    report = match_document_sections(v1, v2)
    assert len(report.matches) == 2
    assert report.get_match_for_from_key("1").to_key == "1"
    assert report.get_match_for_from_key("4").to_key == "4"


def test_matcher_unicode_normalization_differences():
    """Decomposes OpenType ligatures (\ufb01 -> fi) and curved quotes for seamless matching."""
    v1 = [DocumentNode(id=1, node_key="2.1", title="General Speci\ufb01cations", content="In\ufb02ation mechanism: automatic.")]
    v2 = [DocumentNode(id=2, node_key="2.1", title="General Specifications", content="Inflation mechanism: automatic.")]
    report = match_document_sections(v1, v2)
    assert len(report.matches) == 1
    m = report.matches[0]
    assert m.title_similarity == 1.0
    assert m.content_similarity == 1.0
    assert m.is_modified is False


def test_matcher_added_and_deleted_sections():
    """Detects purely added and deleted sections across versions."""
    v1 = [
        DocumentNode(id=1, node_key="1.1", title="Intended Use", content="Adult and pediatric."),
        DocumentNode(id=2, node_key="5.1", title="Archived Feature", content="Legacy serial transfer.")
    ]
    v2 = [
        DocumentNode(id=3, node_key="1.1", title="Intended Use", content="Adult and pediatric."),
        DocumentNode(id=4, node_key="5.3", title="Data Export", content="BLE wireless sync.")
    ]
    report = match_document_sections(v1, v2)
    assert "5.3" in report.added_keys
    assert "5.1" in report.removed_keys
    assert "1.1" in report.unmodified_keys


# ============================================================================
# Generalized Inconsistency Detector Tests (Addressing Item 4 Requirements)
# ============================================================================

def test_inconsistency_existing_4_1_vs_4_2_timing_contradiction():
    """The original planted contradiction: Section 4.1 (2.0s valve vent) vs 4.2 Table E3 (1.5s auto-deflation)."""
    nodes = [
        DocumentNode(
            node_key="4.1",
            title="4.1 Overpressure Protection",
            content="An independent electromechanical relief valve automatically vents the cuff to atmosphere within 2.0 seconds if pressure exceeds 300 mmHg."
        ),
        DocumentNode(
            node_key="4.2",
            title="4.2 Error Codes",
            content="",
            effective_content="| E3 | Overpressure detected (> 300 mmHg). Auto-deflation triggered; valve vents within 1.5 seconds. | Power-cycle device. |"
        )
    ]
    report = detect_document_inconsistencies(version_id=2, document_key="ct200", nodes=nodes)
    timing_issues = [i for i in report.issues if i.category == "timing_contradiction"]
    assert len(timing_issues) == 1
    issue = timing_issues[0]
    assert "4.1" in issue.sections_involved
    assert "4.2" in issue.sections_involved
    assert "2.0s vs 1.5s" in issue.title or "1.5s vs 2.0s" in issue.title


def test_inconsistency_same_contradiction_different_section_numbers():
    """Same timing contradiction occurring in arbitrary section numbers (e.g. 2.4 vs 8.7)."""
    nodes = [
        DocumentNode(
            node_key="2.4",
            title="Pneumatic Safety Mechanism",
            content="Emergency relief valve vents cuff pressure within 5.0 seconds under overpressure fault."
        ),
        DocumentNode(
            node_key="8.7",
            title="Fault Code Reference",
            content="Overpressure error: system auto-deflation vents within 2.5 seconds."
        )
    ]
    report = detect_document_inconsistencies(version_id=1, document_key="ct200", nodes=nodes)
    timing_issues = [i for i in report.issues if i.category == "timing_contradiction"]
    assert len(timing_issues) == 1
    issue = timing_issues[0]
    assert "2.4" in issue.sections_involved
    assert "8.7" in issue.sections_involved
    assert issue.severity == "high"


def test_inconsistency_contradiction_different_section_titles():
    """Contradiction between sections with custom/unusual titles describing the same motor action."""
    nodes = [
        DocumentNode(
            node_key="A.1",
            title="Operator Manual - Measurement Abort",
            content="When user presses Stop button, pump motor stops within 1.0 seconds."
        ),
        DocumentNode(
            node_key="B.4",
            title="Engineering Service Notes",
            content="Upon abort signal, pump motor stops within 3.5 seconds."
        )
    ]
    report = detect_document_inconsistencies(version_id=1, document_key="ct200", nodes=nodes)
    timing_issues = [i for i in report.issues if i.category == "timing_contradiction"]
    assert len(timing_issues) == 1
    assert "A.1" in timing_issues[0].sections_involved
    assert "B.4" in timing_issues[0].sections_involved


def test_inconsistency_unrelated_actions_different_timings_no_contradiction():
    """Two different actions with different time limits must NOT be flagged as contradictions."""
    nodes = [
        DocumentNode(
            node_key="3.1",
            title="Display Power Management",
            content="Device screen backlight turns off and powers down within 30 seconds of inactivity."
        ),
        DocumentNode(
            node_key="4.1",
            title="Overpressure Relief",
            content="Emergency relief valve automatically vents the cuff within 2.0 seconds."
        ),
        DocumentNode(
            node_key="5.2",
            title="Data Communication",
            content="Bluetooth module transmits measurement record within 5.0 seconds."
        )
    ]
    report = detect_document_inconsistencies(version_id=1, document_key="ct200", nodes=nodes)
    timing_issues = [i for i in report.issues if i.category == "timing_contradiction"]
    # Screen standby (30s) vs valve vent (2s) vs bluetooth transmission (5s) are unrelated actions!
    assert len(timing_issues) == 0


def test_inconsistency_multiple_timing_statements_same_section():
    """A section with multiple timing statements compared against another section."""
    nodes = [
        DocumentNode(
            node_key="2.2",
            title="System Operation",
            content=(
                "Emergency relief valve vents within 2.0 seconds during alarm. "
                "Additionally, Bluetooth transmits sync packets within 5.0 seconds."
            )
        ),
        DocumentNode(
            node_key="7.1",
            title="Diagnostics",
            content="Overpressure relief valve vents within 4.0 seconds."
        )
    ]
    report = detect_document_inconsistencies(version_id=1, document_key="ct200", nodes=nodes)
    timing_issues = [i for i in report.issues if i.category == "timing_contradiction"]
    # Only the valve vent (2.0s vs 4.0s) should conflict; bluetooth transmission is unrelated
    assert len(timing_issues) == 1
    assert "2.2" in timing_issues[0].sections_involved
    assert "7.1" in timing_issues[0].sections_involved
    assert "2.0s vs 4.0s" in timing_issues[0].title or "4.0s vs 2.0s" in timing_issues[0].title


def test_inconsistency_no_contradiction_when_timings_agree():
    """No contradiction reported when multiple sections specify the exact same time limit."""
    nodes = [
        DocumentNode(
            node_key="1.3",
            title="General Specs",
            content="Emergency valve vents cuff within 2.0 seconds."
        ),
        DocumentNode(
            node_key="4.1",
            title="Pneumatic Safety",
            content="Relief valve vents cuff within 2.0 seconds."
        )
    ]
    report = detect_document_inconsistencies(version_id=1, document_key="ct200", nodes=nodes)
    timing_issues = [i for i in report.issues if i.category == "timing_contradiction"]
    assert len(timing_issues) == 0
