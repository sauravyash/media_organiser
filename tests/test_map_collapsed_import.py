# tests/test_map_collapsed_import.py
import csv
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "map_collapsed_import.py"
_spec = importlib.util.spec_from_file_location("map_collapsed_import", _SCRIPT)
mapper = importlib.util.module_from_spec(_spec)
# @dataclass resolves annotations through sys.modules, so register before executing
sys.modules[_spec.name] = mapper
_spec.loader.exec_module(mapper)


@pytest.fixture
def trees(tmp_path):
    source = tmp_path / "F"
    collapsed = tmp_path / "movies" / "F"
    source.mkdir(parents=True)
    collapsed.mkdir(parents=True)
    return source, collapsed


def _put(folder: Path, name: str, data: bytes) -> Path:
    p = folder / name
    p.write_bytes(data)
    return p


def _match(collapsed: Path, source: Path):
    return mapper.match_all(mapper.load(str(collapsed)), mapper.load(str(source)))


def test_unique_size_matches_without_hashing(trees):
    source, collapsed = trees
    _put(source, "Fargo (1996).mp4", b"B" * 6000)
    _put(collapsed, "F (1996) [Other].mp4", b"B" * 6000)

    (m,) = _match(collapsed, source)
    assert m.status == "matched"
    assert m.method == "size"
    assert Path(m.source.path).name == "Fargo (1996).mp4"
    assert m.title == "Fargo (1996)"


def test_size_collision_is_broken_by_fingerprint(trees):
    source, collapsed = trees
    _put(source, "Trainspotting.1996.mp4", b"C" * 7000)
    _put(source, "The Rock (1996).mp4", b"D" * 7000)
    _put(collapsed, "F (1996) [Other].mp4", b"D" * 7000)

    (m,) = _match(collapsed, source)
    assert m.status == "matched"
    assert m.method == "fingerprint"
    assert Path(m.source.path).name == "The Rock (1996).mp4"


def test_byte_identical_sources_are_reported_as_interchangeable(trees):
    source, collapsed = trees
    _put(source, "Heat.1995.mp4", b"E" * 8000)
    _put(source, "Heat.1995.copy.mp4", b"E" * 8000)
    _put(collapsed, "F (1995) [Other].mp4", b"E" * 8000)

    (m,) = _match(collapsed, source)
    assert "either source will do" in m.method
    assert len(m.candidates) == 2
    # Which copy is unknowable, but both are the same film, so the name is not in doubt.
    assert m.status == "title-certain"
    assert m.title == "Heat (1995)"


def test_same_film_in_two_places_still_yields_a_certain_title(trees):
    """The real shape: one copy at the root, one in a sub-folder, same size."""
    source, collapsed = trees
    nested = source / "New folder" / "Movies"
    nested.mkdir(parents=True)
    _put(source, "Citizen Kane (1941).mp4", b"K" * 5000)
    _put(nested, "Citizen Kane (1941).mp4", b"K" * 5000)
    _put(collapsed, "F (1941) [Other].mp4", b"K" * 5000)

    (m,) = _match(collapsed, source)
    assert m.status == "title-certain"
    assert m.title == "Citizen Kane (1941)"
    assert "same film" in m.method


def test_trailing_copy_marker_is_not_part_of_the_title(trees):
    """'Metropolis (1927) 2.mp4' is a copy of Metropolis, not a film called '... 2'."""
    source, collapsed = trees
    _put(source, "Metropolis (1927).mp4", b"M" * 5000)
    _put(source, "Metropolis (1927) 2.mp4", b"M" * 5000)
    _put(collapsed, "F (1927) [Other].mp4", b"M" * 5000)

    (m,) = _match(collapsed, source)
    assert m.status == "title-certain"
    assert m.title == "Metropolis (1927)"


def test_reimport_list_picks_the_shallowest_copy(trees, tmp_path):
    source, collapsed = trees
    nested = source / "New folder" / "Movies"
    nested.mkdir(parents=True)
    _put(source, "Citizen Kane (1941).mp4", b"K" * 5000)
    _put(nested, "Citizen Kane (1941).mp4", b"K" * 5000)
    _put(collapsed, "F (1941) [Other].mp4", b"K" * 5000)

    src_entries = mapper.load(str(source))
    matches = mapper.match_all(mapper.load(str(collapsed)), src_entries)
    out = tmp_path / "report"
    mapper.report(matches, src_entries, out)

    lines = (out / "reimport.txt").read_text(encoding="utf-8").split()
    assert "New folder" not in (out / "reimport.txt").read_text(encoding="utf-8")
    assert len([l for l in (out / "reimport.txt").read_text(encoding="utf-8").splitlines() if l]) == 1


def test_file_with_no_source_is_unmatched(trees):
    source, collapsed = trees
    _put(source, "Fargo (1996).mp4", b"B" * 6000)
    _put(collapsed, "F (2001) [Other].mp4", b"Z" * 1234)

    (m,) = _match(collapsed, source)
    assert m.status == "unmatched"


def test_nfo_sourcepath_is_used_as_a_cross_check(trees):
    source, collapsed = trees
    original = _put(source, "Metropolis.1927.mp4", b"A" * 5000)
    video = _put(collapsed, "F (1927) [Other].mp4", b"A" * 5000)
    video.with_suffix(".nfo").write_text(
        f"<movie><title>F</title><sourcepath>{original}</sourcepath></movie>")

    (m,) = _match(collapsed, source)
    assert m.agrees_with_nfo == "yes"


def test_nfo_disagreement_is_surfaced(trees):
    source, collapsed = trees
    _put(source, "Metropolis.1927.mp4", b"A" * 5000)
    decoy = _put(source, "Nosferatu.1922.mp4", b"N" * 4000)
    video = _put(collapsed, "F (1927) [Other].mp4", b"A" * 5000)
    video.with_suffix(".nfo").write_text(
        f"<movie><title>F</title><sourcepath>{decoy}</sourcepath></movie>")

    (m,) = _match(collapsed, source)
    assert m.status == "matched"          # bytes win
    assert "investigate" in m.agrees_with_nfo


def test_inventory_round_trip_matches_across_machines(trees, tmp_path):
    """The cross-machine flow: neither side is readable when the join runs."""
    source, collapsed = trees
    _put(source, "Trainspotting.1996.mp4", b"C" * 7000)
    _put(source, "The Rock (1996).mp4", b"D" * 7000)
    _put(collapsed, "F (1996) [Other].mp4", b"D" * 7000)

    src_tsv, col_tsv = tmp_path / "source.tsv", tmp_path / "collapsed.tsv"
    mapper.write_inventory(mapper.scan(source, "all"), src_tsv)
    mapper.write_inventory(mapper.scan(collapsed, "all"), col_tsv)

    # Simulate the other machine: entries carry recorded fingerprints, not live files.
    src_entries = mapper.read_inventory(src_tsv)
    col_entries = mapper.read_inventory(col_tsv)
    for e in src_entries + col_entries:
        e.live = False
    assert all(e.fingerprint for e in src_entries)

    (m,) = mapper.match_all(col_entries, src_entries)
    assert m.method == "fingerprint"
    assert Path(m.source.path).name == "The Rock (1996).mp4"


def test_missing_fingerprint_tells_you_how_to_fix_it(trees, tmp_path):
    source, collapsed = trees
    _put(source, "Trainspotting.1996.mp4", b"C" * 7000)
    _put(source, "The Rock (1996).mp4", b"D" * 7000)
    _put(collapsed, "F (1996) [Other].mp4", b"D" * 7000)

    tsv = tmp_path / "source.tsv"
    mapper.write_inventory(mapper.scan(source, "none"), tsv)
    src_entries = mapper.read_inventory(tsv)
    col_entries = mapper.scan(collapsed, "none")
    for e in src_entries + col_entries:
        e.live = False

    (m,) = mapper.match_all(col_entries, src_entries)
    assert m.status == "ambiguous"
    assert "--fingerprint all" in m.method


def test_report_lists_sources_this_folder_does_not_account_for(trees, tmp_path):
    source, collapsed = trees
    _put(source, "Fargo (1996).mp4", b"B" * 6000)
    _put(source, "Alien.1979.mp4", b"G" * 9000)      # imported fine, elsewhere
    _put(collapsed, "F (1996) [Other].mp4", b"B" * 6000)

    src_entries = mapper.load(str(source))
    matches = mapper.match_all(mapper.load(str(collapsed)), src_entries)
    out = tmp_path / "report"
    counts = mapper.report(matches, src_entries, out)

    assert counts == {"matched": 1, "title-certain": 0, "ambiguous": 0, "unmatched": 0,
                      "source_elsewhere": 1}
    assert "Alien.1979.mp4" in (out / "source_not_in_this_folder.tsv").read_text(encoding="utf-8")
    assert (out / "reimport.txt").read_text(encoding="utf-8").strip().endswith("Fargo (1996).mp4")

    with (out / "mapping.tsv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert rows[0]["proposed_title"] == "Fargo (1996)"
