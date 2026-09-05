"""Cross-run episodic memory (plan.md insight #3, CoALA `learning`/episodic
memory): remember which fact a field mapped to, keyed by a value-free field
signature, so a repeat encounter with the same site resolves deterministically
and skips the model mapping call.

Safety invariants:
- The signature and everything stored is VALUE-FREE: only a field's identity
  (input type, name attribute, label, role) and the fact KEY it mapped to
  (e.g. "district") — never a user value, never PII, never credentials.
- A recalled mapping is a HINT, re-validated exactly like model output: the
  caller only uses it if the fact key still exists for the current user, and
  the resulting action still passes the policy gate. Memory grants nothing.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from form_contracts import FormField


def site_key(origin: str) -> str:
    """The memory partition for a site. Origin-scoped: forms on the same origin
    share remembered field mappings; a different origin never does."""
    return origin.strip().lower().rstrip("/")


def _norm(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip().lower()


def field_signature(field: FormField) -> str:
    """A stable, value-free identity for a field. Built only from identity
    attributes (input type, name, label/accessible name, role) — never from the
    field's value, current value, or option values, so the signature is stable
    across users and runs and leaks nothing sensitive."""
    parts = (
        _norm(field.input_type),
        _norm(field.target.name_attr),
        _norm(field.label or field.accessible_name),
        _norm(field.target.role),
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RememberedMapping:
    site_key: str
    field_signature: str
    fact_key: str
    input_type: str


class MappingMemory(Protocol):
    """Durable store of field-signature -> fact-key mappings, partitioned by
    site. Implementations must never persist user values (invariant above)."""

    def recall(self, site: str, signature: str) -> str | None:
        """The fact key previously seen to fill this field on this site, if
        any. A hint; the caller re-validates it against the current facts."""
        ...

    def remember(self, site: str, signature: str, fact_key: str, input_type: str) -> None:
        """Record that `fact_key` successfully filled this field on this site.
        Called only after the fill was independently verified."""
        ...


class InMemoryMappingMemory:
    """Process-local mapping memory: the zero-dependency default for tests and
    single-run use. A durable (SQLite-backed) implementation lives in the
    persistence layer."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], RememberedMapping] = {}

    def recall(self, site: str, signature: str) -> str | None:
        hit = self._store.get((site, signature))
        return hit.fact_key if hit else None

    def remember(self, site: str, signature: str, fact_key: str, input_type: str) -> None:
        self._store[(site, signature)] = RememberedMapping(
            site_key=site,
            field_signature=signature,
            fact_key=fact_key,
            input_type=input_type,
        )
