"""Recovery planner ladders and driver-level bounded recovery (plan.md §7.8)."""

from agent_backend.recovery import MAX_RECOVERIES_PER_FIELD, RecoveryPlanner
from form_contracts import FailureClass
from form_contracts import RecoveryStrategy as S


def strategies(planner, field, failure, n):
    return [planner.plan(field, failure).strategy for _ in range(n)]


def test_value_mismatch_ladder():
    p = RecoveryPlanner()
    assert strategies(p, "f", FailureClass.VALUE_MISMATCH, 4) == [
        S.RETRY,
        S.REOBSERVE,
        S.STOP,
        S.STOP,
    ]


def test_validation_error_goes_straight_to_user():
    p = RecoveryPlanner()
    assert strategies(p, "f", FailureClass.VALIDATION_ERROR, 2) == [S.ASK_USER, S.STOP]


def test_value_not_applied_reapplies_by_a_different_method():
    p = RecoveryPlanner()
    assert strategies(p, "f", FailureClass.VALUE_NOT_APPLIED, 3) == [
        S.REAPPLY,
        S.REOBSERVE,
        S.STOP,
    ]


def test_option_not_found_drives_widget_then_waits_then_reformats():
    # The unfamiliar-widget path: try the UI, wait for a cascade, reformat, ask.
    p = RecoveryPlanner()
    assert strategies(p, "f", FailureClass.OPTION_NOT_FOUND, 5) == [
        S.ALT_SELECT,
        S.WAIT_CASCADE,
        S.NORMALIZE_VALUE,
        S.ASK_USER,
        S.STOP,
    ]


def test_cascade_pending_waits_for_dependent_options():
    p = RecoveryPlanner()
    assert strategies(p, "f", FailureClass.CASCADE_PENDING, 3) == [
        S.WAIT_CASCADE,
        S.REOBSERVE,
        S.STOP,
    ]


def test_element_not_found_ladder():
    p = RecoveryPlanner()
    assert strategies(p, "f", FailureClass.ELEMENT_NOT_FOUND, 3) == [S.REOBSERVE, S.SCROLL, S.STOP]


def test_no_strategy_is_repeated_and_ladder_terminates():
    p = RecoveryPlanner()
    seen = strategies(p, "f", FailureClass.VALUE_MISMATCH, 3)[:-1]  # retry, reobserve
    assert len(set(seen)) == len(seen)  # no repeats


def test_clear_resets_field_history():
    p = RecoveryPlanner()
    p.plan("f", FailureClass.VALUE_MISMATCH)  # retry
    p.clear("f")
    assert p.plan("f", FailureClass.VALUE_MISMATCH).strategy is S.RETRY  # fresh again


def test_absolute_backstop():
    p = RecoveryPlanner()
    # Unknown failure uses the default ladder [reobserve, retry, stop]; after
    # the backstop count it is always STOP.
    for _ in range(MAX_RECOVERIES_PER_FIELD + 2):
        d = p.plan("f", None)
    assert d.strategy is S.STOP
