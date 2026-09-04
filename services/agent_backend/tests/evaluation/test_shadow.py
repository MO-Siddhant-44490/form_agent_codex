"""Shadow mode: proposes a full plan without executing anything (plan.md §16
Level 3). Safety: transport.execute is never called."""

from agent_backend.evaluation.shadow import propose_plan
from agent_backend.facts import slice1_facts
from agent_backend.transports.fake import FakeField, FakeTransport
from form_contracts import PolicyDecisionKind


class ExecuteForbiddenTransport(FakeTransport):
    """Fails loudly if the shadow proposer ever tries to execute."""

    def execute(self, action):  # noqa: ARG002
        raise AssertionError("shadow mode must never execute (invariant: no side effect)")


def test_proposes_all_fields_without_executing():
    transport = ExecuteForbiddenTransport()
    plan = propose_plan(transport, slice1_facts())

    # A proposal per mappable field, all policy-allowed, nothing executed.
    assert len(plan.proposed_actions) == 8
    assert all(p.allowed for p in plan.proposed_actions)
    assert plan.unsafe_proposals == []
    assert transport.received_kinds == []  # execute never called


def test_login_page_proposes_nothing():
    transport = ExecuteForbiddenTransport(login_page=True)
    plan = propose_plan(transport, slice1_facts())
    assert plan.login_detected is True
    assert plan.proposed_actions == []


def test_unsafe_field_surfaces_as_an_unsafe_proposal_not_execution():
    # A compromised mapper proposing a credential fill is caught by policy in
    # shadow mode as an unsafe proposal — and still never executed.
    from agent_backend.mapper import Assignment, MappingOutcome

    password = FakeField("password", "password", "password", "Password", required=True)
    transport = ExecuteForbiddenTransport(fields=[*FakeTransport().fields, password])

    class CompromisedMapper:
        def map(self, observation, facts_by_key):
            outcome = MappingOutcome()
            pw = next(f for f in observation.fields if f.field_id == "password")
            fact = facts_by_key["full_name"]
            outcome.assignments["password"] = Assignment(
                field=pw, fact=fact, value=fact.value, checked=None
            )
            return outcome

    plan = propose_plan(transport, slice1_facts(), mapper=CompromisedMapper())
    assert len(plan.unsafe_proposals) == 1
    assert plan.unsafe_proposals[0].policy.decision is PolicyDecisionKind.BLOCK
    assert transport.received_kinds == []
