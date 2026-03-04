"""Web upload interface for media_organiser."""
import os
from pathlib import Path

from flask import Flask, request, render_template, jsonify, redirect, url_for, send_file, abort
from werkzeug.exceptions import RequestEntityTooLarge

from . import audio_tools
from . import musicbrainz_client

app = Flask(
    __name__,
    template_folder=Path(__file__).resolve().parent / "templates",
    static_folder=Path(__file__).resolve().parent / "static",
)

# Allow up to 30GB by default, configurable via MAX_UPLOAD_SIZE env var
max_upload_size = int(os.environ.get("MAX_UPLOAD_SIZE", 30 * 1024 * 1024 * 1024))
app.config["MAX_CONTENT_LENGTH"] = max_upload_size


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
                "path": str(dest.relative_to(music_dir)),
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
    result = audio_tools.ensure_mp3_320(dest, music_dir)
    if result.get("output_path"):
        out_path = Path(result["output_path"])
        try:
            rel_out = out_path.relative_to(music_dir)
        except ValueError:
            rel_out = out_path.name
        result["output_path"] = str(rel_out)
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
            # Both paths are now guaranteed to be absolute, so relative_to() will work
            saved.append(str(dest.relative_to(base_dir)))
        except Exception as e:
            # Catch any errors during save (permissions, disk full, etc.)
            rejected.append(f"{rel_path} (error: {str(e)})")
    
    # Return error status code if no files were successfully uploaded
    if not saved:
        # 422 Unprocessable Entity - request was valid but files couldn't be processed
        return jsonify({"saved": saved, "rejected": rejected}), 422
    
    # Always return JSON with saved and rejected lists
    return jsonify({"saved": saved, "rejected": rejected})


def run_server(host: str = "0.0.0.0", port: int = 6767, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_server()
