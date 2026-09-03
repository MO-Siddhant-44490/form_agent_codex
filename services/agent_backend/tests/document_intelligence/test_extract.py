"""Golden extraction: parse the generated application PDF and check facts,
provenance, abstention, and quality skipping."""

import pytest
from agent_backend.document_intelligence.extract import extract_facts
from agent_backend.document_intelligence.parser import ParserFailure
from agent_backend.document_intelligence.pdf_adapter import PyMuPdfParserAdapter
from form_contracts import FactStatus

from .pdfs import APPLICATION_LINES, build_encrypted_pdf, build_multipage_pdf, build_pdf

PARSER = PyMuPdfParserAdapter()


def parse(lines_or_pages):
    if lines_or_pages and isinstance(lines_or_pages[0], list):
        data = build_multipage_pdf(lines_or_pages)
    else:
        data = build_pdf(lines_or_pages)
    return PARSER.parse("doc-test", data, "application/pdf")


def test_golden_application_document():
    report = extract_facts(parse(APPLICATION_LINES))
    by_key = {f.key: f for f in report.facts}

    assert set(by_key) == {
        "full_name",
        "email",
        "phone",
        "date_of_birth",
        "country",
        "years_experience",
    }
    assert by_key["full_name"].value == "Ada Lovelace"
    assert by_key["email"].value == "ada@example.test"  # normalized case
    assert by_key["phone"].value == "+15550102030"
    assert by_key["date_of_birth"].value == "1998-04-17"  # from "17 Apr 1998"
    assert by_key["country"].value == "India"
    assert by_key["years_experience"].value == "5"
    assert report.unparsed_values == ()


def test_every_fact_carries_provenance():
    report = extract_facts(parse(APPLICATION_LINES))
    for fact in report.facts:
        assert fact.status is FactStatus.EXTRACTED
        assert fact.source is not None
        assert fact.source.parser == "pymupdf"
        assert fact.source.page == 1
        assert fact.source.bounding_box is not None
        x0, y0, x1, y1 = fact.source.bounding_box
        assert x1 > x0 and y1 > y0
        raw = (fact.source.raw_text or "").lower().replace(" ", "_")
        assert fact.key.split("_")[0] in raw
        assert fact.confidence >= 0.9


def test_unnormalizable_value_abstains_instead_of_guessing():
    report = extract_facts(parse(["Date of Birth: 31/02/1998", "Email: ada@example.test"]))
    keys = {f.key for f in report.facts}
    assert "date_of_birth" not in keys  # abstained
    assert "email" in keys
    assert any("date_of_birth" in u for u in report.unparsed_values)


def test_low_quality_page_skipped_and_reported():
    report = extract_facts(parse([["Hi"], APPLICATION_LINES]))
    assert len(report.skipped_pages) == 1
    assert report.skipped_pages[0].page == 1
    assert not report.skipped_pages[0].acceptable
    # Page 2 still extracted fully.
    assert {f.key for f in report.facts} >= {"full_name", "email"}
    assert all(f.source.page == 2 for f in report.facts)


def test_encrypted_pdf_fails_classified():
    with pytest.raises(ParserFailure) as exc:
        PARSER.parse("doc-enc", build_encrypted_pdf(), "application/pdf")
    assert exc.value.reason == "encrypted_document"


def test_corrupt_pdf_fails_classified():
    with pytest.raises(ParserFailure) as exc:
        PARSER.parse("doc-bad", b"%PDF-1.7 garbage" + b"\x00" * 32, "application/pdf")
    assert exc.value.reason == "corrupt_document"
