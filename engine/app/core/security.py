"""Secrets at rest: Fernet key management + encrypt/decrypt.

The key lives in the OS keychain (Windows Credential Manager, macOS Keychain, Linux Secret
Service), not next to the database, so whatever can read the data folder (an agent's
shell, say) still can't decrypt the secrets. An existing key file is moved into the
keychain on first start, once the keychain gives it back exactly. Without a keychain (a
headless server, or key_store=file) the key stays in the file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from app.core.config import settings

log = logging.getLogger(__name__)
SERVICE = "Pantheon Studio"
_fernet: Fernet | None = None


def _keychain_name() -> str:
    # One key per data folder (an installed app and a development checkout differ).
    return f"secrets-key:{settings.data_path}"


def _from_keychain() -> bytes | None:
    import keyring

    value = keyring.get_password(SERVICE, _keychain_name())
    return value.encode() if value else None


def _into_keychain(key: bytes) -> bool:
    """Store the key and read it back; False (and nothing changed) when that fails."""
    import keyring

    try:
        keyring.set_password(SERVICE, _keychain_name(), key.decode())
        return _from_keychain() == key
    except Exception as e:  # noqa: BLE001 - no usable keychain: keep the file
        log.warning("the OS keychain isn't usable (%s); the secrets key stays in a file", e)
        return False


def _from_file(key_path: Path) -> bytes:
    if key_path.exists():
        return key_path.read_bytes().strip()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        key_path.chmod(0o600)
    except OSError:  # pragma: no cover - Windows ACLs
        pass
    return key


def _load_key() -> bytes:
    key_path = Path(settings.fernet_key_path)
    if settings.key_store != "keychain":
        return _from_file(key_path)
    try:
        key = _from_keychain()
    except Exception:  # noqa: BLE001 - no keychain backend at all
        key = None
    if key:
        return key
    key = key_path.read_bytes().strip() if key_path.exists() else Fernet.generate_key()
    if _into_keychain(key):
        if key_path.exists():
            key_path.unlink()
            log.info("moved the secrets key into the OS keychain")
        return key
    return _from_file(key_path)


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
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
