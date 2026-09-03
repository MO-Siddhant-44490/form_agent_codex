"""Encryption-at-rest wrapper (Fernet/AES). The key comes from configuration
(a KMS-derived key in production); plaintext bytes never persist."""

from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class EncryptedLocalStore:
    """Local filesystem store with per-object encryption. Development and
    single-node deployments; swap for EncryptedS3Store in cloud."""

    def __init__(self, root: Path, key: bytes) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(key)

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._fernet.encrypt(data))
        return key

    def get(self, key: str) -> bytes:
        return self._fernet.decrypt(self._path(key).read_bytes())

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("..", "_").lstrip("/")
        return self._root / f"{safe}.enc"

    @staticmethod
    def generate_key() -> bytes:
        return Fernet.generate_key()


class EncryptedS3Store:
    """S3/MinIO store with the same per-object encryption. boto3 is optional
    (server extra); import is lazy."""

    def __init__(self, bucket: str, key: bytes, client=None, **client_kwargs) -> None:
        self._bucket = bucket
        self._fernet = Fernet(key)
        self._client = client
        self._client_kwargs = client_kwargs

    def _ensure_client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("s3", **self._client_kwargs)
        return self._client

    def put(self, key: str, data: bytes) -> str:
        self._ensure_client().put_object(
            Bucket=self._bucket, Key=key, Body=self._fernet.encrypt(data)
        )
        return key

    def get(self, key: str) -> bytes:
        response = self._ensure_client().get_object(Bucket=self._bucket, Key=key)
        return self._fernet.decrypt(response["Body"].read())

    def delete(self, key: str) -> None:
        self._ensure_client().delete_object(Bucket=self._bucket, Key=key)
