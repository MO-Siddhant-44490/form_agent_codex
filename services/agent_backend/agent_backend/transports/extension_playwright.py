"""ExtensionPlaywrightTransport: drives the real MV3 extension's background
test hooks through Playwright (the Slice 1 harness stand-in for the Slice 4
WebSocket backend). The extension still enforces every guard itself — this
transport is a command source, not a bypass."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from form_contracts import (
    ActionResult,
    BrowserAction,
    PageObservation,
    TabSession,
    VerificationResult,
)

from ..transport import ExecuteOutcome, PageUnavailable


class ExtensionPlaywrightTransport:
    def __init__(
        self,
        extension_dist: Path,
        fixture_url: str,
        extra_args: list[str] | None = None,
        headless: bool = True,
        slow_mo: int = 0,
        record_video_dir: str | None = None,
    ) -> None:
        self._extension_dist = extension_dist
        self._fixture_url = fixture_url
        self._extra_args = extra_args or []
        self._headless = headless
        self._slow_mo = slow_mo
        self._record_video_dir = record_video_dir
        self._pw: Any = None
        self._context: Any = None
        self._worker: Any = None
        self._session: TabSession | None = None

    def attach(self) -> TabSession:
        """Launch the browser with the extension and bind to the fixture tab.
        Idempotent: the driver's run_fill/apply_edits each call attach()."""
        if self._session is not None:
            return self._session
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        context_kwargs: dict = {
            "channel": "chromium",
            "headless": self._headless,
            "slow_mo": self._slow_mo,
            "args": [
                f"--disable-extensions-except={self._extension_dist}",
                f"--load-extension={self._extension_dist}",
                *self._extra_args,
            ],
        }
        if self._record_video_dir:
            context_kwargs["record_video_dir"] = self._record_video_dir
            context_kwargs["viewport"] = {"width": 1000, "height": 900}
        self._context = self._pw.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="fa-ext-py-"), **context_kwargs
        )
        workers = self._context.service_workers
        self._worker = workers[0] if workers else self._context.wait_for_event("serviceworker")

        page = self._context.new_page()
        # DOM is enough; heavy sites (analytics, ads) may never fire "load".
        page.goto(self._fixture_url, wait_until="domcontentloaded", timeout=60000)
        # The site may redirect or append query parameters; bind to the tab we
        # actually opened (its final URL), not the URL we asked for.
        state = self._worker.evaluate(
            """async (url) => {
                const tabs = await chrome.tabs.query({});
                const tab = tabs.find((t) => t.url === url) ?? tabs.find((t) => t.active);
                if (!tab?.id || !tab.url) throw new Error(`no tab for ${url}`);
                return globalThis.__formAgentTest.attachToTab(tab.id, tab.url);
            }""",
            page.url,
        )
        if not state.get("attached"):
            raise RuntimeError(f"attach failed: {state.get('error')}")
        self._session = TabSession(
            run_id=state["runId"],
            tab_id=state["tabId"],
            origin=state["origin"],
            attached_at=datetime.now(UTC),
        )
        return self._session

    def observe(self) -> PageObservation:
        try:
            state = self._worker.evaluate("() => globalThis.__formAgentTest.observe()")
        except Exception as error:  # noqa: BLE001 — worker/page mid-navigation
            raise PageUnavailable(str(error)) from error
        if state.get("error"):
            raise PageUnavailable(f"observe failed: {state['error']}")
        return PageObservation.model_validate(state["lastObservation"])

    def execute(self, action: BrowserAction) -> ExecuteOutcome:
        outcome = self._worker.evaluate(
            "(a) => globalThis.__formAgentTest.execute(a)",
            action.model_dump(mode="json"),
        )
        observation = None
        state = outcome.get("state") or {}
        if state.get("lastObservation") and outcome.get("fresh") is not False:
            observation = PageObservation.model_validate(state["lastObservation"])
        verification = None
        if outcome.get("verification"):
            verification = VerificationResult.model_validate(outcome["verification"])
        return ExecuteOutcome(
            result=ActionResult.model_validate(outcome["result"]),
            verification=verification,
            observation=observation,
        )

    def page_eval(self, expression: str) -> Any:
        """Fixture ground-truth checks from tests; not part of the transport
        contract."""
        page = next(p for p in self._context.pages if p.url.startswith(self._fixture_url))
        return page.evaluate(expression)

    def close(self) -> None:
        if self._context:
            self._context.close()
        if self._pw:
            self._pw.stop()
