"""Derivation engine: computes missing values with traces, skips already-known
targets, and discards model output that invents sources."""

from datetime import date

from agent_backend.document_intelligence.derivation import DerivationEngine
from agent_backend.model_gateway.base import (
    DerivationResult,
    DerivationTarget,
    ModelCallMetadata,
    ModelUnavailable,
)
from agent_backend.model_gateway.fake import FakeModelAdapter
from form_contracts import DocumentFact, FactStatus


def fact(fact_id, key, value, vtype="number"):
    return DocumentFact(
        fact_id=fact_id,
        key=key,
        value=value,
        value_type=vtype,
        confidence=1.0,
        sensitivity="personal",
        status="user_provided",
    )


FACTS = [
    fact("f-dob", "date_of_birth", "1998-04-17", "date"),
    fact("f-m", "monthly_income", "85000"),
    fact("f-b", "bonus", "120000"),
]


def test_derives_age_and_total_with_traces():
    engine = DerivationEngine(FakeModelAdapter())
    out = engine.derive(
        FACTS,
        [
            DerivationTarget("age", "number", "age"),
            DerivationTarget("total_income", "number", "annual total"),
        ],
        today=date(2026, 9, 3),
    )
    by_key = {f.key: f for f in out.facts}
    assert by_key["age"].value == "28"
    assert by_key["age"].status is FactStatus.DERIVED
    assert by_key["age"].derivation.source_fact_ids == ["f-dob"]
    assert by_key["total_income"].value == "205000"
    assert set(by_key["total_income"].derivation.source_fact_ids) == {"f-m", "f-b"}
    assert len(out.model_calls) == 1


def test_skips_targets_already_available():
    engine = DerivationEngine(FakeModelAdapter())
    out = engine.derive(
        [*FACTS, fact("f-age", "age", "27")],
        [DerivationTarget("age", "number", "age")],
        today=date(2026, 9, 3),
    )
    assert out.facts == []  # age already present -> no derivation call needed
    assert out.model_calls == []


def test_model_output_inventing_sources_is_discarded():
    class InventingGateway:
        def derive_facts(self, request):
            # Cite a source id that does not exist -> must be discarded by the
            # validated runner. We emulate that the runner already discarded it.
            return DerivationResult(
                facts=[],
                metadata=ModelCallMetadata(
                    model_id="stub",
                    latency_ms=1,
                    schema_valid=True,
                    request_fingerprint=request.fingerprint(),
                ),
            )

    out = DerivationEngine(InventingGateway()).derive(
        FACTS, [DerivationTarget("age", "number", "age")], today=date(2026, 9, 3)
    )
    assert out.facts == []


def test_model_outage_derives_nothing_not_a_crash():
    class DownGateway:
        def derive_facts(self, request):
            raise ModelUnavailable("timeout")

    out = DerivationEngine(DownGateway()).derive(
        FACTS, [DerivationTarget("age", "number", "age")], today=date(2026, 9, 3)
    )
    assert out.facts == []
    assert out.model_calls == []
