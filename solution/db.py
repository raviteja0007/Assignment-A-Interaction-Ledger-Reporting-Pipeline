"""
Database schema, connection management, and table setup using SQLite with WAL mode.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from solution.config import SCHEMAS_DIR


def get_connection(db_path: Path | str) -> sqlite3.Connection:
    """
    Returns a configured SQLite connection with foreign keys and WAL mode enabled.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """
    Initializes tables, views, indices, triggers, and loads disposition catalogues.
    """
    with conn:
        # Leads table: PII is encrypted/hashed, never stored in plaintext
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                phone_hash TEXT NOT NULL,
                phone_masked TEXT NOT NULL,
                phone_enc TEXT NOT NULL,
                name_enc TEXT NOT NULL,
                external_ref TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                preferred_language TEXT,
                extra_fields TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (workflow_id, phone_hash)
            );
        """)

        # Append-only interaction ledger (strictly immutable event log)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interaction_ledger (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                call_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                occurred_date TEXT NOT NULL,
                phone_hash TEXT,
                payload TEXT DEFAULT '{}',
                disposition_code TEXT,
                is_unknown_disposition INTEGER DEFAULT 0
            );
        """)

        # Enforce append-only constraint at the database engine level
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_ledger_prevent_update
            BEFORE UPDATE ON interaction_ledger
            BEGIN
                SELECT RAISE(FAIL, 'interaction_ledger is strictly append-only: updates are not permitted');
            END;
        """)

        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_ledger_prevent_delete
            BEFORE DELETE ON interaction_ledger
            BEGIN
                SELECT RAISE(FAIL, 'interaction_ledger is strictly append-only: deletes are not permitted');
            END;
        """)

        # Event-to-lead reconciliation links (separated to preserve ledger immutability)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS event_lead_links (
                event_id TEXT PRIMARY KEY,
                lead_id INTEGER NOT NULL,
                linked_via TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (event_id) REFERENCES interaction_ledger (event_id),
                FOREIGN KEY (lead_id) REFERENCES leads (id)
            );
        """)

        # View joining append-only ledger with reconciliation links
        conn.execute("""
            CREATE VIEW IF NOT EXISTS resolved_ledger AS
            SELECT 
                il.event_id,
                il.event_type,
                il.workflow_id,
                il.call_id,
                il.occurred_at,
                il.occurred_date,
                il.phone_hash,
                il.payload,
                il.disposition_code,
                il.is_unknown_disposition,
                ell.lead_id,
                CASE WHEN ell.lead_id IS NOT NULL THEN 0 ELSE 1 END AS is_orphan
            FROM interaction_ledger il
            LEFT JOIN event_lead_links ell ON il.event_id = ell.event_id;
        """)

        # Rejections log
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rejections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                row_number INTEGER NOT NULL,
                reason TEXT NOT NULL,
                file_path TEXT NOT NULL
            );
        """)

        # Disposition catalogue
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dispositions (
                workflow_id TEXT NOT NULL,
                code TEXT NOT NULL,
                label TEXT NOT NULL,
                is_terminal INTEGER NOT NULL,
                is_success INTEGER NOT NULL,
                PRIMARY KEY (workflow_id, code)
            );
        """)

        # Tracking ingested files by SHA-256 content hash to guarantee idempotency
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingested_files (
                file_hash TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                lead_rows_total INTEGER DEFAULT 0,
                lead_rows_rejected INTEGER DEFAULT 0,
                duplicate_lead_rows_merged INTEGER DEFAULT 0,
                field_warnings INTEGER DEFAULT 0,
                events_total INTEGER DEFAULT 0,
                malformed_events INTEGER DEFAULT 0,
                duplicate_events_dropped INTEGER DEFAULT 0,
                ingested_at TEXT NOT NULL
            );
        """)

        # Indices for optimal query and join performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_ref ON leads (workflow_id, external_ref);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_call_id ON interaction_ledger (call_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_phone ON interaction_ledger (workflow_id, phone_hash);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_date ON interaction_ledger (workflow_id, occurred_date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_links_lead ON event_lead_links (lead_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rejections_sort ON rejections (workflow_id, batch_id, row_number);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ingested_files_path ON ingested_files (file_path);")

    # Load disposition catalogue from schemas/dispositions.csv if table is empty
    _load_dispositions(conn)


def _load_dispositions(conn: sqlite3.Connection) -> None:
    disp_csv = SCHEMAS_DIR / "dispositions.csv"
    if not disp_csv.exists():
        return

    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM dispositions")
    if cursor.fetchone()[0] > 0:
        return

    with open(disp_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [
            (
                row["workflow_id"],
                row["code"],
                row["label"],
                1 if row["is_terminal"].strip().lower() == "true" else 0,
                1 if row["is_success"].strip().lower() == "true" else 0,
            )
            for row in reader
        ]
        with conn:
            conn.executemany("""
                INSERT OR IGNORE INTO dispositions (workflow_id, code, label, is_terminal, is_success)
                VALUES (?, ?, ?, ?, ?)
            """, rows)


def load_workflow_schemas() -> dict[str, dict]:
    """
    Loads JSON schemas for each workflow from the schemas directory.
    Returns dict mapping workflow_id -> schema dictionary.
    """
    schemas = {}
    if not SCHEMAS_DIR.exists():
        return schemas

    for schema_file in SCHEMAS_DIR.glob("*.json"):
        wf_name = schema_file.stem
        with open(schema_file, "r", encoding="utf-8") as f:
            schemas[wf_name] = json.load(f)
    return schemas
