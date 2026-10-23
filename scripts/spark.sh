#!/usr/bin/env sh
# Run AI Almanac in one container on an NVIDIA DGX Spark under rootless Docker.
# Needs only docker on the host; pixi and every workload environment live in the
# container. See docs/dgx-spark.md.
set -eu

cd "$(dirname "$0")/.."

: "${ALMANAC_DATA_DIR:=${XDG_DATA_HOME:-$HOME/.local/share}/ai-almanac-spark}"
: "${ALMANAC_PORT:=8765}"
# Your own data in ./datasets (git- and docker-ignored) when present, else the
# bundled fixtures.
if [ -z "${ALMANAC_DATASETS_DIR:-}" ]; then
  if [ -d datasets ]; then
    ALMANAC_DATASETS_DIR="$PWD/datasets"
  else
    ALMANAC_DATASETS_DIR="$PWD/testdata"
  fi
fi
export ALMANAC_DATA_DIR ALMANAC_PORT ALMANAC_DATASETS_DIR

# Talk to this user's rootless daemon even when the CLI's current context is the
# system one (which other services on the Spark may need it to be).
if [ -z "${DOCKER_HOST:-}" ]; then
  rootless_sock="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/docker.sock"
  if [ -S "$rootless_sock" ]; then
    export DOCKER_HOST="unix://$rootless_sock"
  fi
fi

compose="docker compose -f compose.spark.yaml"

usage() {
  cat >&2 <<'EOF'
usage: scripts/spark.sh <command>

  up        build the image, start the container, print the URL to open
  prepare   install the workload environments inside the container
  init ...  headless first-run setup (ai-almanac init --yes ...), then restart
  url       print the access (and first-run setup) URL again
  logs      follow the container logs
  shell     open a shell in the running container
  status    show container state and GPU visibility
  down      stop the container (data is kept)

Environment:
  ALMANAC_DATA_DIR      host directory for the database, jobs, environments, and
                        caches (default: ~/.local/share/ai-almanac-spark)
  ALMANAC_DATASETS_DIR  host directory mounted read-only at /datasets
                        (default: ./datasets if it exists, else ./testdata)
  ALMANAC_PORT          loopback port (default: 8765)
EOF
  exit 2
}

preflight() {
  if ! docker info >/dev/null 2>&1; then
    printf 'docker is not reachable. Start rootless Docker with:\n' >&2
    printf '  dockerd-rootless-setuptool.sh install && systemctl --user start docker\n' >&2
    exit 1
  fi
  if ! docker info --format '{{.SecurityOptions}}' | grep -q rootless; then
    printf 'warning: this docker daemon is not rootless; files in %s will be owned by root.\n' \
      "$ALMANAC_DATA_DIR" >&2
  fi
  if ! docker info 2>/dev/null | grep -q 'nvidia.com/gpu=all'; then
    printf 'docker sees no CDI GPU device (nvidia.com/gpu=all).\n' >&2
    printf 'Ask an administrator to run: sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml\n' >&2
    exit 1
  fi
}

print_urls() {
  lines=$($compose logs --no-log-prefix almanac 2>/dev/null \
    | grep -E 'access token required|first-run setup' | tail -n 2)
  if [ -z "$lines" ]; then
    printf 'no URL in the logs yet; try again in a moment or run: scripts/spark.sh logs\n' >&2
    return 1
  fi
  printf '%s\n' "$lines"
}

[ "$#" -ge 1 ] || usage
cmd=$1
shift
case "$cmd" in
  init) ;;
  *) [ "$#" -eq 0 ] || usage ;;
esac

case "$cmd" in
  up)
    preflight
    mkdir -p "$ALMANAC_DATA_DIR"
    $compose up --build --detach --wait
    printf '\nAI Almanac is running (data: %s)\n' "$ALMANAC_DATA_DIR"
    print_urls
    printf '\nInstall the workload environments with the setup wizard or: scripts/spark.sh prepare\n'
    ;;
  prepare) $compose exec almanac ai-almanac env prepare ;;
  init)
    # The server reads settings at startup, so restart it to pick up the result.
    $compose exec almanac ai-almanac init --yes --dataset-mount-roots /datasets "$@"
    $compose restart almanac
    ;;
  url) print_urls ;;
  logs) $compose logs --follow almanac ;;
  shell) $compose exec almanac bash ;;
  status)
    $compose ps
    $compose exec almanac nvidia-smi -L
    ;;
  down) $compose down ;;
  *) usage ;;
esac
