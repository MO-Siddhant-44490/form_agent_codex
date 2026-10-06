"""An e-KYC registration journey over the panel protocol, end to end: a masked
Aadhaar input, a consent checkbox, a text captcha, and a profile whose key for
the Aadhaar number is the abbreviation "aID".

Regression for a live transcript where the agent (1) treated the masked box as
a login credential and filled nothing, (2) asked for the captcha and consent as
free text, (3) read "aid is aadharid" as a VALUE and corrupted the profile."""

from agent_backend.api.app import AppState, create_app
from agent_backend.api.auth import DevTokenAuth
from agent_backend.document_intelligence.fact_store import FactStore
from agent_backend.document_intelligence.store import DocumentStore
from agent_backend.mapper import ModelAssistedMapper
from agent_backend.memory import InMemoryMappingMemory
from agent_backend.model_gateway.base import GatewayResult
from agent_backend.model_gateway.fake import FakeModelAdapter
from agent_backend.persistence.repository import Repository, make_engine
from agent_backend.transports.fake import FakeField, FakeTransport
from fastapi.testclient import TestClient
from form_contracts import (
    PROTOCOL_VERSION,
    BrowserAction,
    FieldMapping,
    FieldMappingBatch,
    ModelCallMetadata,
)

PROFILE = [
    {"key": "full_name", "value": "Ananya Prakash Iyer"},
    {"key": "aID", "value": "976487322387"},
]


class Gateway(FakeModelAdapter):
    """The model proposes aID for the Aadhaar field, but not with certainty."""

    def map_fields(self, request):
        mappings = [
            FieldMapping(
                field_id=f.field_id, fact_key="aID", confidence=0.55, needs_clarification=True
            )
            for f in request.fields
            if "aadhaar" in (f.label or "").lower() and "aID" in {x.key for x in request.facts}
        ]
        return GatewayResult(
            batch=FieldMappingBatch(mappings=mappings),
            metadata=ModelCallMetadata(
                model_id="stub",
                latency_ms=1,
                schema_valid=True,
                request_fingerprint=request.fingerprint(),
            ),
        )


def _ekyc_browser() -> FakeTransport:
    return FakeTransport(
        fields=[
            FakeField("aad", "password", "", "Aadhaar Number/Virtual ID *", required=True),
            FakeField(
                "consent",
                "checkbox",
                "",
                "I consent to the use of my Aadhaar details.",
                required=True,
                checked=False,
            ),
            FakeField("cap", "text", "", "Captcha *", required=True),
        ]
    )


def _drive(ws, browser, run_id, until: str):
    """Act as the extension until the backend sends `until`."""
    for _ in range(80):
        msg = ws.receive_json()
        if msg.get("type") in (until, "fill_error", "chat_error"):
            return msg
        if msg.get("type") == "fill_progress":
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
    raise AssertionError(f"backend never sent {until}")


def test_ekyc_journey_infers_asks_binds_and_keeps_the_profile_honest():
    gateway = Gateway()
    memory = InMemoryMappingMemory()
    state = AppState(
        repo=Repository(make_engine()),
        auth=DevTokenAuth(),
        documents=DocumentStore(),
        facts=FactStore(),
        mapper=ModelAssistedMapper(gateway, memory=memory),
        memory=memory,
    )
    app = create_app(state)
    run_id = "run-ekyc"
    state.repo.create_run(run_id)
    token = state.auth.mint(run_id)
    browser = _ekyc_browser()
    aadhaar, consent, captcha = browser.fields

    client = TestClient(app)
    with client.websocket_connect(f"/ws/{run_id}?token={token}") as ws:
        ws.send_json({"type": "hello", "origin": "http://fake.test", "tab_id": 1})

        # 1. Fill: nothing matches by name; the model's uncertain candidate is
        #    put to the user as a question — not silently dropped, and the form
        #    is not abandoned as a "login/captcha page".
        ws.send_json({"type": "start_fill", "facts": PROFILE})
        result = _drive(ws, browser, run_id, "fill_result")
        assert result["outcome"] == "NEEDS_USER"
        q = next(q for q in result["questions"] if q["field_id"] == "aad")
        assert q["kind"] == "low_confidence" and q["fact_keys"] == ["aID"]
        assert "aID" in q["prompt"] and "Use it" in q["prompt"]
        # The state tells the panel what is whose.
        st = {s["field_id"]: s for s in result["state"]["empty_required"]}
        assert st["cap"]["human_only"] is True
        assert st["consent"]["consent"] is True
        assert "Yours to complete on the page: Captcha *" in result["state"]["text"]

        # 2. "aid is aadharid" — a statement about meaning. The model returns a
        #    bind; the field is filled from the EXISTING value and the profile
        #    is untouched (no fact becomes the literal string 'aadharid').
        gateway.chat_response = (
            '{"reply":"Using aID for the Aadhaar field.","ops":['
            '{"op":"bind","field":"Aadhaar Number/Virtual ID *","key":"aID"}]}'
        )
        ws.send_json({"type": "chat", "text": "aid is aadharid", "facts": PROFILE})
        result = _drive(ws, browser, run_id, "chat_result")
        assert result["applied"] == []
        assert result["filled"] == ["aad"]
        assert aadhaar.value == "976487322387"

        # 3. The consent chip: a one-off value for THAT field, not a profile key.
        ws.send_json({"type": "set_field", "field_id": "consent", "value": "yes", "facts": PROFILE})
        result = _drive(ws, browser, run_id, "chat_result")
        assert result["filled"] == ["consent"]
        assert result["applied"] == []
        assert consent.checked is True

        # 4. The captcha was never touched; it is the human's.
        assert captcha.value is None

        # 5. A revisit maps the Aadhaar field from memory — no question, no model.
        browser2 = _ekyc_browser()
        ws.send_json({"type": "start_fill", "facts": PROFILE})
        result = _drive(ws, browser2, run_id, "fill_result")
        assert "aad" in result["filled"]
        assert not [q for q in result["questions"] if q["field_id"] == "aad"]


def test_chat_set_fact_that_does_not_land_is_not_reported_as_applied():
    """A value that matches no option is NOT written into the profile."""
    gateway = FakeModelAdapter()
    state = AppState(
        repo=Repository(make_engine()),
        auth=DevTokenAuth(),
        documents=DocumentStore(),
        facts=FactStore(),
        mapper=ModelAssistedMapper(gateway),
    )
    app = create_app(state)
    run_id = "run-honest"
    state.repo.create_run(run_id)
    token = state.auth.mint(run_id)
    browser = FakeTransport(
        fields=[FakeField("sex", "select-one", "gender", "Sex", options=["Male", "Female"])]
    )
    facts = [{"key": "gender", "value": "Male"}]
    gateway.chat_response = (
        '{"reply":"Updated gender.","ops":[{"op":"set_fact","key":"gender","value":"Xyz"}]}'
    )
    client = TestClient(app)
    with client.websocket_connect(f"/ws/{run_id}?token={token}") as ws:
        ws.send_json({"type": "hello", "origin": "http://fake.test", "tab_id": 1})
        ws.send_json({"type": "chat", "text": "i m xyz", "facts": facts})
        result = _drive(ws, browser, run_id, "chat_result")
    assert result["applied"] == []  # the panel must not store 'Xyz'
    assert result["unresolved"] == ["gender"]
    assert "couldn't set" in result["reply"]


def test_answering_a_question_mid_flow_continues_to_the_next_page():
    """The flow must not end after the user answers: once the missing field is
    set, the agent carries on — fills what it can and presses Next."""
    gateway = FakeModelAdapter()
    state = AppState(
        repo=Repository(make_engine()),
        auth=DevTokenAuth(),
        documents=DocumentStore(),
        facts=FactStore(),
        mapper=ModelAssistedMapper(gateway),
    )
    app = create_app(state)
    run_id = "run-continue"
    state.repo.create_run(run_id)
    token = state.auth.mint(run_id)
    browser = FakeTransport(
        pages=[
            [
                FakeField("full-name", "text", "full_name", "Full name", required=True),
                FakeField("ref", "text", "ref_code", "Referral code", required=True),
            ],
            [FakeField("email", "email", "email", "Email", required=True)],
        ]
    )
    facts = [
        {"key": "full_name", "value": "Ananya Prakash Iyer"},
        {"key": "email", "value": "ananya.iyer@examplemail.in"},
    ]
    client = TestClient(app)
    with client.websocket_connect(f"/ws/{run_id}?token={token}") as ws:
        ws.send_json({"type": "hello", "origin": "http://fake.test", "tab_id": 1})
        ws.send_json({"type": "start_fill", "facts": facts})
        first = _drive(ws, browser, run_id, "fill_result")
        assert first["outcome"] == "NEEDS_USER"
        assert any(q["field_id"] == "ref" for q in first["questions"])
        assert browser.current_page == 0  # blocked on page 1

        ws.send_json({"type": "set_field", "field_id": "ref", "value": "VIP2026", "facts": facts})
        after = _drive(ws, browser, run_id, "chat_result")
    assert browser.current_page == 1  # it pressed Next by itself
    assert "email" in after["filled"]
    assert "Continuing" in after["reply"]
