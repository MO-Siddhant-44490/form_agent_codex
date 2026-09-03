"""VerificationResult and the failure taxonomy (plan.md §8.5, Module 4)."""

from enum import StrEnum

from pydantic import model_validator

from .common import StrictModel


class VerificationStatus(StrEnum):
    SUCCESS = "SUCCESS"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    NEEDS_REPERCEPTION = "NEEDS_REPERCEPTION"
    NEEDS_USER = "NEEDS_USER"
    BLOCKED = "BLOCKED"
    FATAL_FAILURE = "FATAL_FAILURE"


class FailureClass(StrEnum):
    ELEMENT_NOT_FOUND = "element_not_found"
    STALE_ELEMENT = "stale_element"
    VALUE_MISMATCH = "value_mismatch"
    VALIDATION_ERROR = "validation_error"
    PAGE_CHANGED = "page_changed"
    NAVIGATION_FAILED = "navigation_failed"
    PERMISSION_DENIED = "permission_denied"
    BOT_DETECTION = "bot_detection"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA_REQUIRED = "captcha_required"
    TIMEOUT = "timeout"
    UNSUPPORTED_WIDGET = "unsupported_widget"
    UNKNOWN = "unknown"


class RecommendedTransition(StrEnum):
    CONTINUE = "CONTINUE"
    REPERCEIVE = "REPERCEIVE"
    RETRY = "RETRY"
    ASK_USER = "ASK_USER"
    STOP = "STOP"


class VerificationEvidence(StrictModel):
    observed_value: str | None = None
    field_valid: bool | None = None
    validation_message: str | None = None
    page_fingerprint: str | None = None
    notes: str | None = None


_FAILURE_CLASS_REQUIRED = frozenset(
    {
        VerificationStatus.RETRYABLE_FAILURE,
        VerificationStatus.BLOCKED,
        VerificationStatus.FATAL_FAILURE,
    }
)


class VerificationResult(StrictModel):
    action_id: str
    status: VerificationStatus
    evidence: VerificationEvidence
    failure_class: FailureClass | None = None
    recommended_transition: RecommendedTransition

    @model_validator(mode="after")
    def _failure_class_consistency(self) -> "VerificationResult":
        if self.status is VerificationStatus.SUCCESS and self.failure_class is not None:
            raise ValueError("a SUCCESS verification must not carry a failure_class")
        if self.status in _FAILURE_CLASS_REQUIRED and self.failure_class is None:
            raise ValueError(f"{self.status} requires a failure_class")
        return self


class RecoveryStrategy(StrEnum):
    """Bounded recovery moves the recovery agent may select (plan.md §7.8).
    A ladder of these is walked per field; the same strategy is never repeated
    for the same failure, and the ladder terminates in STOP."""

    RETRY = "retry"  # re-attempt the same action with a fresh idempotency key
    REOBSERVE = "reobserve"  # take a fresh observation and re-map
    SCROLL = "scroll"  # scroll the target into view, then re-attempt
    WAIT_STABLE = "wait_stable"  # wait for the DOM to settle, then re-attempt
    ASK_USER = "ask_user"  # the value/field needs a human (e.g. bad validation)
    STOP = "stop"  # give up on this field; report it, do not loop
