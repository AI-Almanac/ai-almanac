"""The local code sandbox's security properties, checked against the real srt.

Skipped where srt cannot sandbox (not installed, or Linux user namespaces restricted);
`sandbox_unavailable_reason()` is what hides run_code on such hosts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_almanac.paths import data_root
from ai_almanac.server.services import code_sandbox

pytestmark = pytest.mark.skipif(
    code_sandbox.sandbox_unavailable_reason() is not None,
    reason=str(code_sandbox.sandbox_unavailable_reason()),
)

PROBE = """
import json, os, socket
checks = json.loads(os.environ["CHECKS"])
out = {}
def attempt(name, fn):
    try:
        fn()
        out[name] = "allowed"
    except OSError:
        out[name] = "blocked"
attempt("read_input", lambda: open(checks["input"]).read())
attempt("write_output", lambda: open(checks["output"], "w").write("x"))
attempt("read_data_dir", lambda: open(checks["secret"]).read())
attempt("write_elsewhere", lambda: open(checks["elsewhere"], "w").write("x"))
attempt("network", lambda: socket.create_connection(("1.1.1.1", 53), timeout=3))
print(json.dumps(out))
"""


def test_sandbox_confines_reads_writes_and_network(tmp_path: Path) -> None:
    stage, out, elsewhere = tmp_path / "stage", tmp_path / "out", tmp_path / "elsewhere"
    for path in (stage, out, elsewhere):
        path.mkdir()
    (stage / "input.txt").write_text("data")
    secret = data_root() / "secret.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("token")
    script = stage / "probe.py"
    script.write_text(PROBE)
    checks = {
        "input": str(stage / "input.txt"),
        "output": str(out / "result.txt"),
        "secret": str(secret),
        "elsewhere": str(elsewhere / "leak.txt"),
    }

    run = code_sandbox.run_python(
        script, env={"CHECKS": json.dumps(checks)}, readable=[stage], writable=[out], timeout=60
    )

    assert json.loads(run.stdout.strip().splitlines()[-1]) == {
        "read_input": "allowed",
        "write_output": "allowed",
        "read_data_dir": "blocked",
        "write_elsewhere": "blocked",
        "network": "blocked",
    }, run.stderr
