import pytest
from form_contracts import VerificationResult
from pydantic import ValidationError


def result(**overrides):
    base = {
        "action_id": "a-1",
        "status": "SUCCESS",
        "evidence": {"observed_value": "x", "field_valid": True},
        "recommended_transition": "CONTINUE",
    }
    base.update(overrides)
    return base


def test_success_must_not_have_failure_class():
    with pytest.raises(ValidationError, match="failure_class"):
        VerificationResult.model_validate(result(failure_class="value_mismatch"))


def test_retryable_failure_requires_failure_class():
    with pytest.raises(ValidationError, match="requires a failure_class"):
        VerificationResult.model_validate(
            result(status="RETRYABLE_FAILURE", recommended_transition="RETRY")
        )


def test_classified_failure_parses():
    VerificationResult.model_validate(
        result(
            status="RETRYABLE_FAILURE",
            failure_class="value_mismatch",
            recommended_transition="RETRY",
        )
    )
