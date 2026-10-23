import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select
try:
    from .parser import parse_pdf
    from ..models import DocumentVersion, DocumentNode, TestCase
except (ImportError, ValueError):
    from app.services.parser import parse_pdf  # type: ignore
    from app.models import DocumentVersion, DocumentNode, TestCase  # type: ignore

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

    # Store full snapshot of parsed nodes for this version, including rolled-up child content and subtree hashes
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
            effective_content=n.effective_content,
            subtree_hash=n.subtree_hash,
            cross_references=json.dumps(n.cross_references),
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
    # Checks both direct content hash and subtree hash, ensuring changes to child tables (e.g. 4.2.table.1)
    # properly invalidate test cases created on parent headings (e.g. 4.2).
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
        else:
            # Check both direct content hash and subtree hash!
            # If tc.selected_hash matches neither, the section or its children changed.
            current_hashes = {target_node.content_hash, target_node.subtree_hash}
            if tc.selected_hash not in current_hashes:
                tc.stale = True
                tc.stale_reason = "source_modified"
            else:
                tc.stale = False
                tc.stale_reason = None

from .matcher import match_document_sections

def compute_diff(db: Session, from_version_id: int, to_version_id: int) -> dict:
    # Computes section-by-section diff between two versions using matcher service.
    # Handles renumbered sections, title edits, content modifications, and subtree hashing.
    from_nodes = db.scalars(select(DocumentNode).where(DocumentNode.version_id == from_version_id)).all()
    to_nodes = db.scalars(select(DocumentNode).where(DocumentNode.version_id == to_version_id)).all()

    if not from_nodes or not to_nodes:
        return {"error": "One or both document versions not found"}

    report = match_document_sections(from_nodes, to_nodes)

    changed_details = []
    changed_keys = set()
    from_map = {n.node_key: n for n in from_nodes}
    to_map = {n.node_key: n for n in to_nodes}

    for m in report.matches:
        if m.is_modified or m.is_renumbered:
            changed_keys.add(m.from_key)
            fn = from_map.get(m.from_key)
            tn = to_map.get(m.to_key)
            f_hash = (fn.subtree_hash or fn.content_hash) if fn else ""
            t_hash = (tn.subtree_hash or tn.content_hash) if tn else ""
            changed_details.append({
                "node_key": m.from_key,
                "target_key": m.to_key,
                "title": m.to_title,
                "from_hash": f_hash,
                "to_hash": t_hash,
                "diff_type": m.match_type,
                "is_renumbered": m.is_renumbered
            })

    return {
        "from_version_id": from_version_id,
        "to_version_id": to_version_id,
        "added": report.added_keys,
        "removed": report.removed_keys,
        "changed": sorted(list(changed_keys)),
        "changed_details": changed_details
    }

