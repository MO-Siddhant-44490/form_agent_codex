import pytest
from agent_backend.document_intelligence.store import DocumentRejected, DocumentStore

from .pdfs import build_pdf

PDF = build_pdf(["Hello: world"])


def test_upload_assigns_content_derived_identity():
    store = DocumentStore()
    record = store.upload(PDF, "a.pdf", "application/pdf")
    again = store.upload(PDF, "copy-of-a.pdf", "application/pdf")
    assert record.document_id == again.document_id  # same bytes, same identity
    assert record.document_id.startswith("doc-")
    assert store.blob(record.document_id) == PDF


def test_oversized_document_rejected():
    store = DocumentStore(max_bytes=10)
    with pytest.raises(DocumentRejected, match="exceeds"):
        store.upload(PDF, "a.pdf", "application/pdf")


def test_empty_document_rejected():
    with pytest.raises(DocumentRejected, match="empty"):
        DocumentStore().upload(b"", "a.pdf", "application/pdf")


def test_mime_mismatch_rejected():
    with pytest.raises(DocumentRejected, match="does not match"):
        DocumentStore().upload(PDF, "a.png", "image/png")


def test_unsupported_type_rejected():
    with pytest.raises(DocumentRejected, match="unsupported"):
        DocumentStore().upload(b"MZ\x90\x00", "a.exe", "application/x-msdownload")
