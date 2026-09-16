"""FastAPI application: run lifecycle, document upload, audit, and the
authenticated extension WebSocket. Wiring only — the operations the socket
can request live in `operations.py`; orchestration, mapping, and policy live in
their own modules."""

import asyncio
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from form_contracts import redacted_fact_repr

from ..document_intelligence.fact_store import FactStore
from ..document_intelligence.parser_factory import build_parser_from_env
from ..document_intelligence.pipeline import DocumentPipeline
from ..document_intelligence.store import DocumentRejected, DocumentStore
from ..mapper import DeterministicMapper, Mapper
from ..memory import MappingMemory
from ..persistence.repository import Repository, make_engine
from . import operations
from .auth import AuthError, DevTokenAuth
from .session_hub import ExtensionSession, ProtocolError


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

    @app.get("/health")
    def health(st: AppState = Depends(get_state)) -> dict:
        """Readiness for the panel: which model provider is wired and whether
        its credentials work right now, so a fill is never attempted blind."""
        gateway = getattr(st.mapper, "_gateway", None)
        check = getattr(gateway, "check_credentials", None)
        problem = check() if callable(check) else None
        return {
            "status": "ok",
            "provider": type(st.mapper).__name__,
            "model_id": getattr(gateway, "model_id", None),
            "model_ready": problem is None,
            "model_problem": problem,
        }

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
        """Audit-oriented REST upload (values redacted). The panel's interactive
        flow uses the WebSocket `parse_document` operation instead."""
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

        # Panel-initiated operations, by message type. Anything else is a reply
        # to a command the backend sent (observe/execute) and is delivered to
        # the session's pending request.
        panel_ops = {
            "start_fill": lambda m: operations.start_fill(
                st, session, loop, m.get("facts", []), send
            ),
            "chat": lambda m: operations.chat(
                st, session, loop, m.get("text", ""), m.get("facts", []), send, m.get("history", [])
            ),
            "set_field": lambda m: operations.set_field(
                st,
                session,
                loop,
                m.get("field_id", ""),
                m.get("value", ""),
                m.get("facts", []),
                send,
            ),
            "bind": lambda m: operations.bind(
                st, session, loop, m.get("field_id", ""), m.get("key", ""), m.get("facts", []), send
            ),
            "parse_document": lambda m: operations.parse_document(
                st,
                session,
                loop,
                m.get("filename"),
                m.get("mime_type"),
                m.get("content_base64"),
                m.get("facts", []),
                send,
            ),
        }
        try:
            while True:
                raw = await websocket.receive_json()
                op = panel_ops.get(raw.get("type"))
                if op is not None:
                    op(raw)
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
