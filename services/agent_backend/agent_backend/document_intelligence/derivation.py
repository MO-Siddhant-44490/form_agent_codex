"""Derivation stage (plan.md §10, the 'infer/calculate indirect values' need):
given already-extracted facts and the values a form needs but does not have,
ask the model to compute them, with a validated derivation trace. Runs only
for genuinely missing targets — never re-derives what was extracted."""

import hashlib
import json
import threading
from collections import OrderedDict
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


CACHE_SIZE = 2000


class DerivationEngine:
    def __init__(self, gateway) -> None:
        self._gateway = gateway
        # Derived value (or "not derivable") per (target, facts, day). A page
        # re-mapped several times must not re-ask the model the same thing.
        self._cache: OrderedDict[str, DocumentFact | None] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(target: DerivationTarget, facts_fp: str, today: str) -> str:
        return f"{target.key}|{target.value_type}|{target.description}|{facts_fp}|{today}"

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

        available = tuple(
            AvailableFact(f.fact_id, f.key, f.value, str(f.value_type)) for f in available_facts
        )
        day = (today or date.today()).isoformat()
        facts_fp = hashlib.sha256(
            json.dumps([a.__dict__ for a in available], sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

        cached: list[DocumentFact] = []
        ask: list[DerivationTarget] = []
        with self._lock:
            for t in missing:
                k = self._key(t, facts_fp, day)
                if k in self._cache:
                    self._cache.move_to_end(k)
                    if self._cache[k] is not None:
                        cached.append(self._cache[k])
                else:
                    ask.append(t)
        if not ask:
            return DerivationOutcome(facts=cached, model_calls=[])

        request = DerivationRequest(available=available, targets=tuple(ask), today=day)
        try:
            result = self._gateway.derive_facts(request)
        except ModelUnavailable:
            # Deterministic fallback: derive nothing; the field stays a
            # clarification for the user (never guess). Not cached.
            return DerivationOutcome(facts=cached, model_calls=[])
        by_key = {f.key: f for f in result.facts}
        with self._lock:
            for t in ask:
                self._cache[self._key(t, facts_fp, day)] = by_key.get(t.key)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return DerivationOutcome(facts=cached + result.facts, model_calls=[result.metadata])
