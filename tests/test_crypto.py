"""
Unit tests for PII security: HMAC blind indexing, masking, and encryption at rest.
"""
from solution.crypto import decrypt_pii, encrypt_pii, hash_phone, mask_phone


def test_hash_phone_deterministic():
    phone = "+919876543210"
    h1 = hash_phone(phone)
    h2 = hash_phone(phone)
    assert len(h1) == 64
    assert h1 == h2
    assert h1 != hash_phone("+919876543211")


def test_mask_phone():
    assert mask_phone("+919876543210") == "+91******3210"
    assert mask_phone("+917718750036") == "+91******0036"
    assert mask_phone("") == ""


def test_encrypt_decrypt_roundtrip():
    secret_text = "Vivaan Sharma"
    token = encrypt_pii(secret_text)
    assert token != secret_text
    decrypted = decrypt_pii(token)
    assert decrypted == secret_text

