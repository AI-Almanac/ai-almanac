"""Which Cloud Run revision scripts/rollback.sh rolls to, and when it refuses."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rollback.sh"

GOOD = "a" * 40
BAD = "b" * 40
OLDER = "c" * 40
IMAGE = "us-central1-docker.pkg.dev/ai-almanac/almanac/ai-almanac-web"

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")

# Stand-ins for gcloud and gh that answer from JSON fixtures; gh applies --jq
# itself, so the fake does too.
FAKE_GCLOUD = '#!/usr/bin/env bash\ncat "$FAKE_GCLOUD_JSON"\n'
FAKE_GH = """#!/usr/bin/env bash
case "$1 $2" in
"run list") fixture=$FAKE_DIR/runs.json ;;
"run view") fixture=$FAKE_DIR/run-$3.json ;;
esac
while (($#)); do
	if [[ $1 == --jq ]]; then
		exec jq -r "$2" "$fixture"
	fi
	shift
done
"""


def _run(snippet: str, tmp_path: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
    """Source the script, then run `snippet` with fake gcloud and gh on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in {"gcloud": FAKE_GCLOUD, "gh": FAKE_GH}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DIR": str(tmp_path),
        "FAKE_GCLOUD_JSON": str(tmp_path / "gcloud.json"),
    }
    return subprocess.run(
        ["bash", "-c", f'source "$1"; {snippet}', "test", str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


def _write(tmp_path: Path, name: str, payload: object) -> None:
    (tmp_path / name).write_text(json.dumps(payload))


def _select(function: str, arg: str, revisions: list[tuple[str, str]], tmp_path: Path) -> str:
    tsv = "".join(f"{name}\t{tag}\n" for name, tag in revisions)
    return _run(f'{function} "{arg}"', tmp_path, stdin=tsv).stdout.strip()


def _revision(name: str, created: str, tag: str, ready: bool = True) -> dict:
    return {
        "metadata": {"name": name, "creationTimestamp": created},
        "spec": {"containers": [{"image": f"{IMAGE}:{tag}"}]},
        "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]},
    }


def test_previous_skips_revisions_running_the_serving_image(tmp_path: Path) -> None:
    # Config-only `tofu apply`s create new revisions with the same image.
    revisions = [("rev-4", BAD), ("rev-3", BAD), ("rev-2", GOOD)]

    assert _select("previous_revision", "rev-4", revisions, tmp_path) == f"rev-2\t{GOOD}"


def test_previous_is_relative_to_the_serving_revision_not_the_newest(tmp_path: Path) -> None:
    # After an earlier rollback, newer revisions exist that serve no traffic.
    revisions = [("rev-5", BAD), ("rev-3", GOOD), ("rev-2", OLDER)]

    assert _select("previous_revision", "rev-3", revisions, tmp_path) == f"rev-2\t{OLDER}"


def test_previous_is_empty_when_no_older_image_exists(tmp_path: Path) -> None:
    revisions = [("rev-2", GOOD), ("rev-1", GOOD)]

    assert _select("previous_revision", "rev-2", revisions, tmp_path) == ""


def test_sha_picks_the_newest_revision_built_from_it(tmp_path: Path) -> None:
    revisions = [("rev-4", BAD), ("rev-3", GOOD), ("rev-2", GOOD)]

    assert _select("revision_for_sha", GOOD, revisions, tmp_path) == f"rev-3\t{GOOD}"


def test_sha_prefers_the_serving_revision_so_a_resync_leaves_traffic_alone(
    tmp_path: Path,
) -> None:
    # rev-4 came from a config-only apply after a rollback pinned rev-3.
    tsv = f"rev-4\t{GOOD}\nrev-3\t{GOOD}\nrev-2\t{OLDER}\n"

    result = _run(f'revision_for_sha "{GOOD}" rev-3', tmp_path, stdin=tsv)

    assert result.stdout.strip() == f"rev-3\t{GOOD}"


@needs_jq
def test_serving_revision_ignores_tagged_zero_percent_entries(tmp_path: Path) -> None:
    traffic = [
        {"revisionName": "rev-5", "percent": 100, "latestRevision": True},
        {"revisionName": "rev-3", "tag": "old"},
    ]
    _write(tmp_path, "gcloud.json", {"status": {"traffic": traffic}})

    assert _run("select_env prod; serving_revision", tmp_path).stdout.strip() == "rev-5"


@needs_jq
def test_serving_revision_is_empty_when_traffic_is_split(tmp_path: Path) -> None:
    traffic = [
        {"revisionName": "rev-5", "percent": 50},
        {"revisionName": "rev-4", "percent": 50},
    ]
    _write(tmp_path, "gcloud.json", {"status": {"traffic": traffic}})

    assert _run("select_env prod; serving_revision", tmp_path).stdout.strip() == ""


@needs_jq
def test_ready_revisions_are_newest_first_with_their_image_tags(tmp_path: Path) -> None:
    revisions = [
        _revision("rev-2", "2026-10-19T02:00:00Z", OLDER),
        _revision("rev-4", "2026-10-19T04:00:00Z", BAD, ready=False),
        _revision("rev-3", "2026-10-19T03:00:00Z", GOOD),
    ]
    _write(tmp_path, "gcloud.json", revisions)

    result = _run("select_env prod; ready_revisions_newest_first", tmp_path)

    assert result.stdout.splitlines() == [f"rev-3\t{GOOD}", f"rev-2\t{OLDER}"]


@needs_jq
def test_deploy_check_allows_a_run_that_is_only_verifying(tmp_path: Path) -> None:
    _write(tmp_path, "runs.json", [{"databaseId": 7, "status": "in_progress"}])
    jobs = [{"name": "deploy", "status": "completed"}, {"name": "verify", "status": "in_progress"}]
    _write(tmp_path, "run-7.json", {"jobs": jobs})

    result = _run("ENV_NAME=prod SKIP_DEPLOY_CHECK=false; ensure_no_deploy_running", tmp_path)

    assert result.returncode == 0, result.stderr


@needs_jq
def test_deploy_check_refuses_a_run_waiting_to_deploy(tmp_path: Path) -> None:
    _write(tmp_path, "runs.json", [{"databaseId": 8, "status": "waiting"}])
    _write(tmp_path, "run-8.json", {"jobs": [{"name": "deploy", "status": "waiting"}]})

    result = _run("ENV_NAME=prod SKIP_DEPLOY_CHECK=false; ensure_no_deploy_running", tmp_path)

    assert result.returncode == 1
    assert "run 8 has not finished its deploy job" in result.stderr
