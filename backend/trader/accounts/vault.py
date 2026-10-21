"""Encrypted storage for a customer's broker API keys. Fernet (AES-128-CBC + HMAC) with a key from CREDENTIAL_KEY.

The key lives in the environment, the ciphertext in the database, so a database leak alone reveals nothing. Secrets are
write-only: no route returns them, and nothing here logs them.
"""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken


class VaultUnavailable(RuntimeError):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


def _fernet(key: str) -> Fernet:
    if not key:
        raise VaultUnavailable("Saving broker keys needs CREDENTIAL_KEY on the server (see docs/pricing-and-saas.md).")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise VaultUnavailable("CREDENTIAL_KEY is not a valid Fernet key.") from exc


def seal(values: dict[str, str], key: str) -> str:
    return _fernet(key).encrypt(json.dumps(values).encode()).decode()


def unseal(blob: str, key: str) -> dict[str, str]:
    try:
        return json.loads(_fernet(key).decrypt(blob.encode()))
    except InvalidToken as exc:
        raise VaultUnavailable("Stored keys cannot be read with the current CREDENTIAL_KEY.") from exc
