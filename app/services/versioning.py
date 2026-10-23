from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select
from .parser import parse_pdf
from ..models import DocumentVersion, DocumentNode, TestCase

def ingest(db: Session, path: str, filename: str, document_key: str = "ct200") -> tuple[DocumentVersion, bool]:
    # Ingest a new PDF file. If whole-document content hash matches an existing version,
    # return that version without duplicating rows. Otherwise, increment version counter.
    file_hash, parsed_nodes = parse_pdf(path)
    
    # Check if identical version already exists
    existing = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_key == document_key, DocumentVersion.file_hash == file_hash)
        .order_by(DocumentVersion.version.desc())
    ).first()
    if existing:
        return existing, False

    # Monotonically increment version for this document family
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

    # Store full snapshot of parsed nodes for this version
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

    # Re-evaluate staleness on all historical test cases against this newest version
    update_all_test_cases_staleness(db, document_key=document_key, latest_version_id=record.id)

    db.commit()
    db.refresh(record)
    return record, True

def update_all_test_cases_staleness(db: Session, document_key: str, latest_version_id: int):
    # Evaluates whether test cases pinned to older revisions are still accurate in the latest version.
    latest_nodes = db.scalars(
        select(DocumentNode).where(DocumentNode.version_id == latest_version_id)
    ).all()
    latest_by_key = {n.node_key: n for n in latest_nodes}

    # Find prior versions
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
            # Section was removed or renumbered out of existence in the newest version
            tc.stale = True
            tc.stale_reason = "source_deleted"
        elif target_node.content_hash != tc.selected_hash:
            # Section text/table has changed
            tc.stale = True
            tc.stale_reason = "source_modified"
        else:
            # Content is still identical
            tc.stale = False
            tc.stale_reason = None

def compute_diff(db: Session, from_version_id: int, to_version_id: int) -> dict:
    # Computes section-by-section diff between two versions using stable node_keys
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
