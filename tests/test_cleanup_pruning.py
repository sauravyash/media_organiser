from pathlib import Path
import sys
from media_organiser.cli import main as cli_main

def _run_cli(src: Path, dst: Path, extra_args: list[str]):
    argv_bak = sys.argv[:]
    sys.argv = ["media_organiser", str(src), str(dst), *extra_args]
    try:
        cli_main()
    finally:
        sys.argv = argv_bak

def test_prune_removes_yts_poster_and_folder(tmp_path):
    """
    Folder contains only a YTS poster after moving the video -> should delete
    the poster and the folder (pruned).
    """
    src = tmp_path / "in"; dst = tmp_path / "out"
    # source tree
    movie_dir = src / "Some.Movie.2019.1080p"
    movie_dir.mkdir(parents=True)
    vid = movie_dir / "Some.Movie.2019.1080p.mkv"
    vid.write_bytes(b"video")
    # junk poster that mentions a bad word (yts)
    junk = movie_dir / "Some.Movie.2019.YTS.jpg"
    junk.write_bytes(b"img")

    # run with move; ensure bad words include yts (default does)
    _run_cli(src, dst, ["--mode", "move", "--emit-nfo", "off", "--dupe-mode", "off"])

    # after move, the source subfolder should be pruned away
    assert not movie_dir.exists(), "source folder should be removed after junk-aware pruning"
    # file should now be in library
    out = dst / "movies" / "Some Movie" / "Some Movie (2019) [1080p].mkv"

    assert out.exists()

def test_prune_keeps_folder_with_non_junk_leftovers(tmp_path):
    """
    If a leftover file does not match bad words, pruning should NOT remove the folder.
    """
    src = tmp_path / "in"; dst = tmp_path / "out"
    movie_dir = src / "Another.Movie.2018"
    movie_dir.mkdir(parents=True)
    vid = movie_dir / "Another.Movie.2018.mkv"
    vid.write_bytes(b"video")
    # a plain note.txt without bad words
    keep = movie_dir / "readme.txt"
    keep.write_text("notes")

    _run_cli(src, dst, ["--mode", "move", "--emit-nfo", "off", "--dupe-mode", "off"])
    # Folder should remain because non-junk file still there
    assert movie_dir.exists(), "folder must remain when non-junk files are present"
