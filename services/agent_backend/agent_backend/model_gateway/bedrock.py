"""Bedrock adapter via the Converse API (plan.md §11). Model ID and region
come from configuration; nothing else in the system knows Bedrock exists."""

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


@dataclass
class BedrockConfig:
    model_id: str
    region: str = "us-east-1"
    profile: str | None = None  # AWS named profile (e.g. an SSO profile)
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

            session = boto3.Session(profile_name=self._config.profile)
            self._client = session.client(
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

    def derive_facts(self, request: DerivationRequest) -> DerivationResult:
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
            except Exception as error:
                raise ModelUnavailable(f"bedrock converse failed: {error}") from error
            content = response["output"]["message"]["content"]
            text = "".join(part.get("text", "") for part in content)
            usage = response.get("usage", {})
            return text, usage.get("inputTokens"), usage.get("outputTokens")

        return run_derivation_chat(request, self._config.model_id, complete)

    def chat_json(self, system: str, user: str) -> str:
        """A single system+user turn returning the model's raw text (expected to
        be JSON). Used by the reasoning chat interpreter; the caller parses and
        re-validates the result — model output is never trusted directly."""
        client = self._ensure_client()
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
        except Exception as error:
            raise ModelUnavailable(f"bedrock converse failed: {error}") from error
        content = response["output"]["message"]["content"]
        return "".join(part.get("text", "") for part in content)

    def extract_document(self, system: str, prompt: str, doc_bytes: bytes, mime: str) -> str:
        """Send a document (image or PDF/office file) plus a prompt to the
        multimodal model and return its raw text (expected JSON). The model reads
        the document directly — layout-agnostic — so extraction does not depend on
        a fixed label recognizer. Raises ValueError for an unsupported type (the
        caller can fall back) and ModelUnavailable on a call failure."""
        block = _content_block(doc_bytes, mime)
        client = self._ensure_client()
        try:
            response = client.converse(
                modelId=self._config.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [block, {"text": prompt}]}],
                inferenceConfig={"maxTokens": self._config.max_tokens, "temperature": 0.0},
            )
        except Exception as error:
            raise ModelUnavailable(f"bedrock converse (document) failed: {error}") from error
        content = response["output"]["message"]["content"]
        return "".join(part.get("text", "") for part in content)


# Bedrock Converse content blocks by MIME. Images use an `image` block; PDFs and
# office/text files use a `document` block. Anything else raises so the caller
# can fall back.
_IMAGE_FORMATS = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}
_DOC_FORMATS = {
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/csv": "csv",
    "text/html": "html",
    "text/markdown": "md",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
}


def _content_block(doc_bytes: bytes, mime: str) -> dict:
    m = (mime or "").split(";")[0].strip().lower()
    if m in _IMAGE_FORMATS:
        return {"image": {"format": _IMAGE_FORMATS[m], "source": {"bytes": doc_bytes}}}
    fmt = _DOC_FORMATS.get(m)
    if fmt is None:
        raise ValueError(f"unsupported document type for the VLM: {mime!r}")
    return {
        "document": {"format": fmt, "name": "uploaded document", "source": {"bytes": doc_bytes}}
    }
