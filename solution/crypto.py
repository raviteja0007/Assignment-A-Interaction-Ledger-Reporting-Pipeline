"""
PII protection utilities.

Fields marked pii: true are never stored in plaintext at rest:
- Phone numbers use a deterministic HMAC-SHA256 blind index for joins and lead identity.
- Raw phone numbers and customer names are encrypted using symmetric authenticated encryption (Fernet).
- Masked phone numbers (+91******XXXX) are stored for client reports.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from solution.config import get_encryption_secret, get_hmac_secret

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

_cached_cipher: Fernet | None = None
_cached_enc_secret: str | None = None


def get_cipher() -> Fernet:
    """
    Returns an initialized Fernet cipher using the encryption secret from environment.
    Derives a standard 32-byte urlsafe-base64 key if the secret is a plain passphrase.
    """
    global _cached_cipher, _cached_enc_secret
    if Fernet is None:
        raise RuntimeError("cryptography package is required for PII encryption at rest.")

    current_secret = get_encryption_secret()
    if _cached_cipher is not None and _cached_enc_secret == current_secret:
        return _cached_cipher

    # If secret is already a valid 32-byte base64-encoded key, test it directly
    try:
        raw_decoded = base64.urlsafe_b64decode(current_secret.encode("utf-8"))
        if len(raw_decoded) == 32:
            _cached_cipher = Fernet(current_secret.encode("utf-8"))
            _cached_enc_secret = current_secret
            return _cached_cipher
    except Exception:
        pass

    # Deterministically derive 32 bytes from the secret passphrase via SHA-256
    derived_32 = hashlib.sha256(current_secret.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(derived_32)
    _cached_cipher = Fernet(fernet_key)
    _cached_enc_secret = current_secret
    return _cached_cipher


def reset_cipher() -> None:
    """Clears cached cipher (used during testing or key rotation)."""
    global _cached_cipher, _cached_enc_secret
    _cached_cipher = None
    _cached_enc_secret = None


def hash_phone(canonical_phone: str) -> str:
    """
    Computes a deterministic HMAC-SHA256 blind index for an Indian canonical phone.
    Used for lead identity and linking call events without storing raw phone.
    """
    if not canonical_phone:
        return ""
    secret = get_hmac_secret()
    return hmac.new(secret, canonical_phone.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def mask_phone(canonical_phone: str) -> str:
    """
    Returns masked phone in format: +91****** followed by last 4 digits.
    Example: +919876543210 -> +91******3210
    """
    if not canonical_phone or len(canonical_phone) < 4:
        return ""
    return f"+91******{canonical_phone[-4:]}"


def encrypt_pii(val: str | None) -> str:
    """
    Encrypts a PII string for storage at rest using authenticated symmetric encryption (Fernet).
    """
    if not val:
        return ""
    cipher = get_cipher()
    return cipher.encrypt(val.encode("utf-8")).decode("utf-8")


def decrypt_pii(token: str | None) -> str:
    """
    Decrypts an encrypted PII token.
    """
    if not token:
        return ""
    cipher = get_cipher()
    try:
        return cipher.decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""
