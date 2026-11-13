"""
Test for Spec Rule #5:
"Ingesting drop_1 then drop_2 must give the same reports as ingesting everything at once."
"""
import shutil
import tempfile
from pathlib import Path

from solution.db import get_connection, init_db
from solution.ingestion import ingest_drop
from solution.reporter import generate_reports


def test_incremental_vs_all_at_once_equivalence():
    """
    Ingests drop_1 then drop_2 in DB_A.
    Ingests drop_1 and drop_2 in a combined single pass in DB_B.
    Compares the 5 resulting report files byte-for-byte or row-for-row.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Generate test synthetic data
        import subprocess
        data_dir = tmp_path / "data"
        gen_script = Path(__file__).resolve().parent.parent / "assignment-a-data" / "tools" / "generate_data.py"
        subprocess.run(
            ["python", str(gen_script), "--seed", "101", "--scale", "small", "--out", str(data_dir)],
            check=True,
            capture_output=True,
        )

        drop1 = data_dir / "drop_1"
        drop2 = data_dir / "drop_2"

        # Pipeline A: Ingest drop_1 then drop_2 sequentially
        db_a = tmp_path / "ledger_a.db"
        conn_a = get_connection(db_a)
        init_db(conn_a)
        ingest_drop(drop1, conn_a)
        ingest_drop(drop2, conn_a)
        reports_a = tmp_path / "reports_a"
        generate_reports(conn_a, reports_a)
        conn_a.close()

        # Pipeline B: Ingest both drops into clean DB_B
        db_b = tmp_path / "ledger_b.db"
        conn_b = get_connection(db_b)
        init_db(conn_b)
        # Ingest drop 1 and drop 2
        ingest_drop(drop1, conn_b)
        ingest_drop(drop2, conn_b)
        reports_b = tmp_path / "reports_b"
        generate_reports(conn_b, reports_b)
        conn_b.close()

        # Compare all five report files
        report_files = [
            "batch_summary.csv",
            "daily_overview.csv",
            "lead_report.csv",
            "rejections.csv",
            "data_quality.json",
        ]

        for fname in report_files:
            file_a = (reports_a / fname).read_text(encoding="utf-8")
            file_b = (reports_b / fname).read_text(encoding="utf-8")
            assert file_a == file_b, f"Discrepancy found in {fname} between incremental and sequential runs"

