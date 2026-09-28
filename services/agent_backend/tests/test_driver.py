"""Driver loop: completion, retries, budgets, takeover, and the no-submit
invariant — all against the guard-faithful FakeTransport."""

from agent_backend.driver import DriverBudgets, run_fill
from agent_backend.facts import slice1_facts
from agent_backend.transports.fake import FakeTransport
from form_contracts import ActionKind, RunOutcome, VerificationStatus


def test_fills_all_fields_and_completes_without_submitting():
    transport = FakeTransport()
    result = run_fill(transport, slice1_facts())

    assert result.outcome is RunOutcome.COMPLETED
    assert len(result.filled_fields) == 8
    assert all(v.status is VerificationStatus.SUCCESS for v in result.verifications)
    # Every action was verified: one verification per filled field.
    assert len(result.verifications) == len(result.filled_fields)
    # The transport never saw a SUBMIT (invariant 1) — the fake would raise,
    # but assert explicitly for the report.
    assert ActionKind.SUBMIT not in transport.received_kinds
    # DOM-equivalent ground truth on the fake form.
    values = {
        f.name: (f.checked if f.input_type in ("checkbox",) else f.value) for f in transport.fields
    }
    assert values == {
        "full_name": "Ada Lovelace",
        "email": "ada@example.test",
        "phone": "+1 555 010 2030",
        "date_of_birth": "1998-04-17",
        "country": "IN",
        "years_experience": "5",
        "contact_method": "email",
        "subscribe": True,
    }


def test_flaky_field_is_retried_then_succeeds():
    transport = FakeTransport()
    transport.fields[1].fail_executions = 1  # email fails once, then sticks
    result = run_fill(transport, slice1_facts())

    assert result.outcome is RunOutcome.COMPLETED
    assert len(result.filled_fields) == 8
    failures = [v for v in result.verifications if v.status is VerificationStatus.RETRYABLE_FAILURE]
    assert len(failures) == 1


def test_permanently_failing_field_recovers_to_a_classified_block():
    # A value that never sticks walks the value_mismatch ladder (retry ->
    # reobserve -> stop); the field is then blocked and reported, and the run
    # ends NEEDS_USER rather than looping (bounded recovery, plan.md §7.8).
    transport = FakeTransport()
    transport.fields[0].fail_executions = 99
    result = run_fill(transport, slice1_facts())

    assert result.outcome is RunOutcome.NEEDS_USER
    # The other fields were still filled.
    assert len(result.filled_fields) == 7
    # Recovery was attempted and terminated in STOP for the bad field.
    strategies = [d.strategy.value for d in result.recovery_decisions if d.field_id == "full-name"]
    assert strategies == ["retry", "reobserve", "stop"]
    assert any(q.field_id == "full-name" for q in result.questions)


def test_step_budget_forces_classified_termination():
    transport = FakeTransport()
    transport.fields[0].fail_executions = 99
    result = run_fill(
        transport,
        slice1_facts(),
        DriverBudgets(max_steps=2, max_retries_per_action=99),
    )
    assert result.outcome is RunOutcome.BUDGET_EXHAUSTED
    assert result.steps_used == 2


def test_login_page_requests_human_takeover_before_any_action():
    transport = FakeTransport(login_page=True)
    result = run_fill(transport, slice1_facts())

    assert result.outcome is RunOutcome.NEEDS_USER
    assert transport.received_kinds == []  # nothing executed on a login wall


def test_missing_fact_for_required_field_needs_user():
    facts = [f for f in slice1_facts() if f.key != "email"]
    transport = FakeTransport()
    result = run_fill(transport, facts)

    assert result.outcome is RunOutcome.NEEDS_USER
    assert result.unmapped_required == ["email"]
    # Everything mappable was still filled before asking (finish what you can).
    assert len(result.filled_fields) == 7


def test_undriveable_field_is_blocked_not_fatal():
    # A field whose execution FAILS (e.g. an undriveable custom widget) blocks
    # that field and the run finishes NEEDS_USER after filling the rest —
    # rather than crashing the whole run.

    class FailingField(FakeTransport):
        def execute(self, action):
            if action.target and action.target.field_id == "full-name":
                from agent_backend.transport import ExecuteOutcome
                from agent_backend.transports.fake import ActionResultStatus

                return ExecuteOutcome(
                    result=self._result(action, ActionResultStatus.FAILED, error="widget"),
                    verification=None,
                    observation=None,
                )
            return super().execute(action)

    transport = FailingField()
    result = run_fill(transport, slice1_facts())
    assert result.outcome is RunOutcome.NEEDS_USER
    assert any(q.field_id == "full-name" for q in result.questions)
    assert len(result.filled_fields) == 7  # everything else filled


def test_finish_reports_only_open_questions_one_per_field():
    from agent_backend.driver import _current_questions
    from form_contracts import QuestionKind, UserQuestion

    def q(fid, kind):
        return UserQuestion(question_id=f"q-{fid}-{kind}", kind=kind, prompt="?", field_id=fid)

    qs = [
        q("med", QuestionKind.MISSING_FACT),
        q("med", QuestionKind.AMBIGUOUS_MAPPING),  # med was filled later
        q("phys", QuestionKind.MISSING_FACT),
        q("phys", QuestionKind.AMBIGUOUS_MAPPING),
        q("lang", QuestionKind.AMBIGUOUS_MAPPING),
        q("lang", QuestionKind.LOW_CONFIDENCE),
    ]
    out = _current_questions(qs, filled={"med"})
    assert [(x.field_id, x.kind) for x in out] == [
        ("phys", QuestionKind.AMBIGUOUS_MAPPING),
        ("lang", QuestionKind.LOW_CONFIDENCE),
    ]
