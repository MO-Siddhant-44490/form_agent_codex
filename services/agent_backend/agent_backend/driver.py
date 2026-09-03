"""Slice 3 driver: perceive -> map (deterministic first, model-assisted) ->
policy gate -> act -> verify, with hard budgets and classified outcomes
(plan.md §6). Every action passes the deterministic policy gate regardless
of what proposed it; blocked fields are reported, never silently skipped."""

from dataclasses import dataclass, field

from form_contracts import (
    ActionKind,
    BrowserAction,
    DocumentFact,
    ExpectedEffect,
    ModelCallMetadata,
    NavigationControl,
    NavigationKind,
    PageObservation,
    PolicyDecision,
    PolicyDecisionKind,
    QuestionKind,
    RecoveryStrategy,
    RejectionReason,
    RiskLevel,
    RunOutcome,
    TabSession,
    UploadFileRef,
    UserQuestion,
    VerificationResult,
    VerificationStatus,
)

from .mapper import DeterministicMapper, Mapper, MappingOutcome
from .planner import assignment_satisfied, build_action_for
from .policy import check_action
from .recovery import RecoveryDecision, RecoveryPlanner
from .transport import BrowserTransport


@dataclass
class DriverBudgets:
    """Hard caps forcing termination with a classified outcome (invariant 12)."""

    max_steps: int = 60
    max_retries_per_action: int = 2
    max_pages: int = 15


@dataclass
class DriveResult:
    outcome: RunOutcome
    steps_used: int
    filled_fields: list[str] = field(default_factory=list)
    verifications: list[VerificationResult] = field(default_factory=list)
    unmapped_required: list[str] = field(default_factory=list)
    questions: list[UserQuestion] = field(default_factory=list)
    policy_decisions: list[PolicyDecision] = field(default_factory=list)
    recovery_decisions: list[RecoveryDecision] = field(default_factory=list)
    model_calls: list[ModelCallMetadata] = field(default_factory=list)
    detail: str | None = None


_REOBSERVE_REJECTIONS = {RejectionReason.STALE_OBSERVATION, RejectionReason.STALE_SEQUENCE}


def _find_next_control(observation: PageObservation) -> NavigationControl | None:
    """The control that advances to the next page, if any."""
    for control in observation.navigation:
        if control.kind is NavigationKind.NEXT:
            return control
    return None


def _navigation_action(
    observation: PageObservation,
    session: TabSession,
    control: NavigationControl,
    sequence_number: int,
) -> BrowserAction:
    return BrowserAction(
        action_id=f"{session.run_id}:nav-{sequence_number}",
        run_id=session.run_id,
        tab_id=session.tab_id,
        origin=session.origin,
        sequence_number=sequence_number,
        kind=ActionKind.NAVIGATE_NEXT,
        target=control.target,
        expected_effect=ExpectedEffect(navigation_expected=True),
        risk=RiskLevel.LOW,
        idempotency_key=f"{session.run_id}:nav:{observation.page_fingerprint}",
        source_observation_seq=observation.observation_seq,
    )


def _dismiss_action(
    observation: PageObservation,
    session: TabSession,
    dialog,
    sequence_number: int,
) -> BrowserAction:
    return BrowserAction(
        action_id=f"{session.run_id}:dismiss-{sequence_number}",
        run_id=session.run_id,
        tab_id=session.tab_id,
        origin=session.origin,
        sequence_number=sequence_number,
        kind=ActionKind.DISMISS_DIALOG,
        target=dialog.dismiss_target,
        expected_effect=ExpectedEffect(dialog_dismissed=True),
        risk=RiskLevel.LOW,
        idempotency_key=f"{session.run_id}:dismiss:{dialog.dialog_id}",
        source_observation_seq=observation.observation_seq,
    )


def _upload_action(
    field,
    session: TabSession,
    upload: UploadFileRef,
    observation: PageObservation,
    sequence_number: int,
) -> BrowserAction:
    return BrowserAction(
        action_id=f"{session.run_id}:upload-{sequence_number}",
        run_id=session.run_id,
        tab_id=session.tab_id,
        origin=session.origin,
        sequence_number=sequence_number,
        kind=ActionKind.UPLOAD_FILE,
        target=field.target,
        upload_file=upload,
        expected_effect=ExpectedEffect(field_value=upload.filename),
        risk=RiskLevel.MEDIUM,
        idempotency_key=f"{session.run_id}:{field.field_id}:{upload.filename}",
        source_observation_seq=observation.observation_seq,
    )


def _pending_upload(observation, uploads, uploaded_fields, blocked_fields):
    """Next file field with an available upload, not yet done."""
    for field in observation.fields:
        if field.input_type != "file" or field.field_id in uploaded_fields:
            continue
        if field.field_id in blocked_fields:
            continue
        key = field.target.name_attr
        if key and key in uploads and field.current_value != uploads[key].filename:
            return field, uploads[key]
    return None


def _helper_action(
    observation: PageObservation,
    session: TabSession,
    kind: ActionKind,
    sequence_number: int,
) -> BrowserAction:
    """A non-mutating helper action (SCROLL / WAIT_FOR_STABLE_PAGE) used by
    recovery. These carry no target and no expected effect."""
    return BrowserAction(
        action_id=f"{session.run_id}:{kind.value.lower()}-{sequence_number}",
        run_id=session.run_id,
        tab_id=session.tab_id,
        origin=session.origin,
        sequence_number=sequence_number,
        kind=kind,
        source_observation_seq=observation.observation_seq,
    )


def run_fill(
    transport: BrowserTransport,
    facts: list[DocumentFact],
    budgets: DriverBudgets | None = None,
    mapper: Mapper | None = None,
    uploads: dict[str, UploadFileRef] | None = None,
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
    visited_pages: set[str] = set()
    dialogs_tried: dict[str, int] = {}
    uploaded_fields: set[str] = set()
    uploads = uploads or {}
    recovery = RecoveryPlanner()
    mapping: MappingOutcome | None = None
    mapped_fingerprint: str | None = None

    while result.steps_used < budgets.max_steps:
        if observation.login_detected or observation.captcha_detected:
            result.outcome = RunOutcome.NEEDS_USER
            result.detail = "login or CAPTCHA present; human takeover required (invariant 2)"
            return result

        # Dismiss a blocking dialog / cookie banner before interacting with
        # the form. Bounded: each dialog is tried at most twice, then left
        # alone so the run never loops on a persistent overlay.
        dialog = next(
            (
                d
                for d in observation.dialogs
                if d.dismiss_target is not None and dialogs_tried.get(d.dialog_id, 0) < 2
            ),
            None,
        )
        if dialog is not None:
            sequence += 1
            dismiss = _dismiss_action(observation, session, dialog, sequence)
            decision = check_action(dismiss, observation, {})
            result.policy_decisions.append(decision)
            dialogs_tried[dialog.dialog_id] = dialogs_tried.get(dialog.dialog_id, 0) + 1
            if decision.decision is PolicyDecisionKind.ALLOW:
                dismiss_outcome = transport.execute(dismiss)
                result.steps_used += 1
                observation = dismiss_outcome.observation or transport.observe()
            continue

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
            # Attach any pending file uploads before deciding the page is done.
            pending = _pending_upload(observation, uploads, uploaded_fields, blocked_fields)
            if pending is not None:
                upload_field, upload_ref = pending
                sequence += 1
                upload_action = _upload_action(
                    upload_field, session, upload_ref, observation, sequence
                )
                decision = check_action(upload_action, observation, {})
                result.policy_decisions.append(decision)
                if decision.decision is PolicyDecisionKind.BLOCK:
                    blocked_fields.add(upload_field.field_id)
                    continue
                up_outcome = transport.execute(upload_action)
                result.steps_used += 1
                if up_outcome.result.status == "EXECUTED":
                    uploaded_fields.add(upload_field.field_id)
                    if (
                        up_outcome.verification
                        and up_outcome.verification.status.value == "SUCCESS"
                    ):
                        result.filled_fields.append(upload_field.field_id)
                        result.verifications.append(up_outcome.verification)
                    # A file field we uploaded is no longer a missing-fact question.
                    result.questions = [
                        q for q in result.questions if q.field_id != upload_field.field_id
                    ]
                observation = up_outcome.observation or transport.observe()
                continue

            # Required file fields with no available upload need the user
            # (the mapper does not map file inputs to text facts).
            for f in observation.fields:
                if (
                    f.input_type == "file"
                    and f.required
                    and f.field_id not in uploaded_fields
                    and f.field_id not in blocked_fields
                ):
                    blocked_fields.add(f.field_id)
                    if all(q.field_id != f.field_id for q in result.questions):
                        result.questions.append(
                            UserQuestion(
                                question_id=f"q-upload-{f.field_id}",
                                kind=QuestionKind.MISSING_FACT,
                                prompt=f"No file provided for {f.label or f.field_id}",
                                field_id=f.field_id,
                            )
                        )

            # Current page is fully filled. Advance to the next page if the
            # form exposes a "next" control; otherwise this is the final page.
            visited_pages.add(observation.page_fingerprint)
            nav = _find_next_control(observation)
            if nav is None:
                return _finish(result, mapping, blocked_fields)
            if len(visited_pages) > budgets.max_pages:
                result.outcome = RunOutcome.BUDGET_EXHAUSTED
                result.detail = f"page budget ({budgets.max_pages}) exhausted"
                return result

            sequence += 1
            nav_action = _navigation_action(observation, session, nav, sequence)
            decision = check_action(nav_action, observation, {})
            result.policy_decisions.append(decision)
            if decision.decision is PolicyDecisionKind.BLOCK:
                return _finish(result, mapping, blocked_fields)

            nav_outcome = transport.execute(nav_action)
            result.steps_used += 1
            if nav_outcome.result.status != "EXECUTED":
                result.outcome = RunOutcome.BLOCKED
                result.detail = f"navigation rejected: {nav_outcome.result.rejection_reason}"
                return result

            new_observation = nav_outcome.observation or transport.observe()
            if (
                new_observation.page_fingerprint == observation.page_fingerprint
                or new_observation.page_fingerprint in visited_pages
            ):
                # Navigation did not reach a new page: stop rather than loop.
                result.detail = "navigation did not advance to a new page"
                return _finish(result, mapping, blocked_fields)
            observation = new_observation
            continue

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
            recovery.clear(field_id)
            continue
        if verification.status in (
            VerificationStatus.RETRYABLE_FAILURE,
            VerificationStatus.NEEDS_REPERCEPTION,
        ):
            # Bounded recovery: pick the next strategy from the failure ladder.
            decision = recovery.plan(field_id, verification.failure_class)
            result.recovery_decisions.append(decision)
            # A re-attempt must carry a fresh idempotency key.
            retries[field_id] = retries.get(field_id, 0) + 1

            if decision.strategy in (RecoveryStrategy.STOP, RecoveryStrategy.ASK_USER):
                blocked_fields.add(field_id)
                kind = (
                    QuestionKind.LOW_CONFIDENCE
                    if decision.strategy is RecoveryStrategy.ASK_USER
                    else QuestionKind.AMBIGUOUS_MAPPING
                )
                result.questions.append(
                    UserQuestion(
                        question_id=f"q-recover-{field_id}",
                        kind=kind,
                        prompt=(
                            f"Could not fill {field_id} "
                            f"({verification.failure_class}); needs your input."
                        ),
                        field_id=field_id,
                    )
                )
                observation = transport.observe()
                continue
            if decision.strategy is RecoveryStrategy.SCROLL:
                sequence += 1
                transport.execute(_helper_action(observation, session, ActionKind.SCROLL, sequence))
                result.steps_used += 1
            elif decision.strategy is RecoveryStrategy.WAIT_STABLE:
                sequence += 1
                transport.execute(
                    _helper_action(observation, session, ActionKind.WAIT_FOR_STABLE_PAGE, sequence)
                )
                result.steps_used += 1
            # RETRY / REOBSERVE (and after SCROLL/WAIT): fresh observation, the
            # loop re-selects the still-unsatisfied field and rebuilds the
            # action with the bumped attempt.
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
