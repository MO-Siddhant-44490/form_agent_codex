"""Backend configuration from the environment. Sensible in-memory defaults so
the app boots with zero infrastructure (Slices 1-3); point at Postgres/MinIO
for durable deployments (plan.md §20)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BackendConfig:
    database_url: str
    object_store_root: str
    encryption_key: bytes
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "BackendConfig":
        from ..storage.encrypted import EncryptedLocalStore

        key = os.environ.get("STORAGE_ENCRYPTION_KEY")
        return cls(
            database_url=os.environ.get("DATABASE_URL", "sqlite+pysqlite:///:memory:"),
            object_store_root=os.environ.get("OBJECT_STORE_ROOT", "./_object_store"),
            encryption_key=key.encode() if key else EncryptedLocalStore.generate_key(),
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8000")),
        )
