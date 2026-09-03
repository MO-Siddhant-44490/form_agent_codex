"""OpenAI-compatible local adapter (vLLM/SGLang) sharing the same JSON-chat
core as Bedrock, so evaluation cases run through both unchanged."""

import json
import urllib.request
from dataclasses import dataclass

from .base import GatewayResult, MappingRequest, ModelUnavailable
from .json_chat import run_mapping_chat


@dataclass
class LocalOpenAIConfig:
    base_url: str  # e.g. http://localhost:8000/v1
    model_id: str
    max_tokens: int = 1500
    temperature: float = 0.0
    timeout_s: int = 30


class OpenAICompatibleLocalAdapter:
    def __init__(self, config: LocalOpenAIConfig) -> None:
        self._config = config

    @property
    def model_id(self) -> str:
        return self._config.model_id

    def map_fields(self, request: MappingRequest) -> GatewayResult:
        def complete(system: str, user: str) -> tuple[str, int | None, int | None]:
            body = json.dumps(
                {
                    "model": self._config.model_id,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": self._config.max_tokens,
                    "temperature": self._config.temperature,
                }
            ).encode()
            http_request = urllib.request.Request(
                f"{self._config.base_url.rstrip('/')}/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(
                    http_request, timeout=self._config.timeout_s
                ) as response:
                    payload = json.loads(response.read())
            except Exception as error:
                raise ModelUnavailable(f"local model call failed: {error}") from error
            usage = payload.get("usage", {})
            return (
                payload["choices"][0]["message"]["content"],
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
            )

        return run_mapping_chat(request, self._config.model_id, complete)
