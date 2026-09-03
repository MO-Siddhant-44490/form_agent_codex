"""Deterministic Slice 1 planner: maps facts to observed fields by name and
proposes exactly one typed action for the first unsatisfied field. No model
calls — model-assisted mapping arrives in Slice 3 behind the same contract."""

from form_contracts import (
    ActionKind,
    BrowserAction,
    DocumentFact,
    ExpectedEffect,
    FormField,
    PageObservation,
    RiskLevel,
)

KIND_FOR_INPUT_TYPE: dict[str, ActionKind] = {
    "text": ActionKind.SET_TEXT,
    "email": ActionKind.SET_TEXT,
    "tel": ActionKind.SET_TEXT,
    "url": ActionKind.SET_TEXT,
    "textarea": ActionKind.SET_TEXT,
    "date": ActionKind.SET_DATE,
    "number": ActionKind.SET_NUMBER,
    "select-one": ActionKind.SELECT_OPTION,
    "radio": ActionKind.SET_RADIO,
    "checkbox": ActionKind.SET_CHECKBOX,
}


def match_fact(field: FormField, facts_by_key: dict[str, DocumentFact]) -> DocumentFact | None:
    """Slice 1 mapping: exact match on the field's name attribute."""
    name = field.target.name_attr
    if name is not None and name in facts_by_key:
        return facts_by_key[name]
    return None


def desired_checked(fact: DocumentFact) -> bool:
    return fact.value.strip().lower() in {"true", "yes", "1", "on"}


def is_satisfied(field: FormField, fact: DocumentFact) -> bool:
    if field.input_type == "checkbox":
        return field.checked == desired_checked(fact)
    return field.current_value == fact.value


def mappable_fields(
    observation: PageObservation, facts_by_key: dict[str, DocumentFact]
) -> list[tuple[FormField, DocumentFact]]:
    pairs: list[tuple[FormField, DocumentFact]] = []
    for field in observation.fields:
        if field.disabled or field.readonly or field.input_type not in KIND_FOR_INPUT_TYPE:
            continue
        fact = match_fact(field, facts_by_key)
        if fact is not None:
            pairs.append((field, fact))
    return pairs


def unmapped_required_fields(
    observation: PageObservation, facts_by_key: dict[str, DocumentFact]
) -> list[str]:
    """Required fields with no matching fact: clarification territory
    (NEEDS_USER), never silent skipping or guessing."""
    return [
        field.field_id
        for field in observation.fields
        if field.required and not field.disabled and match_fact(field, facts_by_key) is None
    ]


def plan_next_action(
    observation: PageObservation,
    facts_by_key: dict[str, DocumentFact],
    *,
    sequence_number: int,
    attempt: int = 0,
) -> BrowserAction | None:
    """Propose one action for the first unsatisfied mappable field, or None
    when every mappable field is verified-satisfied.

    `attempt` distinguishes retries in the idempotency key: duplicate
    *delivery* of the same command must be a no-op, but a planned retry after
    a failed verification is a new command (invariant 6).
    """
    for field, fact in mappable_fields(observation, facts_by_key):
        if is_satisfied(field, fact):
            continue
        kind = KIND_FOR_INPUT_TYPE[field.input_type]
        is_checkbox = kind is ActionKind.SET_CHECKBOX
        checked = desired_checked(fact) if is_checkbox else None
        value = None if is_checkbox else fact.value
        suffix = f":try{attempt}" if attempt > 0 else ""
        return BrowserAction(
            action_id=f"{observation.run_id}:action-{sequence_number}",
            run_id=observation.run_id,
            tab_id=observation.tab_id,
            origin=observation.origin,
            sequence_number=sequence_number,
            kind=kind,
            target=field.target,
            value_ref=f"fact://{fact.fact_id}",
            resolved_value=value,
            expected_effect=ExpectedEffect(
                field_value=value,
                checked=checked,
                validation_error=False,
            ),
            risk=RiskLevel.LOW,
            idempotency_key=(
                f"{observation.run_id}:{field.field_id}:{checked if is_checkbox else value}{suffix}"
            ),
            source_observation_seq=observation.observation_seq,
        )
    return None


def build_action_for(
    field: FormField,
    *,
    run_id: str,
    tab_id: int,
    origin: str,
    value: str | None,
    checked: bool | None,
    sequence_number: int,
    source_observation_seq: int,
    value_ref: str,
    attempt: int = 0,
) -> BrowserAction:
    """Build the typed action applying an approved assignment to a field.
    Used by the mapping-driven driver; plan_next_action above remains the
    name-match-only convenience path.

    Unknown input types fall back to SET_TEXT rather than crashing: a hostile
    or buggy assignment must still reach the policy gate, which is the layer
    that vetoes it."""
    kind = KIND_FOR_INPUT_TYPE.get(field.input_type, ActionKind.SET_TEXT)
    suffix = f":try{attempt}" if attempt > 0 else ""
    return BrowserAction(
        action_id=f"{run_id}:action-{sequence_number}",
        run_id=run_id,
        tab_id=tab_id,
        origin=origin,
        sequence_number=sequence_number,
        kind=kind,
        target=field.target,
        value_ref=value_ref,
        resolved_value=value,
        expected_effect=ExpectedEffect(
            field_value=value,
            checked=checked,
            validation_error=False,
        ),
        risk=RiskLevel.LOW,
        idempotency_key=(
            f"{run_id}:{field.field_id}:{checked if value is None else value}{suffix}"
        ),
        source_observation_seq=source_observation_seq,
    )


def assignment_satisfied(field: FormField, value: str | None, checked: bool | None) -> bool:
    if checked is not None:
        return field.checked == checked
    return field.current_value == value
