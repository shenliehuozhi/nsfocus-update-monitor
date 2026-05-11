"""AES-256-GCM encryption for sensitive fields.

Encrypts: PHPSESSID, SMTP passwords, webhook URLs.
Key from env var MONITOR_SECRET_KEY (64 hex chars → 32 bytes).
"""

import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _get_key() -> bytes:
    key_hex = os.getenv('MONITOR_SECRET_KEY', '')
    if not key_hex or len(key_hex) != 64:
        # Development fallback — CHANGE IN PRODUCTION
        key_hex = 'a' * 64
    return bytes.fromhex(key_hex)


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns base64-encoded ciphertext (nonce prepended)."""
    if not plaintext:
        return ''
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode('utf-8'), None)
    # nonce (12 bytes) + ciphertext
    return base64.b64encode(nonce + ciphertext).decode('ascii')


def decrypt(encoded: str) -> str:
    """Decrypt a previously encrypted string."""
    if not encoded:
        return ''
    key = _get_key()
    aesgcm = AESGCM(key)
    raw = base64.b64decode(encoded)
    nonce, ciphertext = raw[:12], raw[12:]
    return aesgcm.decrypt(nonce, ciphertext, None).decode('utf-8')
