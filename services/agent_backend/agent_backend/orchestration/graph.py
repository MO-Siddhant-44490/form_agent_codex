"""The durable form-filling graph (Module 8).

Nodes are deterministic transitions over FormFillState; the transport and
mapper are bound at build time. Human input arrives through LangGraph
interrupts (clarification, login takeover, submission approval). Side effects
(execute) are guarded by the extension's idempotency records, so a restart of
a node after a checkpoint never repeats a browser mutation (invariant 6, and
plan.md §6.1 on interrupt-restartable nodes)."""

from typing import Any, Literal

from form_contracts import (
    ActionKind,
    ApprovalToken,
    BrowserAction,
    ExpectedEffect,
    PolicyDecisionKind,
    RiskLevel,
    RunOutcome,
    VerificationStatus,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..mapper import Assignment, DeterministicMapper, Mapper, MappingOutcome
from ..planner import assignment_satisfied, build_action_for
from ..policy import check_action
from ..transport import BrowserTransport
from .state import FormFillState, PlannedAssignment

MAX_STEPS = 60
MAX_RETRIES = 2


def _assignment_from_state(pa: PlannedAssignment, observation) -> Assignment | None:
    field = next((f for f in observation.fields if f.field_id == pa.field_id), None)
    if field is None:
        return None
    return Assignment(field=field, fact=pa.fact, value=pa.value, checked=pa.checked)


def build_form_fill_graph(
    transport: BrowserTransport,
    mapper: Mapper | None = None,
    checkpointer: Any | None = None,
) -> Any:
    mapper = mapper or DeterministicMapper()

    # -- nodes -------------------------------------------------------------

    def attach(state: FormFillState) -> dict:
        session = transport.attach()
        return {
            "tab_id": session.tab_id,
            "origin": session.origin,
            "sequence": 0,
            "steps_used": 0,
            "retries": {},
            "blocked_fields": [],
            "filled_fields": [],
            "questions": [],
            "policy_decisions": [],
            "verifications": [],
            "model_calls": [],
            "assignments": {},
            "submitted": False,
        }

    def perceive(state: FormFillState) -> dict:
        return {"observation": transport.observe()}

    def classify(state: FormFillState) -> dict:
        obs = state["observation"]
        if obs is not None and (obs.login_detected or obs.captcha_detected):
            # Human takeover interrupt: the user completes login/CAPTCHA, we
            # never touch credentials (invariant 2). Resume re-perceives.
            interrupt(
                {
                    "reason": "captcha" if obs.captcha_detected else "login",
                    "message": "Login or CAPTCHA detected — please complete it, then resume.",
                }
            )
        return {}

    def map_fields(state: FormFillState) -> dict:
        obs = state["observation"]
        facts_by_key = {f.key: f for f in state["facts"]}
        if state.get("mapped_fingerprint") == obs.page_fingerprint and state.get("assignments"):
            return {}
        outcome: MappingOutcome = mapper.map(obs, facts_by_key)
        assignments = {
            fid: PlannedAssignment(field_id=fid, fact=a.fact, value=a.value, checked=a.checked)
            for fid, a in outcome.assignments.items()
        }
        merged_questions = list(state.get("questions", []))
        seen = {q.question_id for q in merged_questions}
        for q in outcome.questions:
            if q.question_id not in seen:
                merged_questions.append(q)
        return {
            "assignments": assignments,
            "mapped_fingerprint": obs.page_fingerprint,
            "questions": merged_questions,
            "model_calls": [*state.get("model_calls", []), *outcome.model_calls],
        }

    def select_next(state: FormFillState) -> dict:
        obs = state["observation"]
        blocked = set(state.get("blocked_fields", []))
        for field_id, pa in state.get("assignments", {}).items():
            if field_id in blocked:
                continue
            fresh = next((f for f in obs.fields if f.field_id == field_id), None)
            if fresh is None:
                continue
            if not assignment_satisfied(fresh, pa.value, pa.checked):
                seq = state["sequence"] + 1
                action = build_action_for(
                    fresh,
                    run_id=state["run_id"],
                    tab_id=state["tab_id"],
                    origin=state["origin"],
                    value=pa.value,
                    checked=pa.checked,
                    sequence_number=seq,
                    source_observation_seq=obs.observation_seq,
                    value_ref=f"fact://{pa.fact.fact_id}",
                    attempt=state.get("retries", {}).get(field_id, 0),
                )
                return {
                    "pending_field_id": field_id,
                    "pending_action_json": action.model_dump(mode="json"),
                    "sequence": seq,
                }
        return {"pending_field_id": None, "pending_action_json": None}

    def policy_gate(state: FormFillState) -> dict:
        action = BrowserAction.model_validate(state["pending_action_json"])
        approved = {fid: pa.value for fid, pa in state.get("assignments", {}).items()}
        decision = check_action(action, state["observation"], approved)
        decisions = [*state.get("policy_decisions", []), decision]
        if decision.decision is PolicyDecisionKind.BLOCK:
            return {
                "policy_decisions": decisions,
                "blocked_fields": [*state.get("blocked_fields", []), state["pending_field_id"]],
                "pending_field_id": None,
                "pending_action_json": None,
            }
        return {"policy_decisions": decisions}

    def act_and_verify(state: FormFillState) -> dict:
        action = BrowserAction.model_validate(state["pending_action_json"])
        field_id = state["pending_field_id"]
        outcome = transport.execute(action)
        updates: dict = {"steps_used": state["steps_used"] + 1}
        result = outcome.result

        if result.status == "REJECTED":
            if result.rejection_reason in ("stale_observation", "stale_sequence"):
                return updates  # re-perceive
            updates.update(
                outcome=RunOutcome.BLOCKED.value, detail=f"rejected: {result.rejection_reason}"
            )
            return updates
        if result.status == "DUPLICATE":
            return updates  # already applied; re-perceive
        if result.status == "FAILED":
            updates.update(
                outcome=RunOutcome.FATAL_FAILURE.value, detail=f"execution failed: {result.error}"
            )
            return updates

        if outcome.observation is not None:
            updates["observation"] = outcome.observation
        verification = outcome.verification
        if verification is not None:
            updates["verifications"] = [*state.get("verifications", []), verification]
            if verification.status is VerificationStatus.SUCCESS:
                updates["filled_fields"] = [*state.get("filled_fields", []), field_id]
                retries = dict(state.get("retries", {}))
                retries.pop(field_id, None)
                updates["retries"] = retries
            elif verification.status in (
                VerificationStatus.RETRYABLE_FAILURE,
                VerificationStatus.NEEDS_REPERCEPTION,
            ):
                retries = dict(state.get("retries", {}))
                retries[field_id] = retries.get(field_id, 0) + 1
                updates["retries"] = retries
                if retries[field_id] > MAX_RETRIES:
                    updates.update(
                        outcome=RunOutcome.BUDGET_EXHAUSTED.value,
                        detail=f"retry budget exhausted on {field_id}",
                    )
            elif verification.status is VerificationStatus.NEEDS_USER:
                updates.update(
                    outcome=RunOutcome.NEEDS_USER.value, detail="verification requests takeover"
                )
            elif verification.status is VerificationStatus.BLOCKED:
                updates.update(
                    outcome=RunOutcome.BLOCKED.value,
                    detail=f"blocked: {verification.failure_class}",
                )
        return updates

    def final_review(state: FormFillState) -> dict:
        questions = state.get("questions", [])
        blocked = state.get("blocked_fields", [])
        unmapped = [q.field_id for q in questions if q.kind == "missing_fact"]
        if questions or blocked:
            return {
                "outcome": RunOutcome.NEEDS_USER.value,
                "detail": (
                    f"{len(questions)} question(s), {len(blocked)} blocked field(s); "
                    "everything else filled and verified"
                ),
                "unmapped_required": unmapped,
            }
        if state.get("goal") != "fill_and_submit":
            return {
                "outcome": RunOutcome.COMPLETED.value,
                "detail": "all fields filled and verified; submission not requested",
            }
        return {}  # proceed to approval

    def await_approval(state: FormFillState) -> dict:
        # Submission approval interrupt: the ONLY way a token enters state
        # (invariant 1). The human returns an ApprovalToken dict.
        payload = interrupt(
            {
                "reason": "submission_approval",
                "message": "Review the filled form and approve submission.",
                "filled_fields": state.get("filled_fields", []),
                "origin": state["origin"],
            }
        )
        if not payload or not payload.get("approved"):
            return {"outcome": RunOutcome.CANCELLED.value, "detail": "submission declined"}
        token = ApprovalToken.model_validate(payload["token"])
        return {"approval_token": token}

    def submit(state: FormFillState) -> dict:
        token = state["approval_token"]
        obs = state["observation"]
        seq = state["sequence"] + 1
        action = BrowserAction(
            action_id=f"{state['run_id']}:submit-{seq}",
            run_id=state["run_id"],
            tab_id=state["tab_id"],
            origin=state["origin"],
            sequence_number=seq,
            kind=ActionKind.SUBMIT,
            target=state.get("submit_target"),
            expected_effect=ExpectedEffect(navigation_expected=True),
            risk=RiskLevel.HIGH,
            idempotency_key=f"{state['run_id']}:submit",
            source_observation_seq=obs.observation_seq,
            approval_token_id=token.token_id,
        )
        decision = check_action(action, obs, {})
        if decision.decision is PolicyDecisionKind.BLOCK:
            return {
                "outcome": RunOutcome.BLOCKED.value,
                "detail": f"submit blocked: {decision.rule}",
                "sequence": seq,
            }
        outcome = transport.execute(action)
        if outcome.result.status != "EXECUTED":
            return {
                "outcome": RunOutcome.BLOCKED.value,
                "detail": f"submit {outcome.result.status}",
                "sequence": seq,
            }
        verifications = list(state.get("verifications", []))
        if outcome.verification is not None:
            verifications.append(outcome.verification)
        return {
            "submitted": True,
            "sequence": seq,
            "verifications": verifications,
            "outcome": RunOutcome.COMPLETED.value,
            "detail": "submission approved, executed, and verified",
        }

    # -- routing -----------------------------------------------------------

    def after_select(state: FormFillState) -> Literal["policy_gate", "final_review"]:
        return "policy_gate" if state.get("pending_field_id") else "final_review"

    def after_policy(state: FormFillState) -> Literal["act_and_verify", "select_next"]:
        # Blocked -> pick another field; allowed -> execute.
        return "select_next" if state.get("pending_field_id") is None else "act_and_verify"

    def after_act(state: FormFillState) -> Literal["perceive", "stop"]:
        if state["steps_used"] >= MAX_STEPS and not state.get("outcome"):
            return "stop"
        return "stop" if state.get("outcome") else "perceive"

    def after_review(state: FormFillState) -> Literal["await_approval", "stop"]:
        return "stop" if state.get("outcome") else "await_approval"

    def after_approval(state: FormFillState) -> Literal["submit", "stop"]:
        return "stop" if state.get("outcome") else "submit"

    graph = StateGraph(FormFillState)
    graph.add_node("attach", attach)
    graph.add_node("perceive", perceive)
    graph.add_node("classify", classify)
    graph.add_node("map_fields", map_fields)
    graph.add_node("select_next", select_next)
    graph.add_node("policy_gate", policy_gate)
    graph.add_node("act_and_verify", act_and_verify)
    graph.add_node("final_review", final_review)
    graph.add_node("await_approval", await_approval)
    graph.add_node("submit", submit)

    graph.add_edge(START, "attach")
    graph.add_edge("attach", "perceive")
    graph.add_edge("perceive", "classify")
    graph.add_edge("classify", "map_fields")
    graph.add_edge("map_fields", "select_next")
    graph.add_conditional_edges(
        "select_next", after_select, {"policy_gate": "policy_gate", "final_review": "final_review"}
    )
    graph.add_conditional_edges(
        "policy_gate",
        after_policy,
        {"act_and_verify": "act_and_verify", "select_next": "select_next"},
    )
    graph.add_conditional_edges("act_and_verify", after_act, {"perceive": "perceive", "stop": END})
    graph.add_conditional_edges(
        "final_review", after_review, {"await_approval": "await_approval", "stop": END}
    )
    graph.add_conditional_edges("await_approval", after_approval, {"submit": "submit", "stop": END})
    graph.add_edge("submit", END)

    # Our checkpointed state carries Pydantic contract types; allow the
    # serializer to (de)serialize them explicitly rather than via the
    # deprecation-warned unregistered-type fallback.
    default_checkpointer = MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=True))
    return graph.compile(checkpointer=checkpointer or default_checkpointer)
