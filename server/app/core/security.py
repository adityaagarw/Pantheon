"""Secrets at rest: Fernet key management + encrypt/decrypt."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from app.core.config import settings

_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key_path = Path(settings.fernet_key_path)
        if key_path.exists():
            key = key_path.read_bytes().strip()
        else:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            key_path.write_bytes(key)
            try:
                key_path.chmod(0o600)
            except OSError:  # pragma: no cover - Windows ACLs
                pass
        _fernet = Fernet(key)
    return _fernet


def encrypt_secret(plain: str) -> str:
    return get_fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    return get_fernet().decrypt(token.encode()).decode()


def encrypt_json(value: dict[str, Any]) -> str:
    return encrypt_secret(json.dumps(value))


def decrypt_json(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    return json.loads(decrypt_secret(token))


def mask_secret(plain: str) -> str:
    if len(plain) <= 8:
        return "***"
    return plain[:4] + "…" + plain[-4:]
