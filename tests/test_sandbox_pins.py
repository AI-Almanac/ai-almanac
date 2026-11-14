"""srt sits on a security boundary and is a beta: its version must be exact and locked."""

from __future__ import annotations

import json
import re
from importlib.resources import files

SANDBOX = files("ai_almanac.envs").joinpath("sandbox")
PACKAGE = "@anthropic-ai/sandbox-runtime"


def test_srt_is_pinned_exactly_and_the_lockfile_agrees() -> None:
    declared = json.loads(SANDBOX.joinpath("package.json").read_text())["dependencies"][PACKAGE]
    lock = json.loads(SANDBOX.joinpath("package-lock.json").read_text())

    assert re.fullmatch(r"\d+\.\d+\.\d+", declared), f"{PACKAGE} must be an exact version"
    assert lock["packages"][f"node_modules/{PACKAGE}"]["version"] == declared
