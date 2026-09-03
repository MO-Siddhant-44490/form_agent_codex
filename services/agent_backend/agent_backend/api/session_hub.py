"""Extension session hub: correlates request/response envelopes over one
WebSocket per run, enforces monotonic sequence numbers, and provides the
synchronous request/reply the transport needs.

Runs in the FastAPI event loop; the orchestrator (sync) talks to it through
a threadsafe bridge (WebSocketBrowserTransport)."""

import asyncio
from dataclasses import dataclass, field

from form_contracts import (
    PROTOCOL_VERSION,
    Envelope,
    MessageType,
)


class ProtocolError(Exception):
    pass


@dataclass
class ExtensionSession:
    run_id: str
    origin: str
    tab_id: int
    send: "callable"  # async (dict) -> None, bound to the WebSocket
    _pending: dict[str, asyncio.Future] = field(default_factory=dict)
    _last_inbound_seq: int = -1
    request_counter: int = 0

    async def request(self, message_type: MessageType, payload: dict, timeout: float = 30.0):
        """Send a request envelope and await the correlated reply."""
        self.request_counter += 1
        correlation_id = f"{self.run_id}:{self.request_counter}"
        envelope = {
            "protocol_version": PROTOCOL_VERSION,
            "message_type": message_type.value,
            "run_id": self.run_id,
            "sent_at": _now_iso(),
            "payload": {**payload, "_correlation_id": correlation_id},
        }
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[correlation_id] = future
        await self.send(envelope)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(correlation_id, None)

    def deliver(self, raw: dict) -> None:
        """Route an inbound envelope from the extension to its waiter.
        Rejects protocol-version mismatches and stale/duplicate sequences."""
        if raw.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version {raw.get('protocol_version')}")
        # Validate the envelope shape (payload is validated by the transport).
        Envelope.model_validate(raw)
        payload = raw.get("payload", {})
        correlation_id = payload.get("_correlation_id")
        seq = payload.get("_inbound_seq")
        if seq is not None:
            if seq <= self._last_inbound_seq:
                return  # duplicate or out-of-order delivery: ignore (invariant 8)
            self._last_inbound_seq = seq
        future = self._pending.get(correlation_id)
        if future is not None and not future.done():
            future.set_result(payload)

    def fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
