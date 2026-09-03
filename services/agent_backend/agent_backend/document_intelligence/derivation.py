"""Derivation stage (plan.md §10, the 'infer/calculate indirect values' need):
given already-extracted facts and the values a form needs but does not have,
ask the model to compute them, with a validated derivation trace. Runs only
for genuinely missing targets — never re-derives what was extracted."""

from dataclasses import dataclass
from datetime import date

from form_contracts import DocumentFact, ModelCallMetadata

from ..model_gateway.base import (
    AvailableFact,
    DerivationRequest,
    DerivationTarget,
    ModelUnavailable,
)


@dataclass
class DerivationOutcome:
    facts: list[DocumentFact]
    model_calls: list[ModelCallMetadata]


class DerivationEngine:
    def __init__(self, gateway) -> None:
        self._gateway = gateway

    def derive(
        self,
        available_facts: list[DocumentFact],
        targets: list[DerivationTarget],
        *,
        today: date | None = None,
    ) -> DerivationOutcome:
        # Only ask for targets not already satisfied by an available fact.
        have = {f.key for f in available_facts}
        missing = [t for t in targets if t.key not in have]
        if not missing or not available_facts:
            return DerivationOutcome(facts=[], model_calls=[])

        request = DerivationRequest(
            available=tuple(
                AvailableFact(f.fact_id, f.key, f.value, str(f.value_type)) for f in available_facts
            ),
            targets=tuple(missing),
            today=(today or date.today()).isoformat(),
        )
        try:
            result = self._gateway.derive_facts(request)
        except ModelUnavailable:
            # Deterministic fallback: derive nothing; the field stays a
            # clarification for the user (never guess).
            return DerivationOutcome(facts=[], model_calls=[])
        return DerivationOutcome(facts=result.facts, model_calls=[result.metadata])
