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
FROM ghcr.io/prefix-dev/pixi:0.63.1-bookworm-slim AS builder
# color-operations has no prebuilt wheel for 3.14 yet and compiles from source.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml pixi.lock README.md ./
COPY src ./src
COPY modal ./modal
COPY --from=frontend /build/web/build ./web/build
# The hook carries the env's activation (PATH, GDAL/PROJ data dirs); the
# workspace itself is not in the final image, so its PIXI_PROJECT_* vars go.
RUN pixi install --locked -e prod \
    && pixi shell-hook -e prod -s bash | grep -v PIXI_PROJECT > /app/activate.sh \
    && echo 'exec "$@"' >> /app/activate.sh

FROM debian:bookworm-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIXI_NO_PROGRESS=1
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system almanac \
    && useradd --system --gid almanac --create-home almanac
COPY --from=builder /usr/local/bin/pixi /usr/local/bin/pixi
# Conda envs are not relocatable; keep the build-time prefix.
COPY --from=builder /app/.pixi/envs/prod /app/.pixi/envs/prod
COPY --from=builder /app/activate.sh /app/activate.sh
USER almanac
WORKDIR /home/almanac
EXPOSE 8765
ENTRYPOINT ["/bin/bash", "/app/activate.sh"]
CMD ["ai-almanac", "serve", "--bind", "0.0.0.0", "--port", "8765", "--no-open"]
