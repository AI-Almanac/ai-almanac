"""Run ai-almanac-tools harness commands in the local sandbox.

The server writes the script, so no request can carry code: the script puts a fresh
copy of the bundled tools on ``sys.path`` (srt runs Python with ``-I``, which ignores
PYTHONPATH, and the copy keeps the server's site-packages out of reach) and calls the
harness with a request file. A fetch's network is open only to its source's hosts.

ponytail: srt only. The Modal fetch runner, with signed-URL upload, is the next step (D13).
"""

from __future__ import annotations

import functools
import hashlib
from dataclasses import replace
from pathlib import Path

import ai_almanac_tools

from . import code_sandbox

# The request is the server's own JSON of a parsed FetchRequest, written in as a literal.
_SCRIPT = """\
import json, os, sys
INPUTS = json.loads(os.environ["WORKSPACE_INPUTS"])
sys.path.insert(0, os.path.dirname(INPUTS["tools"]))
from ai_almanac_tools.harness import main
request = os.path.join(os.environ["SCRATCH_DIR"], "request.json")
with open(request, "w") as handle:
    handle.write({request!r})
sys.exit(main(["fetch", request, os.environ["ARTIFACT_DIR"], os.environ["SCRATCH_DIR"]]))
"""


def tools_root() -> Path:
    return Path(ai_almanac_tools.__file__).parent


@functools.cache
def tools_digest() -> str:
    """Identifies the tools source a sandbox ran, sealed into each step's runtime."""
    digest = hashlib.sha256()
    for path in sorted(tools_root().rglob("*.py")):
        digest.update(path.relative_to(tools_root()).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


async def run_fetch(
    request_json: str, *, hosts: tuple[str, ...], into: Path, timeout: float
) -> code_sandbox.SandboxResult:
    """Fetch into ``into`` in the local sandbox, with network only to ``hosts``."""
    result = await code_sandbox.LocalSandbox().run(
        _SCRIPT.format(request=request_json),
        {"tools": tools_root()},
        into,
        timeout=timeout,
        allowed_domains=hosts,
    )
    return replace(result, runtime={**result.runtime, "ai_almanac_tools": tools_digest()})
