# tests/test_cli.py
from pathlib import Path
import os
import sys
import io
import contextlib
import pytest
from media_organiser.cli import main as cli_main


def run_cli_in_proc(src: Path, dst: Path, extra_args: list[str]):
    """
    Run cli.main() with sys.argv patched.
    Always include both source and dest to avoid accidental 'src=dest' cases.
    """
    argv_backup = sys.argv[:]
    sys.argv = ["media_organiser", str(src), str(dst), *extra_args]
    try:
        cli_main()
    finally:
        sys.argv = argv_backup


def test_movie_flow_and_nfo(tmp_path):
    # Arrange: source has a movie file with quality + year in name
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir()
    (dst / "dummy").mkdir(parents=True, exist_ok=True)  # ensure dest root exists

    movie = src / "Some.Movie.2019.1080p.x265.mkv"
    movie.write_bytes(b"V" * 4096)

    # Act
    run_cli_in_proc(src, dst, ["--mode", "copy", "--emit-nfo", "movie", "--dupe-mode", "off"])

    # Assert: movie copied into movies/<clean title>/..., NFO next to it
    mdir = dst / "movies" / "Some Movie"
    out_file = mdir / "Some Movie (1080p).mkv"
    nfo = mdir / "Some Movie (1080p).nfo"

    assert out_file.exists(), "Movie should be copied to the movies library"
    assert nfo.exists(), "Same-stem NFO should be emitted by default (layout=same-stem)"
    # NFO should look like XML (implementation writes an XML header)
    assert nfo.read_bytes().startswith(b"<?xml")


def test_tv_flow_s00_goes_to_specials_and_nfo(tmp_path):
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir()

    ep = src / "Show.Name.S00E01.mkv"
    ep.write_bytes(b"X" * 1024)

    run_cli_in_proc(src, dst, ["--mode", "copy", "--emit-nfo", "tv", "--dupe-mode", "off"])

    sdir = dst / "tv" / "Show Name" / "Specials"
    out_ep = sdir / "Show Name - S00E01 (Other).mkv"
    out_nfo = sdir / "Show Name - S00E01 (Other).nfo"

    assert sdir.exists(), "Season 00 should map to 'Specials'"
    assert out_ep.exists(), "Episode file should be placed in Specials"
    assert out_nfo.exists(), "Episode NFO should be emitted next to the file"
    assert out_nfo.read_bytes().startswith(b"<?xml")


def test_dupe_mode_validation_raises_systemexit(tmp_path):
    """
    Argparse choices should reject invalid --dupe-mode.
    """
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir(); dst.mkdir()
    # Need at least one file so the CLI walks the tree harmlessly if it ran; but we expect exit on parse.
    (src / "Any.2020.1080p.mkv").write_bytes(b"A")

    argv_backup = sys.argv[:]
    sys.argv = ["media_organiser", str(src), str(dst), "--dupe-mode", "bogus"]
    try:
        with pytest.raises(SystemExit):
            cli_main()
    finally:
        sys.argv = argv_backup


def test_dry_run_does_not_move_or_write_nfo(tmp_path, monkeypatch):
    """
    --dry-run should print planned actions but not perform file writes or NFO generation.
    """
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir()

    movie = src / "Dry.Run.Movie.2021.720p.mkv"
    movie.write_bytes(b"DATA" * 1024)

    # Spy on write_movie_nfo to ensure it's not called under --dry-run
    called = {"count": 0}
    def _spy_write_movie_nfo(*args, **kwargs):
        called["count"] += 1
    monkeypatch.setattr("media_organiser.nfo.write_movie_nfo", _spy_write_movie_nfo)

    # Capture stdout to ensure CLI runs without error
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_cli_in_proc(src, dst, ["--mode", "copy", "--emit-nfo", "movie", "--dry-run"])

    # Destination structure is created, but no files copied and no NFO written
    mdir = dst / "movies" / "Dry Run Movie"
    out_file = mdir / "Dry Run Movie (720p).mkv"
    nfo = mdir / "Dry Run Movie (720p).nfo"

    assert mdir.exists(), "Planner creates target directories even in dry-run"
    assert not out_file.exists(), "No file copy in dry-run"
    assert not nfo.exists(), "No NFO written in dry-run"
    assert called["count"] == 0, "write_movie_nfo must not be called in dry-run"


def test_duplicate_skip_in_hash_mode_prints_and_skips(tmp_path, capsys):
    """
    When a duplicate exists in the destination and --dupe-mode hash is active,
    CLI should print 'SKIP DUPLICATE' and not copy the candidate again.
    """
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir()
    # Prepare existing movie in destination library
    existing_dir = dst / "movies" / "Some Movie"
    existing_dir.mkdir(parents=True, exist_ok=True)
    existing = existing_dir / "Some Movie (1080p).mkv"

    blob = os.urandom(4096) + os.urandom(4096)
    existing.write_bytes(blob)

    # Candidate with the same bytes but different name in source
    candidate = src / "Some.Movie.2019.1080p.x265.mkv"
    candidate.write_bytes(blob)

    # Run with hash dupe mode
    run_cli_in_proc(src, dst, ["--mode", "copy", "--emit-nfo", "movie", "--dupe-mode", "hash"])

    captured = capsys.readouterr().out
    assert "SKIP DUPLICATE" in captured
    # Ensure no second copy was created with a different stem
    assert not (existing_dir / "Some Movie (1080p) (2).mkv").exists()
