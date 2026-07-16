#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE=(docker compose --env-file .env.production -f compose.production.yml)
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%S%N)-$$"
ARCHIVE="$BACKUP_DIR/storage-$TIMESTAMP.tar.gz"

mkdir -p "$BACKUP_DIR"

if "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx api; then
  "${COMPOSE[@]}" stop api
  restart_api=true
else
  restart_api=false
fi

restore_api() {
  if [[ "$restart_api" == true ]]; then
    "${COMPOSE[@]}" start api >/dev/null
  fi
}
trap restore_api EXIT

tar --exclude='*.tmp' --exclude='*.log' -czf "$ARCHIVE" storage

restore_api
restart_api=false
trap - EXIT

find "$BACKUP_DIR" -type f -name 'storage-*.tar.gz' -mtime +"${BACKUP_RETENTION_DAYS:-14}" -delete
printf '%s\n' "$ARCHIVE"
