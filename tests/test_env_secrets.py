"""
Unit tests for environment secrets, key derivation, and PII protection at rest.
"""
import os
import sqlite3
import tempfile
from pathlib import Path

from solution.config import get_encryption_secret, get_hmac_secret
from solution.crypto import decrypt_pii, encrypt_pii, hash_phone, reset_cipher
from solution.db import get_connection, init_db
from solution.ingestion import ingest_drop


def test_custom_environment_secrets(monkeypatch):
    """
    Verifies that LEDGER_HMAC_SECRET and LEDGER_ENCRYPTION_KEY can be set via env,
    and changing them produces distinct HMAC blind indices and ciphertexts.
    """
    phone = "+919876543210"

    # Default secret
    monkeypatch.delenv("LEDGER_HMAC_SECRET", raising=False)
    monkeypatch.delenv("LEDGER_ENCRYPTION_KEY", raising=False)
    reset_cipher()
    h1 = hash_phone(phone)
    token1 = encrypt_pii("Sensitive Name")

    # Override secrets via env
    monkeypatch.setenv("LEDGER_HMAC_SECRET", "custom_test_hmac_secret_999")
    monkeypatch.setenv("LEDGER_ENCRYPTION_KEY", "custom_test_encryption_secret_888")
    reset_cipher()

    h2 = hash_phone(phone)
    token2 = encrypt_pii("Sensitive Name")

    assert h1 != h2, "Custom HMAC secret must alter the blind index hash"
    assert token1 != token2, "Custom encryption secret must alter the ciphertext"

    # Must cleanly decrypt with the active secret
    assert decrypt_pii(token2) == "Sensitive Name"

    # Reset back
    reset_cipher()


def test_pii_never_stored_in_plaintext_at_rest():
    """
    Verifies that raw phone number and customer name are never written to disk in plaintext.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_db(conn)

        raw_phone = "+919876543210"
        raw_name = "Aditya Narayan"

        drop = tmp_path / "drop_1"
        (drop / "leads" / "motor_renewal").mkdir(parents=True)
        lead_csv = drop / "leads" / "motor_renewal" / "batch_001.csv"
        with open(lead_csv, "w", encoding="utf-8") as f:
            f.write("customer_name,phone,policy_number\n")
            f.write(f"{raw_name},{raw_phone},MOT/2026/001\n")

        ingest_drop(drop, conn)

        # Inspect raw SQLite database columns
        cursor = conn.cursor()
        cursor.execute("SELECT phone_masked, phone_enc, name_enc FROM leads")
        row = cursor.fetchone()
        masked_phone, phone_enc, name_enc = row["phone_masked"], row["phone_enc"], row["name_enc"]

        # Plaintext phone must NOT be in the table
        assert raw_phone not in (masked_phone, phone_enc)
        assert raw_name not in name_enc

        # Only masked representation (+91******XXXX) exists in readable form
        assert masked_phone == "+91******3210"

        # Encrypted columns must safely decrypt with decrypt_pii
        assert decrypt_pii(phone_enc) == raw_phone
        assert decrypt_pii(name_enc) == raw_name

        conn.close()

