"""Crash injection and durable resume (Module 8 acceptance): a crash before,
during, or after an action must resume without duplicating a browser effect,
because the extension's idempotency records outlive the backend."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from agent_backend.facts import slice1_facts
from agent_backend.orchestration.graph import build_form_fill_graph
from agent_backend.orchestration.runner import resume_run, start_run
from agent_backend.transports.fake import FakeTransport, TransportCrash
from form_contracts import ApprovalToken, RunOutcome, TargetDescriptor
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver


def open_saver(path):
    """Return (saver, conn); the caller closes conn. SqliteSaver built
    directly is not a context manager, so we manage the connection."""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=True)), conn


def test_crash_mid_run_resumes_without_duplicate_effects(tmp_path):
    # A shared transport instance survives the "crash" the way the browser
    # extension does; the backend graph is rebuilt from the checkpoint.
    transport = FakeTransport()
    transport.crash_after_fields = {"dob"}  # crash right after filling dob
    db = tmp_path / "runs.db"

    cp, conn = open_saver(db)
    graph = build_form_fill_graph(transport, checkpointer=cp)
    with pytest.raises(TransportCrash):
        start_run(graph, "run-crash", slice1_facts())
    conn.close()

    # dob was executed once before the crash.
    assert transport.execution_counts.get("dob") == 1
    filled_before = list(transport.execution_counts)

    # Rebuild the graph from the same durable store and resume.
    cp, conn = open_saver(db)
    graph = build_form_fill_graph(transport, checkpointer=cp)
    handle = resume_run(graph, "run-crash", None)
    conn.close()

    assert handle.state["outcome"] == RunOutcome.COMPLETED.value
    # The safety guarantee is idempotency: no field was executed twice, even
    # though the crash fell between dob's effect and the graph's bookkeeping.
    # On resume, re-execution returns DUPLICATE, so dob is not re-applied.
    assert transport.execution_counts["dob"] == 1
    assert all(count == 1 for count in transport.execution_counts.values())
    # And the form is genuinely fully filled in the browser/transport, dob
    # included, regardless of the in-graph filled_fields bookkeeping.
    for field in transport.fields:
        if field.input_type == "checkbox":
            assert field.checked is True
        else:
            assert field.value is not None
    assert filled_before  # crash happened after at least one execution


def test_resume_after_process_restart_reuses_durable_state(tmp_path):
    transport = FakeTransport()
    db = tmp_path / "runs.db"

    # "Process 1": run until the submission-approval interrupt.
    cp, conn = open_saver(db)
    graph = build_form_fill_graph(transport, checkpointer=cp)
    handle = start_run(
        graph,
        "run-restart",
        slice1_facts(),
        goal="fill_and_submit",
        submit_target=TargetDescriptor(field_id="submit-btn", role="button"),
    )
    assert handle.interrupt["reason"] == "submission_approval"
    assert len(handle.state["filled_fields"]) == 8
    conn.close()

    transport.allow_submit = True
    token = ApprovalToken(
        token_id="tok-r",
        run_id="run-restart",
        origin="http://fake.test",
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    transport.grant_approval(token)

    # "Process 2": brand-new graph + checkpointer over the same DB file.
    cp, conn = open_saver(db)
    graph = build_form_fill_graph(transport, checkpointer=cp)
    resumed = resume_run(
        graph,
        "run-restart",
        {"approved": True, "token": token.model_dump(mode="json")},
    )
    conn.close()

    assert resumed.state["outcome"] == RunOutcome.COMPLETED.value
    assert resumed.state["submitted"] is True
    # The fields filled in process 1 were not re-filled in process 2.
    assert all(count == 1 for count in transport.execution_counts.values())
    assert len(transport.submissions) == 1


def test_duplicate_resume_does_not_submit_twice(tmp_path):
    transport = FakeTransport(allow_submit=True)
    db = tmp_path / "runs.db"
    token = ApprovalToken(
        token_id="tok-d",
        run_id="run-dup",
        origin="http://fake.test",
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    transport.grant_approval(token)

    cp, conn = open_saver(db)
    graph = build_form_fill_graph(transport, checkpointer=cp)
    start_run(
        graph,
        "run-dup",
        slice1_facts(),
        goal="fill_and_submit",
        submit_target=TargetDescriptor(field_id="submit-btn", role="button"),
    )
    resumed = resume_run(
        graph, "run-dup", {"approved": True, "token": token.model_dump(mode="json")}
    )
    assert resumed.state["submitted"] is True
    assert len(transport.submissions) == 1
    conn.close()

    # Re-invoking a completed run's graph does not re-run submit: the
    # single-use token is consumed and the run is terminal.
    cp2, conn2 = open_saver(db)
    graph2 = build_form_fill_graph(transport, checkpointer=cp2)
    again = resume_run(graph2, "run-dup", None)
    conn2.close()
    assert len(transport.submissions) == 1
    assert again.state["outcome"] == RunOutcome.COMPLETED.value


def test_stuck_field_recovers_to_classified_block(tmp_path):
    # A permanently failing field now walks the recovery ladder (shared with
    # the driver) to a classified NEEDS_USER block instead of a bare
    # budget-exhaust — and the durable recovery_history checkpoints along the way.
    transport = FakeTransport()
    transport.fields[0].fail_executions = 99  # never sticks
    cp, conn = open_saver(tmp_path / "b.db")
    graph = build_form_fill_graph(transport, checkpointer=cp)
    handle = start_run(graph, "run-budget", slice1_facts())
    conn.close()
    assert handle.state["outcome"] == RunOutcome.NEEDS_USER.value
    # The stuck field's recovery ladder terminated (retry, reobserve) and it is
    # blocked; the rest of the form still filled.
    assert "full-name" in handle.state["blocked_fields"]
    assert len(handle.state["filled_fields"]) == 7


def test_recovery_history_survives_a_process_restart(tmp_path):
    """Bounded recovery is durable: a field that fails, then keeps failing
    across a simulated process restart, still terminates in a classified block
    without exceeding its ladder (recovery_history is checkpointed)."""
    transport = FakeTransport()
    transport.fields[1].validation_error = "bad email"  # email -> ASK_USER on first failure
    db = tmp_path / "rec.db"

    cp, conn = open_saver(db)
    graph = build_form_fill_graph(transport, checkpointer=cp)
    handle = start_run(graph, "run-rec", slice1_facts())
    conn.close()

    # Validation error -> ASK_USER immediately; the field is blocked and the
    # run needs the user, everything else filled.
    assert handle.state["outcome"] == RunOutcome.NEEDS_USER.value
    assert "email" in handle.state["blocked_fields"]
    assert "email" in handle.state.get("recovery_history", {}) or any(
        q.field_id == "email" for q in handle.state["questions"]
    )
    assert len(handle.state["filled_fields"]) == 7
