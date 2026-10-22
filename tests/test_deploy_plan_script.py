"""What scripts/deploy-plan.sh decides a deploy run ships."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "deploy-plan.sh"

NOTHING = {"image": "false", "modal": "false", "migrations": "false"}
EVERYTHING = {"image": "true", "modal": "true", "migrations": "true"}

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")

FAKE_GCLOUD = '#!/usr/bin/env bash\ncat "$FAKE_DIR/service.json"\n'
FAKE_GH = '#!/usr/bin/env bash\ncat "$FAKE_DIR/runs.json"\n'


def _bash(snippet: str, cwd: Path, env: dict[str, str] | None = None, stdin: str = ""):
    return subprocess.run(
        ["bash", "-c", f'source "$1"; {snippet}', "test", str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, **(env or {})},
    )


def _parse(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines())


def _plan(*paths: str) -> dict[str, str]:
    result = _bash("plan_for_paths", REPO_ROOT, stdin="".join(f"{p}\n" for p in paths))
    assert result.returncode == 0, result.stderr
    return _parse(result.stdout)


@pytest.mark.parametrize(
    "paths",
    [
        ["tests/test_blend_staging.py"],
        ["docs/sdlc/rollback.md", "README.md"],
        ["web/tests/adm3.test.ts"],
        ["terraform/envs.tf", ".github/workflows/ci.yml"],
    ],
)
def test_changes_outside_the_release_ship_nothing(paths: list[str]) -> None:
    assert _plan(*paths) == NOTHING


@pytest.mark.parametrize("app", ["app", "blending_app", "forecasts_app"])
def test_a_modal_app_change_ships_only_modal(app: str) -> None:
    assert _plan(f"modal/{app}.py") == {**NOTHING, "modal": "true"}


def test_a_frontend_change_ships_only_the_image() -> None:
    assert _plan("web/src/routes/+page.svelte") == {**NOTHING, "image": "true"}


def test_a_server_file_modal_bundles_ships_both() -> None:
    assert _plan("src/ai_almanac/server/services/forecast_pipeline.py") == {
        **NOTHING,
        "image": "true",
        "modal": "true",
    }


def test_a_migration_ships_the_image_it_runs_from() -> None:
    assert _plan("src/ai_almanac/server/alembic/versions/0029_x.py") == {
        **NOTHING,
        "image": "true",
        "migrations": "true",
    }


def _bundled_repo_files() -> set[str]:
    """Repo files the Modal apps reference by path, in the idioms they use."""
    files: set[str] = set()
    for app in (REPO_ROOT / "modal").glob("*.py"):
        source = app.read_text()
        files.update(re.findall(r'_REPO_ROOT / "([^"]+)"', source))
        services = re.search(r'_SERVICES_DIR = .*?"([^"]+)"', source)
        if services:
            names = re.findall(r'_SERVICES_DIR / "([^"]+)"', source)
            files.update(f"{services.group(1)}/{name}" for name in names)
    return files


def test_every_repo_file_a_modal_app_bundles_ships_to_modal() -> None:
    bundled = _bundled_repo_files()
    assert bundled, "found no bundled files; the idioms in modal/*.py changed"
    for path in bundled:
        assert (REPO_ROOT / path).is_file(), path
        assert _plan(path)["modal"] == "true", path


# --- main: when the plan falls back to shipping everything ---------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, path: str) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(path)
    _git(repo, "add", path)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", path)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _main(
    repo: Path,
    tmp_path: Path,
    runs: list[dict[str, object]],
    *,
    pinned: bool = False,
    env: dict[str, str] | None = None,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in {"gcloud": FAKE_GCLOUD, "gh": FAKE_GH}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    traffic = {"revisionName": "r1", "percent": 100}
    if not pinned:
        traffic["latestRevision"] = True
    (tmp_path / "service.json").write_text(json.dumps({"status": {"traffic": [traffic]}}))
    (tmp_path / "runs.json").write_text(json.dumps(runs))
    result = _bash(
        "main deploy-staging.yml svc us-central1",
        repo,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_DIR": str(tmp_path),
            "GITHUB_RUN_ID": "100",
            **(env or {}),
        },
    )
    assert result.returncode == 0, result.stderr
    return _parse(result.stdout)


def _run(run_id: int, sha: str, conclusion: str | None = "success") -> dict[str, object]:
    status = "completed" if conclusion else "queued"
    return {"databaseId": run_id, "status": status, "conclusion": conclusion, "headSha": sha}


@needs_jq
def test_ships_what_changed_since_the_previous_successful_run(repo: Path, tmp_path: Path) -> None:
    deployed = _commit(repo, "src/app.py")
    _commit(repo, "modal/app.py")
    _commit(repo, "tests/test_app.py")
    assert _main(repo, tmp_path, [_run(99, deployed)]) == {**NOTHING, "modal": "true"}


@needs_jq
def test_a_newer_superseded_run_does_not_count_as_the_previous_one(
    repo: Path, tmp_path: Path
) -> None:
    deployed = _commit(repo, "src/app.py")
    _commit(repo, "modal/app.py")
    runs = [_run(101, deployed, "cancelled"), _run(99, deployed)]
    assert _main(repo, tmp_path, runs) == {**NOTHING, "modal": "true"}


@needs_jq
@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_ships_everything_after_an_unfinished_deploy(
    repo: Path, tmp_path: Path, conclusion: str
) -> None:
    deployed = _commit(repo, "src/app.py")
    _commit(repo, "tests/test_app.py")
    assert _main(repo, tmp_path, [_run(99, deployed, conclusion)]) == EVERYTHING


@needs_jq
def test_ships_everything_without_a_previous_run(repo: Path, tmp_path: Path) -> None:
    _commit(repo, "tests/test_app.py")
    assert _main(repo, tmp_path, []) == EVERYTHING


@needs_jq
def test_ships_everything_when_the_previous_commit_is_unknown(repo: Path, tmp_path: Path) -> None:
    _commit(repo, "tests/test_app.py")
    assert _main(repo, tmp_path, [_run(99, "f" * 40)]) == EVERYTHING


@needs_jq
def test_ships_everything_while_a_rollback_pins_traffic(repo: Path, tmp_path: Path) -> None:
    deployed = _commit(repo, "src/app.py")
    _commit(repo, "tests/test_app.py")
    assert _main(repo, tmp_path, [_run(99, deployed)], pinned=True) == EVERYTHING


@needs_jq
def test_a_manual_run_ships_everything(repo: Path, tmp_path: Path) -> None:
    deployed = _commit(repo, "src/app.py")
    _commit(repo, "tests/test_app.py")
    runs = [_run(99, deployed)]
    assert _main(repo, tmp_path, runs, env={"DEPLOY_EVERYTHING": "true"}) == EVERYTHING
