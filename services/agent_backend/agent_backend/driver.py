"""Slice 3 driver: perceive -> map (deterministic first, model-assisted) ->
policy gate -> act -> verify, with hard budgets and classified outcomes
(plan.md §6). Every action passes the deterministic policy gate regardless
of what proposed it; blocked fields are reported, never silently skipped."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from form_contracts import (
    ActionKind,
    ActionMethodHint,
    BrowserAction,
    DocumentFact,
    ExpectedEffect,
    FailureClass,
    FormField,
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

from .adapt import adapt, candidates, plausible_reshape, site_normalized, widget_shows
from .mapper import Assignment, DeterministicMapper, Mapper, MappingOutcome, user_binding
from .memory import MappingMemory, field_signature, site_key
from .planner import assignment_satisfied, build_action_for, normalize_value
from .policy import check_action
from .recovery import RecoveryDecision, RecoveryPlanner
from .transport import BrowserTransport, resilient
from .validation import ValidationReport, validate_form


@dataclass
class DriverBudgets:
    """Hard caps forcing termination with a classified outcome (invariant 12)."""

    # A long multi-page application (8+ pages, 40+ fields, settle waits per
    # page) needs headroom; still a hard cap.
    max_steps: int = 150
    max_retries_per_action: int = 2
    max_pages: int = 15
    # After "Next", how many settle-and-reobserve rounds to wait for a full
    # page load to land before concluding the click did not advance.
    max_navigation_waits: int = 8
    navigation_wait_s: float = 1.0  # pause between those rounds (~8 s total)


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
    # The model was needed but unreachable (why); fields it would have mapped
    # were left empty. Surfaced to the user, never hidden behind "COMPLETED".
    model_unavailable: str | None = None
    # Optional fields with no confident value: left blank on purpose, not asked.
    left_blank: list[str] = field(default_factory=list)


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
    for form_field in observation.fields:
        if form_field.input_type != "file" or form_field.field_id in uploaded_fields:
            continue
        if form_field.field_id in blocked_fields:
            continue
        key = form_field.target.name_attr
        if key and key in uploads and form_field.current_value != uploads[key].filename:
            return form_field, uploads[key]
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


def _apply_recovery_strategy(
    strategy: RecoveryStrategy,
    field_id: str,
    fresh_field,
    assignment,
    *,
    observation: PageObservation,
    session: TabSession,
    transport: BrowserTransport,
    result: DriveResult,
    sequence: int,
    method_hints: dict[str, ActionMethodHint],
    value_overrides: dict[str, str],
) -> tuple[int, bool]:
    """Carry out a non-terminal recovery strategy's side effect: a helper action
    (scroll / wait), or staging how the next attempt is driven. Returns the
    updated sequence number and whether the strategy calls for a re-map (a
    cascade wait expects new/changed dependent fields)."""
    if strategy is RecoveryStrategy.SCROLL:
        sequence += 1
        transport.execute(_helper_action(observation, session, ActionKind.SCROLL, sequence))
        result.steps_used += 1
        return sequence, False
    if strategy in (RecoveryStrategy.WAIT_STABLE, RecoveryStrategy.WAIT_CASCADE):
        sequence += 1
        transport.execute(
            _helper_action(observation, session, ActionKind.WAIT_FOR_STABLE_PAGE, sequence)
        )
        result.steps_used += 1
        return sequence, strategy is RecoveryStrategy.WAIT_CASCADE
    # ALT_SELECT / REAPPLY / NORMALIZE_VALUE stage how the next attempt is
    # driven; RETRY / REOBSERVE simply re-attempt.
    _prepare_reattempt(strategy, field_id, fresh_field, assignment, method_hints, value_overrides)
    return sequence, False


def _block_with_question(
    result: DriveResult,
    blocked_fields: set[str],
    field_id: str,
    *,
    question_id: str,
    kind: QuestionKind,
    prompt: str,
) -> None:
    """Give up on a field for this run and report it to the user — never loop."""
    blocked_fields.add(field_id)
    result.questions.append(
        UserQuestion(question_id=question_id, kind=kind, prompt=prompt, field_id=field_id)
    )


@dataclass
class EditResult:
    filled_fields: list[str] = field(default_factory=list)
    failed_fields: list[str] = field(default_factory=list)
    # Changed facts whose value did not map to a field/option (e.g. a typo, or a
    # value not among the choices): {key, value, field_label, options}.
    unresolved: list[dict] = field(default_factory=list)


def apply_edits(
    transport: BrowserTransport,
    facts: list[DocumentFact],
    changed_keys: set[str],
    mapper: Mapper | None = None,
    max_actions: int = 12,
    bindings: dict[str, str] | None = None,
    memory: MappingMemory | None = None,
) -> EditResult:
    """Targeted edit: change only the field(s) bound to `changed_keys` (plus any
    required dependent revealed by the change), instead of re-running the whole
    form. Avoids the churn and step-budget exhaustion of a full re-fill for a
    one-field correction. Every action still passes the policy gate and is
    independently verified; never submits.

    `bindings` are explicit field_id -> fact_key assignments the USER made
    ("use aID for Aadhaar Number"): they override the mapper for those fields
    and, once verified, are remembered so the site maps automatically next time."""
    mapper = mapper or DeterministicMapper()
    transport = resilient(transport)
    facts_by_key = {f.key: f for f in facts}
    result = EditResult()

    session = transport.attach()
    observation = transport.observe()
    seq = 0

    def remember(field_id: str, assignment) -> None:
        if memory is None or assignment.fact.key.startswith("field:"):
            return  # a one-off page-specific value is not a reusable binding
        fld = next((f for f in observation.fields if f.field_id == field_id), None)
        if fld is not None:
            memory.remember(
                site_key(session.origin), field_signature(fld), assignment.fact.key, fld.input_type
            )

    def fill(field_id, assignment) -> str:
        nonlocal observation, seq
        # Bounded retry: a single edit can fail transiently (value not yet
        # settled, a widget mid-render). Re-attempt a couple of times with a
        # fresh key before reporting failure — without the full-form loop.
        # Formats to try, best first (a 10-digit number for a mobile box, a
        # date in the field's order, an address abbreviated to fit): the next
        # one is used when the site rejects the previous.
        tried: list[str] = []
        for attempt in range(3):
            fresh = next((f for f in observation.fields if f.field_id == field_id), None)
            if fresh is None:
                return "absent"
            options = candidates(fresh, assignment.value, assignment.fact.key, observation)
            value_now = next(
                (c for c in options if c not in tried), options[0] if options else None
            )
            if assignment.checked is not None:
                value_now = assignment.value
            if _field_done(fresh, assignment, options[0] if options else assignment.value) or any(
                assignment_satisfied(fresh, t, None) and not fresh.validation_message for t in tried
            ):
                return "satisfied"
            if value_now is not None:
                tried.append(value_now)
            seq += 1
            action = build_action_for(
                fresh,
                run_id=session.run_id,
                tab_id=session.tab_id,
                origin=session.origin,
                value=value_now,
                checked=assignment.checked,
                sequence_number=seq,
                source_observation_seq=observation.observation_seq,
                value_ref=f"fact://{assignment.fact.fact_id}",
                attempt=attempt,
            )
            decision = check_action(action, observation, {field_id: assignment.value})
            if decision.decision is PolicyDecisionKind.BLOCK:
                return "blocked"
            outcome = transport.execute(action)
            observation = outcome.observation or transport.observe()
            verified = (
                outcome.verification is None
                or outcome.verification.status is VerificationStatus.SUCCESS
            )
            if outcome.result.status == "EXECUTED" and verified:
                return "ok"
            shown = next((f for f in observation.fields if f.field_id == field_id), None)
            if (
                outcome.result.status == "EXECUTED"
                and shown is not None
                and not shown.validation_message
                and site_normalized(value_now or "", shown.current_value)
            ):
                return "ok"  # the site reformatted it (mask, case); same value
            # The field may cap length (HTML maxlength or a JS limiter). If it
            # accepted a prefix of our value, that value IS applied — accept it
            # rather than failing on the length mismatch.
            after = next((f for f in observation.fields if f.field_id == field_id), None)
            cur = after.current_value if after else None
            value = value_now or ""
            if cur and value.startswith(cur) and 0 < len(cur) < len(value):
                return "ok"
        return "failed"

    # Pass 1: the explicitly-changed facts.
    mapping = mapper.map(observation, facts_by_key)
    for field_id, key in (bindings or {}).items():
        fld = next((f for f in observation.fields if f.field_id == field_id), None)
        if fld is None or key not in facts_by_key:
            continue
        bound = user_binding(fld, facts_by_key[key])
        if isinstance(bound, Assignment):
            mapping.assignments[field_id] = bound
            mapping.questions = [q for q in mapping.questions if q.field_id != field_id]
        else:
            mapping.questions.append(bound)
    addressed: set[str] = set()
    for field_id, assignment in list(mapping.assignments.items()):
        if assignment.fact.key not in changed_keys or seq >= max_actions:
            continue
        addressed.add(assignment.fact.key)
        status = fill(field_id, assignment)
        if status in ("ok", "satisfied"):
            result.filled_fields.append(field_id)
            if status == "ok":
                remember(field_id, assignment)
        elif status in ("failed", "blocked"):
            result.failed_fields.append(field_id)

    # A changed fact with no assignment means its value did not map to a field
    # or match an option — surface it honestly (with the choices, if any) rather
    # than reporting a change that never happened.
    def _label(fid: str) -> str:
        fld = next((f for f in observation.fields if f.field_id == fid), None)
        return (fld.label or fld.accessible_name or fid) if fld else fid

    for key in changed_keys - addressed:
        q = next((qq for qq in mapping.questions if key in (qq.fact_keys or [])), None)
        result.unresolved.append(
            {
                "key": key,
                "value": facts_by_key[key].value if key in facts_by_key else None,
                "field_label": _label(q.field_id) if q and q.field_id else key,
                "options": list(q.options) if q and q.options else None,
            }
        )

    # Pass 2: dependents the change revealed (e.g. state/district after country)
    # — required fields now mappable to a fact but not yet satisfied.
    mapping = mapper.map(observation, facts_by_key)
    for field_id, assignment in list(mapping.assignments.items()):
        if field_id in result.filled_fields or seq >= max_actions:
            continue
        fresh = next((f for f in observation.fields if f.field_id == field_id), None)
        if fresh is None or not fresh.required:
            continue
        if assignment_satisfied(fresh, assignment.value, assignment.checked):
            continue
        if fill(field_id, assignment) == "ok":
            result.filled_fields.append(field_id)

    return result


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
    repair: "Callable[[FormField, str, str], str | None] | None" = None,
    progress: "Callable[[dict], None] | None" = None,
) -> DriveResult:
    """`repair(field, rejected_value, site_error)` proposes a corrected value
    when the site rejects every deterministic reshape (model-backed in the app;
    its output is accepted only as a plausible reshape of the fact).

    `verify` and `recover` are ablation switches (plan.md §17), both on by
    default. verify=False runs open-loop (trust the executor, skip
    verification) — used to measure the value of the perceive-act-verify loop.
    recover=False disables bounded recovery (a failure blocks the field
    immediately) — used to measure recovery's contribution."""
    """Fill every approved-mappable field on the attached page, verifying
    each action. Never submits (invariant 1)."""
    budgets = budgets or DriverBudgets()
    transport = resilient(transport)  # a page mid-reload is waited for, not fatal
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
    # What the driver typed and the site accepted, per field; what it tried and
    # the site rejected; model repairs (each field gets at most one).
    accepted: dict[str, str] = {}
    tried: dict[str, list[str]] = {}
    repaired_values: dict[str, str] = {}
    blocked_fields: set[str] = set()
    visited_pages: set[str] = set()
    dialogs_tried: dict[str, int] = {}
    uploaded_fields: set[str] = set()
    uploads = uploads or {}
    recovery = RecoveryPlanner()
    mapping: MappingOutcome | None = None
    mapped_fingerprint: str | None = None

    def emit(phase: str, **detail) -> None:
        """Live progress for the side panel. Best-effort: never fails a fill."""
        if progress is None:
            return
        try:
            progress({"phase": phase, "page": len(visited_pages) + 1, **detail})
        except Exception:  # noqa: BLE001
            pass

    def _label(f: FormField) -> str:
        return f.label or f.accessible_name or f.target.placeholder or f.field_id

    def _remaining() -> int:
        if mapping is None:
            return 0
        return sum(
            1
            for f in observation.fields
            if f.field_id in mapping.assignments
            and f.field_id not in blocked_fields
            and not _field_done(
                f,
                mapping.assignments[f.field_id],
                _target(f.field_id, mapping.assignments[f.field_id], f),
            )
        )

    def _target(field_id: str, assignment, fresh: FormField) -> str | None:
        """The value to type: what the site already accepted, else a staged
        retry value, else the fact value adapted to this field's constraints."""
        if assignment.checked is not None or assignment.value is None:
            return assignment.value
        if field_id in accepted:
            return accepted[field_id]
        if field_id in value_overrides:
            return value_overrides[field_id]
        return adapt(fresh, assignment.value, assignment.fact.key, observation)

    emit("reading", fields=len(observation.fields))
    stability_waits = 0
    final_settles = 0
    while result.steps_used < budgets.max_steps:
        # A login / MFA gate is the human's (invariant 2). A captcha on the page
        # is too — but only that box: the rest of the form is still filled, and
        # the captcha is reported as theirs to complete. Submission is never ours.
        if observation.login_detected:
            result.outcome = RunOutcome.NEEDS_USER
            result.detail = "login present; human takeover required (invariant 2)"
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
                if d.dismiss_target is not None
                and not d.contains_form  # the form's own modal is not an obstacle
                and dialogs_tried.get(d.dialog_id, 0) < 2
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
            emit("matching", fields=len(observation.fields))
            mapping = mapper.map(observation, facts_by_key)
            mapped_fingerprint = observation.page_fingerprint
            result.model_calls.extend(mapping.model_calls)
            for question in mapping.questions:
                if all(q.question_id != question.question_id for q in result.questions):
                    result.questions.append(question)
            emit("planned", done=len(result.filled_fields), remaining=_remaining())

        # Next unsatisfied, unblocked assignment in the order the PAGE shows
        # fields (top to bottom) — not the order the mapper resolved them.
        next_assignment = None
        for fresh in observation.fields:
            field_id = fresh.field_id
            assignment = mapping.assignments.get(field_id)
            if assignment is None or field_id in blocked_fields:
                continue
            if not _field_done(fresh, assignment, _target(field_id, assignment, fresh)):
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
                    _helper_action(observation, session, ActionKind.WAIT_FOR_STABLE_PAGE, sequence)
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
            # Like a person: don't press "Next" while this page still has a
            # required field only the user can fill (a question, a captcha) —
            # the site would refuse it anyway. Stop here, ask, and the flow
            # continues from this page once the user has answered.
            waiting = _required_empty(observation)
            if waiting:
                return _finish(
                    result,
                    mapping,
                    blocked_fields,
                    transport,
                    verify,
                    stopped=f"waiting for you on this page ({', '.join(waiting[:3])})",
                )
            if len(visited_pages) > budgets.max_pages:
                result.outcome = RunOutcome.BUDGET_EXHAUSTED
                result.detail = f"page budget ({budgets.max_pages}) exhausted"
                return result

            emit("next_page", done=len(result.filled_fields))
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

            new_observation = _await_navigation(
                transport,
                session,
                observation,
                nav_outcome.observation,
                budgets.max_navigation_waits,
                sequence,
                budgets.navigation_wait_s,
            )
            sequence += budgets.max_navigation_waits
            if new_observation is None or new_observation.page_fingerprint in visited_pages:
                # Navigation did not reach a new page: stop rather than loop,
                # and say so — there is more form the user must see to.
                return _finish(
                    result,
                    mapping,
                    blocked_fields,
                    transport,
                    verify,
                    stopped="navigation did not advance to a new page",
                )
            observation = new_observation
            mapping = None
            final_settles = 0  # a new page gets its own cascade settles
            stability_waits = 0
            continue

        field_id, assignment, fresh_field = next_assignment
        emit(
            "filling",
            label=_label(fresh_field),
            done=len(result.filled_fields),
            remaining=_remaining(),
            retry=field_id in tried,
        )
        sequence += 1
        action = build_action_for(
            fresh_field,
            run_id=session.run_id,
            tab_id=session.tab_id,
            origin=session.origin,
            value=(sent := _target(field_id, assignment, fresh_field)),
            checked=assignment.checked,
            sequence_number=sequence,
            source_observation_seq=observation.observation_seq,
            value_ref=f"fact://{assignment.fact.fact_id}",
            attempt=retries.get(field_id, 0),
            method_hint=method_hints.get(field_id),
        )

        # Deterministic policy gate before dispatch (invariant: every action).
        # Approved values: the fact values (deterministic reshapes of them are
        # recognised by the gate itself) plus model repairs already checked to
        # be plausible reshapes of the same fact.
        approved = {**mapping.approved_values(), **repaired_values}
        decision = check_action(action, observation, approved)
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
                _block_with_question(
                    result,
                    blocked_fields,
                    field_id,
                    question_id=f"q-failed-{field_id}",
                    kind=QuestionKind.AMBIGUOUS_MAPPING,
                    prompt=(
                        f"Could not fill {field_id} ({outcome.result.error}); "
                        "please do it manually."
                    ),
                )
                observation = transport.observe()
                continue
            sequence, _ = _apply_recovery_strategy(
                decision.strategy,
                field_id,
                fresh_field,
                assignment,
                observation=observation,
                session=session,
                transport=transport,
                result=result,
                sequence=sequence,
                method_hints=method_hints,
                value_overrides=value_overrides,
            )
            # A failed action always re-maps (options may have loaded since) and
            # re-observes; the loop re-attempts with a fresh key and any staged hint.
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
        # The site reformatted what we typed (an input mask, a case change) and
        # is not complaining: that IS success — accept the site's form of it.
        if verification.status is not VerificationStatus.SUCCESS and _textual(
            assignment, fresh_field
        ):
            shown = next((f for f in observation.fields if f.field_id == field_id), None)
            if (
                shown is not None
                and not shown.validation_message
                and verification.failure_class is FailureClass.VALUE_MISMATCH
                and site_normalized(sent or "", shown.current_value)
            ):
                verification = verification.model_copy(
                    update={"status": VerificationStatus.SUCCESS, "failure_class": None}
                )
                sent = shown.current_value
        if verification.status is VerificationStatus.SUCCESS:
            result.filled_fields.append(field_id)
            if sent is not None and assignment.checked is None:
                accepted[field_id] = sent
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
                _block_with_question(
                    result,
                    blocked_fields,
                    field_id,
                    question_id=f"q-norecover-{field_id}",
                    kind=QuestionKind.AMBIGUOUS_MAPPING,
                    prompt=f"Could not fill {field_id} (recovery disabled).",
                )
                observation = transport.observe()
                continue
            # Like a person re-typing a rejected value: try the next format the
            # field could want, then a model repair guided by the site's own
            # error message — and only then fall through to asking the user.
            if _textual(assignment, fresh_field) and verification.failure_class in _REJECTIONS:
                tried.setdefault(field_id, []).append(sent or "")
                after = next((f for f in observation.fields if f.field_id == field_id), fresh_field)
                nxt = _next_value(
                    after,
                    assignment,
                    tried[field_id],
                    observation,
                    repair=repair if field_id not in repaired_values else None,
                    error=verification.evidence.validation_message
                    or after.validation_message
                    or str(verification.failure_class),
                )
                if nxt is not None:
                    emit(
                        "retrying",
                        label=_label(after),
                        reason=after.validation_message or str(verification.failure_class),
                        repaired=nxt.repaired,
                    )
                    if nxt.repaired:
                        repaired_values[field_id] = nxt.value
                    value_overrides[field_id] = nxt.value
                    accepted.pop(field_id, None)
                    retries[field_id] = retries.get(field_id, 0) + 1
                    continue

            # Bounded recovery: pick the next strategy from the failure ladder.
            decision = recovery.plan(field_id, verification.failure_class)
            result.recovery_decisions.append(decision)
            # A re-attempt must carry a fresh idempotency key.
            retries[field_id] = retries.get(field_id, 0) + 1

            if decision.strategy in (RecoveryStrategy.STOP, RecoveryStrategy.ASK_USER):
                _block_with_question(
                    result,
                    blocked_fields,
                    field_id,
                    question_id=f"q-recover-{field_id}",
                    kind=(
                        QuestionKind.LOW_CONFIDENCE
                        if decision.strategy is RecoveryStrategy.ASK_USER
                        else QuestionKind.AMBIGUOUS_MAPPING
                    ),
                    prompt=_rejection_prompt(
                        fresh_field, observation, verification, tried.get(field_id)
                    ),
                )
                observation = transport.observe()
                continue
            sequence, remap = _apply_recovery_strategy(
                decision.strategy,
                field_id,
                fresh_field,
                assignment,
                observation=observation,
                session=session,
                transport=transport,
                result=result,
                sequence=sequence,
                method_hints=method_hints,
                value_overrides=value_overrides,
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


_REJECTIONS = frozenset(
    {FailureClass.VALIDATION_ERROR, FailureClass.VALUE_MISMATCH, FailureClass.VALUE_NOT_APPLIED}
)
_MAX_VALUE_ATTEMPTS = 5


def _textual(assignment, field: FormField) -> bool:
    return (
        assignment.checked is None
        and assignment.value is not None
        and not field.options
        and field.input_type
        in ("text", "tel", "number", "email", "search", "url", "textarea", "date")
    )


def _field_done(field: FormField, assignment, target: str | None) -> bool:
    """Filled with what we meant to type — or already holding the raw fact
    value and the site is not complaining about it."""
    if assignment_satisfied(field, target, assignment.checked):
        return True
    if _combobox_shows(field, target):
        return True
    return (
        target != assignment.value
        and assignment_satisfied(field, assignment.value, assignment.checked)
        and not field.validation_message
    )


def _rejection_prompt(field, observation, verification, tried) -> str:
    """Tell the user what the SITE said, not our internal failure class."""
    after = next((f for f in observation.fields if f.field_id == field.field_id), field)
    label = after.label or after.accessible_name or after.field_id
    said = verification.evidence.validation_message or after.validation_message
    shown = f" I tried {', '.join(repr(t) for t in tried[-3:])}." if tried else ""
    if said:
        return f"The site rejected “{label}”: “{said}”.{shown} What should I enter?"
    return f"I couldn't fill “{label}” ({verification.failure_class}).{shown} What should I enter?"


def _required_empty(observation: PageObservation) -> list[str]:
    """Labels of required fields on this page that are still empty."""
    from .snapshot import is_filled

    return [
        f.label or f.accessible_name or f.field_id
        for f in observation.fields
        if f.required and f.visible and not f.disabled and not is_filled(f)
    ]


def _combobox_shows(field: FormField, target: str | None) -> bool:
    return widget_shows(field, target)


@dataclass
class _NextValue:
    value: str
    repaired: bool = False


def _next_value(
    field: FormField,
    assignment,
    tried: list[str],
    observation: PageObservation,
    *,
    repair,
    error: str,
) -> "_NextValue | None":
    """The next value to try after a rejection, or None to give up (ask)."""
    if len(tried) >= _MAX_VALUE_ATTEMPTS:
        return None
    for c in candidates(field, assignment.value, assignment.fact.key, observation):
        if c not in tried:
            return _NextValue(c)
    if repair is None:
        return None
    try:
        proposal = repair(field, tried[-1], error)
    except Exception:  # noqa: BLE001 — a failed repair just means "ask the user"
        return None
    if not proposal:
        return None
    proposal = proposal.strip()
    if field.max_length:
        proposal = proposal[: field.max_length]
    if proposal in tried or not plausible_reshape(assignment.value, proposal):
        return None  # new content is not a repair; the user decides
    return _NextValue(proposal, repaired=True)


def _await_navigation(
    transport: BrowserTransport,
    session: TabSession,
    before: PageObservation,
    first: PageObservation | None,
    max_waits: int,
    sequence: int,
    pause_s: float = 1.0,
) -> PageObservation | None:
    """A "Next" that triggers a full page load returns before the new page
    exists: the observation taken right after the click is still the old page
    (or fails while it unloads). Re-observe, with a settle wait between tries,
    until the page fingerprint changes. Returns the new page, or None when it
    never changed within the budget. Bounded (invariant 12)."""
    candidate = first
    for i in range(max_waits + 1):
        if candidate is not None and candidate.page_fingerprint != before.page_fingerprint:
            return candidate
        if i == max_waits:
            break
        time.sleep(pause_s)  # give a full page load time to land
        try:
            transport.execute(
                _helper_action(before, session, ActionKind.WAIT_FOR_STABLE_PAGE, sequence + i + 1)
            )
        except Exception:  # noqa: BLE001 — the old document may be gone mid-load
            pass
        try:
            candidate = transport.observe()
        except Exception:  # noqa: BLE001 — content script not yet in the new page
            candidate = None
    return None


# Most useful question first: a concrete proposal beats "which option?", which
# beats a bare "no fact for this field".
_QUESTION_RANK = {
    QuestionKind.SENSITIVE_MAPPING: 0,
    QuestionKind.LOW_CONFIDENCE: 1,
    QuestionKind.AMBIGUOUS_MAPPING: 2,
    QuestionKind.CONFLICTING_FACTS: 3,
    QuestionKind.MISSING_FACT: 4,
}


def _current_questions(questions: list[UserQuestion], filled: set[str]) -> list[UserQuestion]:
    """Questions accumulate across re-maps on a changing page. Report only what
    is still open: drop questions about fields that ended up filled, and keep one
    question per field — the most useful kind."""
    best: dict[str, UserQuestion] = {}
    order: list[str] = []
    loose: list[UserQuestion] = []
    for q in questions:
        if q.field_id is None:
            loose.append(q)
            continue
        if q.field_id in filled:
            continue
        current = best.get(q.field_id)
        if current is None:
            order.append(q.field_id)
            best[q.field_id] = q
        elif _QUESTION_RANK.get(q.kind, 9) < _QUESTION_RANK.get(current.kind, 9):
            best[q.field_id] = q
    return [best[f] for f in order] + loose


# Asking about an optional field is noise: a person filling the form would just
# leave it. Only questions about fields we TRIED and failed stay (the user may
# want to know), identified by their question ids.
_ATTEMPTED_PREFIXES = ("q-failed-", "q-recover-", "q-norecover-")


def _drop_optional_questions(
    questions: list[UserQuestion], observation: PageObservation
) -> tuple[list[UserQuestion], list[str]]:
    by_id = {f.field_id: f for f in observation.fields}
    kept: list[UserQuestion] = []
    blank: list[str] = []
    for q in questions:
        f = by_id.get(q.field_id) if q.field_id else None
        optional = f is not None and not f.required
        if optional and not q.question_id.startswith(_ATTEMPTED_PREFIXES):
            name = f.label or f.accessible_name or f.field_id
            if name not in blank:
                blank.append(name)
            continue
        kept.append(q)
    return kept, blank


def _finish(
    result: DriveResult,
    mapping: MappingOutcome | None,
    blocked_fields: set[str],
    transport: BrowserTransport,
    verify: bool = True,
    stopped: str | None = None,
) -> DriveResult:
    result.questions = _current_questions(result.questions, set(result.filled_fields))
    final = transport.observe()
    result.questions, result.left_blank = _drop_optional_questions(result.questions, final)
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
    report = validate_form(final, set(result.filled_fields)) if verify else ValidationReport()
    result.validation = report

    model_unavailable = mapping.model_unavailable if mapping is not None else None
    result.model_unavailable = model_unavailable
    has_problems = bool(
        result.questions or blocked_fields or report.issues or model_unavailable or stopped
    )
    if has_problems:
        result.outcome = RunOutcome.NEEDS_USER
        parts = []
        if stopped:
            parts.append(stopped)
        if model_unavailable:
            parts.append(
                "the mapping model was unreachable, so unmatched fields were left empty "
                f"({model_unavailable})"
            )
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
