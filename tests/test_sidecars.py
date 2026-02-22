from media_organiser.sidecars import copy_move_sidecars

def _noop_mover(src, dst, mode, dry_run):
    # emulate copy: create dst with same content
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())

def test_copy_sidecars_preserves_suffix(tmp_tree):
    video = tmp_tree("in/Movie.2019.mkv", b"vid")
    srt_en = tmp_tree("in/Movie.2019.en.srt", "subs")
    srt_forced = tmp_tree("in/Movie.2019.en.forced.srt", "forced")
    out = tmp_tree("out/dummy.txt")  # create /out
    dst_video = out.parent / "Movie Name (1080p).mkv"
    moved = copy_move_sidecars(video, dst_video, _noop_mover, "copy", False)
    names = {m["file"] for m in moved}
    assert "Movie Name (1080p).en.srt" in names
    assert "Movie Name (1080p).en.forced.srt" in names

def test_copy_sidecars_moves_nfo_but_does_not_list_as_subtitle(tmp_tree):
    video = tmp_tree("in/Movie.2019.mkv", b"vid")
    nfo = tmp_tree("in/Movie.2019.nfo", b"<movie><title>Movie</title></movie>")
    out = tmp_tree("out/dummy.txt")
    dst_video = out.parent / "Movie Name (1080p).mkv"
    moved = copy_move_sidecars(video, dst_video, _noop_mover, "copy", False)
    # NFO is moved (same stem) but not in subtitle list
    assert len(moved) == 0
    assert (out.parent / "Movie Name (1080p).nfo").exists()
    assert (out.parent / "Movie Name (1080p).nfo").read_bytes() == nfo.read_bytes()
