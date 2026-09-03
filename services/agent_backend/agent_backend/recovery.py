"""Bounded recovery agent (plan.md §7.8, Module 7.8): classify a verification
failure and select the next bounded strategy from a per-failure ladder. The
same strategy is never repeated for the same field, ladders terminate in STOP,
and the planner itself keeps no unbounded state — so a run can never loop on a
recovery."""

from dataclasses import dataclass, field

from form_contracts import FailureClass, RecoveryStrategy

S = RecoveryStrategy

# Per-failure escalation ladders. Order matters: cheap/local first, human/stop
# last. A validation error means the value does not fit the field, so retrying
# the same value is pointless — go straight to the user.
_LADDERS: dict[FailureClass, list[RecoveryStrategy]] = {
    FailureClass.ELEMENT_NOT_FOUND: [S.REOBSERVE, S.SCROLL, S.STOP],
    FailureClass.STALE_ELEMENT: [S.REOBSERVE, S.STOP],
    FailureClass.PAGE_CHANGED: [S.REOBSERVE, S.STOP],
    FailureClass.VALUE_MISMATCH: [S.RETRY, S.REOBSERVE, S.STOP],
    FailureClass.VALIDATION_ERROR: [S.ASK_USER],
    FailureClass.NAVIGATION_FAILED: [S.WAIT_STABLE, S.REOBSERVE, S.STOP],
    FailureClass.TIMEOUT: [S.WAIT_STABLE, S.RETRY, S.STOP],
    FailureClass.UNSUPPORTED_WIDGET: [S.ASK_USER],
}
_DEFAULT_LADDER = [S.REOBSERVE, S.RETRY, S.STOP]

# Absolute backstop per field regardless of ladder length.
MAX_RECOVERIES_PER_FIELD = 5


@dataclass
class RecoveryDecision:
    field_id: str
    failure_class: FailureClass | None
    strategy: RecoveryStrategy
    reason: str


@dataclass
class RecoveryPlanner:
    # Strategies already spent per field, in order — guarantees no repeat and
    # bounded progression down the ladder.
    _history: dict[str, list[RecoveryStrategy]] = field(default_factory=dict)

    def plan(self, field_id: str, failure_class: FailureClass | None) -> RecoveryDecision:
        used = self._history.setdefault(field_id, [])
        ladder = _LADDERS.get(failure_class, _DEFAULT_LADDER) if failure_class else _DEFAULT_LADDER

        if len(used) >= MAX_RECOVERIES_PER_FIELD:
            return self._record(field_id, failure_class, S.STOP, "recovery attempts exhausted")

        # Next ladder strategy not already tried for this field.
        for strategy in ladder:
            if strategy not in used:
                return self._record(
                    field_id,
                    failure_class,
                    strategy,
                    f"ladder step for {failure_class.value if failure_class else 'unknown'}",
                )
        return self._record(field_id, failure_class, S.STOP, "ladder exhausted")

    def clear(self, field_id: str) -> None:
        """Called when a field finally succeeds, so a later unrelated failure
        on the same field starts fresh."""
        self._history.pop(field_id, None)

    def _record(
        self,
        field_id: str,
        failure_class: FailureClass | None,
        strategy: RecoveryStrategy,
        reason: str,
    ) -> RecoveryDecision:
        if strategy is not S.STOP:
            self._history[field_id].append(strategy)
        return RecoveryDecision(field_id, failure_class, strategy, reason)
