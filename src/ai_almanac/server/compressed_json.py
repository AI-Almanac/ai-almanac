"""Large JSON answers compressed once and sent as they are to every client."""

from __future__ import annotations

import gzip
import json
from typing import Any

from fastapi import Request, Response

# Reference data that changes with a geoBoundaries release, not between page loads.
_KEPT_FOR_A_DAY = "private, max-age=86400"


def gzipped_json(value: Any) -> bytes:
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return gzip.compress(text.encode(), compresslevel=6)


def gzipped_json_response(body: bytes, request: Request) -> Response:
    """``body``, gzipped JSON, sent compressed to a client that accepts gzip and
    decompressed to one that does not."""
    headers = {"Cache-Control": _KEPT_FOR_A_DAY, "Vary": "Accept-Encoding"}
    # The same test Starlette's GZipMiddleware makes.
    if "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
        return Response(body, media_type="application/json", headers=headers)
    return Response(gzip.decompress(body), media_type="application/json", headers=headers)
