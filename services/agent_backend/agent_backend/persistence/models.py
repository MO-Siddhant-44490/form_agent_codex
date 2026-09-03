"""SQLAlchemy ORM models. JSON columns hold already-redacted payloads; raw
secrets and PII never reach a row (invariants 10, 11, §19)."""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    phase: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    origin: Mapped[str | None] = mapped_column(String, nullable=True)
    tab_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal: Mapped[str] = mapped_column(String, default="fill_only")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    events: Mapped[list["EventRow"]] = relationship(back_populates="run")
    actions: Mapped[list["ActionRow"]] = relationship(back_populates="run")


class EventRow(Base):
    """Append-only audit trail. Application code inserts; nothing updates or
    deletes (§19: audit events are append-only from application code)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # redacted

    run: Mapped[RunRow] = relationship(back_populates="events")

    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_event_run_seq"),)


class ActionRow(Base):
    __tablename__ = "actions"

    action_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), nullable=False, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    verification_status: Mapped[str | None] = mapped_column(String, nullable=True)
    action_json: Mapped[dict] = mapped_column(JSON)  # redacted (no resolved_value)

    run: Mapped[RunRow] = relationship(back_populates="actions")


class IdempotencyRow(Base):
    """Durable record that an idempotency key already produced an effect.
    Survives backend restarts so a replay after crash is a no-op (invariant 6)."""

    __tablename__ = "idempotency"

    idempotency_key: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), nullable=False, index=True)
    action_id: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApprovalRow(Base):
    """Submission approval tokens: scoped, single-use, origin-bound, expiring
    (invariant 1). `used` is set atomically on consumption."""

    __tablename__ = "approvals"

    token_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(default=False)


class DocumentRow(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String)
    mime_type: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String)
    storage_key: Mapped[str] = mapped_column(String)  # object-store key
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
