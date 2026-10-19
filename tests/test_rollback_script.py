"""Which Cloud Run revision scripts/rollback.sh rolls to."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rollback.sh"

GOOD = "a" * 40
BAD = "b" * 40
OLDER = "c" * 40


def _select(function: str, arg: str, revisions: list[tuple[str, str]]) -> str:
    """Run one selection function from the script on newest-first revisions."""
    tsv = "".join(f"{name}\t{tag}\n" for name, tag in revisions)
    result = subprocess.run(
        ["bash", "-c", f'source "$1"; {function} "$2"', "test", str(SCRIPT), arg],
        input=tsv,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_previous_skips_revisions_running_the_serving_image() -> None:
    # Config-only `tofu apply`s create new revisions with the same image.
    revisions = [("rev-4", BAD), ("rev-3", BAD), ("rev-2", GOOD)]

    assert _select("previous_revision", "rev-4", revisions) == f"rev-2\t{GOOD}"


def test_previous_is_relative_to_the_serving_revision_not_the_newest() -> None:
    # After an earlier rollback, newer revisions exist that serve no traffic.
    revisions = [("rev-5", BAD), ("rev-3", GOOD), ("rev-2", OLDER)]

    assert _select("previous_revision", "rev-3", revisions) == f"rev-2\t{OLDER}"


def test_previous_is_empty_when_no_older_image_exists() -> None:
    revisions = [("rev-2", GOOD), ("rev-1", GOOD)]

    assert _select("previous_revision", "rev-2", revisions) == ""


def test_sha_picks_the_newest_revision_built_from_it() -> None:
    revisions = [("rev-4", BAD), ("rev-3", GOOD), ("rev-2", GOOD)]

    assert _select("revision_for_sha", GOOD, revisions) == f"rev-3\t{GOOD}"
