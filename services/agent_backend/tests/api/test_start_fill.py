"""The product fill flow over the HTTP WebSocket: the panel sends start_fill,
the backend drives run_fill over the socket, a mock extension answers the
observe/execute requests from a FakeTransport, and the backend returns a
fill_result. Proves the 'click Fill in the panel' path end to end."""

from agent_backend.api.app import AppState, create_app
from agent_backend.api.auth import DevTokenAuth
from agent_backend.document_intelligence.fact_store import FactStore
from agent_backend.document_intelligence.store import DocumentStore
from agent_backend.mapper import DeterministicMapper
from agent_backend.persistence.repository import Repository, make_engine
from agent_backend.transports.fake import FakeField, FakeTransport
from fastapi.testclient import TestClient
from form_contracts import PROTOCOL_VERSION, BrowserAction


def test_start_fill_drives_the_fill_and_returns_a_result():
    state = AppState(
        repo=Repository(make_engine()),
        auth=DevTokenAuth(),
        documents=DocumentStore(),
        facts=FactStore(),
        mapper=DeterministicMapper(),
    )
    app = create_app(state)
    run_id = "run-fill"
    state.repo.create_run(run_id)
    token = state.auth.mint(run_id)

    # A simple 2-field form the mock extension serves.
    browser = FakeTransport(
        fields=[
            FakeField("full-name", "text", "full_name", "Full name", required=True),
            FakeField("email", "email", "email", "Email", required=True),
        ]
    )

    client = TestClient(app)
    with client.websocket_connect(f"/ws/{run_id}?token={token}") as ws:
        ws.send_json({"type": "hello", "origin": "http://fake.test", "tab_id": 1})
        ws.send_json(
            {
                "type": "start_fill",
                "facts": [
                    {"key": "full_name", "value": "Ada Lovelace"},
                    {"key": "email", "value": "ada@example.test"},
                ],
            }
        )

        # Act as the extension: answer observe/execute envelopes until the
        # backend sends the fill_result.
        result = None
        progress = []
        for _ in range(80):
            msg = ws.receive_json()
            if msg.get("type") == "fill_result":
                result = msg
                break
            if msg.get("type") == "fill_progress":
                progress.append(msg)
                continue
            payload = msg["payload"]
            reply = {
                "protocol_version": PROTOCOL_VERSION,
                "message_type": "action_result",
                "run_id": run_id,
                "sent_at": "2026-01-01T00:00:00Z",
                "payload": {"_correlation_id": payload["_correlation_id"]},
            }
            if payload.get("command") == "observe":
                reply["payload"]["observation"] = browser.observe().model_dump(mode="json")
            elif payload.get("command") == "execute":
                outcome = browser.execute(BrowserAction.model_validate(payload["action"]))
                reply["payload"]["result"] = outcome.result.model_dump(mode="json")
                if outcome.observation:
                    reply["payload"]["observation"] = outcome.observation.model_dump(mode="json")
                if outcome.verification:
                    reply["payload"]["verification"] = outcome.verification.model_dump(mode="json")
            ws.send_json(reply)

    assert result is not None, "backend never sent a fill_result"
    # Live progress streamed while filling, in page order.
    filling = [p["label"] for p in progress if p["phase"] == "filling"]
    assert filling == ["Full name", "Email"]
    assert progress[0]["phase"] == "reading" and progress[-1]["phase"] == "checking"
    assert result["outcome"] == "COMPLETED"
    assert set(result["filled"]) == {"full-name", "email"}
    # The form was actually filled in the (mock) browser.
    assert {f.name: f.value for f in browser.fields} == {
        "full_name": "Ada Lovelace",
        "email": "ada@example.test",
    }
