"""Final validation agent: catches form-level problems after filling."""

from agent_backend.driver import run_fill
from agent_backend.facts import slice1_facts
from agent_backend.transports.fake import FakeTransport
from agent_backend.validation import IssueKind, validate_form
from form_contracts import RunOutcome


def test_validate_form_flags_validation_errors_and_empty_required():
    obs = FakeTransport().observe()
    # Nothing filled yet: required fields are empty.
    report = validate_form(obs, expected_filled=set())
    empties = report.by_kind(IssueKind.REQUIRED_EMPTY)
    assert {"full-name", "email", "dob"} <= {i.field_id for i in empties}


def test_driver_final_pass_catches_a_lingering_validation_error():
    # The address (email field here) keeps a validation error even after being
    # filled — a per-field verify might pass on value, the final pass catches it.
    transport = FakeTransport()
    transport.fields[2].validation_error = None  # phone ok
    transport.fields[1].validation_error = "Invalid email"  # email
    result = run_fill(transport, slice1_facts())
    assert result.outcome is RunOutcome.NEEDS_USER
    # The validation report names the erroring field.
    errs = result.validation.by_kind(IssueKind.VALIDATION_ERROR)
    assert any(i.field_id == "email" for i in errs)


def test_clean_form_validates_ok_and_completes():
    result = run_fill(FakeTransport(), slice1_facts())
    assert result.outcome is RunOutcome.COMPLETED
    assert result.validation.ok
    assert "validated" in result.detail
