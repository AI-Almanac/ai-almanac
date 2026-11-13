"""What the server takes from a directory sandboxed code wrote to: regular files only,
and nothing through a link, whether the link is a file in the directory or the directory
itself. Needs no sandbox: the directory is arranged as hostile code could leave it."""

from __future__ import annotations

import os
from pathlib import Path

from ai_almanac.server.services import code_sandbox


def _secret_outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "credentials.txt").write_text("not for the sandbox")
    return outside


def test_the_files_sandboxed_code_wrote_are_taken_by_name(tmp_path: Path) -> None:
    written, taken_into = tmp_path / "written", tmp_path / "taken"
    written.mkdir()
    taken_into.mkdir()
    (written / "b.csv").write_text("b")
    (written / "a.png").write_bytes(b"a")
    (written / "nested").mkdir()

    taken = code_sandbox.take_outputs(written, taken_into)

    assert [path.name for path in taken] == ["a.png", "b.csv"]
    assert (taken_into / "b.csv").read_text() == "b"


def test_a_link_left_among_the_outputs_is_not_followed(tmp_path: Path) -> None:
    outside = _secret_outside(tmp_path)
    written, taken_into = tmp_path / "written", tmp_path / "taken"
    written.mkdir()
    taken_into.mkdir()
    os.symlink(outside / "credentials.txt", written / "soft.txt")
    os.link(outside / "credentials.txt", written / "hard.txt")

    assert code_sandbox.take_outputs(written, taken_into) == []
    assert list(taken_into.iterdir()) == []


def test_an_output_directory_replaced_by_a_link_has_no_outputs(tmp_path: Path) -> None:
    # The sandbox may write its own root, so on macOS it can remove the directory and
    # leave a link to any directory the server can read.
    outside = _secret_outside(tmp_path)
    written, taken_into = tmp_path / "written", tmp_path / "taken"
    taken_into.mkdir()
    os.symlink(outside, written)

    assert code_sandbox.take_outputs(written, taken_into) == []
    assert list(taken_into.iterdir()) == []


def test_an_output_directory_that_is_gone_has_no_outputs(tmp_path: Path) -> None:
    taken_into = tmp_path / "taken"
    taken_into.mkdir()

    assert code_sandbox.take_outputs(tmp_path / "removed", taken_into) == []
