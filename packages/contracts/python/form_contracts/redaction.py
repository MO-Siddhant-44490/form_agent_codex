"""Redaction utilities: sensitive values must never reach logs, traces, or
model requests (invariants 2 and 10)."""

from typing import Any

from .common import Sensitivity
from .facts import DocumentFact

REDACTED = "[REDACTED]"

# Key substrings whose values are always redacted, regardless of context.
SENSITIVE_KEY_PATTERNS = (
    "resolved_value",
    "content_base64",
    "password",
    "passwd",
    "otp",
    "mfa",
    "captcha",
    "secret",
    "token",
    "api_key",
    "apikey",
    "ssn",
    "social_security",
    "credit_card",
    "card_number",
    "cvv",
    "pin",
)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(pattern in lowered for pattern in SENSITIVE_KEY_PATTERNS)


def redact_mapping(data: Any, extra_sensitive_keys: frozenset[str] = frozenset()) -> Any:
    """Recursively redact sensitive values in dicts/lists for safe logging."""
    if isinstance(data, dict):
        return {
            key: (
                REDACTED
                if is_sensitive_key(key) or key in extra_sensitive_keys
                else redact_mapping(value, extra_sensitive_keys)
            )
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact_mapping(item, extra_sensitive_keys) for item in data]
    return data


def redacted_fact_repr(fact: DocumentFact) -> dict[str, Any]:
    """Loggable representation of a fact: value hidden unless public."""
    dumped = fact.model_dump(mode="json", exclude_none=True)
    if fact.sensitivity is not Sensitivity.PUBLIC:
        dumped["value"] = REDACTED
        source = dumped.get("source")
        if source and "raw_text" in source:
            source["raw_text"] = REDACTED
    return dumped
