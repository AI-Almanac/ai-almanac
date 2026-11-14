"""What a sandbox runs: parse one JSON request file, run it, print one JSON result line.

``python -m ai_almanac_tools.harness fetch <request.json> <out_dir> <scratch_dir>``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .sources import FetchRequest, get_source


def main(argv: list[str]) -> int:
    try:
        result = _run(*argv)
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result))
    return 0 if result["ok"] else 1


def _run(command: str, request_file: str, out_dir: str, scratch_dir: str) -> dict:
    if command != "fetch":
        raise ValueError(f"unknown command {command!r}")
    request = FetchRequest.model_validate_json(Path(request_file).read_text())
    fetched = get_source(request.source).fetch(request, Path(out_dir), Path(scratch_dir))
    return {"ok": True, "file": fetched.path.name, "source_version": fetched.source_version}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
