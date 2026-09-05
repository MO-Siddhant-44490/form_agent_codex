"""Repository over the ORM models: the only way the rest of the backend
touches durable state. Redacts action payloads before persisting and keeps
the audit log append-only."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from form_contracts import ApprovalToken, BrowserAction, redact_mapping
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    ActionRow,
    ApprovalRow,
    Base,
    DocumentRow,
    EventRow,
    FieldMappingMemoryRow,
    IdempotencyRow,
    RunRow,
)


def make_engine(url: str = "sqlite+pysqlite:///:memory:") -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    kwargs: dict = {"connect_args": connect_args, "future": True}
    # A bare in-memory SQLite DB is per-connection; StaticPool shares one
    # connection so the whole app (and its threads) sees the same tables.
    if url in ("sqlite+pysqlite:///:memory:", "sqlite://"):
        from sqlalchemy.pool import StaticPool

        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return engine


class Repository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(engine, expire_on_commit=False, future=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # -- runs --------------------------------------------------------------

    def create_run(self, run_id: str, goal: str = "fill_only") -> None:
        with self.session() as s:
            s.add(RunRow(run_id=run_id, phase="ingest_documents", goal=goal))

    def update_run(self, run_id: str, **fields) -> None:
        with self.session() as s:
            run = s.get(RunRow, run_id)
            if run is None:
                raise KeyError(run_id)
            for key, value in fields.items():
                setattr(run, key, value)

    def get_run(self, run_id: str) -> RunRow | None:
        with self.session() as s:
            return s.get(RunRow, run_id)

    # -- append-only audit -------------------------------------------------

    def append_event(self, run_id: str, kind: str, payload: dict | None = None) -> int:
        with self.session() as s:
            last = s.execute(
                select(EventRow.seq).where(EventRow.run_id == run_id).order_by(EventRow.seq.desc())
            ).first()
            seq = (last[0] + 1) if last else 0
            s.add(
                EventRow(
                    run_id=run_id,
                    seq=seq,
                    kind=kind,
                    payload=redact_mapping(payload or {}),
                )
            )
            return seq

    def events(self, run_id: str) -> list[EventRow]:
        with self.session() as s:
            return list(
                s.execute(
                    select(EventRow).where(EventRow.run_id == run_id).order_by(EventRow.seq)
                ).scalars()
            )

    # -- actions -----------------------------------------------------------

    def record_action(
        self, action: BrowserAction, status: str | None, verification_status: str | None
    ) -> None:
        with self.session() as s:
            payload = redact_mapping(action.model_dump(mode="json"))
            existing = s.get(ActionRow, action.action_id)
            if existing is None:
                s.add(
                    ActionRow(
                        action_id=action.action_id,
                        run_id=action.run_id,
                        sequence_number=action.sequence_number,
                        kind=str(action.kind),
                        status=status,
                        verification_status=verification_status,
                        action_json=payload,
                    )
                )
            else:
                existing.status = status
                existing.verification_status = verification_status

    # -- idempotency (durable, invariant 6) --------------------------------

    def find_idempotent(self, key: str) -> dict | None:
        with self.session() as s:
            row = s.get(IdempotencyRow, key)
            return row.result_json if row else None

    def record_idempotent(self, key: str, run_id: str, action_id: str, result_json: dict) -> None:
        with self.session() as s:
            if s.get(IdempotencyRow, key) is None:
                s.add(
                    IdempotencyRow(
                        idempotency_key=key,
                        run_id=run_id,
                        action_id=action_id,
                        result_json=result_json,
                    )
                )

    # -- approvals (invariant 1) -------------------------------------------

    def store_approval(self, token: ApprovalToken) -> None:
        with self.session() as s:
            s.add(
                ApprovalRow(
                    token_id=token.token_id,
                    run_id=token.run_id,
                    origin=token.origin,
                    issued_at=token.issued_at,
                    expires_at=token.expires_at,
                    used=token.used,
                )
            )

    def consume_approval(self, token_id: str, run_id: str, origin: str) -> bool:
        """Atomically mark a valid token used; returns False if unusable.
        The single-use guarantee lives here, not in caller logic."""
        with self.session() as s:
            row = s.get(ApprovalRow, token_id)
            now = datetime.now(UTC)
            expires = row.expires_at if row is None else _aware(row.expires_at)
            if (
                row is None
                or row.used
                or row.run_id != run_id
                or row.origin != origin
                or expires <= now
            ):
                return False
            row.used = True
            return True

    # -- documents ---------------------------------------------------------

    def record_document(
        self,
        document_id: str,
        run_id: str | None,
        filename: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        storage_key: str,
    ) -> None:
        with self.session() as s:
            if s.get(DocumentRow, document_id) is None:
                s.add(
                    DocumentRow(
                        document_id=document_id,
                        run_id=run_id,
                        filename=filename,
                        mime_type=mime_type,
                        size_bytes=size_bytes,
                        sha256=sha256,
                        storage_key=storage_key,
                    )
                )

    # -- episodic mapping memory (value-free; fact key only) ----------------

    def recall_mapping(self, site: str, signature: str) -> str | None:
        with self.session() as s:
            row = s.get(FieldMappingMemoryRow, (site, signature))
            return row.fact_key if row else None

    def remember_mapping(
        self, site: str, signature: str, fact_key: str, input_type: str
    ) -> None:
        with self.session() as s:
            row = s.get(FieldMappingMemoryRow, (site, signature))
            if row is None:
                s.add(
                    FieldMappingMemoryRow(
                        site_key=site,
                        field_signature=signature,
                        fact_key=fact_key,
                        input_type=input_type,
                    )
                )
            else:
                # Reinforce the latest observed mapping for this signature.
                row.fact_key = fact_key
                row.input_type = input_type
                row.hits += 1


class RepositoryMappingMemory:
    """Durable MappingMemory backed by the Repository (survives restarts, so
    memory is genuinely cross-run). Structurally implements the MappingMemory
    protocol used by the mapper and driver."""

    def __init__(self, repo: "Repository") -> None:
        self._repo = repo

    def recall(self, site: str, signature: str) -> str | None:
        return self._repo.recall_mapping(site, signature)

    def remember(self, site: str, signature: str, fact_key: str, input_type: str) -> None:
        self._repo.remember_mapping(site, signature, fact_key, input_type)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
