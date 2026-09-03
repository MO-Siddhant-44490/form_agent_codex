"""DocumentFact and provenance contracts (plan.md §8.1, §10)."""

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .common import Sensitivity, StrictModel


class FactValueType(StrEnum):
    STRING = "string"
    DATE = "date"
    NUMBER = "number"
    BOOLEAN = "boolean"
    EMAIL = "email"
    PHONE = "phone"
    ADDRESS = "address"
    IDENTIFIER = "identifier"
    ENUM = "enum"


class FactStatus(StrEnum):
    EXTRACTED = "extracted"
    CORRECTED = "corrected"
    USER_PROVIDED = "user_provided"
    CONFLICTED = "conflicted"


class FactSource(StrictModel):
    """Document region a fact was extracted from (provenance, invariant 11)."""

    document_id: str
    page: int = Field(ge=1)
    bounding_box: tuple[float, float, float, float] | None = None
    raw_text: str | None = None
    parser: str


class DocumentFact(StrictModel):
    fact_id: str
    key: str
    value: str
    value_type: FactValueType
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity
    status: FactStatus
    source: FactSource | None = None

    @field_validator("sensitivity")
    @classmethod
    def _reject_credentials(cls, v: Sensitivity) -> Sensitivity:
        if v is Sensitivity.CREDENTIAL:
            raise ValueError(
                "credential material (passwords, OTPs, MFA codes, CAPTCHA answers) "
                "must never be stored as a fact (invariant 2)"
            )
        return v

    @model_validator(mode="after")
    def _extracted_needs_provenance(self) -> "DocumentFact":
        if self.status is FactStatus.EXTRACTED and self.source is None:
            raise ValueError("an extracted fact must carry document provenance (invariant 11)")
        return self
