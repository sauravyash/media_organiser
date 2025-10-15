# tests/test_io_ops.py
from pathlib import Path
import os
import hashlib
import shutil
import media_organiser.io_ops as io_ops


def write(p: Path, data: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_safe_path_creates_parents_and_returns_same_when_free(tmp_path):
    # parent dirs are created even if file doesn't exist yet
    dst = tmp_path / "out" / "nested" / "file.txt"
    cand = io_ops.safe_path(dst)
    assert cand == dst
    assert dst.parent.exists()


def test_safe_path_appends_increment_suffix_when_taken(tmp_path):
    base = tmp_path / "out" / "f.dat"
    write(base, b"A")
    # next should be (2)
    cand2 = io_ops.safe_path(base)
    assert cand2.name == "f (2).dat"
    write(cand2, b"B")
    # with base and (2) present, next should be (3)
    cand3 = io_ops.safe_path(base)
    assert cand3.name == "f (3).dat"


def test_copy_basic_and_dry_run_and_collision_numbering(tmp_path, capsys):
    src = tmp_path / "src" / "a.txt"
    write(src, b"hello")

    # dry-run: prints but does nothing
    dst = tmp_path / "out" / "a.txt"
    io_ops.do_move_or_copy(src, dst, mode="copy", dry_run=True)
    out = capsys.readouterr().out
    assert "COPY:" in out and str(src) in out and str(dst) in out
    assert not dst.exists()  # no file written

    # real copy
    io_ops.do_move_or_copy(src, dst, mode="copy", dry_run=False)
    assert dst.exists() and dst.read_bytes() == b"hello"

    # collision -> writes to "a (2).txt"
    io_ops.do_move_or_copy(src, dst, mode="copy", dry_run=False)
    dst2 = dst.with_name("a (2).txt")
    assert dst2.exists() and dst2.read_bytes() == b"hello"


def test_move_basic_and_collision_numbering(tmp_path, capsys):
    src = tmp_path / "in" / "b.bin"
    write(src, os.urandom(64))
    h = sha256(src)

    dst = tmp_path / "lib" / "b.bin"
    io_ops.do_move_or_copy(src, dst, mode="move", dry_run=False)
    out = capsys.readouterr().out
    assert "MOVE:" in out and str(src) in out and str(dst) in out
    assert not src.exists() and dst.exists()
    assert sha256(dst) == h

    # moving again with a new source collides -> should go to "b (2).bin"
    src2 = tmp_path / "in" / "b.bin"
    write(src2, b"NEW")
    io_ops.do_move_or_copy(src2, dst, mode="move", dry_run=False)
    dst2 = dst.with_name("b (2).bin")
    assert dst2.exists() and dst2.read_bytes() == b"NEW"
    assert not src2.exists()


def test_move_fallback_on_shutil_error(monkeypatch, tmp_path):
    """
    Force shutil.move to raise shutil.Error to cover the exception path:
    function should copy2 then unlink the source.
    """
    # make a source and destination
    src = tmp_path / "err" / "c.txt"
    write(src, b"fallback")
    dst = tmp_path / "out" / "c.txt"

    def fake_move(src_path, dst_path):
        raise shutil.Error("simulated rename across devices")

    called = {"count": 0}

    def wrapper(src_path, dst_path):
        called["count"] += 1
        return fake_move(src_path, dst_path)

    monkeypatch.setattr(shutil, "move", wrapper)

    io_ops.do_move_or_copy(src, dst, mode="move", dry_run=False)

    # ensure the fallback happened: destination has bytes, source gone, and move was attempted
    assert called["count"] == 1
    assert dst.exists() and dst.read_bytes() == b"fallback"
    assert not src.exists()
