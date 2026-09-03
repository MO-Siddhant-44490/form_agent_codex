"""Slice 1 driver: the deterministic perceive -> plan -> act -> verify loop
with hard budgets and classified terminal outcomes (plan.md §6). No LangGraph
yet — this loop is what Slice 4 lifts into a durable graph."""

from dataclasses import dataclass, field

from form_contracts import (
    DocumentFact,
    RejectionReason,
    RunOutcome,
    VerificationResult,
    VerificationStatus,
)

from .planner import plan_next_action, unmapped_required_fields
from .transport import BrowserTransport


@dataclass
class DriverBudgets:
    """Hard caps forcing termination with a classified outcome (invariant 12)."""

    max_steps: int = 40
    max_retries_per_action: int = 2


@dataclass
class DriveResult:
    outcome: RunOutcome
    steps_used: int
    filled_fields: list[str] = field(default_factory=list)
    verifications: list[VerificationResult] = field(default_factory=list)
    unmapped_required: list[str] = field(default_factory=list)
    detail: str | None = None


# Rejections that a fresh observation can cure; anything else is terminal.
_REOBSERVE_REJECTIONS = {RejectionReason.STALE_OBSERVATION, RejectionReason.STALE_SEQUENCE}


def run_fill(
    transport: BrowserTransport,
    facts: list[DocumentFact],
    budgets: DriverBudgets | None = None,
) -> DriveResult:
    """Fill every fact-mappable field on the attached page, verifying each
    action. Never submits: the goal is fill-and-stop-at-final-review
    (invariant 1); submission approval is out of the driver's hands."""
    budgets = budgets or DriverBudgets()
    facts_by_key = {f.key: f for f in facts}
    result = DriveResult(outcome=RunOutcome.FATAL_FAILURE, steps_used=0)

    transport.attach()
    observation = transport.observe()
    sequence = 0
    retries: dict[str, int] = {}

    while result.steps_used < budgets.max_steps:
        if observation.login_detected or observation.captcha_detected:
            result.outcome = RunOutcome.NEEDS_USER
            result.detail = "login or CAPTCHA present; human takeover required (invariant 2)"
            return result

        sequence += 1
        target_field = None
        action = plan_next_action(
            observation,
            facts_by_key,
            sequence_number=sequence,
            attempt=0,
        )
        if action is not None:
            target_field = action.target.field_id if action.target else None
            attempt = retries.get(target_field or "", 0)
            if attempt > 0:
                action = plan_next_action(
                    observation, facts_by_key, sequence_number=sequence, attempt=attempt
                )

        if action is None:
            missing = unmapped_required_fields(observation, facts_by_key)
            if missing:
                result.outcome = RunOutcome.NEEDS_USER
                result.unmapped_required = missing
                result.detail = "required fields lack facts; clarification needed"
            else:
                result.outcome = RunOutcome.COMPLETED
                result.detail = "all mappable fields filled and verified; submission not attempted"
            return result

        outcome = transport.execute(action)
        result.steps_used += 1

        if outcome.result.status == "REJECTED":
            if outcome.result.rejection_reason in _REOBSERVE_REJECTIONS:
                observation = transport.observe()
                continue
            result.outcome = RunOutcome.BLOCKED
            result.detail = f"rejected: {outcome.result.rejection_reason} ({outcome.result.error})"
            return result
        if outcome.result.status == "DUPLICATE":
            observation = transport.observe()
            continue
        if outcome.result.status == "FAILED":
            result.outcome = RunOutcome.FATAL_FAILURE
            result.detail = f"execution failed: {outcome.result.error}"
            return result

        verification = outcome.verification
        if verification is not None:
            result.verifications.append(verification)
        observation = outcome.observation or transport.observe()

        if verification is None:
            continue
        if verification.status is VerificationStatus.SUCCESS:
            if target_field:
                result.filled_fields.append(target_field)
                retries.pop(target_field, None)
            continue
        if verification.status in (
            VerificationStatus.RETRYABLE_FAILURE,
            VerificationStatus.NEEDS_REPERCEPTION,
        ):
            key = target_field or "unknown"
            retries[key] = retries.get(key, 0) + 1
            if retries[key] > budgets.max_retries_per_action:
                result.outcome = RunOutcome.BUDGET_EXHAUSTED
                result.detail = f"retry budget exhausted on {key} ({verification.failure_class})"
                return result
            observation = transport.observe()
            continue
        if verification.status is VerificationStatus.NEEDS_USER:
            result.outcome = RunOutcome.NEEDS_USER
            result.detail = "verification requests human takeover"
            return result
        if verification.status is VerificationStatus.BLOCKED:
            result.outcome = RunOutcome.BLOCKED
            result.detail = f"blocked: {verification.failure_class}"
            return result
        result.outcome = RunOutcome.FATAL_FAILURE
        result.detail = f"unrecoverable verification: {verification.failure_class}"
        return result

    result.outcome = RunOutcome.BUDGET_EXHAUSTED
    result.detail = f"step budget ({budgets.max_steps}) exhausted"
    return result
