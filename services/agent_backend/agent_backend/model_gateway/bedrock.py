"""Bedrock adapter via the Converse API (plan.md §11). Model ID and region
come from configuration; nothing else in the system knows Bedrock exists."""

from dataclasses import dataclass
from typing import Any

from .base import GatewayResult, MappingRequest, ModelUnavailable
from .json_chat import run_mapping_chat


@dataclass
class BedrockConfig:
    model_id: str
    region: str = "us-east-1"
    max_tokens: int = 1500
    temperature: float = 0.0
    read_timeout_s: int = 30


class BedrockModelAdapter:
    def __init__(self, config: BedrockConfig, client: Any | None = None) -> None:
        self._config = config
        self._client = client  # injected in tests; built lazily otherwise

    @property
    def model_id(self) -> str:
        return self._config.model_id

    def _ensure_client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._config.region,
                config=Config(
                    read_timeout=self._config.read_timeout_s,
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
        return self._client

    def map_fields(self, request: MappingRequest) -> GatewayResult:
        client = self._ensure_client()

        def complete(system: str, user: str) -> tuple[str, int | None, int | None]:
            try:
                response = client.converse(
                    modelId=self._config.model_id,
                    system=[{"text": system}],
                    messages=[{"role": "user", "content": [{"text": user}]}],
                    inferenceConfig={
                        "maxTokens": self._config.max_tokens,
                        "temperature": self._config.temperature,
                    },
                )
            except Exception as error:  # boto ClientError, timeouts, throttling
                raise ModelUnavailable(f"bedrock converse failed: {error}") from error
            content = response["output"]["message"]["content"]
            text = "".join(part.get("text", "") for part in content)
            usage = response.get("usage", {})
            return text, usage.get("inputTokens"), usage.get("outputTokens")

        return run_mapping_chat(request, self._config.model_id, complete)
