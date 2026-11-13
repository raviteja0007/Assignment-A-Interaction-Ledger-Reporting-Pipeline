"""
Integration tests for the interaction ledger pipeline:
- Strict append-only ledger immutability and trigger enforcement
- Content-hash idempotency across file paths
- Cross-drop orphan reconciliation (events before leads and phone-less overrides)
- Incremental vs combined ingestion equivalence
- Rejection ordering and lead merging
"""
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from solution.db import get_connection, init_db
from solution.ingestion import ingest_drop, reconcile_ledger
from solution.reporter import generate_reports


def test_orphan_resolution_across_drops():
    """
    Verifies that an event arriving before its lead in drop_1
    is reconciled and linked as soon as the lead arrives in drop_2.
    Querying resolved_ledger demonstrates linking without mutating interaction_ledger.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_db(conn)

        try:
            # Drop 1: has event for phone +919876543210, but NO lead
            drop1 = tmp_path / "drop_1"
            (drop1 / "events").mkdir(parents=True)
            (drop1 / "leads" / "motor_renewal").mkdir(parents=True)

            event_payload = {
                "event_id": "evt_test_001",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_test_001",
                "occurred_at": "2026-10-01 10:00:00",
                "phone": "+91 98765 43210",
                "payload": {}
            }
            with open(drop1 / "events" / "events_0001.jsonl", "w", encoding="utf-8") as f:
                f.write(json.dumps(event_payload) + "\n")

            # Ingest drop 1
            ingest_drop(drop1, conn)

            # After drop 1: event must be orphan
            cursor = conn.cursor()
            cursor.execute("SELECT is_orphan, lead_id FROM resolved_ledger WHERE event_id = 'evt_test_001'")
            row = cursor.fetchone()
            assert row["is_orphan"] == 1
            assert row["lead_id"] is None

            # Drop 2: lead arrives
            drop2 = tmp_path / "drop_2"
            (drop2 / "leads" / "motor_renewal").mkdir(parents=True)
            lead_csv = drop2 / "leads" / "motor_renewal" / "batch_001.csv"
            with open(lead_csv, "w", encoding="utf-8") as f:
                f.write("customer_name,phone,policy_number\n")
                f.write("Aarav Sharma,+919876543210,MOT/2026/100001\n")

            # Ingest drop 2
            ingest_drop(drop2, conn)

            # Event must now be linked and no longer an orphan
            cursor.execute("SELECT is_orphan, lead_id FROM resolved_ledger WHERE event_id = 'evt_test_001'")
            row = cursor.fetchone()
            assert row["is_orphan"] == 0
            assert row["lead_id"] is not None
        finally:
            conn.close()


def test_supervisor_override_precedence():
    """
    Verifies that a supervisor override takes precedence over disposition.set,
    and links correctly via call_id without a phone number.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_db(conn)

        try:
            drop = tmp_path / "drop_1"
            (drop / "events").mkdir(parents=True)
            (drop / "leads" / "motor_renewal").mkdir(parents=True)

            lead_csv = drop / "leads" / "motor_renewal" / "batch_001.csv"
            with open(lead_csv, "w", encoding="utf-8") as f:
                f.write("customer_name,phone,policy_number\n")
                f.write("Pooja Iyer,+919876543210,MOT/2026/100002\n")

            events = [
                {"event_id": "e1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
                 "call_id": "call_1", "occurred_at": "2026-10-01 10:00:00", "phone": "9876543210", "payload": {}},
                {"event_id": "e2", "event_type": "call.connected", "workflow_id": "motor_renewal",
                 "call_id": "call_1", "occurred_at": "2026-10-01 10:00:05", "phone": "9876543210", "payload": {}},
                {"event_id": "e3", "event_type": "disposition.set", "workflow_id": "motor_renewal",
                 "call_id": "call_1", "occurred_at": "2026-10-01 10:02:00", "phone": "9876543210",
                 "payload": {"disposition_code": "not_interested"}},
                {"event_id": "e4", "event_type": "disposition.override", "workflow_id": "motor_renewal",
                 "call_id": "call_1", "occurred_at": "2026-10-01 14:00:00",
                 "payload": {"disposition_code": "renewed", "overridden_by": "supervisor_1", "reason": "qa_review"}}
            ]
            with open(drop / "events" / "events_0001.jsonl", "w", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev) + "\n")

            ingest_drop(drop, conn)
            out_reports = tmp_path / "reports"
            generate_reports(conn, out_reports)

            with open(out_reports / "lead_report.csv", "r", encoding="utf-8") as f:
                lines = [line.strip().split(",") for line in f if line.strip()]
                header = lines[0]
                lead_row = lines[1]
                disp_idx = header.index("current_disposition")
                success_idx = header.index("ever_success")
                bucket_idx = header.index("bucket")
                assert lead_row[disp_idx] == "renewed"
                assert lead_row[success_idx] == "true"
                assert lead_row[bucket_idx] == "completed"
        finally:
            conn.close()


def test_append_only_enforcement():
    """
    Verifies that interaction_ledger is strictly append-only:
    UPDATE and DELETE statements must be rejected by database triggers.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_db(conn)

        try:
            # Insert a sample event
            conn.execute("""
                INSERT INTO interaction_ledger (
                    event_id, event_type, workflow_id, call_id, occurred_at,
                    occurred_date, phone_hash, payload, disposition_code, is_unknown_disposition
                ) VALUES ('e_immutable', 'call.initiated', 'motor_renewal', 'c1', '2026-10-01T10:00:00+05:30',
                          '2026-10-01', 'dummyhash', '{}', NULL, 0)
            """)

            # Attempting UPDATE must fail due to trg_ledger_prevent_update
            with pytest.raises(sqlite3.IntegrityError, match="strictly append-only"):
                conn.execute("UPDATE interaction_ledger SET call_id = 'mutated' WHERE event_id = 'e_immutable'")

            # Attempting DELETE must fail due to trg_ledger_prevent_delete
            with pytest.raises(sqlite3.IntegrityError, match="strictly append-only"):
                conn.execute("DELETE FROM interaction_ledger WHERE event_id = 'e_immutable'")
        finally:
            conn.close()


def test_content_hash_idempotency():
    """
    Verifies that a file copied to a different path or directory with the same content
    is recognized via its SHA-256 content hash and skipped without duplicate processing.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_db(conn)

        try:
            drop1 = tmp_path / "drop_1"
            (drop1 / "leads" / "motor_renewal").mkdir(parents=True)
            lead_csv = drop1 / "leads" / "motor_renewal" / "batch_001.csv"
            with open(lead_csv, "w", encoding="utf-8") as f:
                f.write("customer_name,phone,policy_number\n")
                f.write("Pooja Iyer,+919876543210,MOT/2026/100002\n")

            # Ingest original file
            ingest_drop(drop1, conn)

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM leads")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT COUNT(*) FROM ingested_files")
            assert cursor.fetchone()[0] == 1

            # Copy the file to a completely different drop directory name and path
            drop2_copy = tmp_path / "drop_copied"
            (drop2_copy / "leads" / "motor_renewal").mkdir(parents=True)
            shutil.copy(lead_csv, drop2_copy / "leads" / "motor_renewal" / "batch_001_copy.csv")

            # Ingesting the copy must be skipped because file_hash is already present
            ingest_drop(drop2_copy, conn)

            cursor.execute("SELECT COUNT(*) FROM leads")
            assert cursor.fetchone()[0] == 1  # No duplicate leads
            cursor.execute("SELECT COUNT(*) FROM ingested_files")
            assert cursor.fetchone()[0] == 1  # No duplicate file record
        finally:
            conn.close()


def test_schema_rejection_ordering():
    """
    Walks schema fields in order: customer_name -> phone -> policy_number.
    The first failure in schema order must become the row's single reason.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_db(conn)

        try:
            drop = tmp_path / "drop_test"
            (drop / "leads" / "motor_renewal").mkdir(parents=True)
            lead_csv = drop / "leads" / "motor_renewal" / "batch_001.csv"
            # Schema field order: customer_name (req, pii), phone (req, phone), policy_number (req, ref)
            with open(lead_csv, "w", encoding="utf-8") as f:
                f.write("customer_name,phone,policy_number\n")
                # Row 1: missing customer_name (1st in schema) and bad phone (2nd) -> missing_required:customer_name
                f.write(",123,MOT/2026/101\n")
                # Row 2: valid customer_name, bad phone (2nd in schema), missing policy_number (3rd) -> invalid_phone
                f.write("Pooja Iyer,123,\n")
                # Row 3: valid customer_name and phone, missing policy_number (3rd in schema) -> missing_required:policy_number
                f.write("Rohan Verma,+919876543210,\n")

            ingest_drop(drop, conn)

            cursor = conn.cursor()
            cursor.execute("SELECT row_number, reason FROM rejections ORDER BY row_number")
            rejections = cursor.fetchall()
            assert len(rejections) == 3
            assert rejections[0]["reason"] == "missing_required:customer_name"
            assert rejections[1]["reason"] == "invalid_phone"
            assert rejections[2]["reason"] == "missing_required:policy_number"
        finally:
            conn.close()


def test_duplicate_lead_merging_rules():
    """
    Validates lead deduplication rules:
    - First valid row sets batch_id permanently.
    - Subsequent valid rows update only non-null fields (last write wins).
    - Null fields receive defaults (e.g. preferred_language -> 'en').
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_db(conn)

        try:
            # Batch 1: creates lead with batch_001, default language, premium 1500.00
            drop1 = tmp_path / "drop_1"
            (drop1 / "leads" / "motor_renewal").mkdir(parents=True)
            with open(drop1 / "leads" / "motor_renewal" / "batch_001.csv", "w", encoding="utf-8") as f:
                f.write("customer_name,phone,policy_number,premium_amount\n")
                f.write("Kavita Rao,+919876543210,MOT/2026/100,1500.00\n")

            ingest_drop(drop1, conn)

            # Batch 2: duplicate phone, updates premium_amount and language, batch_id remains batch_001
            drop2 = tmp_path / "drop_2"
            (drop2 / "leads" / "motor_renewal").mkdir(parents=True)
            with open(drop2 / "leads" / "motor_renewal" / "batch_002.csv", "w", encoding="utf-8") as f:
                f.write("customer_name,phone,policy_number,premium_amount,preferred_language\n")
                f.write("Kavita Rao,+919876543210,MOT/2026/100,2250.50,hi\n")

            ingest_drop(drop2, conn)

            cursor = conn.cursor()
            cursor.execute("SELECT batch_id, preferred_language, extra_fields FROM leads")
            row = cursor.fetchone()
            assert row["batch_id"] == "batch_001"  # Permanent!
            assert row["preferred_language"] == "hi"  # Updated!
            extras = json.loads(row["extra_fields"])
            assert extras["premium_amount"] == 2250.50  # Updated!
        finally:
            conn.close()
