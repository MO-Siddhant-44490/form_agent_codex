"""In-memory transport simulating a controlled form, with the same guard
semantics as the extension (idempotency, sequence, stale observation).
Used for deterministic driver tests without a browser."""

import hashlib
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime

from form_contracts import (
    ActionKind,
    ActionResult,
    ActionResultStatus,
    BrowserAction,
    FailureClass,
    FormField,
    PageClass,
    PageClassCandidate,
    PageObservation,
    RecommendedTransition,
    RejectionReason,
    TabSession,
    TargetDescriptor,
    VerificationEvidence,
    VerificationResult,
    VerificationStatus,
)

from ..transport import ExecuteOutcome

ORIGIN = "http://fake.test"
RUN_ID = "run-fake"
TAB_ID = 1


@dataclass
class FakeField:
    field_id: str
    input_type: str
    name: str
    label: str
    required: bool = False
    value: str | None = None
    checked: bool | None = None
    options: list[str] | None = None
    # Simulated flakiness: the first N executes do not stick (value_mismatch).
    fail_executions: int = 0


def basic_form_fields() -> list[FakeField]:
    return [
        FakeField("full-name", "text", "full_name", "Full name", required=True),
        FakeField("email", "email", "email", "Email address", required=True),
        FakeField("phone", "tel", "phone", "Phone number"),
        FakeField("dob", "date", "date_of_birth", "Date of birth", required=True),
        FakeField(
            "country",
            "select-one",
            "country",
            "Country",
            required=True,
            options=["", "DE", "IN", "US", "GB"],
        ),
        FakeField("experience", "number", "years_experience", "Years of experience", required=True),
        FakeField(
            "radio-group:contact_method",
            "radio",
            "contact_method",
            "Preferred contact method",
            required=True,
            options=["email", "phone"],
            checked=False,
        ),
        FakeField("subscribe", "checkbox", "subscribe", "Subscribe", checked=False),
    ]


@dataclass
class FakeTransport:
    fields: list[FakeField] = dc_field(default_factory=basic_form_fields)
    login_page: bool = False
    observation_seq: int = 0
    last_action_seq: int = 0
    executed_keys: dict[str, ActionResult] = dc_field(default_factory=dict)
    received_kinds: list[ActionKind] = dc_field(default_factory=list)
    closed: bool = False

    def attach(self) -> TabSession:
        return TabSession(
            run_id=RUN_ID,
            tab_id=TAB_ID,
            origin=ORIGIN,
            attached_at=datetime.now(UTC),
        )

    def _form_field(self, f: FakeField) -> FormField:
        return FormField(
            field_id=f.field_id,
            target=TargetDescriptor(
                field_id=f.field_id,
                role="radiogroup" if f.input_type == "radio" else "textbox",
                accessible_name=f.label,
                input_type=f.input_type,
                label=f.label,
                name_attr=f.name,
            ),
            input_type=f.input_type,
            label=f.label,
            accessible_name=f.label,
            required=f.required,
            checked=f.checked,
            current_value=f.value,
            options=f.options,
        )

    def observe(self) -> PageObservation:
        self.observation_seq += 1
        structure = ",".join(f"{f.field_id}|{f.input_type}" for f in self.fields)
        fingerprint = hashlib.sha256(structure.encode()).hexdigest()
        return PageObservation(
            run_id=RUN_ID,
            tab_id=TAB_ID,
            url=f"{ORIGIN}/form",
            origin=ORIGIN,
            title="Fake form",
            page_fingerprint=f"sha256:{fingerprint}",
            observation_seq=self.observation_seq,
            observed_at=datetime.now(UTC),
            classification=[
                PageClassCandidate(
                    label=PageClass.LOGIN if self.login_page else PageClass.FORM,
                    confidence=0.9,
                )
            ],
            fields=[self._form_field(f) for f in self.fields],
            login_detected=self.login_page,
        )

    def _result(
        self,
        action: BrowserAction,
        status: ActionResultStatus,
        reason: RejectionReason | None = None,
        error: str | None = None,
    ) -> ActionResult:
        return ActionResult(
            action_id=action.action_id,
            status=status,
            rejection_reason=reason,
            error=error,
            executed_at=datetime.now(UTC),
        )

    def execute(self, action: BrowserAction) -> ExecuteOutcome:
        self.received_kinds.append(action.kind)

        # SUBMIT must never reach a transport in Slice 1 (invariant 1); a
        # fake this strict makes any driver regression loud.
        if action.kind is ActionKind.SUBMIT:
            raise AssertionError("driver proposed SUBMIT: invariant 1 violation")

        if action.idempotency_key and action.idempotency_key in self.executed_keys:
            prior = self.executed_keys[action.idempotency_key]
            duplicate = prior.model_copy(update={"status": ActionResultStatus.DUPLICATE})
            return ExecuteOutcome(result=duplicate, verification=None, observation=None)
        if action.sequence_number <= self.last_action_seq:
            return ExecuteOutcome(
                result=self._result(
                    action, ActionResultStatus.REJECTED, RejectionReason.STALE_SEQUENCE, "stale"
                ),
                verification=None,
                observation=None,
            )
        if (
            action.source_observation_seq is not None
            and action.source_observation_seq != self.observation_seq
        ):
            return ExecuteOutcome(
                result=self._result(
                    action,
                    ActionResultStatus.REJECTED,
                    RejectionReason.STALE_OBSERVATION,
                    "stale obs",
                ),
                verification=None,
                observation=None,
            )

        target = next(
            (f for f in self.fields if action.target and f.field_id == action.target.field_id), None
        )
        if target is None:
            return ExecuteOutcome(
                result=self._result(action, ActionResultStatus.FAILED, error="element not found"),
                verification=None,
                observation=None,
            )

        # Apply the effect unless this field is simulating flakiness.
        if target.fail_executions > 0:
            target.fail_executions -= 1
        elif action.kind is ActionKind.SET_CHECKBOX:
            target.checked = bool(action.expected_effect and action.expected_effect.checked)
        elif action.kind is ActionKind.SET_RADIO:
            target.checked = True
            target.value = action.resolved_value
        else:
            target.value = action.resolved_value

        self.last_action_seq = action.sequence_number
        result = self._result(action, ActionResultStatus.EXECUTED)
        if action.idempotency_key:
            self.executed_keys[action.idempotency_key] = result

        observation = self.observe()
        verification = self._verify(action, target, observation)
        return ExecuteOutcome(result=result, verification=verification, observation=observation)

    def _verify(
        self, action: BrowserAction, target: FakeField, observation: PageObservation
    ) -> VerificationResult:
        expected = action.expected_effect
        evidence = VerificationEvidence(
            observed_value=target.value,
            page_fingerprint=observation.page_fingerprint,
        )
        ok = True
        if expected is not None:
            if expected.field_value is not None and target.value != expected.field_value:
                ok = False
            if expected.checked is not None and target.checked != expected.checked:
                ok = False
        if ok:
            return VerificationResult(
                action_id=action.action_id,
                status=VerificationStatus.SUCCESS,
                evidence=evidence,
                recommended_transition=RecommendedTransition.CONTINUE,
            )
        return VerificationResult(
            action_id=action.action_id,
            status=VerificationStatus.RETRYABLE_FAILURE,
            evidence=evidence,
            failure_class=FailureClass.VALUE_MISMATCH,
            recommended_transition=RecommendedTransition.RETRY,
        )

    def close(self) -> None:
        self.closed = True
