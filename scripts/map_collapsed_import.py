#!/usr/bin/env python3
"""
Map a collapsed import back to the source files it came from, so they can be
re-imported deliberately.

When one stray NFO renamed a whole import to a single title, the destination
kept the bytes but lost the names. The source drive still has both. Import does
not re-encode, so file size is an exact join key: sizes that are unique identify
a file outright, and the few that collide are separated by a content fingerprint
(first and last 1 MiB, the same one the organiser's deduplication uses).

Read-only. It writes report files and never touches the library or the source.

Both trees on one machine:

    python scripts/map_collapsed_import.py match /data/content/movies/F /mnt/f

Trees on different machines - take an inventory on each, then join them:

    # on the server
    python scripts/map_collapsed_import.py inventory /data/content/movies/F collapsed.tsv
    # on the Windows box
    python scripts/map_collapsed_import.py inventory F:\\ source.tsv
    # anywhere
    python scripts/map_collapsed_import.py match collapsed.tsv source.tsv

`match` accepts a directory or an inventory .tsv for either side.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media_organiser.constants import VIDEO_EXTS  # noqa: E402
from media_organiser.duplicates import quick_fingerprint  # noqa: E402
from media_organiser.naming import (  # noqa: E402
    count_distinct_movies,
    guess_movie_name,
    guess_movie_name_from_file,
    guess_year_for_movie,
    normalise_movie_title_for_display,
)


@dataclass
class Entry:
    """One video file, from a live tree or a previously written inventory."""
    path: str
    size: int
    fingerprint: Optional[str] = None
    live: bool = False       # the path is readable on this machine

    def fp(self) -> Optional[str]:
        if self.fingerprint is None and self.live:
            try:
                self.fingerprint = quick_fingerprint(Path(self.path))[1]
            except OSError:
                return None
        return self.fingerprint


@dataclass
class Match:
    collapsed: Entry
    source: Optional[Entry] = None
    method: str = "none"
    candidates: list[str] = field(default_factory=list)
    nfo_source: Optional[str] = None
    title: str = ""

    @property
    def status(self) -> str:
        if self.source is not None:
            return "matched"
        return "ambiguous" if len(self.candidates) > 1 else "unmatched"

    @property
    def agrees_with_nfo(self) -> str:
        """Does the size join land on the same file the NFO recorded? Ground truth."""
        if not self.nfo_source or self.source is None:
            return ""
        same = Path(self.nfo_source).name == Path(self.source.path).name
        return "yes" if same else "NO - investigate"


def scan(root: Path, fingerprint: str = "all") -> list[Entry]:
    entries = [
        Entry(str(p), p.stat().st_size, live=True)
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    ]
    if fingerprint == "none":
        return entries
    if fingerprint == "all":
        # A fingerprint reads 2 MiB regardless of file size. Worth it by default:
        # size collisions are only visible once both trees are compared, so an
        # inventory that hashes just its own duplicates can be missing the one
        # fingerprint the join later needs.
        for e in entries:
            e.fp()
        return entries
    counts: dict[int, int] = defaultdict(int)
    for e in entries:
        counts[e.size] += 1
    for e in entries:
        if counts[e.size] > 1:
            e.fp()
    return entries


def read_inventory(tsv: Path) -> list[Entry]:
    entries = []
    with tsv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            path = row["path"]
            entries.append(Entry(
                path,
                int(row["size"]),
                row.get("fingerprint") or None,
                live=Path(path).is_file(),
            ))
    return entries


def load(target: str) -> list[Entry]:
    p = Path(target).expanduser()
    if p.is_dir():
        # Live files fingerprint lazily, so only the collisions actually get hashed.
        return scan(p.resolve(), fingerprint="none")
    if p.is_file() and p.suffix.lower() == ".tsv":
        return read_inventory(p)
    raise SystemExit(f"not a directory or inventory .tsv: {target}")


def write_inventory(entries: Iterable[Entry], out: Path) -> None:
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["size", "fingerprint", "path"])
        for e in entries:
            w.writerow([e.size, e.fingerprint or "", e.path])


def _nfo_sourcepath(video: Path) -> Optional[str]:
    nfo = video.with_suffix(".nfo")
    if not nfo.is_file():
        return None
    try:
        root = ET.fromstring(nfo.read_text(errors="ignore"))
    except (ET.ParseError, OSError):
        return None
    el = root.find("sourcepath")
    return (el.text or "").strip() if el is not None and el.text else None


def _title_for(source: Entry, containers: set[Path]) -> str:
    """What a fixed re-import would call this file."""
    p = Path(source.path)
    if source.live:
        # Authoritative: same call the CLI makes, against the real tree.
        name, _ = guess_movie_name(p, p.parent, parent_is_container=p.parent in containers)
    else:
        name = guess_movie_name_from_file(p.name)
    title = normalise_movie_title_for_display(name)
    year = guess_year_for_movie(p)
    return f"{title} ({year})" if year else title


def match_all(collapsed: list[Entry], source: list[Entry]) -> list[Match]:
    by_size: dict[int, list[Entry]] = defaultdict(list)
    for e in source:
        by_size[e.size].append(e)

    # Mirror the CLI's container detection so proposed titles match a real re-import.
    dir_videos: dict[Path, list[Path]] = defaultdict(list)
    for e in source:
        if e.live:
            dir_videos[Path(e.path).parent].append(Path(e.path))
    containers = {d for d, vids in dir_videos.items() if count_distinct_movies(vids) > 1}

    matches = []
    for c in collapsed:
        m = Match(c, nfo_source=_nfo_sourcepath(Path(c.path)) if c.live else None)
        cands = by_size.get(c.size, [])
        if len(cands) == 1:
            m.source, m.method = cands[0], "size"
        elif len(cands) > 1:
            m.candidates = [e.path for e in cands]
            want = c.fp()
            missing = want is None or any(e.fp() is None for e in cands)
            hits = [e for e in cands if want and e.fp() == want] if want else []
            if len(hits) == 1:
                m.source, m.method = hits[0], "fingerprint"
            elif len(hits) > 1:
                # Byte-identical sources: interchangeable, so the choice does not matter.
                m.candidates = [e.path for e in hits]
                m.method = "identical copies - either source will do"
            elif missing:
                m.method = ("size collision, fingerprint unavailable - re-run inventory "
                            "with --fingerprint all on both sides")
            else:
                m.method = "size collision, no fingerprint agreement"
        if m.source is not None:
            m.title = _title_for(m.source, containers)
        matches.append(m)
    return matches


def report(matches: list[Match], source: list[Entry], out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "mapping.tsv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["status", "collapsed_file", "size", "match_method", "source_path",
                    "proposed_title", "nfo_sourcepath", "nfo_agrees", "candidates"])
        for m in matches:
            w.writerow([
                m.status, Path(m.collapsed.path).name, m.collapsed.size, m.method,
                m.source.path if m.source else "", m.title,
                m.nfo_source or "", m.agrees_with_nfo, " | ".join(m.candidates),
            ])

    matched_sources = {m.source.path for m in matches if m.source}
    with (out_dir / "reimport.txt").open("w", encoding="utf-8") as fh:
        for path in sorted(matched_sources):
            fh.write(path + "\n")

    untouched = [e for e in source if e.path not in matched_sources]
    with (out_dir / "source_not_in_this_folder.tsv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["size", "path"])
        for e in sorted(untouched, key=lambda e: e.path):
            w.writerow([e.size, e.path])

    counts = {"matched": 0, "ambiguous": 0, "unmatched": 0}
    for m in matches:
        counts[m.status] += 1
    counts["source_elsewhere"] = len(untouched)
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    inv = sub.add_parser("inventory", help="record size and fingerprint of every video in a tree")
    inv.add_argument("tree")
    inv.add_argument("out")
    inv.add_argument("--fingerprint", choices=["all", "duplicates", "none"], default="all",
                     help="which files to fingerprint (default: all; 2 MiB read per file)")

    mt = sub.add_parser("match", help="join a collapsed folder against its source tree")
    mt.add_argument("collapsed", help="the collapsed folder, or its inventory .tsv")
    mt.add_argument("source", help="the source tree, or its inventory .tsv")
    mt.add_argument("--out", default="import-mapping", help="report directory (default: ./import-mapping)")

    args = ap.parse_args()

    if args.cmd == "inventory":
        tree = Path(args.tree).expanduser()
        if not tree.is_dir():
            print(f"not a directory: {tree}", file=sys.stderr)
            return 2
        entries = scan(tree.resolve(), args.fingerprint)
        write_inventory(entries, Path(args.out))
        fps = sum(1 for e in entries if e.fingerprint)
        print(f"{len(entries)} videos ({fps} fingerprinted) -> {args.out}")
        return 0

    collapsed, source = load(args.collapsed), load(args.source)
    if not collapsed:
        print("no video files on the collapsed side", file=sys.stderr)
        return 2
    matches = match_all(collapsed, source)
    counts = report(matches, source, Path(args.out))

    checked = [m for m in matches if m.agrees_with_nfo]
    disagreed = [m for m in checked if m.agrees_with_nfo != "yes"]

    for m in matches:
        if m.status == "matched":
            print(f"  {Path(m.collapsed.path).name}\n    <- {m.source.path}  [{m.method}]")
        else:
            print(f"! {Path(m.collapsed.path).name}\n    {m.status.upper()}: {m.method or 'no source file of this size'}")
            for c in m.candidates:
                print(f"      candidate: {c}")

    print(f"\n{len(collapsed)} collapsed files: {counts['matched']} matched, "
          f"{counts['ambiguous']} ambiguous, {counts['unmatched']} unmatched")
    if checked:
        print(f"cross-check against NFO sourcepath: {len(checked) - len(disagreed)}/{len(checked)} agree"
              + ("" if not disagreed else f" - {len(disagreed)} DISAGREE, see mapping.tsv"))
    print(f"{counts['source_elsewhere']} source videos are not in this folder (imported fine, or never imported)")
    print(f"\nreports in {Path(args.out).resolve()}:")
    print("  mapping.tsv                     every collapsed file and where it came from")
    print("  reimport.txt                    source paths to re-import, one per line")
    print("  source_not_in_this_folder.tsv   source videos this folder does not account for")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
