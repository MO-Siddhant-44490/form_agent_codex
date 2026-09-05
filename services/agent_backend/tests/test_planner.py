"""Planner: deterministic mapping, satisfaction detection, action shape."""

from agent_backend.facts import slice1_facts
from agent_backend.planner import (
    assignment_satisfied,
    build_action_for,
    match_fact,
    normalize_value,
    plan_next_action,
    unmapped_required_fields,
)
from agent_backend.transports.fake import FakeTransport
from form_contracts import (
    ActionKind,
    ActionMethodHint,
    DocumentFact,
    FactStatus,
    FactValueType,
    FormField,
    Sensitivity,
    TargetDescriptor,
)


def _field(**overrides) -> FormField:
    base = dict(
        field_id="district",
        target=TargetDescriptor(field_id="district", role="combobox", name_attr="District"),
        input_type="combobox",
    )
    base.update(overrides)
    return FormField.model_validate(base)


def _fact(key: str, value: str) -> DocumentFact:
    return DocumentFact(
        fact_id=f"f-{key}",
        key=key,
        value=value,
        value_type=FactValueType.STRING,
        confidence=1.0,
        sensitivity=Sensitivity.PUBLIC,
        status=FactStatus.USER_PROVIDED,
    )


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


def test_match_fact_is_case_insensitive_on_name():
    # A control named "District" is the same field as a "district" fact
    # (pgportal's state/district cascade uses capitalized name attributes).
    field = _field()  # name_attr="District"
    facts = {"district": _fact("district", "Thane")}
    matched = match_fact(field, facts)
    assert matched is not None and matched.value == "Thane"


def test_match_fact_prefers_exact_over_case_insensitive():
    field = _field(target=TargetDescriptor(field_id="x", role="combobox", name_attr="state"))
    facts = {"state": _fact("state", "MH"), "STATE": _fact("STATE", "wrong")}
    assert match_fact(field, facts).value == "MH"


def test_assignment_satisfied_matches_option_label_against_code():
    # A cascading dropdown is assigned the human label ("Thane") before its
    # options load; once selected, the field's value is the option CODE ("476").
    field = _field(current_value="476", options=["0", "476"], option_labels=["--Select--", "Thane"])
    assert assignment_satisfied(field, "Thane", None) is True


def test_assignment_satisfied_rejects_wrong_option_label():
    field = _field(current_value="0", options=["0", "476"], option_labels=["--Select--", "Thane"])
    assert assignment_satisfied(field, "Thane", None) is False


def test_normalize_value_collapses_whitespace():
    field = _field(input_type="text")
    assert normalize_value("Navi   Mumbai ", field) == "Navi Mumbai"


def test_normalize_value_reshapes_iso_date_to_day_first():
    field = _field(input_type="date")
    assert normalize_value("2001-05-09", field) == "09/05/2001"


def test_normalize_value_reshapes_day_first_to_iso():
    field = _field(input_type="date")
    assert normalize_value("09/05/2001", field) == "2001-05-09"


def test_normalize_value_returns_none_when_nothing_to_reshape():
    field = _field(input_type="text")
    assert normalize_value("Thane", field) is None


def test_build_action_for_carries_method_hint():
    field = _field(
        input_type="text",
        field_id="x",
        target=TargetDescriptor(field_id="x", role="textbox", name_attr="x"),
    )
    action = build_action_for(
        field,
        run_id="r",
        tab_id=1,
        origin="https://example.test",
        value="v",
        checked=None,
        sequence_number=1,
        source_observation_seq=1,
        value_ref="fact://f",
        method_hint=ActionMethodHint.WIDGET_UI,
    )
    assert action.method_hint is ActionMethodHint.WIDGET_UI


def test_prepare_reattempt_stages_interaction_hints():
    from agent_backend.driver import _prepare_reattempt
    from agent_backend.mapper import Assignment
    from form_contracts import RecoveryStrategy

    field = _field(input_type="combobox")
    assignment = Assignment(field=field, fact=_fact("district", "Thane"), value="Thane", checked=None)
    hints: dict = {}
    overrides: dict = {}

    _prepare_reattempt(RecoveryStrategy.ALT_SELECT, "district", field, assignment, hints, overrides)
    assert hints["district"] is ActionMethodHint.WIDGET_UI

    _prepare_reattempt(RecoveryStrategy.REAPPLY, "district", field, assignment, hints, overrides)
    assert hints["district"] is ActionMethodHint.ALTERNATE


def test_prepare_reattempt_normalizes_value():
    from agent_backend.driver import _prepare_reattempt
    from agent_backend.mapper import Assignment
    from form_contracts import RecoveryStrategy

    field = _field(input_type="text")
    assignment = Assignment(
        field=field, fact=_fact("locality", "Navi  Mumbai "), value="Navi  Mumbai ", checked=None
    )
    hints: dict = {}
    overrides: dict = {}
    _prepare_reattempt(
        RecoveryStrategy.NORMALIZE_VALUE, "locality", field, assignment, hints, overrides
    )
    assert overrides["locality"] == "Navi Mumbai"
