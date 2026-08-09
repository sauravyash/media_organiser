"""Tests for the write-side routes: plan, apply, triage and trash."""
import pytest

from media_organiser import fixes, web
from media_organiser.web import app


@pytest.fixture(autouse=True)
def clear_dashboard_cache():
    """The plan is served from the shared dashboard cache; keep tests isolated."""
    web._dashboard_cache.clear()
    yield
    web._dashboard_cache.clear()


@pytest.fixture
def movies_root(tmp_path, monkeypatch):
    root = tmp_path / "library" / "movies"
    (root / "Arrival").mkdir(parents=True)
    (root / "Arrival" / "arrival.2016.720p.mp4").write_bytes(b"x" * 512)

    same = b"a" * 4096
    (root / "About A Boy").mkdir(parents=True)
    (root / "About A Boy" / "About A Boy (2002) [Other].mp4").write_bytes(same)
    (root / "About A Boy" / "About A Boy (2002) [Other] (2).mp4").write_bytes(same)

    monkeypatch.setenv("MOVIES_DIR", str(root))
    monkeypatch.delenv("TRASH_DIR", raising=False)
    return root


@pytest.fixture
def client():
    return app.test_client()


@pytest.mark.parametrize("route", ["/library/fix", "/library/triage", "/library/trash"])
def test_pages_render(client, movies_root, route):
    assert client.get(route).status_code == 200


def test_plan_lists_mechanical_actions(client, movies_root):
    payload = client.get("/api/library/fix/plan").get_json()

    kinds = {group["kind"] for group in payload["groups"]}
    assert "filename-mismatch" in kinds
    assert payload["total"] >= 1


def test_plan_can_be_filtered_to_one_kind(client, movies_root):
    payload = client.get("/api/library/fix/plan?kinds=missing-nfo").get_json()
    assert {group["kind"] for group in payload["groups"]} == {"missing-nfo"}


def test_apply_rejects_an_empty_request(client, movies_root):
    assert client.post("/api/library/fix/apply", json={}).status_code == 400
    assert client.post("/api/library/fix/apply", json={"actions": []}).status_code == 400


def test_apply_then_undo_round_trips_through_the_api(client, movies_root):
    plan = client.get("/api/library/fix/plan").get_json()
    actions = [a for g in plan["groups"] for a in g["actions"]
               if g["kind"] == "filename-mismatch" and not a["collision"]]
    assert actions

    applied = client.post("/api/library/fix/apply", json={"actions": actions}).get_json()
    assert applied["applied"] == len(actions)
    assert (movies_root / "Arrival" / "Arrival (2016) [720p].mp4").is_file()

    trash = client.get("/api/library/trash").get_json()
    assert any(b["batch"] == applied["batch"] for b in trash["batches"])

    undone = client.post("/api/library/trash/undo", json={"batch": applied["batch"]}).get_json()
    assert undone["restored"] == len(actions)
    assert (movies_root / "Arrival" / "arrival.2016.720p.mp4").is_file()


def test_apply_invalidates_the_cached_audit(client, movies_root):
    """A stale cache would keep offering fixes that have already been applied."""
    plan = client.get("/api/library/fix/plan").get_json()
    actions = [a for g in plan["groups"] for a in g["actions"] if g["kind"] == "filename-mismatch"]
    client.post("/api/library/fix/apply", json={"actions": actions})

    again = client.get("/api/library/fix/plan").get_json()
    remaining = [a for g in again["groups"] for a in g["actions"] if g["kind"] == "filename-mismatch"]
    assert len(remaining) < len(actions)


def test_triage_lists_folders_holding_a_decision(client, movies_root):
    payload = client.get("/api/library/movies/triage").get_json()

    folders = {f["folder"] for f in payload["folders"]}
    assert "About A Boy" in folders
    assert "Arrival" not in folders  # only a rename, not a choice


def test_triage_folder_reports_specs_and_identical_copies(client, movies_root):
    target = movies_root / "About A Boy"
    payload = client.get(f"/api/library/movies/triage/folder?path={target}").get_json()

    assert payload["identical_groups"] == 1
    assert len(payload["videos"]) == 2
    assert all(v["identical_group"] == 1 for v in payload["videos"])
    assert all(v["container"] == "mp4" for v in payload["videos"])


def test_triage_folder_refuses_paths_outside_the_library(client, movies_root, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert client.get(f"/api/library/movies/triage/folder?path={outside}").status_code == 404
    assert client.get("/api/library/movies/triage/folder?path=../../etc").status_code == 404


def test_triage_folder_refuses_a_directory_outside_movies(client, movies_root):
    """Resolvable under the library root, but not part of the movie tree."""
    sibling = movies_root.parent / "music"
    sibling.mkdir(exist_ok=True)
    assert client.get(f"/api/library/movies/triage/folder?path={sibling}").status_code == 400


def test_trashing_a_duplicate_through_the_api_frees_space_and_undoes(client, movies_root):
    detail = client.get(
        f"/api/library/movies/triage/folder?path={movies_root / 'About A Boy'}"
    ).get_json()
    victim = detail["videos"][1]

    applied = client.post("/api/library/fix/apply", json={"actions": [{
        "verb": "trash-file", "src": victim["path"],
        "size": victim["size"], "mtime": victim["mtime"],
    }]}).get_json()

    assert applied["applied"] == 1
    trash = client.get("/api/library/trash").get_json()
    assert trash["total_reclaimable"] == 4096

    client.post("/api/library/trash/undo", json={"batch": applied["batch"]})
    assert client.get("/api/library/trash").get_json()["total_reclaimable"] == 0


def test_undo_and_empty_require_a_batch(client, movies_root):
    assert client.post("/api/library/trash/undo", json={}).status_code == 400
    assert client.post("/api/library/trash/empty", json={}).status_code == 400
    assert client.post("/api/library/trash/undo", json={"batch": "../../etc"}).status_code == 400


def test_empty_trash_deletes_for_good(client, movies_root):
    victim = movies_root / "About A Boy" / "About A Boy (2002) [Other] (2).mp4"
    applied = client.post("/api/library/fix/apply", json={"actions": [{
        "verb": "trash-file", "src": str(victim),
    }]}).get_json()

    emptied = client.post("/api/library/trash/empty", json={"batch": applied["batch"]}).get_json()

    assert emptied["deleted"] == 1
    assert not (fixes.get_trash_dir() / applied["batch"]).exists()
    assert not victim.exists()
