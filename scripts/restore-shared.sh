#!/usr/bin/env sh
# Restore a shared deployment from a backup-shared.sh pair. DESTRUCTIVE:
# replaces the database and the persistent data volume. A pre-restore database
# snapshot is written to a temporary directory before anything is dropped.
#
# Set COMPOSE to target a non-default stack, e.g.
#   COMPOSE="docker compose -p ai-almanac-local -f compose.local.yaml" ...
set -eu

confirm=0
if [ "${1:-}" = "--yes" ]; then
  confirm=1
  shift
fi

database_dump="${1:?usage: restore-shared.sh [--yes] DATABASE_DUMP FILES_ARCHIVE}"
files_archive="${2:?usage: restore-shared.sh [--yes] DATABASE_DUMP FILES_ARCHIVE}"
compose="${COMPOSE:-docker compose}"

app_container="$($compose ps -q app)"
if [ -z "$app_container" ]; then
  echo "error: app service is not running; start the stack before restoring" >&2
  exit 1
fi
data_volume="$(docker inspect -f \
  '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' \
  "$app_container")"
if [ -z "$data_volume" ]; then
  echo "error: could not resolve the /data volume from the app container" >&2
  exit 1
fi

if [ "$confirm" -ne 1 ]; then
  printf 'This will REPLACE the database and the contents of volume %s.\n' "$data_volume"
  printf 'Type "restore" to continue: '
  read -r answer
  if [ "$answer" != "restore" ]; then
    echo "aborted"
    exit 1
  fi
fi

snapshot_dir="$(mktemp -d)"
echo "taking pre-restore database snapshot in $snapshot_dir"
$compose exec -T postgres pg_dump -U almanac -d almanac -Fc \
  > "$snapshot_dir/pre-restore.dump" \
  || echo "warning: pre-restore snapshot failed; continuing" >&2

$compose stop caddy app
$compose exec -T postgres dropdb -U almanac --if-exists almanac
$compose exec -T postgres createdb -U almanac almanac
$compose exec -T postgres pg_restore -U almanac -d almanac --clean --if-exists \
  < "$database_dump"
docker run --rm \
  -v "$data_volume:/data" \
  -v "$(cd "$(dirname "$files_archive")" && pwd):/backup:ro" \
  alpine sh -c "rm -rf /data/* && tar -C /data -xzf /backup/$(basename "$files_archive")"
$compose up --detach --wait

echo "restore complete; pre-restore snapshot kept at $snapshot_dir/pre-restore.dump"
