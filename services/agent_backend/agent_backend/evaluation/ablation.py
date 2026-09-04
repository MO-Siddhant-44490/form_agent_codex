"""Ablation harness (plan.md §17): run the same scenarios under different
system configurations to measure what each design choice contributes. The
modular driver makes each ablation a flag rather than a fork.

Scenarios are FakeTransport configs with injected difficulty; configs toggle
the closed-loop, recovery, and mapper choices."""

from collections.abc import Callable
from dataclasses import dataclass

from form_contracts import RunOutcome

from ..driver import run_fill
from ..facts import slice1_facts
from ..mapper import DeterministicMapper, Mapper, ModelAssistedMapper
from ..model_gateway.fake import FakeModelAdapter
from ..transports.fake import FakeTransport, basic_form_fields


@dataclass
class Scenario:
    name: str
    make_transport: Callable[[], FakeTransport]
    # A field that must end up correctly filled for the run to be "truly" done.
    check_field: str
    expected_value: str


@dataclass
class Config:
    name: str
    verify: bool = True
    recover: bool = True
    mapper: Callable[[], Mapper] = DeterministicMapper


@dataclass
class AblationCell:
    scenario: str
    config: str
    outcome: str
    filled_count: int
    # True success: the check field is ACTUALLY correct in the transport,
    # regardless of what the run reported (catches open-loop false successes).
    truly_correct: bool


def _flaky_transport() -> FakeTransport:
    t = FakeTransport()
    t.fields[3].fail_executions = 1  # dob sticks on the retry
    return t


def _invalid_value_transport() -> FakeTransport:
    # The email value sticks (current_value matches) but carries a validation
    # error — the value looks filled to an open-loop run but is actually wrong.
    t = FakeTransport()
    t.fields[1].validation_error = "Invalid email format"
    return t


def _obscure_names_transport() -> FakeTransport:
    fields = basic_form_fields()
    for i, f in enumerate(fields):
        f.name = f"fld_{i}"  # deterministic name-matching fails
    return FakeTransport(fields=fields)


DEFAULT_SCENARIOS = [
    Scenario("clean", FakeTransport, "dob", "1998-04-17"),
    Scenario("flaky_dob", _flaky_transport, "dob", "1998-04-17"),
    Scenario("invalid_email", _invalid_value_transport, "email", "ada@example.test"),
    Scenario("obscure_names", _obscure_names_transport, "dob", "1998-04-17"),
]

DEFAULT_CONFIGS = [
    Config("closed_loop", verify=True, recover=True),
    Config("open_loop", verify=False, recover=True),
    Config("no_recovery", verify=True, recover=False),
    Config(
        "model_mapper",
        verify=True,
        recover=True,
        mapper=lambda: ModelAssistedMapper(FakeModelAdapter()),
    ),
]


def _facts_for(scenario: Scenario) -> list:
    facts = slice1_facts()
    if scenario.name == "obscure_names":
        # country must be public for option selection in the model path.
        from form_contracts import Sensitivity

        facts = [
            f.model_copy(update={"value": "India", "sensitivity": Sensitivity.PUBLIC})
            if f.key == "country"
            else f
            for f in facts
        ]
    return facts


def run_ablation(
    scenarios: list[Scenario] | None = None, configs: list[Config] | None = None
) -> list[AblationCell]:
    scenarios = scenarios or DEFAULT_SCENARIOS
    configs = configs or DEFAULT_CONFIGS
    cells: list[AblationCell] = []
    for scenario in scenarios:
        for config in configs:
            transport = scenario.make_transport()
            result = run_fill(
                transport,
                _facts_for(scenario),
                mapper=config.mapper(),
                verify=config.verify,
                recover=config.recover,
            )
            field = next(
                (f for f in transport._active_fields() if f.field_id == scenario.check_field),
                None,
            )
            # Truly correct = the right value AND no lingering validation error.
            # An open-loop run cannot tell the difference; the closed loop can.
            truly_correct = (
                field is not None
                and field.value == scenario.expected_value
                and field.validation_error is None
            )
            cells.append(
                AblationCell(
                    scenario=scenario.name,
                    config=config.name,
                    outcome=result.outcome.value,
                    filled_count=len(result.filled_fields),
                    truly_correct=truly_correct,
                )
            )
    return cells


def false_success_rate(cells: list[AblationCell]) -> dict[str, float]:
    """Per config: fraction of runs that reported COMPLETED but where the check
    field was NOT actually correct — the headline open-loop failure mode."""
    by_config: dict[str, list[AblationCell]] = {}
    for cell in cells:
        by_config.setdefault(cell.config, []).append(cell)
    out: dict[str, float] = {}
    for config, group in by_config.items():
        completed = [c for c in group if c.outcome == RunOutcome.COMPLETED.value]
        if not completed:
            out[config] = 0.0
            continue
        false = sum(1 for c in completed if not c.truly_correct)
        out[config] = false / len(completed)
    return out
