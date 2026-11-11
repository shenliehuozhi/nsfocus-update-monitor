"""AES-256-GCM encryption for sensitive fields.

Encrypts: PHPSESSID, SMTP passwords, webhook URLs.
Key from env var MONITOR_SECRET_KEY (64 hex chars → 32 bytes).
"""

import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _get_key_hex() -> str:
    key_hex = os.getenv('MONITOR_SECRET_KEY', '')
    if not key_hex or len(key_hex) != 64:
        # Development fallback — CHANGE IN PRODUCTION
        key_hex = 'a' * 64
    return key_hex


def _key_bytes(key_hex: str) -> bytes:
    """Validate an explicit 64-hex key (used by rotation, no silent fallback)."""
    if not key_hex or len(key_hex) != 64:
        raise ValueError('key must be 64 hex chars')
    try:
        return bytes.fromhex(key_hex)
    except ValueError:
        raise ValueError('key must be 64 hex chars')


def _get_key() -> bytes:
    return bytes.fromhex(_get_key_hex())


def encrypt_with_key(plaintext: str, key_hex: str) -> str:
    if not plaintext:
        return ''
    aesgcm = AESGCM(_key_bytes(key_hex))
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode('utf-8'), None)
    # nonce (12 bytes) + ciphertext
    return base64.b64encode(nonce + ciphertext).decode('ascii')


def decrypt_with_key(encoded: str, key_hex: str) -> str:
    if not encoded:
        return ''
    aesgcm = AESGCM(_key_bytes(key_hex))
    raw = base64.b64decode(encoded)
    nonce, ciphertext = raw[:12], raw[12:]
    return aesgcm.decrypt(nonce, ciphertext, None).decode('utf-8')


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns base64-encoded ciphertext (nonce prepended)."""
    return encrypt_with_key(plaintext, _get_key_hex())


def decrypt(encoded: str) -> str:
    """Decrypt a previously encrypted string."""
    return decrypt_with_key(encoded, _get_key_hex())
