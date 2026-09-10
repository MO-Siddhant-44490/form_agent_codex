"""Final validation agent (plan.md §6 FinalReview, §7.7 verification). After
the fill loop, re-observe the whole form and collect every remaining problem —
lingering validation errors, required fields still empty, dropdowns that did
not take — so nothing slips through silently. This catches form-level issues a
per-field check cannot (e.g. "address must be <= 50 characters" that appears
only after filling)."""

from dataclasses import dataclass, field
from enum import StrEnum

from form_contracts import PageObservation

from .planner import HUMAN_ONLY_PURPOSES


class IssueKind(StrEnum):
    VALIDATION_ERROR = "validation_error"  # the site rejects the current value
    REQUIRED_EMPTY = "required_empty"  # a required field has no value
    UNVERIFIED = "unverified"  # a field we tried to fill did not take


@dataclass
class ValidationIssue:
    field_id: str
    kind: IssueKind
    label: str | None
    detail: str


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def by_kind(self, kind: IssueKind) -> list[ValidationIssue]:
        return [i for i in self.issues if i.kind is kind]


def validate_form(observation: PageObservation, expected_filled: set[str]) -> ValidationReport:
    """Scan a fresh observation for problems.

    `expected_filled` are field_ids the driver believed it filled — a field
    here that is empty or shows an error is flagged as unverified/erroring."""
    report = ValidationReport()
    for f in observation.fields:
        # A lingering validation message on any field is a hard problem.
        if f.validation_message:
            report.issues.append(
                ValidationIssue(
                    field_id=f.field_id,
                    kind=IssueKind.VALIDATION_ERROR,
                    label=f.label,
                    detail=f.validation_message,
                )
            )
            continue
        if f.purpose in HUMAN_ONLY_PURPOSES:
            continue  # the human's to complete; reported via the snapshot, not as a defect
        has_value = f.current_value not in (None, "") or f.checked or bool(f.value_length)
        if f.required and not has_value:
            kind = (
                IssueKind.UNVERIFIED if f.field_id in expected_filled else IssueKind.REQUIRED_EMPTY
            )
            report.issues.append(
                ValidationIssue(
                    field_id=f.field_id,
                    kind=kind,
                    label=f.label,
                    detail=(
                        "field we filled did not retain a value"
                        if kind is IssueKind.UNVERIFIED
                        else "required field has no value"
                    ),
                )
            )
    return report
