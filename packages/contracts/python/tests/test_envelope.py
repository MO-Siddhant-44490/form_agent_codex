import pytest
from form_contracts import Envelope
from pydantic import ValidationError


def envelope(**overrides):
    base = {
        "protocol_version": "1.0",
        "message_type": "action_result",
        "run_id": "run-1",
        "sent_at": "2026-09-02T12:00:00Z",
        "payload": {"action_id": "a-1", "status": "EXECUTED"},
    }
    base.update(overrides)
    return base


def test_incompatible_protocol_version_fails_clearly():
    with pytest.raises(ValidationError, match="unsupported protocol version"):
        Envelope.model_validate(envelope(protocol_version="0.9"))


def test_unknown_message_type_rejected():
    with pytest.raises(ValidationError):
        Envelope.model_validate(envelope(message_type="run_shell_command"))


def test_invalid_payload_rejected_on_parse():
    env = Envelope.model_validate(envelope(payload={"action_id": "a-1", "status": "NOPE"}))
    with pytest.raises(ValidationError):
        env.parse_payload()
