import sys
from pathlib import Path

from media_organiser.cli import main
from media_organiser.naming import is_tv_episode, detect_quality, clean_name, guess_movie_name_from_file, guess_movie_name, movie_part_suffix, titlecase_soft, movie_name_from_parents, titlecase_soft, movie_name_from_parents


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