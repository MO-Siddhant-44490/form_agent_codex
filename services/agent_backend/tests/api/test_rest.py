"""REST surface: run lifecycle, document upload -> redacted facts + audit,
and auth token minting (Module 10)."""

from agent_backend.api.app import create_app
from document_intelligence.pdfs import APPLICATION_LINES, build_pdf
from fastapi.testclient import TestClient


def client():
    return TestClient(create_app())


def test_run_lifecycle_and_session_token():
    c = client()
    resp = c.post("/runs", params={"goal": "fill_and_submit"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"].startswith("run-")
    assert body["session_token"]  # per-run shared secret minted
    assert body["goal"] == "fill_and_submit"

    got = c.get(f"/runs/{body['run_id']}").json()
    assert got["phase"] == "ingest_documents"
    assert got["outcome"] is None


def test_unknown_run_404():
    assert client().get("/runs/run-missing").status_code == 404


def test_document_upload_returns_redacted_facts_and_writes_audit():
    c = client()
    run_id = c.post("/runs").json()["run_id"]
    resp = c.post(
        f"/runs/{run_id}/documents",
        files={"file": ("application.pdf", build_pdf(APPLICATION_LINES), "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"].startswith("doc-")
    keys = {f["key"] for f in body["facts"]}
    assert {"full_name", "email", "date_of_birth"} <= keys
    # Personal fact values are redacted in the API response and audit log.
    name_fact = next(f for f in body["facts"] if f["key"] == "full_name")
    assert name_fact["value"] == "[REDACTED]"

    events = c.get(f"/runs/{run_id}/events").json()["events"]
    kinds = [e["kind"] for e in events]
    assert "document_parsed" in kinds
    assert "fact_extracted" in kinds
    # No raw name leaked into any audit payload.
    import json as _json

    assert "Ada Lovelace" not in _json.dumps(events)


def test_bad_document_rejected_422():
    c = client()
    run_id = c.post("/runs").json()["run_id"]
    resp = c.post(
        f"/runs/{run_id}/documents",
        files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
    )
    assert resp.status_code == 422


def test_health_reports_provider_and_model_readiness():
    from agent_backend.api.app import AppState, create_app
    from agent_backend.api.auth import DevTokenAuth
    from agent_backend.document_intelligence.fact_store import FactStore
    from agent_backend.document_intelligence.store import DocumentStore
    from agent_backend.mapper import ModelAssistedMapper
    from agent_backend.model_gateway.fake import FakeModelAdapter
    from agent_backend.persistence.repository import Repository, make_engine
    from fastapi.testclient import TestClient

    class Unready(FakeModelAdapter):
        def check_credentials(self):
            return "no AWS credentials found"

    for gateway, ready in ((FakeModelAdapter(), True), (Unready(), False)):
        app = create_app(
            AppState(
                repo=Repository(make_engine()),
                auth=DevTokenAuth(),
                documents=DocumentStore(),
                facts=FactStore(),
                mapper=ModelAssistedMapper(gateway),
            )
        )
        body = TestClient(app).get("/health").json()
        assert body["provider"] == "ModelAssistedMapper"
        assert body["model_ready"] is ready
        assert (body["model_problem"] is None) is ready
