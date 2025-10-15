#!/usr/bin/env bash
set -euo pipefail

IMPORT_DIR="${IMPORT_DIR:-/data/import}"
LIB_DIR="${LIB_DIR:-/data/library}"

echo "[startup] organising once..."
python /app/main.py "$IMPORT_DIR" "$LIB_DIR" --mode move

echo "[watch] monitoring $IMPORT_DIR for new or changed files..."
# Install hint: provided via Dockerfile (inotify-tools)
inotifywait -m -r -e close_write,create,move,delete "$IMPORT_DIR" | while read -r _; do
  # Debounce a little to let large copies finish
  sleep 20
  echo "[watch] change detected — organising..."
  python /app/main.py "$IMPORT_DIR" "$LIB_DIR" --mode move --dupe-mode name --emit-nfo all
done