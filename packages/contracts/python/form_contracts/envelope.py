"""Protocol envelope with version checking (docs/protocol.md)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import field_validator

from .actions import ActionResult, BrowserAction
from .common import SUPPORTED_PROTOCOL_VERSIONS, StrictModel
from .observation import PageObservation
from .verification import VerificationResult


class MessageType(StrEnum):
    BROWSER_ACTION = "browser_action"
    PAGE_OBSERVATION = "page_observation"
    ACTION_RESULT = "action_result"
    VERIFICATION_RESULT = "verification_result"


PAYLOAD_MODELS = {
    MessageType.BROWSER_ACTION: BrowserAction,
    MessageType.PAGE_OBSERVATION: PageObservation,
    MessageType.ACTION_RESULT: ActionResult,
    MessageType.VERIFICATION_RESULT: VerificationResult,
}


class Envelope(StrictModel):
    protocol_version: str
    message_type: MessageType
    run_id: str
    sent_at: datetime
    payload: dict[str, Any]

    @field_validator("protocol_version")
    @classmethod
    def _supported_version(cls, v: str) -> str:
        if v not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ValueError(
                f"unsupported protocol version {v!r}; this build supports "
                f"{sorted(SUPPORTED_PROTOCOL_VERSIONS)}. Upgrade the older side "
                "before continuing."
            )
        return v

    def parse_payload(
        self,
    ) -> BrowserAction | PageObservation | ActionResult | VerificationResult:
        """Validate and return the typed payload for this envelope."""
        return PAYLOAD_MODELS[self.message_type].model_validate(self.payload)
