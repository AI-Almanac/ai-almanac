#!/usr/bin/env sh
# Live backup of the shared deployment: a pg_dump of the database plus an
# archive of the persistent data volume. Zero downtime; the archive may lag
# the dump by a few seconds (job reconciliation handles the skew on restore).
#
# Set COMPOSE to target a non-default stack, e.g.
#   COMPOSE="docker compose -p ai-almanac-local -f compose.local.yaml" ...
set -eu

destination="${1:?usage: backup-shared.sh DESTINATION_DIRECTORY}"
compose="${COMPOSE:-docker compose}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

app_container="$($compose ps -q app)"
if [ -z "$app_container" ]; then
  echo "error: app service is not running; start the stack before backing up" >&2
  exit 1
fi
data_volume="$(docker inspect -f \
  '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' \
  "$app_container")"
if [ -z "$data_volume" ]; then
  echo "error: could not resolve the /data volume from the app container" >&2
  exit 1
fi

mkdir -p "$destination"
$compose exec -T postgres pg_dump -U almanac -d almanac -Fc \
  > "$destination/postgres-$timestamp.dump"
docker run --rm \
  -v "$data_volume:/data:ro" \
  -v "$destination:/backup" \
  alpine tar -C /data -czf "/backup/files-$timestamp.tar.gz" .

echo "backup written: $destination/postgres-$timestamp.dump"
echo "backup written: $destination/files-$timestamp.tar.gz"
