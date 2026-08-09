#!/usr/bin/env python3
"""
Rebuild a movie folder that a bad import collapsed into a single title.

When ``find_nfo`` handed one stray NFO to every video in a flat import folder,
every file was written as ``<StrayTitle> (<Year>) [<Quality>].<ext>`` into one
folder, and colliding names picked up ``(2)``, ``(3)``… suffixes. The NFOs that
did get written still record ``<originalfilename>`` and ``<sourcepath>``, so the
files they belong to can be renamed back.

Where the NFOs are missing - or wrong, being the metadata the bad import wrote -
``--mapping`` takes the identification from ``map_collapsed_import.py`` instead,
which joins the folder against the source tree on file size and content
fingerprint. It covers files with no NFO at all, and outranks the NFO where both
name a source; a disagreement between them is flagged rather than moved.

This re-derives each name with the organiser's own naming functions, so a
recovered file lands exactly where a correct import would have put it.

    # report only (default)
    python scripts/recover_flattened_import.py /data/content/movies/F

    # with the size join doing the identifying
    python scripts/recover_flattened_import.py /data/content/movies/F \\
        --mapping import-mapping/mapping.tsv

    # rename and move for real
    python scripts/recover_flattened_import.py /data/content/movies/F \\
        --mapping import-mapping/mapping.tsv --apply

Unidentified files are left untouched unless --quarantine is given.
"""
from __future__ import annotations

import argparse
import csv
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media_organiser.constants import VIDEO_EXTS  # noqa: E402
from media_organiser.io_ops import as_local_path, as_pure, safe_path  # noqa: E402
from media_organiser.naming import (  # noqa: E402
    detect_quality,
    guess_movie_name_from_file,
    guess_year_for_movie,
    has_copy_marker,
    movie_name_from_parents,
    movie_part_suffix,
    normalise_movie_title_for_display,
    strip_copy_marker,
)
from media_organiser.nfo import xml_indent  # noqa: E402


@dataclass
class Recovery:
    video: Path
    nfo: Optional[Path]
    original: Optional[str]      # originalfilename / sourcepath from the NFO
    title: Optional[str]         # re-derived movie title
    year: Optional[str]
    dest: Optional[Path]         # where it should live
    note: str = ""

    flagged: bool = False        # NFO may describe a different file — do not move blindly
    via: str = ""                # what identified it: "mapping" or "nfo"

    @property
    def status(self) -> str:
        if self.dest is None:
            return "unidentified"
        if self.dest == self.video:
            return "already-correct"
        if self.flagged:
            return "needs-review"
        return "recoverable"


def _read_nfo(nfo: Path) -> dict:
    try:
        root = ET.fromstring(nfo.read_text(errors="ignore"))
    except (ET.ParseError, OSError):
        return {}
    return {el.tag: (el.text or "").strip() for el in root}


# Stems that name a disc or a container, never a film.
_PLACEHOLDER_STEMS = {
    "movie", "video", "film", "main", "untitled", "output", "video_ts", "vts_01_1", "title00",
}


def _derive(original: str, shared_parent: Optional[str]) -> tuple[str, Optional[str], str, str]:
    """Title, year, quality and part suffix for an original path, as the CLI would."""
    # The original was recorded on whichever machine ran the import, so read its
    # separators rather than this machine's. "Casablanca (1942) 2.mp4" is a file
    # manager's copy, and that trailing number is not part of the film's name.
    src = as_local_path(original)
    src = src.with_name(strip_copy_marker(src.stem) + src.suffix)
    if src.stem.strip().lower() in _PLACEHOLDER_STEMS:
        title = ""
    else:
        title = normalise_movie_title_for_display(guess_movie_name_from_file(src.name))

    # A stem like "video_ts" or "movie" carries no title; fall back to the folder it
    # came from - unless every file shares that folder (the flat dump), which names nothing.
    if len(title) < 2 and src.parent.name and src.parent.name != shared_parent:
        by_parent = movie_name_from_parents(src, src_root=src.parent.parent)
        if by_parent:
            title = normalise_movie_title_for_display(by_parent)

    return title, guess_year_for_movie(src), detect_quality(src.name), movie_part_suffix(src)


def _original_first(candidate: str) -> tuple:
    """
    Rank interchangeable copies so the best-named one wins: a name with no copy marker
    first, then the shallowest path - the original rather than something dragged into
    a subfolder.
    """
    pure = as_pure(candidate)
    return (has_copy_marker(pure.stem), candidate.count("\\") + candidate.count("/"), len(candidate))


def read_mapping(tsv: Path) -> dict[str, str]:
    """
    Collapsed filename -> the source file it came from, from map_collapsed_import's
    mapping.tsv. A title-certain row names no single source, but every candidate is the
    same film, so the shallowest one - the original rather than a copy - names it just
    as well. Ambiguous and unmatched rows identify nothing and are left out.
    """
    found: dict[str, str] = {}
    with tsv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            name = (row.get("collapsed_file") or "").strip()
            if not name:
                continue
            source = (row.get("source_path") or "").strip()
            if not source and row.get("status") == "title-certain":
                candidates = [c.strip() for c in (row.get("candidates") or "").split("|") if c.strip()]
                if candidates:
                    source = min(candidates, key=_original_first)
            if source:
                found[name] = source
    return found


def plan(folder: Path, movies_root: Path, mapping: Optional[dict[str, str]] = None) -> list[Recovery]:
    videos = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS)

    # Every sourcepath sharing one directory means that directory was the dump folder,
    # so its name identifies nothing and must never be used as a title.
    parents = set()
    for video in videos:
        nfo = video.with_suffix(".nfo")
        if nfo.exists():
            src = _read_nfo(nfo).get("sourcepath")
            if src:
                parents.add(Path(src).parent.name)
    shared_parent = parents.pop() if len(parents) == 1 else None

    plans: list[Recovery] = []
    for video in videos:
        nfo = video.with_suffix(".nfo")
        meta = _read_nfo(nfo) if nfo.exists() else {}
        from_nfo = meta.get("sourcepath") or meta.get("originalfilename")
        from_mapping = mapping.get(video.name) if mapping else None
        # The mapping joins on bytes; the NFO is the metadata the bad import wrote. Bytes win.
        original = from_mapping or from_nfo
        via = "mapping" if from_mapping else ("nfo" if from_nfo else "")
        if not original:
            lost = "no mapping row and no NFO" if mapping else "no NFO"
            plans.append(Recovery(video, nfo if nfo.exists() else None, None, None, None, None,
                                  f"{lost}: original filename lost"))
            continue

        note, flagged = "", False
        if from_mapping and from_nfo and as_pure(from_mapping).name != as_pure(from_nfo).name:
            note = f"NFO names a different source ({as_pure(from_nfo).name}) - check before moving"
            flagged = True
        elif via == "nfo":
            recorded = meta.get("size")
            if recorded and recorded.isdigit():
                try:
                    if int(recorded) != video.stat().st_size:
                        note = "NFO size does not match this file - it may describe a different video"
                        flagged = True
                except OSError:
                    pass

        title, year, quality, part = _derive(original, shared_parent)
        if len(title) < 2:
            plans.append(Recovery(video, nfo, original, None, year, None,
                                  note or "could not derive a title from the original name",
                                  via=via))
            continue

        stem = f"{title} {f'({year}) ' if year else ''}[{quality}]{part}"
        plans.append(Recovery(video, nfo, original, title, year,
                              movies_root / title / f"{stem}{video.suffix.lower()}", note, flagged,
                              via=via))
    return plans


def _rewrite_nfo(nfo: Path, title: str, year: Optional[str], video: Path) -> None:
    try:
        root = ET.fromstring(nfo.read_text(errors="ignore"))
    except (ET.ParseError, OSError):
        return
    updates = {"title": title, "filenameandpath": str(video)}
    if year:
        updates["year"] = year
    for tag, value in updates.items():
        el = root.find(tag)
        if el is None:
            el = ET.SubElement(root, tag)
        el.text = value
    xml_indent(root)
    nfo.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def apply(plans: list[Recovery], quarantine: Optional[Path], include_flagged: bool) -> tuple[int, int]:
    movable = {"recoverable", "needs-review"} if include_flagged else {"recoverable"}
    moved = quarantined = 0
    for p in plans:
        if p.status in movable and p.dest is not None:
            dest = safe_path(p.dest)
            p.video.rename(dest)
            if p.nfo is not None and p.nfo.exists():
                nfo_dest = dest.with_suffix(".nfo")
                p.nfo.rename(nfo_dest)
                _rewrite_nfo(nfo_dest, p.title, p.year, dest)
            print(f"RECOVERED: {p.video.name} -> {dest}")
            moved += 1
        elif p.status == "unidentified" and quarantine is not None:
            quarantine.mkdir(parents=True, exist_ok=True)
            dest = safe_path(quarantine / p.video.name)
            p.video.rename(dest)
            if p.nfo is not None and p.nfo.exists():
                p.nfo.rename(dest.with_suffix(".nfo"))
            print(f"QUARANTINED: {p.video.name} -> {dest}")
            quarantined += 1
    return moved, quarantined


def write_report(plans: list[Recovery], report: Path) -> None:
    with report.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["status", "current_name", "size", "identified_by", "original",
                    "recovered_title", "destination", "note"])
        for p in plans:
            try:
                size = p.video.stat().st_size
            except OSError:
                size = ""
            w.writerow([p.status, p.video.name, size, p.via, p.original or "", p.title or "",
                        str(p.dest) if p.dest else "", p.note])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="the collapsed folder, e.g. /data/content/movies/F")
    ap.add_argument("--movies-root", default=None,
                    help="library movies/ root (default: the folder's parent)")
    ap.add_argument("--apply", action="store_true", help="actually move files (default: report only)")
    ap.add_argument("--quarantine", default=None,
                    help="with --apply, move unidentified files here instead of leaving them")
    ap.add_argument("--include-flagged", action="store_true",
                    help="also move files whose NFO may describe a different video")
    ap.add_argument("--report", default=None, help="write a TSV report here (default: <folder>/recovery.tsv)")
    ap.add_argument("--mapping", default=None,
                    help="mapping.tsv from map_collapsed_import; identifies files whose NFO is "
                         "missing, and outranks the NFO where both name a source")
    args = ap.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"not a directory: {folder}", file=sys.stderr)
        return 2
    movies_root = Path(args.movies_root).expanduser().resolve() if args.movies_root else folder.parent
    quarantine = Path(args.quarantine).expanduser().resolve() if args.quarantine else None

    mapping = None
    if args.mapping:
        mapping_tsv = Path(args.mapping).expanduser()
        if not mapping_tsv.is_file():
            print(f"not a file: {mapping_tsv}", file=sys.stderr)
            return 2
        mapping = read_mapping(mapping_tsv)
        print(f"mapping: {len(mapping)} of the folder's files identified by the size join\n")

    plans = plan(folder, movies_root, mapping)
    if not plans:
        print(f"no video files under {folder}")
        return 0

    counts = {"recoverable": 0, "needs-review": 0, "unidentified": 0, "already-correct": 0}
    for p in plans:
        counts[p.status] += 1
        flag = "!" if p.note else " "
        if p.status in ("recoverable", "needs-review"):
            print(f"{flag} {p.video.name}\n    -> {p.dest}")
        elif p.status == "unidentified":
            print(f"{flag} {p.video.name}\n    -> UNIDENTIFIED")
        if p.note:
            print(f"    note: {p.note}")

    report = Path(args.report) if args.report else folder / "recovery.tsv"
    write_report(plans, report)

    print(f"\n{len(plans)} videos: {counts['recoverable']} recoverable, "
          f"{counts['needs-review']} need review, {counts['unidentified']} unidentified, "
          f"{counts['already-correct']} already correct")
    print(f"report: {report}")

    if not args.apply:
        print("\nDry run - nothing moved. Re-run with --apply once the report looks right.")
        return 0

    moved, quarantined = apply(plans, quarantine, args.include_flagged)
    print(f"\nmoved {moved}, quarantined {quarantined}, "
          f"left in place {len(plans) - moved - quarantined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
