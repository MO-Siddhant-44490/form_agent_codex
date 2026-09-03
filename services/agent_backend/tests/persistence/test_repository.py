"""Repository: append-only audit, durable idempotency, single-use approvals,
redaction on write, and survival across a reopened engine (Module 10)."""

from datetime import UTC, datetime, timedelta

from agent_backend.persistence.repository import Repository, make_engine
from form_contracts import ApprovalToken, BrowserAction, ExpectedEffect, TargetDescriptor


def repo(url="sqlite+pysqlite:///:memory:"):
    return Repository(make_engine(url))


def sample_action(**overrides):
    base = dict(
        action_id="a-1",
        run_id="run-1",
        tab_id=1,
        origin="https://example.test",
        sequence_number=1,
        kind="SET_TEXT",
        target=TargetDescriptor(field_id="dob", role="textbox"),
        resolved_value="1998-04-17",
        expected_effect=ExpectedEffect(field_value="1998-04-17"),
        idempotency_key="run-1:dob:1998-04-17",
    )
    base.update(overrides)
    return BrowserAction.model_validate(base)


def test_events_are_append_only_and_ordered():
    r = repo()
    r.create_run("run-1")
    r.append_event("run-1", "page_observed", {"fields": 8})
    r.append_event("run-1", "action_executed", {"field": "dob"})
    events = r.events("run-1")
    assert [e.seq for e in events] == [0, 1]  # append-only, zero-based
    assert [e.kind for e in events] == ["page_observed", "action_executed"]


def test_action_payload_is_redacted_on_persist():
    r = repo()
    r.create_run("run-1")
    r.record_action(sample_action(), status="EXECUTED", verification_status="SUCCESS")
    with r.session() as s:
        from agent_backend.persistence.models import ActionRow

        row = s.get(ActionRow, "a-1")
        # resolved_value is sensitive: must be redacted in the stored payload.
        assert row.action_json["resolved_value"] == "[REDACTED]"
        assert row.status == "EXECUTED"


def test_idempotency_is_durable_across_reopen(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'r.db'}"
    r1 = repo(url)
    r1.create_run("run-1")
    r1.record_idempotent("k-1", "run-1", "a-1", {"status": "EXECUTED"})

    # New Repository over the same file: the record persists (invariant 6).
    r2 = repo(url)
    assert r2.find_idempotent("k-1") == {"status": "EXECUTED"}
    assert r2.find_idempotent("k-unknown") is None


def token(**overrides):
    now = datetime.now(UTC)
    base = dict(
        token_id="tok-1",
        run_id="run-1",
        origin="https://example.test",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    base.update(overrides)
    return ApprovalToken(**base)


def test_approval_is_single_use():
    r = repo()
    r.create_run("run-1")
    r.store_approval(token())
    assert r.consume_approval("tok-1", "run-1", "https://example.test") is True
    # Second consumption fails: single-use (invariant 1).
    assert r.consume_approval("tok-1", "run-1", "https://example.test") is False


def test_approval_rejects_wrong_origin_and_expiry():
    r = repo()
    r.create_run("run-1")
    r.store_approval(token(token_id="t-origin"))
    assert r.consume_approval("t-origin", "run-1", "https://evil.test") is False

    # An already-expired token cannot be built via the contract (correct), so
    # write the expired row directly to prove consume rejects on expiry.
    from agent_backend.persistence.models import ApprovalRow

    with r.session() as sess:
        sess.add(
            ApprovalRow(
                token_id="t-exp",
                run_id="run-1",
                origin="https://example.test",
                issued_at=datetime.now(UTC) - timedelta(minutes=10),
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
                used=False,
            )
        )
    assert r.consume_approval("t-exp", "run-1", "https://example.test") is False


def test_run_state_survives_reopen(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'runs.db'}"
    r1 = repo(url)
    r1.create_run("run-1", goal="fill_and_submit")
    r1.update_run("run-1", phase="verify", origin="https://example.test")
    r1.append_event("run-1", "action_executed", {})

    r2 = repo(url)
    run = r2.get_run("run-1")
    assert run.phase == "verify"
    assert run.goal == "fill_and_submit"
    assert len(r2.events("run-1")) == 1
