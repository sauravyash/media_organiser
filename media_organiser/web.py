"""Web upload interface for media_organiser."""
import os
from pathlib import Path

from flask import Flask, request, render_template, jsonify, redirect, url_for

app = Flask(
    __name__,
    template_folder=Path(__file__).resolve().parent / "templates",
    static_folder=Path(__file__).resolve().parent / "static",
)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB per request


def get_import_dir() -> Path:
    path = Path(os.environ.get("IMPORT_DIR", "/data/import"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_relative_path(import_dir: Path, rel_path: str) -> Path | None:
    """Resolve rel_path under import_dir, rejecting path traversal. Returns None if invalid."""
    base = import_dir.resolve()
    try:
        resolved = (import_dir / rel_path).resolve()
        if base in resolved.parents:
            return resolved
    except (ValueError, OSError):
        pass
    return None


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
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"error": "No files selected"}), 400
        return redirect(url_for("index"))

    paths = request.form.getlist("paths")
    saved = []
    rejected = []
    for i, f in enumerate(files):
        if not f or not f.filename:
            continue
        rel_path = paths[i] if i < len(paths) else f.filename
        dest = _safe_relative_path(import_dir, rel_path)
        if dest is None:
            rejected.append(rel_path)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            stem, ext = dest.stem, dest.suffix
            n = 1
            while dest.exists():
                dest = dest.parent / f"{stem} ({n}){ext}"
                n += 1
        f.save(str(dest))
        saved.append(str(dest.relative_to(import_dir)))

    if request.accept_mimetypes.best == "application/json":
        return jsonify({"saved": saved, "rejected": rejected})
    return redirect(url_for("index"))


def run_server(host: str = "0.0.0.0", port: int = 6767, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_server()
