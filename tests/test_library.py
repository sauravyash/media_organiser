"""Tests for the read-only movie library audit (media_organiser.library)."""
from pathlib import Path

import pytest

from media_organiser.library import (
    audit_movies,
    canonical_stem,
    get_movies_dir,
    scan_movies,
    suggest_clean_title,
    title_key,
)

NFO_TEMPLATE = """<?xml version='1.0' encoding='utf-8'?>
<movie>
  <title>{title}</title>
  <year>{year}</year>
  <quality>{quality}</quality>
  <extension>{ext}</extension>
  <filenameandpath>{path}</filenameandpath>
</movie>
"""


@pytest.fixture
def movies_root(tmp_path):
    root = tmp_path / "library" / "movies"
    root.mkdir(parents=True)
    return root


def make_movie(root: Path, folder: str, filename: str, *, nfo: bool = True,
               nfo_title: str = None, nfo_year: str = "2016",
               nfo_quality: str = None, nfo_path: str = None) -> Path:
    """Create one movie folder with a video and (optionally) a matching NFO."""
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    video = d / filename
    video.write_bytes(b"x" * 128)
    if nfo:
        quality = nfo_quality if nfo_quality is not None else "720p"
        video.with_suffix(".nfo").write_text(NFO_TEMPLATE.format(
            title=nfo_title if nfo_title is not None else folder,
            year=nfo_year,
            quality=quality,
            ext=video.suffix.lstrip("."),
            path=nfo_path if nfo_path is not None else str(video),
        ), encoding="utf-8")
    return video


def kinds(entry: dict) -> set[str]:
    return {i["kind"] for i in entry["issues"]}


def by_folder(entries: list[dict], folder: str) -> dict:
    for e in entries:
        if e["folder"] == folder:
            return e
    raise AssertionError(f"{folder!r} not in {[e['folder'] for e in entries]}")


# --------------------------------------------------------------------------
# helpers


def test_title_key_ignores_articles_punctuation_and_case():
    assert title_key("The Matrix") == title_key("Matrix")
    assert title_key("Assassin's Creed") == title_key("assassins creed")
    assert title_key("Arrival (2016)") == title_key("Arrival")


def test_suggest_clean_title_strips_scene_noise():
    assert suggest_clean_title("3 Idiots (2009) [bluray] [1080p] [yts.am]") == "3 Idiots"
    assert suggest_clean_title("1. Philosophor's Stone") == "Philosophor's Stone"
    assert suggest_clean_title("Arrival") == "Arrival"


def test_canonical_stem_matches_cli_format():
    assert canonical_stem("Arrival", "2016", "720p") == "Arrival (2016) [720p]"
    assert canonical_stem("Arrival", None, "720p") == "Arrival [720p]"
    assert canonical_stem("Shrek", "2001", "480p", " CD 1") == "Shrek (2001) [480p] CD 1"


# --------------------------------------------------------------------------
# scanning


def test_missing_root_returns_no_entries(tmp_path):
    assert scan_movies(tmp_path / "nope") == []
    payload = audit_movies(tmp_path / "nope")
    assert payload["exists"] is False
    assert payload["entries"] == []
    assert payload["summary"]["total"] == 0


def test_well_named_movie_has_no_issues(movies_root):
    make_movie(movies_root, "Arrival", "Arrival (2016) [720p].mp4")
    entry = by_folder(scan_movies(movies_root), "Arrival")
    assert entry["issues"] == []
    assert entry["severity"] is None
    assert entry["year"] == "2016"
    assert entry["quality"] == "720p"
    assert entry["videos"][0]["name"] == "Arrival (2016) [720p].mp4"


def test_missing_nfo_is_flagged_low(movies_root):
    make_movie(movies_root, "Arrival", "Arrival (2016) [720p].mp4", nfo=False)
    entry = by_folder(scan_movies(movies_root), "Arrival")
    assert "missing-nfo" in kinds(entry)
    assert entry["severity"] == "low"


def test_messy_folder_name_suggests_clean_rename(movies_root):
    folder = "3 Idiots (2009) [bluray] [1080p] [yts.am]"
    make_movie(movies_root, folder, f"{folder} (2009) [1080p].mp4", nfo=False)
    entry = by_folder(scan_movies(movies_root), folder)
    assert "messy-folder-name" in kinds(entry)
    messy = next(i for i in entry["issues"] if i["kind"] == "messy-folder-name")
    assert messy["suggestion"] == "Rename folder to '3 Idiots'"
    # The filename is wrong too, and the suggestion uses the cleaned title.
    mismatch = next(i for i in entry["issues"] if i["kind"] == "filename-mismatch")
    assert mismatch["suggestion"] == "Rename to 3 Idiots (2009) [1080p].mp4"


def test_leading_collection_index_is_flagged(movies_root):
    make_movie(movies_root, "1. Philosophor's Stone",
               "1. Philosophor's Stone [Other].mp4", nfo=False)
    entry = by_folder(scan_movies(movies_root), "1. Philosophor's Stone")
    assert "leading-index" in kinds(entry)
    assert "unknown-quality" in kinds(entry)
    assert "missing-year" in kinds(entry)


def test_tv_episode_under_movies_is_high_severity(movies_root):
    make_movie(movies_root, "Breaking Bad", "Breaking Bad - S01E02 (720p).mkv", nfo=False)
    entry = by_folder(scan_movies(movies_root), "Breaking Bad")
    assert "tv-episode-in-movies" in kinds(entry)
    assert entry["severity"] == "high"


def test_season_pack_under_movies_is_detected(movies_root):
    folder = "Breaking Bad S01 Complete Bluray"
    make_movie(movies_root, folder, f"{folder} [Other].mkv", nfo=False)
    entry = by_folder(scan_movies(movies_root), folder)
    assert "tv-episode-in-movies" in kinds(entry)


def test_empty_folder_is_flagged(movies_root):
    (movies_root / "Ghost Folder").mkdir()
    entry = by_folder(scan_movies(movies_root), "Ghost Folder")
    assert "no-video-file" in kinds(entry)
    assert entry["severity"] == "high"


def test_multiple_videos_in_one_folder_is_flagged(movies_root):
    make_movie(movies_root, "Double", "Double (2016) [720p].mp4", nfo=False)
    (movies_root / "Double" / "Another Movie (2011) [720p].mkv").write_bytes(b"y" * 64)
    entry = by_folder(scan_movies(movies_root), "Double")
    assert "multiple-videos" in kinds(entry)


def test_cd_split_movie_is_not_flagged_as_multiple_videos(movies_root):
    make_movie(movies_root, "Shrek", "Shrek (2001) [480p] CD 1.avi", nfo=False)
    (movies_root / "Shrek" / "Shrek (2001) [480p] CD 2.avi").write_bytes(b"y" * 64)
    entry = by_folder(scan_movies(movies_root), "Shrek")
    assert "multiple-videos" not in kinds(entry)


def test_duplicate_titles_are_cross_referenced(movies_root):
    make_movie(movies_root, "Arrival", "Arrival (2016) [720p].mp4", nfo=False)
    make_movie(movies_root, "The Arrival", "The Arrival (2016) [720p].mp4", nfo=False)
    entries = scan_movies(movies_root)
    for folder in ("Arrival", "The Arrival"):
        entry = by_folder(entries, folder)
        assert "duplicate-title" in kinds(entry)
        assert entry["severity"] == "high"


def test_suspect_year_flags_title_year_confusion(movies_root):
    make_movie(movies_root, "Blade Runner", "Blade Runner (2049) [720p].mp4", nfo=False)
    entry = by_folder(scan_movies(movies_root), "Blade Runner")
    assert "suspect-year" in kinds(entry)


def test_nfo_title_mismatch_is_flagged(movies_root):
    make_movie(movies_root, "Arrival", "Arrival (2016) [720p].mp4",
               nfo_title="Some Other Film")
    entry = by_folder(scan_movies(movies_root), "Arrival")
    assert "nfo-title-mismatch" in kinds(entry)


def test_nfo_quality_mismatch_is_flagged(movies_root):
    make_movie(movies_root, "Arrival", "Arrival (2016) [720p].mp4", nfo_quality="1080p")
    entry = by_folder(scan_movies(movies_root), "Arrival")
    assert "quality-mismatch" in kinds(entry)


def test_stale_nfo_path_is_flagged(movies_root):
    make_movie(movies_root, "Arrival", "Arrival (2016) [720p].mp4",
               nfo_path="/old/place/Arrival.2016.BluRay.mp4")
    entry = by_folder(scan_movies(movies_root), "Arrival")
    assert "stale-nfo-path" in kinds(entry)


def test_unreadable_nfo_is_flagged(movies_root):
    video = make_movie(movies_root, "Arrival", "Arrival (2016) [720p].mp4", nfo=False)
    video.with_suffix(".nfo").write_text("this is not xml at all", encoding="utf-8")
    entry = by_folder(scan_movies(movies_root), "Arrival")
    assert "unreadable-nfo" in kinds(entry)


def test_hidden_and_helper_folders_are_skipped(movies_root):
    (movies_root / ".hidden").mkdir()
    (movies_root / "Subs").mkdir()
    make_movie(movies_root, "Arrival", "Arrival (2016) [720p].mp4", nfo=False)
    folders = {e["folder"] for e in scan_movies(movies_root)}
    assert folders == {"Arrival"}


# --------------------------------------------------------------------------
# payload


def test_audit_movies_payload_shape_and_summary(movies_root):
    make_movie(movies_root, "Arrival", "Arrival (2016) [720p].mp4")
    make_movie(movies_root, "1. Philosophor's Stone",
               "1. Philosophor's Stone [Other].mp4", nfo=False)
    payload = audit_movies(movies_root)

    assert payload["exists"] is True
    assert payload["root"] == str(movies_root)
    assert payload["generated_at"]
    summary = payload["summary"]
    assert summary["total"] == 2
    assert summary["flagged"] == 1
    assert summary["clean"] == 1
    assert summary["issues"] == sum(summary["by_kind"].values())
    assert sum(summary["by_severity"].values()) == summary["issues"]


def test_issues_are_sorted_most_severe_first(movies_root):
    folder = "Breaking Bad S01 Complete Bluray"
    make_movie(movies_root, folder, f"{folder} [Other].mkv", nfo=False)
    entry = by_folder(scan_movies(movies_root), folder)
    order = ["high", "medium", "low"]
    ranks = [order.index(i["severity"]) for i in entry["issues"]]
    assert ranks == sorted(ranks)


def test_get_movies_dir_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LIB_DIR", str(tmp_path / "lib"))
    monkeypatch.delenv("MOVIES_DIR", raising=False)
    assert get_movies_dir() == (tmp_path / "lib" / "movies").resolve()

    monkeypatch.setenv("MOVIES_DIR", str(tmp_path / "elsewhere"))
    assert get_movies_dir() == (tmp_path / "elsewhere").resolve()
