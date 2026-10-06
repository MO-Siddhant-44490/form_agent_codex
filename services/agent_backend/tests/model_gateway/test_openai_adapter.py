"""OpenAI provider: request shape, document parts, error handling, provider
selection and .env loading — against a recording stand-in for the HTTP layer
(tests never call a live model)."""

import io
import json
import urllib.error

import pytest
from agent_backend.model_gateway.base import (
    MappingRequest,
    ModelUnavailable,
    mapping_fact_from,
    mapping_field_from,
)
from agent_backend.model_gateway.openai_adapter import OpenAIConfig, OpenAIModelAdapter
from agent_backend.transports.fake import FakeField, FakeTransport
from form_contracts import DocumentFact, FactStatus, FactValueType, Sensitivity


class Recorder:
    """Stands in for urllib.request.urlopen: records requests, replays replies."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())


def completion(content: str, usage=(120, 30)) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
    }


def adapter(replies, model="gpt-4.1"):
    rec = Recorder(replies)
    return OpenAIModelAdapter(OpenAIConfig(api_key="sk-test", model_id=model), opener=rec), rec


def body(request) -> dict:
    return json.loads(request.data)


def test_chat_json_sends_key_json_mode_and_returns_text():
    gw, rec = adapter([completion('{"reply": "ok", "ops": []}')])
    assert gw.chat_json("system", "user") == '{"reply": "ok", "ops": []}'
    req = rec.requests[0]
    assert req.full_url == "https://api.openai.com/v1/chat/completions"
    assert req.get_header("Authorization") == "Bearer sk-test"
    b = body(req)
    assert b["model"] == "gpt-4.1"
    assert b["response_format"] == {"type": "json_object"}
    assert b["messages"][0] == {"role": "system", "content": "system"}
    assert b["temperature"] == 0.0 and b["max_completion_tokens"] == 4096


def test_reasoning_models_get_no_temperature():
    gw, rec = adapter([completion("{}")], model="o4-mini")
    gw.chat_json("s", "u")
    assert "temperature" not in body(rec.requests[0])


def test_documents_go_as_file_or_image_parts():
    gw, rec = adapter([completion('{"facts": []}'), completion('{"facts": []}')])
    gw.extract_document("sys", "extract", b"%PDF-1.4 ...", "application/pdf")
    gw.extract_document("sys", "extract", b"\x89PNG...", "image/png")
    pdf_part = body(rec.requests[0])["messages"][1]["content"][0]
    img_part = body(rec.requests[1])["messages"][1]["content"][0]
    assert pdf_part["type"] == "file" and pdf_part["file"]["file_data"].startswith(
        "data:application/pdf;base64,"
    )
    assert img_part["type"] == "image_url" and img_part["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    with pytest.raises(ValueError):
        gw.extract_document("sys", "x", b"...", "application/zip")


def test_api_errors_become_model_unavailable():
    err = urllib.error.HTTPError(
        "https://api.openai.com/v1/chat/completions",
        401,
        "Unauthorized",
        {},
        io.BytesIO(b'{"error": {"message": "Incorrect API key provided"}}'),
    )
    gw, _ = adapter([err])
    with pytest.raises(ModelUnavailable, match="Incorrect API key"):
        gw.chat_json("s", "u")


def test_field_mapping_round_trip_is_validated_like_any_provider():
    obs = FakeTransport(fields=[FakeField("n", "text", "fld_n", "Your full name")]).observe()
    fact = DocumentFact(
        fact_id="f1",
        key="full_name",
        value="Ananya",
        value_type=FactValueType.STRING,
        confidence=1.0,
        sensitivity=Sensitivity.PERSONAL,
        status=FactStatus.USER_PROVIDED,
    )
    request = MappingRequest(
        fields=(mapping_field_from(obs.fields[0]),), facts=(mapping_fact_from(fact),)
    )
    reply = {
        "mappings": [
            {
                "field_id": "n",
                "fact_key": "full_name",
                "selected_option_value": None,
                "confidence": 0.95,
                "needs_clarification": False,
                "reason": None,
            }
        ]
    }
    gw, _ = adapter([completion(json.dumps(reply))])
    result = gw.map_fields(request)
    assert result.batch.mappings[0].fact_key == "full_name"
    assert result.metadata.model_id == "gpt-4.1"


def test_check_credentials_reports_a_missing_key():
    gw = OpenAIModelAdapter(OpenAIConfig(api_key=None))
    assert "OPENAI_API_KEY" in gw.check_credentials()
    ok, _ = adapter([{"data": []}])
    assert ok.check_credentials() is None


def test_provider_selection(monkeypatch):
    from agent_backend.model_gateway.factory import build_gateway_from_env
    from agent_backend.model_gateway.openai_adapter import OpenAIModelAdapter as A

    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    gw = build_gateway_from_env()
    assert isinstance(gw, A) and gw.model_id == "gpt-4o"  # a key alone selects OpenAI
    monkeypatch.setenv("MODEL_PROVIDER", "bedrock")
    assert not isinstance(build_gateway_from_env(), A)  # an explicit choice wins


def test_dotenv_is_loaded_without_overriding(tmp_path, monkeypatch):
    from agent_backend.api.server import load_dotenv

    env = tmp_path / ".env"
    env.write_text(
        '# comment\nOPENAI_API_KEY="sk-from-file"\nexport MODEL_PROVIDER=openai\nPORT=9999\n'
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("PORT", "8000")
    load_dotenv(str(env))
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-from-file"
    assert os.environ["MODEL_PROVIDER"] == "openai"
    assert os.environ["PORT"] == "8000"  # already set: not overridden
