#!/usr/bin/env bash
# Regenerate web/src/lib/api-types.gen.ts from the FastAPI OpenAPI schema.
# Run via `pixi run generate-api-types`. CI fails if the output is stale.
set -euo pipefail
cd "$(dirname "$0")/.."

python -c "
from ai_almanac.server.app import app
import json, pathlib

def sort_enums(node):
    # Some upstream routers (TiTiler) build enums from unordered sets, so the
    # schema isn't reproducible without sorting them.
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'enum' and isinstance(value, list) and all(isinstance(v, str) for v in value):
                node[key] = sorted(value)
            else:
                sort_enums(value)
    elif isinstance(node, list):
        for item in node:
            sort_enums(item)

schema = app.openapi()
sort_enums(schema)
pathlib.Path('web/openapi.json').write_text(json.dumps(schema))
"
cd web
npx openapi-typescript openapi.json -o src/lib/api-types.gen.ts
rm openapi.json
