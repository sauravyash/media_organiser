from pathlib import Path
from typing import Optional, Tuple
import re
from .constants import (
    YEAR_PATTERN,
    MOVIE_DIR_RE,
    SCENE_WORDS,
    GENERIC_DIRS
)


def detect_quality(filename: str) -> str:
    m = RESOLUTION_PATTERN.search(filename)
    if not m:
        return "Other"
    res = m.group(1).lower()
    if res in ["4k", "uhd"]: return "2160p"
    if res == "8k": return "4320p"
    return res


def titlecase_soft(s: str) -> str:
    return " ".join(w if (w.isupper() and len(w) <= 4) else w.capitalize() for w in s.split())

RESOLUTION_PATTERN = re.compile(
    r"(?i)[\[\(\{]?\s*(480p|576p|720p|1080p|2160p|4320p|4k|8k|uhd|hdr)\s*[\]\)\}]?"
)

def clean_name(raw: str, *, strip_leading_index: bool = True, strip_scene_words: bool = True) -> str:
    name = Path(raw).stem
    name = re.sub(r"\[.*?\]", " ", name)    # [tags]
    name = re.sub(r"[\._]+", " ", name)     # dots/underscores -> space
    if strip_scene_words:
        name = SCENE_WORDS.sub("", name)                # scene words

    print(name)
    name = RESOLUTION_PATTERN.sub("", name)             # 1080p, 2160p, etc.

    print(name)

    if strip_leading_index:
        name = re.sub(r"^\s*\d{1,3}[\s\.\-\)]*", "", name)  # leading numeric index
    name = re.sub(r"\s{2,}", " ", name).strip(" .-_")
    return name

_PATTERNS = [
    re.compile(r"(?i)[\.\s_\-]+S(?P<season>\d{1,2})[\.\s_\-]*E(?P<ep1>\d{1,3})(?:[\.\s_\-]*[-&/]*E?(?P<ep2>\d{1,3}))?"),
    re.compile(r"(?i)[\.\s_\-]+(?P<season>\d{1,2})x(?P<ep1>\d{1,3})(?:[\.\s_\-]*[-&/]*(?P<ep2>\d{1,3}))?"),
    re.compile(r"(?i)[\.\s_\-]+S(?P<season>\d{1,2})[\.\s_\-]+(?P<ep1>\d{1,3})(?:[\.\s_\-]*[-&/]+(?P<ep2>\d{1,3}))?"),
    re.compile(r"(?i)[\.\s_\-]+(?P<season>\d{1,2})[\.\s_\-]*E(?P<ep1>\d{1,3})"),
]

def _clean_title(s: str) -> str:
    # Title-safe cleaning (don’t strip leading numbers or scene words)
    s = re.sub(r"\[.*?\]", " ", s)
    s = re.sub(r"[\._]+", " ", s)
    s = RESOLUTION_PATTERN.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" .-_")
    return s

def is_tv_episode(filename: str) -> tuple[bool, dict]:
    """
    Returns (True, {"series": str, "season": int, "ep1": int, "ep2": Optional[int]})
    by finding the RIGHTMOST valid episode token and using the prefix as the series.
    """
    stem = Path(filename).stem
    best = None
    best_pos = -1
    for pat in _PATTERNS:
        for m in pat.finditer(stem):
            if m.start() > best_pos:
                best, best_pos = m, m.start()
    if not best:
        return False, {}
    series_raw = stem[:best.start()]
    series_raw = re.sub(r"[\.\s_\-]+$", "", series_raw)  # trim trailing separators
    series = _clean_title(series_raw)
    gd = best.groupdict()
    season = int(gd["season"])
    ep1 = int(gd["ep1"])
    ep2 = int(gd["ep2"]) if gd.get("ep2") else None
    return True, {"series": series, "season": season, "ep1": ep1, "ep2": ep2}

def movie_name_from_parents(path: Path) -> Optional[str]:
    for p in [path.parent, path.parent.parent]:
        if not isinstance(p, Path): continue
        name = p.name.strip()
        if name.lower() in GENERIC_DIRS: continue
        m = MOVIE_DIR_RE.match(name)
        if m:
            return titlecase_soft(clean_name(m.group("title")))
    return None

def guess_movie_name_from_file(stem: str) -> str:
    name = clean_name(stem)
    parts = name.split()
    for i, p in enumerate(parts):
        if YEAR_PATTERN.fullmatch(p):
            name = " ".join(parts[:i]).strip()
            break
    return titlecase_soft(name) if name else titlecase_soft(clean_name(stem))
