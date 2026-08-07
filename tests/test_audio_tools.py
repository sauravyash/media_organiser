"""Tests for ffprobe/mutagen bitrate detection (cross-platform)."""

from pathlib import Path

import pytest

from media_organiser import audio_tools


def test_read_audio_metadata_uses_stream_bitrate_when_format_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_ffprobe(path: Path) -> dict:
        return {
            "format": {"duration": "120.0"},
            "streams": [
                {
                    "codec_name": "mp3",
                    "sample_rate": "44100",
                    "bit_rate": "256000",
                }
            ],
        }

    monkeypatch.setattr(audio_tools, "_run_ffprobe", fake_ffprobe)
    p = tmp_path / "t.mp3"
    p.write_bytes(b"fake")
    meta = audio_tools.read_audio_metadata(p)
    assert meta["bitrate_kbps"] == 256
    assert meta["codec_name"] == "mp3"


def test_detect_bitrate_minimum_256_inclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ffprobe(path: Path) -> dict:
        return {
            "format": {"duration": "120.0", "bit_rate": "256000"},
            "streams": [{"codec_name": "mp3", "sample_rate": "44100"}],
        }

    monkeypatch.setattr(audio_tools, "_run_ffprobe", fake_ffprobe)
    p = tmp_path / "t.mp3"
    p.write_bytes(b"fake")
    q = audio_tools.detect_bitrate_and_quality(p)
    assert q["rejected_reason"] is None
    assert q["quality_status"] == "warn"


def test_detect_bitrate_rejects_below_minimum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ffprobe(path: Path) -> dict:
        return {
            "format": {"duration": "120.0", "bit_rate": "255000"},
            "streams": [{"codec_name": "mp3", "sample_rate": "44100"}],
        }

    monkeypatch.setattr(audio_tools, "_run_ffprobe", fake_ffprobe)
    p = tmp_path / "t.mp3"
    p.write_bytes(b"fake")
    q = audio_tools.detect_bitrate_and_quality(p)
    assert q["quality_status"] == "rejected"
    assert q["rejected_reason"] is not None
