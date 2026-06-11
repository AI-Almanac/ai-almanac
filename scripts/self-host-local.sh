#!/usr/bin/env sh
set -eu

project="${COMPOSE_PROJECT_NAME:-ai-almanac-local}"
mode="${1:-stub}"
compose="docker compose -p $project -f compose.local.yaml"

if [ "$mode" = "gpu" ]; then
  compose="$compose -f compose.local-gpu.yaml"
elif [ "$mode" != "stub" ]; then
  printf 'usage: %s [stub|gpu]\n' "$0" >&2
  exit 2
fi

$compose up --build --detach --wait

printf '\nAI Almanac shared development stack is ready:\n'
printf '  App:           http://localhost:18080\n'
printf '  Switch users:  http://localhost:18080/__dev\n'
printf '  Administrator: http://localhost:18080/__dev/login/admin\n'
printf '  Regular user:  http://localhost:18080/__dev/login/user\n'
if [ "$mode" = "gpu" ]; then
  printf '  Runner:         Pixi benchmark environment with GPU access\n'
else
  printf '  Runner:         Synthetic benchmark outputs (no GPU required)\n'
fi
printf '\nRun `pixi run self-host-local-logs` to follow logs.\n'
