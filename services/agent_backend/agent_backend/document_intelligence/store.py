"""Document upload and identity. In-memory for Slices 1-3 (plan.md §4.1);
encrypted S3/MinIO arrives with Slice 4."""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
ALLOWED_MIME_TYPES = frozenset({"application/pdf", "image/png", "image/jpeg"})

_MAGIC = {
    b"%PDF": "application/pdf",
    b"\x89PNG": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


class DocumentRejected(ValueError):
    """Upload refused: size, type, or content sniff failed."""


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime


def sniff_mime(data: bytes) -> str | None:
    for magic, mime in _MAGIC.items():
        if data.startswith(magic):
            return mime
    return None


@dataclass
class DocumentStore:
    max_bytes: int = MAX_DOCUMENT_BYTES
    _records: dict[str, DocumentRecord] = field(default_factory=dict)
    _blobs: dict[str, bytes] = field(default_factory=dict)

    def upload(self, data: bytes, filename: str, declared_mime: str) -> DocumentRecord:
        if len(data) == 0:
            raise DocumentRejected("empty document")
        if len(data) > self.max_bytes:
            raise DocumentRejected(f"document exceeds {self.max_bytes} bytes")
        if declared_mime not in ALLOWED_MIME_TYPES:
            raise DocumentRejected(f"unsupported mime type {declared_mime}")
        sniffed = sniff_mime(data)
        if sniffed != declared_mime:
            raise DocumentRejected(
                f"content does not match declared type ({declared_mime}; sniffed {sniffed})"
            )
        digest = hashlib.sha256(data).hexdigest()
        document_id = f"doc-{digest[:12]}"
        record = DocumentRecord(
            document_id=document_id,
            filename=filename,
            mime_type=declared_mime,
            size_bytes=len(data),
            sha256=digest,
            uploaded_at=datetime.now(UTC),
        )
        self._records[document_id] = record
        self._blobs[document_id] = data
        return record

    def get(self, document_id: str) -> DocumentRecord:
        return self._records[document_id]

    def blob(self, document_id: str) -> bytes:
        return self._blobs[document_id]
