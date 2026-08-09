"""Read-only audit of the organised movie library (the destination side).

The CLI writes movies as::

    <dest>/movies/<Title>/<Title> (<Year>) [<Quality>].<ext>

This module walks that tree, reads the NFOs already written next to each file,
and reports where reality drifts from the convention — mislabels carried over
from scene releases, TV packs that landed in ``/movies``, missing years,
unreadable NFOs and so on. It never touches the files: every finding is an
:class:`~media_organiser.audit.Issue` for the user to verify manually.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .audit import (
    Issue,
    VERB_RENAME_FILE,
    VERB_RENAME_FOLDER,
    VERB_WRITE_NFO,
    sort_issues,
    summarise,
    worst_severity,
)
from .constants import (
    POSTER_NAMES,
    RESOLUTION_PATTERN,
    RESOLUTION_WITH_BRACKETS_PATTERN,
    SCENE_WORDS,
    SUB_EXTS,
    VIDEO_EXTS,
)
from .naming import (
    detect_quality,
    is_tv_episode,
    movie_part_suffix,
    normalise_movie_title_for_display,
    guess_year_for_movie,
    titlecase_soft,
)
from .nfo import read_nfo_to_meta

# Release-site and tracker branding that survives scene naming.
RELEASE_SITE_RE = re.compile(
    r"(?i)\b(yts(?:\s*[.\-]\s*(?:mx|am|ag|lt|re))?|yify|rarbg|ettv|eztv|1337x|"
    r"torrentz?|axxo|fxg|publichd|dvdscr|hdcam|telesync|shaanig|tamilrockers)\b"
)
_BRACKET_BLOCK_RE = re.compile(r"[\[\{][^\]\}]*[\]\}]")
_LEADING_INDEX_RE = re.compile(r"^\s*\d{1,3}\s*[.\-–—)]\s+")
# Season packs that no episode regex catches, e.g. "Breaking Bad S01 Complete".
_SEASON_PACK_RE = re.compile(
    r"(?i)(?:\bS\d{1,2}\b[^/\\]*\bcomplete\b|\bcomplete\b[^/\\]*\bS\d{1,2}\b|\bseason\s*\d{1,2}\b)"
)
_PAREN_YEAR_RE = re.compile(r"\(((?:19|20)\d{2})\)")
_TRAILING_YEAR_RE = re.compile(r"\s*\(\s*(?:19|20)\d{2}\s*\)\s*$")

# Folders the organiser itself creates but that never hold a movie directly.
_IGNORED_DIR_NAMES = {"subs", "subtitles", "sample", "extras", "featurettes"}


def get_library_dir() -> Path:
    """Destination library root. Mirrors ``LIB_DIR`` from ``entrypoint.sh``."""
    path = Path(os.environ.get("LIB_DIR", "./data/library")).expanduser()
    return path.resolve()


def get_movies_dir() -> Path:
    """The ``movies`` subtree of the library, honouring ``MOVIES_DIR`` if set."""
    override = os.environ.get("MOVIES_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return get_library_dir() / "movies"


def title_key(name: str) -> str:
    """Normalised key for spotting two folders that mean the same movie."""
    s = normalise_movie_title_for_display(name).lower()
    s = re.sub(r"^(the|a|an)\s+", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def suggest_clean_title(raw: str) -> str:
    """Best-effort clean title for a messy folder name (the rename target)."""
    s = _BRACKET_BLOCK_RE.sub(" ", raw)
    s = RELEASE_SITE_RE.sub(" ", s)
    s = SCENE_WORDS.sub(" ", s)
    s = RESOLUTION_WITH_BRACKETS_PATTERN.sub(" ", s)
    s = _LEADING_INDEX_RE.sub("", s)
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"\s*[-–—]\s*$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" .-_")
    s = normalise_movie_title_for_display(s)
    return titlecase_soft(s) if s else raw.strip()


def canonical_stem(title: str, year: Optional[str], quality: str, part: str = "") -> str:
    """The filename stem the CLI would produce. Kept in sync with ``cli.main``."""
    return f"{title} {f'({year}) ' if year else ''}[{quality}]{part}"


def _action(verb: str, src: Path, dst: Optional[Path] = None, stat_src: bool = False) -> dict:
    """Describe a change as something :mod:`media_organiser.fixes` can carry out.

    ``size``/``mtime`` are recorded so the fix path can tell whether the file
    moved on under it between the scan and the click that applies the change.
    """
    action = {"verb": verb, "src": str(src), "dst": str(dst) if dst else ""}
    if stat_src:
        try:
            st = src.stat()
            action["size"] = st.st_size
            action["mtime"] = st.st_mtime
        except OSError:
            pass
    return action


def _nfo_for(video: Path) -> Optional[Path]:
    """The NFO belonging to ``video`` under either supported layout."""
    same_stem = video.with_suffix(".nfo")
    if same_stem.exists():
        return same_stem
    kodi = video.parent / "movie.nfo"
    if kodi.exists():
        return kodi
    return None


def _folder_year(folder_name: str) -> Optional[str]:
    m = _PAREN_YEAR_RE.search(folder_name)
    return m.group(1) if m else None


def _looks_like_tv(name: str, path: Path) -> bool:
    is_tv, _info = is_tv_episode(name, path)
    return bool(is_tv) or bool(_SEASON_PACK_RE.search(name))


def _folder_noise(folder_name: str) -> list[str]:
    """Describe what makes a folder name messy; empty list means it's clean."""
    problems: list[str] = []
    if _BRACKET_BLOCK_RE.search(folder_name):
        problems.append("bracketed tags")
    if RELEASE_SITE_RE.search(folder_name):
        problems.append("release-site branding")
    if SCENE_WORDS.search(folder_name):
        problems.append("scene/encoding words")
    if RESOLUTION_PATTERN.search(folder_name):
        problems.append("resolution token")
    if _PAREN_YEAR_RE.search(folder_name):
        problems.append("year (the year belongs in the filename, not the folder)")
    return problems


def _audit_folder(folder: Path, videos: list[Path], leftovers: list[str]) -> list[Issue]:
    """Issues that concern the movie folder as a whole."""
    issues: list[Issue] = []
    name = folder.name

    if not videos:
        issues.append(Issue(
            kind="no-video-file",
            severity="high",
            message="Folder has no video file, only leftovers "
                    f"({', '.join(sorted(leftovers)[:4]) or 'nothing'}).",
            suggestion="Delete the folder or restore the missing video.",
        ))

    # Multi-part movies (CD1/CD2) legitimately hold several files.
    if len(videos) > 1 and not all(movie_part_suffix(v) for v in videos):
        issues.append(Issue(
            kind="multiple-videos",
            severity="high",
            message=f"{len(videos)} video files share one movie folder: "
                    f"{', '.join(sorted(v.name for v in videos)[:4])}.",
            suggestion="Split into one folder per movie, or delete the redundant copies.",
        ))

    if _LEADING_INDEX_RE.search(name):
        issues.append(Issue(
            kind="leading-index",
            severity="medium",
            message=f"Folder name starts with a collection index: {name!r}.",
            suggestion=f"Rename folder to {suggest_clean_title(name)!r}",
        ))

    noise = _folder_noise(name)
    if noise:
        cleaned = suggest_clean_title(name)
        renameable = bool(cleaned) and cleaned != name
        issues.append(Issue(
            kind="messy-folder-name",
            severity="medium",
            message=f"Folder name still carries {', '.join(noise)}.",
            suggestion=(f"Rename folder to {cleaned!r}" if renameable
                        else "Clean the folder name by hand."),
            action=(_action(VERB_RENAME_FOLDER, folder, folder.parent / cleaned)
                    if renameable else None),
        ))

    return issues


def _audit_video(folder: Path, video: Path, nfo: Optional[Path], meta: dict) -> list[Issue]:
    """Issues that concern a single video file and its NFO."""
    issues: list[Issue] = []
    stem = video.stem
    quality = detect_quality(video.name)
    year = guess_year_for_movie(video) or _folder_year(folder.name) or meta.get("year")
    # The name the CLI would derive from the folder as-is, and the name it would
    # derive once the folder is cleaned up. Either is acceptable on disk; the
    # cleaned one is what we suggest renaming to.
    folder_title = normalise_movie_title_for_display(folder.name)
    expected_title = suggest_clean_title(folder.name) or folder_title

    if _looks_like_tv(video.name, video) or _looks_like_tv(folder.name, folder):
        issues.append(Issue(
            kind="tv-episode-in-movies",
            severity="high",
            message=f"{video.name!r} looks like a TV episode or season pack but sits under /movies.",
            suggestion="Move it back to the import folder so it is re-organised into /tv.",
        ))

    if not year:
        issues.append(Issue(
            kind="missing-year",
            severity="medium",
            message=f"No release year in the filename, folder name or NFO for {video.name!r}.",
            suggestion=f"Rename to {canonical_stem(expected_title, '<year>', quality)}{video.suffix}",
        ))
    else:
        try:
            y = int(year)
        except (TypeError, ValueError):
            y = None
        max_year = datetime.now().year + 2
        if y is not None and not (1900 <= y <= max_year):
            issues.append(Issue(
                kind="suspect-year",
                severity="medium",
                message=f"Year {year} is outside 1900–{max_year}; it is probably part of the "
                        f"title rather than a release year (e.g. 'Blade Runner 2049').",
                suggestion="Set the correct release year in the filename and NFO.",
            ))

    if quality == "Other":
        issues.append(Issue(
            kind="unknown-quality",
            severity="low",
            message=f"Quality could not be detected from {video.name!r}; it is filed as [Other].",
            suggestion="Check the actual resolution and re-tag the filename.",
        ))

    if nfo is None:
        issues.append(Issue(
            kind="missing-nfo",
            severity="low",
            message=f"No NFO next to {video.name!r}.",
            suggestion="Re-run the organiser with --emit-nfo all to generate one.",
            action=_action(VERB_WRITE_NFO, video, video.with_suffix(".nfo")),
        ))
    else:
        if not meta:
            issues.append(Issue(
                kind="unreadable-nfo",
                severity="medium",
                message=f"{nfo.name!r} could not be parsed as movie metadata.",
                suggestion="Delete the NFO and re-run the organiser with --overwrite-nfo.",
            ))
        else:
            nfo_title = (meta.get("title") or "").strip()
            if nfo_title and title_key(nfo_title) != title_key(folder.name):
                issues.append(Issue(
                    kind="nfo-title-mismatch",
                    severity="medium",
                    message=f"NFO title {nfo_title!r} does not match folder {folder.name!r}.",
                    suggestion="Decide which title is right, then fix the other.",
                ))

            nfo_quality = (meta.get("quality") or "").strip()
            if nfo_quality and nfo_quality.lower() != quality.lower():
                issues.append(Issue(
                    kind="quality-mismatch",
                    severity="medium",
                    message=f"Filename says [{quality}] but the NFO says {nfo_quality!r}.",
                    suggestion="Re-check the file's real resolution and align both.",
                ))

            recorded = (meta.get("filenameandpath") or "").strip()
            if recorded:
                recorded_name = recorded.replace("\\", "/").rsplit("/", 1)[-1]
                if recorded_name and recorded_name != video.name:
                    issues.append(Issue(
                        kind="stale-nfo-path",
                        severity="low",
                        message=f"NFO still points at {recorded_name!r}; the file is now {video.name!r}.",
                        suggestion="Re-run the organiser with --overwrite-nfo to refresh the NFO.",
                        action=_action(VERB_WRITE_NFO, video, nfo),
                    ))

    part = movie_part_suffix(video)
    expected = canonical_stem(expected_title, year, quality, part)
    accepted = {expected, canonical_stem(folder_title, year, quality, part)}
    if expected_title and stem not in accepted:
        issues.append(Issue(
            kind="filename-mismatch",
            severity="low",
            message=f"Filename does not follow 'Title (Year) [Quality]': {video.name!r}.",
            suggestion=f"Rename to {expected}{video.suffix}",
            # Renamed in place: ``video.parent`` keeps CD1/CD2 subfolders where
            # they are, and a folder rename applied first is remapped over this
            # destination by the fix pipeline.
            action=_action(VERB_RENAME_FILE, video,
                           video.parent / f"{expected}{video.suffix}", stat_src=True),
        ))

    return issues


def scan_movies(movies_root: Optional[Path] = None) -> list[dict]:
    """Scan every movie folder under ``movies_root`` and audit it.

    Returns one serialisable dict per folder, sorted by title. Unreadable
    folders are reported as an issue rather than raising.
    """
    root = Path(movies_root) if movies_root is not None else get_movies_dir()
    entries: list[dict] = []
    if not root.is_dir():
        return entries

    try:
        folders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return entries

    for folder in folders:
        if folder.name.lower() in _IGNORED_DIR_NAMES or folder.name.startswith("."):
            continue

        videos: list[Path] = []
        subtitles: list[str] = []
        posters: list[str] = []
        nfos: list[str] = []
        others: list[Path] = []
        try:
            # rglob so CD1/CD2 subfolders and stray nesting are still seen.
            children = sorted(p for p in folder.rglob("*") if p.is_file())
        except OSError as exc:
            entries.append({
                "folder": folder.name,
                "path": str(folder),
                "title": folder.name,
                "year": None,
                "quality": None,
                "size": 0,
                "videos": [],
                "subtitles": [],
                "posters": [],
                "nfos": [],
                "severity": "high",
                "issues": [Issue(
                    kind="unreadable-folder",
                    severity="high",
                    message=f"Could not read the folder: {exc}",
                    suggestion="Check permissions on the library folder.",
                ).to_dict()],
            })
            continue

        for child in children:
            suffix = child.suffix.lower()
            if suffix in VIDEO_EXTS:
                videos.append(child)
            elif suffix in SUB_EXTS:
                subtitles.append(child.name)
            elif suffix == ".nfo":
                nfos.append(child.name)
            elif child.name.lower() in POSTER_NAMES or suffix in {".jpg", ".jpeg", ".png"}:
                posters.append(child.name)
            else:
                others.append(child)

        leftovers = [p.name for p in others] + nfos + subtitles + posters
        issues = _audit_folder(folder, videos, leftovers)

        video_records = []
        primary_meta: dict = {}
        for video in sorted(videos, key=lambda p: p.name.lower()):
            nfo = _nfo_for(video)
            meta = read_nfo_to_meta(nfo) if nfo else {}
            if meta and not primary_meta:
                primary_meta = meta
            issues.extend(_audit_video(folder, video, nfo, meta))
            try:
                size = video.stat().st_size
            except OSError:
                size = 0
            video_records.append({
                "name": video.name,
                "relpath": str(video.relative_to(folder)).replace("\\", "/"),
                "size": size,
                "quality": detect_quality(video.name),
                "year": guess_year_for_movie(video),
                "nfo": nfo.name if nfo else None,
            })

        issues = sort_issues(issues)
        entries.append({
            "folder": folder.name,
            "path": str(folder),
            "title": primary_meta.get("title") or normalise_movie_title_for_display(folder.name),
            "year": (video_records[0]["year"] if video_records else None) or primary_meta.get("year"),
            "quality": video_records[0]["quality"] if video_records else None,
            "size": sum(v["size"] for v in video_records),
            "videos": video_records,
            "subtitles": sorted(subtitles),
            "posters": sorted(posters),
            "nfos": sorted(nfos),
            "severity": worst_severity(issues),
            "issues": [i.to_dict() for i in issues],
        })

    _flag_duplicate_titles(entries)
    return entries


def _flag_duplicate_titles(entries: list[dict]) -> None:
    """Add a cross-folder duplicate issue where two folders mean the same movie."""
    by_key: dict[str, list[dict]] = {}
    for entry in entries:
        key = title_key(entry["folder"])
        if key:
            by_key.setdefault(key, []).append(entry)

    for group in by_key.values():
        if len(group) < 2:
            continue
        names = sorted(e["folder"] for e in group)
        for entry in group:
            siblings = [n for n in names if n != entry["folder"]]
            issue = Issue(
                kind="duplicate-title",
                severity="high",
                message=f"Same movie appears in {len(group)} folders: {', '.join(names)}.",
                suggestion=f"Merge into one folder and delete {', '.join(repr(s) for s in siblings)}.",
            )
            existing = [Issue(**i) for i in entry["issues"]]
            entry["issues"] = [i.to_dict() for i in sort_issues(existing + [issue])]
            entry["severity"] = "high"


def audit_movies(movies_root: Optional[Path] = None) -> dict:
    """Full movie dashboard payload: root, entries and rolled-up counts."""
    root = Path(movies_root) if movies_root is not None else get_movies_dir()
    entries = scan_movies(root)
    return {
        "root": str(root),
        "exists": root.is_dir(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entries": entries,
        "summary": summarise(entries),
    }
