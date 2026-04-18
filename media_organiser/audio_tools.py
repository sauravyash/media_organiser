from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3

from .duplicates import quick_fingerprint

# Minimum acceptable bitrate for library ingest (inclusive).
MIN_BITRATE_KBPS = 256


def _ffprobe_value_to_bitrate_kbps(value: Any) -> Optional[int]:
    """Parse ffprobe bit_rate (bps as int or str); return rounded kbps, or None if unknown."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().lower()
        if not s or s in ("n/a", "nan"):
            return None
        try:
            bps = int(float(s))
        except ValueError:
            return None
    else:
        try:
            bps = int(value)
        except (TypeError, ValueError):
            return None
    if bps <= 0:
        return None
    return int(round(bps / 1000))


def _bitrate_kbps_from_mutagen_mp3(path: Path) -> Optional[int]:
    try:
        from mutagen.mp3 import MP3
    except ImportError:
        return None
    try:
        mp3 = MP3(path)
        br = getattr(mp3.info, "bitrate", None) if mp3.info else None
        if br is None:
            return None
        return int(br)
    except Exception:
        return None


@dataclass
class AudioAnalysis:
    path: Path
    title: str | None
    artist: str | None
    album: str | None
    year: str | None
    track_number: str | None
    bitrate_kbps: Optional[int]
    sample_rate: Optional[int]
    duration_seconds: Optional[float]
    codec_name: Optional[str]
    quality_status: str
    quality_message: str
    rejected_reason: Optional[str]
    needs_transcode: bool


def _run_ffprobe(path: Path) -> Dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "format=duration,bit_rate:stream=codec_name,sample_rate,bit_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def read_audio_metadata(path: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "bitrate_kbps": None,
        "sample_rate": None,
        "duration_seconds": None,
        "codec_name": None,
    }
    data = _run_ffprobe(path)
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    stream_kbps: Optional[int] = None
    if streams:
        audio_stream = streams[0]
        stream_kbps = _ffprobe_value_to_bitrate_kbps(audio_stream.get("bit_rate"))
        codec = audio_stream.get("codec_name")
        if codec:
            info["codec_name"] = codec
        if "sample_rate" in audio_stream:
            try:
                info["sample_rate"] = int(audio_stream["sample_rate"])
            except (TypeError, ValueError):
                pass
    format_kbps = _ffprobe_value_to_bitrate_kbps(fmt.get("bit_rate"))
    # Prefer stream bitrate: format-level bit_rate is often missing on Linux for MP3/VBR.
    info["bitrate_kbps"] = stream_kbps if stream_kbps is not None else format_kbps
    if info["bitrate_kbps"] is None:
        info["bitrate_kbps"] = _bitrate_kbps_from_mutagen_mp3(path)
    if "duration" in fmt:
        try:
            info["duration_seconds"] = float(fmt["duration"])
        except (TypeError, ValueError):
            pass
    return info


def parse_filename_for_tags(path: Path) -> Dict[str, Optional[str]]:
    name = path.stem
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    track_number: Optional[str] = None
    year: Optional[str] = None

    parts = name.split(" - ")
    if len(parts) == 2:
        artist, title = parts
    elif len(parts) == 3:
        track_number, artist, title = parts
    else:
        title = name

    if path.parent != path.anchor:
        parent_name = path.parent.name
        if parent_name and parent_name not in {artist, title}:
            album = parent_name

    return {
        "artist": artist,
        "title": title,
        "album": album,
        "track_number": track_number,
        "year": year,
    }


def apply_id3_tags(path: Path, tags: Dict[str, str]) -> None:
    audio = MutagenFile(path, easy=True)
    if audio is None:
        try:
            audio = EasyID3(str(path))
        except Exception:
            audio = EasyID3()
    mapping = {
        "title": "title",
        "artist": "artist",
        "album": "album",
        "year": "date",
        "track_number": "tracknumber",
    }
    for src, dst in mapping.items():
        value = tags.get(src)
        if value:
            audio[dst] = [value]
    audio.save(str(path))


def detect_bitrate_and_quality(path: Path) -> Dict[str, Any]:
    meta = read_audio_metadata(path)
    bitrate = meta.get("bitrate_kbps")
    codec_name = (meta.get("codec_name") or "").lower() or None
    duration = meta.get("duration_seconds")

    quality_status = "ok"
    quality_message = "Meets 320 kbps MP3 target"
    rejected_reason: Optional[str] = None
    needs_transcode = False

    if bitrate is None or bitrate < MIN_BITRATE_KBPS:
        quality_status = "rejected"
        quality_message = f"Bitrate below minimum ({MIN_BITRATE_KBPS} kbps) – rejected"
        rejected_reason = quality_message
        needs_transcode = False
    elif codec_name != "mp3" or bitrate < 320:
        quality_status = "warn"
        quality_message = "Needs transcode to 320 kbps MP3"
        needs_transcode = True
    elif bitrate >= 320 and codec_name == "mp3":
        if duration and duration > 60 and bitrate < 310:
            quality_status = "warn"
            quality_message = "Suspicious 320 kbps – may be upsampled"
            needs_transcode = True

    return {
        "bitrate_kbps": bitrate,
        "sample_rate": meta.get("sample_rate"),
        "duration_seconds": duration,
        "codec_name": codec_name,
        "quality_status": quality_status,
        "quality_message": quality_message,
        "rejected_reason": rejected_reason,
        "needs_transcode": needs_transcode,
    }


def analyse_audio(path: Path) -> AudioAnalysis:
    tag_meta = {}
    try:
        audio = MutagenFile(path, easy=True)
        if audio is not None:
            tag_meta = {
                "title": (audio.get("title") or [None])[0],
                "artist": (audio.get("artist") or [None])[0],
                "album": (audio.get("album") or [None])[0],
                "year": (audio.get("date") or [None])[0],
                "track_number": (audio.get("tracknumber") or [None])[0],
            }
    except Exception:
        tag_meta = {}

    filename_tags = parse_filename_for_tags(path)
    for key, value in filename_tags.items():
        if not tag_meta.get(key) and value:
            tag_meta[key] = value

    quality = detect_bitrate_and_quality(path)

    return AudioAnalysis(
        path=path,
        title=tag_meta.get("title"),
        artist=tag_meta.get("artist"),
        album=tag_meta.get("album"),
        year=tag_meta.get("year"),
        track_number=tag_meta.get("track_number"),
        bitrate_kbps=quality.get("bitrate_kbps"),
        sample_rate=quality.get("sample_rate"),
        duration_seconds=quality.get("duration_seconds"),
        codec_name=quality.get("codec_name"),
        quality_status=quality.get("quality_status") or "ok",
        quality_message=quality.get("quality_message") or "",
        rejected_reason=quality.get("rejected_reason"),
        needs_transcode=bool(quality.get("needs_transcode")),
    )


def _sanitize_component(text: str) -> str:
    """Sanitize a filesystem component according to Navidrome spec."""
    # Replace forbidden characters
    replacements = {
        "/": "-",
        "\\": "-",
        ":": "-",
    }
    for bad, repl in replacements.items():
        text = text.replace(bad, repl)
    # Remove characters that should be dropped
    for bad in ['?', '*', '"', "<", ">", "|"]:
        text = text.replace(bad, "")
    # Normalise whitespace
    text = " ".join(text.split())
    return text.strip()


def _parse_int_first_part(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    part = str(value).split("/")[0].strip()
    if not part:
        return None
    try:
        return int(part)
    except ValueError:
        return None


def _find_duplicate_in_music_library(source: Path, export_dir: Path, target_name: str) -> Optional[Path]:
    """
    Find an existing identical file in the music library.
    Preference order:
    1) Same filename + same fingerprint
    2) Any filename + same fingerprint
    """
    try:
        src_fp = quick_fingerprint(source)
    except OSError:
        return None

    same_name_match: Optional[Path] = None
    for existing in export_dir.rglob("*.mp3"):
        try:
            if not existing.is_file():
                continue
            if existing.resolve() == source.resolve():
                continue
            if existing.stat().st_size != src_fp[0]:
                continue
            ex_fp = quick_fingerprint(existing)
            if ex_fp != src_fp:
                continue
            if existing.name == target_name:
                return existing
            if same_name_match is None:
                same_name_match = existing
        except (OSError, FileNotFoundError):
            continue
    return same_name_match


def _compute_library_target(source: Path, export_dir: Path) -> Path:
    """Compute Artist / YEAR - Album / NN - Title.mp3 style destination."""
    artist: Optional[str] = None
    album: Optional[str] = None
    album_artist: Optional[str] = None
    title: Optional[str] = None
    year: Optional[str] = None
    track_number_raw: Optional[str] = None
    disc_number_raw: Optional[str] = None

    try:
        audio = MutagenFile(source, easy=True)
        if audio is not None:
            artist = (audio.get("artist") or [None])[0]
            album = (audio.get("album") or [None])[0]
            album_artist = (audio.get("albumartist") or [None])[0]
            title = (audio.get("title") or [None])[0]
            year = (audio.get("date") or [None])[0]
            track_number_raw = (audio.get("tracknumber") or [None])[0]
            disc_number_raw = (audio.get("discnumber") or [None])[0]
    except Exception:
        pass

    filename_tags = parse_filename_for_tags(source)
    if not artist and filename_tags.get("artist"):
        artist = filename_tags["artist"]
    if not title and filename_tags.get("title"):
        title = filename_tags["title"]
    if not album and filename_tags.get("album"):
        album = filename_tags["album"]
    if not year and filename_tags.get("year"):
        year = filename_tags["year"]
    if not track_number_raw and filename_tags.get("track_number"):
        track_number_raw = filename_tags["track_number"]

    # Fallbacks
    if not title:
        title = source.stem
    if not artist:
        artist = "Unknown Artist"
    if not album:
        album = "Unknown Album"

    artist_safe = _sanitize_component(artist)
    album_safe = _sanitize_component(album)
    title_safe = _sanitize_component(title)

    if year:
        year_str = _sanitize_component(year)[:4]
    else:
        year_str = "0000"

    track_no = _parse_int_first_part(track_number_raw)
    disc_no = _parse_int_first_part(disc_number_raw) or 1

    # Detect compilations by album artist
    album_artist_norm = (album_artist or "").strip().lower()
    is_compilation = album_artist_norm in {"various artists", "va"}

    if is_compilation:
        root = export_dir / "Compilations"
        album_dir = root / f"{year_str} - {album_safe}"
        # NN - Artist - Track Title.mp3
        if track_no is None:
            track_prefix = "00"
        else:
            track_prefix = f"{track_no:02d}"
        filename = f"{track_prefix} - {artist_safe} - {title_safe}.mp3"
    else:
        root = export_dir / artist_safe
        album_dir = root / f"{year_str} - {album_safe}"
        # Multi-disc flattened: DD-TT - Track Title.mp3
        if track_no is None:
            track_prefix = "00"
            filename = f"{track_prefix} - {title_safe}.mp3"
        else:
            if disc_no and disc_no > 1:
                filename = f"{disc_no:02d}-{track_no:02d} - {title_safe}.mp3"
            else:
                filename = f"{track_no:02d} - {title_safe}.mp3"

    target = album_dir / filename

    try:
        source_fp = quick_fingerprint(source)
    except OSError:
        source_fp = None

    n = 1
    while True:
        if not target.exists():
            return target
        if source_fp is not None:
            try:
                if quick_fingerprint(target) == source_fp:
                    return target
            except (OSError, FileNotFoundError):
                pass
        stem = target.stem
        suffix = target.suffix
        target = target.with_name(f"{stem} ({n}){suffix}")
        n += 1


def ensure_mp3_320(
    source: Path, export_dir: Path, *, scan_library_duplicates: bool = True
) -> Dict[str, Any]:
    export_dir.mkdir(parents=True, exist_ok=True)

    quality = detect_bitrate_and_quality(source)
    bitrate = quality.get("bitrate_kbps") or 0
    codec_name = (quality.get("codec_name") or "").lower()
    rejected_reason = quality.get("rejected_reason")

    if rejected_reason:
        return {
            "status": "rejected",
            "reason": rejected_reason,
            "output_path": None,
            "quality_status": "rejected",
            "quality_message": rejected_reason,
        }

    target = _compute_library_target(source, export_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    if scan_library_duplicates:
        duplicate = _find_duplicate_in_music_library(source, export_dir, target.name)
        if duplicate is not None:
            try:
                source.unlink(missing_ok=True)
            except OSError:
                pass
            return {
                "status": "ok",
                "reason": None,
                "output_path": str(duplicate),
                "quality_status": "ok",
                "quality_message": "Duplicate removed (identical to existing library file)",
            }

    # Identical bytes already in the library under this naming scheme — drop the extra file.
    if target.exists() and source.resolve() != target.resolve():
        try:
            if quick_fingerprint(source) == quick_fingerprint(target):
                source.unlink(missing_ok=True)
                return {
                    "status": "ok",
                    "reason": None,
                    "output_path": str(target),
                    "quality_status": "ok",
                    "quality_message": "Duplicate removed (identical to existing library file)",
                }
        except OSError:
            pass

    if codec_name == "mp3" and bitrate >= 320 and not quality.get("needs_transcode"):
        if source.resolve() != target.resolve():
            target.write_bytes(source.read_bytes())
        return {
            "status": "ok",
            "reason": None,
            "output_path": str(target),
            "quality_status": "ok",
            "quality_message": "Already 320 kbps MP3",
        }

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-b:a",
        "320k",
        "-qscale:a",
        "0",
        str(target),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        return {
            "status": "error",
            "reason": f"Transcode failed: {e}",
            "output_path": None,
            "quality_status": "warn",
            "quality_message": "Transcode failed",
        }

    post_quality = detect_bitrate_and_quality(target)
    return {
        "status": "ok",
        "reason": None,
        "output_path": str(target),
        "quality_status": post_quality.get("quality_status") or "ok",
        "quality_message": post_quality.get("quality_message")
        or "Transcoded to 320 kbps MP3",
    }

