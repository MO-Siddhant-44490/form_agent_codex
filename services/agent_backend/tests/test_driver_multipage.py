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
