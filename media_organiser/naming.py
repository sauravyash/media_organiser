from collections import Counter
from pathlib import Path
from typing import Optional
import re

from .constants import RESOLUTION_WITH_BRACKETS_PATTERN, RESOLUTION_PATTERN
from .nfo import find_nfo, parse_local_nfo_for_title
from .constants import (
    YEAR_PATTERN,
    MOVIE_DIR_RE,
    SCENE_WORDS,
    GENERIC_DIRS,
    MOVIE_PART_RE,
)


def detect_quality(filename: str) -> str:
    m = RESOLUTION_PATTERN.search(filename)
    if not m:
        return "Other"
    res = m.group(1).lower()
    if res in ["4k", "uhd"]: return "2160p"
    if res == "8k": return "4320p"
    if res == "hd": return "720p"
    if res == "fhd": return "1080p"
    return res


def titlecase_soft(s: str) -> str:
    return " ".join(w if (w.isupper() and len(w) <= 4) else w.capitalize() for w in s.split())

def clean_name(raw: str, *, strip_leading_index: bool = True, strip_scene_words: bool = True) -> str:
    name = Path(raw).stem
    name = re.sub(r"\[.*?\]", " ", name)    # [tags]
    name = re.sub(r"[\._]+", " ", name)     # dots/underscores -> space
    if strip_scene_words:
        name = SCENE_WORDS.sub("", name)                # scene words

    name = RESOLUTION_WITH_BRACKETS_PATTERN.sub("", name)             # 1080p, 2160p, etc.


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

def find_separator(text: str) -> str | None:
    """
    Find the most likely word separator in a string.
    Common separators include: space, comma, tab, semicolon, etc.
    Returns the separator character or None if none found.
    """
    # Define common separators
    separators = [' ', '\t', ',', ';', '|', '-', '_', ':', '.']

    # Count occurrences of each separator
    counts = Counter({sep: text.count(sep) for sep in separators})

    # Get the separator with the highest count
    most_common_sep, count = counts.most_common(1)[0]

    # Return the separator if found
    return most_common_sep if count > 0 else None

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

def movie_name_from_parents(path: Path, src_root=Path) -> Optional[str]:
    """
    Try extracting a movie title from the immediate parent or grandparent directory.
    Mirrors the tokenization/year-truncation/resolution logic in guess_movie_name_from_file,
    but returns a plain title (no file suffix). Resolution is appended in parentheses if found.

    Example:
        /Movies/The.Matrix.1999.1080p/ -> "The Matrix (1080p)"
        /Movies/Inception (2010)/      -> "Inception"
    """
    # Prefer closer parent first, then grandparent
    for p in (getattr(path, "parent", None), getattr(getattr(path, "parent", None), "parent", None)):
        if not isinstance(p, Path):
            continue

        raw = (p.name or "").strip()
        if not raw or raw.lower() in GENERIC_DIRS:
            continue

        if p.name == src_root.name:
            break

        # If your directory regex exposes a "title" group, start from that; else use the raw name
        m = MOVIE_DIR_RE.match(raw) if "MOVIE_DIR_RE" in globals() else None
        candidate = m.group("title") if m and "title" in m.groupdict() else raw

        # Tokenize like the file-based function
        sep = find_separator(candidate) or " "
        tokens = [t for t in candidate.split(sep=sep) if t]

        # Separate resolution-ish tokens from title tokens
        res_tokens, title_tokens = [], []
        for tok in tokens:
            if RESOLUTION_PATTERN.fullmatch(tok):
                res_tokens.append(tok)
            else:
                title_tokens.append(tok)


        # Prefer specific numeric resolutions over generic tags
        preferred_order = ["4320p", "8k", "2160p", "4k", "1080p", "720p", "576p", "480p"]
        resolution = None
        if res_tokens:
            lower_set = {t.lower() for t in res_tokens}
            for cand in preferred_order:
                if cand in lower_set:
                    resolution = cand
                    break
            if resolution is None:
                resolution = next((t.lower() for t in res_tokens), None)

        # Truncate title at the first year token (e.g., 1999, 2010)

        for i, tok in enumerate(title_tokens):
            if YEAR_PATTERN.fullmatch(tok):
                title_tokens = title_tokens[:i]
                break

        # Build base title
        base = titlecase_soft(" ".join(title_tokens).strip())
        if not base:
            continue

        return base

    return None


def movie_part_suffix(path: Path) -> str:
    """
    If the path (filename stem or parent dir) indicates a multi-part movie (CD1, CD2, Part1, etc.),
    return a suffix like ' CD1' or ' CD 2' for the output filename; otherwise return ''.
    """
    # Check filename stem first (e.g. Shrek.DVDRip.XviD.CD1-BELiAL)
    m = MOVIE_PART_RE.search(path.stem)
    if m:
        num = m.group(1) or m.group(2) or m.group(3)
        return f" CD {num}"
    # Check parent dir (e.g. .../CD 1/Shrek.avi)
    if path.parent and path.parent.name:
        m = MOVIE_PART_RE.search(path.parent.name)
        if m:
            num = m.group(1) or m.group(2) or m.group(3)
            return f" CD {num}"
    return ""


def guess_movie_name_from_file(filename: str) -> str:
    p = Path(filename)
    suffix = p.suffix  # e.g. ".mp4"
    sep = find_separator(p.stem) or " "
    tokens = p.stem.split(sep=sep)
    # separate resolution-ish tokens from title tokens
    res_tokens, title_tokens = [], []
    for tok in tokens:
        if RESOLUTION_PATTERN.fullmatch(tok):
            res_tokens.append(tok)
        else:
            title_tokens.append(tok)

    # prefer a specific numeric resolution over generic tags like "hd"/"uhd"/"fhd"/"hdr"
    # also normalize to lowercase like "720p" to match your test
    preferred_order = ["4320p", "8k", "2160p", "4k", "1080p", "720p", "576p", "480p"]
    resolution = None
    if res_tokens:
        lower_set = {t.lower() for t in res_tokens}
        for cand in preferred_order:
            if cand in lower_set:
                # normalize '4k'/'8k' to '2160p'/'4320p' if you prefer; here we keep as-is
                resolution = cand
                break
        # if only generic tags present (hd/uhd/fhd/hdr), pick the first and lowercase it
        if resolution is None:
            resolution = next((t.lower() for t in res_tokens), None)

    # truncate title at first year token if present
    for i, tok in enumerate(title_tokens):
        if YEAR_PATTERN.fullmatch(tok):
            title_tokens = title_tokens[:i]
            break

    base = titlecase_soft(" ".join(title_tokens).strip())
    return f"{base}"

def guess_movie_name(path: Path, src_root=Path) -> tuple[str, Path|None]:
    used_nfo = find_nfo(path)
    if used_nfo:
        t = parse_local_nfo_for_title(used_nfo)
        if t: return t, used_nfo
    by_parent = movie_name_from_parents(path, src_root=src_root)
    if by_parent: return by_parent, used_nfo
    return guess_movie_name_from_file(path.stem), used_nfo