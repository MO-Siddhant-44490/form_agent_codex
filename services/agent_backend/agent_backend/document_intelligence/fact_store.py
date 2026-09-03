"""Versioned fact store (plan.md §10.4): multiple candidates and conflicts
are preserved, user corrections create a new authoritative version with
audit history retained, and conflicted or low-confidence facts are withheld
from use until resolved (clarification instead of guessing)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from form_contracts import DocumentFact, FactStatus

MIN_USABLE_CONFIDENCE = 0.7


class FactEventKind(StrEnum):
    ADDED = "added"
    CONFLICT_DETECTED = "conflict_detected"
    CORRECTED = "corrected"


@dataclass(frozen=True)
class FactEvent:
    at: datetime
    kind: FactEventKind
    fact_id: str
    key: str
    detail: str


@dataclass
class FactStore:
    _versions: dict[str, list[DocumentFact]] = field(default_factory=dict)
    _events: list[FactEvent] = field(default_factory=list)

    # -- ingestion ---------------------------------------------------------

    def add(self, fact: DocumentFact) -> DocumentFact:
        versions = self._versions.setdefault(fact.key, [])
        stored = fact
        for existing in versions:
            if (
                existing.status in (FactStatus.EXTRACTED, FactStatus.CONFLICTED)
                and existing.value != fact.value
            ):
                # Different extracted values for the same key: mark BOTH
                # conflicted; neither is usable until a human resolves it
                # (never silently pick one — plan.md §7.2).
                stored = fact.model_copy(update={"status": FactStatus.CONFLICTED})
                self._replace(
                    existing, existing.model_copy(update={"status": FactStatus.CONFLICTED})
                )
                self._record(
                    FactEventKind.CONFLICT_DETECTED, stored, f"{existing.value!r} vs {fact.value!r}"
                )
                break
        versions.append(stored)
        self._record(FactEventKind.ADDED, stored, f"value={stored.value!r}")
        return stored

    def add_all(self, facts: list[DocumentFact]) -> None:
        for fact in facts:
            self.add(fact)

    # -- correction --------------------------------------------------------

    def correct(self, key: str, value: str, note: str = "") -> DocumentFact:
        """A user correction supersedes everything for the key — including
        conflicts — and is fully audited (invariant 11: traceable to a user
        correction)."""
        versions = self._versions.setdefault(key, [])
        template = versions[-1] if versions else None
        corrected = DocumentFact(
            fact_id=f"{key}-corrected-{len(versions) + 1}",
            key=key,
            value=value,
            value_type=template.value_type if template else "string",
            confidence=1.0,
            sensitivity=template.sensitivity if template else "personal",
            status=FactStatus.CORRECTED,
        )
        versions.append(corrected)
        self._record(FactEventKind.CORRECTED, corrected, note or f"value={value!r}")
        return corrected

    # -- reads -------------------------------------------------------------

    def active(self) -> dict[str, DocumentFact]:
        """Authoritative fact per key. Corrections win; conflicted or
        low-confidence keys are withheld (they need clarification first)."""
        active: dict[str, DocumentFact] = {}
        for key, versions in self._versions.items():
            corrected = [f for f in versions if f.status is FactStatus.CORRECTED]
            if corrected:
                active[key] = corrected[-1]
                continue
            usable = [
                f
                for f in versions
                if f.status is FactStatus.EXTRACTED and f.confidence >= MIN_USABLE_CONFIDENCE
            ]
            has_conflict = any(f.status is FactStatus.CONFLICTED for f in versions)
            if usable and not has_conflict:
                active[key] = usable[-1]
        return active

    def needing_review(self) -> dict[str, list[DocumentFact]]:
        """Keys blocked on a human: conflicts or low confidence."""
        blocked: dict[str, list[DocumentFact]] = {}
        for key, versions in self._versions.items():
            if any(f.status is FactStatus.CORRECTED for f in versions):
                continue
            conflicted = [f for f in versions if f.status is FactStatus.CONFLICTED]
            low = [
                f
                for f in versions
                if f.status is FactStatus.EXTRACTED and f.confidence < MIN_USABLE_CONFIDENCE
            ]
            if conflicted or (
                low
                and not any(
                    f.status is FactStatus.EXTRACTED and f.confidence >= MIN_USABLE_CONFIDENCE
                    for f in versions
                )
            ):
                blocked[key] = conflicted or low
        return blocked

    def history(self, key: str) -> list[DocumentFact]:
        return list(self._versions.get(key, []))

    def events(self) -> list[FactEvent]:
        return list(self._events)

    # -- internals ---------------------------------------------------------

    def _replace(self, old: DocumentFact, new: DocumentFact) -> None:
        versions = self._versions[old.key]
        versions[versions.index(old)] = new

    def _record(self, kind: FactEventKind, fact: DocumentFact, detail: str) -> None:
        self._events.append(
            FactEvent(
                at=datetime.now(UTC), kind=kind, fact_id=fact.fact_id, key=fact.key, detail=detail
            )
        )
