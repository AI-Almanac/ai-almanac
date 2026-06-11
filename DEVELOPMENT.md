# Development Guide

How to hack on ai-almanac. Pixi manages the Python and Node runtimes, project
dependencies, and development tasks. No Docker or external database is needed.

---

## Architecture overview

```
ai-almanac (one Python process)
├── FastAPI server (uvicorn)
│   ├── /...        JSON API and WebSocket routes
│   ├── /config.js  runtime config injected into the SPA
│   └── /...        bundled SvelteKit SPA (when built)
└── Detached job supervisor → workload process → managed Pixi benchmark env
```

Storage: filesystem under `$AI_ALMANAC_DATA_DIR` (default:
`~/.local/share/ai-almanac/`).
Database: SQLite at `<data-dir>/almanac.db`, auto-migrated on startup.
Auth: none in personal mode; shared deployments use trusted proxy identity and
application authorization. See [`docs/deployment.md`](./docs/deployment.md).

---

## Prerequisites

- [Pixi](https://pixi.sh/) — environment and task manager

Pixi installs Python, Node.js, npm, Process Compose, and the project
dependencies from `pixi.lock`.

---

## Quick start

```bash
git clone <repo>
cd ai-almanac

pixi run dev
```

`pixi run dev` uses Process Compose to run both long-lived services:

- SvelteKit with Vite hot module replacement at `http://localhost:5173`
- FastAPI with Uvicorn auto-reload at `http://localhost:8765`

The frontend receives `VITE_API_URL=http://localhost:8765`, so HTTP and
WebSocket requests target FastAPI while Vite serves and reloads the UI.
Process Compose also runs `npm install` before starting the frontend.

To test a single-process production-style serve, build the SPA first so the
backend can serve it:

```bash
pixi run serve
```

That builds `web/build/` and serves the API and SPA together at
`http://localhost:8765`.

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
    ├── auth.py            request identity, admission, and role authorization
    ├── db.py              async SQLAlchemy access and user persistence
    ├── sync_db.py         supervisor database access and capacity locking
    ├── alembic/           SQLite/PostgreSQL schema migrations
    ├── config/            YAML registries: models.yaml, datasets.yaml,
    │                      regions.yaml, romp.yaml
    ├── routers/           auth, jobs, data, settings, uploads, chat
    ├── services/
    │   ├── job_manager.py detached supervision and restart reconciliation
    │   ├── job_workload.py invokes ROMP through the managed Pixi environment
    │   ├── local_runner.py submits work to the local supervisor
    │   ├── romp.py        renders per-job ROMP configuration
    │   ├── job_events.py  per-job WebSocket pub/sub broker
    │   ├── e2s.py         earth2studio RMSE/MAE/ACC/bias subprocess script
    │   ├── storage.py     local storage implementation
    │   ├── artifacts.py   validates and publishes completed artifacts
    │   ├── metrics.py     ROMP metric domain aggregation
    │   ├── benchmark_*.py benchmark planning state and domain logic
    │   └── chat_*.py      LLM chat state, tools, and figures
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
│   │   ├── account.svelte.ts identity and capability state from /auth/me
│   │   ├── components/    MetricMap, ResultsViewer, JobLogs, etc.
│   │   └── almanac/       static reference catalog (model families, datasets)
│   └── routes/
│       ├── benchmarks/    job submission + listing
│       ├── almanac/       reference catalog pages
│       ├── data-sources/  observation and model source catalog
│       ├── regions/       reusable geographic regions
│       └── settings/      administrator application settings
└── build/                 npm run build output (bundled into Python wheel)
```

`web/src/lib/api.ts:BASE_URL` reads from `window.__ALMANAC_CONFIG__.apiUrl`
first (injected by the backend's `/config.js`), then falls back to
`import.meta.env.VITE_API_URL` (build-time, for the dev server).

---

## Frontend commands

```bash
pixi run frontend     # Vite dev server only
pixi run build-web    # production SPA → web/build/
pixi run check-web    # svelte-check type-check
pixi run test-web     # vitest
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
pixi add --pypi somepackage
pixi add --pypi --feature dev somepackage
```

Use `pixi add` rather than editing dependency declarations manually so
`pyproject.toml` and `pixi.lock` stay synchronized.

---

## Running tests

```bash
pixi run test                # Python and frontend tests
pixi run test-python         # Python tests
pixi run test-python -k stream
pixi run test-web            # frontend tests
pixi run check               # Ruff and svelte-check
```

---

## Building a release wheel

```bash
pixi run build
```

This builds the SvelteKit SPA first, then creates the wheel and source
distribution. The wheel includes the SPA under `ai_almanac/server/static/`.
