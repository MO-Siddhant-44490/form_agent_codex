"""Driver dismisses a blocking dialog before filling, and never loops on a
persistent one (Module 11)."""

from agent_backend.driver import run_fill
from agent_backend.facts import slice1_facts
from agent_backend.transports.fake import FakeTransport
from form_contracts import ActionKind, RunOutcome


def test_dismisses_dialog_then_fills():
    transport = FakeTransport(dialog_present=True)
    result = run_fill(transport, slice1_facts())

    assert result.outcome is RunOutcome.COMPLETED
    assert transport.dialog_dismissed_count == 1
    assert ActionKind.DISMISS_DIALOG in transport.received_kinds
    assert len(result.filled_fields) == 8  # form filled after dismissal


def test_persistent_dialog_does_not_loop():
    # A dialog that re-appears after dismissal: bounded to 2 attempts, then the
    # driver proceeds instead of looping forever.
    transport = FakeTransport(dialog_present=True)
    original = transport._dialogs

    def sticky():
        transport.dialog_present = True  # re-arm before each observation
        return original()

    transport._dialogs = sticky
    result = run_fill(transport, slice1_facts())

    # Two dismissal attempts, then it stops trying (no infinite loop).
    assert transport.received_kinds.count(ActionKind.DISMISS_DIALOG) == 2
    assert result.outcome in (RunOutcome.COMPLETED, RunOutcome.NEEDS_USER)
