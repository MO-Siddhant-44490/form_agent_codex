"""Model gateway selection from the environment. Bedrock is the default
provider (plan.md §11 baseline); MODEL_PROVIDER=fake keeps a run offline and
deterministic. The mapper always degrades to abstention if the model is
unavailable at call time (ModelAssistedMapper catches ModelUnavailable), so a
missing/expired credential never crashes a run — it just asks the user."""

import os

from ..mapper import DeterministicMapper, Mapper, ModelAssistedMapper
from .base import ModelGateway
from .bedrock import BedrockConfig, BedrockModelAdapter
from .fake import FakeModelAdapter

# APAC cross-region inference profile; override per account/region with
# BEDROCK_MODEL_ID (list options: python -m agent_backend.model_gateway.list_models).
DEFAULT_BEDROCK_MODEL_ID = "apac.anthropic.claude-sonnet-4-5-20250929-v1:0"


def build_gateway_from_env() -> ModelGateway:
    provider = os.environ.get("MODEL_PROVIDER", "bedrock").lower()
    if provider == "fake":
        return FakeModelAdapter()
    if provider == "local":
        from .local_openai import LocalOpenAIConfig, OpenAICompatibleLocalAdapter

        return OpenAICompatibleLocalAdapter(
            LocalOpenAIConfig(
                base_url=os.environ.get("LOCAL_MODEL_URL", "http://localhost:8000/v1"),
                model_id=os.environ.get("LOCAL_MODEL_ID", "local-model"),
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


def build_default_mapper() -> Mapper:
    """Application-level default mapper. MODEL_PROVIDER=none forces the pure
    deterministic mapper (no model calls at all)."""
    if os.environ.get("MODEL_PROVIDER", "").lower() == "none":
        return DeterministicMapper()
    return ModelAssistedMapper(build_gateway_from_env())
