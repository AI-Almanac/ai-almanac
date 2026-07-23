"""Seed a fake completed forecast job from a staging GCS job directory.

Downloads blended_forecast_probabilities.csv from staging and inserts a
complete fake blend + forecast job into the local SQLite DB so the UI can
be iterated on without deploying to staging.

Usage:
    # list jobs in bucket that have blend forecast output
    pixi run python scripts/seed_forecast_fixture.py --list gs://BUCKET

    # seed one of them locally
    pixi run python scripts/seed_forecast_fixture.py gs://BUCKET/JOB_ID
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path


def gcloud(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gcloud", "storage", *args], capture_output=True, text=True)


def list_blend_jobs(bucket_uri: str) -> None:
    bucket = bucket_uri.rstrip("/")
    print(f"Searching {bucket} for blend forecast outputs…")
    result = gcloud("ls", f"{bucket}/**/blended_forecast_probabilities.csv")
    if result.returncode != 0:
        sys.exit(f"gcloud storage ls failed:\n{result.stderr}")
    paths = result.stdout.strip().splitlines()
    if not paths:
        print("No blended_forecast_probabilities.csv found.")
        return
    print(f"Found {len(paths)} job(s):\n")
    for p in paths:
        # gs://bucket/JOB_ID/output/blended_forecast_probabilities.csv -> JOB_ID
        parts = p.removeprefix(bucket).strip("/").split("/")
        job_id = parts[0] if parts else "?"
        print(f"  {bucket}/{job_id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "gcs_prefix",
        help="GCS job directory (gs://bucket/job-id) or bucket with --list",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List jobs in the bucket that have blend forecast output",
    )
    args = parser.parse_args()

    if args.list:
        list_blend_jobs(args.gcs_prefix)
        return

    prefix = args.gcs_prefix.rstrip("/")
    csv_uri = f"{prefix}/output/blended_forecast_probabilities.csv"

    # Resolve local paths via the app's own path module so we always match
    # wherever the running server's data dir is.
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from ai_almanac.paths import database_path, jobs_dir

    db_path = database_path()
    if not db_path.exists():
        sys.exit(f"DB not found at {db_path} — is the server initialised?")

    # Pick up the local dev user.
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    user = con.execute("SELECT id FROM users WHERE external_id = 'local' LIMIT 1").fetchone()
    if not user:
        sys.exit("No 'local' user found — start the dev server once first.")
    user_id = user["id"]

    # Download the CSV to a temp location first so we can compute its checksum.
    tmp_csv = Path("/tmp/blend_forecast_fixture.csv")
    print(f"Downloading {csv_uri} …")
    result = gcloud("cp", csv_uri, str(tmp_csv))
    if result.returncode != 0:
        sys.exit(f"gcloud storage cp failed:\n{result.stderr}")

    csv_bytes = tmp_csv.read_bytes()
    checksum = hashlib.sha256(csv_bytes).hexdigest()

    now = datetime.now(UTC).isoformat()
    blend_id = str(uuid.uuid4())
    forecast_id = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())

    # Minimal config_json values the API reads when serialising ForecastOut.
    blend_config: dict = {
        "job_type": "blend",
        "region_id": "india",
        "blend_params": {"cutoff_month_day": "05-01"},
    }
    forecast_config: dict = {
        "job_type": "forecast",
        "blend_id": blend_id,
        "forecast_model_ids": ["GraphCast", "GenCast", "FuXi", "AIFS"],
        "region_id": "india",
        "init_time": None,
    }

    con.execute(
        """
        INSERT INTO jobs (id, user_id, dataset_id, job_type, status,
            config_json, created_at, completed_at, artifacts_published_at,
            visibility)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            blend_id,
            user_id,
            "fixture",
            "blend",
            "complete",
            json.dumps(blend_config),
            now,
            now,
            now,
            "private",
        ),
    )

    con.execute(
        """
        INSERT INTO jobs (id, user_id, dataset_id, job_type, status,
            config_json, created_at, completed_at, artifacts_published_at,
            visibility)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            forecast_id,
            user_id,
            "fixture",
            "forecast",
            "complete",
            json.dumps(forecast_config),
            now,
            now,
            now,
            "private",
        ),
    )

    con.execute(
        """
        INSERT INTO job_artifacts (id, job_id, kind, filename, media_type,
            size_bytes, checksum, storage_key, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            forecast_id,
            "output",
            "blended_forecast_probabilities.csv",
            "text/csv",
            len(csv_bytes),
            checksum,
            f"{forecast_id}/output/blended_forecast_probabilities.csv",
            now,
        ),
    )

    con.commit()
    con.close()

    # Place the CSV where LocalStorage.read_result_text expects it.
    dest = jobs_dir() / forecast_id / "output" / "blended_forecast_probabilities.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(tmp_csv, dest)

    print(f"Seeded blend   job: {blend_id}")
    print(f"Seeded forecast job: {forecast_id}")
    print(f"CSV at: {dest}")
    print()
    print("Open http://localhost:5173/forecasts to see it.")


if __name__ == "__main__":
    main()
