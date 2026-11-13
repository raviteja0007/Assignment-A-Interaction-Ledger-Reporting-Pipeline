"""
Generates the five report files strictly adhering to the output contract:
1. batch_summary.csv
2. daily_overview.csv
3. lead_report.csv
4. rejections.csv
5. data_quality.json
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path


def generate_reports(conn: sqlite3.Connection, out_dir: Path | str) -> None:
    """
    Computes metrics and writes all five report files to out_dir.
    """
    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Compute call-level facts
    calls = _compute_call_facts(conn)

    # 2. Compute lead-level facts
    lead_facts = _compute_lead_facts(conn, calls)

    # 3. Write batch_summary.csv
    _write_batch_summary(conn, lead_facts, out_path / "batch_summary.csv")

    # 4. Write daily_overview.csv
    _write_daily_overview(conn, calls, out_path / "daily_overview.csv")

    # 5. Write lead_report.csv
    _write_lead_report(conn, lead_facts, out_path / "lead_report.csv")

    # 6. Write rejections.csv
    _write_rejections(conn, out_path / "rejections.csv")

    # 7. Write data_quality.json
    _write_data_quality(conn, out_path / "data_quality.json")


def _compute_call_facts(conn: sqlite3.Connection) -> dict[str, dict]:
    """
    Returns dict mapping call_id -> dict of call facts.
    Only counts calls that have a linked call.initiated event.
    """
    cursor = conn.cursor()

    # Load catalogue mapping (workflow_id, code) -> (is_terminal, is_success)
    cursor.execute("SELECT workflow_id, code, is_terminal, is_success FROM dispositions")
    disp_cat = {(r[0], r[1]): (bool(r[2]), bool(r[3])) for r in cursor.fetchall()}

    # Find all call_ids with a linked call.initiated event
    cursor.execute("""
        SELECT call_id, workflow_id, lead_id, occurred_at, occurred_date
        FROM resolved_ledger
        WHERE event_type = 'call.initiated' AND lead_id IS NOT NULL AND is_orphan = 0
        ORDER BY occurred_at ASC
    """)
    call_initiations = {}
    for r in cursor.fetchall():
        cid = r["call_id"]
        if cid not in call_initiations:
            call_initiations[cid] = {
                "call_id": cid,
                "workflow_id": r["workflow_id"],
                "lead_id": r["lead_id"],
                "initiated_at": r["occurred_at"],
                "initiated_date": r["occurred_date"],
                "connected": False,
                "telephony_failed": False,
                "disposition_events": [],
            }

    if not call_initiations:
        return {}

    # Fetch all events for these calls
    call_ids = list(call_initiations.keys())
    # Query in chunks to avoid parameter limits
    chunk_size = 900
    all_events = []
    for i in range(0, len(call_ids), chunk_size):
        chunk = call_ids[i:i + chunk_size]
        placeholders = ",".join(["?"] * len(chunk))
        cursor.execute(f"""
            SELECT call_id, event_type, occurred_at, disposition_code, is_unknown_disposition
            FROM resolved_ledger
            WHERE call_id IN ({placeholders}) AND is_orphan = 0
            ORDER BY occurred_at ASC
        """, chunk)
        all_events.extend(cursor.fetchall())

    for ev in all_events:
        cid = ev["call_id"]
        etype = ev["event_type"]
        if cid not in call_initiations:
            continue

        if etype == "call.connected":
            call_initiations[cid]["connected"] = True
        elif etype == "call.telephony_failed":
            call_initiations[cid]["telephony_failed"] = True
        elif etype.startswith("disposition.") and ev["is_unknown_disposition"] == 0:
            call_initiations[cid]["disposition_events"].append({
                "type": etype,
                "occurred_at": ev["occurred_at"],
                "code": ev["disposition_code"],
            })

    # Determine effective disposition for each call
    for cid, cinfo in call_initiations.items():
        wf = cinfo["workflow_id"]
        disp_evs = cinfo["disposition_events"]

        # 1. Look for latest disposition.override
        overrides = [d for d in disp_evs if d["type"] == "disposition.override"]
        effective_disp = None
        if overrides:
            overrides.sort(key=lambda x: x["occurred_at"])
            effective_disp = overrides[-1]["code"]
        else:
            # 2. Look for latest disposition.set
            sets = [d for d in disp_evs if d["type"] == "disposition.set"]
            if sets:
                sets.sort(key=lambda x: x["occurred_at"])
                effective_disp = sets[-1]["code"]

        cinfo["effective_disposition"] = effective_disp
        if effective_disp and (wf, effective_disp) in disp_cat:
            cinfo["is_terminal"], cinfo["is_success"] = disp_cat[(wf, effective_disp)]
        else:
            cinfo["is_terminal"], cinfo["is_success"] = False, False

    return call_initiations


def _compute_lead_facts(conn: sqlite3.Connection, calls: dict[str, dict]) -> dict[int, dict]:
    """
    Computes aggregated facts and bucket assignment for each lead.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, workflow_id, external_ref, batch_id, phone_masked
        FROM leads
        ORDER BY workflow_id, external_ref
    """)
    leads = {
        r["id"]: {
            "id": r["id"],
            "workflow_id": r["workflow_id"],
            "external_ref": r["external_ref"],
            "batch_id": r["batch_id"],
            "phone_masked": r["phone_masked"],
            "calls": [],
        }
        for r in cursor.fetchall()
    }

    for c in calls.values():
        lid = c["lead_id"]
        if lid in leads:
            leads[lid]["calls"].append(c)

    for lid, linfo in leads.items():
        lcalls = linfo["calls"]
        total_calls = len(lcalls)
        connected_calls = sum(1 for c in lcalls if c["connected"])

        # Determine bucket
        if total_calls == 0:
            bucket = "yet_to_reach_out"
        elif any(c["is_terminal"] for c in lcalls):
            bucket = "completed"
        elif all(c["telephony_failed"] for c in lcalls):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"

        # Last call initiated at
        last_call_at = ""
        if lcalls:
            # Sort by initiated_at
            lcalls.sort(key=lambda x: x["initiated_at"])
            last_call_at = lcalls[-1]["initiated_at"]

        # Current disposition: effective disposition of most recent call (by initiated time) that has one
        current_disposition = ""
        calls_with_disp = [c for c in lcalls if c["effective_disposition"]]
        if calls_with_disp:
            calls_with_disp.sort(key=lambda x: x["initiated_at"])
            current_disposition = calls_with_disp[-1]["effective_disposition"]

        ever_success = any(c["is_success"] for c in lcalls)

        linfo["total_calls"] = total_calls
        linfo["connected_calls"] = connected_calls
        linfo["bucket"] = bucket
        linfo["last_call_at"] = last_call_at
        linfo["current_disposition"] = current_disposition
        linfo["ever_success"] = "true" if ever_success else "false"

    return leads


def _write_batch_summary(conn: sqlite3.Connection, lead_facts: dict[int, dict], out_csv: Path) -> None:
    batches = {}
    for linfo in lead_facts.values():
        key = (linfo["workflow_id"], linfo["batch_id"])
        if key not in batches:
            batches[key] = {
                "workflow_id": linfo["workflow_id"],
                "batch_id": linfo["batch_id"],
                "total_leads": 0,
                "yet_to_reach_out": 0,
                "in_progress_telephony_failed": 0,
                "in_progress_dialed": 0,
                "completed": 0,
            }
        b = batches[key]
        b["total_leads"] += 1
        b[linfo["bucket"]] += 1

    sorted_batches = sorted(batches.values(), key=lambda x: (x["workflow_id"], x["batch_id"]))

    fieldnames = [
        "workflow_id", "batch_id", "total_leads", "yet_to_reach_out",
        "in_progress_telephony_failed", "in_progress_dialed", "completed"
    ]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted_batches)


def _write_daily_overview(conn: sqlite3.Connection, calls: dict[str, dict], out_csv: Path) -> None:
    days = {}
    for c in calls.values():
        wf = c["workflow_id"]
        dt = c["initiated_date"]
        key = (wf, dt)
        if key not in days:
            days[key] = {
                "workflow_id": wf,
                "date": dt,
                "total_calls": 0,
                "connected_calls": 0,
                "success_lead_ids": set(),
            }
        d = days[key]
        d["total_calls"] += 1
        if c["connected"]:
            d["connected_calls"] += 1
        if c["is_success"]:
            d["success_lead_ids"].add(c["lead_id"])

    rows = []
    for (wf, dt) in sorted(days.keys()):
        d = days[(wf, dt)]
        tot = d["total_calls"]
        conn_cnt = d["connected_calls"]
        rate = (conn_cnt / tot) if tot > 0 else 0.0
        rows.append({
            "workflow_id": wf,
            "date": dt,
            "total_calls": tot,
            "connected_calls": conn_cnt,
            "connectivity_rate": f"{rate:.4f}",
            "success_leads": len(d["success_lead_ids"]),
        })

    fieldnames = ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_lead_report(conn: sqlite3.Connection, lead_facts: dict[int, dict], out_csv: Path) -> None:
    sorted_leads = sorted(lead_facts.values(), key=lambda x: (x["workflow_id"], x["external_ref"]))

    rows = [
        {
            "workflow_id": l["workflow_id"],
            "external_ref": l["external_ref"],
            "batch_id": l["batch_id"],
            "phone_masked": l["phone_masked"],
            "bucket": l["bucket"],
            "total_calls": l["total_calls"],
            "connected_calls": l["connected_calls"],
            "last_call_at": l["last_call_at"],
            "current_disposition": l["current_disposition"],
            "ever_success": l["ever_success"],
        }
        for l in sorted_leads
    ]

    fieldnames = [
        "workflow_id", "external_ref", "batch_id", "phone_masked", "bucket",
        "total_calls", "connected_calls", "last_call_at", "current_disposition", "ever_success"
    ]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_rejections(conn: sqlite3.Connection, out_csv: Path) -> None:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT workflow_id, batch_id, row_number, reason
        FROM rejections
        ORDER BY workflow_id, batch_id, row_number
    """)
    rows = [
        {
            "workflow_id": r["workflow_id"],
            "batch_id": r["batch_id"],
            "row_number": r["row_number"],
            "reason": r["reason"],
        }
        for r in cursor.fetchall()
    ]

    fieldnames = ["workflow_id", "batch_id", "row_number", "reason"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_data_quality(conn: sqlite3.Connection, out_json: Path) -> None:
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COALESCE(SUM(lead_rows_total), 0),
            COALESCE(SUM(lead_rows_rejected), 0),
            COALESCE(SUM(duplicate_lead_rows_merged), 0),
            COALESCE(SUM(field_warnings), 0)
        FROM ingested_files
        WHERE file_type = 'lead'
    """)
    lead_total, lead_rej, dup_merged, field_warn = cursor.fetchone()

    cursor.execute("""
        SELECT reason, COUNT(*)
        FROM rejections
        GROUP BY reason
        ORDER BY reason
    """)
    rejections_by_reason = {r[0]: r[1] for r in cursor.fetchall()}

    cursor.execute("""
        SELECT
            COALESCE(SUM(events_total), 0),
            COALESCE(SUM(malformed_events), 0),
            COALESCE(SUM(duplicate_events_dropped), 0)
        FROM ingested_files
        WHERE file_type = 'event'
    """)
    ev_total, malformed, dup_dropped = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM resolved_ledger WHERE is_orphan = 1")
    orphan_events = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM resolved_ledger
        WHERE is_orphan = 0 AND is_unknown_disposition = 1
    """)
    unknown_disp_events = cursor.fetchone()[0]

    quality_data = {
        "lead_rows_total": int(lead_total),
        "lead_rows_rejected": int(lead_rej),
        "rejections_by_reason": rejections_by_reason,
        "duplicate_lead_rows_merged": int(dup_merged),
        "field_warnings": int(field_warn),
        "events_total": int(ev_total),
        "malformed_events": int(malformed),
        "duplicate_events_dropped": int(dup_dropped),
        "orphan_events": int(orphan_events),
        "unknown_disposition_events": int(unknown_disp_events),
    }

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(quality_data, f, indent=2)

