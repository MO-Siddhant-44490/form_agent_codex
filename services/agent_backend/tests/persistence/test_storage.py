"""Encrypted object store: ciphertext at rest, plaintext round-trips."""

from agent_backend.storage.encrypted import EncryptedLocalStore


def test_encrypts_at_rest_and_round_trips(tmp_path):
    key = EncryptedLocalStore.generate_key()
    store = EncryptedLocalStore(tmp_path, key)
    plaintext = b"%PDF-1.7 sensitive passport content"

    store.put("docs/doc-1", plaintext)
    # On-disk bytes are ciphertext, not the plaintext.
    on_disk = (tmp_path / "docs/doc-1.enc").read_bytes()
    assert plaintext not in on_disk
    assert b"passport" not in on_disk
    # Round-trip recovers the exact plaintext.
    assert store.get("docs/doc-1") == plaintext


def test_wrong_key_cannot_decrypt(tmp_path):
    import pytest
    from cryptography.fernet import InvalidToken

    EncryptedLocalStore(tmp_path, EncryptedLocalStore.generate_key()).put("k", b"data")
    other = EncryptedLocalStore(tmp_path, EncryptedLocalStore.generate_key())
    with pytest.raises(InvalidToken):
        other.get("k")


def test_delete_removes_object(tmp_path):
    store = EncryptedLocalStore(tmp_path, EncryptedLocalStore.generate_key())
    store.put("k", b"data")
    store.delete("k")
    assert not (tmp_path / "k.enc").exists()
