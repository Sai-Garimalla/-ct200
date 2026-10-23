from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select
from .parser import parse_pdf
from ..models import DocumentVersion, DocumentNode, TestCase

def ingest(db: Session, path: str, filename: str, document_key: str = "ct200") -> tuple[DocumentVersion, bool]:
    """Ingest a PDF file, create a new document version if content has changed,
    persist hierarchical nodes, and update test case staleness."""
    file_hash, parsed_nodes = parse_pdf(path)
    
    # Check if identical version already exists
    existing = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_key == document_key, DocumentVersion.file_hash == file_hash)
        .order_by(DocumentVersion.version.desc())
    ).first()
    if existing:
        return existing, False

    # Get latest version number
    latest_version = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_key == document_key)
        .order_by(DocumentVersion.version.desc())
    ).first()
    next_version = (latest_version.version + 1) if latest_version else 1

    record = DocumentVersion(
        document_key=document_key,
        version=next_version,
        filename=filename,
        file_hash=file_hash,
        created_at=datetime.now(timezone.utc).isoformat()
    )
    db.add(record)
    db.flush()

    for n in parsed_nodes:
        db.add(DocumentNode(
            version_id=record.id,
            node_key=n.node_key,
            parent_key=n.parent_key,
            title=n.title,
            node_type=n.node_type,
            level=n.level,
            content=n.content,
            content_hash=n.content_hash,
            page=n.page
        ))
    db.flush()

    # Update staleness of earlier test cases
    update_all_test_cases_staleness(db, document_key=document_key, latest_version_id=record.id)

    db.commit()
    db.refresh(record)
    return record, True

def update_all_test_cases_staleness(db: Session, document_key: str, latest_version_id: int):
    """Evaluate and update staleness status and reasons for all test cases
    pinned to prior versions of the given document key."""
    latest_nodes = db.scalars(
        select(DocumentNode).where(DocumentNode.version_id == latest_version_id)
    ).all()
    latest_by_key = {n.node_key: n for n in latest_nodes}

    # Retrieve all test cases for earlier versions of this document
    prior_versions = db.scalars(
        select(DocumentVersion.id).where(
            DocumentVersion.document_key == document_key,
            DocumentVersion.id != latest_version_id
        )
    ).all()
    if not prior_versions:
        return

    test_cases = db.scalars(
        select(TestCase).where(TestCase.version_id.in_(prior_versions))
    ).all()

    for tc in test_cases:
        target_node = latest_by_key.get(tc.node_key)
        if target_node is None:
            tc.stale = True
            tc.stale_reason = "source_deleted"
        elif target_node.content_hash != tc.selected_hash:
            tc.stale = True
            tc.stale_reason = "source_modified"
        else:
            tc.stale = False
            tc.stale_reason = None

def compute_diff(db: Session, from_version_id: int, to_version_id: int) -> dict:
    """Compute detailed logical structural and content diff between two document versions."""
    from_nodes = db.scalars(select(DocumentNode).where(DocumentNode.version_id == from_version_id)).all()
    to_nodes = db.scalars(select(DocumentNode).where(DocumentNode.version_id == to_version_id)).all()

    if not from_nodes or not to_nodes:
        return {"error": "One or both document versions not found"}

    from_map = {n.node_key: n for n in from_nodes}
    to_map = {n.node_key: n for n in to_nodes}

    added = sorted(set(to_map.keys()) - set(from_map.keys()))
    removed = sorted(set(from_map.keys()) - set(to_map.keys()))
    
    changed = []
    changed_details = []
    for k in sorted(set(from_map.keys()) & set(to_map.keys())):
        if from_map[k].content_hash != to_map[k].content_hash:
            changed.append(k)
            changed_details.append({
                "node_key": k,
                "title": to_map[k].title,
                "from_hash": from_map[k].content_hash,
                "to_hash": to_map[k].content_hash,
                "diff_type": "content_modified"
            })

    return {
        "from_version_id": from_version_id,
        "to_version_id": to_version_id,
        "added": added,
        "removed": removed,
        "changed": changed,
        "changed_details": changed_details
    }
