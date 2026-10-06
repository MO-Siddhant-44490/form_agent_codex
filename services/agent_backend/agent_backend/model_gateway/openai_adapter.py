"""OpenAI (and OpenAI-compatible) model gateway: the same five touchpoints as
the Bedrock adapter — field mapping, value derivation, chat interpretation,
document extraction (images and PDFs), and repair — over the Chat Completions
API, using only the standard library (no SDK dependency).

Select it with MODEL_PROVIDER=openai and OPENAI_API_KEY (optionally
OPENAI_MODEL, OPENAI_BASE_URL). Like every gateway, its output is DATA: the
callers parse and re-validate it, and an unreachable or failing API raises
ModelUnavailable so a fill degrades to asking the user instead of crashing.
"""

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .base import (
    DerivationRequest,
    DerivationResult,
    GatewayResult,
    MappingRequest,
    ModelUnavailable,
)
from .json_chat import run_derivation_chat, run_mapping_chat

DEFAULT_OPENAI_MODEL = "gpt-4.1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"})
_FILE_TYPES = frozenset({"application/pdf"})
# Reasoning models reject a non-default temperature.
_NO_TEMPERATURE_PREFIXES = ("o1", "o3", "o4", "gpt-5")


@dataclass
class OpenAIConfig:
    api_key: str | None
    model_id: str = DEFAULT_OPENAI_MODEL
    base_url: str = DEFAULT_OPENAI_BASE_URL
    max_tokens: int = 4096
    temperature: float = 0.0
    timeout_s: int = 90


class OpenAIModelAdapter:
    def __init__(self, config: OpenAIConfig, opener=None) -> None:
        self._config = config
        self._open = opener or urllib.request.urlopen  # injectable for tests

    @property
    def model_id(self) -> str:
        return self._config.model_id

    # -- transport --------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self._config.base_url.rstrip('/')}{path}",
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with self._open(request, timeout=self._config.timeout_s) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = _error_message(error)
            raise ModelUnavailable(f"openai call failed ({error.code}): {detail}") from error
        except Exception as error:  # network, timeout, bad JSON
            raise ModelUnavailable(f"openai call failed: {error}") from error

    def _complete(self, system: str, content: Any) -> tuple[str, int | None, int | None]:
        """One system + user turn in JSON mode -> (text, input_tokens, output_tokens)."""
        body: dict[str, Any] = {
            "model": self._config.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "max_completion_tokens": self._config.max_tokens,
            # Every prompt asks for a JSON object; JSON mode guarantees one.
            "response_format": {"type": "json_object"},
        }
        if not self._config.model_id.startswith(_NO_TEMPERATURE_PREFIXES):
            body["temperature"] = self._config.temperature
        payload = self._post("/chat/completions", body)
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as error:
            raise ModelUnavailable(f"openai returned no message: {error}") from error
        usage = payload.get("usage") or {}
        return text, usage.get("prompt_tokens"), usage.get("completion_tokens")

    # -- the gateway surface ------------------------------------------------------

    def map_fields(self, request: MappingRequest) -> GatewayResult:
        return run_mapping_chat(
            request, self._config.model_id, lambda system, user: self._complete(system, user)
        )

    def derive_facts(self, request: DerivationRequest) -> DerivationResult:
        return run_derivation_chat(
            request, self._config.model_id, lambda system, user: self._complete(system, user)
        )

    def chat_json(self, system: str, user: str) -> str:
        return self._complete(system, user)[0]

    def extract_document(self, system: str, prompt: str, doc_bytes: bytes, mime: str) -> str:
        """Send an image or PDF plus a prompt; return the model's raw JSON text.
        Raises ValueError for an unsupported type (the caller falls back)."""
        part = _document_part(doc_bytes, mime)
        return self._complete(system, [part, {"type": "text", "text": prompt}])[0]

    def check_credentials(self) -> str | None:
        """Readiness probe: None when the key works, else the reason."""
        if not self._config.api_key and "api.openai.com" in self._config.base_url:
            return "OPENAI_API_KEY is not set — add it to .env or export it, then restart"
        request = urllib.request.Request(
            f"{self._config.base_url.rstrip('/')}/models", headers=self._headers(), method="GET"
        )
        try:
            with self._open(request, timeout=10):
                return None
        except urllib.error.HTTPError as error:
            return f"OpenAI rejected the key or request ({error.code}): {_error_message(error)}"
        except Exception as error:  # noqa: BLE001 — a probe never raises
            return f"OpenAI unreachable: {error}"


def _document_part(doc_bytes: bytes, mime: str) -> dict:
    m = (mime or "").split(";")[0].strip().lower()
    data = base64.b64encode(doc_bytes).decode()
    if m in _IMAGE_TYPES:
        m = "image/jpeg" if m == "image/jpg" else m
        return {"type": "image_url", "image_url": {"url": f"data:{m};base64,{data}"}}
    if m in _FILE_TYPES:
        return {
            "type": "file",
            "file": {"filename": "document.pdf", "file_data": f"data:{m};base64,{data}"},
        }
    raise ValueError(f"unsupported document type for the model: {mime!r}")


def _error_message(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read() or b"{}")
        return str((body.get("error") or {}).get("message") or error.reason)[:200]
    except Exception:  # noqa: BLE001
        return str(error.reason)[:200]
