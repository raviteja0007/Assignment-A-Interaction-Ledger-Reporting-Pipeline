"""
Command-line interface for the Interaction Ledger and Reporting Pipeline.

Usage:
    python -m solution ingest <drop_directory> [--db <db_path>]
    python -m solution report --out <output_directory> [--db <db_path>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path if running directly as a script
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from solution.config import DEFAULT_DB_PATH
from solution.db import get_connection, init_db
from solution.ingestion import ingest_drop
from solution.reporter import generate_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="interaction-pipeline",
        description="Agentic Universe Interaction Ledger & Reporting Pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a data drop directory")
    ingest_parser.add_argument("drop_dir", type=str, help="Path to drop folder (e.g. data/drop_1)")
    ingest_parser.add_argument(
        "--db",
        type=str,
        default=str(DEFAULT_DB_PATH),
        help=f"Path to SQLite database file (default: {DEFAULT_DB_PATH})",
    )

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate final contract reports")
    report_parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Directory to write output report CSVs and JSON",
    )
    report_parser.add_argument(
        "--db",
        type=str,
        default=str(DEFAULT_DB_PATH),
        help=f"Path to SQLite database file (default: {DEFAULT_DB_PATH})",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    conn = get_connection(db_path)
    init_db(conn)

    try:
        if args.command == "ingest":
            drop_path = Path(args.drop_dir)
            print(f"Ingesting {drop_path} into {db_path}...")
            ingest_drop(drop_path, conn)
            print("Ingestion completed successfully.")
        elif args.command == "report":
            out_path = Path(args.out)
            print(f"Generating reports to {out_path} from {db_path}...")
            generate_reports(conn, out_path)
            print(f"Reports successfully generated in {out_path}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

