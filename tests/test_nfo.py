# tests/test_nfo_merge.py
from pathlib import Path
import xml.etree.ElementTree as ET
import media_organiser.nfo as nfo

from media_organiser.nfo import (
    write_movie_nfo, write_episode_nfo, nfo_path_for
)

def test_write_movie_nfo_merge_first(tmp_path):
    dst_video = tmp_path / "Movie (1080p).mkv"
    dst_video.write_bytes(b"x")

    # base meta prefers existing title/year
    base = {"title": "Existing Title", "year": "2011", "subtitles": [{"file":"old.srt","lang":"en"}]}
    computed = {
        "title": "Computed Title",
        "year": "2012",
        "quality": "1080p",
        "extension": "mkv",
        "size": 1,
        "uniqueid_localhash": "abc123",
        "filenameandpath": str(dst_video),
        "originalfilename": "Movie.mkv",
        "sourcepath": "/src/Movie.mkv",
        "subtitles": [{"file":"new.srt","lang":"en"}],
    }

    out = nfo_path_for(dst_video, "movie", "same-stem")
    write_movie_nfo(dst_video, computed, base, overwrite=True, layout="same-stem")

    xml = out.read_bytes()
    assert xml.startswith(b"<?xml")  # xml declaration should be present
    root = ET.fromstring(xml)
    assert root.findtext("title") == "Existing Title"    # kept from base
    assert root.findtext("year") == "2011"               # kept from base
    # merged subtitles should include both
    subs = root.find("subtitles")
    files = {s.attrib["file"] for s in subs.findall("subtitle")}
    assert {"old.srt", "new.srt"} <= files

def test_write_episode_nfo_basic(tmp_path):
    dst_video = tmp_path / "Series - S01E02 (720p).mkv"
    dst_video.write_bytes(b"x")

    computed = {
        "showtitle": "Series",
        "season": 1,
        "episode": 2,
        "title": "Series S01E02",
        "quality": "720p",
        "extension": "mkv",
        "size": 1,
        "uniqueid_localhash": "xyz",
        "filenameandpath": str(dst_video),
        "originalfilename": "series.s01e02.mkv",
        "sourcepath": "/src/series.s01e02.mkv",
        "subtitles": [],
    }
    out = nfo_path_for(dst_video, "tv", "same-stem")
    write_episode_nfo(dst_video, computed, base_meta=None, overwrite=True, layout="same-stem")

    xml = out.read_text()
    assert "<episodedetails>" in xml and "<showtitle>Series</showtitle>" in xml


MOVIE_EXISTING = """<movie><title>Existing Title</title><year>1999</year></movie>"""
EP_EXISTING = """<episodedetails><title>Ep Title</title><season>1</season><episode>2</episode></episodedetails>"""

# def parse(path: Path):
#     return ET.fromstring(path.read_text(encoding="utf-8"))

def test_movie_nfo_skip_when_exists_kodi_layout(tmp_path):
    # Arrange: have a movie file and an existing kodi-style movie.nfo
    movie_dir = tmp_path / "Movies" / "Title (2020)"
    movie_dir.mkdir(parents=True)
    dst_video = movie_dir / "Title (2020).mkv"
    dst_video.write_bytes(b"x")

    existing = movie_dir / "movie.nfo"  # kodi layout -> movie.nfo next to video
    existing_xml = b'<?xml version="1.0" encoding="utf-8"?><movie><title>Existing Title</title><year>1999</year></movie>'
    existing.write_bytes(existing_xml)

    # Act: attempt to write with overwrite=False; should SKIP
    computed = {"title": "New Title", "year": "2020"}
    nfo.write_movie_nfo(dst_video, computed, base_meta=None, overwrite=False, layout="kodi")

    # Assert: file unchanged
    assert existing.read_bytes() == existing_xml


def test_nfo_path_for_movie_kodi_and_same_stem(tmp_path):
    f = tmp_path / "X.mkv"
    f.write_bytes(b".")
    assert nfo.nfo_path_for(f, "movie", "kodi").name == "movie.nfo"
    assert nfo.nfo_path_for(f, "movie", "same-stem").name == "X.nfo"

