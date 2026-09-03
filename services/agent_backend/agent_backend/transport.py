"""Browser transport contract (plan.md §5): the driver never touches Chrome
APIs or Playwright directly. Slice 1 implements the fake and extension
transports; waitForUser/native-messaging variants arrive with later slices."""

from dataclasses import dataclass
from typing import Protocol

from form_contracts import (
    ActionResult,
    BrowserAction,
    PageObservation,
    TabSession,
    VerificationResult,
)


@dataclass
class ExecuteOutcome:
    """Result of one guarded execute: the executor's report, the independent
    verification, and the fresh observation the verification was based on."""

    result: ActionResult
    verification: VerificationResult | None
    observation: PageObservation | None


class BrowserTransport(Protocol):
    def attach(self) -> TabSession: ...

    def observe(self) -> PageObservation: ...

    def execute(self, action: BrowserAction) -> ExecuteOutcome: ...

    def close(self) -> None: ...
