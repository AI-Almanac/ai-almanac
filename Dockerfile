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
FROM ghcr.io/prefix-dev/pixi:0.81.0-bookworm-slim AS builder
# color-operations has no prebuilt wheel for 3.14 yet and compiles from source.
# The pixi image has no CA bundle, and PyPI downloads verify against the system's.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml pixi.lock README.md ./
COPY src ./src
COPY modal ./modal
COPY --from=frontend /build/web/build ./web/build
# The final stage replays the env's activation scripts as static ENV, so any
# command works without a shell wrapper (Cloud Run `command` replaces
# ENTRYPOINT). Fail the build if a dependency adds a script the ENV misses.
RUN pixi install --locked -e prod
RUN ls .pixi/envs/prod/etc/conda/activate.d/*.sh | xargs -n1 basename | sort | tr '\n' ' ' \
        | grep -qx 'gdal-activate.sh libxml2-split_activate.sh proj4-activate.sh ' \
    || { echo "Conda activation scripts changed; update the ENV block in the final stage." >&2; exit 1; }

FROM debian:bookworm-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIXI_NO_PROGRESS=1 \
    CONDA_PREFIX=/app/.pixi/envs/prod \
    PATH=/app/.pixi/envs/prod/bin:$PATH \
    GDAL_DATA=/app/.pixi/envs/prod/share/gdal \
    GDAL_DRIVER_PATH=/app/.pixi/envs/prod/lib/gdalplugins \
    CPL_ZIP_ENCODING=UTF-8 \
    XML_CATALOG_FILES="file:///app/.pixi/envs/prod/etc/xml/catalog file:///etc/xml/catalog" \
    PROJ_DATA=/app/.pixi/envs/prod/share/proj \
    PROJ_NETWORK=ON
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system almanac \
    && useradd --system --gid almanac --create-home almanac
COPY --from=builder /usr/local/bin/pixi /usr/local/bin/pixi
# Conda envs are not relocatable; keep the build-time prefix.
COPY --from=builder /app/.pixi/envs/prod /app/.pixi/envs/prod
USER almanac
WORKDIR /home/almanac
EXPOSE 8765
CMD ["ai-almanac", "serve", "--bind", "0.0.0.0", "--port", "8765", "--no-open"]
