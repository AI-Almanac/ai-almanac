#!/usr/bin/env sh
set -eu

project="${COMPOSE_PROJECT_NAME:-ai-almanac-local}"
runner="stub"
compose="docker compose -p $project -f compose.local.yaml"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --runner)
      if [ "$#" -lt 2 ]; then
        printf '%s requires a value\n' "$1" >&2
        exit 2
      fi
      runner="$2"
      shift 2
      ;;
    stub|gpu)
      runner="$1"
      shift
      ;;
    *)
      printf 'usage: %s [stub|gpu] [--runner stub|gpu|modal]\n' "$0" >&2
      exit 2
      ;;
  esac
done

case "$runner" in
  stub) ;;
  gpu) compose="$compose -f compose.local-gpu.yaml" ;;
  modal) ;;
  *)
    printf 'unsupported runner: %s\n' "$runner" >&2
    exit 2
    ;;
esac

$compose up --build --detach --wait

printf '\nAI Almanac shared development stack is ready:\n'
printf '  App:           http://localhost:18080\n'
printf '  Switch users:  http://localhost:18080/__dev\n'
printf '  Administrator: http://localhost:18080/__dev/login/admin\n'
printf '  Regular user:  http://localhost:18080/__dev/login/user\n'
if [ "$runner" = "gpu" ]; then
  printf '  Runner:         Pixi benchmark environment with GPU access\n'
elif [ "$runner" = "modal" ]; then
  printf '  Runner:         Modal\n'
else
  printf '  Runner:         Synthetic benchmark outputs (no GPU required)\n'
fi
printf '\nRun `pixi run self-host-local-logs` to follow logs.\n'
