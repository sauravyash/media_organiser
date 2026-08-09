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
# One worker only: the dashboard cache in web.py is per-process, so extra
# workers would each hold their own copy and "Rescan" would refresh just one.
# Threads give concurrency while ffmpeg/beet block. The long timeout covers
# multi-gigabyte uploads, during which the worker cannot heartbeat.
exec gunicorn \
  --bind 0.0.0.0:6767 \
  --workers 1 \
  --threads 8 \
  --timeout 3600 \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile - \
  media_organiser.web:app