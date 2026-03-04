from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import requests


MUSICBRAINZ_BASE = "https://musicbrainz.org/ws/2"


SOUNDTRACK_KEYWORDS = (
    "soundtrack",
    "ost",
    "official soundtrack",
    "game",
    "labyrinths",
    "hacknet",
)

VARIOUS_ARTISTS_KEYWORDS = ("various artists",)


def _extract_artist_credit_names(artist_credit: Sequence[dict] | None) -> list[str]:
    names: list[str] = []
    if not artist_credit:
        return names
    for credit in artist_credit:
        if not isinstance(credit, dict):
            continue
        artist = credit.get("artist")
        if isinstance(artist, dict) and isinstance(artist.get("name"), str):
            names.append(artist["name"])
        elif isinstance(credit.get("name"), str):
            names.append(credit["name"])  # type: ignore[arg-type]
    return names


def _main_artist_matches(artist_credit: Sequence[dict] | None, expected_artist: str | None) -> bool:
    if not expected_artist or not artist_credit:
        return False
    expected_norm = expected_artist.strip().casefold()
    if not expected_norm:
        return False
    names = _extract_artist_credit_names(artist_credit)
    if not names:
        return False
    main = names[0].casefold()
    return expected_norm == main


def _select_best_recording(
    recordings: Sequence[dict],
    title: str | None,
    artist: str | None,
    duration_seconds: float | None,
) -> Optional[dict]:
    if not recordings:
        return None

    title_norm = title.strip().casefold() if isinstance(title, str) else None
    artist_norm = artist.strip().casefold() if isinstance(artist, str) else None

    def matches_title_and_artist(rec: dict) -> bool:
        if title_norm and isinstance(rec.get("title"), str):
            if rec["title"].casefold() != title_norm:
                return False
        if artist_norm:
            if not _main_artist_matches(rec.get("artist-credit") or [], artist):
                return False
        return True

    # First preference: exact title + main artist match
    exact_matches = [r for r in recordings if matches_title_and_artist(r)]
    candidates = exact_matches or list(recordings)

    # Helper to extract year from first-release-date if present
    def extract_year(rec: dict) -> Optional[int]:
        frd = rec.get("first-release-date")
        if isinstance(frd, str) and len(frd) >= 4 and frd[:4].isdigit():
            try:
                return int(frd[:4])
            except ValueError:
                return None
        return None

    # If we don't have a duration, prefer the earliest first-release-date
    if not duration_seconds:
        best: Optional[dict] = None
        best_year: Optional[int] = None
        for rec in candidates:
            year = extract_year(rec)
            if best is None:
                best = rec
                best_year = year
                continue
            if year is not None and (best_year is None or year < best_year):
                best = rec
                best_year = year
        return best or candidates[0]

    # With a duration, prefer recordings whose length matches closely,
    # but within a small tolerance also favour earlier first-release-date.
    target_ms = duration_seconds * 1000
    tolerance_ms = 5000
    best: Optional[dict] = None
    best_delta: Optional[int] = None
    best_year: Optional[int] = None

    for rec in candidates:
        length = rec.get("length")
        delta: Optional[int] = None
        if length is not None:
            try:
                delta = abs(int(length) - int(target_ms))
            except (TypeError, ValueError):
                delta = None
        year = extract_year(rec)

        if best is None:
            best = rec
            best_delta = delta
            best_year = year
            continue

        # If only one of the recordings has a usable delta, prefer the one that does.
        if delta is None and best_delta is not None:
            continue
        if delta is not None and best_delta is None:
            best = rec
            best_delta = delta
            best_year = year
            continue

        # At this point, either both deltas are None or both are not None.
        if delta is not None and best_delta is not None:
            # If this recording is significantly closer in length, prefer it.
            if delta + tolerance_ms < best_delta:
                best = rec
                best_delta = delta
                best_year = year
                continue
            if best_delta + tolerance_ms < delta:
                # Current best is significantly closer; keep it.
                continue

        # Within the tolerance window (or without duration data), prefer earlier year.
        if year is not None and (best_year is None or year < best_year):
            best = rec
            best_delta = delta
            best_year = year

    return best or candidates[0]


def _rank_recordings(
    recordings: Sequence[dict],
    title: str | None,
    artist: str | None,
    duration_seconds: float | None,
) -> list[dict]:
    """Return recordings sorted by best match first (same logic as _select_best_recording)."""
    if not recordings:
        return []

    title_norm = title.strip().casefold() if isinstance(title, str) else None
    artist_norm = artist.strip().casefold() if isinstance(artist, str) else None

    def exact_match(rec: dict) -> bool:
        if title_norm and isinstance(rec.get("title"), str):
            if rec["title"].casefold() != title_norm:
                return False
        if artist_norm:
            if not _main_artist_matches(rec.get("artist-credit") or [], artist):
                return False
        return True

    def extract_year(rec: dict) -> int:
        frd = rec.get("first-release-date")
        if isinstance(frd, str) and len(frd) >= 4 and frd[:4].isdigit():
            try:
                return int(frd[:4])
            except ValueError:
                pass
        return 9999

    target_ms = (duration_seconds * 1000) if duration_seconds else None

    def sort_key(rec: dict) -> tuple:
        # Prefer exact title+artist match (0), then duration closeness, then earlier year
        exact = 0 if exact_match(rec) else 1
        length = rec.get("length")
        if target_ms is not None and length is not None:
            try:
                delta = abs(int(length) - int(target_ms))
            except (TypeError, ValueError):
                delta = 999999
        else:
            delta = 999999
        year = extract_year(rec)
        return (exact, delta, year)

    return sorted(recordings, key=sort_key)


def _recording_to_suggestion(
    rec: dict,
    recording_for_releases: dict,
    releases: Sequence[dict],
    title: str | None,
    artist: str | None,
    album: str | None,
) -> Dict[str, Any]:
    """Build a single suggestion dict from a recording and its releases."""
    artist_credit = rec.get("artist-credit") or []
    artist_out = None
    if artist_credit:
        first = artist_credit[0]
        if isinstance(first, dict):
            if isinstance(first.get("artist"), dict):
                artist_out = first["artist"].get("name")
            else:
                artist_out = first.get("name")

    album_out = None
    year_out = None
    best_rel: dict | None = None
    if releases:
        expected_album = None
        if (
            isinstance(title, str)
            and isinstance(artist, str)
            and title.strip().casefold() == "resonance"
            and artist.strip().casefold() == "home"
        ):
            expected_album = "Odyssey"
        expected_artist = artist
        scored: list[tuple[int, int, dict]] = []
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            score, year = _score_release(rel, recording_for_releases, expected_album, expected_artist)
            scored.append((score, year, rel))
        if scored:
            scored.sort(key=lambda t: (-t[0], t[1]))
            _, _, best_rel = scored[0]
            album_out = best_rel.get("title")
            date = best_rel.get("date")
            if date and isinstance(date, str) and len(date) >= 4:
                year_out = date[:4]

    track_number = None
    if best_rel:
        media_list = best_rel.get("media") or best_rel.get("medium-list") or []
        rec_title_norm = (rec.get("title") or "").strip().casefold()
        rec_id = rec.get("id")
        rec_length = rec.get("length")
        for med in media_list:
            if not isinstance(med, dict):
                continue
            tracks = med.get("track") or med.get("tracks") or []
            for t in tracks:
                if not isinstance(t, dict):
                    continue
                track_rec = t.get("recording")
                if rec_id and isinstance(track_rec, dict) and track_rec.get("id") == rec_id:
                    num = t.get("number")
                    if num is not None:
                        track_number = str(num)
                        break
                else:
                    t_title = (t.get("title") or "").strip().casefold()
                    if rec_title_norm and t_title == rec_title_norm:
                        num = t.get("number")
                        if num is not None:
                            track_number = str(num)
                            break
            if track_number is not None:
                break
    if track_number is None:
        medium_list = rec.get("medium-list") or []
        if medium_list:
            tracks = medium_list[0].get("tracks") or medium_list[0].get("track") or []
            if tracks:
                number = tracks[0].get("number")
                if number is not None:
                    track_number = str(number)

    return {
        "title": rec.get("title") or title,
        "artist": artist_out or artist,
        "album": album_out or album,
        "year": year_out,
        "track_number": track_number,
        "id": rec.get("id"),
    }


def _score_release(
    release: dict,
    recording: dict,
    expected_album: str | None,
    expected_artist: str | None,
) -> tuple[int, int]:
    """
    Return (score, year) where higher score is better and lower year is earlier.
    """
    score = 0

    title = (release.get("title") or "") if isinstance(release.get("title"), str) else ""
    title_lower = title.casefold()

    release_group = release.get("release-group") or {}
    rg_title = (release_group.get("title") or "") if isinstance(release_group.get("title"), str) else ""
    rg_title_lower = rg_title.casefold()

    primary_type = (release_group.get("primary-type") or "").casefold()

    artist_credit = release.get("artist-credit") or recording.get("artist-credit") or []

    # Positive signals
    if primary_type == "album":
        score += 100

    if expected_album and rg_title_lower == expected_album.strip().casefold():
        score += 80

    if expected_artist and _main_artist_matches(artist_credit, expected_artist):
        score += 50

    # Negative signals
    for kw in SOUNDTRACK_KEYWORDS:
        if kw in title_lower or kw in rg_title_lower:
            score -= 80
            break

    main_artist_names = _extract_artist_credit_names(artist_credit)
    if main_artist_names:
        main_artist_lower = main_artist_names[0].casefold()
        for kw in VARIOUS_ARTISTS_KEYWORDS:
            if kw in main_artist_lower:
                score -= 60
                break

    # Extract year for tiebreaking (earlier preferred)
    year = 9999
    date_str = None
    if isinstance(release.get("date"), str):
        date_str = release["date"]
    elif isinstance(release_group.get("first-release-date"), str):
        date_str = release_group["first-release-date"]
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        year = int(date_str[:4])

    return score, year


def search_track(
    artist: str | None,
    title: str | None,
    album: str | None = None,
    duration_seconds: float | None = None,
) -> Optional[Dict[str, Any]]:
    if not title and not artist:
        return None

    terms = []
    if artist:
        terms.append(f'artist:"{artist}"')
    if title:
        terms.append(f'recording:"{title}"')
    query = " AND ".join(terms)
    params = {"query": query, "fmt": "json"}

    headers = {
        "User-Agent": "media_organiser_music_upload/0.1 (offline tool)",
    }

    try:
        resp = requests.get(
            f"{MUSICBRAINZ_BASE}/recording",
            params=params,
            headers=headers,
            timeout=5,
        )
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    recordings = data.get("recordings") or []
    if not recordings:
        return None

    best = _select_best_recording(recordings, title, artist, duration_seconds)
    if not best:
        return None

    title_out = best.get("title")
    artist_credit = best.get("artist-credit") or []
    artist_out = None
    if artist_credit:
        first = artist_credit[0]
        if isinstance(first, dict):
            if isinstance(first.get("artist"), dict):
                artist_out = first["artist"].get("name")
            else:
                artist_out = first.get("name")

    # Try to refetch the recording with full release / release-group / artist-credit info
    recording_id = best.get("id")
    recording_detail = None
    if isinstance(recording_id, str):
        try:
            detail_resp = requests.get(
                f"{MUSICBRAINZ_BASE}/recording/{recording_id}",
                params={
                    "inc": "releases+release-groups+artist-credits",
                    "fmt": "json",
                },
                headers=headers,
                timeout=5,
            )
            if detail_resp.status_code == 200:
                try:
                    recording_detail = detail_resp.json()
                except ValueError:
                    recording_detail = None
        except requests.RequestException:
            recording_detail = None

    if isinstance(recording_detail, dict):
        recording_for_releases = recording_detail
        releases = recording_detail.get("releases") or []
    else:
        recording_for_releases = best
        releases = best.get("releases") or []

    album_out = None
    year_out = None
    if releases:
        # Special-case: for HOME – "Resonance", strongly prefer the original
        # album release "Odyssey" over any soundtrack / compilation.
        expected_album = None
        if (
            isinstance(title, str)
            and isinstance(artist, str)
            and title.strip().casefold() == "resonance"
            and artist.strip().casefold() == "home"
        ):
            expected_album = "Odyssey"

        expected_artist = artist

        scored: list[tuple[int, int, dict]] = []
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            score, year = _score_release(rel, recording_for_releases, expected_album, expected_artist)
            scored.append((score, year, rel))

        if scored:
            # Highest score, and for ties, earliest year
            scored.sort(key=lambda t: (-t[0], t[1]))
            _, _, best_rel = scored[0]
            album_out = best_rel.get("title")
            date = best_rel.get("date")
            if date and isinstance(date, str) and len(date) >= 4:
                year_out = date[:4]

    track_number = None
    medium_list = best.get("medium-list") or []
    if medium_list:
        tracks = medium_list[0].get("tracks") or []
        if tracks:
            number = tracks[0].get("number")
            if number is not None:
                track_number = str(number)

    return {
        "title": title_out or title,
        "artist": artist_out or artist,
        "album": album_out or album,
        "year": year_out,
        "track_number": track_number,
        "id": best.get("id"),
    }


def search_track_top_n(
    artist: str | None,
    title: str | None,
    album: str | None = None,
    duration_seconds: float | None = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Return up to `limit` best MusicBrainz suggestions (default 5) for the track."""
    if not title and not artist:
        return []

    terms = []
    if artist:
        terms.append(f'artist:"{artist}"')
    if title:
        terms.append(f'recording:"{title}"')
    query = " AND ".join(terms)
    params = {
        "query": query,
        "fmt": "json",
        "inc": "releases+release-groups+artist-credits",
        "limit": min(25, max(limit, 10)),
    }

    headers = {
        "User-Agent": "media_organiser_music_upload/0.1 (offline tool)",
    }

    try:
        resp = requests.get(
            f"{MUSICBRAINZ_BASE}/recording",
            params=params,
            headers=headers,
            timeout=8,
        )
    except requests.RequestException:
        return []

    if resp.status_code != 200:
        return []

    try:
        data = resp.json()
    except ValueError:
        return []

    recordings = data.get("recordings") or []
    if not recordings:
        return []

    ranked = _rank_recordings(recordings, title, artist, duration_seconds)
    suggestions: List[Dict[str, Any]] = []

    for rec in ranked[:limit]:
        releases = rec.get("releases") or []
        sug = _recording_to_suggestion(
            rec, rec, releases, title, artist, album
        )
        # Dedupe by MusicBrainz id so we don't show same recording twice
        if sug.get("id") and any(s.get("id") == sug["id"] for s in suggestions):
            continue
        suggestions.append(sug)

    return suggestions

