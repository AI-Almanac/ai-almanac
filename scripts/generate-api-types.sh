#!/usr/bin/env bash
# Regenerate web/src/lib/api-types.gen.ts from the FastAPI OpenAPI schema.
# Run via `pixi run generate-api-types`. CI fails if the output is stale.
set -euo pipefail
cd "$(dirname "$0")/.."

python -c "
from ai_almanac.server.app import app
import json, pathlib
pathlib.Path('web/openapi.json').write_text(json.dumps(app.openapi()))
"
cd web
npx openapi-typescript openapi.json -o src/lib/api-types.gen.ts
rm openapi.json
