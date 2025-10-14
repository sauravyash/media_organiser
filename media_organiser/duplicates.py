from pathlib import Path
from typing import Optional, Tuple
from .constants import VIDEO_EXTS
from .naming import clean_name
from .constants import RESOLUTION_PATTERN
import hashlib

def normalized_stem_ignore_quality(p: Path) -> str:
    s = clean_name(p.stem)
    s = RESOLUTION_PATTERN.sub("", s)
    return s.strip()

def quick_fingerprint(p: Path, sample_bytes: int = 1<<20) -> tuple[int, str]:
    size = p.stat().st_size
    h = hashlib.md5()
    with p.open("rb") as f:
        if size <= 2 * sample_bytes:
            h.update(f.read())
        else:
            h.update(f.read(sample_bytes))
            f.seek(max(0, size - sample_bytes))
            h.update(f.read(sample_bytes))
    return size, h.hexdigest()

def is_duplicate_in_dir(candidate: Path, dest_dir: Path, mode: str = "hash") -> Optional[Path]:
    if mode == "off":
        return None
    cand_norm = normalized_stem_ignore_quality(candidate)
    cand_size = candidate.stat().st_size
    cand_fp: Optional[Tuple[int, str]] = None
    for existing in dest_dir.glob("*"):
        if not existing.is_file() or existing.suffix.lower() not in VIDEO_EXTS:
            continue
        try:
            if mode == "name":
                if normalized_stem_ignore_quality(existing) == cand_norm:
                    return existing
            elif mode == "size":
                if existing.stat().st_size == cand_size:
                    return existing
            elif mode == "hash":
                if existing.stat().st_size != cand_size:
                    continue
                if cand_fp is None:
                    cand_fp = quick_fingerprint(candidate)
                if quick_fingerprint(existing) == cand_fp:
                    return existing
        except FileNotFoundError:
            continue
    return None
