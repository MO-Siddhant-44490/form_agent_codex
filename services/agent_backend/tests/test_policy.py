"""Policy gate: every rule fires deterministically; model output grants
nothing (Module 9 subset, invariants 2, 5, 11)."""

from agent_backend.policy import check_action
from agent_backend.transports.fake import FakeTransport
from form_contracts import (
    BrowserAction,
    ExpectedEffect,
    FormField,
    PolicyDecisionKind,
    PolicyRule,
    TargetDescriptor,
)


def observation(extra_fields: list[FormField] | None = None):
    transport = FakeTransport()
    obs = transport.observe()
    if extra_fields:
        obs = obs.model_copy(update={"fields": [*obs.fields, *extra_fields]})
    return obs


def action(
    field_id: str = "full-name",
    value: str | None = "Ada Lovelace",
    kind: str = "SET_TEXT",
    origin: str = "http://fake.test",
    **overrides,
):
    base = dict(
        action_id="a-1",
        run_id="run-fake",
        tab_id=1,
        origin=origin,
        sequence_number=1,
        kind=kind,
        target=TargetDescriptor(field_id=field_id, role="textbox"),
        resolved_value=value,
        expected_effect=ExpectedEffect(field_value=value, validation_error=False),
        idempotency_key="k-1",
    )
    base.update(overrides)
    return BrowserAction.model_validate(base)


APPROVED = {"full-name": "Ada Lovelace"}


def test_approved_action_allowed():
    decision = check_action(action(), observation(), APPROVED)
    assert decision.decision is PolicyDecisionKind.ALLOW


def test_origin_mismatch_blocked():
    decision = check_action(action(origin="https://evil.test"), observation(), APPROVED)
    assert decision.rule is PolicyRule.ORIGIN_MISMATCH


def test_unknown_target_blocked():
    decision = check_action(action(field_id="ghost-field"), observation(), {"ghost-field": "x"})
    assert decision.rule is PolicyRule.UNKNOWN_TARGET


def test_credential_field_blocked_even_if_mapped():
    password = FormField(
        field_id="password",
        target=TargetDescriptor(field_id="password", role="textbox"),
        input_type="password",
        value_redacted=True,
    )
    decision = check_action(
        action(field_id="password", value="hunter2"),
        observation([password]),
        {"password": "hunter2"},  # even an "approved" mapping cannot allow this
    )
    assert decision.decision is PolicyDecisionKind.BLOCK
    assert decision.rule is PolicyRule.CREDENTIAL_FIELD


def test_hidden_field_blocked():
    hidden = FormField(
        field_id="honeypot",
        target=TargetDescriptor(field_id="honeypot", role="textbox"),
        input_type="text",
        visible=False,
    )
    decision = check_action(
        action(field_id="honeypot", value="x"),
        observation([hidden]),
        {"honeypot": "x"},
    )
    assert decision.rule is PolicyRule.HIDDEN_FIELD


def test_value_without_provenance_blocked():
    # Field is mapped, but the action carries a different value than approved.
    decision = check_action(action(value="Someone Else"), observation(), APPROVED)
    assert decision.rule is PolicyRule.VALUE_WITHOUT_PROVENANCE


def test_unapproved_field_blocked():
    decision = check_action(action(field_id="email", value="x@y.test"), observation(), APPROVED)
    assert decision.rule is PolicyRule.VALUE_WITHOUT_PROVENANCE


def test_unsupported_option_blocked():
    decision = check_action(
        action(field_id="country", value="Wakanda", kind="SELECT_OPTION"),
        observation(),
        {"country": "Wakanda"},
    )
    assert decision.rule is PolicyRule.UNSUPPORTED_OPTION


def test_submit_without_approval_blocked():
    # The Pydantic contract already refuses to construct a token-less SUBMIT;
    # the policy rule is defense-in-depth for actions arriving past schema
    # validation (a buggy or hostile command source), so simulate that with
    # model_construct.
    hostile = BrowserAction.model_construct(
        action_id="a-s",
        run_id="run-fake",
        tab_id=1,
        origin="http://fake.test",
        sequence_number=9,
        kind="SUBMIT",
        target=TargetDescriptor(field_id="submit-btn", role="button"),
        value_ref=None,
        resolved_value=None,
        expected_effect=ExpectedEffect(navigation_expected=True),
        risk="high",
        idempotency_key="k-submit",
        source_observation_seq=None,
        approval_token_id=None,
    )
    decision = check_action(hostile, observation(), APPROVED)
    assert decision.rule is PolicyRule.SUBMIT_WITHOUT_APPROVAL
