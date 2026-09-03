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
