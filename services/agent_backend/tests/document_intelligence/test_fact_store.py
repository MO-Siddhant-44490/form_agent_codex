"""Fact store: conflicts block use, corrections supersede with audit history,
low confidence is withheld (plan.md §10.4)."""

from agent_backend.document_intelligence.fact_store import FactEventKind, FactStore
from form_contracts import DocumentFact, FactSource, FactStatus


def extracted(key: str, value: str, doc: str = "doc-1", confidence: float = 0.95) -> DocumentFact:
    return DocumentFact(
        fact_id=f"{doc}-{key}-{value}",
        key=key,
        value=value,
        value_type="string",
        confidence=confidence,
        sensitivity="personal",
        status=FactStatus.EXTRACTED,
        source=FactSource(document_id=doc, page=1, parser="pymupdf"),
    )


def test_single_extraction_becomes_active():
    store = FactStore()
    store.add(extracted("email", "ada@example.test"))
    assert store.active()["email"].value == "ada@example.test"
    assert store.needing_review() == {}


def test_agreeing_documents_do_not_conflict():
    store = FactStore()
    store.add(extracted("email", "ada@example.test", doc="doc-1"))
    store.add(extracted("email", "ada@example.test", doc="doc-2"))
    assert store.active()["email"].value == "ada@example.test"


def test_conflicting_values_block_the_key_until_resolved():
    store = FactStore()
    store.add(extracted("date_of_birth", "1998-04-17", doc="doc-1"))
    store.add(extracted("date_of_birth", "1998-04-18", doc="doc-2"))

    assert "date_of_birth" not in store.active()  # never silently pick one
    review = store.needing_review()
    assert set(f.value for f in review["date_of_birth"]) == {"1998-04-17", "1998-04-18"}
    assert any(e.kind is FactEventKind.CONFLICT_DETECTED for e in store.events())


def test_correction_resolves_conflict_and_wins():
    store = FactStore()
    store.add(extracted("date_of_birth", "1998-04-17", doc="doc-1"))
    store.add(extracted("date_of_birth", "1998-04-18", doc="doc-2"))
    corrected = store.correct("date_of_birth", "1998-04-17", note="user checked passport")

    assert store.active()["date_of_birth"] == corrected
    assert store.needing_review() == {}
    # Full audit history retained: both extractions + the correction.
    history = store.history("date_of_birth")
    assert [f.status for f in history] == [
        FactStatus.CONFLICTED,
        FactStatus.CONFLICTED,
        FactStatus.CORRECTED,
    ]
    assert any(e.kind is FactEventKind.CORRECTED for e in store.events())


def test_low_confidence_fact_withheld_pending_review():
    store = FactStore()
    store.add(extracted("phone", "5550102030", confidence=0.4))
    assert "phone" not in store.active()
    assert "phone" in store.needing_review()


def test_correction_of_unseen_key_creates_user_fact():
    store = FactStore()
    fact = store.correct("contact_method", "email")
    assert store.active()["contact_method"] == fact
    assert fact.status is FactStatus.CORRECTED


def test_derived_fact_requires_derivation_trace():
    import pytest
    from form_contracts import Derivation, DocumentFact, FactStatus
    from pydantic import ValidationError

    # A derived fact without a trace is rejected (invariant 11).
    with pytest.raises(ValidationError, match="derivation trace"):
        DocumentFact(
            fact_id="d-1",
            key="age",
            value="28",
            value_type="number",
            confidence=0.95,
            sensitivity="personal",
            status=FactStatus.DERIVED,
        )

    # With a trace it is valid and needs no document source.
    fact = DocumentFact(
        fact_id="d-1",
        key="age",
        value="28",
        value_type="number",
        confidence=0.95,
        sensitivity="personal",
        status=FactStatus.DERIVED,
        derivation=Derivation(
            operation="age_from_date_of_birth",
            source_fact_ids=["f-dob"],
            explanation="today minus DOB",
        ),
    )
    assert fact.source is None
    assert fact.derivation.source_fact_ids == ["f-dob"]


def test_derived_fact_surfaces_as_active():
    from agent_backend.document_intelligence.fact_store import FactStore
    from form_contracts import Derivation, DocumentFact, FactStatus

    store = FactStore()
    store.add(
        DocumentFact(
            fact_id="d-age",
            key="age",
            value="28",
            value_type="number",
            confidence=0.95,
            sensitivity="personal",
            status=FactStatus.DERIVED,
            derivation=Derivation(
                operation="age_from_date_of_birth",
                source_fact_ids=["f-dob"],
                explanation="today - DOB",
            ),
        )
    )
    # A derived fact is authoritative like an extracted one (regression: it was
    # computed but dropped from active()).
    assert store.active()["age"].value == "28"
    assert store.active()["age"].status is FactStatus.DERIVED
