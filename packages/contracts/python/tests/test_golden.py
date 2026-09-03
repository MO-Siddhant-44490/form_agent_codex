"""Golden serialization: Python must parse and re-serialize the exact messages
the TypeScript side validates (Module 0 acceptance)."""

from form_contracts import (
    BrowserAction,
    DocumentFact,
    Envelope,
    PageObservation,
    VerificationResult,
)

GOLDEN_CASES = [
    ("document_fact", DocumentFact),
    ("browser_action", BrowserAction),
    ("verification_result", VerificationResult),
    ("page_observation", PageObservation),
    ("envelope", Envelope),
]


def test_golden_round_trip(golden):
    for name, model in GOLDEN_CASES:
        data = golden(name)
        parsed = model.model_validate(data)
        dumped = parsed.model_dump(mode="json", exclude_none=True)
        assert dumped == data, f"round-trip mismatch for {name}"


def test_envelope_payload_parses_to_typed_model(golden):
    env = Envelope.model_validate(golden("envelope"))
    payload = env.parse_payload()
    assert isinstance(payload, VerificationResult)
    assert payload.action_id == "action-184"
