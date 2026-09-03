"""Slice 3 acceptance at the driver level: model-assisted mapping feeds the
loop through the policy gate; hostile assignments are blocked at dispatch."""

from agent_backend.driver import run_fill
from agent_backend.facts import slice1_facts
from agent_backend.mapper import Assignment, MappingOutcome, ModelAssistedMapper
from agent_backend.model_gateway.fake import FakeModelAdapter
from agent_backend.transports.fake import FakeField, FakeTransport, basic_form_fields
from form_contracts import PolicyDecisionKind, PolicyRule, RunOutcome


def tricky_fields():
    fields = basic_form_fields()
    for i, f in enumerate(fields):
        f.name = f"fld_{i}"
    return fields


def facts_with_country_name():
    facts = []
    for fact in slice1_facts():
        if fact.key == "country":
            fact = fact.model_copy(update={"value": "India"})
        facts.append(fact)
    return facts


def test_model_assisted_fill_completes_on_obscure_field_names():
    transport = FakeTransport(fields=tricky_fields())
    result = run_fill(
        transport,
        facts_with_country_name(),
        mapper=ModelAssistedMapper(FakeModelAdapter()),
    )
    assert result.outcome is RunOutcome.COMPLETED, result.detail
    assert len(result.filled_fields) == 8
    assert len(result.model_calls) == 1  # one mapping call, cached by fingerprint
    country = next(f for f in transport.fields if f.input_type == "select-one")
    assert country.value == "IN"  # model-bridged conversion, policy-validated
    allowed = [d for d in result.policy_decisions if d.decision is PolicyDecisionKind.ALLOW]
    assert len(allowed) == 8  # every executed action passed the gate


def test_deterministic_path_makes_no_model_calls():
    transport = FakeTransport()
    result = run_fill(
        transport,
        slice1_facts(),
        mapper=ModelAssistedMapper(FakeModelAdapter()),
    )
    assert result.outcome is RunOutcome.COMPLETED
    assert result.model_calls == []  # names matched; the model was never needed


def test_policy_blocks_hostile_assignment_at_dispatch():
    """Even if the mapping layer were compromised, the dispatch-time gate
    still refuses credential fields; the run reports instead of guessing."""
    password_field = FakeField("password", "password", "password", "Password", required=True)
    transport = FakeTransport(fields=[*basic_form_fields(), password_field])

    class CompromisedMapper:
        def map(self, observation, facts_by_key):
            outcome = MappingOutcome()
            pw = next(f for f in observation.fields if f.field_id == "password")
            fact = facts_by_key["full_name"]
            outcome.assignments["password"] = Assignment(
                field=pw,
                fact=fact,
                value=fact.value,
                checked=None,
            )
            return outcome

    result = run_fill(transport, slice1_facts(), mapper=CompromisedMapper())
    assert result.outcome is RunOutcome.NEEDS_USER
    blocks = [d for d in result.policy_decisions if d.decision is PolicyDecisionKind.BLOCK]
    assert len(blocks) == 1
    assert blocks[0].rule is PolicyRule.CREDENTIAL_FIELD
    assert next(f for f in transport.fields if f.field_id == "password").value is None


def test_clarification_questions_surface_in_the_result():
    facts = [f for f in slice1_facts() if f.key != "contact_method"]
    transport = FakeTransport()
    result = run_fill(transport, facts)
    assert result.outcome is RunOutcome.NEEDS_USER
    assert len(result.questions) == 1
    assert result.questions[0].field_id == "radio-group:contact_method"
    assert result.unmapped_required == ["radio-group:contact_method"]


def test_driver_fills_derived_field_end_to_end():
    """Full loop: a form with an Age field and only date_of_birth facts — the
    driver derives, policy-approves (the derived fact has provenance), fills,
    and verifies, ending COMPLETED."""
    from agent_backend.document_intelligence.derivation import DerivationEngine
    from agent_backend.driver import run_fill
    from agent_backend.model_gateway.fake import FakeModelAdapter
    from agent_backend.transports.fake import FakeField, FakeTransport
    from form_contracts import RunOutcome

    fields = [
        FakeField("full-name", "text", "full_name", "Full name", required=True),
        FakeField("age", "number", "age", "Age", required=True),
    ]
    transport = FakeTransport(fields=fields)
    facts = [f for f in slice1_facts() if f.key in {"full_name", "date_of_birth"}]

    mapper = ModelAssistedMapper(
        FakeModelAdapter(), derivation_engine=DerivationEngine(FakeModelAdapter())
    )
    result = run_fill(transport, facts, mapper=mapper)

    assert result.outcome is RunOutcome.COMPLETED, result.detail
    age_field = next(f for f in transport.fields if f.field_id == "age")
    assert age_field.value and int(age_field.value) > 0  # filled with a computed age
    assert "age" in result.filled_fields
