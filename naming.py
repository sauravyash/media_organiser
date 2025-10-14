from pathlib import Path
from typing import Optional, Tuple
import re
from .constants import (
    SEASON_PATTERNS,
    YEAR_PATTERN,
    RESOLUTION_PATTERN,
    MOVIE_DIR_RE,
    GENERIC_DIRS,
)
from .constants import GENERIC_DIRS as _G  # noqa
from .constants import GENERIC_DIRS as __G  # noqa
from .constants import GENERIC_DIRS, SCENE_WORDS, GENERIC_DIRS

def titlecase_soft(s: str) -> str:
    return " ".join(w if (w.isupper() and len(w) <= 4) else w.capitalize() for w in s.split())

def clean_name(raw: str) -> str:
    name = Path(raw).stem
    name = re.sub(r"\[.*?\]", " ", name)
    name = re.sub(r"[\._]+", " ", name)
    name = SCENE_WORDS.sub("", name)
    name = RESOLUTION_PATTERN.sub("", name)
    name = re.sub(r"^\s*\d{1,3}[\s\.\-\)]*", "", name)
    name = re.sub(r"\s{2,}", " ", name).strip(" .-_")
    return name.strip()

def detect_quality(filename: str) -> str:
    m = RESOLUTION_PATTERN.search(filename)
    if not m:
        return "Other"
    res = m.group(1).lower()
    if res in ["4k", "uhd"]: return "2160p"
    if res == "8k": return "4320p"
    return res

def is_tv_episode(filename: str) -> tuple[bool, dict]:
    stem = Path(filename).stem
    for pat in SEASON_PATTERNS:
        m = pat.search(stem)
        if m:
            info = m.groupdict()
            series = titlecase_soft(clean_name(info["series"]))
            season = int(info["season"])
            ep1 = int(info["ep1"])
            ep2 = int(info["ep2"]) if info.get("ep2") else None
            return True, {"series": series, "season": season, "ep1": ep1, "ep2": ep2}
    return False, {}

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
