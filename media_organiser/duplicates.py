from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

import hashlib

from .constants import RESOLUTION_PATTERN, VIDEO_EXTS
from .naming import clean_name


def iter_library_video_files(movies_root: Path, tv_root: Path) -> Iterator[Path]:
    for root in (movies_root, tv_root):
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                yield p


class LibraryImportDupIndex:
    """
    Precomputed index of video files already under movies/ and tv/ for matching imports.
    Rules match is_duplicate_in_dir per --dupe-mode: name (normalized stem), size (file size only),
    hash (size + sampled MD5 fingerprint).
    """

    __slots__ = ("mode", "_by_name", "_by_size", "_by_size_fp")

    def __init__(
        self,
        mode: str,
        by_name: Dict[str, Path],
        by_size: Dict[int, Path],
        by_size_fp: Dict[Tuple[int, str], Path],
    ) -> None:
        self.mode = mode
        self._by_name = by_name
        self._by_size = by_size
        self._by_size_fp = by_size_fp

    def find_duplicate(self, candidate: Path) -> Optional[Path]:
        if self.mode == "name":
            return self._by_name.get(normalized_stem_ignore_quality(candidate))
        try:
            cand_size = candidate.stat().st_size
        except OSError:
            return None
        if self.mode == "size":
            return self._by_size.get(cand_size)
        cand_fp = quick_fingerprint(candidate)
        return self._by_size_fp.get((cand_size, cand_fp[1]))


def build_library_import_dup_index(movies_root: Path, tv_root: Path, mode: str) -> Optional[LibraryImportDupIndex]:
    if mode == "off":
        return None
    by_name: Dict[str, Path] = {}
    by_size: Dict[int, Path] = {}
    by_size_fp: Dict[Tuple[int, str], Path] = {}
    for p in iter_library_video_files(movies_root, tv_root):
        try:
            norm = normalized_stem_ignore_quality(p)
            sz = p.stat().st_size
        except (OSError, FileNotFoundError):
            continue
        if mode == "name":
            by_name.setdefault(norm, p)
        elif mode == "size":
            by_size.setdefault(sz, p)
        elif mode == "hash":
            try:
                fp = quick_fingerprint(p)
            except OSError:
                continue
            by_size_fp.setdefault((sz, fp[1]), p)
    return LibraryImportDupIndex(mode, by_name, by_size, by_size_fp)

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
