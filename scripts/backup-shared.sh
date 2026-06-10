#!/usr/bin/env sh
set -eu

destination="${1:?usage: backup-shared.sh DESTINATION_DIRECTORY}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$destination"

docker compose exec -T postgres pg_dump -U almanac -d almanac -Fc \
  > "$destination/postgres-$timestamp.dump"
docker run --rm \
  -v ai-almanac-web-python-first_persistent_data:/data:ro \
  -v "$destination:/backup" \
  alpine tar -C /data -czf "/backup/files-$timestamp.tar.gz" .
