"""PageObservation validation: credential values never observed (invariant 2)."""

import pytest
from form_contracts import FormField, PageObservation
from pydantic import ValidationError


def field_data(**overrides):
    base = {
        "field_id": "f-1",
        "target": {"field_id": "f-1", "role": "textbox"},
        "input_type": "text",
    }
    base.update(overrides)
    return base


def test_password_value_must_not_be_observed():
    with pytest.raises(ValidationError, match="never be observed"):
        FormField.model_validate(field_data(input_type="password", current_value="hunter2"))


def test_password_field_allowed_when_redacted():
    field = FormField.model_validate(field_data(input_type="password", value_redacted=True))
    assert field.current_value is None


def test_redacted_field_must_not_carry_value():
    with pytest.raises(ValidationError, match="redacted"):
        FormField.model_validate(field_data(value_redacted=True, current_value="x"))


def test_url_outside_origin_rejected():
    with pytest.raises(ValidationError, match="not within origin"):
        PageObservation.model_validate(
            {
                "run_id": "run-1",
                "tab_id": 1,
                "url": "https://evil.test/steal",
                "origin": "https://example.test",
                "page_fingerprint": "sha256:x",
                "observation_seq": 0,
                "observed_at": "2026-09-02T12:00:00Z",
            }
        )
