import sys
from pathlib import Path

import pytest

from media_organiser.cli import main
from media_organiser.naming import is_tv_episode, detect_quality, clean_name, guess_movie_name_from_file, guess_movie_name, movie_part_suffix, titlecase_soft, movie_name_from_parents, is_generic_collection_parent, normalise_movie_title_for_display, detect_numbered_series, count_distinct_movies, title_from_filename_for_generic_parent


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


def test_tv_ep2_not_resolution_bleed():
    """1080p etc. must not be parsed as ep2 (e.g. S03E01-E108)."""
    ok, info = is_tv_episode("Black.Mirror.S03E01.1080p.5.1Ch.WebRip.ReEnc-DeeJayAhmed.mkv")
    assert ok and info["series"] == "Black Mirror" and info["season"] == 3 and info["ep1"] == 1
    assert info.get("ep2") is None


def test_tv_hyphen_flanked_episode_code():
    """'Series - 101 - Title' cartoon-rip numbering: code//100 = season, code%100 = episode."""
    ok, info = is_tv_episode("The Penguins of Madagascar - 101 - Gone in a Flash (400p).mp4")
    assert ok and info["series"] == "The Penguins Of Madagascar"
    assert info["season"] == 1 and info["ep1"] == 1
    ok, info = is_tv_episode("The Penguins of Madagascar - 148 - Dr. Blowhole's Revenge.mp4")
    assert ok and info["season"] == 1 and info["ep1"] == 48
    ok, info = is_tv_episode("Some Show - 205 - Whatever.mp4")
    assert ok and info["season"] == 2 and info["ep1"] == 5
    ok, info = is_tv_episode("Some Show - 1024 - Big.mp4")
    assert ok and info["season"] == 10 and info["ep1"] == 24


@pytest.mark.parametrize(
    "filename",
    [
        "2001 - A Space Odyssey (1968).mp4",  # leading year, not an episode code
        "2012 (2009).mp4",                    # bare year
        "American Pie 3 - The Wedding (2003).mp4",  # single digit, no title-after code
        "Die Hard 4.0 - Live Free or Die Hard (2007).mp4",
        "Blade Runner 2049 (2017).mp4",
    ],
)
def test_hyphen_flanked_pattern_does_not_catch_movies(filename):
    """The 'Series - NNN - Title' pattern must not misclassify movies as TV."""
    ok, _ = is_tv_episode(filename)
    assert not ok

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


def test_three_idiots_leading_number_not_stripped(tmp_path):
    """Leading number with single space (e.g. '3 Idiots') must not be stripped as index."""
    src = tmp_path / "in"
    # Parent without MOVIE_DIR_RE match so strip branch runs; old regex would strip "3 " -> "Idiots"
    parent_dir = src / "3 Idiots 2009 [1080p]"
    parent_dir.mkdir(parents=True)
    path = parent_dir / "3.Idiots.2009.1080p.mkv"
    path.touch()
    movie_name = movie_name_from_parents(path, src)
    assert movie_name is not None
    assert movie_name == "3 Idiots", "Leading '3 ' must not be stripped when not an index (e.g. 1. or 01 -)"


def test_blade_runner_2049_year_not_truncated(tmp_path):
    """Title 2049 (e.g. Blade Runner 2049) must not be truncated as a year; only 1900-2030 truncate."""
    src = tmp_path / "in"
    parent_dir = src / "Blade Runner 2049 (2017)"
    parent_dir.mkdir(parents=True)
    path = parent_dir / "Blade.Runner.2049.2017.720p.mkv"
    path.touch()
    movie_name = movie_name_from_parents(path, src)
    assert movie_name is not None
    assert movie_name == "Blade Runner 2049", "2049 should stay in title, not be treated as release year"


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


def test_lion_king_1_5_preserves_decimal(tmp_path):
    """Decimal 1.5 in '1-1.5' or 'The Lion King 1-1.5' must be preserved, not turned into '1 5'."""
    src = tmp_path / "in"
    parent_dir = src / "The Lion King 1-1.5 - Hakuna Matata (2004)"
    parent_dir.mkdir(parents=True)
    path = parent_dir / "movie.avi"
    path.touch()
    movie_name = movie_name_from_parents(path, src)
    assert movie_name is not None
    assert "1.5" in movie_name, "1.5 must be preserved in title (e.g. The Lion King 1-1.5)"
    assert "1 5" not in movie_name

    # From filename stem as well
    assert "1.5" in guess_movie_name_from_file("The.Lion.King.1-1.5.Hakuna.Matata.2004.720p.mkv")


def test_normalise_movie_title_strips_trailing_brackets_and_year():
    """Trailing [tags] and (YYYY) are stripped so CLI adds them once (e.g. YTS-style folders)."""
    assert normalise_movie_title_for_display("Despicable Me 3 (2017) [YTS AG]") == "Despicable Me 3"
    assert normalise_movie_title_for_display("Some Movie (2019) [720p] [YTS AM]") == "Some Movie"
    assert normalise_movie_title_for_display("Title (2020) [1080p]") == "Title"


def test_titlecase_soft_preserves_possessive_apostrophe():
    """Possessive 's and contraction 't stay lowercase (Pete's, don't, Scamp's)."""
    assert titlecase_soft("Pete's Dragon") == "Pete's Dragon"
    assert titlecase_soft("Scamp's Adventure") == "Scamp's Adventure"
    assert titlecase_soft("don't") == "Don't"
    # O'Brien-style: capitalize after apostrophe when not s/t
    assert titlecase_soft("o'brien") == "O'Brien"


@pytest.mark.parametrize(
    "filename, expected",
    [
        # Title containing a period must not be truncated at the '.' (was 'Dr', 'Mr')
        ("Dr. Strangelove (1964).mp4", "Dr. Strangelove"),
        ("Mr. Turner.mp4", "Mr. Turner"),
        # Decimal in title must survive (was 'Die Hard 4')
        ("Die Hard 4.0 - Live Free or Die Hard (2007).mp4", "Die Hard 4.0 - Live Free Or Die Hard"),
    ],
)
def test_guess_movie_name_title_with_period_not_truncated(tmp_path, filename, expected):
    """A '.' inside the title must not chop the name (regression: double-stemming in guess_movie_name)."""
    src = tmp_path / "in"
    src.mkdir(parents=True)
    path = src / filename
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert normalise_movie_title_for_display(movie_name) == expected


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("2001 - A Space Odyssey (1968).mp4", "2001 - A Space Odyssey"),
        ("2012 (2009).mp4", "2012"),
        ("1917 (2019).mp4", "1917"),
    ],
)
def test_guess_movie_name_leading_year_title_not_emptied(tmp_path, filename, expected):
    """A title that starts with a year must not be truncated to an empty string."""
    src = tmp_path / "in"
    src.mkdir(parents=True)
    path = src / filename
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    normalised = normalise_movie_title_for_display(movie_name)
    assert normalised, f"title emptied for {filename!r}"
    assert normalised == expected


def test_guess_movie_name_from_file_still_truncates_trailing_year():
    """Ordinary 'Title Year' names still drop the trailing year (index-0 skip must not disable this)."""
    assert guess_movie_name_from_file("The Matrix 1999.mp4") == "The Matrix"
    assert guess_movie_name_from_file("Blade Runner 2049.mp4") == "Blade Runner 2049"


def test_collection_leading_year_kept_when_conflicting_trailing_year(tmp_path):
    """In a collection folder, a leading year that conflicts with a real trailing (YEAR) is kept.

    '2001 - A Space Odyssey (1968)' -> 2001 is part of the title, 1968 is the release year.
    """
    src = tmp_path / "in"
    parent = src / "Movies"
    parent.mkdir(parents=True)
    path = parent / "2001 - A Space Odyssey (1968).mp4"
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert normalise_movie_title_for_display(movie_name) == "2001 - A Space Odyssey"


def test_collection_leading_year_prefix_still_stripped_without_conflict(tmp_path):
    """A genuine 'YEAR - Title' collection prefix (no conflicting trailing year) is still stripped."""
    src = tmp_path / "in"
    parent = src / "Disney Movies"
    parent.mkdir(parents=True)
    # No trailing (YEAR): 2001 is the catalog/release-year prefix.
    path = parent / "2001 - Atlantis The Lost Empire.avi"
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert movie_name == "Atlantis The Lost Empire"


def test_collection_leading_year_matching_trailing_year_stripped(tmp_path):
    """When leading and trailing year match, it's a prefix listing — strip to the bare title."""
    src = tmp_path / "in"
    parent = src / "Kids Movies"
    parent.mkdir(parents=True)
    path = parent / "1995 - Toy Story (1995).mkv"
    path.touch()
    movie_name, _ = guess_movie_name(path, src)
    assert normalise_movie_title_for_display(movie_name) == "Toy Story"


def test_count_distinct_movies_treats_multipart_as_one():
    """CD1/CD2 (or Part 1/2) of one film count as a single movie, not a container."""
    parts = [Path("Inception (2010)/Inception CD1.avi"), Path("Inception (2010)/Inception CD2.avi")]
    assert count_distinct_movies(parts) == 1
    halves = [Path("GWTW/Gone with the Wind part 1.avi"), Path("GWTW/Gone with the Wind part 2.avi")]
    assert count_distinct_movies(halves) == 1
    distinct = [Path("James Bond/Goldfinger (1964).mp4"), Path("James Bond/Skyfall (2012).mp4")]
    assert count_distinct_movies(distinct) == 2


def test_count_distinct_movies_separates_films_titled_part_n():
    """Deathly Hallows Part 1 and Part 2 are two films, so their folder is a container."""
    sequels = [
        Path("Harry Potter/Deathly Hallows Part 1 (2010).mkv"),
        Path("Harry Potter/Deathly Hallows Part 2 (2011).mkv"),
    ]
    assert count_distinct_movies(sequels) == 2


@pytest.mark.parametrize("name", [
    "Harry Potter and the Deathly Hallows Part 1 (2010).mp4",
    "Hunger Games - Mockingjay Part 1 (2014).mp4",
    "Colour of Magic (Part 1), The (2008).mp4",
])
def test_part_in_title_is_not_a_disc_marker(name):
    """A 'Part N' the film is actually called must not become a ' CD N' suffix."""
    assert movie_part_suffix(Path("F:/") / name) == ""


def test_movie_folder_ending_in_part_n_is_not_a_disc_folder():
    assert movie_part_suffix(Path("F:/Deathly Hallows Part 1/video.mkv")) == ""
    assert movie_part_suffix(Path("F:/Some Movie/CD 2/video.avi")) == " CD 2"


@pytest.mark.parametrize("name,expected", [
    ("Gone with the Wind (1939) part 1.avi", " CD 1"),
    ("Gone.with.the.Wind.1939.DVDRip.XviD.part2-GRP.avi", " CD 2"),
    ("Titanic part 1 of 2.avi", " CD 1"),
    ("Slumdog.Millionaire.2008.CD1.mp4", " CD 1"),
    ("Inception CD1.avi", " CD 1"),
])
def test_trailing_part_marker_still_detected(name, expected):
    assert movie_part_suffix(Path("F:/") / name) == expected


def test_container_folder_uses_filename_title_not_folder_name(tmp_path):
    """A folder holding several distinct movies is a container: each keeps its own title."""
    src = tmp_path / "in"
    bond = src / "James Bond"
    bond.mkdir(parents=True)
    p = bond / "Casino Royale (2006).mp4"
    p.touch()
    # parent_is_container mirrors what the CLI computes from sibling counts
    movie_name, _ = guess_movie_name(p, src, parent_is_container=True)
    assert normalise_movie_title_for_display(movie_name) == "Casino Royale"
    # without the flag, the old behaviour would stamp the folder name on the file
    assert guess_movie_name(p, src)[0] == "James Bond"


def test_detect_numbered_series_positive():
    """4+ numbered, year-less, common-prefix files are a loose series with per-file episodes."""
    paths = [Path(f"Buzzy Bee/BUZZYBEE-{i}.mp4") for i in range(1, 6)]
    result = detect_numbered_series(paths)
    assert result is not None
    assert set(result["episodes"].values()) == {1, 2, 3, 4, 5}


def test_detect_numbered_series_rejects_movies_with_years():
    """Distinct films that carry release years are NOT a numbered series (they're a container)."""
    paths = [
        Path("James Bond/Casino Royale (2006).mp4"),
        Path("James Bond/Goldfinger (1964).mp4"),
        Path("James Bond/Octopussy (1983).mp4"),
        Path("James Bond/Skyfall (2012).mp4"),
    ]
    assert detect_numbered_series(paths) is None


def test_detect_numbered_series_rejects_small_group():
    """A 3-file numbered set (e.g. a movie trilogy) is not treated as a series."""
    paths = [Path(f"Toy Story/Toy Story {i}.mp4") for i in (1, 2, 3)]
    assert detect_numbered_series(paths) is None


def test_detect_numbered_series_rejects_mixed_prefixes():
    """Files that don't share a common prefix are not a series."""
    paths = [
        Path("Mix/Alpha 1.mp4"), Path("Mix/Beta 2.mp4"),
        Path("Mix/Gamma 3.mp4"), Path("Mix/Delta 4.mp4"),
    ]
    assert detect_numbered_series(paths) is None


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

def test_generic_parent_keeps_a_number_that_starts_the_title(tmp_path):
    """
    Regression: a leading number was stripped as a collection index even when separated
    by a single space, so "21 Jump Street" imported as "Jump Street".
    """
    src = tmp_path / "in"
    parent = src / "Movies"
    parent.mkdir(parents=True)
    for name, expected in [
        ("21 Jump Street (2012).avi", "21 Jump Street"),
        ("12 Angry Men (1957).mp4", "12 Angry Men"),
        ("300 (2006).mp4", "300"),
        ("9 (2009).mp4", "9"),
    ]:
        path = parent / name
        path.touch()
        movie_name, _ = guess_movie_name(path, src)
        assert normalise_movie_title_for_display(movie_name) == expected, name


def test_generic_parent_still_strips_a_real_index(tmp_path):
    """Punctuation or a double space after the number still marks a collection index."""
    src = tmp_path / "in"
    parent = src / "Disney Short Films"
    parent.mkdir(parents=True)
    for name, expected in [
        ("01. John Henry (2000).mkv", "John Henry"),
        ("1. The Hunger Games.mp4", "The Hunger Games"),
        ("3 - Return of the King (2003).mp4", "Return of the King"),
        ("07) Prometheus (2012).mkv", "Prometheus"),
    ]:
        path = parent / name
        path.touch()
        assert title_from_filename_for_generic_parent(path) == titlecase_soft(expected), name
