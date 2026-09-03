"""Planner: deterministic mapping, satisfaction detection, action shape."""

from agent_backend.facts import slice1_facts
from agent_backend.planner import plan_next_action, unmapped_required_fields
from agent_backend.transports.fake import FakeTransport
from form_contracts import ActionKind


def facts_by_key():
    return {f.key: f for f in slice1_facts()}


def test_plans_first_unsatisfied_field_in_document_order():
    obs = FakeTransport().observe()
    action = plan_next_action(obs, facts_by_key(), sequence_number=1)
    assert action is not None
    assert action.target.field_id == "full-name"
    assert action.kind is ActionKind.SET_TEXT
    assert action.resolved_value == "Ada Lovelace"
    assert action.value_ref == "fact://fact-full-name"
    assert action.expected_effect.field_value == "Ada Lovelace"
    assert action.source_observation_seq == obs.observation_seq


def test_kind_selection_per_input_type():
    transport = FakeTransport()
    # Satisfy the earlier fields so later ones get planned.
    for field in transport.fields:
        if field.input_type == "select-one":
            expected_kind = ActionKind.SELECT_OPTION
            target = field.field_id
            break
        field.value = facts_by_key()[field.name].value
    obs = transport.observe()
    action = plan_next_action(obs, facts_by_key(), sequence_number=2)
    assert action is not None
    assert action.target.field_id == target
    assert action.kind is expected_kind


def test_checkbox_action_uses_checked_not_value():
    transport = FakeTransport()
    for field in transport.fields:
        if field.input_type == "checkbox":
            continue
        if field.input_type == "radio":
            field.checked = True
            field.value = "email"
        else:
            field.value = facts_by_key()[field.name].value
    obs = transport.observe()
    action = plan_next_action(obs, facts_by_key(), sequence_number=3)
    assert action is not None
    assert action.kind is ActionKind.SET_CHECKBOX
    assert action.resolved_value is None
    assert action.expected_effect.checked is True


def test_returns_none_when_everything_satisfied():
    transport = FakeTransport()
    for field in transport.fields:
        if field.input_type == "checkbox":
            field.checked = True
        elif field.input_type == "radio":
            field.checked = True
            field.value = "email"
        else:
            field.value = facts_by_key()[field.name].value
    obs = transport.observe()
    assert plan_next_action(obs, facts_by_key(), sequence_number=4) is None


def test_retry_attempt_changes_idempotency_key_only():
    obs = FakeTransport().observe()
    first = plan_next_action(obs, facts_by_key(), sequence_number=1, attempt=0)
    retry = plan_next_action(obs, facts_by_key(), sequence_number=2, attempt=1)
    assert first is not None and retry is not None
    assert first.idempotency_key != retry.idempotency_key
    assert retry.idempotency_key.endswith(":try1")
    assert first.target == retry.target


def test_unmapped_required_fields_reported():
    facts = facts_by_key()
    del facts["email"]
    obs = FakeTransport().observe()
    assert unmapped_required_fields(obs, facts) == ["email"]
