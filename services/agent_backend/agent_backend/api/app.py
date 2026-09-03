"""FastAPI application: run lifecycle, document upload, audit, and the
authenticated extension WebSocket. Wiring only — orchestration, mapping, and
policy live in their own modules."""

from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from form_contracts import redacted_fact_repr

from ..document_intelligence.fact_store import FactStore
from ..document_intelligence.parser_factory import build_parser_from_env
from ..document_intelligence.pipeline import DocumentPipeline
from ..document_intelligence.store import DocumentRejected, DocumentStore
from ..mapper import DeterministicMapper, Mapper
from ..persistence.repository import Repository, make_engine
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
    sessions: dict[str, ExtensionSession] = field(default_factory=dict)


def create_app(state: AppState | None = None) -> FastAPI:
    app = FastAPI(title="Form Agent backend")
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
        try:
            while True:
                raw = await websocket.receive_json()
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
