"""VLM document extraction: JSON parsing, credential filtering, normalization."""

from agent_backend.document_intelligence.vlm_extract import (
    build_prompt,
    parse_extraction,
    supports_vlm,
    vlm_extract,
)
from agent_backend.model_gateway.fake import FakeModelAdapter


def test_parse_extraction_normalizes_and_dedups():
    raw = (
        '{"facts": [{"key": "Full Name", "value": " Asha Rao "}, '
        '{"key": "email", "value": "asha@example.com"}, '
        '{"key": "full_name", "value": "Asha Rao"}]}'
    )
    facts = parse_extraction(raw)
    pairs = [(f.key, f.value) for f in facts]
    assert pairs == [("full_name", "Asha Rao"), ("email", "asha@example.com")]


def test_parse_extraction_drops_credentials_and_empties():
    raw = (
        '{"facts": [{"key": "password", "value": "hunter2"}, '
        '{"key": "cc_number", "value": "4111 1111"}, '
        '{"key": "otp", "value": "123456"}, '
        '{"key": "pincode", "value": "560001"}, '
        '{"key": "note", "value": ""}]}'
    )
    assert [(f.key, f.value) for f in parse_extraction(raw)] == [("pincode", "560001")]


def test_parse_extraction_tolerates_prose_wrapped_json():
    raw = 'Here you go:\n```json\n{"facts": [{"key": "email", "value": "a@b.com"}]}\n``` done'
    assert [(f.key, f.value) for f in parse_extraction(raw)] == [("email", "a@b.com")]


def test_build_prompt_targets_form_fields():
    p = build_prompt(["Email", "Pincode", ""])
    assert "Email" in p and "Pincode" in p
    assert "form being filled" in p.lower()


def test_vlm_extract_uses_the_gateway():
    gw = FakeModelAdapter()
    gw.extract_response = '{"facts": [{"key": "mobile", "value": "9990001112"}]}'
    facts = vlm_extract(gw, b"%PDF-1.4 ...", "application/pdf")
    assert [(f.key, f.value) for f in facts] == [("mobile", "9990001112")]


def test_supports_vlm_detects_capability():
    assert supports_vlm(FakeModelAdapter()) is True
    assert supports_vlm(object()) is False
    assert supports_vlm(None) is False
