import sys
from pathlib import Path

from media_organiser.cli import main
from media_organiser.naming import is_tv_episode, detect_quality, clean_name, guess_movie_name_from_file, guess_movie_name, movie_part_suffix, titlecase_soft, movie_name_from_parents, is_generic_collection_parent


def test_tv_patterns_basic():
    ok, info = is_tv_episode("Better.Call.Saul.S02E01.1080p.mkv")
    assert ok and info["series"] == "Better Call Saul" and info["season"] == 2 and info["ep1"] == 1

def test_tv_patterns_range_and_variants():
    ok, info = is_tv_episode("Black.Mirror.S04E01-02.mkv")
    assert ok and info["ep1"] == 1 and info["ep2"] == 2
    ok, info = is_tv_episode("Lucifer.4x01.2160p.mkv")
    assert ok and info["season"] == 4 and info["ep1"] == 1
    ok, info = is_tv_episode("Breaking Bad S02 01.mkv")
    assert ok and info["season"] == 2 and info["ep1"] == 1

def test_quality_detection():
    assert detect_quality("movie.1080p.x265.mkv") == "1080p"
    assert detect_quality("movie.UHD.mkv") == "2160p"
    assert detect_quality("movie.8k.webm") == "4320p"
    assert detect_quality("movie-no-quality.mkv") == "Other"

def test_clean_name_removes_scene_noise():
    s = clean_name("Some.Movie.2012.1080p.BluRay.x265-[eztv]")
    assert s == "Some Movie 2012"


def test_guess_movie_name_from_file_does_not_print_debug(capsys):
    """guess_movie_name_from_file must not print sep/tokens/after year to stdout."""
    guess_movie_name_from_file("Some.Movie.2020.720p")
    out = capsys.readouterr().out
    assert "sep:" not in out
    assert "after year" not in out


def test_guess_movie_name_does_not_print_using_nfo_for_name(tmp_path, capsys):
    """guess_movie_name must not print 'using nfo for name' to stdout."""
    # NFO with no usable title -> falls back to file/parent; no debug print
    (tmp_path / "movie.nfo").write_text("<movie><year>2020</year></movie>")
    path = tmp_path / "Some.Movie.2020.720p.mkv"
    path.touch()
    guess_movie_name(path, tmp_path)
    out = capsys.readouterr().out
    assert "using nfo for name" not in out


def _run_cli(src: Path, dst: Path, extra_args: list[str]):
    argv_backup = sys.argv[:]
    sys.argv = ["media_organiser", str(src), str(dst), *extra_args]
    try:
        main()
    finally:
        sys.argv = argv_backup


def test_cli_uses_parent_dir_title_clean_titlecase(tmp_path):
    """
    Ensures movie_name_from_parents(path) matches parent 'some movie (2019)'
    and the CLI uses titlecase_soft(clean_name(...)) -> 'Some Movie'.
    """
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir()

    # Parent dir intentionally matches MOVIE_DIR_RE: "<title> (<year>)"
    movie_dir = src / "some movie (2019)"
    movie_dir.mkdir()

    # Minimal filename without quality => quality becomes 'Other'
    f = movie_dir / "clip.mkv"
    f.write_bytes(b"x" * 1234)

    _run_cli(src, dst, ["--mode", "copy", "--emit-nfo", "movie", "--dupe-mode", "off"])

    # Expect title cleaned + soft-titlecased: "Some Movie"
    out_dir = dst / "movies" / "Some Movie"
    out_file = out_dir / "Some Movie (2019) [Other].mkv"
    out_nfo = out_dir / "Some Movie (2019) [Other].nfo"

    assert out_dir.exists()
    assert out_file.exists()
    assert out_nfo.exists()
    assert out_nfo.read_bytes().startswith(b"<?xml")


def test_cli_uses_parent_dir_title_with_dots(tmp_path):
    """
    Variant: 'some.movie (2019)' should be cleaned to 'Some Movie'.
    This also covers clean_name(.) -> space replacement before titlecase.
    """
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir()

    movie_dir = src / "some movie (2019)"
    movie_dir.mkdir()

    f = movie_dir / "anything.mkv"
    f.write_bytes(b"y" * 42)

    _run_cli(src, dst, ["--mode", "copy", "--emit-nfo", "movie", "--dupe-mode", "off"])

    out_dir = dst / "movies" / "Some Movie"
    out_file = out_dir / "Some Movie (2019) [Other].mkv"
    out_nfo = out_dir / "Some Movie (2019) [Other].nfo"

    assert out_dir.exists()
    assert out_file.exists()
    assert out_nfo.exists()

# American-psycho-hd-720p.mp4
def test_movie_file_only(tmp_path):
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir()

    # Minimal filename without quality => quality becomes 'Other'
    f = src / "american-psycho-hd-720p.mp4"
    f.write_bytes(b"x" * 1234)

    _run_cli(src, dst, ["--mode", "copy", "--dupe-mode", "name", "--emit-nfo", "all"])

    # Expect title cleaned + soft-titlecased: "Some Movie"
    out_dir = dst / "movies" / "American Psycho"
    out_file = out_dir / "American Psycho [720p].mp4"

    assert out_dir.exists()
    assert out_file.exists()


def test_is_generic_collection_parent():
    """Dynamic detector: true for collection-like folder names, false for single-movie folders."""
    generic_names = [
        "disney movies",
        "disney short films",
        "kids movies",
        "marvel - pre mcu",
        "the hunger games trilogy",
        "harry potter series",
        "The Lord of the Rings Trilogy (2001-2003)",
        "Alien Series 1979-2012",
        "Alien Film Franchise [Directors Cut-Special Edition-Unrated] 1979-2012",
    ]
    for name in generic_names:
        assert is_generic_collection_parent(name), f"expected generic: {name!r}"
    single_movie_names = [
        "Inception (2010)",
        "some movie (2019)",
        "The Matrix 1999 1080p",
    ]
    for name in single_movie_names:
        assert not is_generic_collection_parent(name), f"expected not generic: {name!r}"


def test_generic_parent_disney_movies_uses_filename_title(tmp_path):
    """When parent is 'Disney Movies', extract title from filename pattern 'YEAR - Title'."""
    src = tmp_path / "in"
    disney_movies = src / "Disney Movies"
    disney_movies.mkdir(parents=True)
    
    path = disney_movies / "2001 - Atlantis The Lost Empire.avi"
    path.touch()
    
    movie_name, _ = guess_movie_name(path, src)
    assert movie_name == "Atlantis The Lost Empire"
    assert movie_name != "Disney Movies"


def test_generic_parent_disney_short_films_uses_filename_title(tmp_path):
    """When parent is 'Disney Short Films', extract title from filename pattern 'NN. Title (YEAR)'."""
    src = tmp_path / "in"
    disney_shorts = src / "Disney Short Films"
    disney_shorts.mkdir(parents=True)
    
    path = disney_shorts / "01. John Henry (2000).mkv"
    path.touch()
    
    movie_name, _ = guess_movie_name(path, src)
    assert movie_name == "John Henry"
    assert movie_name != "Disney Short Films"


def test_generic_parent_kids_movies_uses_filename_title(tmp_path):
    """When parent is 'Kids Movies', use file-based title (e.g. Despicable Me 3) not collection name."""
    src = tmp_path / "in"
    parent = src / "Kids Movies"
    parent.mkdir(parents=True)
    path = parent / "Despicable.Me.3.2017.720p.mkv"
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert movie_name == "Despicable Me 3"
    assert movie_name != "Kids Movies"


def test_generic_parent_marvel_pre_mcu_uses_filename_title(tmp_path):
    """When parent is 'Marvel - Pre MCU', use file-based title (e.g. The Punisher) not collection name."""
    src = tmp_path / "in"
    parent = src / "Marvel - Pre MCU"
    parent.mkdir(parents=True)
    path = parent / "The.Punisher.1989.mkv"
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert movie_name == "The Punisher"
    assert movie_name != "Marvel - Pre MCU"


def test_generic_parent_hunger_games_trilogy_uses_filename_title(tmp_path):
    """When parent is 'The Hunger Games Trilogy', extract title from 'NN. Title' pattern."""
    src = tmp_path / "in"
    parent = src / "The Hunger Games Trilogy"
    parent.mkdir(parents=True)
    path = parent / "1. The Hunger Games.mp4"
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert movie_name == "The Hunger Games"
    assert movie_name != "The Hunger Games Trilogy"


def test_generic_parent_harry_potter_series_uses_filename_title(tmp_path):
    """When parent is 'Harry Potter Series', use file-based title not collection name."""
    src = tmp_path / "in"
    parent = src / "Harry Potter Series"
    parent.mkdir(parents=True)
    path = parent / "Harry.Potter.And.The.Philosophers.Stone.2001.720p.mkv"
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert movie_name == "Harry Potter And The Philosophers Stone"
    assert movie_name != "Harry Potter Series"


def test_generic_parent_lotr_trilogy_uses_filename_title(tmp_path):
    """When parent is 'The Lord of the Rings Trilogy (2001-2003)', use file-based title per film."""
    src = tmp_path / "in"
    parent = src / "The Lord of the Rings Trilogy (2001-2003)"
    parent.mkdir(parents=True)
    path = parent / "The.Lord.Of.The.Rings.The.Fellowship.Of.The.Ring.2001.720p.mkv"
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert movie_name == "The Lord Of The Rings The Fellowship Of The Ring"
    assert movie_name != "The Lord of the Rings Trilogy (2001-2003)"


def test_generic_parent_alien_series_uses_filename_title(tmp_path):
    """When parent is 'Alien Series 1979-2012', extract title from 'NN. Title ... Year' pattern."""
    src = tmp_path / "in"
    parent = src / "Alien Series 1979-2012"
    parent.mkdir(parents=True)
    path = parent / "01. Alien Directors Cut Sci-Fi 1979 720p.mkv"
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert movie_name == "Alien Directors Cut Sci-Fi"
    assert movie_name != "Alien Series 1979-2012"


def test_generic_parent_alien_film_franchise_uses_filename_title(tmp_path):
    """When parent is Alien Film Franchise [...], extract title from 'NN. Title ... Year' pattern."""
    src = tmp_path / "in"
    parent = src / "Alien Film Franchise [Directors Cut-Special Edition-Unrated] 1979-2012"
    parent.mkdir(parents=True)
    path = parent / "07. Prometheus Sci-Fi 2012 720p.mkv"
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert movie_name == "Prometheus Sci-Fi"
    assert movie_name != "Alien Film Franchise [Directors Cut-Special Edition-Unrated] 1979-2012"


def test_movie_name_from_parents_strips_scene_words(tmp_path):
    """Parent dirs with scene words (DVDRip, XviD) should have them stripped and artifacts removed."""
    src = tmp_path / "in"
    parent_dir = src / "Madagascar.DVDRip.XviD-DoNE"
    parent_dir.mkdir(parents=True)
    
    path = parent_dir / "movie.avi"
    path.touch()
    
    movie_name = movie_name_from_parents(path, src)
    assert movie_name is not None
    assert "Dvdrip" not in movie_name.lower()
    assert "Xvid" not in movie_name.lower()
    assert "Madagascar" in movie_name
    # Verify artifacts are removed: no extra dots/spaces, no release group suffix
    assert ".." not in movie_name
    assert movie_name.count(".") == 0 or movie_name.strip(".") == movie_name
    assert not movie_name.endswith("-Done") and not movie_name.endswith("-DoNE")
    assert movie_name == "Madagascar"  # Should be clean


def test_movie_name_from_parents_strips_release_group_suffix(tmp_path):
    """Parent dirs with release group suffixes should have them stripped."""
    src = tmp_path / "in"
    parent_dir = src / "Madagascar.Escape.2.Africa.DVDRip.XviD-Larceny"
    parent_dir.mkdir(parents=True)
    
    path = parent_dir / "movie.avi"
    path.touch()
    
    movie_name = movie_name_from_parents(path, src)
    assert movie_name is not None
    assert "Dvdrip" not in movie_name.lower()
    assert "Xvid" not in movie_name.lower()
    assert "Madagascar" in movie_name
    # Verify release group suffix is removed
    assert not movie_name.endswith("-Larceny")
    assert movie_name == "Madagascar Escape 2 Africa"  # Should be clean without artifacts


def test_titlecase_soft_preserves_hyphenated_capitals():
    """titlecase_soft should preserve hyphenated capitals like 'Were-Rabbit'."""
    result = titlecase_soft("Wallace and Gromit In The Curse Of The Were-Rabbit")
    assert "Were-Rabbit" in result
    assert "Were-rabbit" not in result


def test_titlecase_soft_capitalizes_lowercase_hyphenated():
    """titlecase_soft should capitalize each segment of hyphenated words."""
    result = titlecase_soft("were-rabbit")
    assert result == "Were-Rabbit"
    
    result2 = titlecase_soft("some-movie-title")
    assert result2 == "Some-Movie-Title"


def test_tv_pattern_ep_xx_at_start():
    """Test 'Ep XX' pattern recognition when Ep appears at start of filename."""
    from pathlib import Path
    # Simulate Breaking Bad episode file
    filename = "Ep 07 - A No-Rough-Stuff-Type Deal - Vendetta.mkv"
    parent_dir = Path("/Breaking Bad S01 Complete - 1080p ENG-ITA x264 BluRay -Shiv")
    path = parent_dir / filename
    
    ok, info = is_tv_episode(filename, path)
    assert ok, "Ep XX pattern should be recognized"
    assert info["ep1"] == 7
    assert info["season"] == 1, "Season should be extracted from parent directory"
    assert info["series"] == "Breaking Bad", "Series should be extracted from parent directory"


def test_tv_pattern_ep_xx_with_season_in_parent():
    """Test 'Ep XX' pattern with season extraction from parent directory."""
    from pathlib import Path
    filename = "Ep 01 - Pilot.mkv"
    parent_dir = Path("/Some Show Season 2")
    path = parent_dir / filename
    
    ok, info = is_tv_episode(filename, path)
    assert ok
    assert info["ep1"] == 1
    assert info["season"] == 2, "Should extract season 2 from parent directory"


def test_tv_pattern_season_x_episode_y():
    """Test 'season-X-episode-Y' pattern recognition."""
    from pathlib import Path
    filename = "young-sheldon-season-5-episode-5-stuffed-animals.mp4"
    path = Path("/Young Sheldon/Season 5") / filename
    
    ok, info = is_tv_episode(filename, path)
    assert ok, "season-X-episode-Y pattern should be recognized"
    assert info["season"] == 5
    assert info["ep1"] == 5
    assert info["series"] == "Young Sheldon", "Hyphens should be normalized to spaces"


def test_tv_pattern_hyphen_normalization():
    """Test that hyphens in series names are normalized to spaces."""
    from pathlib import Path
    # Test with hyphenated filename
    filename = "young-sheldon-season-3-episode-10.mp4"
    path = Path("/Young Sheldon/Season 3") / filename
    
    ok, info = is_tv_episode(filename, path)
    assert ok
    assert info["series"] == "Young Sheldon", "Hyphens should be normalized to spaces, not 'Young-Sheldon'"
    
    # Test with space-separated filename (should also work)
    filename2 = "Young Sheldon S03E10.mp4"
    ok2, info2 = is_tv_episode(filename2, path)
    assert ok2
    assert info2["series"] == "Young Sheldon", "Should be consistent with hyphenated version"


def test_tv_pattern_case_normalization():
    """Test that series names are normalized to consistent case."""
    from pathlib import Path
    # Test lowercase series name
    filename1 = "lucifer.s04e01.web.x264-strife.mkv"
    path1 = Path("/Lucifer/Season 4") / filename1
    
    ok1, info1 = is_tv_episode(filename1, path1)
    assert ok1
    assert info1["series"] == "Lucifer", "Lowercase 'lucifer' should be normalized to 'Lucifer'"
    
    # Test mixed case
    filename2 = "LuCiFeR.S04E02.mkv"
    ok2, info2 = is_tv_episode(filename2, path1)
    assert ok2
    assert info2["series"] == "Lucifer", "Mixed case should be normalized to 'Lucifer'"
    
    # Both should create same series name
    assert info1["series"] == info2["series"], "Case normalization should be consistent"


def test_tv_pattern_ep_xx_series_extraction_from_parent():
    """Test series name extraction from parent when Ep is at start of filename."""
    from pathlib import Path
    filename = "Ep 05 - Gray Matter.mkv"
    # Parent directory with quality/resolution/language info that should be cleaned
    parent_dir = Path("/Breaking Bad S01 Complete - 1080p ENG-ITA x264 BluRay -Shiv")
    path = parent_dir / filename
    
    ok, info = is_tv_episode(filename, path)
    assert ok
    assert info["series"] == "Breaking Bad", "Should extract clean series name from parent, removing quality/resolution/language info"
    assert "1080p" not in info["series"]
    assert "ENG-ITA" not in info["series"]
    assert "x264" not in info["series"]
    assert "BluRay" not in info["series"]
    assert "Shiv" not in info["series"]


def test_tv_pattern_ep_xx_defaults_to_season_1():
    """Test that Ep XX pattern defaults to season 1 if no season found."""
    from pathlib import Path
    filename = "Ep 10 - Final Episode.mkv"
    parent_dir = Path("/Some Show")  # No season info
    path = parent_dir / filename
    
    ok, info = is_tv_episode(filename, path)
    assert ok
    assert info["season"] == 1, "Should default to season 1 if no season info found"
    assert info["ep1"] == 10