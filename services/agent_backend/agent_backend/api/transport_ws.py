"""WebSocketBrowserTransport: a BrowserTransport implementation that drives a
connected extension over the session hub. Bridges the synchronous
orchestrator to the async WebSocket via run_coroutine_threadsafe, so the
LangGraph loop runs unchanged over a real browser (plan.md §5)."""

import asyncio
from datetime import UTC, datetime

from form_contracts import (
    ActionResult,
    BrowserAction,
    MessageType,
    PageObservation,
    TabSession,
    VerificationResult,
)

from ..transport import ExecuteOutcome, PageUnavailable
from .session_hub import ExtensionSession


class WebSocketBrowserTransport:
    def __init__(self, session: ExtensionSession, loop: asyncio.AbstractEventLoop) -> None:
        self._session = session
        self._loop = loop

    def _call(self, message_type: MessageType, payload: dict) -> dict:
        future = asyncio.run_coroutine_threadsafe(
            self._session.request(message_type, payload), self._loop
        )
        try:
            return future.result(timeout=35.0)
        except TimeoutError as error:  # no reply: the extension could not answer
            future.cancel()
            raise PageUnavailable(f"extension did not answer {payload.get('command')}") from error

    def attach(self) -> TabSession:
        return TabSession(
            run_id=self._session.run_id,
            tab_id=self._session.tab_id,
            origin=self._session.origin,
            attached_at=datetime.now(UTC),
        )

    def observe(self) -> PageObservation:
        reply = self._call(MessageType.PAGE_OBSERVATION, {"command": "observe"})
        if not reply.get("observation"):
            raise PageUnavailable(reply.get("error") or "observation unavailable")
        return PageObservation.model_validate(reply["observation"])

    def execute(self, action: BrowserAction) -> ExecuteOutcome:
        reply = self._call(
            MessageType.BROWSER_ACTION,
            {"command": "execute", "action": action.model_dump(mode="json")},
        )
        if not reply.get("result"):
            raise PageUnavailable(reply.get("error") or "execute produced no result")
        observation = (
            PageObservation.model_validate(reply["observation"])
            if reply.get("observation")
            else None
        )
        verification = (
            VerificationResult.model_validate(reply["verification"])
            if reply.get("verification")
            else None
        )
        return ExecuteOutcome(
            result=ActionResult.model_validate(reply["result"]),
            verification=verification,
            observation=observation,
        )

    def close(self) -> None:
        pass
