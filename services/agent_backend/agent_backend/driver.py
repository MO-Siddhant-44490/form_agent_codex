"""Slice 3 driver: perceive -> map (deterministic first, model-assisted) ->
policy gate -> act -> verify, with hard budgets and classified outcomes
(plan.md §6). Every action passes the deterministic policy gate regardless
of what proposed it; blocked fields are reported, never silently skipped."""

from dataclasses import dataclass, field

from form_contracts import (
    ActionKind,
    ActionMethodHint,
    BrowserAction,
    DocumentFact,
    ExpectedEffect,
    FailureClass,
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
from .memory import MappingMemory, field_signature, site_key
from .planner import assignment_satisfied, build_action_for, normalize_value
from .policy import check_action
from .recovery import RecoveryDecision, RecoveryPlanner
from .transport import BrowserTransport
from .validation import ValidationReport, validate_form


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
    validation: ValidationReport = field(default_factory=ValidationReport)
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


def _prepare_reattempt(
    strategy: RecoveryStrategy,
    field_id: str,
    fresh_field,
    assignment,
    method_hints: dict[str, ActionMethodHint],
    value_overrides: dict[str, str],
) -> None:
    """Apply an *interaction* recovery strategy by staging how the next attempt
    on this field is built: a method hint the executor honours, or a reshaped
    value. The loop rebuilds the action from this state on its next pass."""
    if strategy is RecoveryStrategy.ALT_SELECT:
        method_hints[field_id] = ActionMethodHint.WIDGET_UI
    elif strategy is RecoveryStrategy.REAPPLY:
        method_hints[field_id] = ActionMethodHint.ALTERNATE
    elif strategy is RecoveryStrategy.NORMALIZE_VALUE:
        base = value_overrides.get(field_id, assignment.value)
        if base is not None:
            reshaped = normalize_value(base, fresh_field)
            if reshaped is not None and reshaped != base:
                value_overrides[field_id] = reshaped


def run_fill(
    transport: BrowserTransport,
    facts: list[DocumentFact],
    budgets: DriverBudgets | None = None,
    mapper: Mapper | None = None,
    uploads: dict[str, UploadFileRef] | None = None,
    *,
    verify: bool = True,
    recover: bool = True,
    memory: MappingMemory | None = None,
) -> DriveResult:
    """`verify` and `recover` are ablation switches (plan.md §17), both on by
    default. verify=False runs open-loop (trust the executor, skip
    verification) — used to measure the value of the perceive-act-verify loop.
    recover=False disables bounded recovery (a failure blocks the field
    immediately) — used to measure recovery's contribution."""
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
    # Per-field recovery state staged for the NEXT attempt: a method hint the
    # executor honours (drive the widget UI / an alternate input method) and a
    # reshaped value. Cleared once the field succeeds.
    method_hints: dict[str, ActionMethodHint] = {}
    value_overrides: dict[str, str] = {}
    blocked_fields: set[str] = set()
    visited_pages: set[str] = set()
    dialogs_tried: dict[str, int] = {}
    uploaded_fields: set[str] = set()
    uploads = uploads or {}
    recovery = RecoveryPlanner()
    mapping: MappingOutcome | None = None
    mapped_fingerprint: str | None = None

    stability_waits = 0
    final_settles = 0
    while result.steps_used < budgets.max_steps:
        if observation.login_detected or observation.captcha_detected:
            result.outcome = RunOutcome.NEEDS_USER
            result.detail = "login or CAPTCHA present; human takeover required (invariant 2)"
            return result

        # Cascading dropdowns and dynamic fields load asynchronously (e.g.
        # selecting a country reveals state/district). If the DOM has not
        # settled, wait for it and re-perceive before mapping/acting, so
        # dependent fields and their options are present. Bounded per settle
        # cycle; reset once stable.
        if not observation.dom_stable and stability_waits < 3:
            stability_waits += 1
            sequence += 1
            transport.execute(
                _helper_action(observation, session, ActionKind.WAIT_FOR_STABLE_PAGE, sequence)
            )
            result.steps_used += 1
            new_obs = transport.observe()
            # Re-map when the settled page differs (new/changed fields).
            if new_obs.page_fingerprint != observation.page_fingerprint:
                mapping = None
            observation = new_obs
            continue
        stability_waits = 0

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

            # Before concluding the page is done, give any just-triggered
            # cascade (e.g. filling `state` fires an AJAX that loads the
            # `district` options) a chance to surface its dependent fields.
            # Wait for the DOM to settle and re-observe; if that reveals new
            # fillable assignments, remap and keep going. Bounded (a handful of
            # waits) so a genuinely complete page still finishes promptly even
            # when the cascade is slow to start.
            if final_settles < 3:
                final_settles += 1
                sequence += 1
                transport.execute(
                    _helper_action(
                        observation, session, ActionKind.WAIT_FOR_STABLE_PAGE, sequence
                    )
                )
                result.steps_used += 1
                settled = transport.observe()
                if settled.page_fingerprint != observation.page_fingerprint:
                    mapping = None  # new/changed fields -> remap
                observation = settled
                continue  # re-enter the loop: fill new fields, else settle again

            # Current page is fully filled. Advance to the next page if the
            # form exposes a "next" control; otherwise this is the final page.
            visited_pages.add(observation.page_fingerprint)
            nav = _find_next_control(observation)
            if nav is None:
                return _finish(result, mapping, blocked_fields, transport, verify)
            if len(visited_pages) > budgets.max_pages:
                result.outcome = RunOutcome.BUDGET_EXHAUSTED
                result.detail = f"page budget ({budgets.max_pages}) exhausted"
                return result

            sequence += 1
            nav_action = _navigation_action(observation, session, nav, sequence)
            decision = check_action(nav_action, observation, {})
            result.policy_decisions.append(decision)
            if decision.decision is PolicyDecisionKind.BLOCK:
                return _finish(result, mapping, blocked_fields, transport, verify)

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
                return _finish(result, mapping, blocked_fields, transport, verify)
            observation = new_observation
            continue

        field_id, assignment, fresh_field = next_assignment
        sequence += 1
        action = build_action_for(
            fresh_field,
            run_id=session.run_id,
            tab_id=session.tab_id,
            origin=session.origin,
            value=value_overrides.get(field_id, assignment.value),
            checked=assignment.checked,
            sequence_number=sequence,
            source_observation_seq=observation.observation_seq,
            value_ref=f"fact://{assignment.fact.fact_id}",
            attempt=retries.get(field_id, 0),
            method_hint=method_hints.get(field_id),
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
            # A failed action goes through bounded recovery. The executor
            # classifies WHY it failed (option not found, cascade pending, ...);
            # use that class so recovery picks a matched strategy, falling back
            # to a wait-then-reobserve for an unclassified failure. Only after
            # the ladder is exhausted is the field blocked and reported — the
            # run never crashes on one field.
            failure_class = outcome.result.failure_class or FailureClass.NAVIGATION_FAILED
            decision = recovery.plan(field_id, failure_class)
            result.recovery_decisions.append(decision)
            retries[field_id] = retries.get(field_id, 0) + 1
            if decision.strategy is RecoveryStrategy.STOP:
                blocked_fields.add(field_id)
                result.questions.append(
                    UserQuestion(
                        question_id=f"q-failed-{field_id}",
                        kind=QuestionKind.AMBIGUOUS_MAPPING,
                        prompt=(
                            f"Could not fill {field_id} ({outcome.result.error}); "
                            "please do it manually."
                        ),
                        field_id=field_id,
                    )
                )
                observation = transport.observe()
                continue
            if decision.strategy in (
                RecoveryStrategy.WAIT_STABLE,
                RecoveryStrategy.WAIT_CASCADE,
            ):
                sequence += 1
                transport.execute(
                    _helper_action(observation, session, ActionKind.WAIT_FOR_STABLE_PAGE, sequence)
                )
                result.steps_used += 1
            else:
                # ALT_SELECT / REAPPLY / NORMALIZE_VALUE stage how the next
                # attempt is driven (widget UI, alternate method, reshaped value).
                _prepare_reattempt(
                    decision.strategy,
                    field_id,
                    fresh_field,
                    assignment,
                    method_hints,
                    value_overrides,
                )
            # Re-observe (options may now be loaded), re-map, and the loop
            # re-attempts the field with a fresh key and any staged hint.
            mapping = None
            observation = transport.observe()
            continue

        verification = outcome.verification
        if verification is not None:
            result.verifications.append(verification)
        observation = outcome.observation or transport.observe()

        # Open-loop ablation (verify=False): trust the executor's EXECUTED and
        # move on, ignoring the verification result. This is how the value of
        # the perceive-act-verify loop is measured — a false success here is
        # exactly what the closed loop catches.
        if not verify:
            result.filled_fields.append(field_id)
            continue

        if verification is None:
            continue
        if verification.status is VerificationStatus.SUCCESS:
            result.filled_fields.append(field_id)
            retries.pop(field_id, None)
            method_hints.pop(field_id, None)
            value_overrides.pop(field_id, None)
            recovery.clear(field_id)
            # Episodic memory: record which fact filled this field (value-free —
            # only the fact KEY) so a repeat visit resolves it without the model.
            if memory is not None:
                memory.remember(
                    site_key(session.origin),
                    field_signature(fresh_field),
                    assignment.fact.key,
                    fresh_field.input_type,
                )
            continue
        if verification.status in (
            VerificationStatus.RETRYABLE_FAILURE,
            VerificationStatus.NEEDS_REPERCEPTION,
        ):
            # No-recovery ablation: a failure blocks the field immediately.
            if not recover:
                blocked_fields.add(field_id)
                result.questions.append(
                    UserQuestion(
                        question_id=f"q-norecover-{field_id}",
                        kind=QuestionKind.AMBIGUOUS_MAPPING,
                        prompt=f"Could not fill {field_id} (recovery disabled).",
                        field_id=field_id,
                    )
                )
                observation = transport.observe()
                continue
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
            remap = False
            if decision.strategy is RecoveryStrategy.SCROLL:
                sequence += 1
                transport.execute(_helper_action(observation, session, ActionKind.SCROLL, sequence))
                result.steps_used += 1
            elif decision.strategy in (
                RecoveryStrategy.WAIT_STABLE,
                RecoveryStrategy.WAIT_CASCADE,
            ):
                sequence += 1
                transport.execute(
                    _helper_action(observation, session, ActionKind.WAIT_FOR_STABLE_PAGE, sequence)
                )
                result.steps_used += 1
                # A cascade wait expects new/changed dependent fields: force a
                # remap so their freshly-loaded options are picked up.
                remap = decision.strategy is RecoveryStrategy.WAIT_CASCADE
            else:
                # ALT_SELECT / REAPPLY / NORMALIZE_VALUE stage how the next
                # attempt is driven; RETRY / REOBSERVE just re-attempt.
                _prepare_reattempt(
                    decision.strategy,
                    field_id,
                    fresh_field,
                    assignment,
                    method_hints,
                    value_overrides,
                )
            if remap:
                mapping = None
            # Fresh observation; the loop re-selects the still-unsatisfied field
            # and rebuilds the action with the bumped attempt and any staged hint.
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


def _finish(
    result: DriveResult,
    mapping: MappingOutcome,
    blocked_fields: set[str],
    transport: BrowserTransport,
    verify: bool = True,
) -> DriveResult:
    result.unmapped_required = [
        q.field_id
        for q in result.questions
        if q.kind is QuestionKind.MISSING_FACT and q.field_id is not None
    ]

    # Final validation pass (the "validating agent"): re-observe the whole form
    # and collect every remaining problem — lingering validation errors,
    # required-empty fields, fields that did not retain a value. Catches
    # form-level issues a per-field check cannot (e.g. an address-length rule).
    # Part of verification, so the open-loop (verify=False) ablation skips it.
    report = (
        validate_form(transport.observe(), set(result.filled_fields))
        if verify
        else ValidationReport()
    )
    result.validation = report

    has_problems = bool(result.questions or blocked_fields or report.issues)
    if has_problems:
        result.outcome = RunOutcome.NEEDS_USER
        parts = []
        if result.questions:
            parts.append(f"{len(result.questions)} clarification question(s)")
        if blocked_fields:
            parts.append(f"{len(blocked_fields)} policy-blocked field(s)")
        if report.issues:
            parts.append(f"{len(report.issues)} validation issue(s)")
        result.detail = "; ".join(parts) + "; everything else filled and verified"
    else:
        result.outcome = RunOutcome.COMPLETED
        result.detail = "all fields filled and validated; submission not attempted"
    return result
