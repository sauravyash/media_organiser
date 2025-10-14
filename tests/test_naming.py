from media_organiser.naming import is_tv_episode, detect_quality, clean_name

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
