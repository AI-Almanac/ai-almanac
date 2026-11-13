"""Reading a server-sent event stream as the browser would."""

from __future__ import annotations

import json


def sse_events(body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
