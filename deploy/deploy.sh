#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

[[ -f .env.production ]] || { echo 'Missing .env.production' >&2; exit 1; }

COMPOSE=(docker compose --env-file .env.production -f compose.production.yml)

"${COMPOSE[@]}" config --quiet

mkdir -p storage backups
shopt -s nullglob dotglob
storage_items=(storage/*)
shopt -u nullglob dotglob
if ((${#storage_items[@]})); then
  "$ROOT_DIR/deploy/backup.sh"
fi

if ! docker run --rm --user 10001:10001 -v "$ROOT_DIR/storage:/data:Z" alpine:3.20 \
  sh -c 'test -w /data && { test ! -e /data/db.json || test -w /data/db.json; }'; then
  docker run --rm -v "$ROOT_DIR/storage:/data:Z" alpine:3.20 chown -R 10001:10001 /data
fi

"${COMPOSE[@]}" build --pull
"${COMPOSE[@]}" up -d

for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T web wget -qO- http://127.0.0.1/healthz >/dev/null 2>&1 \
    && "${COMPOSE[@]}" exec -T web wget -qO- http://api:8000/api/health >/dev/null 2>&1; then
    "${COMPOSE[@]}" ps
    exit 0
  fi
  sleep 2
done

"${COMPOSE[@]}" ps >&2
"${COMPOSE[@]}" logs --tail=100 api web >&2
exit 1
