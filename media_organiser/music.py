"""Read-only audit of the music library, backed by the ``beet`` CLI.

Unlike the movie side — where this project owns the naming convention — the
music library is beets' business. So we ask ``beet`` for the library contents
(``beet ls -f ...``) rather than walking the filesystem, and report tagging
gaps as recommendations. Suggestions are given as the exact ``beet`` command to
run, so the user can verify each one by hand before applying it.

Nothing here writes to the beets library: only ``ls`` and ``stats`` are ever
invoked.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .audit import Issue, sort_issues, summarise, worst_severity

# Unit separator: safe field delimiter, never present in a tag value.
FIELD_SEP = "\x1f"

# beets item fields we read. Order matters — it defines the format string.
ITEM_FIELDS = (
    "id", "artist", "albumartist", "album", "title", "track", "tracktotal",
    "disc", "year", "genre", "format", "bitrate", "length", "path",
    "mb_trackid", "mb_albumid", "comp", "added",
)
ALBUM_FIELDS = (
    "id", "albumartist", "album", "year", "genre", "albumtype",
    "mb_albumid", "comp", "added", "path",
)

_PLACEHOLDER_RE = re.compile(r"(?i)^\s*(?:\[?unknown[^\]]*\]?|various|untitled|track\s*\d*|n/?a|none|-+)\s*$")
_LEADING_INT_RE = re.compile(r"\d+")
DEFAULT_TIMEOUT = 120
LOW_BITRATE_KBPS = 128
LOSSLESS_FORMATS = {"FLAC", "ALAC", "WAV", "AIFF", "APE", "WV"}


class BeetsUnavailable(RuntimeError):
    """Raised when the ``beet`` executable cannot be run at all."""


def beet_binary() -> str:
    """Path to the beets executable; override with ``BEET_BIN``."""
    return os.environ.get("BEET_BIN", "beet")


def beet_base_args() -> list[str]:
    """Base argv for every beets call, honouring config/library overrides."""
    args = [beet_binary()]
    config = os.environ.get("BEETS_CONFIG")
    if config:
        args += ["-c", config]
    library = os.environ.get("BEETS_LIBRARY")
    if library:
        args += ["-l", library]
    # Deliberately not MUSIC_LIB_DIR: that is the music *upload* export dir,
    # whereas this is beets' own -d music directory.
    directory = os.environ.get("BEETS_DIRECTORY")
    if directory:
        args += ["-d", directory]
    return args


def beet_available() -> bool:
    """True when the configured beets executable can be located."""
    binary = beet_binary()
    return bool(shutil.which(binary) or Path(binary).is_file())


def run_beet(extra_args: list[str], timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run ``beet`` with ``extra_args`` and return stdout.

    Raises :class:`BeetsUnavailable` when beets is missing, misconfigured or
    times out — the dashboard turns that into an actionable empty state rather
    than a 500.
    """
    argv = beet_base_args() + extra_args
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise BeetsUnavailable(
            f"{beet_binary()!r} was not found. Install beets or set BEET_BIN to its path."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BeetsUnavailable(f"beets timed out after {timeout}s running {' '.join(argv)}.") from exc
    except OSError as exc:
        raise BeetsUnavailable(f"Could not run beets: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise BeetsUnavailable(
            f"beets exited with code {proc.returncode}: {detail[-1] if detail else 'no output'}"
        )
    return proc.stdout


def _format_string(fields: tuple[str, ...]) -> str:
    return FIELD_SEP.join(f"${f}" for f in fields)


def parse_rows(stdout: str, fields: tuple[str, ...]) -> list[dict]:
    """Split ``beet ls -f`` output into dicts, skipping malformed lines."""
    rows = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(FIELD_SEP)
        if len(parts) != len(fields):
            # A tag containing a newline can split a record; drop it rather
            # than silently mis-assigning fields.
            continue
        rows.append({name: value.strip() for name, value in zip(fields, parts)})
    return rows


def fetch_items(query: Optional[list[str]] = None) -> list[dict]:
    """Every track in the beets library, as raw field dicts."""
    args = ["ls", "-f", _format_string(ITEM_FIELDS)] + list(query or [])
    return parse_rows(run_beet(args), ITEM_FIELDS)


def fetch_albums(query: Optional[list[str]] = None) -> list[dict]:
    """Every album in the beets library, as raw field dicts."""
    args = ["ls", "-a", "-f", _format_string(ALBUM_FIELDS)] + list(query or [])
    return parse_rows(run_beet(args), ALBUM_FIELDS)


def _blank(value: Optional[str]) -> bool:
    """True when a tag is empty or a placeholder like 'Unknown Artist'."""
    if value is None:
        return True
    value = value.strip()
    if not value or value == "$":
        return True
    return bool(_PLACEHOLDER_RE.match(value))


def _as_int(value: Optional[str]) -> int:
    """Leading integer of a beets-rendered value ('320kbps' -> 320)."""
    if not value:
        return 0
    m = _LEADING_INT_RE.search(value)
    return int(m.group(0)) if m else 0


def _quote(value: str) -> str:
    """Quote a value for inclusion in a suggested beet command."""
    return '"' + value.replace('"', '\\"') + '"'


def _found(value: Optional[str]) -> str:
    """Describe what a placeholder tag actually contains, for the issue text."""
    value = (value or "").strip()
    return f"found {value!r}" if value else "the tag is empty"


def _modify(track_id: str, assignment: str) -> str:
    return f"beet modify id:{track_id} {assignment}"


def audit_track(item: dict, seen_keys: dict[tuple, str]) -> list[Issue]:
    """Recommended changes for one track."""
    issues: list[Issue] = []
    track_id = item.get("id") or "?"
    artist = item.get("artist", "")
    album = item.get("album", "")
    title = item.get("title", "")
    path = item.get("path", "")

    if _blank(artist):
        issues.append(Issue(
            kind="missing-artist",
            severity="high",
            message=f"No usable artist tag ({_found(artist)}).",
            suggestion=_modify(track_id, "artist=<Artist>"),
        ))
    if _blank(album):
        issues.append(Issue(
            kind="missing-album",
            severity="medium",
            message=f"No usable album tag ({_found(album)}).",
            suggestion=_modify(track_id, "album=<Album>"),
        ))
    if _blank(title):
        issues.append(Issue(
            kind="missing-title",
            severity="high",
            message=f"No usable title tag ({_found(title)}).",
            suggestion=_modify(track_id, "title=<Title>"),
        ))
    if _blank(item.get("albumartist")) and not _blank(album):
        issues.append(Issue(
            kind="missing-albumartist",
            severity="low",
            message="Album artist is empty, so this track will not group with its album.",
            suggestion=_modify(track_id, "albumartist=<Album Artist>"),
        ))

    year = _as_int(item.get("year"))
    if not year:
        issues.append(Issue(
            kind="missing-year",
            severity="medium",
            message="No release year.",
            suggestion=_modify(track_id, "year=<YYYY>"),
        ))
    elif not (1900 <= year <= datetime.now().year + 1):
        issues.append(Issue(
            kind="suspect-year",
            severity="medium",
            message=f"Release year {year} looks wrong.",
            suggestion=_modify(track_id, "year=<YYYY>"),
        ))

    if not _as_int(item.get("track")):
        issues.append(Issue(
            kind="missing-track-number",
            severity="medium",
            message="Track number is 0, so album ordering will be wrong.",
            suggestion=_modify(track_id, "track=<N>"),
        ))

    if _blank(item.get("mb_trackid")):
        issues.append(Issue(
            kind="unmatched",
            severity="medium",
            message="No MusicBrainz track id — this was imported as-is, not matched to a release.",
            suggestion=f"beet import -s {_quote(path)}" if path else "Re-import this track and let beets match it.",
        ))

    if _blank(item.get("genre")):
        issues.append(Issue(
            kind="missing-genre",
            severity="low",
            message="No genre tag.",
            suggestion=_modify(track_id, "genre=<Genre>"),
        ))

    fmt = (item.get("format") or "").upper()
    bitrate = _as_int(item.get("bitrate"))
    if fmt and fmt not in LOSSLESS_FORMATS and 0 < bitrate < LOW_BITRATE_KBPS:
        issues.append(Issue(
            kind="low-bitrate",
            severity="low",
            message=f"{fmt} at {bitrate}kbps is below {LOW_BITRATE_KBPS}kbps.",
            suggestion="Replace with a higher-quality rip if you have one.",
        ))

    if path and not Path(path).exists():
        issues.append(Issue(
            kind="file-missing",
            severity="high",
            # Not !r: a Windows path would come out with doubled backslashes.
            message=f"beets has this track at {path} but the file is not there.",
            suggestion=f"beet remove id:{track_id}   # or restore the file and run: beet update",
        ))

    # Duplicate detection: same album artist + album + title recorded twice.
    key = (
        (item.get("albumartist") or artist).lower(),
        album.lower(),
        title.lower(),
        _as_int(item.get("disc")),
        _as_int(item.get("track")),
    )
    if not _blank(title):
        first = seen_keys.get(key)
        if first is not None:
            issues.append(Issue(
                kind="duplicate-track",
                severity="high",
                message=f"Same track is already in the library as id {first}.",
                suggestion=f"beet remove id:{track_id}   # after confirming id:{first} is the copy to keep",
            ))
        else:
            seen_keys[key] = track_id

    return issues


def audit_album(album: dict, track_count: int) -> list[Issue]:
    """Recommended changes for one album (things a single track can't show)."""
    issues: list[Issue] = []
    album_id = album.get("id") or "?"
    name = album.get("album", "")

    if _blank(album.get("albumartist")):
        issues.append(Issue(
            kind="missing-albumartist",
            severity="medium",
            message=f"Album {name or '(untitled)'!r} has no album artist.",
            suggestion=f"beet modify -a id:{album_id} albumartist=<Album Artist>",
        ))
    if _blank(album.get("mb_albumid")):
        issues.append(Issue(
            kind="unmatched",
            severity="medium",
            message="Album has no MusicBrainz release id — it was never matched.",
            suggestion=f"beet import -s {_quote(album.get('path', ''))}" if album.get("path")
            else "Re-import this album and let beets match it.",
        ))
    if not _as_int(album.get("year")):
        issues.append(Issue(
            kind="missing-year",
            severity="medium",
            message="Album has no release year.",
            suggestion=f"beet modify -a id:{album_id} year=<YYYY>",
        ))
    if track_count == 0:
        issues.append(Issue(
            kind="empty-album",
            severity="high",
            message="Album has no tracks attached in the beets database.",
            suggestion=f"beet remove -a id:{album_id}",
        ))
    return issues


def _album_group_key(row: dict) -> tuple:
    return ((row.get("albumartist") or "").lower(), (row.get("album") or "").lower())


def scan_music() -> dict:
    """Full music dashboard payload, or an ``available: False`` explanation."""
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    base = {
        "generated_at": generated_at,
        "beet": beet_binary(),
        "config": os.environ.get("BEETS_CONFIG"),
        "library": os.environ.get("BEETS_LIBRARY"),
        "directory": os.environ.get("BEETS_DIRECTORY"),
    }

    # Tracks are the primary listing: if that call fails, beets is genuinely
    # unreachable and the dashboard shows setup help instead.
    try:
        items = fetch_items()
    except BeetsUnavailable as exc:
        return {
            **base,
            "available": False,
            "error": str(exc),
            "album_error": None,
            "tracks": [],
            "albums": [],
            "summary": summarise([]),
            "album_summary": summarise([]),
        }

    # Albums are secondary. `beet ls -a` aborts on the first album it cannot
    # render (e.g. an album row with no items left), so a single bad row must
    # not blank out the tracks the user can still act on.
    album_error = None
    try:
        albums = fetch_albums()
    except BeetsUnavailable as exc:
        albums = []
        album_error = str(exc)

    tracks_per_album: dict[tuple, int] = {}
    for item in items:
        key = _album_group_key(item)
        tracks_per_album[key] = tracks_per_album.get(key, 0) + 1

    seen_keys: dict[tuple, str] = {}
    tracks = []
    for item in items:
        issues = sort_issues(audit_track(item, seen_keys))
        tracks.append({
            "id": item.get("id"),
            "artist": item.get("artist"),
            "albumartist": item.get("albumartist"),
            "album": item.get("album"),
            "title": item.get("title"),
            "track": _as_int(item.get("track")),
            "disc": _as_int(item.get("disc")),
            "year": _as_int(item.get("year")),
            "genre": item.get("genre"),
            "format": item.get("format"),
            "bitrate": _as_int(item.get("bitrate")),
            "length": item.get("length"),
            "path": item.get("path"),
            "matched": not _blank(item.get("mb_trackid")),
            "severity": worst_severity(issues),
            "issues": [i.to_dict() for i in issues],
        })

    album_records = []
    for album in albums:
        count = tracks_per_album.get(_album_group_key(album), 0)
        issues = sort_issues(audit_album(album, count))
        album_records.append({
            "id": album.get("id"),
            "albumartist": album.get("albumartist"),
            "album": album.get("album"),
            "year": _as_int(album.get("year")),
            "genre": album.get("genre"),
            "albumtype": album.get("albumtype"),
            "tracks": count,
            "path": album.get("path"),
            "matched": not _blank(album.get("mb_albumid")),
            "severity": worst_severity(issues),
            "issues": [i.to_dict() for i in issues],
        })

    tracks.sort(key=lambda t: ((t["albumartist"] or t["artist"] or "").lower(),
                               (t["album"] or "").lower(), t["disc"], t["track"]))
    album_records.sort(key=lambda a: ((a["albumartist"] or "").lower(), (a["album"] or "").lower()))

    return {
        **base,
        "available": True,
        "error": None,
        "album_error": album_error,
        "tracks": tracks,
        "albums": album_records,
        "summary": summarise(tracks),
        "album_summary": summarise(album_records),
    }
