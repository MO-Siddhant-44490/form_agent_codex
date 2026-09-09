"""Deterministic fake adapter for unit and graph tests: maps by normalized
label/accessible-name similarity to fact keys, no network, no cost."""

import time

from form_contracts import FieldMapping, FieldMappingBatch, ModelCallMetadata

from .base import GatewayResult, MappingFact, MappingField, MappingRequest

_LABEL_HINTS: dict[str, tuple[str, ...]] = {
    "full_name": ("full name", "name", "applicant"),
    "email": ("email", "e-mail"),
    "phone": ("phone", "mobile", "contact number"),
    "date_of_birth": ("date of birth", "dob", "birth"),
    "country": ("country", "residence"),
    "years_experience": ("experience", "years"),
    "contact_method": ("contact method", "preferred contact"),
    "subscribe": ("subscribe", "newsletter"),
}


def _label_text(field: MappingField) -> str:
    return " ".join(
        part for part in (field.label, field.accessible_name, field.nearby_text) if part
    ).lower()


def _pick_option(fact: MappingFact, options: tuple[str, ...]) -> str | None:
    if fact.value is None:
        return None
    if fact.value in options:
        return fact.value
    lowered = fact.value.lower()
    # e.g. fact "India" vs option value "IN" with no label knowledge: prefix.
    for option in options:
        if option and (lowered.startswith(option.lower()) or option.lower().startswith(lowered)):
            return option
    return None


class FakeModelAdapter:
    model_id = "fake-mapper-v1"

    # Tests may set a canned chat plan (raw JSON string) the interpreter parses.
    chat_response: str = '{"reply": "ok", "ops": []}'

    def chat_json(self, system: str, user: str) -> str:
        return self.chat_response

    def map_fields(self, request: MappingRequest) -> GatewayResult:
        start = time.monotonic()
        fact_keys = {f.key: f for f in request.facts}
        mappings: list[FieldMapping] = []
        for field in request.fields:
            text = _label_text(field)
            chosen: str | None = None
            for key, hints in _LABEL_HINTS.items():
                if key in fact_keys and any(hint in text for hint in hints):
                    chosen = key
                    break
            if chosen is None:
                mappings.append(
                    FieldMapping(
                        field_id=field.field_id,
                        confidence=0.2,
                        needs_clarification=True,
                        reason="no label match",
                    )
                )
                continue
            selected = _pick_option(fact_keys[chosen], field.options) if field.options else None
            mappings.append(
                FieldMapping(
                    field_id=field.field_id,
                    fact_key=chosen,
                    selected_option_value=selected,
                    confidence=0.9,
                )
            )
        return GatewayResult(
            batch=FieldMappingBatch(mappings=mappings),
            metadata=ModelCallMetadata(
                model_id=self.model_id,
                latency_ms=int((time.monotonic() - start) * 1000),
                schema_valid=True,
                request_fingerprint=request.fingerprint(),
            ),
        )

    def derive_facts(self, request):
        """Deterministic derivation for tests: age from date_of_birth, and
        sum of numeric source facts into any target whose key contains
        'total'. Enough to exercise the pipeline without a model."""
        import time
        from datetime import date

        from .base import DerivationResult

        start = time.monotonic()
        by_key = {a.key: a for a in request.available}
        facts = []
        today = date.fromisoformat(request.today)
        for target in request.targets:
            if "age" in target.key and "date_of_birth" in by_key:
                dob = date.fromisoformat(by_key["date_of_birth"].value)
                age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                facts.append(
                    self._derived(
                        target.key,
                        str(age),
                        target.value_type,
                        "age_from_date_of_birth",
                        [by_key["date_of_birth"].fact_id],
                        f"{today} minus DOB {dob}",
                        request.fingerprint(),
                    )
                )
            elif "total" in target.key:
                nums, ids = [], []
                for a in request.available:
                    try:
                        nums.append(float(a.value.replace(",", "")))
                        ids.append(a.fact_id)
                    except ValueError:
                        continue
                if nums:
                    total = sum(nums)
                    value = str(int(total)) if total.is_integer() else str(total)
                    facts.append(
                        self._derived(
                            target.key,
                            value,
                            target.value_type,
                            "sum",
                            ids,
                            f"sum of {len(nums)} numeric source facts",
                            request.fingerprint(),
                        )
                    )
        return DerivationResult(
            facts=facts,
            metadata=ModelCallMetadata(
                model_id=self.model_id,
                latency_ms=int((time.monotonic() - start) * 1000),
                schema_valid=True,
                request_fingerprint=request.fingerprint(),
            ),
        )

    @staticmethod
    def _derived(key, value, value_type, operation, source_ids, explanation, fp):
        from form_contracts import Derivation, DocumentFact, FactStatus

        return DocumentFact(
            fact_id=f"derived-{key}-{fp}",
            key=key,
            value=value,
            value_type=value_type,
            confidence=0.95,
            sensitivity="personal",
            status=FactStatus.DERIVED,
            derivation=Derivation(
                operation=operation,
                source_fact_ids=source_ids,
                explanation=explanation,
            ),
        )
