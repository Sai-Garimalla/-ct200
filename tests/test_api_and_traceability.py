import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db import Base, get_db

BASE_DIR = Path(__file__).resolve().parent.parent
PDF_V1 = str(BASE_DIR / "sample_data_ct200_manual.pdf")
PDF_V2 = str(BASE_DIR / "sample_data_ct200_manual_v2.pdf")

TEST_DB_URL = "sqlite:///./test_ct200.db"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    if os.path.exists("./test_ct200.db"):
        try:
            os.unlink("./test_ct200.db")
        except Exception:
            pass

client = TestClient(app)

def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

def test_end_to_end_ingestion_and_traceability():
    # 1. Ingest Version 1
    with open(PDF_V1, "rb") as f:
        res = client.post("/documents/ingest?document_key=ct200", files={"file": ("manual_v1.pdf", f, "application/pdf")})
    assert res.status_code == 200
    v1_data = res.json()
    v1_id = v1_data["id"]
    assert v1_data["version"] == 1
    assert v1_data["node_count"] >= 25

    # 2. Browse tree
    tree_res = client.get(f"/documents/{v1_id}/tree")
    assert tree_res.status_code == 200
    nodes = tree_res.json()
    assert len(nodes) == v1_data["node_count"]
    node_keys = {n["node_key"] for n in nodes}
    assert "3.2" in node_keys
    assert "1.1" in node_keys

    # 3. Browse API: Top-level sections (defaulting to latest version)
    top_res = client.get("/sections/top-level?document_key=ct200")
    assert top_res.status_code == 200
    top_nodes = top_res.json()
    assert len(top_nodes) >= 6
    top_keys = [n["node_key"] for n in top_nodes]
    assert "1" in top_keys
    assert "2" in top_keys

    # 4. Browse API: Get node by ID including immediate children
    node_1 = next(n for n in nodes if n["node_key"] == "1")
    node_detail_res = client.get(f"/nodes/{node_1['id']}")
    assert node_detail_res.status_code == 200
    node_detail = node_detail_res.json()
    assert node_detail["node_key"] == "1"
    assert len(node_detail["children"]) >= 1
    child_keys = [c["node_key"] for c in node_detail["children"]]
    assert "1.1" in child_keys

    # 5. Retrieve Version-Pinned Section
    sec_res = client.get(f"/documents/{v1_id}/nodes/3.2")
    assert sec_res.status_code == 200
    sec_data = sec_res.json()
    assert sec_data["node_key"] == "3.2"
    assert "initial target of 180 mmHg" in sec_data["content"]
    assert sec_data["level"] == 2
    assert sec_data["page"] == 3

    # 6. Search nodes
    search_res = client.get(f"/documents/{v1_id}/search?q=cuff")
    assert search_res.status_code == 200
    assert len(search_res.json()) >= 1

    # 7. Selection API: Submit named version-pinned selection
    node_3_2 = next(n for n in nodes if n["node_key"] == "3.2")
    node_1_1 = next(n for n in nodes if n["node_key"] == "1.1")
    sel_res = client.post("/selections", json={
        "name": "Inflation Dynamics & Indications",
        "version_id": v1_id,
        "node_ids": [node_3_2["id"], node_1_1["id"]]
    })
    assert sel_res.status_code == 200
    sel_data = sel_res.json()
    assert sel_data["name"] == "Inflation Dynamics & Indications"
    assert sel_data["version_id"] == v1_id
    assert len(sel_data["resolved_nodes"]) == 2
    selection_id = sel_data["id"]

    # Verify retrieval of selection resolves exact pinned text
    get_sel_res = client.get(f"/selections/{selection_id}")
    assert get_sel_res.status_code == 200
    assert len(get_sel_res.json()["resolved_nodes"]) == 2

    # 8. Generate QA Test Cases using Selection ID
    gen_res = client.post("/test-cases/generate", json={
        "selection_id": selection_id,
        "count": 2
    })
    assert gen_res.status_code == 200
    created_cases = gen_res.json()
    assert len(created_cases) == 2
    case_ids = [c["id"] for c in created_cases]

    # Verify Duplicate Selection Policy (idempotent cached return when regenerate=False)
    dup_res = client.post("/test-cases/generate", json={
        "selection_id": selection_id,
        "count": 2,
        "regenerate": False
    })
    assert dup_res.status_code == 200
    dup_cases = dup_res.json()
    assert [c["id"] for c in dup_cases] == case_ids

    # 9. Also test direct node_keys generation for 4.1
    gen_direct = client.post("/test-cases/generate", json={
        "version_id": v1_id,
        "node_keys": ["3.2"],
        "count": 1
    })
    assert gen_direct.status_code == 200
    direct_case = gen_direct.json()[0]
    assert direct_case["node_key"] == "3.2"

    # 10. Ingest Version 2 (which modifies section 3.2, keeps 1.1 unchanged, and adds 5.3)
    with open(PDF_V2, "rb") as f:
        res2 = client.post("/documents/ingest?document_key=ct200", files={"file": ("manual_v2.pdf", f, "application/pdf")})
    assert res2.status_code == 200
    v2_data = res2.json()
    v2_id = v2_data["id"]
    assert v2_data["version"] == 2

    # 11. Diff versions
    diff_res = client.get(f"/documents/{v1_id}/diff/{v2_id}")
    assert diff_res.status_code == 200
    diff_data = diff_res.json()
    assert "5.3" in diff_data["added"]
    assert "3.2" in diff_data["changed"]

    # 12. Browse API: Given a node ID, check change across versions with diff summary
    change_3_2 = client.get(f"/nodes/{node_3_2['id']}/changes")
    assert change_3_2.status_code == 200
    ch_info = change_3_2.json()
    assert ch_info["has_changed"] is True
    assert ch_info["change_type"] == "modified"
    assert "Content modified" in ch_info["diff_summary"]

    # Verify /history alias endpoint
    hist_3_2 = client.get(f"/nodes/{node_3_2['id']}/history")
    assert hist_3_2.status_code == 200
    assert hist_3_2.json()["has_changed"] is True

    change_1_1 = client.get(f"/nodes/{node_1_1['id']}/changes")
    assert change_1_1.status_code == 200
    assert change_1_1.json()["has_changed"] is False
    assert change_1_1.json()["change_type"] == "unchanged"

    # 13. Verify staleness detection on previously generated test cases
    tc_3_2 = next(tc for tc in created_cases if tc["node_key"] == "3.2")
    tc_1_1 = next(tc for tc in created_cases if tc["node_key"] == "1.1")

    stale_check_3_2 = client.get(f"/test-cases/{tc_3_2['id']}/staleness")
    assert stale_check_3_2.status_code == 200
    stale_info_3_2 = stale_check_3_2.json()
    assert stale_info_3_2["stale"] is True
    assert stale_info_3_2["stale_reason"] == "source_modified"

    stale_check_1_1 = client.get(f"/test-cases/{tc_1_1['id']}/staleness")
    assert stale_check_1_1.status_code == 200
    stale_info_1_1 = stale_check_1_1.json()
    assert stale_info_1_1["stale"] is False
    assert stale_info_1_1["stale_reason"] is None

    # 14. Retrieval API: Filter test cases by selection ID, node key, and staleness
    sel_filtered = client.get(f"/test-cases?selection_id={selection_id}")
    assert sel_filtered.status_code == 200
    assert len(sel_filtered.json()) >= 2

    node_filtered = client.get("/test-cases?node_key=3.2")
    assert node_filtered.status_code == 200
    assert any(c["node_key"] == "3.2" for c in node_filtered.json())

    list_stale = client.get("/test-cases?stale=true")
    assert list_stale.status_code == 200
    stale_ids = [c["id"] for c in list_stale.json()]
    assert tc_3_2["id"] in stale_ids
    assert tc_1_1["id"] not in stale_ids
