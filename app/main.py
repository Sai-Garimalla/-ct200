import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .db import Base, engine, get_db
from .models import DocumentVersion, DocumentNode, TestCase, Selection
from .schemas import (
    NodeOut,
    NodeDetailOut,
    ChildNodeSummary,
    NodeChangeSummary,
    ResolvedNode,
    SelectionCreate,
    SelectionOut,
    TestCaseOut,
    GenerateRequest,
    DiffOut,
    VersionOut,
    InconsistencyItem,
    InconsistencyReport
)
from .services.versioning import ingest, compute_diff
from .services.qa_generator import call_llm_with_retry
from .services.inconsistency_detector import detect_document_inconsistencies

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="CT-200 Document Intelligence & QA Traceability API",
    description="Backend API for ingesting CT-200 manual PDFs, extracting hierarchical trees and tables, versioning documents, managing version-pinned selections, and generating traceable QA test cases with staleness impact detection.",
    version="1.1.0"
)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", include_in_schema=False)
def serve_dashboard():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "CT-200 Document Intelligence API is active. Open /docs for Swagger UI."}

@app.get("/health")
def health():
    return {"status": "ok", "service": "ct200-document-intelligence"}

# ============================================================================
# Ingestion & Versions
# ============================================================================

@app.post("/documents/ingest", response_model=VersionOut)
def ingest_document(
    file: UploadFile = File(..., description="PDF manual file to upload"),
    document_key: str = Query("ct200", description="Document identifier/family name"),
    db: Session = Depends(get_db)
):
    """Upload and ingest a PDF manual. Extracts sections and tables into the database
    and increments the document version number."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = file.file.read()
        tmp.write(content)
        temp_path = tmp.name

    try:
        version_rec, created = ingest(db, temp_path, file.filename, document_key=document_key)
    finally:
        Path(temp_path).unlink(missing_ok=True)

    return VersionOut(
        id=version_rec.id,
        document_key=version_rec.document_key,
        version=version_rec.version,
        filename=version_rec.filename,
        file_hash=version_rec.file_hash,
        created_at=version_rec.created_at,
        node_count=len(version_rec.nodes)
    )

@app.get("/documents", response_model=list[VersionOut])
def list_documents(db: Session = Depends(get_db)):
    """List all ingested document versions in the database."""
    versions = db.scalars(
        select(DocumentVersion).order_by(DocumentVersion.document_key, DocumentVersion.version)
    ).all()
    return [
        VersionOut(
            id=v.id,
            document_key=v.document_key,
            version=v.version,
            filename=v.filename,
            file_hash=v.file_hash,
            created_at=v.created_at,
            node_count=len(v.nodes)
        )
        for v in versions
    ]

@app.get("/documents/{version_id}", response_model=VersionOut)
def get_document_version(version_id: int, db: Session = Depends(get_db)):
    """Get metadata for a specific document version."""
    v = db.get(DocumentVersion, version_id)
    if not v:
        raise HTTPException(status_code=404, detail=f"Document version {version_id} not found")
    return VersionOut(
        id=v.id,
        document_key=v.document_key,
        version=v.version,
        filename=v.filename,
        file_hash=v.file_hash,
        created_at=v.created_at,
        node_count=len(v.nodes)
    )

# ============================================================================
# Browse API
# ============================================================================

@app.get("/sections/top-level", response_model=list[NodeOut])
def list_top_level_sections(
    document_key: str = Query("ct200", description="Document family key"),
    version: int | None = Query(None, description="Version number (defaults to latest if omitted)"),
    db: Session = Depends(get_db)
):
    """List top-level sections (level 1 / roots) with version parameter defaulting to latest."""
    if version is None:
        latest = db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_key == document_key)
            .order_by(DocumentVersion.version.desc())
        ).first()
        if not latest:
            raise HTTPException(status_code=404, detail=f"No versions found for document key '{document_key}'")
        target_version_id = latest.id
    else:
        v_rec = db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_key == document_key, DocumentVersion.version == version)
        ).first()
        if not v_rec:
            raise HTTPException(status_code=404, detail=f"Version {version} not found for document key '{document_key}'")
        target_version_id = v_rec.id

    top_level_nodes = db.scalars(
        select(DocumentNode)
        .where(
            DocumentNode.version_id == target_version_id,
            (DocumentNode.parent_key.is_(None)) | (DocumentNode.level <= 1)
        )
        .order_by(DocumentNode.id)
    ).all()
    return top_level_nodes

@app.get("/documents/{version_id}/tree", response_model=list[NodeOut])
def browse_tree(version_id: int, db: Session = Depends(get_db)):
    """Browse the complete extracted hierarchy of headings, sections, and tables for a version."""
    version = db.get(DocumentVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Document version not found")
    return db.scalars(
        select(DocumentNode).where(DocumentNode.version_id == version_id).order_by(DocumentNode.id)
    ).all()

@app.get("/nodes/{node_id}", response_model=NodeDetailOut)
def get_node_by_id(node_id: int, db: Session = Depends(get_db)):
    """Get a specific node by its ID, including its immediate children, full text, and content hash."""
    node = db.get(DocumentNode, node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Document node {node_id} not found")
    
    # Query children of this node within the same version
    children = db.scalars(
        select(DocumentNode)
        .where(
            DocumentNode.version_id == node.version_id,
            DocumentNode.parent_key == node.node_key
        )
        .order_by(DocumentNode.id)
    ).all()

    child_summaries = [
        ChildNodeSummary(
            id=c.id,
            node_key=c.node_key,
            title=c.title,
            node_type=c.node_type,
            level=c.level
        )
        for c in children
    ]

    return NodeDetailOut(
        id=node.id,
        version_id=node.version_id,
        node_key=node.node_key,
        parent_key=node.parent_key,
        title=node.title,
        node_type=node.node_type,
        level=node.level,
        content=node.content,
        content_hash=node.content_hash,
        effective_content=node.effective_content,
        subtree_hash=node.subtree_hash,
        cross_references=json.loads(node.cross_references) if node.cross_references else [],
        page=node.page,
        children=child_summaries
    )

@app.get("/nodes/{node_id}/changes", response_model=NodeChangeSummary)
@app.get("/nodes/{node_id}/history", response_model=NodeChangeSummary)
def check_node_version_change(node_id: int, db: Session = Depends(get_db)):
    """Given a node ID, return whether it has changed across versions, with a lightweight diff summary."""
    node = db.get(DocumentNode, node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Document node {node_id} not found")

    pinned_version = db.get(DocumentVersion, node.version_id)
    latest_version = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_key == pinned_version.document_key)
        .order_by(DocumentVersion.version.desc())
    ).first()

    node_hash = node.subtree_hash or node.content_hash
    if latest_version.id == pinned_version.id:
        return NodeChangeSummary(
            node_id=node.id,
            node_key=node.node_key,
            title=node.title,
            pinned_version_id=pinned_version.id,
            pinned_version=pinned_version.version,
            latest_version_id=latest_version.id,
            latest_version=latest_version.version,
            has_changed=False,
            change_type="unchanged",
            diff_summary="This node belongs to the latest version of the document.",
            from_hash=node_hash,
            to_hash=node_hash
        )

    latest_node = db.scalars(
        select(DocumentNode).where(
            DocumentNode.version_id == latest_version.id,
            DocumentNode.node_key == node.node_key
        )
    ).first()

    if not latest_node:
        return NodeChangeSummary(
            node_id=node.id,
            node_key=node.node_key,
            title=node.title,
            pinned_version_id=pinned_version.id,
            pinned_version=pinned_version.version,
            latest_version_id=latest_version.id,
            latest_version=latest_version.version,
            has_changed=True,
            change_type="deleted_in_latest",
            diff_summary=f"Section '{node.node_key}' was deleted or renumbered in version {latest_version.version}.",
            from_hash=node_hash,
            to_hash=None
        )

    latest_hash = latest_node.subtree_hash or latest_node.content_hash
    if latest_node.content_hash == node.content_hash and latest_node.subtree_hash == node.subtree_hash:
        return NodeChangeSummary(
            node_id=node.id,
            node_key=node.node_key,
            title=node.title,
            pinned_version_id=pinned_version.id,
            pinned_version=pinned_version.version,
            latest_version_id=latest_version.id,
            latest_version=latest_version.version,
            has_changed=False,
            change_type="unchanged",
            diff_summary=f"Section content and child elements are identical between version {pinned_version.version} and version {latest_version.version}.",
            from_hash=node_hash,
            to_hash=latest_hash
        )

    change_label = "modified" if latest_node.content_hash != node.content_hash else "child_content_modified"
    diff_desc = (
        f"Content modified between version {pinned_version.version} and version {latest_version.version} (hash {node.content_hash[:8]}... -> {latest_node.content_hash[:8]}...)."
        if latest_node.content_hash != node.content_hash
        else f"Subtree child content (tables/subsections) modified between version {pinned_version.version} and version {latest_version.version}."
    )

    return NodeChangeSummary(
        node_id=node.id,
        node_key=node.node_key,
        title=node.title,
        pinned_version_id=pinned_version.id,
        pinned_version=pinned_version.version,
        latest_version_id=latest_version.id,
        latest_version=latest_version.version,
        has_changed=True,
        change_type=change_label,
        diff_summary=diff_desc,
        from_hash=node_hash,
        to_hash=latest_hash
    )

@app.get("/documents/{version_id}/nodes/{node_key}", response_model=NodeOut)
def get_version_pinned_section(version_id: int, node_key: str, db: Session = Depends(get_db)):
    """Retrieve an exact section or table node pinned to a specific document version."""
    node = db.scalars(
        select(DocumentNode).where(
            DocumentNode.version_id == version_id,
            DocumentNode.node_key == node_key
        )
    ).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Section '{node_key}' not found in document version {version_id}")
    return node

@app.get("/documents/{version_id}/search", response_model=list[NodeOut])
def search_nodes(version_id: int, q: str, db: Session = Depends(get_db)):
    """Search for matching keywords in section titles, keys, and content for a version."""
    if not db.get(DocumentVersion, version_id):
        raise HTTPException(status_code=404, detail="Document version not found")
    rows = db.scalars(
        select(DocumentNode).where(DocumentNode.version_id == version_id)
    ).all()
    query_term = q.casefold()
    return [
        NodeOut.model_validate(n) for n in rows
        if query_term in n.title.casefold() or query_term in n.content.casefold() or query_term in n.node_key.casefold()
    ]

@app.get("/documents/{version_id}/diff/{other_version_id}", response_model=DiffOut)
def diff_versions(version_id: int, other_version_id: int, db: Session = Depends(get_db)):
    """Compare two versions of a document to find added, removed, and changed sections."""
    result = compute_diff(db, from_version_id=version_id, to_version_id=other_version_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result

# ============================================================================
# Selection API (Version-Pinned Named Selections)
# ============================================================================

def _resolve_selection_nodes(db: Session, version_id: int, node_ids: list[int], node_keys: list[str]) -> list[DocumentNode]:
    """Resolves and validates nodes for a version-pinned selection."""
    nodes_by_id = {}
    if node_ids:
        rows = db.scalars(
            select(DocumentNode).where(
                DocumentNode.version_id == version_id,
                DocumentNode.id.in_(node_ids)
            )
        ).all()
        for r in rows:
            nodes_by_id[r.id] = r

    if node_keys:
        rows = db.scalars(
            select(DocumentNode).where(
                DocumentNode.version_id == version_id,
                DocumentNode.node_key.in_(node_keys)
            )
        ).all()
        for r in rows:
            nodes_by_id[r.id] = r

    return list(nodes_by_id.values())

@app.post("/selections", response_model=SelectionOut)
def create_selection(req: SelectionCreate, db: Session = Depends(get_db)):
    """Submit a set of node IDs or keys as a named, version-pinned selection.
    References specific node+version pairs so future re-ingestions preserve exact text."""
    version = db.get(DocumentVersion, req.version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Document version {req.version_id} not found")

    if not req.node_ids and not req.node_keys:
        raise HTTPException(status_code=400, detail="Must provide at least one node ID or node key")

    resolved_nodes = _resolve_selection_nodes(db, req.version_id, req.node_ids, req.node_keys)
    if not resolved_nodes:
        raise HTTPException(status_code=404, detail="None of the specified nodes were found in the target version")

    final_ids = [n.id for n in resolved_nodes]
    final_keys = [n.node_key for n in resolved_nodes]

    selection_rec = Selection(
        name=req.name,
        version_id=req.version_id,
        node_ids=json.dumps(final_ids),
        node_keys=json.dumps(final_keys),
        created_at=datetime.now(timezone.utc).isoformat()
    )
    db.add(selection_rec)
    db.commit()
    db.refresh(selection_rec)

    resolved_out = [
        ResolvedNode(
            id=n.id,
            node_key=n.node_key,
            title=n.title,
            level=n.level,
            content=n.content,
            effective_content=n.effective_content,
            content_hash=n.content_hash,
            subtree_hash=n.subtree_hash,
            cross_references=json.loads(n.cross_references) if n.cross_references else [],
            page=n.page
        )
        for n in resolved_nodes
    ]

    return SelectionOut(
        id=selection_rec.id,
        name=selection_rec.name,
        version_id=selection_rec.version_id,
        node_ids=final_ids,
        node_keys=final_keys,
        created_at=selection_rec.created_at,
        resolved_nodes=resolved_out
    )

@app.get("/selections", response_model=list[SelectionOut])
def list_selections(
    version_id: int | None = Query(None, description="Optional filter by document version ID"),
    db: Session = Depends(get_db)
):
    """List all saved version-pinned selections."""
    stmt = select(Selection)
    if version_id is not None:
        stmt = stmt.where(Selection.version_id == version_id)
    selections = db.scalars(stmt.order_by(Selection.id.desc())).all()

    result = []
    for s in selections:
        parsed_ids = json.loads(s.node_ids) if s.node_ids else []
        parsed_keys = json.loads(s.node_keys) if s.node_keys else []
        resolved = _resolve_selection_nodes(db, s.version_id, parsed_ids, parsed_keys)
        result.append(SelectionOut(
            id=s.id,
            name=s.name,
            version_id=s.version_id,
            node_ids=parsed_ids,
            node_keys=parsed_keys,
            created_at=s.created_at,
            resolved_nodes=[
                ResolvedNode(
                    id=n.id,
                    node_key=n.node_key,
                    title=n.title,
                    level=n.level,
                    content=n.content,
                    effective_content=n.effective_content,
                    content_hash=n.content_hash,
                    subtree_hash=n.subtree_hash,
                    cross_references=json.loads(n.cross_references) if n.cross_references else [],
                    page=n.page
                )
                for n in resolved
            ]
        ))
    return result

@app.get("/selections/{selection_id}", response_model=SelectionOut)
def get_selection(selection_id: int, db: Session = Depends(get_db)):
    """Fetch a specific version-pinned selection and resolve its exact source text."""
    s = db.get(Selection, selection_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Selection {selection_id} not found")

    parsed_ids = json.loads(s.node_ids) if s.node_ids else []
    parsed_keys = json.loads(s.node_keys) if s.node_keys else []
    resolved = _resolve_selection_nodes(db, s.version_id, parsed_ids, parsed_keys)

    return SelectionOut(
        id=s.id,
        name=s.name,
        version_id=s.version_id,
        node_ids=parsed_ids,
        node_keys=parsed_keys,
        created_at=s.created_at,
        resolved_nodes=[
            ResolvedNode(
                id=n.id,
                node_key=n.node_key,
                title=n.title,
                level=n.level,
                content=n.content,
                effective_content=n.effective_content,
                content_hash=n.content_hash,
                subtree_hash=n.subtree_hash,
                cross_references=json.loads(n.cross_references) if n.cross_references else [],
                page=n.page
            )
            for n in resolved
        ]
    )

# ============================================================================
# LLM QA Generation API
# ============================================================================

@app.post("/test-cases/generate", response_model=list[TestCaseOut])
def generate_test_cases(req: GenerateRequest, db: Session = Depends(get_db)):
    """Generate source-grounded QA test cases from a version-pinned selection or direct node keys.
    Duplicate Policy: If a selection has previously generated test cases and regenerate=False,
    existing cases are returned immediately to ensure idempotency and save token costs."""
    
    target_version_id = req.version_id
    chosen_nodes = []
    active_selection_id = None

    if req.selection_id is not None:
        selection = db.get(Selection, req.selection_id)
        if not selection:
            raise HTTPException(status_code=404, detail=f"Selection {req.selection_id} not found")
        active_selection_id = selection.id
        target_version_id = selection.version_id

        # Idempotency / Duplicate Selection Policy Check
        if not req.regenerate:
            existing_cases = db.scalars(
                select(TestCase).where(TestCase.selection_id == active_selection_id)
            ).all()
            if existing_cases:
                return existing_cases

        parsed_ids = json.loads(selection.node_ids) if selection.node_ids else []
        parsed_keys = json.loads(selection.node_keys) if selection.node_keys else []
        chosen_nodes = _resolve_selection_nodes(db, selection.version_id, parsed_ids, parsed_keys)
    else:
        if target_version_id is None:
            raise HTTPException(status_code=400, detail="Must specify version_id when not referencing a selection_id")
        if not req.node_keys:
            raise HTTPException(status_code=400, detail="Must provide node_keys when not referencing a selection_id")
        
        version = db.get(DocumentVersion, target_version_id)
        if not version:
            raise HTTPException(status_code=404, detail=f"Document version {target_version_id} not found")

        rows = db.scalars(select(DocumentNode).where(DocumentNode.version_id == target_version_id)).all()
        nodes_by_key = {n.node_key: n for n in rows}
        chosen_nodes = [nodes_by_key[k] for k in req.node_keys if k in nodes_by_key]

    if not chosen_nodes:
        raise HTTPException(status_code=404, detail="No valid target document nodes found for test case generation")

    nodes_by_key = {n.node_key: n for n in chosen_nodes}

    # Generate drafts via LLM or deterministic fallback
    drafts = call_llm_with_retry(chosen_nodes, count=req.count)

    created_cases = []
    for draft in drafts:
        matched_node = nodes_by_key.get(draft.source_node_key, chosen_nodes[0])
        # Pin hash to subtree_hash so parent nodes with child changes trigger staleness
        hash_to_pin = matched_node.subtree_hash or matched_node.content_hash
        case_rec = TestCase(
            version_id=target_version_id,
            selection_id=active_selection_id,
            node_key=matched_node.node_key,
            selected_hash=hash_to_pin,
            title=draft.title,
            steps=json.dumps(draft.steps),
            expected_result=draft.expected_result,
            source_quote=draft.source_quote,
            stale=False,
            stale_reason=None
        )
        db.add(case_rec)
        db.flush()
        created_cases.append(case_rec)

    db.commit()
    return created_cases

# ============================================================================
# Retrieval & Staleness API
# ============================================================================

@app.get("/test-cases", response_model=list[TestCaseOut])
def list_test_cases(
    version_id: int | None = Query(None, description="Filter test cases by document version ID"),
    selection_id: int | None = Query(None, description="Filter test cases by selection ID"),
    node_key: str | None = Query(None, description="Filter test cases by source node key (e.g. '3.2')"),
    node_id: int | None = Query(None, description="Filter test cases by source node primary key ID"),
    stale: bool | None = Query(None, description="Filter by staleness status (true/false)"),
    db: Session = Depends(get_db)
):
    """Retrieve generated QA test cases with full traceability and staleness visibility.
    Supports filtering by selection ID, node key, node ID, version, or staleness."""
    stmt = select(TestCase)
    if version_id is not None:
        stmt = stmt.where(TestCase.version_id == version_id)
    if selection_id is not None:
        stmt = stmt.where(TestCase.selection_id == selection_id)
    if node_key is not None:
        stmt = stmt.where(TestCase.node_key == node_key)
    if node_id is not None:
        target_node = db.get(DocumentNode, node_id)
        if target_node:
            stmt = stmt.where(TestCase.node_key == target_node.node_key, TestCase.version_id == target_node.version_id)
        else:
            return []
    if stale is not None:
        stmt = stmt.where(TestCase.stale == stale)

    return db.scalars(stmt.order_by(TestCase.id)).all()

@app.get("/test-cases/{case_id}", response_model=TestCaseOut)
def get_test_case(case_id: int, db: Session = Depends(get_db)):
    """Fetch a single test case by its ID."""
    case = db.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Test case {case_id} not found")
    return case

@app.get("/test-cases/{case_id}/staleness")
def evaluate_test_case_staleness(case_id: int, db: Session = Depends(get_db)):
    """Evaluate whether a test case is stale compared to the latest version of the document."""
    case = db.get(TestCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Test case {case_id} not found")
    
    pinned_version = db.get(DocumentVersion, case.version_id)
    latest_version = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_key == pinned_version.document_key)
        .order_by(DocumentVersion.version.desc())
    ).first()

    latest_node = db.scalars(
        select(DocumentNode).where(
            DocumentNode.version_id == latest_version.id,
            DocumentNode.node_key == case.node_key
        )
    ).first()

    is_stale = False
    reason = None
    if latest_node is None:
        is_stale = True
        reason = "source_deleted"
    else:
        # Check both direct content hash and subtree hash
        current_hashes = {latest_node.content_hash, latest_node.subtree_hash}
        if case.selected_hash not in current_hashes:
            is_stale = True
            reason = "source_modified"

    return {
        "test_case_id": case.id,
        "selection_id": case.selection_id,
        "pinned_version_id": case.version_id,
        "pinned_version": pinned_version.version,
        "latest_version_id": latest_version.id,
        "latest_version": latest_version.version,
        "node_key": case.node_key,
        "selected_hash": case.selected_hash,
        "latest_node_hash": latest_node.subtree_hash or latest_node.content_hash if latest_node else None,
        "stale": is_stale,
        "stale_reason": reason
    }

# ============================================================================
# Document Inconsistency & Relational Cross-References API
# ============================================================================

@app.get("/documents/{version_id}/inconsistencies", response_model=InconsistencyReport)
def get_document_inconsistencies(version_id: int, db: Session = Depends(get_db)):
    """Audits a document version for planted contradictions, vendor anomalies,
    and cross-reference linkage (e.g. 4.1 vs 4.2 timing, Anthropic in 6.2, 3.3 cross-references)."""
    version = db.get(DocumentVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Document version not found")
    nodes = db.scalars(
        select(DocumentNode).where(DocumentNode.version_id == version_id).order_by(DocumentNode.id)
    ).all()
    return detect_document_inconsistencies(version_id=version.id, document_key=version.document_key, nodes=nodes)

@app.get("/documents/{version_id}/cross-references")
def get_cross_references(version_id: int, db: Session = Depends(get_db)):
    """Returns captured cross-reference links and graph edges across sections in the document."""
    version = db.get(DocumentVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Document version not found")
    nodes = db.scalars(
        select(DocumentNode).where(DocumentNode.version_id == version_id)
    ).all()
    nodes_by_key = {n.node_key: n for n in nodes}
    edges = []
    for n in nodes:
        if n.cross_references:
            try:
                refs = json.loads(n.cross_references) if isinstance(n.cross_references, str) else n.cross_references
            except Exception:
                refs = []
            for r in refs:
                target = nodes_by_key.get(r)
                edges.append({
                    "source_node_key": n.node_key,
                    "source_title": n.title,
                    "target_node_key": r,
                    "target_title": target.title if target else None,
                    "is_resolved": target is not None
                })
    return {"version_id": version_id, "document_key": version.document_key, "total_edges": len(edges), "edges": edges}

