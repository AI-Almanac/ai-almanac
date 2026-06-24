FROM node:22-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
ARG VITE_GLOBUS_CLIENT_ID
ARG VITE_GLOBUS_REDIRECT_URL
ENV VITE_GLOBUS_CLIENT_ID=$VITE_GLOBUS_CLIENT_ID \
    VITE_GLOBUS_REDIRECT_URL=$VITE_GLOBUS_REDIRECT_URL
RUN npm run build

FROM ghcr.io/prefix-dev/pixi:latest AS pixi

FROM python:3.12-slim-bookworm AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
COPY modal ./modal
COPY --from=frontend /build/web/build ./web/build
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIXI_NO_PROGRESS=1
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system almanac \
    && useradd --system --gid almanac --create-home almanac
COPY --from=pixi /usr/local/bin/pixi /usr/local/bin/pixi
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels
USER almanac
WORKDIR /home/almanac
EXPOSE 8765
CMD ["ai-almanac", "serve", "--bind", "0.0.0.0", "--port", "8765", "--no-open"]
