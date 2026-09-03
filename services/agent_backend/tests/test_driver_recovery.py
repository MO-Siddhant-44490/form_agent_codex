"""Driver-level bounded recovery: transient failures recover, validation
errors ask the user, and a stuck field is reported without looping."""

from agent_backend.driver import run_fill
from agent_backend.facts import slice1_facts
from agent_backend.transports.fake import FakeTransport
from form_contracts import RecoveryStrategy, RunOutcome


def test_transient_value_mismatch_recovers_via_retry():
    transport = FakeTransport()
    transport.fields[0].fail_executions = 1  # sticks on the retry
    result = run_fill(transport, slice1_facts())

    assert result.outcome is RunOutcome.COMPLETED
    assert len(result.filled_fields) == 8
    # Exactly one recovery (a RETRY) was needed for the flaky field.
    retries = [d for d in result.recovery_decisions if d.strategy is RecoveryStrategy.RETRY]
    assert len(retries) == 1


def test_validation_error_asks_the_user_without_retrying_the_bad_value():
    transport = FakeTransport()
    transport.fields[1].validation_error = "Invalid email format"  # email field
    result = run_fill(transport, slice1_facts())

    assert result.outcome is RunOutcome.NEEDS_USER
    email_recovery = [d for d in result.recovery_decisions if d.field_id == "email"]
    # Validation error -> ASK_USER immediately, never a blind retry.
    assert [d.strategy for d in email_recovery] == [RecoveryStrategy.ASK_USER]
    assert any(q.field_id == "email" for q in result.questions)
    # Every other field was still filled.
    assert len(result.filled_fields) == 7


def test_recovery_never_loops_and_finishes_the_rest():
    transport = FakeTransport()
    transport.fields[3].fail_executions = 99  # dob never sticks
    result = run_fill(transport, slice1_facts())

    assert result.outcome is RunOutcome.NEEDS_USER
    dob = [d.strategy.value for d in result.recovery_decisions if d.field_id == "dob"]
    assert dob[-1] == "stop"  # terminated, no infinite loop
    assert "dob" not in result.filled_fields
    assert len(result.filled_fields) == 7  # the rest completed
