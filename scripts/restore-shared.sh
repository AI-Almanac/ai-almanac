#!/usr/bin/env sh
set -eu

database_dump="${1:?usage: restore-shared.sh DATABASE_DUMP FILES_ARCHIVE}"
files_archive="${2:?usage: restore-shared.sh DATABASE_DUMP FILES_ARCHIVE}"

docker compose stop app
docker compose exec -T postgres dropdb -U almanac --if-exists almanac
docker compose exec -T postgres createdb -U almanac almanac
docker compose exec -T postgres pg_restore -U almanac -d almanac --clean --if-exists < "$database_dump"
docker run --rm \
  -v ai-almanac-web-python-first_persistent_data:/data \
  -v "$(dirname "$files_archive"):/backup:ro" \
  alpine sh -c "rm -rf /data/* && tar -C /data -xzf /backup/$(basename "$files_archive")"
docker compose up -d migrate app
