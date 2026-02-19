# io_ops.py
from pathlib import Path
import shutil
import re

# Quality order for comparison (higher index = higher quality)
QUALITY_ORDER = ["Other", "480p", "576p", "720p", "1080p", "2160p", "4320p"]

def _extract_quality_from_name(name: str) -> str:
    """Extract quality from filename, returns 'Other' if not found."""
    quality_match = re.search(r"\((\d+p|Other)\)", name)
    if quality_match:
        return quality_match.group(1)
    return "Other"

def _compare_quality(q1: str, q2: str) -> int:
    """Compare two qualities. Returns 1 if q1 > q2, -1 if q1 < q2, 0 if equal."""
    try:
        idx1 = QUALITY_ORDER.index(q1) if q1 in QUALITY_ORDER else 0
        idx2 = QUALITY_ORDER.index(q2) if q2 in QUALITY_ORDER else 0
        if idx1 > idx2:
            return 1
        elif idx1 < idx2:
            return -1
        return 0
    except (ValueError, AttributeError):
        return 0

def safe_path(path: Path, quality: str = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    
    # If quality info provided and existing file has lower quality, try to replace
    if quality:
        existing_quality = _extract_quality_from_name(path.name)
        if _compare_quality(quality, existing_quality) > 0:
            # New file has higher quality, append quality indicator to differentiate
            # This allows both files to coexist, with user deciding which to keep
            quality_suffix = f" [{quality}]"
            if quality_suffix not in stem:
                new_stem = f"{stem}{quality_suffix}"
                cand = path.with_name(f"{new_stem}{suf}")
                if not cand.exists():
                    return cand
    
    # Default behavior: append number
    i = 2
    while True:
        cand = path.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
        i += 1

def do_move_or_copy(src: Path, dst: Path, mode: str, dry_run: bool, quality: str = None):
    dst = safe_path(dst, quality)
    action = "COPY" if mode == "copy" else "MOVE"
    print(f"{action}: {src} -> {dst}")
    if dry_run:
        return
    if mode == "copy":
        shutil.copy2(src, dst)
    else:
        try:
            shutil.move(str(src), str(dst))
        except shutil.Error:
            shutil.copy2(src, dst)
            src.unlink(missing_ok=True)
