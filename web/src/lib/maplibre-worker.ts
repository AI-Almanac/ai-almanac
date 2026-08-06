// maplibre-gl v6 under a bundler cannot locate its worker on its own; every
// map surface must import this module once before constructing a Map.
// `?worker&url` (not plain `?url`) makes Vite emit a self-contained worker
// chunk — see the maplibre Vite installation docs.
import { setWorkerUrl } from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';

setWorkerUrl(workerUrl);
