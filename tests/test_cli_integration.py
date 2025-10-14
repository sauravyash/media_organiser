import sys
from pathlib import Path
import xml.etree.ElementTree as ET
import types

from media_organiser.cli import main as cli_main

def run_cli(tmp_path, args):
    argv = ["-m", "media_organiser"]  # mimic -m call
    sys_argv_backup = sys.argv[:]
    sys.argv = ["media_organiser"] + args
    try:
        cli_main()
    finally:
        sys.argv = sys_argv_backup

def test_movie_flow_and_nfo(tmp_path):
    # Arrange source
    src = tmp_path / "in"
    dst = tmp_path / "out"
    (src / "in").mkdir(parents=True, exist_ok=True)  # just ensure parent exists
    movie = src / "Some.Movie.2019.1080p.x265.mkv"
    movie.parent.mkdir(parents=True, exist_ok=True)
    movie.write_bytes(b"V" * 4096)

    # Run
    run_cli(tmp_path, [str(src), str(dst), "--mode", "copy", "--emit-nfo", "movie", "--dupe-mode", "off"])

    # Assert structure
    mdir = dst / "movies" / "Some Movie"
    out_file = mdir / "Some Movie (1080p).mkv"
    assert out_file.exists()

    # NFO exists and has xml declaration
    nfo = mdir / "Some Movie (1080p).nfo"
    assert nfo.exists()
    xml = nfo.read_bytes()
    assert xml.startswith(b"<?xml")

def test_tv_flow_jellyfin_season_names(tmp_path):
    src = tmp_path / "in"
    dst = tmp_path / "out"
    ep = src / "Show.Name.S00E01.mkv"
    ep.parent.mkdir(parents=True, exist_ok=True)
    ep.write_bytes(b"X")

    run_cli(tmp_path, [str(src), str(dst), "--mode", "copy", "--emit-nfo", "tv", "--dupe-mode", "off"])

    # Season 00 -> Specials; and tvshow.nfo created
    sdir = dst / "tv" / "Show Name" / "Specials"
    assert sdir.exists()
    tvshow_nfo = sdir / "Show Name - S00E01 (Other).nfo"
    assert tvshow_nfo.exists()
    # episode file present
    out = sdir / "Show Name - S00E01 (Other).mkv"
    assert out.exists()
