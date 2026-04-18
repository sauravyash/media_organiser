# tests/test_audio_duplicates.py
from pathlib import Path

from media_organiser import audio_tools


def _library_canonical(export: Path) -> Path:
    return export / "MyArtist" / "0000 - 2020 - MyAlbum" / "01 - MySong.mp3"


def _make_incoming_same_tags(base: Path) -> Path:
    parent = base / "incoming" / "2020 - MyAlbum"
    parent.mkdir(parents=True, exist_ok=True)
    p = parent / "01 - MyArtist - MySong.mp3"
    return p


def test_compute_library_target_returns_canonical_when_bytes_match(tmp_path):
    export = tmp_path / "music"
    canon = _library_canonical(export)
    canon.parent.mkdir(parents=True, exist_ok=True)
    payload = b"same-track-" + b"z" * 3000
    canon.write_bytes(payload)

    src = _make_incoming_same_tags(tmp_path)
    src.write_bytes(payload)

    out = audio_tools._compute_library_target(src, export)
    assert out == canon


def test_compute_library_target_uses_numbered_slot_when_bytes_differ(tmp_path):
    export = tmp_path / "music"
    canon = _library_canonical(export)
    canon.parent.mkdir(parents=True, exist_ok=True)
    canon.write_bytes(b"first-version")

    src = _make_incoming_same_tags(tmp_path)
    src.write_bytes(b"second-version")

    out = audio_tools._compute_library_target(src, export)
    assert out == canon.parent / "01 - MySong (1).mp3"


def test_ensure_mp3_320_deletes_incoming_when_library_duplicate(tmp_path, monkeypatch):
    export = tmp_path / "music"
    canon = _library_canonical(export)
    canon.parent.mkdir(parents=True, exist_ok=True)
    payload = b"dup-mp3-" + b"q" * 4000
    canon.write_bytes(payload)

    src = _make_incoming_same_tags(tmp_path)
    src.write_bytes(payload)

    monkeypatch.setattr(
        audio_tools,
        "detect_bitrate_and_quality",
        lambda _p: {
            "bitrate_kbps": 320,
            "codec_name": "mp3",
            "rejected_reason": None,
            "needs_transcode": False,
        },
    )

    result = audio_tools.ensure_mp3_320(src, export)
    assert result["status"] == "ok"
    assert Path(result["output_path"]) == canon
    assert not src.exists()
    assert canon.exists()


def test_ensure_mp3_320_scans_music_library_for_duplicate(tmp_path, monkeypatch):
    export = tmp_path / "music"
    existing = export / "OtherArtist" / "1999 - OtherAlbum" / "02 - Different Name.mp3"
    existing.parent.mkdir(parents=True, exist_ok=True)
    payload = b"same-bytes-" + b"k" * 3000
    existing.write_bytes(payload)

    src = _make_incoming_same_tags(tmp_path)
    src.write_bytes(payload)

    monkeypatch.setattr(
        audio_tools,
        "detect_bitrate_and_quality",
        lambda _p: {
            "bitrate_kbps": 320,
            "codec_name": "mp3",
            "rejected_reason": None,
            "needs_transcode": False,
        },
    )

    result = audio_tools.ensure_mp3_320(src, export)
    assert result["status"] == "ok"
    assert Path(result["output_path"]) == existing
    assert not src.exists()
    assert existing.exists()


def test_ensure_mp3_320_can_disable_library_scan(tmp_path, monkeypatch):
    export = tmp_path / "music"
    existing = export / "OtherArtist" / "1999 - OtherAlbum" / "02 - Different Name.mp3"
    existing.parent.mkdir(parents=True, exist_ok=True)
    payload = b"same-bytes-" + b"k" * 3000
    existing.write_bytes(payload)

    src = _make_incoming_same_tags(tmp_path)
    src.write_bytes(payload)

    monkeypatch.setattr(
        audio_tools,
        "detect_bitrate_and_quality",
        lambda _p: {
            "bitrate_kbps": 320,
            "codec_name": "mp3",
            "rejected_reason": None,
            "needs_transcode": False,
        },
    )

    result = audio_tools.ensure_mp3_320(src, export, scan_library_duplicates=False)
    assert result["status"] == "ok"
    assert src.exists(), "Source is kept when library scan is disabled in copy path"
