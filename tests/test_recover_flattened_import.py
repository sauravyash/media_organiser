# tests/test_recover_flattened_import.py
import importlib.util
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "recover_flattened_import.py"
_spec = importlib.util.spec_from_file_location("recover_flattened_import", _SCRIPT)
recover = importlib.util.module_from_spec(_spec)
# @dataclass resolves annotations through sys.modules, so register before executing
sys.modules[_spec.name] = recover
_spec.loader.exec_module(recover)


def _collapsed(folder: Path, name: str, original: str | None, size: int = 512,
               nfo_size: int | None = None) -> Path:
    """One file as the bad import left it: collapsed name, NFO only if it got one."""
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / name
    video.write_bytes(b"x" * size)
    if original is not None:
        video.with_suffix(".nfo").write_text(
            '<?xml version="1.0" encoding="utf-8"?><movie>'
            f"<title>F</title><size>{nfo_size if nfo_size is not None else size}</size>"
            f"<originalfilename>{Path(original).name}</originalfilename>"
            f"<sourcepath>{original}</sourcepath></movie>"
        )
    return video


@pytest.fixture
def library(tmp_path):
    return tmp_path / "movies"


def test_plan_recovers_title_year_and_quality(library):
    folder = library / "F"
    _collapsed(folder, "F (1977) [1080p].mp4", "/import/F/Star.Wars.1977.1080p.BluRay.mp4")

    (p,) = recover.plan(folder, library)
    assert p.status == "recoverable"
    assert p.title == "Star Wars"
    assert p.year == "1977"
    assert p.dest == library / "Star Wars" / "Star Wars (1977) [1080p].mp4"


def test_plan_keeps_multipart_suffix(library):
    folder = library / "F"
    _collapsed(folder, "F (2008) [Other] CD 1.mp4", "/import/F/Slumdog.Millionaire.2008.CD1.mp4")

    (p,) = recover.plan(folder, library)
    assert p.dest.name == "Slumdog Millionaire (2008) [Other] CD 1.mp4"


def test_plan_marks_files_without_an_nfo_unidentified(library):
    folder = library / "F"
    _collapsed(folder, "F (1999) [Other] (5).mp4", None)

    (p,) = recover.plan(folder, library)
    assert p.status == "unidentified"
    assert "original filename lost" in p.note


def test_plan_flags_an_nfo_that_describes_a_different_file(library):
    """A collided file could inherit an NFO written for its neighbour; never move it blindly."""
    folder = library / "F"
    _collapsed(folder, "F (1996) [Other] (2).mp4", "/import/F/Trainspotting.1996.mp4",
               size=999, nfo_size=512)

    (p,) = recover.plan(folder, library)
    assert p.status == "needs-review"
    assert p.dest == library / "Trainspotting" / "Trainspotting (1996) [Other].mp4"


def test_shared_dump_folder_is_never_used_as_a_title(library):
    """Every source shares one parent, so that parent names nothing."""
    folder = library / "F"
    _collapsed(folder, "F (2005) [Other].mp4", "/import/F/movie.mp4")

    (p,) = recover.plan(folder, library)
    assert p.status == "unidentified"
    assert "could not derive a title" in p.note


def test_apply_moves_video_and_rewrites_its_nfo(library):
    folder = library / "F"
    video = _collapsed(folder, "F (1996) [Other].mp4", "/import/F/Fargo (1996).mp4")

    plans = recover.plan(folder, library)
    moved, quarantined = recover.apply(plans, quarantine=None, include_flagged=False)

    assert (moved, quarantined) == (1, 0)
    assert not video.exists()
    dest = library / "Fargo" / "Fargo (1996) [Other].mp4"
    assert dest.exists()

    root = ET.fromstring(dest.with_suffix(".nfo").read_bytes())
    assert root.findtext("title") == "Fargo"
    assert root.findtext("year") == "1996"
    assert root.findtext("filenameandpath") == str(dest)
    assert root.findtext("sourcepath") == "/import/F/Fargo (1996).mp4"  # provenance kept


def test_apply_holds_back_flagged_files_unless_asked(library):
    folder = library / "F"
    video = _collapsed(folder, "F (1996) [Other] (2).mp4", "/import/F/Trainspotting.1996.mp4",
                       size=999, nfo_size=512)

    assert recover.apply(recover.plan(folder, library), None, include_flagged=False) == (0, 0)
    assert video.exists()

    assert recover.apply(recover.plan(folder, library), None, include_flagged=True) == (1, 0)
    assert (library / "Trainspotting" / "Trainspotting (1996) [Other].mp4").exists()


def test_apply_quarantines_unidentified_files_when_asked(library):
    folder = library / "F"
    _collapsed(folder, "F (1999) [Other] (5).mp4", None)
    quarantine = library / "_unidentified"

    moved, quarantined = recover.apply(recover.plan(folder, library), quarantine, False)

    assert (moved, quarantined) == (0, 1)
    assert (quarantine / "F (1999) [Other] (5).mp4").exists()


def test_report_lists_every_file(library, tmp_path):
    folder = library / "F"
    _collapsed(folder, "F (1996) [Other].mp4", "/import/F/Fargo (1996).mp4")
    _collapsed(folder, "F (1999) [Other] (5).mp4", None)

    report = tmp_path / "recovery.tsv"
    recover.write_report(recover.plan(folder, library), report)

    lines = report.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t")[0] == "status"
    assert len(lines) == 3
    assert any("Fargo" in line for line in lines[1:])
