"""Authenticated WebSocket: rejects bad tokens, requires hello, and the
session hub correlates replies, drops stale/duplicate deliveries."""

import asyncio

import pytest
from agent_backend.api.app import AppState, create_app
from agent_backend.api.session_hub import ExtensionSession, ProtocolError
from agent_backend.document_intelligence.fact_store import FactStore
from agent_backend.document_intelligence.store import DocumentStore
from agent_backend.persistence.repository import Repository, make_engine
from fastapi.testclient import TestClient
from form_contracts import PROTOCOL_VERSION, MessageType


def app_with_state():
    state = AppState(
        repo=Repository(make_engine()),
        auth=__import__("agent_backend.api.auth", fromlist=["DevTokenAuth"]).DevTokenAuth(),
        documents=DocumentStore(),
        facts=FactStore(),
    )
    return create_app(state), state


def test_ws_rejects_invalid_token():
    app, state = app_with_state()
    run_id = "run-1"
    state.repo.create_run(run_id)
    state.auth.mint(run_id)
    from starlette.websockets import WebSocketDisconnect

    c = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f"/ws/{run_id}?token=wrong"):
            pass


def test_ws_accepts_valid_token_and_records_attach():
    app, state = app_with_state()
    run_id = "run-1"
    state.repo.create_run(run_id)
    token = state.auth.mint(run_id)
    c = TestClient(app)
    with c.websocket_connect(f"/ws/{run_id}?token={token}") as ws:
        ws.send_json({"type": "hello", "origin": "https://example.test", "tab_id": 5})
        # Session registered; attach event written.
        # Give the server a tick by sending a throwaway inbound frame.
    events = [e.kind for e in state.repo.events(run_id)]
    assert "tab_attached" in events


@pytest.mark.asyncio
async def test_session_hub_correlates_replies():
    sent = []

    async def send(env):
        sent.append(env)

    session = ExtensionSession(run_id="run-1", origin="o", tab_id=1, send=send)

    async def responder():
        await asyncio.sleep(0.01)
        correlation = sent[0]["payload"]["_correlation_id"]
        session.deliver(
            {
                "protocol_version": PROTOCOL_VERSION,
                "message_type": "page_observation",
                "run_id": "run-1",
                "sent_at": "2026-09-03T00:00:00Z",
                "payload": {
                    "_correlation_id": correlation,
                    "_inbound_seq": 1,
                    "observation": {"ok": True},
                },
            }
        )

    task = asyncio.create_task(responder())
    reply = await session.request(MessageType.PAGE_OBSERVATION, {"command": "observe"})
    await task
    assert reply["observation"] == {"ok": True}


@pytest.mark.asyncio
async def test_session_hub_ignores_duplicate_and_stale_inbound():
    async def send(env):
        pass

    session = ExtensionSession(run_id="run-1", origin="o", tab_id=1, send=send)
    base = {
        "protocol_version": PROTOCOL_VERSION,
        "message_type": "action_result",
        "run_id": "run-1",
        "sent_at": "2026-09-03T00:00:00Z",
    }
    session.deliver({**base, "payload": {"_inbound_seq": 5, "result": {"a": 1}}})
    # Duplicate (seq 5) and stale (seq 3) are ignored without error.
    session.deliver({**base, "payload": {"_inbound_seq": 5, "result": {"a": 2}}})
    session.deliver({**base, "payload": {"_inbound_seq": 3, "result": {"a": 3}}})
    assert session._last_inbound_seq == 5


@pytest.mark.asyncio
async def test_session_hub_rejects_protocol_mismatch():
    async def send(env):
        pass

    session = ExtensionSession(run_id="run-1", origin="o", tab_id=1, send=send)
    with pytest.raises(ProtocolError):
        session.deliver(
            {
                "protocol_version": "0.9",
                "message_type": "action_result",
                "run_id": "run-1",
                "sent_at": "2026-09-03T00:00:00Z",
                "payload": {},
            }
        )
