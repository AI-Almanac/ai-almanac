# AI Almanac web application

This directory contains the SvelteKit SPA bundled into the `ai-almanac` Python
package. It is not deployed as an independent web service in the supported
production architecture.

Run the complete development stack from the repository root:

```bash
pixi run dev
```

This starts Vite at `http://localhost:5173` and FastAPI at
`http://localhost:8765`. Vite sends API and WebSocket requests to FastAPI
through `VITE_API_URL`.

Useful frontend-only tasks:

```bash
pixi run frontend
pixi run check-web
pixi run test-web
pixi run build-web
```

The production build uses SvelteKit's static adapter. `web/build/` is included
in the Python wheel and served by FastAPI from the same origin as the API. See
[`../DEVELOPMENT.md`](../DEVELOPMENT.md) for development details and
[`../docs/deployment.md`](../docs/deployment.md) for hosting instructions.
