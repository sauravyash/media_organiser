"""Tests for the write side: trash, journal, undo, and bulk mechanical fixes."""
import json
import os

import pytest

from media_organiser import fixes
from media_organiser.audit import (
    VERB_RENAME_FILE,
    VERB_RENAME_FOLDER,
    VERB_TRASH,
    VERB_WRITE_NFO,
)
from media_organiser.library import scan_movies


@pytest.fixture
def movies_root(tmp_path, monkeypatch):
    """A library rooted at ``tmp_path/library`` so trash and journal land inside it."""
    root = tmp_path / "library" / "movies"
    root.mkdir(parents=True)
    monkeypatch.setenv("MOVIES_DIR", str(root))
    monkeypatch.delenv("TRASH_DIR", raising=False)
    return root


def make_video(root, folder, filename, data=b"x" * 512):
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    video = d / filename
    video.write_bytes(data)
    return video


def trash_action(video, **overrides):
    st = video.stat()
    action = {"verb": VERB_TRASH, "src": str(video), "size": st.st_size, "mtime": st.st_mtime}
    action.update(overrides)
    return action


# ---------------------------------------------------------------------------
# Trash and undo
# ---------------------------------------------------------------------------

def test_trash_moves_file_aside_and_undo_puts_it_back(movies_root):
    video = make_video(movies_root, "Airplane", "Airplane (1980) [Other] (2).mp4")

    result = fixes.apply_actions([trash_action(video)])

    assert result["applied"] == 1
    assert not video.exists()
    batch = result["batch"]
    trashed = fixes.get_trash_dir() / batch / "movies" / "Airplane" / video.name
    assert trashed.is_file()

    undo = fixes.undo_batch(batch)
    assert undo["restored"] == 1
    assert video.is_file()
    assert not trashed.exists()


def test_trash_lands_on_the_same_filesystem_as_the_library(movies_root):
    """Trash sits beside ``movies/`` so moving into it is a rename, not a copy."""
    assert fixes.get_trash_dir() == movies_root.parent / ".trash"
    assert fixes.journal_path().parent == movies_root.parent / ".media_organiser"


def test_trash_is_marked_so_media_servers_skip_it(movies_root):
    video = make_video(movies_root, "Airplane", "dupe.mp4")
    fixes.apply_actions([trash_action(video)])
    assert (fixes.get_trash_dir() / ".ignore").is_file()


def test_trash_is_refused_when_the_file_changed_since_the_scan(movies_root):
    video = make_video(movies_root, "Airplane", "dupe.mp4")
    action = trash_action(video)
    video.write_bytes(b"y" * 4096)  # a re-import replaced it after the scan

    result = fixes.apply_actions([action])

    assert result["applied"] == 0
    assert result["skipped"] == 1
    assert "changed on disk" in result["results"][0]["reason"]
    assert video.is_file()


def test_undo_refuses_when_something_else_took_the_name(movies_root):
    video = make_video(movies_root, "Airplane", "dupe.mp4")
    batch = fixes.apply_actions([trash_action(video)])["batch"]
    video.write_bytes(b"z" * 64)  # a new file now occupies the original path

    undo = fixes.undo_batch(batch)

    assert undo["restored"] == 0
    assert "occupies" in undo["results"][0]["reason"]
    assert video.read_bytes() == b"z" * 64


def test_undo_is_idempotent(movies_root):
    video = make_video(movies_root, "Airplane", "dupe.mp4")
    batch = fixes.apply_actions([trash_action(video)])["batch"]

    assert fixes.undo_batch(batch)["restored"] == 1
    again = fixes.undo_batch(batch)
    assert again["restored"] == 0
    assert again["error"]


# ---------------------------------------------------------------------------
# Renames
# ---------------------------------------------------------------------------

def test_rename_file_is_journalled_and_reversible(movies_root):
    video = make_video(movies_root, "Arrival", "arrival.2016.720p.mp4")
    target = video.parent / "Arrival (2016) [720p].mp4"

    batch = fixes.apply_actions([
        {"verb": VERB_RENAME_FILE, "src": str(video), "dst": str(target)}
    ])["batch"]

    assert target.is_file() and not video.exists()
    fixes.undo_batch(batch)
    assert video.is_file() and not target.exists()


def test_rename_never_clobbers_an_existing_target(movies_root):
    """``safe_path``'s ``(2)`` suffix created these duplicates; the fix path refuses instead."""
    video = make_video(movies_root, "Arrival", "arrival.mp4")
    occupied = make_video(movies_root, "Arrival", "Arrival (2016) [720p].mp4", data=b"other")

    result = fixes.apply_actions([
        {"verb": VERB_RENAME_FILE, "src": str(video), "dst": str(occupied)}
    ])

    assert result["applied"] == 0
    assert "already exists" in result["results"][0]["reason"]
    assert video.is_file()
    assert occupied.read_bytes() == b"other"


def test_folder_rename_remaps_file_renames_in_the_same_batch(movies_root):
    """A file rename planned against the messy folder must survive the folder rename."""
    messy = "American Pie (1999) [YTS AG]"
    video = make_video(movies_root, messy, "American Pie (1999) [YTS AG] (1999) [720p].mp4")
    clean_folder = movies_root / "American Pie"
    clean_file = movies_root / messy / "American Pie (1999) [720p].mp4"

    result = fixes.apply_actions([
        # Deliberately out of order: the pipeline sorts folders ahead of files.
        {"verb": VERB_RENAME_FILE, "src": str(video), "dst": str(clean_file)},
        {"verb": VERB_RENAME_FOLDER, "src": str(movies_root / messy), "dst": str(clean_folder)},
    ])

    assert result["applied"] == 2, result["results"]
    assert (clean_folder / "American Pie (1999) [720p].mp4").is_file()
    assert not (movies_root / messy).exists()


def test_undo_unwinds_a_folder_rename_after_the_files_inside_it(movies_root):
    messy = "Clueless (1995) [ ] [1080p] [YTS AM]"
    video = make_video(movies_root, messy, "messy.mp4")
    result = fixes.apply_actions([
        {"verb": VERB_RENAME_FILE, "src": str(video),
         "dst": str(movies_root / messy / "Clueless (1995) [1080p].mp4")},
        {"verb": VERB_RENAME_FOLDER, "src": str(movies_root / messy),
         "dst": str(movies_root / "Clueless")},
    ])

    undo = fixes.undo_batch(result["batch"])

    assert undo["restored"] == 2, undo["results"]
    assert video.is_file()
    assert not (movies_root / "Clueless").exists()


# ---------------------------------------------------------------------------
# NFOs
# ---------------------------------------------------------------------------

def test_write_nfo_creates_one_and_undo_removes_it(movies_root):
    video = make_video(movies_root, "Argo", "Argo (2012) [Other].mp4")
    nfo = video.with_suffix(".nfo")

    batch = fixes.apply_actions([
        {"verb": VERB_WRITE_NFO, "src": str(video), "dst": str(nfo)}
    ])["batch"]

    assert nfo.is_file()
    assert "<title>" in nfo.read_text(encoding="utf-8")
    fixes.undo_batch(batch)
    assert not nfo.exists()


def test_refreshing_an_nfo_fixes_the_path_but_keeps_hand_edits(movies_root):
    """The stale path is what needs correcting; a curated title is not."""
    video = make_video(movies_root, "Blow", "Blow (2001) [Other].mp4")
    nfo = video.with_suffix(".nfo")
    nfo.write_text(
        "<?xml version='1.0' encoding='utf-8'?><movie>"
        "<title>Blow</title><year>2001</year>"
        "<filenameandpath>/old/Blow (2001) [Other].avi</filenameandpath>"
        "</movie>",
        encoding="utf-8",
    )

    fixes.apply_actions([{"verb": VERB_WRITE_NFO, "src": str(video), "dst": str(nfo)}])

    written = nfo.read_text(encoding="utf-8")
    assert "/old/Blow" not in written
    assert str(video) in written
    assert "<title>Blow</title>" in written
    assert "<year>2001</year>" in written


def test_overwriting_an_nfo_sets_the_old_one_aside_so_undo_restores_it(movies_root):
    video = make_video(movies_root, "Blow", "Blow (2001) [Other].mp4")
    nfo = video.with_suffix(".nfo")
    original = "<movie><title>Blow</title></movie>"
    nfo.write_text(original, encoding="utf-8")

    batch = fixes.apply_actions([
        {"verb": VERB_WRITE_NFO, "src": str(video), "dst": str(nfo)}
    ])["batch"]
    assert nfo.read_text(encoding="utf-8") != original
    assert str(video) in nfo.read_text(encoding="utf-8")

    fixes.undo_batch(batch)
    assert nfo.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hostile", [
    "../../etc/passwd",
    "movies/../../../secrets.txt",
    "/etc/passwd" if os.name != "nt" else "C:/Windows/System32/drivers/etc/hosts",
])
def test_paths_outside_the_library_are_rejected(movies_root, hostile):
    assert fixes.resolve_under_root(hostile) is None


def test_actions_with_unknown_verbs_are_dropped(movies_root):
    video = make_video(movies_root, "Argo", "Argo.mp4")
    result = fixes.apply_actions([{"verb": "rm -rf", "src": str(video)}])
    assert result["results"] == []
    assert video.is_file()


def test_a_torn_journal_line_does_not_hide_the_rest(movies_root):
    video = make_video(movies_root, "Argo", "Argo.mp4")
    fixes.apply_actions([trash_action(video)])
    with fixes.journal_path().open("a", encoding="utf-8") as fh:
        fh.write('{"batch": "half-writ')

    assert len(fixes.read_journal()) == 1


def test_empty_batch_deletes_for_good_and_marks_the_journal(movies_root):
    video = make_video(movies_root, "Argo", "Argo.mp4", data=b"x" * 2048)
    batch = fixes.apply_actions([trash_action(video)])["batch"]

    result = fixes.empty_batch(batch)

    assert result["deleted"] == 1
    assert result["freed"] == 2048
    assert not (fixes.get_trash_dir() / batch).exists()
    assert fixes.undo_batch(batch)["restored"] == 0


# ---------------------------------------------------------------------------
# Planning from a real audit
# ---------------------------------------------------------------------------

def test_plan_turns_audited_issues_into_applyable_actions(movies_root):
    make_video(movies_root, "Arrival", "arrival.2016.720p.mp4")

    plan = fixes.plan_mechanical(scan_movies(movies_root))
    kinds = {group["kind"] for group in plan["groups"]}

    assert "filename-mismatch" in kinds
    assert "missing-nfo" in kinds
    assert plan["total"] >= 2
    for group in plan["groups"]:
        for action in group["actions"]:
            assert action["src"] and action["id"]


def test_plan_does_not_flag_an_nfo_refresh_as_a_clash(movies_root):
    """Rewriting an NFO onto itself is the point, not a collision."""
    video = make_video(movies_root, "Blow", "Blow (2001) [Other].mp4")
    video.with_suffix(".nfo").write_text(
        "<?xml version='1.0' encoding='utf-8'?><movie><title>Blow</title>"
        "<filenameandpath>/old/Blow.avi</filenameandpath></movie>",
        encoding="utf-8",
    )

    plan = fixes.plan_mechanical(scan_movies(movies_root), ["stale-nfo-path"])
    actions = [a for g in plan["groups"] for a in g["actions"]]

    assert actions, "expected a stale-nfo-path action"
    assert not any(a["collision"] for a in actions)
    assert plan["collisions"] == 0


def test_plan_flags_a_rename_whose_target_is_taken(movies_root):
    make_video(movies_root, "Arrival", "arrival.2016.720p.mp4")
    # The name the audit wants is already used by a second file in the folder.
    make_video(movies_root, "Arrival", "Arrival (2016) [720p].mp4")

    plan = fixes.plan_mechanical(scan_movies(movies_root), ["filename-mismatch"])
    actions = [a for g in plan["groups"] for a in g["actions"]]

    assert any(a["collision"] for a in actions)
    assert plan["collisions"] >= 1


def test_planned_actions_apply_end_to_end(movies_root):
    make_video(movies_root, "Arrival", "arrival.2016.720p.mp4")
    plan = fixes.plan_mechanical(scan_movies(movies_root))
    actions = [a for g in plan["groups"] for a in g["actions"] if not a["collision"]]

    result = fixes.apply_actions(actions)

    assert result["errors"] == 0, result["results"]
    assert result["applied"] == len(actions)
    assert (movies_root / "Arrival" / "Arrival (2016) [720p].mp4").is_file()
    assert (movies_root / "Arrival" / "Arrival (2016) [720p].nfo").is_file()


def test_dry_run_changes_nothing(movies_root):
    video = make_video(movies_root, "Arrival", "arrival.2016.720p.mp4")
    result = fixes.apply_actions([trash_action(video)], dry_run=True)

    assert result["dry_run"] is True
    assert result["batch"] is None
    assert video.is_file()
    assert not fixes.journal_path().exists()


# ---------------------------------------------------------------------------
# Triage inspection
# ---------------------------------------------------------------------------

def test_inspect_folder_groups_byte_identical_copies(movies_root):
    same = b"a" * 4096
    make_video(movies_root, "About A Boy", "About A Boy (2002) [Other].mp4", data=same)
    make_video(movies_root, "About A Boy", "About A Boy (2002) [Other] (2).mp4", data=same)

    detail = fixes.inspect_folder(movies_root / "About A Boy")

    assert detail["identical_groups"] == 1
    assert all(v["identical_group"] == 1 for v in detail["videos"])
    # First row is the default keeper, so the renumbered copy must not lead.
    assert detail["videos"][0]["name"] == "About A Boy (2002) [Other].mp4"
    assert detail["videos"][1]["name"] == "About A Boy (2002) [Other] (2).mp4"


def test_inspect_folder_leaves_different_files_ungrouped(movies_root):
    make_video(movies_root, "Aladdin", "Aladdin (1992) [Other].m4v", data=b"a" * 4096)
    make_video(movies_root, "Aladdin", "Aladdin (1994) [Other].avi", data=b"b" * 8192)

    detail = fixes.inspect_folder(movies_root / "Aladdin")

    assert detail["identical_groups"] == 0
    assert all(v["identical_group"] is None for v in detail["videos"])
    # Biggest first, so the likely keeper is the default pick.
    assert detail["videos"][0]["size"] > detail["videos"][1]["size"]


def test_inspect_folder_marks_multi_part_movies(movies_root):
    make_video(movies_root, "Shrek Original", "Shrek Original [Other] CD 1.avi", data=b"a" * 2048)
    make_video(movies_root, "Shrek Original", "Shrek Original [Other] CD 2.avi", data=b"b" * 2048)

    detail = fixes.inspect_folder(movies_root / "Shrek Original")

    assert all(v["part"] for v in detail["videos"]), detail["videos"]
