"""BrowserAction, ExpectedEffect, and ActionResult contracts (plan.md §8.3-8.4)."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .common import RiskLevel, StrictModel, validate_origin
from .observation import TargetDescriptor


class ActionKind(StrEnum):
    SET_TEXT = "SET_TEXT"
    SET_NUMBER = "SET_NUMBER"
    SELECT_OPTION = "SELECT_OPTION"
    SET_CHECKBOX = "SET_CHECKBOX"
    SET_RADIO = "SET_RADIO"
    SET_DATE = "SET_DATE"
    UPLOAD_FILE = "UPLOAD_FILE"
    CLICK = "CLICK"
    SCROLL = "SCROLL"
    DISMISS_DIALOG = "DISMISS_DIALOG"
    WAIT_FOR_STABLE_PAGE = "WAIT_FOR_STABLE_PAGE"
    NAVIGATE_NEXT = "NAVIGATE_NEXT"
    SUBMIT = "SUBMIT"


# Kinds that change page or form state and therefore require an expected
# effect and an idempotency key (invariant 6).
MUTATING_KINDS = frozenset(
    {
        ActionKind.SET_TEXT,
        ActionKind.SET_NUMBER,
        ActionKind.SELECT_OPTION,
        ActionKind.SET_CHECKBOX,
        ActionKind.SET_RADIO,
        ActionKind.SET_DATE,
        ActionKind.UPLOAD_FILE,
        ActionKind.CLICK,
        ActionKind.DISMISS_DIALOG,
        ActionKind.NAVIGATE_NEXT,
        ActionKind.SUBMIT,
    }
)


class ExpectedEffect(StrictModel):
    """What the verifier should observe after the action (invariant 7)."""

    field_value: str | None = None
    checked: bool | None = None
    selected_option: str | None = None
    validation_error: bool | None = None
    dialog_dismissed: bool | None = None
    navigation_expected: bool | None = None
    expected_url_prefix: str | None = None


class BrowserAction(StrictModel):
    action_id: str
    run_id: str
    tab_id: int
    origin: str
    sequence_number: int = Field(ge=0)
    kind: ActionKind
    target: TargetDescriptor | None = None
    # Values are referenced by fact id (fact://...) or question id
    # (answer://...); raw values are resolved only after policy approval.
    value_ref: str | None = None
    # The single policy-approved value the executor may apply. Populated by
    # the dispatcher after the policy gate; must never appear in traces
    # (redaction.py treats it as sensitive).
    resolved_value: str | None = None
    expected_effect: ExpectedEffect | None = None
    risk: RiskLevel = RiskLevel.LOW
    idempotency_key: str | None = None
    # Observation this action was planned from; executor rejects if stale.
    source_observation_seq: int | None = None
    # Required for SUBMIT (invariant 1).
    approval_token_id: str | None = None

    _origin_ok = field_validator("origin")(validate_origin)

    @model_validator(mode="after")
    def _mutating_requirements(self) -> "BrowserAction":
        if self.kind in MUTATING_KINDS:
            if not self.idempotency_key:
                raise ValueError(
                    f"{self.kind} is mutating and requires an idempotency_key (invariant 6)"
                )
            if self.expected_effect is None:
                raise ValueError(
                    f"{self.kind} is mutating and requires an expected_effect (invariant 6)"
                )
        if self.kind is ActionKind.SUBMIT and not self.approval_token_id:
            raise ValueError(
                "SUBMIT requires an approval_token_id from explicit user approval (invariant 1)"
            )
        return self


class ActionResultStatus(StrEnum):
    EXECUTED = "EXECUTED"
    DUPLICATE = "DUPLICATE"  # idempotency key already completed; not re-executed
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class RejectionReason(StrEnum):
    RUN_MISMATCH = "run_mismatch"
    TAB_MISMATCH = "tab_mismatch"
    ORIGIN_MISMATCH = "origin_mismatch"
    STALE_SEQUENCE = "stale_sequence"
    STALE_OBSERVATION = "stale_observation"
    MISSING_APPROVAL = "missing_approval"
    POLICY_BLOCK = "policy_block"
    UNSUPPORTED = "unsupported"


class ActionResult(StrictModel):
    action_id: str
    status: ActionResultStatus
    rejection_reason: RejectionReason | None = None
    error: str | None = None
    executed_at: datetime | None = None

    @model_validator(mode="after")
    def _rejection_needs_reason(self) -> "ActionResult":
        if self.status is ActionResultStatus.REJECTED and self.rejection_reason is None:
            raise ValueError("a REJECTED result must carry a machine-readable rejection_reason")
        if self.status is not ActionResultStatus.REJECTED and self.rejection_reason is not None:
            raise ValueError("rejection_reason is only valid on REJECTED results")
        return self
