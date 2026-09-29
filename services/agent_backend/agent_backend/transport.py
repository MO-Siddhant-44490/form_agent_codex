"""Browser transport contract (plan.md §5): the driver never touches Chrome
APIs or Playwright directly. Slice 1 implements the fake and extension
transports; waitForUser/native-messaging variants arrive with later slices."""

import time
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


class PageUnavailable(RuntimeError):
    """The page could not be read or acted on right now — typically a full
    page load is in flight (after "Next"), or the extension did not answer in
    time. Transient: callers retry after a short wait instead of failing."""


class ResilientTransport:
    """Wraps any transport so a page mid-reload is waited for instead of
    crashing the run. Reads retry for a bounded time; an action is retried
    once — safe because every mutating action carries an idempotency key and
    the extension answers a duplicate delivery with the recorded result
    instead of repeating it (invariant 6). Bounded (invariant 12)."""

    def __init__(self, inner: BrowserTransport, reads: int = 8, pause_s: float = 0.6) -> None:
        self._inner = inner
        self._reads = reads
        self._pause_s = pause_s

    def attach(self) -> TabSession:
        return self._inner.attach()

    def observe(self) -> PageObservation:
        for attempt in range(self._reads):
            try:
                return self._inner.observe()
            except PageUnavailable:
                if attempt == self._reads - 1:
                    raise
                time.sleep(self._pause_s)
        raise PageUnavailable("unreachable")  # pragma: no cover

    def execute(self, action: BrowserAction) -> ExecuteOutcome:
        try:
            return self._inner.execute(action)
        except PageUnavailable:
            time.sleep(self._pause_s)
            return self._inner.execute(action)

    def close(self) -> None:
        self._inner.close()

    def __getattr__(self, name):  # page_eval etc. on test transports
        return getattr(self._inner, name)


def resilient(transport: BrowserTransport) -> BrowserTransport:
    return transport if isinstance(transport, ResilientTransport) else ResilientTransport(transport)
