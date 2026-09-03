"""Docling challenger benchmark seed (plan.md §10.2): the same golden
document through Docling, compared field-for-field against the pymupdf
baseline. Heavy (torch + layout models), so gated: install with
`uv sync --extra docling`, run with RUN_DOCLING=1."""

import importlib.util
import os

import pytest
from agent_backend.document_intelligence.docling_adapter import DoclingParserAdapter
from agent_backend.document_intelligence.extract import extract_facts
from agent_backend.document_intelligence.pdf_adapter import PyMuPdfParserAdapter

from .pdfs import APPLICATION_LINES, build_pdf

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DOCLING") != "1" or importlib.util.find_spec("docling") is None,
    reason="set RUN_DOCLING=1 and install with `uv sync --extra docling`",
)


def test_docling_matches_pymupdf_on_golden_application():
    data = build_pdf(APPLICATION_LINES)
    baseline = extract_facts(PyMuPdfParserAdapter().parse("doc-base", data, "application/pdf"))
    challenger = extract_facts(DoclingParserAdapter().parse("doc-base", data, "application/pdf"))

    baseline_values = {f.key: f.value for f in baseline.facts}
    challenger_values = {f.key: f.value for f in challenger.facts}
    assert challenger_values == baseline_values
    for fact in challenger.facts:
        assert fact.source is not None and fact.source.parser == "docling"
