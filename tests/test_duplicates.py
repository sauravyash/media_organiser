from media_organiser.duplicates import is_duplicate_in_dir, quick_fingerprint

def test_duplicate_by_hash(tmp_path):
    # existing file in dest folder
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    existing = dest_dir / "Movie (1080p).mkv"
    existing.write_bytes(b"A" * 10_000 + b"B" * 10_000)  # 20 KB

    # candidate elsewhere with same content but different name
    candidate = tmp_path / "MovieX.mkv"
    candidate.write_bytes(existing.read_bytes())

    # should detect duplicate
    dup = is_duplicate_in_dir(candidate, dest_dir, mode="hash")
    assert dup == existing

def test_quick_fingerprint_is_stable(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"HEAD" * 4096 + b"TAIL" * 4096)
    size, h = quick_fingerprint(p)
    assert size == p.stat().st_size
    # change one byte at tail, hash should change
    p.write_bytes(b"HEAD" * 4096 + b"TAIl" * 4096)
    size2, h2 = quick_fingerprint(p)
    assert h != h2
