"""A page mid-reload is waited for, not fatal (the flaky-fill root cause)."""

import pytest
from agent_backend.driver import run_fill
from agent_backend.facts import slice1_facts
from agent_backend.transport import PageUnavailable, ResilientTransport
from agent_backend.transports.fake import FakeTransport
from form_contracts import RunOutcome


class Flaky:
    """Every Nth observe fails the way a reloading tab does."""

    def __init__(self, inner, every=3, burst=2):
        self.inner, self.every, self.burst, self.n, self.failing = inner, every, burst, 0, 0

    def attach(self):
        return self.inner.attach()

    def observe(self):
        self.n += 1
        if self.failing == 0 and self.n % self.every == 0:
            self.failing = self.burst
        if self.failing:
            self.failing -= 1
            raise PageUnavailable("observe failed: Receiving end does not exist")
        return self.inner.observe()

    def execute(self, action):
        return self.inner.execute(action)

    def close(self):
        pass


def test_run_fill_survives_transient_unreadable_pages():
    flaky = Flaky(FakeTransport())
    result = run_fill(ResilientTransport(flaky, pause_s=0), slice1_facts())
    assert result.outcome is RunOutcome.COMPLETED, result.detail
    assert len(result.filled_fields) == 8


def test_persistent_failure_still_surfaces_after_the_bound():
    class Dead(Flaky):
        def observe(self):
            raise PageUnavailable("tab closed")

    with pytest.raises(PageUnavailable):
        ResilientTransport(Dead(FakeTransport()), reads=3, pause_s=0).observe()
