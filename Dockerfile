# Two pixi versions: the builder needs one new enough to read pixi.lock; the
# image ships the one ai_almanac.envs.pixi_bootstrap pins (PIXI_VERSION), since
# that is what the workload env manifests are written and tested against.
# tests/test_pixi_pins.py keeps PIXI_RUNTIME_VERSION in sync.
ARG PIXI_RUNTIME_VERSION=0.67.2

FROM ghcr.io/prefix-dev/pixi:${PIXI_RUNTIME_VERSION}-bookworm-slim AS runtime-pixi

FROM node:26-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
ARG VITE_GLOBUS_CLIENT_ID
ARG VITE_GLOBUS_REDIRECT_URL
ENV VITE_GLOBUS_CLIENT_ID=$VITE_GLOBUS_CLIENT_ID \
    VITE_GLOBUS_REDIRECT_URL=$VITE_GLOBUS_REDIRECT_URL
RUN npm run build

# Install from pixi.lock so the image ships exactly the versions CI tests.
FROM ghcr.io/prefix-dev/pixi:0.82.0-bookworm-slim AS builder
# color-operations has no prebuilt wheel for 3.14 yet and compiles from source.
# The pixi image has no CA bundle, and PyPI downloads verify against the system's.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# prod (Cloud Run) carries the `cloud` extra; local (DGX Spark, see
# compose.spark.yaml) is the same wheel without it.
ARG PIXI_ENV=prod
# Dependencies first, against a stub of the package, so this layer depends on
# the manifest and lockfile alone and a code change doesn't reinstall them.
# The cache mount keeps downloads (and the source-built wheel) for when it does
# rerun.
COPY pyproject.toml pixi.lock README.md LICENSE ./
RUN mkdir -p src/ai_almanac web/build modal \
    && touch src/ai_almanac/__init__.py modal/blending_app.py
RUN --mount=type=cache,target=/root/.cache,sharing=locked \
    pixi install --locked -e ${PIXI_ENV}
# Then the real package over the stub, without touching its dependencies. uv
# rather than a second `pixi install`, which doesn't rebuild a path dependency
# whose source alone changed (pixi 0.82), so would keep the stub.
COPY src ./src
COPY modal ./modal
COPY --from=frontend /build/web/build ./web/build
RUN --mount=type=cache,target=/root/.cache,sharing=locked \
    pixi exec --spec uv -- uv pip install --python .pixi/envs/${PIXI_ENV}/bin/python \
        --no-deps --reinstall-package ai-almanac .
# The final stage replays the env's activation scripts as static ENV, so any
# command works without a shell wrapper (Cloud Run `command` replaces
# ENTRYPOINT). Fail the build if a dependency adds a script the ENV misses.
RUN ls .pixi/envs/${PIXI_ENV}/etc/conda/activate.d/*.sh | xargs -n1 basename | sort | tr '\n' ' ' \
        | grep -qx 'gdal-activate.sh libxml2-split_activate.sh proj4-activate.sh ' \
    || { echo "Conda activation scripts changed; update the ENV block in the final stage." >&2; exit 1; }

FROM debian:bookworm-slim
ARG PIXI_ENV=prod
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIXI_NO_PROGRESS=true \
    CONDA_PREFIX=/app/.pixi/envs/${PIXI_ENV} \
    PATH=/app/.pixi/envs/${PIXI_ENV}/bin:$PATH \
    GDAL_DATA=/app/.pixi/envs/${PIXI_ENV}/share/gdal \
    GDAL_DRIVER_PATH=/app/.pixi/envs/${PIXI_ENV}/lib/gdalplugins \
    CPL_ZIP_ENCODING=UTF-8 \
    XML_CATALOG_FILES="file:///app/.pixi/envs/${PIXI_ENV}/etc/xml/catalog file:///etc/xml/catalog" \
    PROJ_DATA=/app/.pixi/envs/${PIXI_ENV}/share/proj \
    PROJ_NETWORK=ON
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system almanac \
    && useradd --system --gid almanac --create-home almanac
COPY --from=runtime-pixi /usr/local/bin/pixi /usr/local/bin/pixi
# Conda envs are not relocatable; keep the build-time prefix.
COPY --from=builder /app/.pixi/envs/${PIXI_ENV} /app/.pixi/envs/${PIXI_ENV}
USER almanac
WORKDIR /home/almanac
EXPOSE 8765
CMD ["ai-almanac", "serve", "--bind", "0.0.0.0", "--port", "8765", "--no-open"]
