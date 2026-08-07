"""Tests for the read-only library dashboard routes."""
import pytest

from media_organiser import web
from media_organiser.web import app

NFO = """<?xml version='1.0' encoding='utf-8'?>
<movie><title>Arrival</title><year>2016</year><quality>720p</quality></movie>
"""


@pytest.fixture(autouse=True)
def clear_dashboard_cache():
    """The dashboard cache is module-level; keep tests independent of each other."""
    web._dashboard_cache.clear()
    yield
    web._dashboard_cache.clear()


@pytest.fixture
def movies_root(tmp_path, monkeypatch):
    root = tmp_path / "library" / "movies"
    (root / "Arrival").mkdir(parents=True)
    video = root / "Arrival" / "Arrival (2016) [720p].mp4"
    video.write_bytes(b"x" * 64)
    video.with_suffix(".nfo").write_text(NFO, encoding="utf-8")
    (root / "1. Philosophor's Stone").mkdir(parents=True)
    (root / "1. Philosophor's Stone" / "1. Philosophor's Stone [Other].mp4").write_bytes(b"y" * 64)
    monkeypatch.setenv("MOVIES_DIR", str(root))
    return root


@pytest.fixture
def client():
    return app.test_client()


@pytest.fixture
def no_beets(monkeypatch):
    monkeypatch.setenv("BEET_BIN", "definitely-not-a-real-beet-binary")


# --------------------------------------------------------------------------
# pages


def test_movie_library_page_renders(client, movies_root):
    r = client.get("/library/movies")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Movie library" in body
    assert str(movies_root) in body
    assert "/api/library/movies" in body


def test_music_library_page_renders(client):
    r = client.get("/library/music")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Music library" in body
    assert "/api/library/music" in body


def test_dashboards_are_linked_from_every_page(client, movies_root):
    for url in ("/", "/music", "/library/movies", "/library/music"):
        body = client.get(url).get_data(as_text=True)
        assert "/library/movies" in body, url
        assert "/library/music" in body, url


def test_existing_upload_pages_still_work(client):
    assert client.get("/").status_code == 200
    assert client.get("/music").status_code == 200


# --------------------------------------------------------------------------
# movie API


def test_api_movies_returns_entries_and_summary(client, movies_root):
    r = client.get("/api/library/movies")
    assert r.status_code == 200
    data = r.get_json()

    assert data["exists"] is True
    assert data["root"] == str(movies_root)
    folders = {e["folder"] for e in data["entries"]}
    assert folders == {"Arrival", "1. Philosophor's Stone"}

    arrival = next(e for e in data["entries"] if e["folder"] == "Arrival")
    assert arrival["issues"] == []

    messy = next(e for e in data["entries"] if e["folder"] == "1. Philosophor's Stone")
    assert {i["kind"] for i in messy["issues"]} >= {"leading-index", "missing-year"}

    assert data["summary"]["total"] == 2
    assert data["summary"]["flagged"] == 1


def test_api_movies_reports_a_missing_root(client, monkeypatch, tmp_path):
    monkeypatch.setenv("MOVIES_DIR", str(tmp_path / "nowhere"))
    data = client.get("/api/library/movies").get_json()
    assert data["exists"] is False
    assert data["entries"] == []


# --------------------------------------------------------------------------
# music API


def test_api_music_reports_missing_beets_without_erroring(client, no_beets):
    r = client.get("/api/library/music")
    assert r.status_code == 200
    data = r.get_json()
    assert data["available"] is False
    assert "not found" in data["error"]
    assert data["tracks"] == []


# --------------------------------------------------------------------------
# caching


def test_repeat_requests_reuse_the_cached_scan(client, monkeypatch, movies_root):
    calls = []

    def counting_audit(*args, **kwargs):
        calls.append(1)
        return {"root": "x", "exists": True, "entries": [], "summary": {}, "generated_at": ""}

    monkeypatch.setattr(web, "audit_movies", counting_audit)
    client.get("/api/library/movies")
    client.get("/api/library/movies")
    assert len(calls) == 1


def test_refresh_bypasses_the_cache(client, monkeypatch, movies_root):
    calls = []

    def counting_audit(*args, **kwargs):
        calls.append(1)
        return {"root": "x", "exists": True, "entries": [], "summary": {}, "generated_at": ""}

    monkeypatch.setattr(web, "audit_movies", counting_audit)
    client.get("/api/library/movies")
    client.get("/api/library/movies?refresh=1")
    assert len(calls) == 2


def test_zero_ttl_disables_caching(client, monkeypatch, movies_root):
    calls = []

    def counting_audit(*args, **kwargs):
        calls.append(1)
        return {"root": "x", "exists": True, "entries": [], "summary": {}, "generated_at": ""}

    monkeypatch.setenv("DASHBOARD_CACHE_TTL", "0")
    monkeypatch.setattr(web, "audit_movies", counting_audit)
    client.get("/api/library/movies")
    client.get("/api/library/movies")
    assert len(calls) == 2


def test_invalid_ttl_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DASHBOARD_CACHE_TTL", "not-a-number")
    assert web._cache_ttl() == 60.0
