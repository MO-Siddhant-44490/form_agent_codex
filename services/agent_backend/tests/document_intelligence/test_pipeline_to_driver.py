"""Slice 2 acceptance: document -> facts with provenance -> review/correction
-> the Slice 1 browser loop fills the form from those facts."""

from agent_backend.document_intelligence.fact_store import FactStore
from agent_backend.document_intelligence.pdf_adapter import PyMuPdfParserAdapter
from agent_backend.document_intelligence.pipeline import DocumentPipeline
from agent_backend.document_intelligence.store import DocumentStore
from agent_backend.driver import run_fill
from agent_backend.transports.fake import FakeTransport
from form_contracts import RunOutcome

from .pdfs import APPLICATION_LINES, build_pdf

PASSPORT_LINES = [
    "Passport Extract",
    "Name: Ada Lovelace",
    "DOB: 18/04/1998",  # disagrees with the application form
]


def build_pipeline() -> DocumentPipeline:
    return DocumentPipeline(store=DocumentStore(), parser=PyMuPdfParserAdapter(), facts=FactStore())


def test_document_to_form_fill_with_conflict_resolution():
    pipeline = build_pipeline()

    _, report1, _ = pipeline.ingest(
        build_pdf(APPLICATION_LINES), "application.pdf", "application/pdf"
    )
    _, report2, _ = pipeline.ingest(build_pdf(PASSPORT_LINES), "passport.pdf", "application/pdf")
    assert len(report1.facts) == 6
    assert len(report2.facts) == 2  # name + dob

    # The DOB disagreement must surface for review, not be guessed away.
    review = pipeline.facts.needing_review()
    assert "date_of_birth" in review

    # User review: resolve the conflict, adapt country to the site's code,
    # and answer the two facts no document contains.
    pipeline.facts.correct("date_of_birth", "1998-04-17", note="passport misread; form is right")
    pipeline.facts.correct("country", "IN", note="site wants ISO code")
    pipeline.facts.correct("contact_method", "email")
    pipeline.facts.correct("subscribe", "true")

    active = pipeline.facts.active()
    assert set(active) == {
        "full_name",
        "email",
        "phone",
        "date_of_birth",
        "country",
        "years_experience",
        "contact_method",
        "subscribe",
    }

    # Reviewed facts drive the Slice 1 loop to completion.
    transport = FakeTransport()
    result = run_fill(transport, list(active.values()))
    assert result.outcome is RunOutcome.COMPLETED, result.detail
    assert len(result.filled_fields) == 8
    values = {f.name: f.value for f in transport.fields if f.input_type not in ("checkbox",)}
    assert values["date_of_birth"] == "1998-04-17"  # the corrected value won
    assert values["country"] == "IN"
    assert values["phone"] == "+15550102030"


def test_unresolved_conflict_keeps_field_out_of_the_loop():
    pipeline = build_pipeline()
    pipeline.ingest(build_pdf(APPLICATION_LINES), "application.pdf", "application/pdf")
    pipeline.ingest(build_pdf(PASSPORT_LINES), "passport.pdf", "application/pdf")
    pipeline.facts.correct("contact_method", "email")

    active = pipeline.facts.active()
    assert "date_of_birth" not in active  # conflict unresolved -> withheld

    transport = FakeTransport()
    result = run_fill(transport, list(active.values()))
    # dob is a required field with no usable fact: the driver fills the rest
    # and asks, it does not guess.
    assert result.outcome is RunOutcome.NEEDS_USER
    assert "dob" in result.unmapped_required
    dob_field = next(f for f in transport.fields if f.field_id == "dob")
    assert dob_field.value is None
