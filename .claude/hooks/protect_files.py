#!/usr/bin/env python3
"""PreToolUse hook: block agent writes to generated and credential files.

Reads the hook payload from stdin and exits 2 (block; stderr is shown to the
agent) when the target file must not be edited directly. Exits 0 otherwise.
Failing open on malformed input is deliberate: a broken hook must not brick
every edit.
"""

import fnmatch
import json
import sys

PROTECTED = [
    # Generated: only scripts/generate-api-types.sh may write this.
    ("web/src/lib/api-types.gen.ts", "generated; run `pixi run generate-api-types` instead"),
    # Credentials and local secrets.
    (".env", "local secrets file; edit it yourself outside the agent"),
    ("web/.env", "local secrets file; edit it yourself outside the agent"),
    ("*service-account*.json", "credential file"),
    ("*.pem", "credential file"),
    ("*.tfstate", "terraform state is remote (GCS); never hand-edit state"),
    ("*.tfstate.*", "terraform state is remote (GCS); never hand-edit state"),
    # Lockfiles are tool-owned.
    ("pixi.lock", "tool-owned lockfile; run `pixi update`/`pixi add`"),
    ("uv.lock", "tool-owned lockfile; run `uv lock`"),
    ("web/package-lock.json", "tool-owned lockfile; run npm from web/"),
]


def matches(norm: str, pattern: str) -> bool:
    if "/" in pattern:
        return norm == pattern or norm.endswith("/" + pattern)
    return fnmatch.fnmatch(norm.rsplit("/", 1)[-1], pattern)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        path = payload.get("tool_input", {}).get("file_path", "") or ""
    except Exception:
        return 0
    if not path:
        return 0
    norm = path.replace("\\", "/")
    for pattern, reason in PROTECTED:
        if matches(norm, pattern):
            print(f"Blocked write to {path}: {reason}.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
