"""Common base types, enums, and validation helpers shared by all contracts."""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

PROTOCOL_VERSION = "1.0"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"1.0"})

# scheme://host[:port] with no path, query, fragment, or credentials.
_ORIGIN_RE = re.compile(r"^https?://[a-zA-Z0-9.-]+(:\d{1,5})?$")


class StrictModel(BaseModel):
    """Base for all protocol messages: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


def validate_origin(value: str) -> str:
    if not _ORIGIN_RE.match(value):
        raise ValueError(f"invalid origin {value!r}: must be scheme://host[:port] with no path")
    return value


class Sensitivity(StrEnum):
    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    # Credential material (passwords, OTPs, MFA codes, CAPTCHA answers) must
    # never be stored as facts (invariant 2). The label exists so that code can
    # classify and *reject* such data explicitly.
    CREDENTIAL = "credential"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RunOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    NEEDS_USER = "NEEDS_USER"
    BLOCKED = "BLOCKED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FATAL_FAILURE = "FATAL_FAILURE"
    CANCELLED = "CANCELLED"


class HumanTakeoverReason(StrEnum):
    LOGIN = "login"
    MFA = "mfa"
    OTP = "otp"
    CAPTCHA = "captcha"
    BOT_DETECTION = "bot_detection"
    UNSUPPORTED_WIDGET = "unsupported_widget"
    USER_REQUESTED = "user_requested"
    POLICY_BLOCK = "policy_block"
