"""Model gateway selection from the environment.

    MODEL_PROVIDER=openai   OpenAI (OPENAI_API_KEY; optional OPENAI_MODEL,
                            OPENAI_BASE_URL for any OpenAI-compatible API)
    MODEL_PROVIDER=bedrock  Claude on Amazon Bedrock (AWS credentials)
    MODEL_PROVIDER=local    a local OpenAI-compatible server (vLLM, Ollama...)
    MODEL_PROVIDER=fake     offline and deterministic (tests, demos)
    MODEL_PROVIDER=none     no model at all (deterministic mapper only)

Unset: OpenAI when OPENAI_API_KEY is present, otherwise Bedrock.

The mapper always degrades to abstention if the model is unavailable at call
time (ModelAssistedMapper catches ModelUnavailable), so a
missing/expired credential never crashes a run — it just asks the user."""

import os

from ..mapper import DeterministicMapper, Mapper, ModelAssistedMapper
from .base import ModelGateway
from .bedrock import BedrockConfig, BedrockModelAdapter
from .fake import FakeModelAdapter

# APAC cross-region inference profile; override per account/region with
# BEDROCK_MODEL_ID (list options: python -m agent_backend.model_gateway.list_models).
DEFAULT_BEDROCK_MODEL_ID = "apac.anthropic.claude-sonnet-4-20250514-v1:0"


def selected_provider() -> str:
    explicit = os.environ.get("MODEL_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    return "openai" if os.environ.get("OPENAI_API_KEY") else "bedrock"


def build_gateway_from_env() -> ModelGateway:
    provider = selected_provider()
    if provider == "fake":
        return FakeModelAdapter()
    if provider in ("openai", "local"):
        from .openai_adapter import (
            DEFAULT_OPENAI_BASE_URL,
            DEFAULT_OPENAI_MODEL,
            OpenAIConfig,
            OpenAIModelAdapter,
        )

        if provider == "openai":
            return OpenAIModelAdapter(
                OpenAIConfig(
                    api_key=os.environ.get("OPENAI_API_KEY"),
                    model_id=os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
                    base_url=os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
                )
            )
        # A local OpenAI-compatible server (vLLM, Ollama, LM Studio): no key needed.
        return OpenAIModelAdapter(
            OpenAIConfig(
                api_key=os.environ.get("LOCAL_MODEL_KEY"),
                model_id=os.environ.get("LOCAL_MODEL_ID", "local-model"),
                base_url=os.environ.get("LOCAL_MODEL_URL", "http://localhost:11434/v1"),
            )
        )
    # Default: Bedrock.
    return BedrockModelAdapter(
        BedrockConfig(
            model_id=os.environ.get("BEDROCK_MODEL_ID", DEFAULT_BEDROCK_MODEL_ID),
            region=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")),
            profile=os.environ.get("AWS_PROFILE"),
        )
    )


def build_default_mapper(memory=None) -> Mapper:
    """Application-level default mapper. MODEL_PROVIDER=none forces the pure
    deterministic mapper (no model calls at all). Otherwise the model-assisted
    mapper is wired with a derivation engine on the same gateway, so required
    fields with no direct fact are computed (age, totals, tenure) before
    falling back to clarification. DERIVE_VALUES=off disables derivation.

    `memory`, when provided, is the cross-run episodic mapping memory consulted
    before the model, so a previously-seen field resolves without a model call."""
    if os.environ.get("MODEL_PROVIDER", "").lower() == "none":
        return DeterministicMapper()
    gateway = build_gateway_from_env()
    derivation = None
    if os.environ.get("DERIVE_VALUES", "on").lower() != "off":
        from ..document_intelligence.derivation import DerivationEngine

        derivation = DerivationEngine(gateway)
    return ModelAssistedMapper(gateway, derivation_engine=derivation, memory=memory)
