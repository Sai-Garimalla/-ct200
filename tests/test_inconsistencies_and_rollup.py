import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.services.versioning import ingest
from app.models import DocumentVersion, DocumentNode, TestCase

BASE_DIR = Path(__file__).resolve().parent.parent
PDF_V1 = str(BASE_DIR / "sample_data_ct200_manual.pdf")
PDF_V2 = str(BASE_DIR / "sample_data_ct200_manual_v2.pdf")

@pytest.fixture
def test_client_and_db(tmp_path):
    db_file = tmp_path / "test_inconsistencies.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    db = TestingSessionLocal()
    yield client, db
    app.dependency_overrides.clear()
    db.close()

def test_parent_child_content_rollup_and_staleness(test_client_and_db):
    client, db = test_client_and_db

    # 1. Ingest v1
    v1_rec, created1 = ingest(db, PDF_V1, "ct200_v1.pdf", document_key="ct200")
    assert created1 is True

    # 2. Check node 4.2 in v1: direct content is empty, but effective_content has child table
    node_4_2_v1 = db.query(DocumentNode).filter_by(version_id=v1_rec.id, node_key="4.2").first()
    assert node_4_2_v1 is not None
    assert len(node_4_2_v1.content.strip()) == 0  # direct content is empty
    assert len(node_4_2_v1.effective_content.strip()) > 50  # rolled-up child table exists
    assert "Error Code" in node_4_2_v1.effective_content or "E1" in node_4_2_v1.effective_content
    assert node_4_2_v1.subtree_hash != node_4_2_v1.content_hash

    # 3. Create selection on node 4.2
    sel_res = client.post("/selections", json={
        "name": "Error Handling Suite",
        "version_id": v1_rec.id,
        "node_keys": ["4.2"]
    })
    assert sel_res.status_code == 200
    sel_data = sel_res.json()
    resolved_4_2 = sel_data["resolved_nodes"][0]
    assert len(resolved_4_2["effective_content"]) > 0
    assert "E1" in resolved_4_2["effective_content"]

    # 4. Generate test case on selection 4.2
    gen_res = client.post("/test-cases/generate", json={
        "selection_id": sel_data["id"],
        "count": 1
    })
    assert gen_res.status_code == 200
    cases_v1 = gen_res.json()
    assert len(cases_v1) == 1
    tc_v1 = cases_v1[0]
    # Pinned hash should be subtree_hash (non-empty hash of effective_content)
    assert tc_v1["selected_hash"] == node_4_2_v1.subtree_hash
    assert tc_v1["stale"] is False

    # 5. Ingest v2 (where 4.2.table.1 changes: E3 timing becomes 1.5s, E6 is added)
    v2_rec, created2 = ingest(db, PDF_V2, "ct200_v2.pdf", document_key="ct200")
    assert created2 is True

    # 6. Staleness check: test case on parent node 4.2 MUST be marked stale because its child table changed
    stale_res = client.get(f"/test-cases/{tc_v1['id']}/staleness")
    assert stale_res.status_code == 200
    stale_data = stale_res.json()
    assert stale_data["stale"] is True
    assert stale_data["stale_reason"] == "source_modified"

    # 7. Check node history / change summary on parent node 4.2
    history_res = client.get(f"/nodes/{node_4_2_v1.id}/history")
    assert history_res.status_code == 200
    history_data = history_res.json()
    assert history_data["has_changed"] is True
    assert history_data["change_type"] in ["modified", "child_content_modified"]

def test_planted_inconsistencies_and_cross_references(test_client_and_db):
    client, db = test_client_and_db

    # Ingest v2 PDF
    v2_rec, _ = ingest(db, PDF_V2, "ct200_v2.pdf", document_key="ct200")

    # 1. Audit inconsistencies
    inconsistencies_res = client.get(f"/documents/{v2_rec.id}/inconsistencies")
    assert inconsistencies_res.status_code == 200
    report = inconsistencies_res.json()
    assert report["total_issues"] >= 2

    # Check timing contradiction (Section 4.1 2.0s vs 4.2 E3 1.5s)
    timing_issues = [i for i in report["issues"] if i["category"] == "timing_contradiction"]
    assert len(timing_issues) >= 1
    t_issue = timing_issues[0]
    assert "4.1" in t_issue["sections_involved"]
    assert "4.2" in t_issue["sections_involved"]
    assert "2.0" in t_issue["title"] or "2" in t_issue["title"]
    assert "1.5" in t_issue["title"] or "1.5" in t_issue["description"]

    # Check vendor anomaly (Section 6.2 citing Anthropic)
    vendor_issues = [i for i in report["issues"] if i["category"] == "vendor_brand_anomaly"]
    assert len(vendor_issues) >= 1
    v_issue = vendor_issues[0]
    assert "6.2" in v_issue["sections_involved"]
    assert "Anthropic" in v_issue["title"] or "Anthropic" in v_issue["description"]

    # 2. Check cross-references endpoint
    xref_res = client.get(f"/documents/{v2_rec.id}/cross-references")
    assert xref_res.status_code == 200
    xref_data = xref_res.json()
    edges = xref_data["edges"]

    # Section 3.3 references 2.1 and 4.3
    sec_3_3_edges = [e for e in edges if e["source_node_key"] == "3.3"]
    targets_from_3_3 = {e["target_node_key"] for e in sec_3_3_edges}
    assert "2.1" in targets_from_3_3
    assert "4.3" in targets_from_3_3

def test_heading_trailing_dot_preservation(test_client_and_db):
    client, db = test_client_and_db
    v1_rec, _ = ingest(db, PDF_V1, "ct200_v1.pdf", document_key="ct200")

    tree_res = client.get(f"/documents/{v1_rec.id}/tree")
    assert tree_res.status_code == 200
    nodes = tree_res.json()

    # Headings like "1." in the PDF must preserve the trailing dot: "1. Device Overview"
    node_1 = next((n for n in nodes if n["node_key"] == "1"), None)
    assert node_1 is not None
    assert node_1["title"].startswith("1. ")

def test_exact_requested_test_case_count(test_client_and_db):
    client, db = test_client_and_db
    v1_rec, _ = ingest(db, PDF_V1, "ct200_v1.pdf", document_key="ct200")

    for requested_count in [1, 3, 5]:
        res = client.post("/test-cases/generate", json={
            "version_id": v1_rec.id,
            "node_keys": ["2.1"],
            "count": requested_count
        })
        assert res.status_code == 200
        cases = res.json()
        assert len(cases) == requested_count
        for c in cases:
            assert c["title"]
            assert len(c["steps"]) >= 2
            assert c["expected_result"]
            assert c["node_key"] == "2.1"

