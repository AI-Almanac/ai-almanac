# Development Guide

How to hack on ai-almanac. The whole stack is one Python package — no Docker,
no separate frontend service, no Postgres for local dev.

---

## Architecture overview

```
ai-almanac (one Python process)
├── FastAPI server (uvicorn)
│   ├── /api/...    JSON API
│   ├── /config.js  runtime config injected into the SPA
│   └── /...        bundled SvelteKit SPA (when built)
└── InProcessRunner — shells out to `pixi run momp-run` in the benchmark env
```

Storage: filesystem under `$AI_ALMANAC_DATA_DIR` (default:
`~/.local/share/ai-almanac/`).
Database: SQLite at `<data-dir>/almanac.db`, auto-migrated on startup.
Auth: none — see [`DEPLOY_PUBLIC.md`](./DEPLOY_PUBLIC.md) for reverse-proxy setup.

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [Node.js](https://nodejs.org/) 20+ and npm
- [pixi](https://pixi.sh/) — only required to actually run benchmarks (not for working on the web UI / API)

---

## Quick start

```bash
git clone <repo>
cd ai-almanac

# Python side — installs the package editable
uv sync

# Frontend side — installs SvelteKit deps and starts the Vite dev server
cd web && npm install
npm run dev    # serves the SPA at http://localhost:5173

# In another terminal — run the Python server with auto-reload
cd ..
uv run ai-almanac serve --reload --no-open
# API + SPA fallback at http://localhost:8765
# In dev, prefer http://localhost:5173 (Vite hot reload) and let it proxy /api/* to :8765
```

The Vite dev server provides hot reload; the FastAPI server provides the API
and `/config.js`. Vite proxies `/api/*` and `/config.js` to the FastAPI port
(see `web/vite.config.ts` — adjust the proxy target if you bind FastAPI
elsewhere).

To test a single-process production-style serve, build the SPA first so the
backend can serve it:

```bash
cd web && npm run build
cd ..
AI_ALMANAC_DATA_DIR=/tmp/almanac-dev uv run ai-almanac serve
# Everything at http://localhost:8765 — same process serves API and SPA
```

---

## Backend layout

```
src/ai_almanac/
├── __init__.py
├── __main__.py            python -m ai_almanac
├── cli.py                 typer CLI: serve, env, reset, version
├── paths.py               AI_ALMANAC_DATA_DIR resolution via platformdirs
├── settings.py            pydantic-settings, YAML registries
├── envs/
│   ├── manager.py         pixi env lifecycle
│   └── benchmark.pixi.toml  benchmark env spec
└── server/
    ├── app.py             FastAPI app, lifespan, static SPA mount
    ├── db.py              SQLAlchemy async + auto-migrate
    ├── attribution.py     reads X-Forwarded-User → CurrentUser shim
    ├── alembic/           migrations (collapsed to one SQLite-native baseline)
    ├── config/            YAML registries: models.yaml, datasets.yaml,
    │                      regions.yaml, romp.yaml
    ├── routers/           jobs, datasets, config, regions, chat
    ├── services/
    │   ├── runner.py      InProcessRunner — spawns momp-run via pixi
    │   ├── job_events.py  per-job WebSocket pub/sub broker
    │   ├── e2s.py         earth2studio RMSE/MAE/ACC/bias subprocess script
    │   ├── storage.py     LocalStorage (only impl)
    │   ├── metrics.py     ROMP metric domain aggregation
    │   ├── benchmark_*.py LLM-driven benchmark planning state machine
    │   └── chat_*.py      LLM chat + figure handling
    └── static/            bundled SvelteKit SPA (populated at wheel-build time)
```

---

## Frontend layout

```
web/
├── svelte.config.js       adapter-static, fallback: index.html
├── src/
│   ├── app.html           includes <script src="/config.js">
│   ├── lib/
│   │   ├── api.ts         fetch wrappers + subscribeJob() WebSocket helper
│   │   ├── auth.ts        no-op shim (auth lives at the proxy, not in the app)
│   │   ├── auth-store.ts  ditto — isAuthenticated always true
│   │   ├── components/    MetricMap, ResultsViewer, JobLogs, etc.
│   │   └── almanac/       static reference catalog (model families, datasets)
│   └── routes/
│       ├── benchmarks/    job submission + listing
│       ├── almanac/       reference catalog pages
│       └── user/          (vestigial — accounts aren't a thing locally)
└── build/                 npm run build output (bundled into Python wheel)
```

`web/src/lib/api.ts:BASE_URL` reads from `window.__ALMANAC_CONFIG__.apiUrl`
first (injected by the backend's `/config.js`), then falls back to
`import.meta.env.VITE_API_URL` (build-time, for the dev server).

---

## Frontend commands

```bash
cd web
npm run dev      # Vite dev server with hot reload (port 5173)
npm run build    # production SPA → web/build/
npm run check    # svelte-check type-check
npm run lint     # prettier
npm run format   # prettier --write
npm run test     # vitest
```

---

## Adding a model

1. Add an entry to `src/ai_almanac/server/config/models.yaml`
   (`id`, `display_name`, `region`, etc.).
2. Set `{REGION}_{ID}_MODEL_DIR=/path/to/model/files` in your shell env or
   a `.env` file at the repo root.
3. Restart `ai-almanac serve`. Models without a directory get filtered out.

No code changes required — the registry is YAML-driven and env-resolved.

---

## Adding Python dependencies

```bash
uv add somepackage          # runtime
uv add --dev somepackage    # dev only
```

Never edit `pyproject.toml` directly — `uv add` keeps the lockfile in sync.

---

## Running tests

```bash
uv run pytest                # unit tests
uv run pytest -k stream      # subset
cd web && npm run test       # frontend (vitest)
```

---

## Building a release wheel

```bash
# Build the SPA first — hatch's force-include needs web/build/ populated
cd web && npm run build
cd ..

# Build the wheel + sdist
uvx --from build python -m build

# The wheel contains the SvelteKit SPA bundled into
# ai_almanac/server/static/, so `pip install dist/ai_almanac-*.whl` ships
# a self-contained ai-almanac.
```
