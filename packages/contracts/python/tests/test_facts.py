"""DocumentFact validation: credential rejection and provenance (invariants 2, 11)."""

import pytest
from form_contracts import DocumentFact
from pydantic import ValidationError


def valid_fact(**overrides):
    base = {
        "fact_id": "fact-1",
        "key": "date_of_birth",
        "value": "1998-04-17",
        "value_type": "date",
        "confidence": 0.9,
        "sensitivity": "personal",
        "status": "extracted",
        "source": {"document_id": "doc-1", "page": 1, "parser": "docling"},
    }
    base.update(overrides)
    return base


def test_valid_fact_parses():
    DocumentFact.model_validate(valid_fact())


def test_credential_sensitivity_rejected():
    with pytest.raises(ValidationError, match="never be stored"):
        DocumentFact.model_validate(valid_fact(sensitivity="credential"))


def test_extracted_fact_requires_provenance():
    with pytest.raises(ValidationError, match="provenance"):
        DocumentFact.model_validate(valid_fact(source=None))


def test_user_provided_fact_may_omit_source():
    DocumentFact.model_validate(valid_fact(status="user_provided", source=None))


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_bounds(confidence):
    with pytest.raises(ValidationError):
        DocumentFact.model_validate(valid_fact(confidence=confidence))
