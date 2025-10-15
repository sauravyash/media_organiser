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

# ------------------------- parse_local_nfo_for_title -------------------------
def test_parse_title_with_underscores(tmp_path):
    p = tmp_path / "movie.nfo"
    p.write_text("<movie><title>some_movie</title></movie>")
    assert nfo.parse_local_nfo_for_title(p) == "Some Movie"

def test_parse_title_from_plaintext(tmp_path):
    p = tmp_path / "movie.nfo"
    p.write_text("Title: some.movie")
    assert nfo.parse_local_nfo_for_title(p) == "Some Movie"


def test_parse_title_from_movie_xml(tmp_path):
    p = tmp_path / "movie.nfo"
    # dots/spaces to exercise clean_name + titlecase_soft -> "Some Movie"
    p.write_text("<movie><title>some.movie</title></movie>")
    assert nfo.parse_local_nfo_for_title(p) == "Some Movie"


def test_parse_title_when_root_is_wrapper_with_movie_child(tmp_path):
    p = tmp_path / "wrapped.nfo"
    p.write_text("<root><movie><title>another_title</title></movie></root>")
    # underscores -> space, soft titlecase -> "Another Title"
    assert nfo.parse_local_nfo_for_title(p) == "Another Title"


def test_parse_title_from_plaintext_fallback(tmp_path):
    p = tmp_path / "plain.nfo"
    # malformed XML forcing ET.ParseError → regex fallback
    p.write_text("Not XML\nTitle:   mixed.Case   \nOther: x")
    assert nfo.parse_local_nfo_for_title(p) == "Mixed Case"


def test_parse_title_outer_exception_returns_none(tmp_path):
    # Pass a directory so read_text raises (IsADirectoryError) → outer except → None
    d = tmp_path / "adir"
    d.mkdir()
    assert nfo.parse_local_nfo_for_title(d) is None


# ---------------------------- read_nfo_to_meta -------------------------------

def test_read_nfo_movie_with_uniqueid_and_subtitles(tmp_path):
    p = tmp_path / "m.nfo"
    p.write_text(
        """<movie>
             <title>some.movie</title>
             <year>2020</year>
             <quality>1080p</quality>
             <extension>mkv</extension>
             <size>12345</size>
             <filenameandpath>/a/b.mkv</filenameandpath>
             <originalfilename>orig.mkv</originalfilename>
             <sourcepath>/src/path.mkv</sourcepath>
             <uniqueid type="imdb">tt123</uniqueid>
             <uniqueid type="localhash">abc123</uniqueid>
             <subtitles>
               <subtitle file="sub.en.srt" lang="en"/>
               <subtitle file="sub.fr.srt" lang="fr"/>
             </subtitles>
           </movie>"""
    )
    meta = nfo.read_nfo_to_meta(p)
    # scope + main fields
    assert meta["scope"] == "movie"
    assert meta["title"] == "some.movie"   # read as-is; titlecase happens elsewhere in your pipeline
    assert meta["year"] == "2020"
    assert meta["quality"] == "1080p"
    assert meta["extension"] == "mkv"
    assert meta["size"] == "12345"
    assert meta["filenameandpath"] == "/a/b.mkv"
    assert meta["originalfilename"] == "orig.mkv"
    assert meta["sourcepath"] == "/src/path.mkv"
    # uniqueid localhash preference
    assert meta["uniqueid_localhash"] == "abc123"
    # subtitles parsed
    assert meta["subtitles"] == [
        {"file": "sub.en.srt", "lang": "en"},
        {"file": "sub.fr.srt", "lang": "fr"},
    ]


def test_read_nfo_episode_with_uniqueid(tmp_path):
    p = tmp_path / "e.nfo"
    p.write_text(
        """<episodedetails>
             <showtitle>Show Name</showtitle>
             <season>1</season>
             <episode>2</episode>
             <episode_to>3</episode_to>
             <title>Ep Title</title>
             <quality>720p</quality>
             <extension>mp4</extension>
             <size>999</size>
             <filenameandpath>/tv/ep.mp4</filenameandpath>
             <originalfilename>EpS01E02.mp4</originalfilename>
             <sourcepath>/src/ep.mp4</sourcepath>
             <uniqueid type="localhash">deadbeef</uniqueid>
            </episodedetails>"""
    )
    meta = nfo.read_nfo_to_meta(p)
    assert meta["scope"] == "tv"
    assert meta["showtitle"] == "Show Name"
    assert meta["season"] == "1"
    assert meta["episode"] == "2"
    assert meta["episode_to"] == "3"
    assert meta["title"] == "Ep Title"
    assert meta["quality"] == "720p"
    assert meta["extension"] == "mp4"
    assert meta["size"] == "999"
    assert meta["filenameandpath"] == "/tv/ep.mp4"
    assert meta["originalfilename"] == "EpS01E02.mp4"
    assert meta["sourcepath"] == "/src/ep.mp4"
    assert meta["uniqueid_localhash"] == "deadbeef"


def test_read_nfo_plaintext_fallback_extracts_title_and_year(tmp_path):
    p = tmp_path / "fallback.nfo"
    # invalid XML → except branch; it should pick Title: ... and a plausible year
    p.write_text("garbage\ntitle = fallback name\nsomething 2017 somewhere")
    meta = nfo.read_nfo_to_meta(p)
    # title passed through clean_name + titlecase_soft in except-branch
    assert meta.get("title") == "Fallback Name"
    assert meta.get("year") == "2017"

def test_parse_title_falls_back_to_root_when_no_known_container(tmp_path: Path):
    p = tmp_path / "weird.nfo"
    # root tag is "weirdroot", no <movie> child → node becomes None → fallback to root
    p.write_text("<weirdroot><title>some.movie</title></weirdroot>")
    # Expect your fixed normalisation/titlecasing (from earlier) to yield "Some Movie"
    assert nfo.parse_local_nfo_for_title(p) == "Some Movie"


def test_write_episode_nfo_skips_if_exists_and_no_overwrite(tmp_path, monkeypatch, capsys):
    # Arrange: make a fixed output path and pre-create it
    out = tmp_path / "S01E01.nfo"
    out.write_text("old")

    # Monkeypatch nfo_path_for → always return our 'out'
    monkeypatch.setattr(nfo, "nfo_path_for", lambda dst, kind, layout: out)

    # Act: call with overwrite=False → should SKIP and not alter the file
    write_episode_nfo(
        dst_video=tmp_path / "S01E01.mkv",
        computed={"title": "New Title"},
        base_meta=None,
        overwrite=False,
        layout="kodi",
    )

    # Assert: printed skip, file unchanged
    captured = capsys.readouterr().out
    assert f"NFO SKIP (exists): {out}" in captured
    assert out.read_text() == "old"


def test_write_episode_nfo_writes_xml_and_handles_uniqueid_and_subs(tmp_path, monkeypatch, capsys):
    out = tmp_path / "S02E03.nfo"
    monkeypatch.setattr(nfo, "nfo_path_for", lambda dst, kind, layout: out)

    # Provide rich computed metadata to exercise fields + uniqueid + subtitles
    computed = {
        "showtitle": "My Show",
        "season": 2,
        "episode": 3,
        "episode_to": None,  # ensure falsy path is okay
        "title": "Some Episode",
        "quality": "1080p",
        "extension": "mkv",
        "size": 123456789,
        "uniqueid_localhash": "abc123",
        "filenameandpath": "/media/TV/My Show/S02E03.mkv",
        "originalfilename": "My.Show.S02E03.1080p.mkv",
        "sourcepath": "/downloads",
        "subtitles": [
            {"file": "/subs/S02E03.eng.srt", "lang": "en"},
            {"file": "/subs/S02E03.spa.srt", "lang": "es"},
        ],
    }

    # To keep this test decoupled from merge_subtitles logic, set base_meta None
    write_episode_nfo(
        dst_video=tmp_path / "S02E03.mkv",
        computed=computed,
        base_meta=None,
        overwrite=True,  # force write even if file somehow existed
        layout="kodi",
    )

    # Assert: wrote and printed
    captured = capsys.readouterr().out
    assert f"NFO WRITE: {out}" in captured
    assert out.exists()

    # Parse and validate a few important bits
    tree = ET.fromstring(out.read_text(encoding="utf-8"))
    assert tree.tag == "episodedetails"
    assert tree.findtext("showtitle") == "My Show"
    assert tree.findtext("season") == "2"
    assert tree.findtext("episode") == "3"
    assert tree.findtext("title") == "Some Episode"
    assert tree.findtext("quality") == "1080p"
    assert tree.findtext("extension") == "mkv"
    assert tree.findtext("size") == "123456789"

    # uniqueid element with attributes
    uid = tree.find("uniqueid")
    assert uid is not None
    assert uid.get("type") == "localhash"
    assert uid.get("default") == "true"
    assert uid.text == "abc123"

    # subtitles block
    subs_el = tree.find("subtitles")
    assert subs_el is not None
    subs = subs_el.findall("subtitle")
    assert len(subs) == 2
    assert subs[0].get("file") == "/subs/S02E03.eng.srt"
    assert subs[0].get("lang") == "en"
    assert subs[1].get("file") == "/subs/S02E03.spa.srt"
    assert subs[1].get("lang") == "es"


def test_write_episode_nfo_omits_empty_subtitles_block(tmp_path, monkeypatch, capsys):
    out = tmp_path / "S01E02.nfo"
    monkeypatch.setattr(nfo, "nfo_path_for", lambda dst, kind, layout: out)

    computed = {
        "showtitle": "Empty Subs Show",
        "season": 1,
        "episode": 2,
        "title": "No Subs",
        "subtitles": [],  # ensure no <subtitles> element is created
    }

    write_episode_nfo(
        dst_video=tmp_path / "S01E02.mkv",
        computed=computed,
        base_meta=None,
        overwrite=True,
        layout="kodi",
    )

    xml = out.read_text(encoding="utf-8")
    root = ET.fromstring(xml)
    assert root.findtext("title") == "No Subs"
    assert root.find("subtitles") is None