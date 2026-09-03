import pytest
from form_contracts import ApprovalToken, RunState
from pydantic import ValidationError


def test_approval_token_expiry_must_follow_issue():
    with pytest.raises(ValidationError, match="expires_at"):
        ApprovalToken.model_validate(
            {
                "token_id": "tok-1",
                "run_id": "run-1",
                "origin": "https://example.test",
                "issued_at": "2026-09-02T12:00:00Z",
                "expires_at": "2026-09-02T11:59:00Z",
            }
        )


def test_approval_scope_only_submit():
    with pytest.raises(ValidationError):
        ApprovalToken.model_validate(
            {
                "token_id": "tok-1",
                "run_id": "run-1",
                "origin": "https://example.test",
                "scope": "anything",
                "issued_at": "2026-09-02T12:00:00Z",
                "expires_at": "2026-09-02T12:10:00Z",
            }
        )


def run_state(**overrides):
    base = {
        "run_id": "run-1",
        "phase": "perceive",
        "budgets": {
            "max_steps": 100,
            "max_retries_per_action": 3,
            "max_model_calls": 50,
            "max_wall_clock_seconds": 600,
        },
        "created_at": "2026-09-02T12:00:00Z",
        "updated_at": "2026-09-02T12:00:00Z",
    }
    base.update(overrides)
    return base


def test_terminal_run_requires_outcome():
    with pytest.raises(ValidationError, match="classified outcome"):
        RunState.model_validate(run_state(phase="terminal"))


def test_outcome_only_in_terminal_phase():
    with pytest.raises(ValidationError, match="terminal"):
        RunState.model_validate(run_state(outcome="COMPLETED"))


def test_terminal_with_outcome_parses():
    RunState.model_validate(run_state(phase="terminal", outcome="COMPLETED"))
