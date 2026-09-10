"""The user's profile as a list of simple {key, value[, sensitivity]} items —
the shape the side panel edits and sends — and the pure operations on it:
building typed facts from it, upserting corrections, and merging a document's
extracted facts with conflict detection. No I/O, no model calls."""

from collections.abc import Iterable
from typing import Protocol

from form_contracts import DocumentFact, FactStatus, FactValueType, Sensitivity

# Non-sensitive geography keys; everything else defaults to personal.
PUBLIC_KEYS = frozenset({"country", "state", "district", "locality", "pincode", "gender"})


def sensitivity_for(key: str) -> str:
    return "public" if key in PUBLIC_KEYS else "personal"


class HasKeyValue(Protocol):
    key: str
    value: str


def facts_from_payload(items: list[dict]) -> list[DocumentFact]:
    """Typed, user-provided facts from the panel's {key, value} list. Items
    without a key or value are skipped."""
    facts: list[DocumentFact] = []
    for i, item in enumerate(items):
        key = item.get("key")
        value = item.get("value")
        if not key or value is None:
            continue
        facts.append(
            DocumentFact(
                fact_id=f"user-{key}-{i}",
                key=key,
                value=str(value),
                value_type=FactValueType(item.get("value_type", "string")),
                confidence=1.0,
                sensitivity=Sensitivity(item.get("sensitivity", "personal")),
                status=FactStatus.USER_PROVIDED,
            )
        )
    return facts


def upsert_facts(fact_items: list[dict], updates: Iterable[dict]) -> list[dict]:
    """Apply {key, value} updates to a profile: existing keys are overwritten,
    new keys appended (with a sensitivity inferred from the key)."""
    by_key = {f["key"]: dict(f) for f in fact_items if f.get("key")}
    for update in updates:
        key = update["key"]
        if key in by_key:
            by_key[key]["value"] = update["value"]
        else:
            by_key[key] = {
                "key": key,
                "value": update["value"],
                "sensitivity": sensitivity_for(key),
            }
    return list(by_key.values())


def merge_document(
    current: list[dict], extracted: Iterable[HasKeyValue]
) -> tuple[list[dict], list[dict]]:
    """Merge a document's extracted facts into the current profile.

    - a new key is ADDED
    - an identical value is redundant (deduped)
    - a DIFFERENT value for an existing key is a CONFLICT the user must resolve —
      the existing value is kept until they do; nothing is silently overwritten.

    Returns (merged_fields, conflicts) where a conflict is
    {key, existing, incoming}."""
    values: dict[str, str] = {}
    order: list[str] = []
    for item in current:
        key = item.get("key")
        if key and key not in values:
            values[key] = item.get("value")
            order.append(key)

    conflicts: list[dict] = []
    for fact in extracted:
        key, value = fact.key, fact.value
        if key not in values:
            values[key] = value
            order.append(key)
        elif str(values[key]).strip() == str(value).strip():
            continue  # same value from another document
        elif not any(c["key"] == key for c in conflicts):
            conflicts.append({"key": key, "existing": values[key], "incoming": value})

    fields = [{"key": k, "value": values[k]} for k in order]
    return fields, conflicts
