"""End-to-end over the async bridge: the sync orchestration graph drives a
mock extension through WebSocketBrowserTransport, across the same
run_coroutine_threadsafe boundary the real WebSocket endpoint uses. The mock
extension answers each request envelope from a local FakeTransport.

This proves the transport contract holds over the async hub without a browser;
test_websocket.py covers the literal socket, auth, and protocol handshake."""

import asyncio
import threading

from agent_backend.api.session_hub import ExtensionSession
from agent_backend.api.transport_ws import WebSocketBrowserTransport
from agent_backend.facts import slice1_facts
from agent_backend.orchestration.graph import build_form_fill_graph
from agent_backend.orchestration.runner import start_run
from agent_backend.transports.fake import FakeTransport
from form_contracts import PROTOCOL_VERSION, BrowserAction, RunOutcome


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def test_graph_fills_form_over_the_async_bridge():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=_run_loop, args=(loop,), daemon=True)
    thread.start()

    browser = FakeTransport()  # stands in for the real page behind the extension

    # The "extension": each outbound request envelope is answered by executing
    # against the local browser and delivering a correlated reply. Runs on the
    # hub's event loop, exactly like a real socket read handler would.
    async def extension_send(envelope: dict) -> None:
        payload = envelope["payload"]
        reply = {
            "_correlation_id": payload["_correlation_id"],
            "_inbound_seq": session.request_counter,
        }
        command = payload.get("command")
        if command == "observe":
            reply["observation"] = browser.observe().model_dump(mode="json")
        elif command == "execute":
            outcome = browser.execute(BrowserAction.model_validate(payload["action"]))
            reply["result"] = outcome.result.model_dump(mode="json")
            if outcome.observation:
                reply["observation"] = outcome.observation.model_dump(mode="json")
            if outcome.verification:
                reply["verification"] = outcome.verification.model_dump(mode="json")
        session.deliver(
            {
                "protocol_version": PROTOCOL_VERSION,
                "message_type": "action_result",
                "run_id": "run-ws",
                "sent_at": "2026-09-03T00:00:00Z",
                "payload": reply,
            }
        )

    session = ExtensionSession(
        run_id="run-ws",
        origin="http://fake.test",
        tab_id=1,
        send=extension_send,
    )
    transport = WebSocketBrowserTransport(session, loop)
    graph = build_form_fill_graph(transport)

    try:
        handle = start_run(graph, "run-ws", slice1_facts())
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)

    assert handle.state["outcome"] == RunOutcome.COMPLETED.value
    assert len(handle.state["filled_fields"]) == 8
    # Every action really crossed the wire to the browser.
    assert sum(browser.execution_counts.values()) == 8
