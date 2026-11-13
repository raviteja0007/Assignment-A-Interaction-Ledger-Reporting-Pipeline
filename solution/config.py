"""
Configuration settings for the interaction ledger and reporting pipeline.
"""
from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = BASE_DIR / "assignment-a-data" / "schemas"

# Database path (can be overridden by environment variable or CLI argument)
DEFAULT_DB_PATH = Path(os.getenv("LEDGER_DB_PATH", "ledger.db"))

# Timezone: Naive timestamps and report metrics are in IST
IST = timezone(timedelta(hours=5, minutes=30))

# Environment Secrets:
# 1. LEDGER_HMAC_SECRET: secret salt used for deterministic blind-index hashing of phones.
# 2. LEDGER_ENCRYPTION_KEY: secret key or passphrase for Fernet symmetric PII encryption.
DEFAULT_HMAC_SALT = "agentic_universe_secure_salt_2026"
DEFAULT_ENCRYPTION_SECRET = "agentic_universe_fernet_secret_2026"


def get_hmac_secret() -> bytes:
    """Returns the HMAC secret bytes from environment or secure default."""
    return os.getenv("LEDGER_HMAC_SECRET", DEFAULT_HMAC_SALT).encode("utf-8")


def get_encryption_secret() -> str:
    """Returns the encryption secret string from environment or secure default."""
    return os.getenv("LEDGER_ENCRYPTION_KEY", os.getenv("LEDGER_SECRET_KEY", DEFAULT_ENCRYPTION_SECRET))


# For backwards-compatibility with direct imports
HMAC_SECRET = get_hmac_secret()
