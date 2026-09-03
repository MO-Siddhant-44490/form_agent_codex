"""Model gateway: adapter behavior, schema enforcement, data minimization,
and untrusted fencing (Module 6)."""

import json

import pytest
from agent_backend.model_gateway.base import (
    MappingRequest,
    ModelUnavailable,
    mapping_fact_from,
    mapping_field_from,
)
from agent_backend.model_gateway.bedrock import BedrockConfig, BedrockModelAdapter
from agent_backend.model_gateway.fake import FakeModelAdapter
from agent_backend.model_gateway.json_chat import run_mapping_chat
from agent_backend.model_gateway.prompts import UNTRUSTED_OPEN, build_user_prompt
from agent_backend.model_gateway.recorded import RecordedModelAdapter, RecordingWrapper
from form_contracts import DocumentFact, FormField, TargetDescriptor


def form_field(
    field_id: str,
    label: str,
    input_type: str = "text",
    options: list[str] | None = None,
    required: bool = True,
) -> FormField:
    return FormField(
        field_id=field_id,
        target=TargetDescriptor(field_id=field_id, role="textbox", label=label),
        input_type=input_type,
        label=label,
        accessible_name=label,
        required=required,
        options=options,
    )


def fact(key: str, value: str, sensitivity: str = "personal") -> DocumentFact:
    return DocumentFact(
        fact_id=f"fact-{key}",
        key=key,
        value=value,
        value_type="string",
        confidence=1.0,
        sensitivity=sensitivity,
        status="user_provided",
    )


def request() -> MappingRequest:
    return MappingRequest(
        fields=(
            mapping_field_from(form_field("f-name", "Full name")),
            mapping_field_from(
                form_field("f-country", "Country", "select-one", options=["", "IN", "US"])
            ),
        ),
        facts=(
            mapping_fact_from(fact("full_name", "Ada Lovelace")),
            mapping_fact_from(fact("country", "India", sensitivity="public")),
        ),
    )


def test_personal_values_never_enter_the_request_or_prompt():
    req = request()
    by_key = {f.key: f for f in req.facts}
    assert by_key["full_name"].value is None  # personal: withheld
    assert by_key["country"].value == "India"  # public: allowed for options
    prompt = build_user_prompt(req)
    assert "Ada Lovelace" not in prompt
    assert "India" in prompt


def test_page_text_is_fenced_as_untrusted():
    prompt = build_user_prompt(request())
    assert UNTRUSTED_OPEN in prompt
    # Fields land inside the fence, facts outside it.
    assert prompt.index(UNTRUSTED_OPEN) < prompt.index("Full name")
    assert prompt.index("KNOWN FACTS") > prompt.index("UNTRUSTED_PAGE_DATA>>>")


def test_fake_adapter_maps_by_label_and_selects_options():
    result = FakeModelAdapter().map_fields(request())
    by_field = {m.field_id: m for m in result.batch.mappings}
    assert by_field["f-name"].fact_key == "full_name"
    assert by_field["f-country"].fact_key == "country"
    assert by_field["f-country"].selected_option_value == "IN"
    assert result.metadata.schema_valid is True
    assert result.metadata.request_fingerprint == request().fingerprint()


def test_recording_roundtrip(tmp_path):
    recorded_result = RecordingWrapper(FakeModelAdapter(), tmp_path).map_fields(request())
    replayed = RecordedModelAdapter(tmp_path).map_fields(request())
    assert replayed.batch == recorded_result.batch


def test_recorded_adapter_without_recording_is_unavailable(tmp_path):
    with pytest.raises(ModelUnavailable, match="no recording"):
        RecordedModelAdapter(tmp_path).map_fields(request())


def test_schema_invalid_output_retries_once_then_fails():
    calls = []

    def bad_complete(system: str, user: str):
        calls.append(1)
        return "I think the name field maps to full_name!", None, None

    with pytest.raises(ModelUnavailable, match="schema-invalid"):
        run_mapping_chat(request(), "test-model", bad_complete)
    assert len(calls) == 2  # bounded retry, then classified failure


def test_recovers_when_retry_returns_valid_json():
    responses = iter(
        [
            "not json at all",
            json.dumps(
                {"mappings": [{"field_id": "f-name", "fact_key": "full_name", "confidence": 0.9}]}
            ),
        ]
    )

    def flaky_complete(system: str, user: str):
        return next(responses), 10, 5

    result = run_mapping_chat(request(), "test-model", flaky_complete)
    assert result.metadata.retries == 1
    assert result.batch.mappings[0].fact_key == "full_name"


class FakeBedrockClient:
    def __init__(self):
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "mappings": [
                                        {
                                            "field_id": "f-name",
                                            "fact_key": "full_name",
                                            "confidence": 0.95,
                                        }
                                    ],
                                }
                            )
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 120, "outputTokens": 30},
        }


def test_bedrock_adapter_request_shape_and_accounting():
    client = FakeBedrockClient()
    adapter = BedrockModelAdapter(
        BedrockConfig(model_id="us.anthropic.claude-sonnet-5:0"),
        client=client,
    )
    result = adapter.map_fields(request())

    call = client.calls[0]
    assert call["modelId"] == "us.anthropic.claude-sonnet-5:0"
    assert call["inferenceConfig"]["temperature"] == 0.0
    sent_text = call["messages"][0]["content"][0]["text"]
    assert "Ada Lovelace" not in sent_text  # minimization holds end-to-end
    assert UNTRUSTED_OPEN in sent_text

    assert result.batch.mappings[0].fact_key == "full_name"
    assert result.metadata.input_tokens == 120
    assert result.metadata.output_tokens == 30
    assert result.metadata.model_id == "us.anthropic.claude-sonnet-5:0"


def test_bedrock_failure_is_classified_unavailable():
    class BrokenClient:
        def converse(self, **kwargs):
            raise RuntimeError("ThrottlingException")

    adapter = BedrockModelAdapter(BedrockConfig(model_id="m"), client=BrokenClient())
    with pytest.raises(ModelUnavailable, match="bedrock converse failed"):
        adapter.map_fields(request())
