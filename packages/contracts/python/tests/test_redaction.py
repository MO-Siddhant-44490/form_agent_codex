"""Sensitive values must be redacted in logs and errors (invariants 2, 10)."""

from form_contracts import (
    REDACTED,
    DocumentFact,
    redact_mapping,
    redacted_fact_repr,
)


def test_sensitive_keys_redacted_recursively():
    data = {
        "user": {"password": "hunter2", "name": "Ada"},
        "session": [{"api_key": "sk-123"}, {"otp_code": "000111"}],
        "note": "plain",
    }
    redacted = redact_mapping(data)
    assert redacted["user"]["password"] == REDACTED
    assert redacted["session"][0]["api_key"] == REDACTED
    assert redacted["session"][1]["otp_code"] == REDACTED
    assert redacted["user"]["name"] == "Ada"
    assert redacted["note"] == "plain"


def test_personal_fact_value_redacted_in_repr():
    fact = DocumentFact.model_validate(
        {
            "fact_id": "fact-1",
            "key": "date_of_birth",
            "value": "1998-04-17",
            "value_type": "date",
            "confidence": 0.9,
            "sensitivity": "personal",
            "status": "extracted",
            "source": {
                "document_id": "doc-1",
                "page": 1,
                "raw_text": "17 APR 1998",
                "parser": "docling",
            },
        }
    )
    repr_ = redacted_fact_repr(fact)
    assert repr_["value"] == REDACTED
    assert repr_["source"]["raw_text"] == REDACTED
    assert repr_["key"] == "date_of_birth"


def test_public_fact_value_kept():
    fact = DocumentFact.model_validate(
        {
            "fact_id": "fact-2",
            "key": "country",
            "value": "India",
            "value_type": "string",
            "confidence": 0.99,
            "sensitivity": "public",
            "status": "user_provided",
        }
    )
    assert redacted_fact_repr(fact)["value"] == "India"
