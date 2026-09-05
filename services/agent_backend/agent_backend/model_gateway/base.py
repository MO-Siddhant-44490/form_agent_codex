"""Gateway request/response types and the adapter protocol.

Data minimization (plan.md §11.2): a MappingRequest carries compact typed
views of fields and facts, never raw DOM or documents. Fact *values* are
included only for public-sensitivity facts (needed to pick enumerated
options); personal values never leave the backend for mapping decisions."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol

from form_contracts import (
    DocumentFact,
    FieldMappingBatch,
    FormField,
    ModelCallMetadata,
    Sensitivity,
)


@dataclass(frozen=True)
class MappingField:
    field_id: str
    input_type: str
    label: str | None
    accessible_name: str | None
    required: bool
    options: tuple[str, ...] | None
    nearby_text: str | None
    # Additional grounding signals (plan.md insight #2): the autocomplete token
    # is a strong structural hint, the placeholder is often the only visible label.
    autocomplete: str | None = None
    placeholder: str | None = None


@dataclass(frozen=True)
class MappingFact:
    key: str
    value_type: str
    sensitivity: str
    # Only populated for public facts; personal/sensitive values are withheld.
    value: str | None


@dataclass(frozen=True)
class MappingRequest:
    fields: tuple[MappingField, ...]
    facts: tuple[MappingFact, ...]

    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "fields": [f.__dict__ for f in self.fields],
                "facts": [f.__dict__ for f in self.facts],
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def mapping_field_from(form_field: FormField) -> MappingField:
    return MappingField(
        field_id=form_field.field_id,
        input_type=form_field.input_type,
        label=form_field.label,
        accessible_name=form_field.accessible_name,
        required=form_field.required,
        options=tuple(form_field.options) if form_field.options else None,
        nearby_text=form_field.nearby_text,
        autocomplete=form_field.target.autocomplete,
        placeholder=form_field.target.placeholder,
    )


def mapping_fact_from(fact: DocumentFact) -> MappingFact:
    return MappingFact(
        key=fact.key,
        value_type=str(fact.value_type),
        sensitivity=str(fact.sensitivity),
        value=fact.value if fact.sensitivity is Sensitivity.PUBLIC else None,
    )


@dataclass
class GatewayResult:
    batch: FieldMappingBatch
    metadata: ModelCallMetadata


class ModelUnavailable(Exception):
    """Timeout, throttling, or schema-invalid output after bounded retries.
    Callers fall back to deterministic behavior or abstain (plan.md §11.2)."""


class ModelGateway(Protocol):
    def map_fields(self, request: MappingRequest) -> GatewayResult: ...


@dataclass
class GatewayAccounting:
    """In-memory call log (redacted metadata only)."""

    calls: list[ModelCallMetadata] = field(default_factory=list)

    def record(self, metadata: ModelCallMetadata) -> None:
        self.calls.append(metadata)


# -- derivation (compute/infer values not directly in the document) --------


@dataclass(frozen=True)
class AvailableFact:
    """A fact already extracted, offered to the derivation model as a source.
    Values are included so the model can actually compute (age, sums, tenure);
    the derivation stage runs on already-ingested document data, and its
    output is re-validated against these source ids before use."""

    fact_id: str
    key: str
    value: str
    value_type: str


@dataclass(frozen=True)
class DerivationTarget:
    """A value the form needs that is not directly present."""

    key: str
    value_type: str
    description: str


@dataclass(frozen=True)
class DerivationRequest:
    available: tuple[AvailableFact, ...]
    targets: tuple[DerivationTarget, ...]
    today: str  # ISO date, so "age"-style calculations are deterministic

    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "available": [a.__dict__ for a in self.available],
                "targets": [t.__dict__ for t in self.targets],
                "today": self.today,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class DerivationResult:
    facts: list[DocumentFact]
    metadata: ModelCallMetadata
