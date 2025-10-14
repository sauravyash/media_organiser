import pytest
from media_organiser.posters import carry_poster_with_sieve

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
