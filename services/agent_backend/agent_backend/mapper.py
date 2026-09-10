"""Field mapping (plan.md §7.4, Module 7): deterministic candidates first
(exact name match), model assistance only for what remains, and clarification
questions instead of guesses. Model output is validated against the actual
observation and fact set — unknown fields, unknown facts, invented options,
and credential targets are discarded (invariants 2, 5)."""

from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum
from typing import Protocol

from form_contracts import (
    DocumentFact,
    FieldPurpose,
    FormField,
    ModelCallMetadata,
    PageObservation,
    QuestionKind,
    Sensitivity,
    UserQuestion,
)

from .grounding import autocomplete_fact
from .memory import MappingMemory, field_signature, site_key
from .model_gateway.base import (
    DerivationTarget,
    MappingRequest,
    ModelGateway,
    ModelUnavailable,
    mapping_fact_from,
    mapping_field_from,
)
from .planner import HUMAN_ONLY_PURPOSES, KIND_FOR_INPUT_TYPE, desired_checked, match_fact

# Form input types -> the value_type a derived value should take.
_DERIVED_VALUE_TYPE = {
    "number": "number",
    "date": "date",
    "email": "email",
    "tel": "phone",
}


def _derivation_key(field: FormField) -> str:
    import re

    label = field.label or field.accessible_name or field.field_id
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or field.field_id


def _derivation_description(field: FormField) -> str:
    parts = [field.label or field.accessible_name or field.field_id]
    if field.nearby_text and field.nearby_text not in parts[0]:
        parts.append(field.nearby_text)
    return " — ".join(parts)


MIN_MODEL_CONFIDENCE = 0.6


class MappingSource(StrEnum):
    """How a field->fact binding was produced — its trust provenance. NAME_MATCH
    and AUTOCOMPLETE derive from signals an untrusted page cannot forge without
    also being the legitimately-correct field; MODEL and MEMORY are influenced by
    (or recalled from) untrusted page content and are lower trust."""

    NAME_MATCH = "name_match"
    AUTOCOMPLETE = "autocomplete"
    DERIVATION = "derivation"
    MEMORY = "memory"
    MODEL = "model"
    USER = "user"  # the user said "use this fact for that field": highest trust


# Bindings whose field identity came from untrusted page content: not trusted to
# route a SENSITIVE value without human confirmation (indirect-injection guard).
_LOW_TRUST_SOURCES = frozenset({MappingSource.MODEL, MappingSource.MEMORY})


@dataclass(frozen=True)
class Assignment:
    field: FormField
    fact: DocumentFact
    value: str | None  # fact-derived value to apply (None for checkboxes)
    checked: bool | None
    source: MappingSource = MappingSource.NAME_MATCH


@dataclass
class MappingOutcome:
    assignments: dict[str, Assignment] = dc_field(default_factory=dict)
    questions: list[UserQuestion] = dc_field(default_factory=list)
    model_calls: list[ModelCallMetadata] = dc_field(default_factory=list)

    def approved_values(self) -> dict[str, str | None]:
        return {field_id: a.value for field_id, a in self.assignments.items()}


class Mapper(Protocol):
    def map(
        self, observation: PageObservation, facts_by_key: dict[str, DocumentFact]
    ) -> MappingOutcome: ...


def _mappable(field: FormField) -> bool:
    """Fillable at all: credentials and captchas are the human's (invariant 2).
    A masked identifier (redacted value, STANDARD purpose) IS mappable."""
    return (
        not field.disabled
        and not field.readonly
        and field.visible
        and field.purpose not in HUMAN_ONLY_PURPOSES
        and field.input_type in KIND_FOR_INPUT_TYPE
    )


def _inferable(field: FormField) -> bool:
    """May be bound by an INDIRECT signal (autocomplete, memory, the model). A
    consent/declaration control is not: it is only ever set on the user's
    explicit say-so — a fact keyed to that very field."""
    return _mappable(field) and field.purpose is not FieldPurpose.CONSENT


def _question(
    kind: QuestionKind, field: FormField, prompt: str, fact_keys: list[str] | None = None
) -> UserQuestion:
    return UserQuestion(
        question_id=f"q-{field.field_id}-{kind}",
        kind=kind,
        prompt=prompt,
        field_id=field.field_id,
        fact_keys=fact_keys or [],
        options=field.options,
    )


# Placeholder option values (not real choices) that should be ignored when
# matching a fact to a dropdown.
_PLACEHOLDER_OPTION = ("", "select", "--", "choose", "first")


def match_option(value: str, options: list[str], labels: list[str] | None = None) -> str | None:
    """Match a fact value to a dropdown option and return the option VALUE the
    executor must select. Matches exact, then case-insensitive/trimmed, against
    the option values and — when provided — the human labels (so a name fact
    "Maharashtra" maps to the code option "MH"). Deliberately conservative: no
    loose substring match (that would pick "IN" for "India")."""
    if value in options:
        return value
    v = value.strip().lower()
    for option in options:
        if option and option.strip().lower() == v:
            return option
    if labels:
        for i, label in enumerate(labels):
            if label and label.strip().lower() == v and i < len(options):
                return options[i]
    return None


def _assign(
    field: FormField, fact: DocumentFact, source: MappingSource = MappingSource.NAME_MATCH
) -> Assignment | UserQuestion:
    """Derive the applicable value; a needed-but-impossible conversion becomes
    a question, never a guess. `source` records how the binding was produced."""
    if field.input_type == "checkbox":
        return Assignment(
            field=field, fact=fact, value=None, checked=desired_checked(fact), source=source
        )
    if field.options:
        matched = match_option(fact.value, field.options, field.option_labels)
        if matched is not None:
            return Assignment(field=field, fact=fact, value=matched, checked=None, source=source)
        return _question(
            QuestionKind.AMBIGUOUS_MAPPING,
            field,
            f"Which option matches {fact.key} = {fact.value!r}?",
            fact_keys=[fact.key],
        )
    # A combobox with no options discovered yet (e.g. a cascading dropdown whose
    # choices load only when opened): assign optimistically with the fact value;
    # the executor opens it and matches an option at click time, and recovery
    # handles it if none is found.
    return Assignment(field=field, fact=fact, value=fact.value, checked=None, source=source)


def user_binding(field: FormField, fact: DocumentFact) -> Assignment | UserQuestion:
    """An explicit, user-stated binding ("use aID for Aadhaar Number"): the
    highest-trust source. Still derives the value through _assign, so an option
    that does not exist becomes a question, never a guess."""
    return _assign(field, fact, source=MappingSource.USER)


def _assign_with_option(
    field: FormField,
    fact: DocumentFact,
    option_value: str | None,
    source: MappingSource = MappingSource.MODEL,
) -> Assignment | UserQuestion:
    """Like _assign, but a validated model-selected option may bridge the
    fact-to-option conversion for enumerated controls."""
    direct = _assign(field, fact, source)
    if isinstance(direct, Assignment):
        return direct
    if option_value is not None and field.options is not None and option_value in field.options:
        return Assignment(field=field, fact=fact, value=option_value, checked=None, source=source)
    return direct


class DeterministicMapper:
    """Slice 1/2 behavior: exact name-attribute matching only."""

    def map(
        self, observation: PageObservation, facts_by_key: dict[str, DocumentFact]
    ) -> MappingOutcome:
        outcome = MappingOutcome()
        for form_field in observation.fields:
            if not _mappable(form_field):
                continue
            fact = match_fact(form_field, facts_by_key)
            if fact is None:
                if form_field.required:
                    outcome.questions.append(
                        _question(
                            QuestionKind.MISSING_FACT,
                            form_field,
                            f"No fact for required field {form_field.label or form_field.field_id}",
                        )
                    )
                continue
            assigned = _assign(form_field, fact)
            if isinstance(assigned, Assignment):
                outcome.assignments[form_field.field_id] = assigned
            else:
                outcome.questions.append(assigned)
        return outcome


class ModelAssistedMapper:
    """Deterministic first; the model ranks only the leftovers, and its
    output is re-validated field-by-field before anything is assigned. When a
    derivation engine is provided, required fields still unmapped after the
    model pass are batched and computed from available facts (age from DOB,
    totals from line items, ...) before falling back to clarification."""

    def __init__(
        self,
        gateway: ModelGateway,
        derivation_engine=None,
        memory: MappingMemory | None = None,
    ) -> None:
        self._gateway = gateway
        self._derivation = derivation_engine
        self._memory = memory

    def map(
        self, observation: PageObservation, facts_by_key: dict[str, DocumentFact]
    ) -> MappingOutcome:
        outcome = DeterministicMapper().map(observation, facts_by_key)
        # Retry unresolved fields (questions from missing name matches) via
        # the model; keep conversion questions (those already have a fact).
        unresolved = [
            f
            for f in observation.fields
            if _inferable(f)
            and f.field_id not in outcome.assignments
            and match_fact(f, facts_by_key) is None
        ]
        if not unresolved:
            return self._guard_sensitive(outcome)

        # Structural grounding (plan.md insight #2): the autocomplete attribute
        # is a W3C-standard, value-free signal that binds a field to a fact more
        # reliably than a scraped label — resolve it deterministically before
        # spending a model call.
        unresolved = self._autocomplete_ground(facts_by_key, unresolved, outcome)
        if not unresolved:
            self._derive_missing(observation, facts_by_key, outcome)
            return self._guard_sensitive(outcome)

        # Episodic memory (plan.md insight #3): a field this site mapped before
        # resolves deterministically here — no model call. Recalled mappings are
        # re-validated (the fact must still exist and _assign must accept it), so
        # memory is a hint, never authority.
        if self._memory is not None:
            unresolved = self._recall(observation, facts_by_key, unresolved, outcome)
            if not unresolved:
                self._derive_missing(observation, facts_by_key, outcome)
                return self._guard_sensitive(outcome)

        unused_facts = [
            fact
            for key, fact in facts_by_key.items()
            if key not in {a.fact.key for a in outcome.assignments.values()}
        ]
        # The model mapping pass runs only when there are leftover facts to map;
        # derivation (below) still runs regardless, so a field whose value must
        # be computed from already-used facts (age from a mapped DOB) is reached.
        if unused_facts:
            self._model_map(observation, facts_by_key, unresolved, unused_facts, outcome)

        self._derive_missing(observation, facts_by_key, outcome)
        return self._guard_sensitive(outcome)

    def _guard_sensitive(self, outcome: MappingOutcome) -> MappingOutcome:
        """Indirect-injection / exfiltration guard (plan.md insight #4, CaMeL):
        a SENSITIVE fact bound by an untrusted, page-derived signal (the model,
        or possibly-poisoned memory) is NOT auto-filled. Withdraw the assignment
        and ask the user to confirm the field before the value flows there.
        High-trust bindings (exact name, standard autocomplete) are unaffected,
        and PERSONAL/PUBLIC facts still auto-fill."""
        for field_id in list(outcome.assignments):
            assignment = outcome.assignments[field_id]
            if (
                assignment.fact.sensitivity is Sensitivity.SENSITIVE
                and assignment.source in _LOW_TRUST_SOURCES
            ):
                del outcome.assignments[field_id]
                if all(q.field_id != field_id for q in outcome.questions):
                    outcome.questions.append(
                        _question(
                            QuestionKind.SENSITIVE_MAPPING,
                            assignment.field,
                            f"Confirm before filling the sensitive value "
                            f"'{assignment.fact.key}' into "
                            f"'{assignment.field.label or field_id}' "
                            f"(matched by {assignment.source.value}, not a trusted signal).",
                            fact_keys=[assignment.fact.key],
                        )
                    )
        return outcome

    def _autocomplete_ground(
        self,
        facts_by_key: dict[str, DocumentFact],
        unresolved: list[FormField],
        outcome: MappingOutcome,
    ) -> list[FormField]:
        """Assign fields whose autocomplete token grounds to a fact, re-validated
        through _assign. Returns the fields still unresolved."""
        still: list[FormField] = []
        for field in unresolved:
            fact = autocomplete_fact(field, facts_by_key)
            if fact is None:
                still.append(field)
                continue
            assigned = _assign(field, fact, MappingSource.AUTOCOMPLETE)
            if isinstance(assigned, Assignment):
                outcome.assignments[field.field_id] = assigned
                outcome.questions = [q for q in outcome.questions if q.field_id != field.field_id]
            else:
                still.append(field)
        return still

    def _recall(
        self,
        observation: PageObservation,
        facts_by_key: dict[str, DocumentFact],
        unresolved: list[FormField],
        outcome: MappingOutcome,
    ) -> list[FormField]:
        """Assign fields whose signature this site remembered, re-validated
        against the current facts. Returns the fields still unresolved (which
        go on to the model). Never assigns from memory alone — a remembered fact
        that no longer exists, or does not cleanly assign, is left to the model
        or to a clarification question."""
        assert self._memory is not None
        site = site_key(observation.origin)
        still: list[FormField] = []
        for field in unresolved:
            fact_key = self._memory.recall(site, field_signature(field))
            fact = facts_by_key.get(fact_key) if fact_key else None
            if fact is None:
                still.append(field)
                continue
            assigned = _assign(field, fact, MappingSource.MEMORY)
            if isinstance(assigned, Assignment):
                outcome.assignments[field.field_id] = assigned
                outcome.questions = [q for q in outcome.questions if q.field_id != field.field_id]
            else:
                still.append(field)
        return still

    def _model_map(
        self,
        observation: PageObservation,
        facts_by_key: dict[str, DocumentFact],
        unresolved: list[FormField],
        unused_facts: list[DocumentFact],
        outcome: MappingOutcome,
    ) -> None:
        request = MappingRequest(
            fields=tuple(mapping_field_from(f) for f in unresolved),
            facts=tuple(mapping_fact_from(f) for f in unused_facts),
        )
        try:
            result = self._gateway.map_fields(request)
        except ModelUnavailable:
            # Deterministic fallback: abstain — the missing-fact questions
            # from the deterministic pass stand (plan.md §11.2).
            return
        outcome.model_calls.append(result.metadata)

        fields_by_id = {f.field_id: f for f in unresolved}
        for mapping in result.batch.mappings:
            form_field = fields_by_id.get(mapping.field_id)
            if form_field is None:
                continue  # model invented a field: discard
            if mapping.fact_key is None:
                continue  # deterministic missing-fact question already stands
            fact = facts_by_key.get(mapping.fact_key)
            if fact is None:
                continue  # model invented a fact: discard
            if mapping.needs_clarification or mapping.confidence < MIN_MODEL_CONFIDENCE:
                # A plausible-but-uncertain candidate (an abbreviation or
                # synonym — "aID" for "Aadhaar Number") is put to the user as a
                # yes/no, replacing the bare "no fact" question: inferring and
                # asking beats silently leaving the field.
                self._propose(outcome, form_field, fact)
                continue
            assigned = _assign_with_option(form_field, fact, mapping.selected_option_value)
            if isinstance(assigned, Assignment):
                outcome.assignments[form_field.field_id] = assigned
                # A model mapping resolves the deterministic missing-fact
                # question for this field, if one was raised.
                outcome.questions = [
                    q for q in outcome.questions if q.field_id != form_field.field_id
                ]
            else:
                outcome.questions.append(assigned)

        self._derive_missing(observation, facts_by_key, outcome)
        return outcome

    @staticmethod
    def _propose(outcome: MappingOutcome, form_field: FormField, fact: DocumentFact) -> None:
        label = form_field.label or form_field.accessible_name or form_field.field_id
        shown = fact.value if fact.sensitivity is Sensitivity.PUBLIC else "(value hidden)"
        outcome.questions = [q for q in outcome.questions if q.field_id != form_field.field_id]
        outcome.questions.append(
            _question(
                QuestionKind.LOW_CONFIDENCE,
                form_field,
                f"I didn't find an exact match for '{label}', but you have "
                f"'{fact.key}' = {shown}. Use it for this field?",
                fact_keys=[fact.key],
            )
        )

    def _derive_missing(
        self,
        observation: PageObservation,
        facts_by_key: dict[str, DocumentFact],
        outcome: MappingOutcome,
    ) -> None:
        """For required fields still unmapped after deterministic + model
        passes, ask the derivation engine to compute a value from available
        facts. A field whose value can be derived is filled and its
        clarification question withdrawn; the uncomputable remainder stays a
        question (never guessed)."""
        if self._derivation is None or not facts_by_key:
            return
        pending = [
            f
            for f in observation.fields
            if _mappable(f)
            and f.field_id not in outcome.assignments
            and f.required
            and match_fact(f, facts_by_key) is None
        ]
        if not pending:
            return

        # One batched derivation call: distinct target key -> field(s).
        targets: dict[str, DerivationTarget] = {}
        fields_for_key: dict[str, list[FormField]] = {}
        for field in pending:
            key = _derivation_key(field)
            fields_for_key.setdefault(key, []).append(field)
            targets.setdefault(
                key,
                DerivationTarget(
                    key=key,
                    value_type=_DERIVED_VALUE_TYPE.get(field.input_type, "string"),
                    description=_derivation_description(field),
                ),
            )

        result = self._derivation.derive(list(facts_by_key.values()), list(targets.values()))
        outcome.model_calls.extend(result.model_calls)
        for derived in result.facts:
            for field in fields_for_key.get(derived.key, []):
                assigned = _assign(field, derived, MappingSource.DERIVATION)
                if isinstance(assigned, Assignment):
                    outcome.assignments[field.field_id] = assigned
                    outcome.questions = [
                        q for q in outcome.questions if q.field_id != field.field_id
                    ]
