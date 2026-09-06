"""Backend configuration from the environment. Zero-infrastructure defaults
(local SQLite file + local object store) so the app boots durably with no
setup; point DATABASE_URL at Postgres/MinIO for a real deployment (plan.md
§20)."""

import os
from dataclasses import dataclass

# File-backed by default so cross-run episodic memory (and the audit log,
# idempotency, approvals) survive a restart. Relative to the working directory;
# override with DATABASE_URL for Postgres or a fixed absolute path.
DEFAULT_DATABASE_URL = "sqlite+pysqlite:///./_agent_state/agent_backend.db"


@dataclass(frozen=True)
class BackendConfig:
    database_url: str
    object_store_root: str
    encryption_key: bytes
    host: str
    port: int
    model_provider: str
    bedrock_model_id: str

    @classmethod
    def from_env(cls) -> "BackendConfig":
        from ..storage.encrypted import EncryptedLocalStore

        key = os.environ.get("STORAGE_ENCRYPTION_KEY")
        from ..model_gateway.factory import DEFAULT_BEDROCK_MODEL_ID

        return cls(
            database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            object_store_root=os.environ.get("OBJECT_STORE_ROOT", "./_object_store"),
            encryption_key=key.encode() if key else EncryptedLocalStore.generate_key(),
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8000")),
            model_provider=os.environ.get("MODEL_PROVIDER", "bedrock"),
            bedrock_model_id=os.environ.get("BEDROCK_MODEL_ID", DEFAULT_BEDROCK_MODEL_ID),
        )
