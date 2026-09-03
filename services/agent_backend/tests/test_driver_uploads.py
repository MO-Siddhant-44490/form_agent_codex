"""Driver file-upload pass and the UPLOAD_FILE policy rule."""

import base64

from agent_backend.driver import run_fill
from agent_backend.facts import slice1_facts
from agent_backend.transports.fake import FakeField, FakeTransport
from form_contracts import ActionKind, RunOutcome, UploadFileRef


def resume_ref():
    return UploadFileRef(
        filename="resume.pdf",
        mime_type="application/pdf",
        content_base64=base64.b64encode(b"%PDF-1.7 resume").decode(),
    )


def test_uploads_a_file_to_a_file_field():
    fields = [
        FakeField("name", "text", "full_name", "Full name", required=True),
        FakeField("resume", "file", "resume", "Upload resume", required=True),
    ]
    transport = FakeTransport(fields=fields)
    result = run_fill(transport, slice1_facts(), uploads={"resume": resume_ref()})

    assert result.outcome is RunOutcome.COMPLETED, result.detail
    assert ActionKind.UPLOAD_FILE in transport.received_kinds
    assert "resume" in result.filled_fields
    assert next(f for f in transport.fields if f.field_id == "resume").value == "resume.pdf"


def test_required_file_field_without_an_upload_asks_the_user():
    fields = [
        FakeField("name", "text", "full_name", "Full name", required=True),
        FakeField("resume", "file", "resume", "Upload resume", required=True),
    ]
    transport = FakeTransport(fields=fields)
    result = run_fill(transport, slice1_facts())  # no uploads

    assert result.outcome is RunOutcome.NEEDS_USER
    assert any(q.field_id == "resume" for q in result.questions)
    # The text field was still filled.
    assert "name" in result.filled_fields


def test_upload_content_is_redacted_in_traces():
    from form_contracts import redact_mapping

    action_dump = {
        "kind": "UPLOAD_FILE",
        "upload_file": {"filename": "resume.pdf", "content_base64": "c2VjcmV0"},
    }
    redacted = redact_mapping(action_dump)
    assert redacted["upload_file"]["content_base64"] == "[REDACTED]"
    assert redacted["upload_file"]["filename"] == "resume.pdf"  # filename is fine
