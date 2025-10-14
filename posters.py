from pathlib import Path
from typing import List
from .constants import POSTER_NAMES
import shutil

def parse_range_pair(s: str, sep: str, cast=float) -> tuple:
    lo, hi = s.split(sep, 1); return cast(lo), cast(hi)

def _read_exif_strings(p: Path) -> List[str]:
    try:
        from PIL import Image
        with Image.open(p) as im:
            strings = []
            exif = im.getexif()
            if exif:
                for _, v in exif.items():
                    try:
                        if isinstance(v, bytes): v = v.decode(errors="ignore")
                        if isinstance(v, str): strings.append(v.lower())
                    except Exception: pass
            for _, v in (getattr(im, "info", {}) or {}).items():
                if isinstance(v, str): strings.append(v.lower())
            return strings
    except Exception:
        return []

def _image_basic_checks(p: Path) -> dict:
    info = {"width": None, "height": None, "aspect": None, "border_ratio": None}
    try:
        from PIL import Image, ImageStat
        with Image.open(p) as im:
            w, h = im.size
            info["width"], info["height"] = w, h
            info["aspect"] = w / h if h else None
            frame = max(2, min(w, h)//100)
            left  = im.crop((0, 0, frame, h))
            right = im.crop((w-frame, 0, w, h))
            top   = im.crop((0, 0, w, frame))
            bot   = im.crop((0, h-frame, w, h))
            strips = [left, right, top, bot]
            vals = []
            for s in strips:
                st = ImageStat.Stat(s.convert("L"))
                vals.append(st.stddev[0] if st.stddev else 0.0)
            solid = sum(1 for v in vals if v < 2.0)
            info["border_ratio"] = solid / 4.0
    except Exception:
        pass
    return info

def is_suspect_poster(p: Path, min_w: int, min_h: int, aspect_lo: float, aspect_hi: float, bad_words: List[str]) -> tuple[bool, str]:
    name = p.name.lower()
    for kw in bad_words:
        if kw and kw in name:
            return True, f"keyword:{kw}"
    info = _image_basic_checks(p)
    w, h, aspect, border = info["width"], info["height"], info["aspect"], info["border_ratio"]
    if w and h:
        if w < min_w or h < min_h:
            return True, f"small:{w}x{h}"
        if aspect and not (aspect_lo <= aspect <= aspect_hi):
            return True, f"aspect:{aspect:.2f}"
    for s in _read_exif_strings(p):
        for kw in bad_words:
            if kw and kw in s:
                return True, f"exif:{kw}"
    if border is not None and border >= 0.75:
        return True, f"border:{border:.2f}"
    return False, ""

def carry_poster_with_sieve(src_context: Path, dst_dir: Path, policy: str,
                            min_w: int, min_h: int, aspect_lo: float, aspect_hi: float, bad_words: List[str],
                            mover, mode: str, dry_run: bool):
    if policy in ("off", "skip"):
        return
    candidates = []
    for base in (src_context.parent, src_context.parent.parent):
        if base and base.exists():
            for nm in POSTER_NAMES:
                p = base / nm
                if p.exists():
                    candidates.append(p)
    for src in candidates:
        suspect, reason = is_suspect_poster(src, min_w, min_h, aspect_lo, aspect_hi, bad_words)
        dst = dst_dir / src.name
        if suspect and policy == "keep":
            print(f"POSTER SKIP (suspect:{reason}): {src}")
            continue
        if suspect and policy == "quarantine":
            qdir = dst_dir / "_quarantine"; qdir.mkdir(parents=True, exist_ok=True)
            dst = qdir / src.name
            print(f"POSTER QUARANTINE ({reason}): {src} -> {dst}")
        else:
            print(f"POSTER KEEP: {src} -> {dst}")
        mover(src, dst, mode, dry_run)
