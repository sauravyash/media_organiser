#!/usr/bin/env bash
set -euo pipefail

IMPORT_DIR="${IMPORT_DIR:-/data/import}"
LIB_DIR="${LIB_DIR:-/data/library}"
export IMPORT_DIR LIB_DIR

echo "[startup] organising once..."
python /app/main.py "$IMPORT_DIR" "$LIB_DIR" --mode move

echo "[watch] monitoring $IMPORT_DIR for new or changed files..."
# Run inotify loop in background so we can start the web server
(
  inotifywait -m -r -e close_write,create,move,delete "$IMPORT_DIR" | while read -r _; do
    sleep 20
    echo "[watch] change detected — organising..."
    python /app/main.py "$IMPORT_DIR" "$LIB_DIR" --mode move --dupe-mode name --emit-nfo all --carry-posters keep
  done
) &

echo "[web] starting upload interface on port 6767..."
exec flask --app media_organiser.web:app run --host 0.0.0.0 --port 6767