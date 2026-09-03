"""Slice 3 driver: perceive -> map (deterministic first, model-assisted) ->
policy gate -> act -> verify, with hard budgets and classified outcomes
(plan.md §6). Every action passes the deterministic policy gate regardless
of what proposed it; blocked fields are reported, never silently skipped."""

from dataclasses import dataclass, field

from form_contracts import (
    DocumentFact,
    ModelCallMetadata,
    PolicyDecision,
    PolicyDecisionKind,
    QuestionKind,
    RejectionReason,
    RunOutcome,
    UserQuestion,
    VerificationResult,
    VerificationStatus,
)

from .mapper import DeterministicMapper, Mapper, MappingOutcome
from .planner import assignment_satisfied, build_action_for
from .policy import check_action
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
    questions: list[UserQuestion] = field(default_factory=list)
    policy_decisions: list[PolicyDecision] = field(default_factory=list)
    model_calls: list[ModelCallMetadata] = field(default_factory=list)
    detail: str | None = None


_REOBSERVE_REJECTIONS = {RejectionReason.STALE_OBSERVATION, RejectionReason.STALE_SEQUENCE}


def run_fill(
    transport: BrowserTransport,
    facts: list[DocumentFact],
    budgets: DriverBudgets | None = None,
    mapper: Mapper | None = None,
) -> DriveResult:
    """Fill every approved-mappable field on the attached page, verifying
    each action. Never submits (invariant 1)."""
    budgets = budgets or DriverBudgets()
    mapper = mapper or DeterministicMapper()
    facts_by_key = {f.key: f for f in facts}
    result = DriveResult(outcome=RunOutcome.FATAL_FAILURE, steps_used=0)

    session = transport.attach()
    observation = transport.observe()
    sequence = 0
    retries: dict[str, int] = {}
    blocked_fields: set[str] = set()
    mapping: MappingOutcome | None = None
    mapped_fingerprint: str | None = None

    while result.steps_used < budgets.max_steps:
        if observation.login_detected or observation.captcha_detected:
            result.outcome = RunOutcome.NEEDS_USER
            result.detail = "login or CAPTCHA present; human takeover required (invariant 2)"
            return result

        # Re-map only when the page structure changed (fingerprints are
        # value-free, so filling fields does not trigger remapping).
        if mapping is None or mapped_fingerprint != observation.page_fingerprint:
            mapping = mapper.map(observation, facts_by_key)
            mapped_fingerprint = observation.page_fingerprint
            result.model_calls.extend(mapping.model_calls)
            for question in mapping.questions:
                if all(q.question_id != question.question_id for q in result.questions):
                    result.questions.append(question)

        # Next unsatisfied, unblocked assignment in document order.
        next_assignment = None
        for field_id, assignment in mapping.assignments.items():
            if field_id in blocked_fields:
                continue
            fresh = next((f for f in observation.fields if f.field_id == field_id), None)
            if fresh is None:
                continue
            if not assignment_satisfied(fresh, assignment.value, assignment.checked):
                next_assignment = (field_id, assignment, fresh)
                break

        if next_assignment is None:
            return _finish(result, mapping, blocked_fields)

        field_id, assignment, fresh_field = next_assignment
        sequence += 1
        action = build_action_for(
            fresh_field,
            run_id=session.run_id,
            tab_id=session.tab_id,
            origin=session.origin,
            value=assignment.value,
            checked=assignment.checked,
            sequence_number=sequence,
            source_observation_seq=observation.observation_seq,
            value_ref=f"fact://{assignment.fact.fact_id}",
            attempt=retries.get(field_id, 0),
        )

        # Deterministic policy gate before dispatch (invariant: every action).
        decision = check_action(action, observation, mapping.approved_values())
        if decision.decision is PolicyDecisionKind.BLOCK:
            result.policy_decisions.append(decision)
            blocked_fields.add(field_id)
            continue
        result.policy_decisions.append(decision)

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
            result.filled_fields.append(field_id)
            retries.pop(field_id, None)
            continue
        if verification.status in (
            VerificationStatus.RETRYABLE_FAILURE,
            VerificationStatus.NEEDS_REPERCEPTION,
        ):
            retries[field_id] = retries.get(field_id, 0) + 1
            if retries[field_id] > budgets.max_retries_per_action:
                result.outcome = RunOutcome.BUDGET_EXHAUSTED
                result.detail = (
                    f"retry budget exhausted on {field_id} ({verification.failure_class})"
                )
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


def _finish(result: DriveResult, mapping: MappingOutcome, blocked_fields: set[str]) -> DriveResult:
    result.unmapped_required = [
        q.field_id
        for q in result.questions
        if q.kind is QuestionKind.MISSING_FACT and q.field_id is not None
    ]
    if result.questions or blocked_fields:
        result.outcome = RunOutcome.NEEDS_USER
        parts = []
        if result.questions:
            parts.append(f"{len(result.questions)} clarification question(s)")
        if blocked_fields:
            parts.append(f"{len(blocked_fields)} policy-blocked field(s)")
        result.detail = "; ".join(parts) + "; everything else filled and verified"
    else:
        result.outcome = RunOutcome.COMPLETED
        result.detail = "all mappable fields filled and verified; submission not attempted"
    return result
