"""Web upload interface for media_organiser."""
import os
from pathlib import Path

from flask import Flask, request, render_template, jsonify, redirect, url_for
from werkzeug.exceptions import RequestEntityTooLarge

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
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload():
    import_dir = get_import_dir()
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
            
        dest = _safe_relative_path(import_dir, rel_path)
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
            saved.append(str(dest.relative_to(import_dir)))
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
