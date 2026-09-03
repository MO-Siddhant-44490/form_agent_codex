"""Field mapping (plan.md §7.4, Module 7): deterministic candidates first
(exact name match), model assistance only for what remains, and clarification
questions instead of guesses. Model output is validated against the actual
observation and fact set — unknown fields, unknown facts, invented options,
and credential targets are discarded (invariants 2, 5)."""

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Protocol

from form_contracts import (
    DocumentFact,
    FormField,
    ModelCallMetadata,
    PageObservation,
    QuestionKind,
    UserQuestion,
)

from .model_gateway.base import (
    MappingRequest,
    ModelGateway,
    ModelUnavailable,
    mapping_fact_from,
    mapping_field_from,
)
from .planner import KIND_FOR_INPUT_TYPE, desired_checked, match_fact

MIN_MODEL_CONFIDENCE = 0.6


@dataclass(frozen=True)
class Assignment:
    field: FormField
    fact: DocumentFact
    value: str | None  # fact-derived value to apply (None for checkboxes)
    checked: bool | None


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
    return (
        not field.disabled
        and not field.readonly
        and field.visible
        and not field.value_redacted
        and field.input_type in KIND_FOR_INPUT_TYPE
    )


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


def _assign(field: FormField, fact: DocumentFact) -> Assignment | UserQuestion:
    """Derive the applicable value; a needed-but-impossible conversion becomes
    a question, never a guess."""
    if field.input_type == "checkbox":
        return Assignment(field=field, fact=fact, value=None, checked=desired_checked(fact))
    if field.options is not None:
        if fact.value in field.options:
            return Assignment(field=field, fact=fact, value=fact.value, checked=None)
        return _question(
            QuestionKind.AMBIGUOUS_MAPPING,
            field,
            f"Which option matches {fact.key} = {fact.value!r}?",
            fact_keys=[fact.key],
        )
    return Assignment(field=field, fact=fact, value=fact.value, checked=None)


def _assign_with_option(
    field: FormField, fact: DocumentFact, option_value: str | None
) -> Assignment | UserQuestion:
    """Like _assign, but a validated model-selected option may bridge the
    fact-to-option conversion for enumerated controls."""
    direct = _assign(field, fact)
    if isinstance(direct, Assignment):
        return direct
    if option_value is not None and field.options is not None and option_value in field.options:
        return Assignment(field=field, fact=fact, value=option_value, checked=None)
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
    output is re-validated field-by-field before anything is assigned."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    def map(
        self, observation: PageObservation, facts_by_key: dict[str, DocumentFact]
    ) -> MappingOutcome:
        outcome = DeterministicMapper().map(observation, facts_by_key)
        # Retry unresolved fields (questions from missing name matches) via
        # the model; keep conversion questions (those already have a fact).
        unresolved = [
            f
            for f in observation.fields
            if _mappable(f)
            and f.field_id not in outcome.assignments
            and match_fact(f, facts_by_key) is None
        ]
        if not unresolved:
            return outcome

        unused_facts = [
            fact
            for key, fact in facts_by_key.items()
            if key not in {a.fact.key for a in outcome.assignments.values()}
        ]
        if not unused_facts:
            return outcome

        request = MappingRequest(
            fields=tuple(mapping_field_from(f) for f in unresolved),
            facts=tuple(mapping_fact_from(f) for f in unused_facts),
        )
        try:
            result = self._gateway.map_fields(request)
        except ModelUnavailable:
            # Deterministic fallback: abstain — the missing-fact questions
            # from the deterministic pass stand (plan.md §11.2).
            return outcome
        outcome.model_calls.append(result.metadata)

        fields_by_id = {f.field_id: f for f in unresolved}
        for mapping in result.batch.mappings:
            form_field = fields_by_id.get(mapping.field_id)
            if form_field is None:
                continue  # model invented a field: discard
            if mapping.fact_key is None or mapping.needs_clarification:
                continue  # deterministic missing-fact question already stands
            fact = facts_by_key.get(mapping.fact_key)
            if fact is None:
                continue  # model invented a fact: discard
            if mapping.confidence < MIN_MODEL_CONFIDENCE:
                outcome.questions.append(
                    _question(
                        QuestionKind.LOW_CONFIDENCE,
                        form_field,
                        f"Is {form_field.label or form_field.field_id} really "
                        f"{mapping.fact_key}? (model confidence {mapping.confidence:.2f})",
                        fact_keys=[mapping.fact_key],
                    )
                )
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
        return outcome
