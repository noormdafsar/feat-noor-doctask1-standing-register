from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db.base import Base, new_id, utcnow


class Family(Base):
    """One master agreement and everything downstream of it.

    The unit of locking, of retrieval scoping, and of deliverable identity.
    """

    __tablename__ = "family"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("fam"))
    slug: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        # Content hash is the dedupe key: re-dropping the same bytes into the
        # watched folder is a no-op, not a second run.
        UniqueConstraint("family_id", "sha256", name="uq_document_family_sha"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("doc"))
    family_id: Mapped[str] = mapped_column(ForeignKey("family.id", ondelete="CASCADE"))
    sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String)
    mime: Mapped[str] = mapped_column(String)
    raw_text: Mapped[str] = mapped_column(Text, default="")

    doc_type: Mapped[str] = mapped_column(String, default="unclassified")
    doc_type_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    doc_type_tier: Mapped[str] = mapped_column(String, default="")

    # Set when classification never resolved. The document still contributes its
    # text to retrieval; it just cannot contribute typed facts.
    blocked_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    parent_document_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    doc_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Block(Base):
    """The provenance substrate.

    Every citation anywhere in this system is a block id plus an offset pair.
    Nothing else in the schema stores text offsets, so citations cannot drift.
    """

    __tablename__ = "block"
    __table_args__ = (Index("block_doc_idx", "document_id", "idx"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("blk"))
    document_id: Mapped[str] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    page: Mapped[int] = mapped_column(Integer, default=1)
    idx: Mapped[int] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class Chunk(Base):
    __tablename__ = "chunk"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("chk"))
    document_id: Mapped[str] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    family_id: Mapped[str] = mapped_column(String, index=True)
    block_ids: Mapped[list[str]] = mapped_column(ARRAY(String))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(settings.embed_dim))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=True)


class Fact(Base):
    """The typed output of extraction.

    A fact with no surviving span is deleted, never downgraded -- see
    app/graph/nodes.py::verify_spans.
    """

    __tablename__ = "fact"
    __table_args__ = (Index("fact_family_key_idx", "family_id", "key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("fct"))
    document_id: Mapped[str] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    family_id: Mapped[str] = mapped_column(String)
    key: Mapped[str] = mapped_column(String)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    effective_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String, default="verified")
    run_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    spans: Mapped[list["FactSpan"]] = relationship(
        back_populates="fact", cascade="all, delete-orphan", lazy="selectin"
    )


class FactSpan(Base):
    __tablename__ = "fact_span"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("spn"))
    fact_id: Mapped[str] = mapped_column(ForeignKey("fact.id", ondelete="CASCADE"))
    document_id: Mapped[str] = mapped_column(String)
    block_id: Mapped[str] = mapped_column(String)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)

    fact: Mapped[Fact] = relationship(back_populates="spans")


class Gap(Base):
    """A term the system looked for and could not support from the sources.

    Recorded rather than inferred. This table is the reason the register can say
    "not stated in sources" instead of producing a plausible number.
    """

    __tablename__ = "gap"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("gap"))
    family_id: Mapped[str] = mapped_column(String, index=True)
    term_key: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String)
    document_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    run_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RegisterRow(Base):
    __tablename__ = "register_row"
    __table_args__ = (UniqueConstraint("family_id", "term_key", name="uq_row_family_term"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("row"))
    family_id: Mapped[str] = mapped_column(String, index=True)
    term_key: Mapped[str] = mapped_column(String)
    current_value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String, default="settled")  # settled|contested|absent|blocked
    content_hash: Mapped[str] = mapped_column(String(64))
    derived_from_fact_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RegisterRowVersion(Base):
    """The changelog: what changed, when, and because of which source."""

    __tablename__ = "register_row_version"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("rev"))
    family_id: Mapped[str] = mapped_column(String, index=True)
    term_key: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run_id: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String)
    because_of_document_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conflict(Base):
    """Surfaced, never auto-resolved. Status moves only through the gate."""

    __tablename__ = "conflict"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("cfl"))
    family_id: Mapped[str] = mapped_column(String, index=True)
    term_key: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    candidate_fact_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String, default="open")  # open|accepted|rejected
    resolution: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Finding(Base):
    __tablename__ = "finding"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("fnd"))
    family_id: Mapped[str] = mapped_column(String, index=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    rule_id: Mapped[str] = mapped_column(String)
    rule_pack: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="rule")
    severity: Mapped[str] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(String, default="")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String, default="proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "run"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("run"))
    family_id: Mapped[str] = mapped_column(String, index=True)
    mode: Mapped[str] = mapped_column(String, default="full")  # full|incremental
    rule_pack: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="running")
    trigger_document_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Step(Base):
    """Behaviours 1 and 10 both read directly off this table."""

    __tablename__ = "step"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("stp"))
    run_id: Mapped[str] = mapped_column(String, index=True)
    node: Mapped[str] = mapped_column(String)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[str] = mapped_column(String, default="")
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    usd_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    replayed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NodeResult(Base):
    """Idempotency cache.

    This is what makes resume free rather than merely possible: a node that
    already committed for a given input hash is never re-executed, so no model
    call is paid for twice across a kill/restart.
    """

    __tablename__ = "node_result"
    __table_args__ = (
        UniqueConstraint("run_id", "node", "input_sha", name="uq_node_result"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("nrs"))
    run_id: Mapped[str] = mapped_column(String, index=True)
    node: Mapped[str] = mapped_column(String)
    input_sha: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelCall(Base):
    """Per-call cache, keyed on the request hash.

    Distinct from NodeResult: this survives a node being re-entered for a
    different reason, and it is what the kill/resume test counts to prove no
    duplicate spend.
    """

    __tablename__ = "model_call"
    __table_args__ = (UniqueConstraint("run_id", "request_sha", name="uq_model_call"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("mcl"))
    run_id: Mapped[str] = mapped_column(String, index=True)
    request_sha: Mapped[str] = mapped_column(String(64))
    task: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalBatch(Base):
    __tablename__ = "approval_batch"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("bat"))
    run_id: Mapped[str] = mapped_column(String, index=True)
    family_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|resolved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalItem(Base):
    __tablename__ = "approval_item"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("itm"))
    batch_id: Mapped[str] = mapped_column(ForeignKey("approval_batch.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String)  # row_update | finding | conflict | escalation
    ref_id: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    decision: Mapped[str] = mapped_column(String, default="pending")  # pending|accept|reject
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
