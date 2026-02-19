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
    GENERIC_COLLECTION_DIRS,
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
    def hyphenated_titlecase(w: str) -> str:
        """Capitalize each segment of a hyphenated word."""
        if w.isupper() and len(w) <= 4:
            return w
        return "-".join(part.capitalize() for part in w.split("-"))
    
    return " ".join(hyphenated_titlecase(w) for w in s.split())

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
    re.compile(r"(?i)\bseason[\.\s_\-]+(?P<season>\d{1,2})[\.\s_\-]+episode[\.\s_\-]+(?P<ep1>\d{1,3})(?:[\.\s_\-]*[-&/]*(?P<ep2>\d{1,3}))?"),
    re.compile(r"(?i)(?:^|[\.\s_\-]+)Ep\s+(?P<ep1>\d{1,3})(?:[\.\s_\-]*[-&/]*(?P<ep2>\d{1,3}))?"),
]

def _clean_title(s: str) -> str:
    # Title-safe cleaning (don’t strip leading numbers or scene words)
    s = re.sub(r"\[.*?\]", " ", s)
    s = re.sub(r"[\._]+", " ", s)
    s = RESOLUTION_PATTERN.sub("", s)
    # Normalize hyphens to spaces for series names (e.g., "young-sheldon" -> "young sheldon")
    s = re.sub(r"-+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" .-_")
    # Normalize case using titlecase_soft for consistent capitalization
    return titlecase_soft(s) if s else s

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

def is_tv_episode(filename: str, path: Optional[Path] = None) -> tuple[bool, dict]:
    """
    Returns (True, {"series": str, "season": int, "ep1": int, "ep2": Optional[int]})
    by finding the RIGHTMOST valid episode token and using the prefix as the series.
    
    For "Ep XX" patterns without season info, tries to extract season from parent directory.
    """
    stem = Path(filename).stem
    best = None
    best_pos = -1
    best_pattern_idx = -1
    for idx, pat in enumerate(_PATTERNS):
        for m in pat.finditer(stem):
            if m.start() > best_pos:
                best, best_pos, best_pattern_idx = m, m.start(), idx
    if not best:
        return False, {}
    
    series_raw = stem[:best.start()]
    series_raw = re.sub(r"[\.\s_\-]+$", "", series_raw)  # trim trailing separators
    series = _clean_title(series_raw)
    gd = best.groupdict()
    
    # Handle "Ep XX" pattern (no season in pattern)
    if "season" not in gd or gd["season"] is None:
        season = None
        # Try to extract season from parent directory
        if path is None:
            path = Path(filename)
        parent_name = path.parent.name if path.parent else ""
        # Look for "Season X", "SXX", "Season XX" patterns in parent directory
        season_match = re.search(r"(?i)(?:season\s*)?S?(?P<season>\d{1,2})", parent_name)
        if season_match:
            season = int(season_match.group("season"))
        else:
            # Try to extract from series name if it contains season info
            series_season_match = re.search(r"(?i)\bS(?P<season>\d{1,2})\b", series_raw)
            if series_season_match:
                season = int(series_season_match.group("season"))
        
        if season is None:
            # Default to season 1 if we can't find it
            season = 1
        
        # If series is empty (Ep at start), try to extract from parent directory
        if not series and path and path.parent:
            # Try parent directory name, removing season/quality info
            parent_series = parent_name
            # Remove season patterns (S01, Season 1, etc.)
            parent_series = re.sub(r"(?i)\s*S\d{1,2}\s*", " ", parent_series)
            parent_series = re.sub(r"(?i)\s*Season\s*\d{1,2}\s*", " ", parent_series)
            # Remove quality/resolution patterns
            parent_series = re.sub(r"(?i)\s*\d+p\s*", " ", parent_series)
            parent_series = re.sub(r"(?i)\s*Complete\s*", " ", parent_series)
            # Remove language/codec info (ENG-ITA, x264, etc.)
            parent_series = re.sub(r"(?i)\s*(?:ENG|ITA|ENG-ITA|x264|x265|BluRay|Blu-Ray)\s*", " ", parent_series)
            # Remove release group patterns (everything after last dash if it looks like a release group)
            parent_series = re.sub(r"\s*-\s*[A-Z][a-z]+.*$", "", parent_series)
            # Clean up
            parent_series = re.sub(r"[\.\s_\-]+", " ", parent_series).strip()
            if parent_series:
                series = _clean_title(parent_series)
    else:
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
        
        # Strip scene words from candidate (e.g. "DVDRip", "XviD") and normalize
        candidate = SCENE_WORDS.sub(" ", candidate)
        # Replace dots/underscores with spaces (like clean_name does)
        candidate = re.sub(r"[._]+", " ", candidate)
        # Strip trailing release group patterns (e.g., "-DoNE", "-Larceny")
        candidate = re.sub(r"-[A-Za-z0-9]+\s*$", "", candidate)
        # Normalize spaces and strip separators
        candidate = re.sub(r"\s+", " ", candidate).strip(" .-_")
        if not candidate or len(candidate) < 2:
            continue

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


def title_from_filename_for_generic_parent(path: Path) -> Optional[str]:
    """
    When parent is a generic collection dir (e.g. "Disney Movies"), extract title from filename.
    Handles patterns like:
    - "2001 - Atlantis The Lost Empire.avi" -> "Atlantis The Lost Empire"
    - "01. John Henry (2000).mkv" -> "John Henry"
    """
    stem = path.stem
    
    # Pattern 1: "YEAR - Title" or "YEAR - Title (YEAR)"
    year_title_match = re.match(r"^(?:19|20)\d{2}\s*[-–—]\s*(.+)$", stem)
    if year_title_match:
        title_part = year_title_match.group(1)
        # Strip trailing (YEAR) if present
        title_part = re.sub(r"\s*\(\d{4}\)\s*$", "", title_part)
        # Normalize spaces/dots
        title_part = re.sub(r"[._]+", " ", title_part)
        title_part = re.sub(r"\s+", " ", title_part).strip()
        if title_part:
            return titlecase_soft(title_part)
    
    # Pattern 2: "NN. Title" or "NN. Title (YEAR)"
    index_title_match = re.match(r"^\d{1,3}[.\s)\-]+\s*(.+?)(?:\s*\(\d{4}\))?\s*$", stem)
    if index_title_match:
        title_part = index_title_match.group(1)
        # Normalize spaces/dots
        title_part = re.sub(r"[._]+", " ", title_part)
        title_part = re.sub(r"\s+", " ", title_part).strip()
        if title_part:
            return titlecase_soft(title_part)
    
    return None


def guess_movie_name(path: Path, src_root=Path) -> tuple[str, Path|None]:
    used_nfo = find_nfo(path)
    if used_nfo:
        t = parse_local_nfo_for_title(used_nfo)
        if t: return t, used_nfo
    
    # If parent is a generic collection dir, try extracting title from filename
    if path.parent and path.parent.name.lower() in GENERIC_COLLECTION_DIRS:
        title_from_file = title_from_filename_for_generic_parent(path)
        if title_from_file:
            return title_from_file, used_nfo
    
    by_parent = movie_name_from_parents(path, src_root=src_root)
    if by_parent: return by_parent, used_nfo
    return guess_movie_name_from_file(path.stem), used_nfo