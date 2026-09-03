"""BrowserAction validation: unknown kinds, missing binding fields, and the
submission lock (invariants 1, 6, 8)."""

import pytest
from form_contracts import BrowserAction
from pydantic import ValidationError


def valid_action(**overrides):
    base = {
        "action_id": "a-1",
        "run_id": "run-1",
        "tab_id": 1,
        "origin": "https://example.test",
        "sequence_number": 1,
        "kind": "SET_TEXT",
        "target": {"field_id": "f-1", "role": "textbox"},
        "value_ref": "fact://fact-1",
        "expected_effect": {"field_value": "x"},
        "idempotency_key": "run-1:f-1:x",
    }
    base.update(overrides)
    return base


def test_valid_action_parses():
    BrowserAction.model_validate(valid_action())


def test_unknown_kind_rejected():
    with pytest.raises(ValidationError):
        BrowserAction.model_validate(valid_action(kind="EXECUTE_JS"))


@pytest.mark.parametrize("field", ["origin", "sequence_number", "run_id", "tab_id"])
def test_binding_fields_cannot_be_omitted(field):
    data = valid_action()
    del data[field]
    with pytest.raises(ValidationError):
        BrowserAction.model_validate(data)


def test_mutating_action_requires_idempotency_key():
    with pytest.raises(ValidationError, match="idempotency_key"):
        BrowserAction.model_validate(valid_action(idempotency_key=None))


def test_mutating_action_requires_expected_effect():
    with pytest.raises(ValidationError, match="expected_effect"):
        BrowserAction.model_validate(valid_action(expected_effect=None))


def test_submit_without_approval_token_rejected():
    with pytest.raises(ValidationError, match="approval_token_id"):
        BrowserAction.model_validate(valid_action(kind="SUBMIT"))


def test_submit_with_approval_token_parses():
    BrowserAction.model_validate(valid_action(kind="SUBMIT", approval_token_id="tok-1"))


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.test/path",
        "javascript:alert(1)",
        "example.test",
        "https://user:pass@example.test",
        "file:///etc/passwd",
    ],
)
def test_invalid_origins_rejected(origin):
    with pytest.raises(ValidationError):
        BrowserAction.model_validate(valid_action(origin=origin))


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        BrowserAction.model_validate(valid_action(javascript="alert(1)"))


def test_non_mutating_action_needs_no_idempotency_key():
    BrowserAction.model_validate(
        {
            "action_id": "a-2",
            "run_id": "run-1",
            "tab_id": 1,
            "origin": "https://example.test",
            "sequence_number": 2,
            "kind": "SCROLL",
        }
    )
