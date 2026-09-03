"""Graph state: everything here must survive checkpointing. Transports and
mappers are runtime dependencies bound at graph build time, never state."""

from typing import Annotated, Literal, TypedDict

from form_contracts import (
    ApprovalToken,
    DocumentFact,
    ModelCallMetadata,
    PageObservation,
    PolicyDecision,
    TargetDescriptor,
    UserQuestion,
    VerificationResult,
)
from pydantic import BaseModel


class PlannedAssignment(BaseModel):
    """Serializable assignment (mapper Assignment, checkpoint-safe)."""

    field_id: str
    fact: DocumentFact
    value: str | None
    checked: bool | None


def _overwrite(_old, new):
    return new


class FormFillState(TypedDict, total=False):
    # Immutable run inputs
    run_id: str
    goal: Literal["fill_only", "fill_and_submit"]
    facts: list[DocumentFact]
    submit_target: TargetDescriptor | None

    # Session binding
    tab_id: int
    origin: str

    # Loop state
    observation: Annotated[PageObservation | None, _overwrite]
    assignments: dict[str, PlannedAssignment]
    mapped_fingerprint: str | None
    pending_field_id: str | None
    pending_action_json: dict | None  # BrowserAction dump; rebuilt on use
    sequence: int
    steps_used: int
    retries: dict[str, int]
    blocked_fields: list[str]
    filled_fields: list[str]
    questions: list[UserQuestion]
    policy_decisions: list[PolicyDecision]
    verifications: list[VerificationResult]
    model_calls: list[ModelCallMetadata]
    approval_token: ApprovalToken | None
    submitted: bool

    # Terminal
    outcome: str | None
    detail: str | None
