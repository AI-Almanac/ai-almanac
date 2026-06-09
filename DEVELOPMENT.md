# Development Guide

How to hack on ai-almanac. Pixi manages the Python and Node runtimes, project
dependencies, and development tasks. No Docker or external database is needed.

---

## Architecture overview

```
ai-almanac (one Python process)
├── FastAPI server (uvicorn)
│   ├── /api/...    JSON API
│   ├── /config.js  runtime config injected into the SPA
│   └── /...        bundled SvelteKit SPA (when built)
└── Durable supervisor → workload process → `pixi run momp-run`
```

Storage: filesystem under `$AI_ALMANAC_DATA_DIR` (default:
`~/.local/share/ai-almanac/`).
Database: SQLite at `<data-dir>/almanac.db`, auto-migrated on startup.
Auth: none — see [`DEPLOY_PUBLIC.md`](./DEPLOY_PUBLIC.md) for reverse-proxy setup.

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
    ├── db.py              SQLAlchemy async + auto-migrate
    ├── attribution.py     reads X-Forwarded-User → CurrentUser shim
    ├── alembic/           migrations (collapsed to one SQLite-native baseline)
    ├── config/            YAML registries: models.yaml, datasets.yaml,
    │                      regions.yaml, romp.yaml
    ├── routers/           jobs, datasets, config, regions, chat
    ├── services/
    │   ├── romp.py        renders per-job ROMP configuration
    │   ├── job_workload.py invokes ROMP through the managed Pixi environment
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
