"""Ablation harness demonstrates the research claims (plan.md §17)."""

from agent_backend.evaluation.ablation import (
    DEFAULT_CONFIGS,
    DEFAULT_SCENARIOS,
    false_success_rate,
    run_ablation,
)
from form_contracts import RunOutcome


def cells():
    return run_ablation()


def test_open_loop_has_false_successes_closed_loop_does_not():
    rates = false_success_rate(cells())
    # The perceive-act-verify loop prevents the false success open-loop makes.
    assert rates["open_loop"] > 0
    assert rates["closed_loop"] == 0.0


def test_closed_loop_catches_the_invalid_value_open_loop_misses():
    by = {(c.scenario, c.config): c for c in cells()}
    invalid_open = by[("invalid_email", "open_loop")]
    invalid_closed = by[("invalid_email", "closed_loop")]
    # Open loop reports done but is wrong; closed loop surfaces it for the user.
    assert invalid_open.outcome == RunOutcome.COMPLETED.value
    assert invalid_open.truly_correct is False
    assert invalid_closed.outcome == RunOutcome.NEEDS_USER.value


def test_recovery_completes_a_flaky_field_that_no_recovery_gives_up_on():
    by = {(c.scenario, c.config): c for c in cells()}
    assert by[("flaky_dob", "closed_loop")].outcome == RunOutcome.COMPLETED.value
    assert by[("flaky_dob", "closed_loop")].truly_correct is True
    # Without recovery, the transient failure is not retried -> not completed.
    assert by[("flaky_dob", "no_recovery")].outcome != RunOutcome.COMPLETED.value


def test_model_mapper_completes_obscure_names_deterministic_cannot():
    by = {(c.scenario, c.config): c for c in cells()}
    # The model-assisted mapper resolves obscure field names.
    assert by[("obscure_names", "model_mapper")].truly_correct is True
    assert by[("obscure_names", "closed_loop")].truly_correct is False  # deterministic fails


def test_grid_is_complete():
    assert len(cells()) == len(DEFAULT_SCENARIOS) * len(DEFAULT_CONFIGS)
