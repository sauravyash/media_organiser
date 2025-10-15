import builtins
import importlib

from media_organiser.posters import carry_poster_with_sieve

from pathlib import Path
import types
import pytest
import media_organiser.posters as posters

# noinspection PyBroadException
try:
    from PIL import Image
    PIL_OK = True
except Exception:
    PIL_OK = False

def _mover(src, dst, mode, dry_run):
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())

@pytest.mark.skipif(not PIL_OK, reason="Pillow not installed")
def test_poster_quarantine_on_keyword(tmp_path):
    # Create a fake poster with a blacklist keyword in filename
    src_dir = tmp_path / "in"
    dst_dir = tmp_path / "movies" / "Film"
    src_dir.mkdir(parents=True, exist_ok=True)
    dst_dir.mkdir(parents=True, exist_ok=True)

    p = src_dir / "poster.yify.jpg"
    # small valid image
    Image.new("RGB", (800, 1200)).save(p)

    carry_poster_with_sieve(
        src_context=p, dst_dir=dst_dir, policy="quarantine",
        min_w=600, min_h=900, aspect_lo=0.66, aspect_hi=0.75,
        bad_words=["yify","yts"], mover=_mover, mode="copy", dry_run=False
    )
    assert (dst_dir / "_quarantine" / p.name).exists()


def _make_image(path: Path, size=(600,900)):
    try:
        from PIL import Image
    except Exception:
        pytest.skip("Pillow not installed")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size).save(path)

def _patch_no_pillow(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        # Break any attempt to import PIL.*
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no pillow")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # re-run module import paths under "no pillow"
    return importlib.reload(posters)


def test_carry_poster_keep_path_without_pillow(tmp_path, monkeypatch):
    posters_no_pil = _patch_no_pillow(monkeypatch)

    # Arrange: src file exists
    src_dir = tmp_path / "src"; src_dir.mkdir()
    dst_dir = tmp_path / "dst"; dst_dir.mkdir()
    src_file = src_dir / "poster.jpg"
    src_file.write_bytes(b"\xff\xd8\xff")  # doesn't matter; helpers won't read it without PIL

    # Stub mover that records calls but does nothing (dry run anyway)
    calls = []
    def mover_stub(src, dst, mode, dry_run):
        calls.append((src, dst, mode, dry_run))

    # Act: policy=keep; no bad words; dry_run=True
    posters_no_pil.carry_poster_with_sieve(
        src_context=src_file,
        dst_dir=dst_dir,
        policy="keep",
        min_w=400, min_h=600,
        aspect_lo=0.60, aspect_hi=0.80,   # ~2:3 band
        bad_words=[],
        mover=mover_stub,
        mode="copy",
        dry_run=True,
    )

    # Assert: without PIL, image checks are skipped -> not suspect -> kept
    # We should have exactly one call to mover, with dst in dst_dir root.
    assert len(calls) == 2
    src, dst, mode, dry = calls[0]
    assert src == src_file
    assert dst == dst_dir / src_file.name
    assert mode == "copy" and dry is True


def test_carry_poster_quarantine_on_keyword_without_pillow(tmp_path, monkeypatch):
    posters_no_pil = _patch_no_pillow(monkeypatch)

    src_dir = tmp_path / "src"; src_dir.mkdir()
    dst_dir = tmp_path / "dst"; dst_dir.mkdir()
    src_file = src_dir / "YIFY_poster.png"
    src_file.write_bytes(b"\x89PNG\r\n")  # bytes are irrelevant here

    calls = []
    def mover_stub(src, dst, mode, dry_run):
        calls.append((src, dst, mode, dry_run))

    posters_no_pil.carry_poster_with_sieve(
        src_context=src_file,
        dst_dir=dst_dir,
        policy="quarantine",
        min_w=400, min_h=600,
        aspect_lo=0.60, aspect_hi=0.80,
        bad_words=["yify", "yts"],
        mover=mover_stub,
        mode="copy",
        dry_run=True,
    )

    # Assert: keyword triggers suspect -> quarantine to _quarantine/
    assert len(calls) == 1
    _, dst, _, dry = calls[0]
    assert dst.parent == dst_dir / "_quarantine"
    assert dst.name == src_file.name
    assert dry is True