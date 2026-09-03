"""Live Textract + Bedrock derivation through the full pipeline. Gated on
RUN_AWS_INTEGRATION=1 with valid SSO creds (aws sso login --profile dev);
real per-page/token cost, so it stays skipped by default."""

import os

import pymupdf
import pytest
from agent_backend.document_intelligence.derivation import DerivationEngine
from agent_backend.document_intelligence.fact_store import FactStore
from agent_backend.document_intelligence.pipeline import DocumentPipeline
from agent_backend.document_intelligence.store import DocumentStore
from agent_backend.document_intelligence.textract_adapter import TextractParserAdapter
from agent_backend.model_gateway.base import DerivationTarget
from agent_backend.model_gateway.bedrock import BedrockConfig, BedrockModelAdapter
from form_contracts import FactStatus

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AWS_INTEGRATION") != "1",
    reason="set RUN_AWS_INTEGRATION=1 with valid AWS SSO creds",
)

REGION = "ap-south-1"
PROFILE = os.environ.get("AWS_PROFILE", "dev")
MODEL = "apac.anthropic.claude-sonnet-4-20250514-v1:0"


def salary_png() -> bytes:
    lines = [
        "Salary Certificate",
        "Employee Name: Ada Lovelace",
        "Date of Birth: 17 April 1998",
        "Basic Monthly Salary: 85,000",
        "Annual Bonus: 120,000",
    ]
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for ln in lines:
        page.insert_text((72, y), ln, fontsize=13)
        y += 32
    png = page.get_pixmap(dpi=150).tobytes("png")
    doc.close()
    return png


def test_live_textract_then_bedrock_derivation():
    pipeline = DocumentPipeline(
        store=DocumentStore(),
        parser=TextractParserAdapter(region=REGION, profile=PROFILE),
        facts=FactStore(),
        derivation=DerivationEngine(
            BedrockModelAdapter(BedrockConfig(model_id=MODEL, region=REGION, profile=PROFILE))
        ),
        derivation_targets=[
            DerivationTarget("age", "number", "applicant age in years"),
            DerivationTarget(
                "total_annual_income",
                "number",
                "total annual income: basic monthly salary * 12 + annual bonus",
            ),
        ],
    )
    _, report, model_calls = pipeline.ingest(salary_png(), "salary.png", "image/png")

    # Textract pulled every labelled value (not just the hardcoded keys).
    extracted = {f.key: f.value for f in report.facts}
    assert any("name" in k for k in extracted)
    assert any("salary" in k or "income" in k for k in extracted)

    # Bedrock derived age (not present in the document) with a trace.
    active = pipeline.facts.active()
    assert "age" in active
    age_fact = active["age"]
    assert age_fact.status is FactStatus.DERIVED
    assert age_fact.derivation is not None
    assert age_fact.derivation.source_fact_ids  # cites its source(s)
    assert int(age_fact.value) >= 27  # born 1998, today is 2026+

    # And it summed the salary components into a value the document never states.
    assert "total_annual_income" in active
    total = active["total_annual_income"]
    assert total.status is FactStatus.DERIVED
    assert int(str(total.value).replace(",", "")) == 85000 * 12 + 120000  # 1,140,000
    assert len(model_calls) == 1
