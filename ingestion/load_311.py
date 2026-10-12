"""Download the Montreal 311 CSV and load it into DuckDB as raw.requetes311 (all text).

Usage:
    python ingestion/load_311.py --download-only   # fetch the CSV, skip the load
    python ingestion/load_311.py                    # download (if missing) and load the full data
    python ingestion/load_311.py --force-download   # re-download even if present
    python ingestion/load_311.py --sample           # load data_sample/requetes311_sample.csv, no download

Database file: $MONTREAL_311_DB if set, else data/montreal_311.duckdb. With --sample and no
override, the sample goes to data/montreal_311_sample.duckdb so it never overwrites the full
load. Set MONTREAL_311_DB (absolute path) to the same file when running dbt on the sample.
"""
import argparse
import os
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
SAMPLE_PATH = ROOT / "data_sample" / "requetes311_sample.csv"


def db_path(sample: bool) -> Path:
    if os.environ.get("MONTREAL_311_DB"):
        return Path(os.environ["MONTREAL_311_DB"])
    return ROOT / "data" / ("montreal_311_sample.duckdb" if sample else "montreal_311.duckdb")


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
            # Report once per 100 MB crossed.
            if total and done // (100 << 20) != (done - len(chunk)) // (100 << 20):
                print(f"  {done / 1e6:.0f}/{total / 1e6:.0f} MB", flush=True)
    tmp.replace(CSV_PATH)


def load(csv_path: Path, database: Path, source: str) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(database))
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    # all_varchar: no type inference, every column lands as text.
    con.execute(
        """
        CREATE OR REPLACE TABLE raw.requetes311 AS
        SELECT * FROM read_csv(?, all_varchar=true, header=true, sample_size=-1)
        """,
        [str(csv_path)],
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
        ["requetes311", loaded_at, row_count, source, csv_path.stat().st_size],
    )
    con.close()
    print(f"Loaded raw.requetes311 into {database}: {row_count:,} rows at {loaded_at.isoformat()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--sample", action="store_true", help="load the committed sample instead of downloading")
    args = ap.parse_args()
    if args.sample:
        load(SAMPLE_PATH, db_path(sample=True), source=f"sample:{SAMPLE_PATH.name}")
    else:
        download(force=args.force_download)
        if not args.download_only:
            load(CSV_PATH, db_path(sample=False), source=URL)
    sys.exit(0)
