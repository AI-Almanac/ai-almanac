#!/usr/bin/env python3
"""PostToolUse hook: auto-format the file the agent just edited.

Python sources go through ruff format, web sources through the repo's
prettier. Always exits 0 — formatting is best-effort and must never block
the edit loop.
"""

import json
import os
import shutil
import subprocess
import sys

PROJECT = os.environ.get("CLAUDE_PROJECT_DIR", ".")

WEB_EXTS = {".ts", ".js", ".svelte", ".css", ".json", ".md", ".html"}


def run(cmd: list[str]) -> None:
    subprocess.run(
        cmd,
        cwd=PROJECT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        path = payload.get("tool_input", {}).get("file_path", "") or ""
    except Exception:
        return 0
    if not path or not os.path.isfile(path):
        return 0

    rel = os.path.relpath(path, PROJECT).replace("\\", "/")
    ext = os.path.splitext(rel)[1]

    if ext == ".py" and rel.startswith(("src/", "tests/", "modal/", "scripts/")):
        if shutil.which("ruff"):
            run(["ruff", "format", rel])
        elif shutil.which("pixi"):
            run(["pixi", "run", "-e", "dev", "ruff", "format", rel])
    elif rel.startswith("web/") and ext in WEB_EXTS and ".gen." not in rel:
        prettier = os.path.join(PROJECT, "web", "node_modules", ".bin", "prettier")
        if os.path.exists(prettier):
            run([prettier, "--write", rel])
    return 0


if __name__ == "__main__":
    sys.exit(main())
