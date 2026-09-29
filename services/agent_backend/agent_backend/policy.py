"""Deterministic policy gate (plan.md §7.5, Module 9 subset): every proposed
action is checked here before dispatch, whatever proposed it. Model output
grants nothing — this gate re-derives safety from the observation and the
provenance of the value (invariants 2, 5, 11)."""

from form_contracts import (
    ActionKind,
    BrowserAction,
    FieldPurpose,
    FormField,
    PageObservation,
    PolicyDecision,
    PolicyDecisionKind,
    PolicyRule,
)

from .adapt import candidates
from .mapper import match_option as _match_option
from .purpose import effective_purpose

# Field-mutating kinds must target a field present in the fresh observation.
VALUE_KINDS = frozenset(
    {
        ActionKind.SET_TEXT,
        ActionKind.SET_NUMBER,
        ActionKind.SET_DATE,
        ActionKind.SELECT_OPTION,
        ActionKind.SET_CHECKBOX,
        ActionKind.SET_RADIO,
    }
)

# A credential is decided by the field's PURPOSE (perception classifies it from
# the control's own text) — a masked identifier rendered as a password input is
# profile data, not a login secret. A password-type input with no purpose
# signal at all is treated as a credential by perception, so this stays safe.


def _block(action: BrowserAction, rule: PolicyRule, detail: str) -> PolicyDecision:
    return PolicyDecision(
        action_id=action.action_id,
        decision=PolicyDecisionKind.BLOCK,
        rule=rule,
        detail=detail,
    )


def check_action(
    action: BrowserAction,
    observation: PageObservation,
    approved_values: dict[str, str | None],
) -> PolicyDecision:
    """`approved_values` maps field_id -> the fact-derived value the mapper
    approved for it (None for checkbox state changes). Any value not in that
    map has no provenance and is blocked (invariant 11)."""
    # Normalize the kind: an action smuggled past schema validation may carry
    # a raw string, and the gate must still classify it correctly.
    kind = ActionKind(action.kind)

    if action.origin != observation.origin:
        return _block(
            action,
            PolicyRule.ORIGIN_MISMATCH,
            f"action origin {action.origin} != page origin {observation.origin}",
        )

    if kind is ActionKind.SUBMIT and not action.approval_token_id:
        return _block(action, PolicyRule.SUBMIT_WITHOUT_APPROVAL, "no approval token")

    if kind is ActionKind.UPLOAD_FILE:
        field = _find_field(observation, action)
        if field is None:
            return _block(action, PolicyRule.UNKNOWN_TARGET, "file field not in observation")
        if not field.visible:
            return _block(action, PolicyRule.HIDDEN_FIELD, f"{field.field_id} not visible")
        if action.upload_file is None:
            return _block(
                action, PolicyRule.VALUE_WITHOUT_PROVENANCE, "UPLOAD_FILE has no file reference"
            )
        return PolicyDecision(action_id=action.action_id, decision=PolicyDecisionKind.ALLOW)

    if kind in VALUE_KINDS:
        field = _find_field(observation, action)
        if field is None:
            return _block(
                action,
                PolicyRule.UNKNOWN_TARGET,
                f"target {action.target.field_id if action.target else None} "
                "not present in the fresh observation",
            )
        if not field.visible:
            return _block(action, PolicyRule.HIDDEN_FIELD, f"{field.field_id} is not visible")
        purpose = effective_purpose(field)
        if purpose is FieldPurpose.CREDENTIAL:
            return _block(
                action,
                PolicyRule.CREDENTIAL_FIELD,
                f"{field.field_id} is a credential; never filled (invariant 2)",
            )
        if purpose is FieldPurpose.CAPTCHA:
            return _block(
                action,
                PolicyRule.HUMAN_ONLY_FIELD,
                f"{field.field_id} is a captcha; the human completes it (invariant 2)",
            )
        if action.target and action.target.field_id not in approved_values:
            return _block(
                action,
                PolicyRule.VALUE_WITHOUT_PROVENANCE,
                f"no approved mapping for {action.target.field_id}",
            )
        if action.resolved_value is not None:
            approved = approved_values.get(action.target.field_id if action.target else "")
            # A deterministic reshape of the fact value (national phone number,
            # reformatted date, abbreviated-to-fit address) is still that fact.
            reshaped = approved is not None and action.resolved_value in candidates(
                field, approved, observation=observation
            )
            if action.resolved_value != approved and not reshaped:
                return _block(
                    action,
                    PolicyRule.VALUE_WITHOUT_PROVENANCE,
                    "resolved value differs from the fact-derived value",
                )
            # Enforce option membership only when the field actually exposes
            # options (a cascading/lazy combobox has none until opened — the
            # executor validates the option at click time). Match tolerantly
            # (case-insensitive) to align with the mapper.
            if (
                field.options
                and _match_option(action.resolved_value, field.options, field.option_labels) is None
            ):
                return _block(
                    action,
                    PolicyRule.UNSUPPORTED_OPTION,
                    f"{action.resolved_value!r} is not an observed option",
                )

    return PolicyDecision(action_id=action.action_id, decision=PolicyDecisionKind.ALLOW)


def _find_field(observation: PageObservation, action: BrowserAction) -> FormField | None:
    if action.target is None:
        return None
    for field in observation.fields:
        if field.field_id == action.target.field_id:
            return field
        if (
            action.target.name_attr is not None
            and field.target.name_attr == action.target.name_attr
        ):
            return field
    return None
