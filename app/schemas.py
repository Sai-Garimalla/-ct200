import json
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Any

class ChildNodeSummary(BaseModel):
    id: int
    node_key: str
    title: str
    node_type: str
    level: int

class NodeOut(BaseModel):
    id: int
    node_key: str
    parent_key: str | None = None
    title: str
    node_type: str
    level: int
    content: str
    content_hash: str
    effective_content: str = ""
    subtree_hash: str = ""
    cross_references: list[str] = []
    page: int

    @field_validator("cross_references", mode="before")
    @classmethod
    def parse_cross_refs(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except Exception:
                pass
        elif isinstance(v, list):
            return [str(item) for item in v]
        return []

    model_config = ConfigDict(from_attributes=True)

class NodeDetailOut(BaseModel):
    id: int
    version_id: int
    node_key: str
    parent_key: str | None = None
    title: str
    node_type: str
    level: int
    content: str
    content_hash: str
    effective_content: str = ""
    subtree_hash: str = ""
    cross_references: list[str] = []
    page: int
    children: list[ChildNodeSummary] = []

    @field_validator("cross_references", mode="before")
    @classmethod
    def parse_cross_refs(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except Exception:
                pass
        elif isinstance(v, list):
            return [str(item) for item in v]
        return []

    model_config = ConfigDict(from_attributes=True)

class NodeChangeSummary(BaseModel):
    node_id: int
    node_key: str
    title: str
    pinned_version_id: int
    pinned_version: int
    latest_version_id: int
    latest_version: int
    has_changed: bool
    change_type: str  # "unchanged", "modified", "deleted_in_latest"
    diff_summary: str
    from_hash: str
    to_hash: str | None = None
    child_changes: list[str] = []

class ResolvedNode(BaseModel):
    id: int
    node_key: str
    title: str
    level: int
    content: str
    effective_content: str = ""
    content_hash: str
    subtree_hash: str = ""
    cross_references: list[str] = []
    page: int

class SelectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Name of the selection")
    version_id: int = Field(..., description="Document version ID to pin this selection to")
    node_ids: list[int] = Field(default=[], description="List of DocumentNode primary key IDs")
    node_keys: list[str] = Field(default=[], description="List of DocumentNode keys (e.g. ['2.1', '3.2', '4.2'])")

class SelectionOut(BaseModel):
    id: int
    name: str
    version_id: int
    node_ids: list[int]
    node_keys: list[str]
    created_at: str
    resolved_nodes: list[ResolvedNode] = []

    model_config = ConfigDict(from_attributes=True)

class TestCaseOut(BaseModel):
    id: int
    version_id: int
    selection_id: int | None = None
    node_key: str
    selected_hash: str
    title: str
    steps: list[str]
    expected_result: str
    source_quote: str | None = None
    stale: bool
    stale_reason: str | None = None

    @field_validator("steps", mode="before")
    @classmethod
    def parse_steps(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
                return [v]
            except Exception:
                return [line.strip() for line in v.splitlines() if line.strip()]
        elif isinstance(v, list):
            return [str(item) for item in v]
        return []

    model_config = ConfigDict(from_attributes=True)

class GenerateRequest(BaseModel):
    version_id: int | None = Field(default=None, description="Document version ID (required if selection_id not provided)")
    selection_id: int | None = Field(default=None, description="Saved version-pinned selection ID to generate from")
    node_keys: list[str] = Field(default=[], description="List of section node keys (if selection_id not used)")
    count: int = Field(default=3, ge=1, le=10, description="Number of test cases to generate (1 to 10)")
    regenerate: bool = Field(default=False, description="If true, bypasses cache and regenerates when selection was previously used")

class TestCaseDraft(BaseModel):
    title: str = Field(description="Descriptive title of the QA test case")
    steps: list[str] = Field(description="Sequential test execution steps")
    expected_result: str = Field(description="Expected behavior or pass/fail criterion")
    source_node_key: str = Field(description="Document node key grounded in source")
    source_quote: str | None = Field(default=None, description="Direct quote or excerpt from the document section")

class QAGenerationResponse(BaseModel):
    test_cases: list[TestCaseDraft] = Field(description="List of structured test cases")

class ChangedNodeDetail(BaseModel):
    node_key: str
    title: str
    from_hash: str
    to_hash: str
    diff_type: str = "content_modified"

class DiffOut(BaseModel):
    from_version_id: int
    to_version_id: int
    added: list[str]
    removed: list[str]
    changed: list[str]
    changed_details: list[ChangedNodeDetail] = []

class VersionOut(BaseModel):
    id: int
    document_key: str
    version: int
    filename: str
    file_hash: str
    created_at: str
    node_count: int | None = None

    model_config = ConfigDict(from_attributes=True)

class InconsistencyItem(BaseModel):
    category: str = Field(description="Category of issue: timing_contradiction, vendor_brand_anomaly, broken_cross_reference")
    severity: str = Field(description="Severity: high or medium")
    sections_involved: list[str] = Field(description="Section keys involved in the conflict")
    title: str = Field(description="Short summary of the inconsistency")
    description: str = Field(description="Detailed explanation of the contradiction or anomaly")
    evidence: str | None = Field(default=None, description="Direct quoted excerpts proving the discrepancy")

class InconsistencyReport(BaseModel):
    version_id: int
    document_key: str
    total_issues: int
    issues: list[InconsistencyItem]
