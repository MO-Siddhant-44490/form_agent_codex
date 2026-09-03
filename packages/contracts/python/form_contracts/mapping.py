"""Field-mapping, clarification, policy, and model-accounting contracts
(plan.md §8.6). Model outputs arrive as these validated types — never as
free text or executable instructions (invariant 5)."""

from enum import StrEnum

from pydantic import Field, model_validator

from .common import StrictModel


class FieldMapping(StrictModel):
    """One proposed field->fact assignment. Three valid shapes:
    - mapped: `fact_key` set (optionally with `selected_option_value` for an
      enumerated control; it must exist among the field's observed options or
      policy discards the mapping);
    - clarify: `fact_key` null and `needs_clarification` true (ambiguous, low
      confidence, or missing fact);
    - skip: `fact_key` null and `needs_clarification` false (deliberately not
      filled — e.g. a credential field the model correctly refuses to map).
    A skip yields no assignment and no question; the deterministic mapper and
    policy gate remain the enforcement points regardless."""

    field_id: str
    fact_key: str | None = None
    selected_option_value: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: bool = False
    reason: str | None = None


class FieldMappingBatch(StrictModel):
    mappings: list[FieldMapping] = Field(default_factory=list)


class QuestionKind(StrEnum):
    MISSING_FACT = "missing_fact"
    AMBIGUOUS_MAPPING = "ambiguous_mapping"
    CONFLICTING_FACTS = "conflicting_facts"
    LOW_CONFIDENCE = "low_confidence"


class UserQuestion(StrictModel):
    question_id: str
    kind: QuestionKind
    prompt: str
    field_id: str | None = None
    fact_keys: list[str] = Field(default_factory=list)
    options: list[str] | None = None


class PolicyRule(StrEnum):
    CREDENTIAL_FIELD = "credential_field"
    HIDDEN_FIELD = "hidden_field"
    UNKNOWN_TARGET = "unknown_target"
    VALUE_WITHOUT_PROVENANCE = "value_without_provenance"
    ORIGIN_MISMATCH = "origin_mismatch"
    SUBMIT_WITHOUT_APPROVAL = "submit_without_approval"
    UNSUPPORTED_OPTION = "unsupported_option"


class PolicyDecisionKind(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class PolicyDecision(StrictModel):
    action_id: str
    decision: PolicyDecisionKind
    rule: PolicyRule | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def _block_needs_rule(self) -> "PolicyDecision":
        if self.decision is PolicyDecisionKind.BLOCK and self.rule is None:
            raise ValueError("a BLOCK decision must name the rule that fired")
        return self


class ModelCallMetadata(StrictModel):
    """Accounting for one model call (plan.md §11.2): what ran, how long, how
    much — never the raw request or response content."""

    model_id: str
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = None
    output_tokens: int | None = None
    schema_valid: bool
    retries: int = Field(default=0, ge=0)
    request_fingerprint: str  # hash of the redacted request, not the request
