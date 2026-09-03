"""TabSession, ApprovalToken, budgets, and run state (plan.md §8.6)."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import RunOutcome, StrictModel, validate_origin


class TabSession(StrictModel):
    run_id: str
    tab_id: int
    origin: str
    page_fingerprint: str | None = None
    attached_at: datetime
    last_observation_seq: int = Field(default=0, ge=0)
    last_action_seq: int = Field(default=0, ge=0)

    _origin_ok = field_validator("origin")(validate_origin)


class ApprovalToken(StrictModel):
    """Scoped, single-use, origin-bound, expiring submit approval (invariant 1)."""

    token_id: str
    run_id: str
    origin: str
    scope: Literal["submit"] = "submit"
    issued_at: datetime
    expires_at: datetime
    used: bool = False

    _origin_ok = field_validator("origin")(validate_origin)

    @model_validator(mode="after")
    def _expiry_after_issue(self) -> "ApprovalToken":
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self


class BudgetState(StrictModel):
    """Hard caps that force termination with a classified outcome (invariant 12)."""

    max_steps: int = Field(gt=0)
    steps_used: int = Field(default=0, ge=0)
    max_retries_per_action: int = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    model_calls_used: int = Field(default=0, ge=0)
    max_wall_clock_seconds: int = Field(gt=0)

    def exhausted(self) -> bool:
        return self.steps_used >= self.max_steps or self.model_calls_used >= self.max_model_calls


class RunPhase(StrEnum):
    INGEST_DOCUMENTS = "ingest_documents"
    BUILD_FACT_STORE = "build_fact_store"
    ATTACH_TAB = "attach_tab"
    PERCEIVE = "perceive"
    CLASSIFY = "classify"
    MAP_FIELDS = "map_fields"
    CLARIFY = "clarify"
    POLICY_GATE = "policy_gate"
    ACT = "act"
    VERIFY = "verify"
    RECOVER = "recover"
    FINAL_REVIEW = "final_review"
    AWAIT_SUBMIT_APPROVAL = "await_submit_approval"
    SUBMIT = "submit"
    VERIFY_RECEIPT = "verify_receipt"
    TERMINAL = "terminal"


class RunState(StrictModel):
    run_id: str
    phase: RunPhase
    outcome: RunOutcome | None = None
    budgets: BudgetState
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _terminal_has_outcome(self) -> "RunState":
        if self.phase is RunPhase.TERMINAL and self.outcome is None:
            raise ValueError("a terminal run must carry a classified outcome (invariant 12)")
        if self.phase is not RunPhase.TERMINAL and self.outcome is not None:
            raise ValueError("outcome is only valid in the terminal phase")
        return self
