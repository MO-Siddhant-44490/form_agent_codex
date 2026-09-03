"""Graph transition tests with the fake transport (no real model/browser):
completion, model-assisted path, clarification interrupt."""

from datetime import UTC, datetime, timedelta

from agent_backend.facts import slice1_facts
from agent_backend.mapper import ModelAssistedMapper
from agent_backend.model_gateway.fake import FakeModelAdapter
from agent_backend.orchestration.graph import build_form_fill_graph
from agent_backend.orchestration.runner import resume_run, start_run
from agent_backend.transports.fake import FakeTransport, basic_form_fields
from form_contracts import ApprovalToken, RunOutcome, Sensitivity, TargetDescriptor


def test_fill_only_completes():
    transport = FakeTransport()
    graph = build_form_fill_graph(transport)
    handle = start_run(graph, "run-1", slice1_facts())

    assert handle.interrupt is None
    assert handle.state["outcome"] == RunOutcome.COMPLETED.value
    assert len(handle.state["filled_fields"]) == 8
    assert transport.submissions == []  # fill_only never submits


def test_model_assisted_path_through_graph():
    fields = basic_form_fields()
    for i, f in enumerate(fields):
        f.name = f"fld_{i}"
    transport = FakeTransport(fields=fields)
    facts = [
        f.model_copy(update={"value": "India", "sensitivity": Sensitivity.PUBLIC})
        if f.key == "country"
        else f
        for f in slice1_facts()
    ]
    graph = build_form_fill_graph(transport, mapper=ModelAssistedMapper(FakeModelAdapter()))
    handle = start_run(graph, "run-2", facts)

    assert handle.state["outcome"] == RunOutcome.COMPLETED.value
    assert len(handle.state["filled_fields"]) == 8
    assert len(handle.state["model_calls"]) == 1


def test_missing_required_fact_ends_needs_user():
    facts = [f for f in slice1_facts() if f.key != "email"]
    transport = FakeTransport()
    graph = build_form_fill_graph(transport)
    handle = start_run(graph, "run-3", facts)

    assert handle.state["outcome"] == RunOutcome.NEEDS_USER.value
    question_fields = [q.field_id for q in handle.state["questions"]]
    assert "email" in question_fields
    # Everything else still filled.
    assert len(handle.state["filled_fields"]) == 7


def test_fill_and_submit_completes_with_approval():
    transport = FakeTransport(allow_submit=True)
    graph = build_form_fill_graph(transport)
    submit_target = TargetDescriptor(field_id="submit-btn", role="button")
    handle = start_run(
        graph, "run-4", slice1_facts(), goal="fill_and_submit", submit_target=submit_target
    )

    # Paused at submission approval — nothing submitted yet.
    assert handle.interrupt is not None
    assert handle.interrupt["reason"] == "submission_approval"
    assert transport.submissions == []
    assert len(handle.state["filled_fields"]) == 8

    token = ApprovalToken(
        token_id="tok-1",
        run_id="run-4",
        origin=transport.attach().origin,
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    transport.grant_approval(token)
    resumed = resume_run(graph, "run-4", {"approved": True, "token": token.model_dump(mode="json")})

    assert resumed.state["outcome"] == RunOutcome.COMPLETED.value
    assert resumed.state["submitted"] is True
    assert len(transport.submissions) == 1


def test_declining_approval_cancels_without_submitting():
    transport = FakeTransport(allow_submit=True)
    graph = build_form_fill_graph(transport)
    handle = start_run(
        graph,
        "run-5",
        slice1_facts(),
        goal="fill_and_submit",
        submit_target=TargetDescriptor(field_id="submit-btn", role="button"),
    )
    assert handle.interrupt["reason"] == "submission_approval"

    resumed = resume_run(graph, "run-5", {"approved": False})
    assert resumed.state["outcome"] == RunOutcome.CANCELLED.value
    assert transport.submissions == []


def test_login_page_interrupts_for_takeover_then_resumes():
    transport = FakeTransport(login_page=True)
    graph = build_form_fill_graph(transport)
    handle = start_run(graph, "run-6", slice1_facts())

    # Interrupted at classify; nothing executed on a login wall.
    assert handle.interrupt is not None
    assert handle.interrupt["reason"] == "login"
    assert transport.received_kinds == []

    # User completes login; simulate by clearing the login flag, then resume.
    transport.login_page = False
    resumed = resume_run(graph, "run-6", {"resolved": True})
    assert resumed.state["outcome"] == RunOutcome.COMPLETED.value
    assert len(resumed.state["filled_fields"]) == 8
