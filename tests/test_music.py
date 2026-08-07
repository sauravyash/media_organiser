"""Tests for the beets-backed music library audit (media_organiser.music)."""
import subprocess

import pytest

from media_organiser import music
from media_organiser.music import (
    ALBUM_FIELDS,
    BeetsUnavailable,
    FIELD_SEP,
    ITEM_FIELDS,
    audit_album,
    audit_track,
    beet_base_args,
    parse_rows,
    run_beet,
    scan_music,
)


def item(**overrides) -> dict:
    """A fully-tagged track; override fields to introduce a specific problem."""
    base = {
        "id": "1",
        "artist": "Portishead",
        "albumartist": "Portishead",
        "album": "Dummy",
        "title": "Glory Box",
        "track": "10",
        "tracktotal": "11",
        "disc": "1",
        "year": "1994",
        "genre": "Trip-Hop",
        "format": "FLAC",
        "bitrate": "900kbps",
        "length": "5:06",
        "path": "",
        "mb_trackid": "abc-123",
        "mb_albumid": "def-456",
        "comp": "False",
        "added": "2024-01-01",
    }
    base.update(overrides)
    return base


def album(**overrides) -> dict:
    base = {
        "id": "7",
        "albumartist": "Portishead",
        "album": "Dummy",
        "year": "1994",
        "genre": "Trip-Hop",
        "albumtype": "album",
        "mb_albumid": "def-456",
        "comp": "False",
        "added": "2024-01-01",
        "path": "",
    }
    base.update(overrides)
    return base


def kinds(issues) -> set[str]:
    return {i.kind for i in issues}


# --------------------------------------------------------------------------
# plumbing


def test_beet_base_args_picks_up_env(monkeypatch):
    monkeypatch.setenv("BEET_BIN", "/opt/beets/beet")
    monkeypatch.setenv("BEETS_CONFIG", "/cfg/config.yaml")
    monkeypatch.setenv("BEETS_LIBRARY", "/lib/musiclibrary.db")
    monkeypatch.setenv("BEETS_DIRECTORY", "/music")
    assert beet_base_args() == [
        "/opt/beets/beet", "-c", "/cfg/config.yaml",
        "-l", "/lib/musiclibrary.db", "-d", "/music",
    ]


def test_beet_base_args_defaults_to_bare_beet(monkeypatch):
    for name in ("BEET_BIN", "BEETS_CONFIG", "BEETS_LIBRARY", "BEETS_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)
    assert beet_base_args() == ["beet"]


def test_parse_rows_splits_on_unit_separator():
    line = FIELD_SEP.join(["1", "A", "B"])
    rows = parse_rows(line + "\n", ("id", "artist", "album"))
    assert rows == [{"id": "1", "artist": "A", "album": "B"}]


def test_parse_rows_skips_malformed_and_blank_lines():
    good = FIELD_SEP.join(["1", "A", "B"])
    bad = FIELD_SEP.join(["1", "A"])  # a newline inside a tag split this record
    rows = parse_rows(f"{good}\n{bad}\n\n", ("id", "artist", "album"))
    assert rows == [{"id": "1", "artist": "A", "album": "B"}]


def test_run_beet_raises_when_binary_missing(monkeypatch):
    monkeypatch.setenv("BEET_BIN", "definitely-not-a-real-beet-binary")
    with pytest.raises(BeetsUnavailable, match="was not found"):
        run_beet(["ls"])


def test_run_beet_raises_on_nonzero_exit(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 2, stdout="", stderr="no such library")

    monkeypatch.setattr(music.subprocess, "run", fake_run)
    with pytest.raises(BeetsUnavailable, match="no such library"):
        run_beet(["ls"])


def test_run_beet_raises_on_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="beet", timeout=1)

    monkeypatch.setattr(music.subprocess, "run", fake_run)
    with pytest.raises(BeetsUnavailable, match="timed out"):
        run_beet(["ls"])


def test_run_beet_returns_stdout(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(music.subprocess, "run", fake_run)
    assert run_beet(["ls"]) == "ok\n"


# --------------------------------------------------------------------------
# track-level findings


def test_fully_tagged_track_has_no_issues():
    assert audit_track(item(), {}) == []


@pytest.mark.parametrize("field,value,kind", [
    ("artist", "", "missing-artist"),
    ("artist", "Unknown Artist", "missing-artist"),
    ("album", "[Unknown Album]", "missing-album"),
    ("title", "", "missing-title"),
    ("title", "Track 05", "missing-title"),
    ("year", "0", "missing-year"),
    ("track", "0", "missing-track-number"),
    ("mb_trackid", "", "unmatched"),
    ("genre", "", "missing-genre"),
])
def test_missing_tags_are_flagged(field, value, kind):
    assert kind in kinds(audit_track(item(**{field: value}), {}))


def test_suspect_year_is_flagged():
    assert "suspect-year" in kinds(audit_track(item(year="1492"), {}))


def test_low_bitrate_only_applies_to_lossy_formats():
    lossy = audit_track(item(format="MP3", bitrate="96kbps"), {})
    assert "low-bitrate" in kinds(lossy)
    # A low reported bitrate on a lossless file is not a quality problem.
    lossless = audit_track(item(format="FLAC", bitrate="96kbps"), {})
    assert "low-bitrate" not in kinds(lossless)


def test_missing_file_on_disk_is_flagged(tmp_path):
    gone = str(tmp_path / "gone.flac")
    issues = audit_track(item(path=gone), {})
    assert "file-missing" in kinds(issues)
    missing = next(i for i in issues if i.kind == "file-missing")
    assert missing.suggestion.startswith("beet remove id:1")


def test_existing_file_on_disk_is_not_flagged(tmp_path):
    real = tmp_path / "here.flac"
    real.write_bytes(b"audio")
    assert "file-missing" not in kinds(audit_track(item(path=str(real)), {}))


def test_duplicate_tracks_are_flagged_on_the_second_copy():
    seen = {}
    assert "duplicate-track" not in kinds(audit_track(item(id="1"), seen))
    issues = audit_track(item(id="2"), seen)
    assert "duplicate-track" in kinds(issues)
    dup = next(i for i in issues if i.kind == "duplicate-track")
    assert "id:1" in dup.suggestion


def test_various_artists_is_not_treated_as_a_placeholder():
    assert "missing-artist" not in kinds(audit_track(item(artist="Various Artists"), {}))


def test_suggestions_are_runnable_beet_commands():
    issues = audit_track(item(year="0", genre=""), {})
    for issue in issues:
        assert issue.suggestion.startswith("beet ")


# --------------------------------------------------------------------------
# album-level findings


def test_fully_tagged_album_has_no_issues():
    assert audit_album(album(), track_count=11) == []


def test_album_without_tracks_is_flagged():
    issues = audit_album(album(), track_count=0)
    assert "empty-album" in kinds(issues)


def test_album_missing_metadata_is_flagged():
    issues = audit_album(album(albumartist="", mb_albumid="", year="0"), track_count=5)
    assert kinds(issues) == {"missing-albumartist", "unmatched", "missing-year"}
    for issue in issues:
        assert "-a id:7" in issue.suggestion or issue.suggestion.startswith("Re-import")


# --------------------------------------------------------------------------
# whole-library payload


def test_scan_music_reports_unavailable_beets(monkeypatch):
    monkeypatch.setenv("BEET_BIN", "definitely-not-a-real-beet-binary")
    payload = scan_music()
    assert payload["available"] is False
    assert "not found" in payload["error"]
    assert payload["tracks"] == []
    assert payload["albums"] == []
    assert payload["summary"]["total"] == 0


def test_scan_music_builds_payload_from_beet_output(monkeypatch):
    def fake_run_beet(args, timeout=music.DEFAULT_TIMEOUT):
        if "-a" in args:
            rows = [album(), album(id="8", album="Third", mb_albumid="", year="0")]
            fields = ALBUM_FIELDS
        else:
            rows = [item(), item(id="2", title="Roads", track="11", genre="")]
            fields = ITEM_FIELDS
        return "\n".join(FIELD_SEP.join(r[f] for f in fields) for r in rows) + "\n"

    monkeypatch.setattr(music, "run_beet", fake_run_beet)
    payload = scan_music()

    assert payload["available"] is True
    assert payload["error"] is None
    assert len(payload["tracks"]) == 2
    assert len(payload["albums"]) == 2

    glory = next(t for t in payload["tracks"] if t["title"] == "Glory Box")
    assert glory["issues"] == []
    assert glory["year"] == 1994
    assert glory["bitrate"] == 900
    assert glory["matched"] is True

    roads = next(t for t in payload["tracks"] if t["title"] == "Roads")
    assert {i["kind"] for i in roads["issues"]} == {"missing-genre"}

    third = next(a for a in payload["albums"] if a["album"] == "Third")
    assert {i["kind"] for i in third["issues"]} >= {"unmatched", "missing-year", "empty-album"}

    assert payload["summary"]["total"] == 2
    assert payload["album_summary"]["total"] == 2


def test_failing_album_listing_does_not_blank_the_tracks(monkeypatch):
    """`beet ls -a` aborts on the first unrenderable album; tracks must survive."""
    def fake_run_beet(args, timeout=music.DEFAULT_TIMEOUT):
        if "-a" in args:
            raise BeetsUnavailable("beets exited with code 1: ValueError: empty album for album id 1")
        return FIELD_SEP.join(item()[f] for f in ITEM_FIELDS) + "\n"

    monkeypatch.setattr(music, "run_beet", fake_run_beet)
    payload = scan_music()

    assert payload["available"] is True
    assert payload["error"] is None
    assert len(payload["tracks"]) == 1
    assert payload["albums"] == []
    assert "empty album" in payload["album_error"]


def test_failing_track_listing_marks_the_whole_library_unavailable(monkeypatch):
    def fake_run_beet(args, timeout=music.DEFAULT_TIMEOUT):
        raise BeetsUnavailable("beets exited with code 1: no such table")

    monkeypatch.setattr(music, "run_beet", fake_run_beet)
    payload = scan_music()
    assert payload["available"] is False
    assert "no such table" in payload["error"]


def test_scan_music_counts_tracks_per_album(monkeypatch):
    def fake_run_beet(args, timeout=music.DEFAULT_TIMEOUT):
        if "-a" in args:
            return FIELD_SEP.join(album()[f] for f in ALBUM_FIELDS) + "\n"
        rows = [item(id="1"), item(id="2", title="Roads", track="11")]
        return "\n".join(FIELD_SEP.join(r[f] for f in ITEM_FIELDS) for r in rows) + "\n"

    monkeypatch.setattr(music, "run_beet", fake_run_beet)
    payload = scan_music()
    assert payload["albums"][0]["tracks"] == 2
    assert payload["albums"][0]["issues"] == []
