"""Shadow mode (FormBench-Live, plan.md §16 Level 3): observe a page, map
facts, and propose typed, policy-checked actions — WITHOUT executing any of
them. Safe on held-out live sites (no side effect), and the basis for the
propose-only research evaluation. The proposer never touches
transport.execute and never navigates."""

from dataclasses import dataclass, field

from form_contracts import (
    BrowserAction,
    DocumentFact,
    ModelCallMetadata,
    PolicyDecision,
    PolicyDecisionKind,
    UserQuestion,
)

from ..mapper import DeterministicMapper, Mapper
from ..planner import build_action_for
from ..policy import check_action
from ..transport import BrowserTransport


@dataclass
class ProposedAction:
    field_id: str
    fact_key: str | None
    action: BrowserAction
    policy: PolicyDecision

    @property
    def allowed(self) -> bool:
        return self.policy.decision is PolicyDecisionKind.ALLOW


@dataclass
class ShadowPlan:
    origin: str
    url: str
    fields_observed: list[str]
    proposed_actions: list[ProposedAction] = field(default_factory=list)
    questions: list[UserQuestion] = field(default_factory=list)
    model_calls: list[ModelCallMetadata] = field(default_factory=list)
    login_detected: bool = False
    captcha_detected: bool = False

    @property
    def allowed_actions(self) -> list[ProposedAction]:
        return [p for p in self.proposed_actions if p.allowed]

    @property
    def unsafe_proposals(self) -> list[ProposedAction]:
        """Proposals the policy gate would block — a first-class safety metric
        (plan.md §17). In a correct system this is empty."""
        return [p for p in self.proposed_actions if not p.allowed]


def propose_plan(
    transport: BrowserTransport,
    facts: list[DocumentFact],
    mapper: Mapper | None = None,
) -> ShadowPlan:
    """Attach, observe once, map, and propose one policy-checked action per
    mappable field. Executes nothing. Read-only and side-effect-free."""
    mapper = mapper or DeterministicMapper()
    facts_by_key = {f.key: f for f in facts}

    session = transport.attach()
    observation = transport.observe()
    plan = ShadowPlan(
        origin=observation.origin,
        url=observation.url,
        fields_observed=[f.field_id for f in observation.fields],
        login_detected=observation.login_detected,
        captcha_detected=observation.captcha_detected,
    )
    if observation.login_detected or observation.captcha_detected:
        # A login/CAPTCHA page: propose nothing, flag for takeover.
        return plan

    mapping = mapper.map(observation, facts_by_key)
    plan.questions = list(mapping.questions)
    plan.model_calls = list(mapping.model_calls)

    approved = mapping.approved_values()
    seq = 0
    for field_id, assignment in mapping.assignments.items():
        fresh = next((f for f in observation.fields if f.field_id == field_id), None)
        if fresh is None:
            continue
        seq += 1
        action = build_action_for(
            fresh,
            run_id=session.run_id,
            tab_id=session.tab_id,
            origin=session.origin,
            value=assignment.value,
            checked=assignment.checked,
            sequence_number=seq,
            source_observation_seq=observation.observation_seq,
            value_ref=f"fact://{assignment.fact.fact_id}",
        )
        decision = check_action(action, observation, approved)
        plan.proposed_actions.append(
            ProposedAction(
                field_id=field_id,
                fact_key=assignment.fact.key,
                action=action,
                policy=decision,
            )
        )
    return plan
