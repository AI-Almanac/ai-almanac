#!/usr/bin/env sh
# Run AI Almanac in one container on an NVIDIA DGX Spark under rootless Docker.
# Needs only docker on the host; pixi and every workload environment live in the
# container. See docs/dgx-spark.md.
set -eu

cd "$(dirname "$0")/.."

: "${ALMANAC_DATA_DIR:=${XDG_DATA_HOME:-$HOME/.local/share}/ai-almanac-spark}"
: "${ALMANAC_PORT:=8765}"
# The datasets live in ./datasets (git- and docker-ignored), registered under
# the names in deploy/spark-datasets.yaml.
: "${ALMANAC_DATASETS_DIR:=$PWD/datasets}"
# What a fresh install's assistant runs; change it later in Settings.
: "${ALMANAC_LLM_MODEL:=nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4}"
export ALMANAC_DATA_DIR ALMANAC_PORT ALMANAC_DATASETS_DIR
# Where compose.spark.yaml mounts deploy/spark-datasets.yaml.
dataset_names=/etc/ai-almanac/spark-datasets.yaml

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

  up        build the image, start the container, print the URL to open; on a
            fresh data directory, also set up (local assistant model, workload
            environments, model weights), else reinstall any workload
            environment the new image changed, then register any new datasets
  prepare   install the workload environments inside the container
  init ...  headless first-run setup (ai-almanac init --yes ...), then restart
  discover [--register]
            list the data sources found under /datasets; --register adds the ready ones
  url       print the access (and first-run setup) URL again
  logs      follow the container logs
  shell     open a shell in the running container
  status    show container state and GPU visibility
  down      stop the container (data is kept)

Environment:
  ALMANAC_DATA_DIR      host directory for the database, jobs, environments, and
                        caches (default: ~/.local/share/ai-almanac-spark)
  ALMANAC_DATASETS_DIR  host directory mounted read-only at /datasets
                        (default: ./datasets)
  ALMANAC_PORT          loopback port (default: 8765)
  ALMANAC_LLM_MODEL     the local assistant model a fresh install runs
                        (default: nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4)
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
  # Only the latest start's lines: an earlier one's setup link has expired.
  lines=$($compose logs --no-log-prefix almanac 2>/dev/null \
    | awk '/ serving at / { found = "" } /access token required|first-run setup/ { found = found $0 "\n" }
           END { printf "%s", found }')
  if [ -z "$lines" ]; then
    printf 'no URL in the logs yet; try again in a moment or run: scripts/spark.sh logs\n' >&2
    return 1
  fi
  printf '%s\n' "$lines"
}

# Whether this data directory still needs its first-run setup.
needs_setup() {
  $compose exec -T almanac python -c '
from ai_almanac.settings import reload_settings
reload_settings()
from ai_almanac.server.services.setup import setup_required
raise SystemExit(0 if setup_required() else 1)'
}

# A bind mount holds on to the directory it was given, so after the datasets
# directory is deleted and recreated (rather than copied into) the container
# still sees the old, empty one until it is recreated.
datasets_mount_is_stale() {
  inside=$($compose exec -T almanac stat -c %i /datasets 2>/dev/null) || return 1
  [ "$(stat -c %i "$ALMANAC_DATASETS_DIR")" != "$inside" ]
}

[ "$#" -ge 1 ] || usage
cmd=$1
shift
case "$cmd" in
  init | discover) ;;
  *) [ "$#" -eq 0 ] || usage ;;
esac

case "$cmd" in
  up)
    preflight
    mkdir -p "$ALMANAC_DATA_DIR" "$ALMANAC_DATASETS_DIR"
    $compose up --build --detach --wait
    if datasets_mount_is_stale; then
      printf '\n%s was replaced since the container started; recreating it to see the new one\n' \
        "$ALMANAC_DATASETS_DIR"
      $compose up --detach --wait --force-recreate
    fi
    if needs_setup; then
      printf '\nFirst start: setting up with %s (the downloads take a while)\n' "$ALMANAC_LLM_MODEL"
      $compose exec -T almanac ai-almanac init --yes --if-needed --local-llm \
        --llm-model "$ALMANAC_LLM_MODEL" --dataset-mount-roots /datasets
      setup_ran=1
    else
      # A rebuild may change a workload environment's pins, and jobs refuse to
      # run in one installed for another version.
      if ! $compose exec -T almanac ai-almanac env prepare --if-stale; then
        printf '\nCould not update the workload environments; jobs will refuse to run until\n' >&2
        printf 'scripts/spark.sh prepare succeeds\n' >&2
      fi
    fi
    # Sources are read per request, so registered ones appear without a restart.
    printf '\nRegistering new datasets in %s\n' "$ALMANAC_DATASETS_DIR"
    $compose exec -T almanac ai-almanac sources discover --register --names "$dataset_names"
    if [ -n "${setup_ran:-}" ]; then
      # The server reads settings at startup.
      $compose restart almanac
      $compose up --detach --wait
    fi
    printf '\nAI Almanac is running (data: %s)\n' "$ALMANAC_DATA_DIR"
    print_urls
    ;;
  prepare) $compose exec almanac ai-almanac env prepare ;;
  discover) $compose exec almanac ai-almanac sources discover --names "$dataset_names" "$@" ;;
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
