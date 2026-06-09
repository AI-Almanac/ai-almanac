# CLAUDE.md

Repository guidance for Claude Code.

## Development

Pixi is the project environment and task manager. Do not use `uv` for this
repository.

```bash
pixi run dev
```

This starts:

- SvelteKit with Vite HMR at `http://localhost:5173`
- FastAPI with Uvicorn reload at `http://localhost:8765`

Useful tasks:

```bash
pixi run test
pixi run check
pixi run build
pixi run backend
pixi run frontend
```

Use `pixi add --pypi <package>` for Python runtime dependencies and
`pixi add --pypi --feature dev <package>` for development dependencies.
Use npm from `web/` for frontend dependencies.

## Architecture

- `src/ai_almanac/`: local-first Python package and CLI
- `src/ai_almanac/server/`: FastAPI API, SQLite persistence, job supervision
- `src/ai_almanac/server/config/`: packaged model, dataset, region, and ROMP defaults
- `web/`: SvelteKit frontend
- `testdata/`: compact NetCDF fixtures
- `src/ai_almanac/envs/`: separately managed benchmark runtime

The production wheel includes the static frontend build. Development uses the
Vite server directly; `VITE_API_URL` points it at FastAPI.

## Testing

Run focused tests while working, then use:

```bash
pixi run test
pixi run check
```

Python tests use a temporary SQLite data directory. Frontend tests use Vitest.

## Data Sources

Runtime data sources and regions are managed through the application database.
Packaged YAML files seed an empty installation and provide defaults.

Model initialization weekdays use Python weekday numbering: Monday is `0` and
Sunday is `6`.
