from sqlalchemy import String, Integer, Text, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_key: Mapped[str] = mapped_column(String(200), index=True)
    version: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(300))
    file_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))
    nodes: Mapped[list["DocumentNode"]] = relationship(
        back_populates="version_record",
        cascade="all, delete-orphan",
        order_by="DocumentNode.id"
    )
    test_cases: Mapped[list["TestCase"]] = relationship(
        back_populates="version_record",
        cascade="all, delete-orphan"
    )
    selections: Mapped[list["Selection"]] = relationship(
        back_populates="version_record",
        cascade="all, delete-orphan"
    )

class DocumentNode(Base):
    __tablename__ = "document_nodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    node_key: Mapped[str] = mapped_column(String(300), index=True)
    parent_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    node_type: Mapped[str] = mapped_column(String(40), default="paragraph")
    level: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    page: Mapped[int] = mapped_column(Integer, default=1)
    version_record: Mapped[DocumentVersion] = relationship(back_populates="nodes")

class Selection(Base):
    __tablename__ = "selections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    node_ids: Mapped[str] = mapped_column(Text)  # JSON-encoded list of ints
    node_keys: Mapped[str] = mapped_column(Text) # JSON-encoded list of strings
    created_at: Mapped[str] = mapped_column(String(40))
    version_record: Mapped[DocumentVersion] = relationship(back_populates="selections")

class TestCase(Base):
    __tablename__ = "test_cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    selection_id: Mapped[int | None] = mapped_column(ForeignKey("selections.id"), nullable=True, index=True)
    node_key: Mapped[str] = mapped_column(String(300), index=True)
    selected_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500))
    steps: Mapped[str] = mapped_column(Text)
    expected_result: Mapped[str] = mapped_column(Text)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version_record: Mapped[DocumentVersion] = relationship(back_populates="test_cases")
