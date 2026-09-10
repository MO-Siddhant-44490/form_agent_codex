"""Deterministic Slice 1 planner: maps facts to observed fields by name and
proposes exactly one typed action for the first unsatisfied field. No model
calls — model-assisted mapping arrives in Slice 3 behind the same contract."""

from form_contracts import (
    ActionKind,
    ActionMethodHint,
    BrowserAction,
    DocumentFact,
    ExpectedEffect,
    FieldPurpose,
    FormField,
    PageObservation,
    RiskLevel,
)

# Fields the agent never writes: the human's alone (invariant 2).
HUMAN_ONLY_PURPOSES = frozenset({FieldPurpose.CREDENTIAL, FieldPurpose.CAPTCHA})

KIND_FOR_INPUT_TYPE: dict[str, ActionKind] = {
    "text": ActionKind.SET_TEXT,
    "email": ActionKind.SET_TEXT,
    "tel": ActionKind.SET_TEXT,
    "url": ActionKind.SET_TEXT,
    "textarea": ActionKind.SET_TEXT,
    # A masked identifier (Aadhaar/PAN rendered as a password input) is typed
    # like text; a real credential never reaches the planner (purpose gate).
    "password": ActionKind.SET_TEXT,
    "date": ActionKind.SET_DATE,
    "number": ActionKind.SET_NUMBER,
    "select-one": ActionKind.SELECT_OPTION,
    "combobox": ActionKind.SELECT_OPTION,
    "radio": ActionKind.SET_RADIO,
    "checkbox": ActionKind.SET_CHECKBOX,
}


def match_fact(field: FormField, facts_by_key: dict[str, DocumentFact]) -> DocumentFact | None:
    """Direct (high-trust) mapping: match on the field's name attribute, exact
    first then case-insensitively (a control named "District" is the same field
    as a "district" fact — case is a presentation detail, not identity)."""
    name = field.target.name_attr
    if name is None:
        return None
    if name in facts_by_key:
        return facts_by_key[name]
    lowered = name.strip().lower()
    for key, fact in facts_by_key.items():
        if key.strip().lower() == lowered:
            return fact
    return None


def desired_checked(fact: DocumentFact) -> bool:
    return fact.value.strip().lower() in {"true", "yes", "1", "on"}


def is_satisfied(field: FormField, fact: DocumentFact) -> bool:
    if field.input_type == "checkbox":
        return field.checked == desired_checked(fact)
    if field.value_redacted:
        return redacted_holds(field, fact.value)
    return field.current_value == fact.value


def redacted_holds(field: FormField, value: str | None) -> bool:
    """A redacted (masked) field never reports its value; the strongest check
    available without the value leaving the page is that it holds a value of
    the expected length."""
    return bool(value) and field.value_length == len(value)


def mappable_fields(
    observation: PageObservation, facts_by_key: dict[str, DocumentFact]
) -> list[tuple[FormField, DocumentFact]]:
    pairs: list[tuple[FormField, DocumentFact]] = []
    for field in observation.fields:
        if field.disabled or field.readonly or field.input_type not in KIND_FOR_INPUT_TYPE:
            continue
        if field.purpose in HUMAN_ONLY_PURPOSES:
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
        if field.required
        and not field.disabled
        and field.purpose not in HUMAN_ONLY_PURPOSES
        and match_fact(field, facts_by_key) is None
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
    method_hint: "ActionMethodHint | None" = None,
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
        method_hint=method_hint,
    )


def assignment_satisfied(field: FormField, value: str | None, checked: bool | None) -> bool:
    if checked is not None:
        return field.checked == checked
    if field.value_redacted:
        return redacted_holds(field, value)
    if field.current_value == value:
        return True
    # Option controls store the option CODE; `value` may be the human label
    # (an optimistic cascading-dropdown assignment made before options loaded,
    # e.g. "Thane" vs code "476"). Satisfied when the selected option's label
    # matches, in either direction.
    cur = field.current_value
    if value is None or cur is None:
        return False
    if cur.strip().lower() == value.strip().lower():
        return True
    options = field.options or []
    labels = field.option_labels or []
    if cur in options:
        idx = options.index(cur)
        if idx < len(labels) and labels[idx].strip().lower() == value.strip().lower():
            return True
    return False


def normalize_value(value: str, field: FormField) -> str | None:
    """Reformat a value into a shape the field is more likely to accept, for the
    NORMALIZE_VALUE recovery strategy. Deterministic and conservative: returns a
    *different* candidate string, or None when there is nothing safe to change
    (recovery then falls through to the next rung). No guessing of new content —
    only reshaping the value we already have.

    - Collapse runs of whitespace and trim (a stray double space or trailing
      space is a common reason an option/value fails to match).
    - For a date field, offer the common alternate ISO<->DMY shape so a picker
      or text input that wants the other format can accept it.
    """
    collapsed = " ".join(value.split())

    if field.input_type == "date" or (field.target and field.target.role == "datepicker"):
        alt = _reshape_date(collapsed)
        if alt is not None and alt != value:
            return alt

    if collapsed != value:
        return collapsed
    return None


def _reshape_date(value: str) -> str | None:
    """ISO (YYYY-MM-DD) <-> day-first (DD/MM/YYYY), if the input matches one."""
    import re

    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if iso:
        y, m, d = iso.groups()
        return f"{d}/{m}/{y}"
    dmy = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", value)
    if dmy:
        d, m, y = dmy.groups()
        return f"{y}-{m}-{d}"
    return None
