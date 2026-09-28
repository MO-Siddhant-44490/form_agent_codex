"""Multi-page fill: the driver fills each page, navigates NEXT, and completes
on the final page — no loops, no skipped fields (Module 11 acceptance)."""

from agent_backend.driver import DriverBudgets, run_fill
from agent_backend.facts import slice1_facts
from agent_backend.transports.fake import FakeField, FakeTransport
from form_contracts import ActionKind, RunOutcome


def three_page_form():
    return [
        [
            FakeField("full-name", "text", "full_name", "Full name", required=True),
            FakeField("email", "email", "email", "Email", required=True),
        ],
        [
            FakeField("dob", "date", "date_of_birth", "Date of birth", required=True),
            FakeField(
                "country",
                "select-one",
                "country",
                "Country",
                required=True,
                options=["", "IN", "US"],
            ),
        ],
        [
            FakeField("experience", "number", "years_experience", "Experience", required=True),
            FakeField("subscribe", "checkbox", "subscribe", "Subscribe"),
        ],
    ]


def test_fills_all_pages_and_navigates():
    transport = FakeTransport(pages=three_page_form())
    result = run_fill(transport, slice1_facts())

    assert result.outcome is RunOutcome.COMPLETED, result.detail
    # 6 fields across 3 pages all filled and verified.
    assert len(result.filled_fields) == 6
    # Two NAVIGATE_NEXT actions between the three pages.
    assert transport.received_kinds.count(ActionKind.NAVIGATE_NEXT) == 2
    # Reached the last page.
    assert transport.current_page == 2
    # Never submitted (invariant 1).
    assert ActionKind.SUBMIT not in transport.received_kinds
    # Every page's values landed.
    all_values = {f.name: f.value for page in transport.pages for f in page}
    assert all_values["full_name"] == "Ada Lovelace"
    assert all_values["date_of_birth"] == "1998-04-17"
    assert all_values["years_experience"] == "5"


def test_page_budget_terminates_cleanly():
    transport = FakeTransport(pages=three_page_form())
    result = run_fill(transport, slice1_facts(), DriverBudgets(max_pages=1))
    assert result.outcome is RunOutcome.BUDGET_EXHAUSTED
    assert "page budget" in result.detail


def test_missing_fact_on_a_later_page_reports_after_filling_the_rest():
    facts = [f for f in slice1_facts() if f.key != "years_experience"]
    transport = FakeTransport(pages=three_page_form())
    result = run_fill(transport, facts)
    # Pages 1-2 filled; page 3's required experience has no fact.
    assert result.outcome is RunOutcome.NEEDS_USER
    assert any(q.field_id == "experience" for q in result.questions)
    # It still navigated to the last page and filled what it could there.
    assert transport.current_page == 2
    filled_names = {f.name for page in transport.pages for f in page if f.value or f.checked}
    assert "full_name" in filled_names and "date_of_birth" in filled_names


class SlowPageLoad:
    """A 'Next' that triggers a full page load: the observation returned right
    after the click, and the next few observes, still show the OLD page (the
    new document has not arrived yet), then the new page appears."""

    def __init__(self, inner: FakeTransport, stale_observes: int):
        self.inner = inner
        self.stale_observes = stale_observes
        self._stale = None
        self._remaining = 0

    def attach(self):
        return self.inner.attach()

    def observe(self):
        if self._remaining > 0:
            self._remaining -= 1
            return self._stale
        return self.inner.observe()

    def execute(self, action):
        if action.kind is ActionKind.NAVIGATE_NEXT:
            before = self.inner.observe()
            outcome = self.inner.execute(action)
            self._stale, self._remaining = before, self.stale_observes
            return (
                outcome.model_copy(update={"observation": before})
                if hasattr(outcome, "model_copy")
                else type(outcome)(
                    result=outcome.result, observation=before, verification=outcome.verification
                )
            )
        return self.inner.execute(action)


def test_waits_for_a_slow_page_load_after_next():
    transport = SlowPageLoad(FakeTransport(pages=three_page_form()), stale_observes=3)
    result = run_fill(transport, slice1_facts(), budgets=DriverBudgets(navigation_wait_s=0))
    assert result.outcome is RunOutcome.COMPLETED, result.detail
    assert len(result.filled_fields) == 6  # every page reached despite the lag


def test_next_that_never_advances_is_reported_not_completed():
    # Page load never lands within the budget: more form remains, so the
    # outcome must say so instead of "all fields filled".
    transport = SlowPageLoad(FakeTransport(pages=three_page_form()), stale_observes=100)
    result = run_fill(
        transport,
        slice1_facts(),
        budgets=DriverBudgets(navigation_wait_s=0, max_navigation_waits=3),
    )
    assert result.outcome is RunOutcome.NEEDS_USER
    assert "navigation did not advance" in (result.detail or "")
    assert set(result.filled_fields) == {"full-name", "email"}
