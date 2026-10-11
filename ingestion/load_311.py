"""Download the Montreal 311 CSV and load it into DuckDB as raw.requetes311 (all text).

Usage:
    python ingestion/load_311.py --download-only   # fetch the CSV, skip the load
    python ingestion/load_311.py                    # download (if missing) and load
    python ingestion/load_311.py --force-download   # re-download even if present
"""
import argparse
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import duckdb

URL = (
    "https://donnees.montreal.ca/dataset/5866f832-676d-4b07-be6a-e99c21eb17e4/"
    "resource/2cfa0e06-9be4-49a6-b7f1-ee9f2363a872/download/requetes311.csv"
)
ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "raw" / "requetes311.csv"
DB_PATH = ROOT / "data" / "montreal_311.duckdb"


def download(force: bool = False) -> None:
    if CSV_PATH.exists() and not force:
        print(f"Using existing {CSV_PATH} ({CSV_PATH.stat().st_size / 1e6:.1f} MB)")
        return
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CSV_PATH.with_suffix(".part")
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / 1e6:.0f}/{total / 1e6:.0f} MB", end="", flush=True)
    print()
    tmp.replace(CSV_PATH)


def load() -> None:
    con = duckdb.connect(str(DB_PATH))
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    # all_varchar: no type inference, every column lands as text.
    con.execute(
        """
        CREATE OR REPLACE TABLE raw.requetes311 AS
        SELECT * FROM read_csv(?, all_varchar=true, header=true, sample_size=-1)
        """,
        [str(CSV_PATH)],
    )
    row_count = con.execute("SELECT count(*) FROM raw.requetes311").fetchone()[0]
    loaded_at = datetime.now(timezone.utc)

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS raw._load_log (
            table_name VARCHAR, loaded_at TIMESTAMPTZ, row_count BIGINT,
            source_url VARCHAR, file_bytes BIGINT
        )
        """
    )
    con.execute(
        "INSERT INTO raw._load_log VALUES (?, ?, ?, ?, ?)",
        ["requetes311", loaded_at, row_count, URL, CSV_PATH.stat().st_size],
    )
    con.close()
    print(f"Loaded raw.requetes311: {row_count:,} rows at {loaded_at.isoformat()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--force-download", action="store_true")
    args = ap.parse_args()
    download(force=args.force_download)
    if not args.download_only:
        load()
    sys.exit(0)
