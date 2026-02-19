"""Tests for the web upload API (POST /upload)."""
import io
from pathlib import Path

import pytest

from media_organiser.web import app


@pytest.fixture
def import_dir(tmp_path):
    """Temporary directory used as IMPORT_DIR for uploads."""
    d = tmp_path / "import"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def client(import_dir, monkeypatch):
    """Flask test client with IMPORT_DIR set to a temp directory."""
    monkeypatch.setenv("IMPORT_DIR", str(import_dir))
    # Re-read env in app by triggering config; get_import_dir uses os.environ
    return app.test_client()


def test_upload_single_file_json(client, import_dir):
    """POST with single file and Accept: application/json returns saved paths."""
    data = {"file": (io.BytesIO(b"hello world"), "foo.txt")}
    r = client.post(
        "/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    out = r.get_json()
    assert out["saved"] == ["foo.txt"]
    assert out["rejected"] == []
    assert (import_dir / "foo.txt").read_bytes() == b"hello world"


def test_upload_multiple_files_json(client, import_dir):
    """POST with multiple files (files[]) returns all saved paths."""
    data = {
        "files": [
            (io.BytesIO(b"a"), "one.txt"),
            (io.BytesIO(b"b"), "two.txt"),
        ]
    }
    r = client.post(
        "/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    out = r.get_json()
    assert set(out["saved"]) == {"one.txt", "two.txt"}
    assert out["rejected"] == []
    assert (import_dir / "one.txt").read_bytes() == b"a"
    assert (import_dir / "two.txt").read_bytes() == b"b"


def test_upload_with_paths_form(client, import_dir):
    """POST with paths form array uses those as destination paths."""
    data = {
        "file": (io.BytesIO(b"content"), "original.txt"),
        "paths": "subdir/custom.txt",
    }
    r = client.post(
        "/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    out = r.get_json()
    assert out["saved"] == ["subdir/custom.txt"]
    assert (import_dir / "subdir" / "custom.txt").read_bytes() == b"content"


def test_upload_no_files_returns_400_json(client):
    """POST with no file selected returns 400 and error message for JSON."""
    r = client.post(
        "/upload",
        data={},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "No files selected"


def test_upload_rejects_path_traversal(client, import_dir):
    """Paths that escape import dir are rejected and return 422 error."""
    data = {
        "file": (io.BytesIO(b"x"), "bad.txt"),
        "paths": "../../../etc/passwd",
    }
    r = client.post(
        "/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 422  # Unprocessable Entity - no files saved
    out = r.get_json()
    assert out["saved"] == []
    assert out["rejected"] == ["../../../etc/passwd"]
    assert not (import_dir / ".." / ".." / ".." / "etc" / "passwd").exists()


def test_upload_duplicate_appends_number(client, import_dir):
    """Uploading same filename again creates 'name (1).ext'."""
    (import_dir / "same.txt").write_bytes(b"first")
    data = {"file": (io.BytesIO(b"second"), "same.txt")}
    r = client.post(
        "/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    out = r.get_json()
    assert out["saved"] == ["same (1).txt"]
    assert (import_dir / "same (1).txt").read_bytes() == b"second"
    assert (import_dir / "same.txt").read_bytes() == b"first"


def test_upload_returns_json_when_no_json_accept(client, import_dir):
    """POST without JSON accept still returns JSON with saved/rejected lists."""
    data = {"file": (io.BytesIO(b"x"), "r.txt")}
    r = client.post("/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.is_json
    out = r.get_json()
    assert out["saved"] == ["r.txt"]
    assert out["rejected"] == []
    assert (import_dir / "r.txt").read_bytes() == b"x"


def test_upload_no_files_returns_json_error_without_json_accept(client):
    """POST with no files and no JSON accept returns JSON error."""
    r = client.post("/upload", data={})
    assert r.status_code == 400
    assert r.is_json
    assert r.get_json()["error"] == "No files selected"


def test_index_returns_200(client):
    """GET / returns 200."""
    r = client.get("/")
    assert r.status_code == 200


def test_upload_hidden_files_accepted(client, import_dir):
    """Hidden files (starting with .) can be uploaded if explicitly sent."""
    # The backend doesn't filter hidden files - that's a frontend concern.
    # This test verifies hidden files can still be uploaded if sent explicitly.
    data = {
        "files": [
            (io.BytesIO(b"hidden content"), ".DS_Store"),
            (io.BytesIO(b"another hidden"), ".AppleDouble"),
            (io.BytesIO(b"normal file"), "normal.txt"),
        ]
    }
    r = client.post(
        "/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    out = r.get_json()
    # All files should be saved (backend doesn't filter hidden files)
    assert set(out["saved"]) == {".DS_Store", ".AppleDouble", "normal.txt"}
    assert out["rejected"] == []
    assert (import_dir / ".DS_Store").read_bytes() == b"hidden content"
    assert (import_dir / ".AppleDouble").read_bytes() == b"another hidden"
    assert (import_dir / "normal.txt").read_bytes() == b"normal file"


def test_upload_hidden_files_in_subdirs(client, import_dir):
    """Hidden files in subdirectories can be uploaded."""
    data = {
        "file": (io.BytesIO(b"content"), "file.txt"),
        "paths": "subdir/.DS_Store",
    }
    r = client.post(
        "/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    out = r.get_json()
    assert out["saved"] == ["subdir/.DS_Store"]
    assert out["rejected"] == []
    assert (import_dir / "subdir" / ".DS_Store").read_bytes() == b"content"


def test_upload_always_returns_json_response(client, import_dir):
    """Upload endpoint always returns JSON regardless of Accept header."""
    # Test with no Accept header
    data = {"file": (io.BytesIO(b"test"), "always.json")}
    r = client.post("/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.is_json
    out = r.get_json()
    assert "saved" in out
    assert "rejected" in out
    assert out["saved"] == ["always.json"]
    
    # Test with HTML Accept header (create new BytesIO for second request)
    data2 = {"file": (io.BytesIO(b"test2"), "always2.json")}
    r = client.post(
        "/upload",
        data=data2,
        content_type="multipart/form-data",
        headers={"Accept": "text/html"},
    )
    assert r.status_code == 200
    assert r.is_json
    out = r.get_json()
    assert "saved" in out
    assert "rejected" in out
    assert out["saved"] == ["always2.json"]


def test_upload_mixed_success_and_rejection(client, import_dir):
    """Upload with some files saved and some rejected returns correct lists."""
    # Create a file that will be saved
    data = {
        "files": [
            (io.BytesIO(b"good"), "good.txt"),
            (io.BytesIO(b"bad"), "bad.txt"),
        ],
        "paths": ["good.txt", "../../../etc/passwd"],  # Second path is traversal attempt
    }
    r = client.post(
        "/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert r.is_json
    out = r.get_json()
    assert "good.txt" in out["saved"]
    assert "../../../etc/passwd" in out["rejected"]
    assert len(out["saved"]) == 1
    assert len(out["rejected"]) == 1
    assert (import_dir / "good.txt").read_bytes() == b"good"


def test_upload_empty_file_list_returns_error_json(client):
    """Upload with empty file list returns JSON error."""
    # Send empty files list
    data = {"files": []}
    r = client.post("/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 400
    assert r.is_json
    assert r.get_json()["error"] == "No files selected"


def test_upload_all_files_rejected_returns_422(client, import_dir):
    """Upload with all files rejected returns 422 Unprocessable Entity."""
    data = {
        "files": [
            (io.BytesIO(b"x"), "bad1.txt"),
            (io.BytesIO(b"y"), "bad2.txt"),
        ],
        "paths": ["../../../etc/passwd", "../../../etc/shadow"],  # Both are traversal attempts
    }
    r = client.post(
        "/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 422  # Unprocessable Entity - no files saved
    assert r.is_json
    out = r.get_json()
    assert out["saved"] == []
    assert len(out["rejected"]) == 2
    assert "../../../etc/passwd" in out["rejected"]
    assert "../../../etc/shadow" in out["rejected"]


def test_upload_success_returns_200(client, import_dir):
    """Upload with at least one successful file returns 200 OK."""
    data = {
        "files": [
            (io.BytesIO(b"success"), "good.txt"),
        ],
    }
    r = client.post(
        "/upload",
        data=data,
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    assert r.is_json
    out = r.get_json()
    assert out["saved"] == ["good.txt"]
    assert out["rejected"] == []
