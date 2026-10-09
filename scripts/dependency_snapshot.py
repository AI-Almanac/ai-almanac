"""Turn `pixi list --json` into a GitHub dependency-graph snapshot.

Dependabot cannot read pixi.lock, so CI submits the production environment's
resolved packages through the dependency submission API instead; GitHub then
raises Dependabot alerts against exactly what the image ships.

    pixi list -e prod --platform linux-64 --json | python scripts/dependency_snapshot.py
"""

import json
import os
import sys
from datetime import UTC, datetime


def is_python_distribution(package: dict) -> bool:
    # ponytail: conda Python packages are assumed to share their PyPI name;
    # renamed ones (pytorch vs torch) go unscanned rather than mismatched.
    if package["kind"] == "pypi":
        return True
    return any(dep.split()[0] == "python" for dep in package.get("depends") or [])


def resolved(packages: list[dict]) -> dict:
    return {
        package["name"]: {
            "package_url": f"pkg:pypi/{package['name']}@{package['version']}",
            "relationship": "direct" if package.get("requested_spec") else "indirect",
        }
        for package in packages
        if is_python_distribution(package)
    }


def snapshot(packages: list[dict], *, sha: str, ref: str, job_id: str, scanned: str) -> dict:
    return {
        "version": 0,
        "sha": sha,
        "ref": ref,
        "job": {"correlator": "pixi-prod-linux-64", "id": job_id},
        "detector": {"name": "pixi-dependency-snapshot", "version": "1", "url": ""},
        "scanned": scanned,
        "manifests": {
            "pixi.lock": {
                "name": "pixi.lock (prod, linux-64)",
                "file": {"source_location": "pixi.lock"},
                "resolved": resolved(packages),
            }
        },
    }


if __name__ == "__main__":
    payload = snapshot(
        json.load(sys.stdin),
        sha=os.environ["GITHUB_SHA"],
        ref=os.environ["GITHUB_REF"],
        job_id=os.environ["GITHUB_RUN_ID"],
        scanned=datetime.now(UTC).isoformat(),
    )
    json.dump(payload, sys.stdout)
