"""Web upload interface and read-only library dashboards for media_organiser."""
import os
import time
from pathlib import Path

from flask import Flask, request, render_template, jsonify, redirect, url_for, send_file, abort
from werkzeug.exceptions import RequestEntityTooLarge

from . import audio_tools
from . import fixes
from . import musicbrainz_client
from .library import audit_movies, get_movies_dir
from .music import scan_music

app = Flask(
    __name__,
    template_folder=Path(__file__).resolve().parent / "templates",
    static_folder=Path(__file__).resolve().parent / "static",
)

# Allow up to 30GB by default, configurable via MAX_UPLOAD_SIZE env var
max_upload_size = int(os.environ.get("MAX_UPLOAD_SIZE", 30 * 1024 * 1024 * 1024))
app.config["MAX_CONTENT_LENGTH"] = max_upload_size


# Scanning a large library is slow, so dashboard payloads are memoised for a
# short window. "Rescan" in the UI sends ?refresh=1 to bypass the cache.
_dashboard_cache: dict[str, tuple[float, dict]] = {}


def _cache_ttl() -> float:
    try:
        return float(os.environ.get("DASHBOARD_CACHE_TTL", 60))
    except ValueError:
        return 60.0


def _cached(key: str, builder, refresh: bool = False) -> dict:
    """Return ``builder()`` output, reusing a recent result unless refreshing."""
    ttl = _cache_ttl()
    if not refresh and ttl > 0:
        hit = _dashboard_cache.get(key)
        if hit and (time.monotonic() - hit[0]) < ttl:
            return hit[1]
    data = builder()
    _dashboard_cache[key] = (time.monotonic(), data)
    return data


def _wants_refresh() -> bool:
    return request.args.get("refresh") in ("1", "true", "yes")


def _invalidate_dashboard_cache() -> None:
    """Drop memoised scans after a write, so the next read sees the new names."""
    _dashboard_cache.clear()


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def get_import_dir() -> Path:
    path = Path(os.environ.get("IMPORT_DIR", "./data/import"))
    path.mkdir(parents=True, exist_ok=True)
    # Always return resolved (absolute) path to avoid issues with relative_to()
    return path.resolve()


def get_music_export_dir() -> Path:
    path = Path(os.environ.get("MUSIC_LIB_DIR", "./data/music"))
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _safe_relative_path(import_dir: Path, rel_path: str) -> Path | None:
    """Resolve rel_path under import_dir, rejecting path traversal. Returns None if invalid."""
    base = import_dir.resolve()
    try:
        resolved = (import_dir / rel_path).resolve()
        # Check if resolved is within base: base must be resolved itself or a parent
        # For files: base will be in resolved.parents
        # For edge case where resolved == base: check equality
        if base == resolved or base in resolved.parents:
            return resolved
    except (ValueError, OSError):
        pass
    return None


@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    """Handle 413 Request Entity Too Large errors."""
    if request.accept_mimetypes.best == "application/json":
        max_size_gb = app.config["MAX_CONTENT_LENGTH"] / (1024 * 1024 * 1024)
        return jsonify({
            "error": f"File too large. Maximum upload size is {max_size_gb:.1f} GB."
        }), 413
    return redirect(url_for("index"))


@app.route("/")
def index():
    return render_template("upload.html", active_mode="video")


@app.route("/music")
def music_upload():
    return render_template("music_upload.html", active_mode="music")


@app.route("/music/preview")
def music_preview():
    music_dir = get_music_export_dir()
    rel_path = request.args.get("path")
    if not rel_path:
        abort(400)
    dest = _safe_relative_path(music_dir, rel_path)
    if dest is None or not dest.is_file():
        abort(404)
    suffix = dest.suffix.lower()
    if suffix == ".mp3":
        mimetype = "audio/mpeg"
    elif suffix == ".flac":
        mimetype = "audio/flac"
    elif suffix in {".m4a", ".aac"}:
        mimetype = "audio/aac"
    elif suffix == ".ogg":
        mimetype = "audio/ogg"
    elif suffix == ".wav":
        mimetype = "audio/wav"
    else:
        mimetype = "application/octet-stream"
    return send_file(dest, mimetype=mimetype, conditional=True)


@app.route("/api/music/metadata", methods=["POST"])
def music_metadata():
    music_dir = get_music_export_dir()
    payload = request.get_json(silent=True) or {}
    paths = payload.get("paths") or []
    tracks: list[dict] = []
    for rel in paths:
        if not isinstance(rel, str):
            continue
        dest = _safe_relative_path(music_dir, rel)
        if dest is None or not dest.is_file():
            continue
        analysis = audio_tools.analyse_audio(dest)
        tracks.append(
            {
                "path": dest.relative_to(music_dir).as_posix(),
                "title": analysis.title,
                "artist": analysis.artist,
                "album": analysis.album,
                "year": analysis.year,
                "track_number": analysis.track_number,
                "bitrate_kbps": analysis.bitrate_kbps,
                "sample_rate": analysis.sample_rate,
                "duration_seconds": analysis.duration_seconds,
                "codec_name": analysis.codec_name,
                "quality_status": analysis.quality_status,
                "quality_message": analysis.quality_message,
                "rejected_reason": analysis.rejected_reason,
                "needs_transcode": analysis.needs_transcode,
            }
        )
    return jsonify({"tracks": tracks})


@app.route("/api/music/apply-tags", methods=["POST"])
def music_apply_tags():
    music_dir = get_music_export_dir()
    payload = request.get_json(silent=True) or {}
    tracks = payload.get("tracks") or []
    results: list[dict] = []
    for t in tracks:
        rel = t.get("path")
        if not isinstance(rel, str):
            continue
        dest = _safe_relative_path(music_dir, rel)
        if dest is None or not dest.is_file():
            results.append({"path": rel, "status": "error", "reason": "Invalid path"})
            continue
        quality = audio_tools.detect_bitrate_and_quality(dest)
        if quality.get("rejected_reason"):
            results.append({"path": rel, "status": "rejected", "reason": quality["rejected_reason"]})
            continue
        tags = {
            "title": t.get("title") or "",
            "artist": t.get("artist") or "",
            "album": t.get("album") or "",
            "year": t.get("year") or "",
            "track_number": t.get("track_number") or "",
        }
        try:
            audio_tools.apply_id3_tags(dest, tags)
            results.append({"path": rel, "status": "ok"})
        except Exception as e:
            results.append({"path": rel, "status": "error", "reason": str(e)})
    return jsonify({"status": "ok", "results": results})


@app.route("/api/music/musicbrainz", methods=["POST"])
def music_musicbrainz():
    payload = request.get_json(silent=True) or {}
    title = payload.get("title") or None
    artist = payload.get("artist") or None
    album = payload.get("album") or None
    duration = payload.get("duration_seconds")
    suggestions = musicbrainz_client.search_track_top_n(
        artist=artist,
        title=title,
        album=album,
        duration_seconds=duration,
        limit=5,
    )
    return jsonify({"suggestions": suggestions})


@app.route("/api/music/transcode", methods=["POST"])
def music_transcode():
    music_dir = get_music_export_dir()
    payload = request.get_json(silent=True) or {}
    rel = payload.get("path")
    if not isinstance(rel, str):
        return jsonify({"status": "error", "reason": "Missing path"}), 400
    dest = _safe_relative_path(music_dir, rel)
    if dest is None or not dest.is_file():
        return jsonify({"status": "error", "reason": "Invalid path"}), 400
    scan_library_duplicates = _env_flag("MUSIC_IMPORT_DEDUPE", default=True)
    if "scan_library_duplicates" in payload:
        scan_library_duplicates = bool(payload.get("scan_library_duplicates"))
    result = audio_tools.ensure_mp3_320(
        dest,
        music_dir,
        scan_library_duplicates=scan_library_duplicates,
    )
    if result.get("output_path"):
        out_path = Path(result["output_path"])
        try:
            rel_out = out_path.relative_to(music_dir).as_posix()
        except ValueError:
            rel_out = out_path.name
        result["output_path"] = rel_out
    return jsonify(result)


@app.route("/upload", methods=["POST"])
def upload():
    # Music UI sends mode=music so uploads go to MUSIC_LIB_DIR instead of IMPORT_DIR
    use_music_dir = request.form.get("mode") == "music"
    base_dir = get_music_export_dir() if use_music_dir else get_import_dir()
    if "files" in request.files:
        files = request.files.getlist("files")
    elif "file" in request.files and request.files["file"].filename:
        files = [request.files["file"]]
    else:
        # Always return JSON error
        return jsonify({"error": "No files selected"}), 400

    paths = request.form.getlist("paths")
    saved = []
    rejected = []
    
    # Pair files with their paths, filtering out empty files
    file_path_pairs = []
    for i, f in enumerate(files):
        if not f or not f.filename:
            continue
        # Get the relative path, preferring the paths array if available
        if i < len(paths) and paths[i]:
            rel_path = paths[i]
        else:
            rel_path = f.filename
        file_path_pairs.append((f, rel_path))
    
    for f, rel_path in file_path_pairs:
        # Skip if we don't have a valid path
        if not rel_path:
            rejected.append(f.filename or "unknown")
            continue
            
        dest = _safe_relative_path(base_dir, rel_path)
        if dest is None:
            rejected.append(rel_path)
            continue
        
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                stem, ext = dest.stem, dest.suffix
                n = 1
                while dest.exists():
                    dest = dest.parent / f"{stem} ({n}){ext}"
                    n += 1
            # Ensure dest is resolved (absolute) before saving
            dest = dest.resolve()
            f.save(str(dest))
            # Both paths are now guaranteed to be absolute, so relative_to() will work.
            # as_posix() keeps the JSON API on "/" separators on every platform, matching
            # the webkitRelativePath-style paths the browser sends and splits on.
            saved.append(dest.relative_to(base_dir).as_posix())
        except Exception as e:
            # Catch any errors during save (permissions, disk full, etc.)
            rejected.append(f"{rel_path} (error: {str(e)})")
    
    # Return error status code if no files were successfully uploaded
    if not saved:
        # 422 Unprocessable Entity - request was valid but files couldn't be processed
        return jsonify({"saved": saved, "rejected": rejected}), 422
    
    # Always return JSON with saved and rejected lists
    return jsonify({"saved": saved, "rejected": rejected})


@app.route("/library/movies")
def movie_library():
    """Read-only listing of everything already filed under /movies."""
    return render_template(
        "library_movies.html",
        active_mode="movie-library",
        movies_root=str(get_movies_dir()),
    )


@app.route("/api/library/movies")
def api_movie_library():
    """JSON payload backing the movie library dashboard."""
    return jsonify(_cached("movies", audit_movies, refresh=_wants_refresh()))


@app.route("/library/music")
def music_library():
    """Read-only listing of the beets music library."""
    return render_template("library_music.html", active_mode="music-library")


@app.route("/api/library/music")
def api_music_library():
    """JSON payload backing the music library dashboard (from `beet ls`)."""
    return jsonify(_cached("music", scan_music, refresh=_wants_refresh()))


# ---------------------------------------------------------------------------
# Applying fixes
#
# The dashboards above only read. Everything below writes, so each route goes
# through media_organiser.fixes, which journals what it did and can undo it.
# ---------------------------------------------------------------------------

# Issue kinds that need a person to choose between real alternatives, rather
# than a rename the audit could already spell out.
TRIAGE_KINDS = ("multiple-videos", "duplicate-title", "no-video-file")


@app.route("/library/fix")
def fix_page():
    """Bulk-apply the mechanical fixes the audit already worked out."""
    return render_template("library_fix.html", active_mode="movie-fix")


@app.route("/library/triage")
def triage_page():
    """Decide, one folder at a time, which copy of a movie to keep."""
    return render_template("library_triage.html", active_mode="movie-fix")


@app.route("/library/trash")
def trash_page():
    """Everything the fix pipeline has set aside, with undo."""
    return render_template("library_trash.html", active_mode="movie-fix")


@app.route("/api/library/fix/plan")
def api_fix_plan():
    """Mechanical actions grouped by issue kind. Reads only."""
    kinds = [k for k in (request.args.get("kinds") or "").split(",") if k] or None
    payload = _cached("movies", audit_movies, refresh=_wants_refresh())
    plan = fixes.plan_mechanical(payload.get("entries") or [], kinds)
    plan["generated_at"] = payload.get("generated_at")
    plan["root"] = payload.get("root")
    return jsonify(plan)


@app.route("/api/library/fix/apply", methods=["POST"])
def api_fix_apply():
    payload = request.get_json(silent=True) or {}
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        return jsonify({"error": "No actions supplied"}), 400
    if len(actions) > 5000:
        return jsonify({"error": "Too many actions in one batch; apply in smaller runs"}), 400

    result = fixes.apply_actions(actions, dry_run=bool(payload.get("dry_run")))
    if not result.get("dry_run"):
        _invalidate_dashboard_cache()
    return jsonify(result)


@app.route("/api/library/movies/triage")
def api_triage_list():
    """Folders holding a decision, cheapest data first — no hashing here."""
    kinds = set(k for k in (request.args.get("kinds") or "").split(",") if k) or set(TRIAGE_KINDS)
    payload = _cached("movies", audit_movies, refresh=_wants_refresh())
    folders = []
    for entry in payload.get("entries") or []:
        matched = [i for i in (entry.get("issues") or []) if i.get("kind") in kinds]
        if not matched:
            continue
        folders.append({
            "folder": entry.get("folder"),
            "path": entry.get("path"),
            "title": entry.get("title"),
            "year": entry.get("year"),
            "size": entry.get("size"),
            "severity": entry.get("severity"),
            "video_count": len(entry.get("videos") or []),
            "issues": matched,
        })
    return jsonify({
        "root": payload.get("root"),
        "generated_at": payload.get("generated_at"),
        "folders": folders,
        "total": len(folders),
    })


@app.route("/api/library/movies/triage/folder")
def api_triage_folder():
    """Per-file specs for one folder, fingerprinting only same-size candidates."""
    raw = request.args.get("path") or ""
    folder = fixes.resolve_under_root(raw)
    movies_root = get_movies_dir()
    if folder is None or not folder.is_dir():
        return jsonify({"error": "Unknown folder"}), 404
    if folder != movies_root and movies_root not in folder.parents:
        return jsonify({"error": "Folder is outside the movie library"}), 400
    return jsonify(fixes.inspect_folder(folder))


@app.route("/api/library/trash")
def api_trash():
    return jsonify(fixes.trash_summary())


@app.route("/api/library/trash/undo", methods=["POST"])
def api_trash_undo():
    payload = request.get_json(silent=True) or {}
    batch = payload.get("batch")
    if not isinstance(batch, str) or not batch:
        return jsonify({"error": "No batch supplied"}), 400
    result = fixes.undo_batch(batch)
    _invalidate_dashboard_cache()
    return jsonify(result), (400 if result.get("error") else 200)


@app.route("/api/library/trash/empty", methods=["POST"])
def api_trash_empty():
    payload = request.get_json(silent=True) or {}
    batch = payload.get("batch")
    if not isinstance(batch, str) or not batch:
        return jsonify({"error": "No batch supplied"}), 400
    result = fixes.empty_batch(batch)
    return jsonify(result), (400 if result.get("error") else 200)


def run_server(host: str = "0.0.0.0", port: int = 6767, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_server()
