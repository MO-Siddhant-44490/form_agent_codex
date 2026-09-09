"""FastAPI application: run lifecycle, document upload, audit, and the
authenticated extension WebSocket. Wiring only — orchestration, mapping, and
policy live in their own modules."""

import asyncio
import threading
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from form_contracts import DocumentFact, FactStatus, FactValueType, Sensitivity, redacted_fact_repr

from ..chat_agent import interpret_or_fallback
from ..document_intelligence.fact_store import FactStore
from ..document_intelligence.parser_factory import build_parser_from_env
from ..document_intelligence.pipeline import DocumentPipeline
from ..document_intelligence.store import DocumentRejected, DocumentStore
from ..driver import apply_edits, run_fill
from ..mapper import DeterministicMapper, Mapper
from ..memory import MappingMemory
from ..persistence.repository import Repository, make_engine
from ..snapshot import build_snapshot, summarize
from .auth import AuthError, DevTokenAuth
from .session_hub import ExtensionSession, ProtocolError
from .transport_ws import WebSocketBrowserTransport

# Non-sensitive geography keys; everything else defaults to personal.
PUBLIC_KEYS = frozenset({"country", "state", "district", "locality", "pincode", "gender"})


@dataclass
class AppState:
    repo: Repository
    auth: DevTokenAuth
    documents: DocumentStore
    facts: FactStore
    # Mapper used when the backend drives an orchestration graph. Defaults to
    # the deterministic mapper for zero-dependency tests; the server wires the
    # env-selected mapper (Bedrock by default) via build_default_mapper().
    mapper: Mapper = field(default_factory=DeterministicMapper)
    # Cross-run episodic mapping memory (durable). When set, run_fill records
    # verified field->fact mappings here and the mapper recalls them.
    memory: "MappingMemory | None" = None
    sessions: dict[str, ExtensionSession] = field(default_factory=dict)
    # Last form-state snapshot per run, so the chat layer can report what
    # changed since the previous turn (new cascade fields, new errors).
    form_state: dict[str, list] = field(default_factory=dict)


def _facts_from_payload(items: list[dict]) -> list[DocumentFact]:
    """Build user-provided facts from the panel's simple {key, value} list."""
    facts = []
    for i, item in enumerate(items):
        key = item.get("key")
        value = item.get("value")
        if not key or value is None:
            continue
        sens = Sensitivity(item.get("sensitivity", "personal"))
        facts.append(
            DocumentFact(
                fact_id=f"user-{key}-{i}",
                key=key,
                value=str(value),
                value_type=FactValueType(item.get("value_type", "string")),
                confidence=1.0,
                sensitivity=sens,
                status=FactStatus.USER_PROVIDED,
            )
        )
    return facts


def _fill_payload(result, state) -> dict:
    """The common fill-result fields shared by a plain fill and a chat turn."""
    return {
        "outcome": result.outcome.value,
        "filled": result.filled_fields,
        "questions": [
            {
                "field_id": q.field_id,
                "kind": q.kind.value,
                "prompt": q.prompt,
                # fact_keys lets the panel route an answer to the right fact;
                # options let it render choices for a confirmation.
                "fact_keys": list(q.fact_keys),
                "options": list(q.options) if q.options else None,
            }
            for q in result.questions
        ],
        "validation_issues": [
            {"field_id": i.field_id, "kind": i.kind.value, "label": i.label, "detail": i.detail}
            for i in result.validation.issues
        ],
        "state": state,
        "detail": result.detail,
    }


def _reperceive(st, transport, run_id):
    """Snapshot the form as it now stands and summarize the delta since the last
    turn. Best-effort; never fails the caller."""
    try:
        snapshot = build_snapshot(transport.observe())
        state = summarize(snapshot, st.form_state.get(run_id))
        st.form_state[run_id] = snapshot
        return state
    except Exception:
        return None


def _merge_facts(fact_items: list[dict], updates: list[dict]) -> list[dict]:
    by_key = {f["key"]: dict(f) for f in fact_items if f.get("key")}
    for u in updates:
        k = u["key"]
        if k in by_key:
            by_key[k]["value"] = u["value"]
        else:
            by_key[k] = {
                "key": k,
                "value": u["value"],
                "sensitivity": "public" if k in PUBLIC_KEYS else "personal",
            }
    return list(by_key.values())


def _start_fill(st, session, loop, fact_items, send) -> None:
    """Drive a fill in the connected tab (in a worker thread, since run_fill is
    sync) and stream the result back to the panel. Never submits."""
    facts = _facts_from_payload(fact_items)

    def worker():
        transport = WebSocketBrowserTransport(session, loop)
        try:
            result = run_fill(transport, facts, mapper=st.mapper, memory=st.memory)
            state = _reperceive(st, transport, session.run_id)
            payload = {"type": "fill_result", **_fill_payload(result, state)}
        except Exception as error:  # surface failures to the panel
            payload = {"type": "fill_error", "error": str(error)}
        st.repo.append_event(session.run_id, "fill_completed", {"outcome": payload.get("outcome")})
        asyncio.run_coroutine_threadsafe(send(payload), loop)

    threading.Thread(target=worker, daemon=True).start()


def _chat(st, session, loop, text, fact_items, send, history=None) -> None:
    """A reasoning chat turn: perceive the live form, interpret the user's
    message into a plan (LLM, with a deterministic fallback), apply any fact
    updates, re-fill through the gated pipeline, and report the new state.
    Never submits; the plan can only set facts / refill / explain."""

    def worker():
        transport = WebSocketBrowserTransport(session, loop)
        try:
            snapshot = build_snapshot(transport.observe())
            gateway = getattr(st.mapper, "_gateway", None)
            plan = interpret_or_fallback(gateway, text, snapshot, fact_items, history or [])
            updates = plan.fact_updates()
            payload = {"type": "chat_result", "reply": plan.reply, "applied": updates}
            if updates:
                # Targeted edit: change only the affected field(s), not the whole
                # form — avoids the step-budget churn of a full re-fill.
                merged = _merge_facts(fact_items, updates)
                edit = apply_edits(
                    transport,
                    _facts_from_payload(merged),
                    {u["key"] for u in updates},
                    mapper=st.mapper,
                )
                payload.update(
                    {
                        "outcome": "EDITED",
                        "filled": edit.filled_fields,
                        "questions": [],
                        "validation_issues": [],
                        "state": _reperceive(st, transport, session.run_id),
                        "detail": None,
                    }
                )
            elif any(o.op == "refill" for o in plan.ops):
                result = run_fill(
                    transport, _facts_from_payload(fact_items), mapper=st.mapper, memory=st.memory
                )
                payload.update(_fill_payload(result, _reperceive(st, transport, session.run_id)))
            else:
                # Explain-only: report the current state without acting.
                payload["state"] = summarize(snapshot, st.form_state.get(session.run_id))
                st.form_state[session.run_id] = snapshot
        except Exception as error:
            payload = {"type": "chat_error", "error": str(error)}
        st.repo.append_event(session.run_id, "chat_turn", {"reply": payload.get("reply")})
        asyncio.run_coroutine_threadsafe(send(payload), loop)

    threading.Thread(target=worker, daemon=True).start()


def create_app(state: AppState | None = None) -> FastAPI:
    app = FastAPI(title="Form Agent backend")
    from fastapi.middleware.cors import CORSMiddleware

    # Dev: the side panel calls from a chrome-extension:// origin. The server
    # binds to localhost, so allowing all origins here is safe for local use.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.app_state = state or AppState(
        repo=Repository(make_engine()),
        auth=DevTokenAuth(),
        documents=DocumentStore(),
        facts=FactStore(),
    )

    def get_state() -> AppState:
        return app.state.app_state

    @app.post("/runs")
    def create_run(goal: str = "fill_only", st: AppState = Depends(get_state)) -> dict:
        import uuid

        run_id = f"run-{uuid.uuid4()}"
        st.repo.create_run(run_id, goal=goal)
        st.repo.append_event(run_id, "run_created", {"goal": goal})
        token = st.auth.mint(run_id)
        return {"run_id": run_id, "session_token": token, "goal": goal}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, st: AppState = Depends(get_state)) -> dict:
        run = st.repo.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return {
            "run_id": run.run_id,
            "phase": run.phase,
            "outcome": run.outcome,
            "goal": run.goal,
        }

    @app.get("/runs/{run_id}/events")
    def get_events(run_id: str, st: AppState = Depends(get_state)) -> dict:
        events = st.repo.events(run_id)
        return {
            "run_id": run_id,
            "events": [
                {"seq": e.seq, "kind": e.kind, "at": e.at.isoformat(), "payload": e.payload}
                for e in events
            ],
        }

    @app.post("/runs/{run_id}/documents")
    async def upload_document(
        run_id: str, file: UploadFile, st: AppState = Depends(get_state)
    ) -> dict:
        if st.repo.get_run(run_id) is None:
            raise HTTPException(404, "run not found")
        data = await file.read()
        pipeline = DocumentPipeline(
            store=st.documents, parser=build_parser_from_env(), facts=st.facts
        )
        try:
            record, report, _model_calls = pipeline.ingest(
                data, file.filename or "upload", file.content_type or "application/octet-stream"
            )
        except DocumentRejected as error:
            raise HTTPException(422, str(error)) from error
        st.repo.record_document(
            record.document_id,
            run_id,
            record.filename,
            record.mime_type,
            record.size_bytes,
            record.sha256,
            storage_key=f"docs/{record.document_id}",
        )
        st.repo.append_event(run_id, "document_parsed", {"document_id": record.document_id})
        for fact in report.facts:
            st.repo.append_event(run_id, "fact_extracted", redacted_fact_repr(fact))
        return {
            "document_id": record.document_id,
            "facts": [redacted_fact_repr(f) for f in report.facts],
            "skipped_pages": [q.page for q in report.skipped_pages],
            "needs_review": list(st.facts.needing_review().keys()),
        }

    @app.websocket("/ws/{run_id}")
    async def extension_ws(websocket: WebSocket, run_id: str, token: str = "") -> None:
        st: AppState = websocket.app.state.app_state
        try:
            st.auth.verify(run_id, token)
        except AuthError:
            await websocket.close(code=4401)  # unauthenticated
            return
        await websocket.accept()

        hello = await websocket.receive_json()
        if hello.get("type") != "hello":
            await websocket.close(code=4400)
            return

        async def send(envelope: dict) -> None:
            await websocket.send_json(envelope)

        session = ExtensionSession(
            run_id=run_id,
            origin=hello["origin"],
            tab_id=hello["tab_id"],
            send=send,
        )
        st.sessions[run_id] = session
        st.repo.append_event(run_id, "tab_attached", {"origin": hello["origin"]})
        loop = asyncio.get_event_loop()
        try:
            while True:
                raw = await websocket.receive_json()
                if raw.get("type") == "start_fill":
                    _start_fill(st, session, loop, raw.get("facts", []), send)
                    continue
                if raw.get("type") == "chat":
                    _chat(
                        st,
                        session,
                        loop,
                        raw.get("text", ""),
                        raw.get("facts", []),
                        send,
                        raw.get("history", []),
                    )
                    continue
                try:
                    session.deliver(raw)
                except ProtocolError as error:
                    await websocket.close(code=4400, reason=str(error))
                    break
        except WebSocketDisconnect:
            session.fail_pending(ConnectionError("extension disconnected"))
        finally:
            # Session may reconnect; keep durable state, drop the live handle.
            if st.sessions.get(run_id) is session:
                del st.sessions[run_id]

    return app
