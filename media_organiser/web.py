"""Web upload interface for media_organiser."""
import os
from pathlib import Path

from flask import Flask, request, render_template, jsonify, redirect, url_for

from .constants import VIDEO_EXTS, SUB_EXTS

UPLOAD_EXTS = VIDEO_EXTS | SUB_EXTS

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


def allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in UPLOAD_EXTS


@app.route("/")
def index():
    return render_template("upload.html", allowed_extensions=sorted(UPLOAD_EXTS))


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

    saved = []
    rejected = []
    for f in files:
        if not f or not f.filename:
            continue
        if not allowed_file(f.filename):
            rejected.append(f.filename)
            continue
        dest = import_dir / f.filename
        if dest.exists():
            base, ext = dest.stem, dest.suffix
            n = 1
            while dest.exists():
                dest = import_dir / f"{base} ({n}){ext}"
                n += 1
        f.save(str(dest))
        saved.append(dest.name)

    if request.accept_mimetypes.best == "application/json":
        return jsonify({"saved": saved, "rejected": rejected})
    return redirect(url_for("index"))


def run_server(host: str = "0.0.0.0", port: int = 6767, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_server()
