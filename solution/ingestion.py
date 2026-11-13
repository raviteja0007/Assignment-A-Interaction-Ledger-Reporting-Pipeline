"""
Ingestion engine for lead batches and event streams into the database.
Handles validation, normalization, deduplication, blind indexing, content-hash idempotency,
and strict append-only orphan reconciliation.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from solution.config import IST
from solution.crypto import encrypt_pii, hash_phone, mask_phone
from solution.db import load_workflow_schemas
from solution.normalizer import (
    build_header_mapping,
    normalize_phone,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_timestamp,
)


def ingest_drop(drop_dir: Path | str, conn: sqlite3.Connection) -> None:
    """
    Ingests a single drop directory containing leads/ and events/.
    """
    drop_path = Path(drop_dir).resolve()
    if not drop_path.exists():
        raise FileNotFoundError(f"Drop directory does not exist: {drop_path}")

    schemas = load_workflow_schemas()

    # 1. Ingest Leads
    leads_dir = drop_path / "leads"
    if leads_dir.exists():
        # Process workflows and batches in deterministic order
        for wf_dir in sorted(leads_dir.iterdir()):
            if not wf_dir.is_dir():
                continue
            wf_name = wf_dir.name
            schema = schemas.get(wf_name)
            if not schema:
                continue

            for batch_file in sorted(wf_dir.glob("*.csv")):
                _ingest_lead_file(batch_file, wf_name, schema, conn)

    # 2. Ingest Events
    events_dir = drop_path / "events"
    if events_dir.exists():
        for event_file in sorted(events_dir.glob("*.jsonl")):
            _ingest_event_file(event_file, conn)

    # 3. Reconcile orphans (links newly arrived leads to previously or newly arrived events)
    reconcile_ledger(conn)


def _ingest_lead_file(
    file_path: Path,
    workflow_id: str,
    schema: dict,
    conn: sqlite3.Connection,
) -> None:
    norm_path = str(file_path.resolve())

    # Content-hash idempotency check: read file bytes and hash with SHA-256
    with open(file_path, "rb") as bf:
        content_bytes = bf.read()
    file_hash = hashlib.sha256(content_bytes).hexdigest()

    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM ingested_files WHERE file_hash = ?", (file_hash,))
    if cursor.fetchone():
        return  # Already ingested with identical content hash

    batch_id = file_path.stem
    schema_fields = schema["fields"]
    ref_field = next(f["name"] for f in schema_fields if f.get("reference"))

    lead_rows_total = 0
    lead_rows_rejected = 0
    duplicate_lead_rows_merged = 0
    field_warnings = 0

    rejections_to_insert = []
    leads_to_insert = []
    leads_to_update = []

    # Cache existing leads for this workflow: phone_hash -> lead record dict
    cursor.execute("""
        SELECT id, phone_hash, external_ref, preferred_language, extra_fields
        FROM leads
        WHERE workflow_id = ?
    """, (workflow_id,))
    existing_leads = {
        row["phone_hash"]: {
            "id": row["id"],
            "external_ref": row["external_ref"],
            "preferred_language": row["preferred_language"],
            "extra_fields": json.loads(row["extra_fields"] or "{}"),
        }
        for row in cursor.fetchall()
    }

    now_iso = datetime.now(tz=IST).isoformat()

    # Read CSV text from content_bytes
    text_stream = io.StringIO(content_bytes.decode("utf-8-sig"))
    reader = csv.reader(text_stream)
    try:
        raw_headers = next(reader)
    except StopIteration:
        return  # Empty file

    header_map = build_header_mapping(raw_headers, schema_fields)

    file_row_num = 0
    for raw_row in reader:
        # Blank rows (every cell empty or whitespace) are skipped and not counted
        if not raw_row or all(c.strip() == "" for c in raw_row):
            continue

        file_row_num += 1
        lead_rows_total += 1

        row_dict = {
            header_map[raw_headers[i]]: raw_row[i].strip()
            for i in range(min(len(raw_headers), len(raw_row)))
            if raw_headers[i] in header_map and raw_row[i].strip() != ""
        }

        # Field validation in schema order
        rejection_reason = None
        for field in schema_fields:
            fname = field["name"]
            val = row_dict.get(fname)
            is_required = field.get("required", False)

            if is_required and (val is None or val == ""):
                rejection_reason = f"missing_required:{fname}"
                break

            if field["type"] == "phone" and val:
                canon_p = normalize_phone(val)
                if canon_p is None:
                    rejection_reason = "invalid_phone"
                    break

        if rejection_reason:
            rejections_to_insert.append((workflow_id, batch_id, file_row_num, rejection_reason, norm_path))
            lead_rows_rejected += 1
            continue

        # Non-rejected row: parse fields and count warnings on optional invalid fields
        parsed_row = {}
        for field in schema_fields:
            fname = field["name"]
            ftype = field["type"]
            raw_val = row_dict.get(fname)

            if raw_val is None or raw_val == "":
                parsed_row[fname] = None
                continue

            if ftype == "phone":
                parsed_row[fname] = normalize_phone(raw_val)
            elif ftype == "date":
                dt_val = parse_date(raw_val)
                if dt_val is None:
                    field_warnings += 1
                parsed_row[fname] = dt_val
            elif ftype == "decimal":
                num_val = parse_decimal(raw_val)
                if num_val is None:
                    field_warnings += 1
                parsed_row[fname] = num_val
            elif ftype == "enum":
                enum_val = parse_enum(raw_val, field.get("values", []))
                if enum_val is None:
                    field_warnings += 1
                parsed_row[fname] = enum_val
            else:
                parsed_row[fname] = raw_val

        canonical_phone = parsed_row["phone"]
        p_hash = hash_phone(canonical_phone)

        # Check for duplicates
        if p_hash in existing_leads:
            duplicate_lead_rows_merged += 1
            lead_info = existing_leads[p_hash]

            # Last write wins on non-null fields
            if parsed_row.get("preferred_language") is not None:
                lead_info["preferred_language"] = parsed_row["preferred_language"]

            for field in schema_fields:
                fname = field["name"]
                if fname in ("phone", "customer_name", "preferred_language"):
                    continue
                if parsed_row.get(fname) is not None:
                    if fname == ref_field:
                        lead_info["external_ref"] = parsed_row[fname]
                    else:
                        lead_info["extra_fields"][fname] = parsed_row[fname]

            leads_to_update.append((
                lead_info["external_ref"],
                lead_info["preferred_language"],
                json.dumps(lead_info["extra_fields"]),
                now_iso,
                lead_info["id"],
            ))
        else:
            # Apply defaults for null fields
            pref_lang = parsed_row.get("preferred_language")
            lang_default = next((f.get("default") for f in schema_fields if f["name"] == "preferred_language"), "en")
            if pref_lang is None:
                pref_lang = lang_default

            extra_dict = {}
            for field in schema_fields:
                fname = field["name"]
                if fname in ("phone", "customer_name", "preferred_language", ref_field):
                    continue
                extra_dict[fname] = parsed_row.get(fname)

            new_record = {
                "workflow_id": workflow_id,
                "phone_hash": p_hash,
                "phone_masked": mask_phone(canonical_phone),
                "phone_enc": encrypt_pii(canonical_phone),
                "name_enc": encrypt_pii(parsed_row.get("customer_name")),
                "external_ref": parsed_row.get(ref_field, ""),
                "batch_id": batch_id,
                "preferred_language": pref_lang,
                "extra_fields": extra_dict,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            leads_to_insert.append(new_record)
            # Keep cache updated in case of duplicates within the same file
            existing_leads[p_hash] = {
                "id": None,  # Will be set in DB
                "external_ref": new_record["external_ref"],
                "preferred_language": pref_lang,
                "extra_fields": extra_dict,
            }

    # Atomic write to DB
    with conn:
        if rejections_to_insert:
            conn.executemany("""
                INSERT INTO rejections (workflow_id, batch_id, row_number, reason, file_path)
                VALUES (?, ?, ?, ?, ?)
            """, rejections_to_insert)

        for lead in leads_to_insert:
            cursor.execute("""
                INSERT INTO leads (
                    workflow_id, phone_hash, phone_masked, phone_enc, name_enc,
                    external_ref, batch_id, preferred_language, extra_fields,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lead["workflow_id"],
                lead["phone_hash"],
                lead["phone_masked"],
                lead["phone_enc"],
                lead["name_enc"],
                lead["external_ref"],
                lead["batch_id"],
                lead["preferred_language"],
                json.dumps(lead["extra_fields"]),
                lead["created_at"],
                lead["updated_at"],
            ))
            lead_id = cursor.lastrowid
            existing_leads[lead["phone_hash"]]["id"] = lead_id

        if leads_to_update:
            conn.executemany("""
                UPDATE leads
                SET external_ref = ?, preferred_language = ?, extra_fields = ?, updated_at = ?
                WHERE id = ?
            """, leads_to_update)

        conn.execute("""
            INSERT INTO ingested_files (
                file_hash, file_path, file_type, lead_rows_total, lead_rows_rejected,
                duplicate_lead_rows_merged, field_warnings, ingested_at
            ) VALUES (?, ?, 'lead', ?, ?, ?, ?, ?)
        """, (
            file_hash,
            norm_path,
            lead_rows_total,
            lead_rows_rejected,
            duplicate_lead_rows_merged,
            field_warnings,
            now_iso,
        ))


def _ingest_event_file(file_path: Path, conn: sqlite3.Connection) -> None:
    norm_path = str(file_path.resolve())

    # Content-hash idempotency check
    with open(file_path, "rb") as bf:
        content_bytes = bf.read()
    file_hash = hashlib.sha256(content_bytes).hexdigest()

    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM ingested_files WHERE file_hash = ?", (file_hash,))
    if cursor.fetchone():
        return  # Already ingested

    # Load catalogue of valid dispositions per workflow
    cursor.execute("SELECT workflow_id, code FROM dispositions")
    valid_dispositions = {(r[0], r[1]) for r in cursor.fetchall()}

    # Cache known event_ids to drop duplicates across drops
    cursor.execute("SELECT event_id FROM interaction_ledger")
    seen_event_ids = {r[0] for r in cursor.fetchall()}

    events_total = 0
    malformed_events = 0
    duplicate_events_dropped = 0
    events_to_insert = []

    text_stream = io.StringIO(content_bytes.decode("utf-8", errors="replace"))
    for line in text_stream:
        line_str = line.strip()
        if not line_str:
            continue

        events_total += 1

        # 1. Malformed check
        try:
            data = json.loads(line_str)
        except Exception:
            malformed_events += 1
            continue

        if not isinstance(data, dict):
            malformed_events += 1
            continue

        eid = data.get("event_id")
        if not eid:
            malformed_events += 1
            continue

        # 2. Duplicate check
        if eid in seen_event_ids:
            duplicate_events_dropped += 1
            continue
        seen_event_ids.add(eid)

        # Valid JSON & new event
        etype = data.get("event_type", "")
        wf = data.get("workflow_id", "")
        cid = data.get("call_id", "")
        occurred_raw = data.get("occurred_at", "")
        payload = data.get("payload", {}) or {}

        # Parse timestamp to IST
        try:
            dt_ist = parse_timestamp(occurred_raw)
            occurred_at = dt_ist.isoformat()
            occurred_date = dt_ist.strftime("%Y-%m-%d")
        except Exception:
            malformed_events += 1
            continue

        # Phone hash
        phone_val = data.get("phone")
        p_hash = None
        if phone_val:
            canon_p = normalize_phone(phone_val)
            if canon_p:
                p_hash = hash_phone(canon_p)

        # Disposition code check
        disp_code = payload.get("disposition_code") if etype.startswith("disposition.") else None
        is_unknown_disp = 0
        if etype.startswith("disposition."):
            if (wf, disp_code) not in valid_dispositions:
                is_unknown_disp = 1

        events_to_insert.append((
            eid,
            etype,
            wf,
            cid,
            occurred_at,
            occurred_date,
            p_hash,
            json.dumps(payload),
            disp_code,
            is_unknown_disp,
        ))

    now_iso = datetime.now(tz=IST).isoformat()
    with conn:
        if events_to_insert:
            # Strictly append-only insert (never updates existing ledger rows)
            conn.executemany("""
                INSERT INTO interaction_ledger (
                    event_id, event_type, workflow_id, call_id, occurred_at,
                    occurred_date, phone_hash, payload, disposition_code,
                    is_unknown_disposition
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, events_to_insert)

        conn.execute("""
            INSERT INTO ingested_files (
                file_hash, file_path, file_type, events_total, malformed_events,
                duplicate_events_dropped, ingested_at
            ) VALUES (?, ?, 'event', ?, ?, ?, ?)
        """, (
            file_hash,
            norm_path,
            events_total,
            malformed_events,
            duplicate_events_dropped,
            now_iso,
        ))


def reconcile_ledger(conn: sqlite3.Connection) -> None:
    """
    Links unlinked events to leads via the event_lead_links table.
    Strictly preserves the append-only immutability of interaction_ledger:
    NEVER performs an UPDATE on interaction_ledger.
    """
    now_iso = datetime.now(tz=IST).isoformat()
    with conn:
        # 1. Link events with phone_hash to leads
        conn.execute("""
            INSERT OR IGNORE INTO event_lead_links (event_id, lead_id, linked_via, created_at)
            SELECT il.event_id, l.id, 'phone_hash', ?
            FROM interaction_ledger il
            JOIN leads l ON il.workflow_id = l.workflow_id AND il.phone_hash = l.phone_hash
            WHERE il.phone_hash IS NOT NULL
              AND il.event_id NOT IN (SELECT event_id FROM event_lead_links);
        """, (now_iso,))

        # 2. Link events without phone (e.g. disposition.override) through call_id
        conn.execute("""
            INSERT OR IGNORE INTO event_lead_links (event_id, lead_id, linked_via, created_at)
            SELECT il.event_id, other_links.lead_id, 'call_id', ?
            FROM interaction_ledger il
            JOIN interaction_ledger other_ev ON il.call_id = other_ev.call_id AND il.event_id != other_ev.event_id
            JOIN event_lead_links other_links ON other_ev.event_id = other_links.event_id
            WHERE il.event_id NOT IN (SELECT event_id FROM event_lead_links);
        """, (now_iso,))
