# tests/test_cli.py
from pathlib import Path
import os
import sys
import io
import contextlib
import pytest
from media_organiser.cli import main as cli_main



def _run_cli(argv):
    argv_backup = sys.argv[:]
    sys.argv = ["media_organiser", *argv]
    try:
        cli_main()
    finally:
        sys.argv = argv_backup


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
    out_file = mdir / "Some Movie (2019) [1080p].mkv"
    nfo = mdir / "Some Movie (2019) [1080p].nfo"

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
    out_file = mdir / "Dry Run Movie (2021) [720p].mkv"
    nfo = mdir / "Dry Run Movie (2021) [720p].nfo"

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
    assert not (existing_dir / "Some Movie (2019) [1080p] (2).mkv").exists()



def test_guess_movie_name_uses_nfo_title(tmp_path, monkeypatch):
    """
    Covers:
      t = parse_local_nfo_for_title(used_nfo)
      if t: return t, used_nfo
    by making find_nfo return a path and parse_local_nfo_for_title return a title.
    """
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir(); dst.mkdir()

    # movie in src, with year & quality so CLI goes through movie path
    mv = src / "anything.2019.1080p.mkv"
    mv.write_bytes(b"data" * 1024)

    # Provide an NFO path and a forced title from that NFO
    fake_nfo = src / "movie.nfo"
    fake_nfo.write_text("""
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
    <title>Preferred Title</title>
    <originaltitle>Preferred Title</originaltitle>
    <year>2019</year>
    <premiered>2019-01-01</premiered>
    <resolution>1080p</resolution>
    <plot></plot>
    <studio></studio>
    <id></id>
</movie>
    """)

    monkeypatch.setattr("media_organiser.nfo.find_nfo", lambda p: fake_nfo if p == mv else None)
    monkeypatch.setattr("media_organiser.nfo.parse_local_nfo_for_title", lambda nfo: "Preferred Title")
    # Ensure guess_movie_name_from_file won't be consulted (but safe if it is)
    monkeypatch.setattr("media_organiser.naming.guess_movie_name_from_file", lambda stem: "Should Not Use")

    _run_cli([str(src), str(dst), "--mode", "copy", "--emit-nfo", "movie", "--dupe-mode", "off"])

    out_dir = dst / "movies" / "Preferred Title"
    out_file = out_dir / "Preferred Title (2019) [1080p].mkv"
    assert out_file.exists(), "Title must come from parse_local_nfo_for_title"


def test_skip_items_already_under_dest_movies_or_tv(tmp_path, capsys):
    """
    Covers:
      if dest_root in path.parents and (movies_root in path.parents or tv_root in path.parents):
          continue
    by pointing source==dest and seeding a file already inside dest/movies.
    """
    dest = tmp_path / "lib"
    movies = dest / "movies" / "Existing Movie"
    movies.mkdir(parents=True)
    inside = movies / "Existing Movie (Other).mkv"
    inside.write_bytes(b"x" * 123)

    # Make source==dest; the walker will see `inside` but must skip it
    _run_cli([str(dest), str(dest), "--mode", "copy", "--emit-nfo", "movie", "--dupe-mode", "off"])

    # Assert no duplicate such as "(2)" got created
    assert not (movies / "Existing Movie (Other) (2).mkv").exists()
    out = capsys.readouterr().out
    assert "Done." in out


def test_tv_dupe_mode_hash_skips_and_prints(tmp_path, capsys):
    """
    Covers the TV duplicate-skip branch:
      if args.dupe_mode != "off":
          dup = is_duplicate_in_dir(...)
          if dup: print(...); continue
    """
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir(); dst.mkdir()

    # Destination already has the episode
    series_dir = dst / "tv" / "Show Name" / "Season 01"
    series_dir.mkdir(parents=True)
    existing = series_dir / "Show Name - S01E01 (Other).mkv"
    blob = os.urandom(2048) + os.urandom(2048)
    existing.write_bytes(blob)

    # Source has a candidate with same content but different naming
    ep = src / "Show.Name.S01E01.mkv"
    ep.write_bytes(blob)

    _run_cli([str(src), str(dst), "--mode", "copy", "--emit-nfo", "tv", "--dupe-mode", "hash"])

    out = capsys.readouterr().out
    assert "SKIP DUPLICATE:" in out
    # no "(2)" duplicate copy in dest
    assert not (series_dir / "Show Name - S01E01 (Other) (2).mkv").exists()


def test_tv_nfo_merges_src_dest_and_subtitles(tmp_path, monkeypatch):
    """
    Covers the TV NFO merge chain:
        if src_nfo: base_meta = merge_first(base_meta, read_nfo_to_meta(src_nfo))
        if dest_nfo.exists(): base_meta = merge_first(base_meta, read_nfo_to_meta(dest_nfo))
        if "subtitles" in base_meta or subs: base_meta["subtitles"] = merge_subtitles(...)
    """
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir(); dst.mkdir()

    ep = src / "Show.Name.S01E02.mkv"
    ep.write_bytes(b"episode-bytes")

    # Pretend we found a source NFO and it contributes subtitles
    fake_src_nfo = src / "tvsrc.nfo"
    fake_src_nfo.write_text("<xml/>")

    def fake_find_nfo(p: Path):
        return fake_src_nfo if p == ep else None

    # read_nfo_to_meta returns subtitles key to trigger merge_subtitles
    def fake_read_nfo_to_meta(p: Path):
        if p == fake_src_nfo:
            return {"subtitles": [{"lang": "en", "path": "s.en.srt"}], "from": "src"}
        if p.name == "dest_tv.nfo":
            return {"subtitles": [{"lang": "fr", "path": "s.fr.srt"}], "from": "dest"}
        return {}

    # make nfo_path_for point to a file that "exists" (so dest_nfo.exists() is True)
    def fake_nfo_path_for(out_file: Path, scope: str, layout: str):
        p = out_file.with_name("dest_tv.nfo")
        p.write_text("<xml/>")
        return p

    # Combine dicts "sensibly" for the test; order isn't critical
    def fake_merge_first(a, b):
        c = dict(a)
        for k, v in b.items():
            if k not in c:
                c[k] = v
        return c

    subs_seen = {"called": False}
    def fake_copy_move_sidecars(src_path, out_path, mover, mode, dry_run):
        # pretend we discovered one external subtitle
        return [{"lang": "de", "path": "s.de.srt"}]

    def fake_merge_subtitles(a, b):
        subs_seen["called"] = True
        return (a or []) + (b or [])

    # Patch into the cli module namespace (important!)
    monkeypatch.setattr("media_organiser.cli.find_nfo", fake_find_nfo)
    monkeypatch.setattr("media_organiser.cli.read_nfo_to_meta", fake_read_nfo_to_meta)
    monkeypatch.setattr("media_organiser.cli.nfo_path_for", fake_nfo_path_for)
    monkeypatch.setattr("media_organiser.cli.merge_first", fake_merge_first)
    monkeypatch.setattr("media_organiser.cli.merge_subtitles", fake_merge_subtitles)
    monkeypatch.setattr("media_organiser.cli.copy_move_sidecars", fake_copy_move_sidecars)

    # Run TV flow (must not be dry-run for NFO creation path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _run_cli([str(src), str(dst), "--mode", "copy", "--emit-nfo", "tv", "--dupe-mode", "off"])

    # Output file & NFO exist
    sdir = dst / "tv" / "Show Name" / "Season 01"
    out_ep = sdir / "Show Name - S01E02 (Other).mkv"
    assert out_ep.exists()

    # Our fake dest nfo path created by fake_nfo_path_for
    dest_nfo = sdir / "dest_tv.nfo"
    assert dest_nfo.exists()

    # ensure merge_subtitles was used
    assert subs_seen["called"] is True



def test_carry_posters_called_in_movie_flow(tmp_path, monkeypatch):
    """
    Covers:
        if args.carry_posters != "off":
            carry_poster_with_sieve(...)

    We stub carry_poster_with_sieve and assert it was called with the movie out_dir.
    """
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir()

    mv = src / "Some.Movie.2019.mkv"
    mv.write_bytes(b"data" * 256)

    called = {"ok": False, "dst_dir": None, "policy": None}

    def fake_carry_poster_with_sieve(*, src_context, dst_dir, policy, min_w, min_h,
                                      aspect_lo, aspect_hi, bad_words, mover, mode, dry_run):
        called["ok"] = True
        called["dst_dir"] = dst_dir
        called["policy"] = policy

    monkeypatch.setattr("media_organiser.cli.carry_poster_with_sieve", fake_carry_poster_with_sieve)

    # Don't waste time writing NFOs here
    run_cli_in_proc(src, dst, ["--mode", "copy", "--emit-nfo", "off", "--dupe-mode", "off", "--carry-posters", "keep"])

    # Expect posters logic to have been invoked and dst_dir is the movie folder
    assert called["ok"] is True
    assert called["policy"] == "keep"
    assert called["dst_dir"] == (dst / "movies" / "Some Movie")


# def test_movie_nfo_merges_dest_and_subtitles(tmp_path, monkeypatch):
#     """
#     Covers (movie branch):
#         base_meta = merge_first(base_meta, read_nfo_to_meta(dest_nfo))
#         base_meta["subtitles"] = merge_subtitles(base_meta.get("subtitles"), subs)
#
#     Strategy:
#       - Stub nfo_path_for to return a path that exists (dest_nfo.exists() True).
#       - Stub read_nfo_to_meta to return some metadata (we tag it as 'from': 'dest').
#       - Stub merge_first to confirm it's called with the dest metadata.
#       - Stub copy_move_sidecars to return non-empty 'subs' so subtitle merge triggers.
#       - Stub merge_subtitles to confirm it's called.
#     """
#     src = tmp_path / "in"
#     dst = tmp_path / "out"
#     src.mkdir()
#
#     mv = src / "Title.2020.1080p.mkv"
#     mv.write_bytes(b"content" * 128)
#
#     # Ensure movie flow: no source NFO
#     monkeypatch.setattr("media_organiser.nfo.find_nfo", lambda p: None)
#
#     # When cli asks for dest nfo path, ensure it exists so `dest_nfo.exists()` is True
#     def fake_nfo_path_for(out_file: Path, scope: str, layout: str):
#         p = out_file.with_name("movie_existing_dest.nfo")
#         p.parent.mkdir(parents=True, exist_ok=True)
#         p.write_text("<movie><title>Title</title><year>2020</year></movie>")
#         return p
#     monkeypatch.setattr("media_organiser.nfo.nfo_path_for", fake_nfo_path_for)
#
#     # read_nfo_to_meta returns a marker so we can check merge_first was called with it
#     def fake_read_nfo_to_meta(p: Path):
#         if p.name == "movie_existing_dest.nfo":
#             return {"from": "dest", "subtitles": [{"lang": "fr", "path": "d.fr.srt"}]}
#         return {}
#     monkeypatch.setattr("media_organiser.nfo.read_nfo_to_meta", fake_read_nfo_to_meta)
#
#     # Track merge_first calls and return a shallow-first-merge
#     merges = {"dest_merge_seen": False}
#     def fake_merge_first(a, b):
#         merges["dest_merge_seen"] = merges.get("dest_merge_seen", False) or (b.get("from") == "dest")
#         c = dict(a)
#         for k, v in b.items():
#             if k not in c:
#                 c[k] = v
#         return c
#     monkeypatch.setattr("media_organiser.nfo.merge_first", fake_merge_first)
#
#     # Make sidecar discovery non-empty so the 'merge_subtitles' branch fires
#     monkeypatch.setattr("media_organiser.sidecars.copy_move_sidecars",
#                         lambda *args, **kwargs: [{"lang": "en", "path": "s.en.srt"}])
#
#     # Track that merge_subtitles is actually called
#     called = {"merge_subs": False}
#     def fake_merge_subtitles(a, b):
#         called["merge_subs"] = True
#         return (a or []) + (b or [])
#     monkeypatch.setattr("media_organiser.nfo.merge_subtitles", fake_merge_subtitles)
#
#     # Avoid touching filesystem XML content of final write; we just want branches
#     monkeypatch.setattr("media_organiser.nfo.write_movie_nfo", lambda *a, **k: None)
#
#     # Run (not dry-run, so NFO code executes)
#     buf = io.StringIO()
#     with contextlib.redirect_stdout(buf):
#         run_cli_in_proc(src, dst, ["--mode", "copy", "--emit-nfo", "movie", "--dupe-mode", "off"])
#
#     # Assertions: output file exists (ensures we went through movie path)
#     out_dir = dst / "movies" / "Title"
#     out_file = out_dir / "Title (2020) [1080p].mkv"
#     assert out_file.exists()
#
#     # Both merge points were exercised
#     assert merges["dest_merge_seen"] is True, "merge_first must be called with dest_nfo metadata"
#     assert called["merge_subs"] is True, "merge_subtitles must be called when subs or base_meta['subtitles'] present"