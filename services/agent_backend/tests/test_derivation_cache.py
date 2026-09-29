"""The derivation engine asks the model once per (target, facts, day)."""

from datetime import date

from agent_backend.document_intelligence.derivation import DerivationEngine
from agent_backend.model_gateway.base import DerivationTarget
from form_contracts import DocumentFact, FactStatus, FactValueType, ModelCallMetadata, Sensitivity


def _fact(key, value):
    return DocumentFact(
        fact_id=f"f-{key}",
        key=key,
        value=value,
        value_type=FactValueType.STRING,
        confidence=1.0,
        sensitivity=Sensitivity.PERSONAL,
        status=FactStatus.USER_PROVIDED,
    )


class Counting:
    def __init__(self):
        self.requests = []

    def derive_facts(self, request):
        from types import SimpleNamespace

        self.requests.append([t.key for t in request.targets])
        facts = [_fact("age", "38")] if any(t.key == "age" for t in request.targets) else []
        return SimpleNamespace(
            facts=facts,
            metadata=ModelCallMetadata(
                model_id="stub", latency_ms=1, schema_valid=True, request_fingerprint="x"
            ),
        )


def test_derivations_are_cached_including_not_derivable():
    gw = Counting()
    engine = DerivationEngine(gw)
    facts = [_fact("date_of_birth", "1988-09-23")]
    targets = [
        DerivationTarget(key="age", value_type="number", description="Age"),
        DerivationTarget(key="id_number", value_type="string", description="ID Number"),
    ]
    day = date(2026, 9, 29)
    first = engine.derive(facts, targets, today=day)
    again = engine.derive(facts, targets, today=day)
    assert [f.value for f in first.facts] == [f.value for f in again.facts] == ["38"]
    assert len(gw.requests) == 1  # the second call hit the cache
    assert again.model_calls == []
    # Different facts -> asked again.
    engine.derive([*facts, _fact("city", "Chennai")], targets, today=day)
    assert len(gw.requests) == 2
