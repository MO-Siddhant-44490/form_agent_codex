"""The repository against real PostgreSQL. Gated on TEST_DATABASE_URL (e.g.
postgresql+psycopg://form_agent:form_agent_dev@localhost:5432/form_agent after
`docker compose -f infra/compose.yaml up -d`). The same assertions as the
SQLite suite, proving the repository API is backend-neutral."""

import os
import uuid

import pytest
from agent_backend.persistence.repository import Repository, make_engine

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="set TEST_DATABASE_URL to run")


def test_run_and_audit_roundtrip_on_postgres():
    repo = Repository(make_engine(TEST_DB))
    run_id = f"run-{uuid.uuid4()}"
    repo.create_run(run_id, goal="fill_and_submit")
    repo.append_event(run_id, "page_observed", {"fields": 8})
    repo.append_event(run_id, "action_executed", {"field": "dob"})

    run = repo.get_run(run_id)
    assert run.goal == "fill_and_submit"
    assert [e.seq for e in repo.events(run_id)] == [0, 1]


def test_idempotency_and_approval_on_postgres():
    from datetime import UTC, datetime, timedelta

    from form_contracts import ApprovalToken

    repo = Repository(make_engine(TEST_DB))
    run_id = f"run-{uuid.uuid4()}"
    repo.create_run(run_id)
    repo.record_idempotent(f"{run_id}:k", run_id, "a-1", {"status": "EXECUTED"})
    assert repo.find_idempotent(f"{run_id}:k") == {"status": "EXECUTED"}

    token = ApprovalToken(
        token_id=f"tok-{uuid.uuid4()}",
        run_id=run_id,
        origin="https://example.test",
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    repo.store_approval(token)
    assert repo.consume_approval(token.token_id, run_id, "https://example.test") is True
    assert repo.consume_approval(token.token_id, run_id, "https://example.test") is False
