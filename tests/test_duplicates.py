# tests/test_duplicates.py
from pathlib import Path
import os

import media_organiser.duplicates as dup
from media_organiser.duplicates import (
    normalized_stem_ignore_quality,
    quick_fingerprint,
    is_duplicate_in_dir,
)


def write(p: Path, data: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


# ---------- normalized_stem_ignore_quality ----------
def test_normalized_stem_strips_resolution_tokens(tmp_path):
    # Common style: "Movie (2160p).mkv" vs candidate "Movie.mkv"
    p1 = tmp_path / "Movie (2160p).mkv"
    p2 = tmp_path / "Movie.mkv"
    # we only pass Paths; function uses .stem
    assert normalized_stem_ignore_quality(p1) == normalized_stem_ignore_quality(p2)


# ---------- quick_fingerprint ----------

def test_quick_fingerprint_small_file_reads_all(tmp_path):
    # Force 'size <= 2 * sample_bytes' branch by giving a bigger sample_bytes
    data = b"hello world" * 10
    f = tmp_path / "small.bin"
    write(f, data)
    size, md5hex = quick_fingerprint(f, sample_bytes=1024)  # 2KB > len(data)
    assert size == len(data)
    # sanity: matches plain MD5 of entire file
    import hashlib as _hh
    assert md5hex == _hh.md5(data).hexdigest()


def test_quick_fingerprint_head_and_tail_sampling(tmp_path):
    # Trigger 'else' branch: sample head + tail around sample_bytes
    sample_bytes = 8
    # Build a file with distinct head/tail regions
    data = b"HEAD1234" + b"X" * 64 + b"TAIL5678"
    assert len(data) > 2 * sample_bytes  # ensure we hit the branch
    f = tmp_path / "big.bin"
    write(f, data)
    size1, h1 = quick_fingerprint(f, sample_bytes=sample_bytes)
    # Change a tail byte -> fingerprint must change
    mutated = data[:-1] + (bytes([data[-1] ^ 0x01]))
    write(f, mutated)
    size2, h2 = quick_fingerprint(f, sample_bytes=sample_bytes)
    assert size1 == size2 == len(mutated)
    assert h1 != h2


# ---------- is_duplicate_in_dir (all modes) ----------

def test_is_duplicate_off_returns_none(tmp_path):
    src = tmp_path / "MovieX.mkv"
    write(src, b"A")
    dest = tmp_path / "lib"
    dest.mkdir()
    assert is_duplicate_in_dir(src, dest, mode="off") is None


def test_duplicate_by_name_ignores_quality_tokens(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    # Destination has 1080p; candidate has 2160p.
    existing = dest / "My Film (1080p).mkv"
    write(existing, b"XXX")

    candidate = tmp_path / "incoming" / "My Film (2160p).mkv"
    write(candidate, b"YYYY")

    # After normalization both stems become "My Film ()", so NAME mode should match.
    match = is_duplicate_in_dir(candidate, dest, mode="name")
    assert match == existing


def test_duplicate_by_size(tmp_path):
    dest = tmp_path / "dest"; dest.mkdir()
    # create a reference file of size 1024
    existing = dest / "clip.mkv"
    write(existing, b"x" * 1024)
    # candidate same size but different name/content
    candidate = tmp_path / "clip2.mkv"
    write(candidate, b"y" * 1024)
    match = is_duplicate_in_dir(candidate, dest, mode="size")
    assert match == existing


def test_duplicate_by_hash_when_sizes_equal(tmp_path):
    dest = tmp_path / "dest"; dest.mkdir()
    existing = dest / "Movie (1080p).mkv"
    blob = os.urandom(4096) + os.urandom(4096)  # different head/tail chunks
    write(existing, blob)
    # candidate elsewhere with identical bytes but different name
    candidate = tmp_path / "MovieX.mkv"
    write(candidate, blob)
    match = is_duplicate_in_dir(candidate, dest, mode="hash")
    assert match == existing


def test_hash_mode_skips_when_sizes_differ(tmp_path, monkeypatch):
    dest = tmp_path / "dest"; dest.mkdir()
    existing = dest / "a.mkv"
    write(existing, b"A" * 100)  # 100B
    candidate = tmp_path / "b.mkv"
    write(candidate, b"A" * 101)  # 101B
    # Ensure we don't accidentally compute hashes when sizes differ
    called = {"fp": 0}

    real_qf = dup.quick_fingerprint

    def spy_qf(p, sample_bytes=1 << 20):
        called["fp"] += 1
        return real_qf(p, sample_bytes)

    monkeypatch.setattr(dup, "quick_fingerprint", spy_qf)
    match = is_duplicate_in_dir(candidate, dest, mode="hash")
    assert match is None
    # Only the candidate might have been computed lazily if a same-size match existed;
    # in this case, sizes differ so nothing should have been hashed.
    assert called["fp"] == 0


def test_is_duplicate_skips_non_files_and_non_video_suffix(tmp_path):
    """
    Covers: `if not existing.is_file() or existing.suffix.lower() not in VIDEO_EXTS: continue`
    - A subdirectory (not a file) triggers the first part
    - A .txt file (non-video suffix) triggers the second part
    Both should be skipped, yielding no duplicate.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    # Non-file entry (directory)
    (dest / "a_dir").mkdir()

    # Non-video file
    (dest / "notes.txt").write_text("hello")

    # Candidate is a video file that shouldn't match either
    candidate = tmp_path / "MovieX.mkv"
    write(candidate, b"A" * 100)

    # NAME mode (but entries are skipped before name check)
    assert is_duplicate_in_dir(candidate, dest, mode="name") is None
    # SIZE mode (same — skipped before size check)
    assert is_duplicate_in_dir(candidate, dest, mode="size") is None
    # HASH mode (same — skipped before size/hash checks)
    assert is_duplicate_in_dir(candidate, dest, mode="hash") is None


def test_hash_mode_size_mismatch_continue_no_hash(tmp_path, monkeypatch):
    """
    Covers: `if existing.stat().st_size != cand_size: continue`
    Ensure quick_fingerprint is NOT called at all when sizes differ.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    # Existing video with size 100B
    existing = dest / "clip.mkv"
    write(existing, b"A" * 100)

    # Candidate with a different size (101B)
    candidate = tmp_path / "clip2.mkv"
    write(candidate, b"A" * 101)

    calls = {"count": 0}
    real_qf = dup.quick_fingerprint

    def spy_qf(p: Path, sample_bytes=1 << 20):
        calls["count"] += 1
        return real_qf(p, sample_bytes)

    # Spy on quick_fingerprint to confirm it isn't used due to size mismatch
    monkeypatch.setattr(dup, "quick_fingerprint", spy_qf)

    match = is_duplicate_in_dir(candidate, dest, mode="hash")
    assert match is None
    assert calls["count"] == 0

def test_is_duplicate_file_not_found_error_continue(tmp_path, monkeypatch):
    """
    Cover the `except FileNotFoundError: continue` branch.
    Ensure is_file() doesn't trigger stat(), then raise at existing.stat().
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    ghost = dest / "ghost.mkv"
    ghost.write_bytes(b"AAAA")

    candidate = tmp_path / "MovieX.mkv"
    candidate.write_bytes(b"AAAA")

    # Make is_file() return True for ghost WITHOUT calling stat()
    real_is_file = Path.is_file
    def fake_is_file(self: Path):
        if self == ghost:
            return True
        return real_is_file(self)
    monkeypatch.setattr(Path, "is_file", fake_is_file)

    # Now make stat() raise ONLY for ghost (inside the try in is_duplicate_in_dir)
    real_stat = Path.stat
    def flaky_stat(self: Path, **kwargs):
        if self == ghost:
            raise FileNotFoundError("gone")
        return real_stat(self, **kwargs)
    monkeypatch.setattr(Path, "stat", flaky_stat)

    # Use a mode that calls existing.stat() (size/hash); should swallow the error.
    result = is_duplicate_in_dir(candidate, dest, mode="size")
    assert result is None


