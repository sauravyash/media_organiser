import argparse
import re
from pathlib import Path

from stabilize import is_file_size_stable
from .cleanup import prune_junk_then_empty_dirs
from .constants import VIDEO_EXTS, YEAR_PATTERN
from .naming import detect_quality, is_tv_episode, _clean_title, guess_movie_name
from .nfo import (
    find_nfo,  read_nfo_to_meta, nfo_path_for,
    write_movie_nfo, write_episode_nfo, merge_first, merge_subtitles
)
from .duplicates import is_duplicate_in_dir, quick_fingerprint
from .io_ops import do_move_or_copy
from .sidecars import copy_move_sidecars
from .posters import carry_poster_with_sieve, parse_range_pair  # optional; default off



def main():
    ap = argparse.ArgumentParser(description="Organise media into /movies and /tv, copy subs, and emit local NFOs (offline).")
    ap.add_argument("source")
    ap.add_argument("dest", nargs="?", default=None)
    ap.add_argument("--mode", choices=["move","copy"], default="move")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dupe-mode", choices=["off","name","size","hash"], default="hash")
    # NFO
    ap.add_argument("--emit-nfo", choices=["off","movie","tv","all"], default="all")
    ap.add_argument("--nfo-layout", choices=["same-stem","kodi"], default="same-stem")
    ap.add_argument("--overwrite-nfo", action="store_true")
    # Posters (optional; default off)
    ap.add_argument("--carry-posters", choices=["off","keep","skip","quarantine"], default="off")
    ap.add_argument("--poster-min-wh", default="600x900")
    ap.add_argument("--poster-aspect", default="0.66-0.75")
    ap.add_argument("--poster-keywords", default="yify,yts,rarbg,ettv,yifytorrent,yify-movie")
    args = ap.parse_args()

    src_root = Path(args.source).expanduser().resolve()
    dest_root = Path(args.dest).expanduser().resolve() if args.dest else src_root
    movies_root = dest_root / "movies"
    tv_root     = dest_root / "tv"
    movies_root.mkdir(parents=True, exist_ok=True)
    tv_root.mkdir(parents=True, exist_ok=True)

    # Poster sieve config
    min_w, min_h = map(int, args.poster_min_wh.lower().split("x"))
    aspect_lo, aspect_hi = parse_range_pair(args.poster_aspect, "-", float)
    bad_words = [w.strip().lower() for w in args.poster_keywords.split(",") if w.strip()]

    for path in src_root.rglob("*"):
        if not path.is_file(): continue
        if path.suffix.lower() not in VIDEO_EXTS: continue

        # skip incomplete uploads (e.g., vsftpd client still writing)
        if not is_file_size_stable(path, interval=1.0):
            print(f"[skip] file not stable or still growing: {path}")
            continue

        # skip items already in /movies or /tv under dest
        if dest_root in path.parents and (movies_root in path.parents or tv_root in path.parents):
            continue
        # skip obvious samples
        if re.search(r"(?i)\bsample\b", path.name): continue

        quality = detect_quality(path.name)
        is_tv, info = is_tv_episode(path.name)

        if is_tv:
            series = _clean_title(info["series"])

            s_no = info["season"]
            e_no = info["ep1"]
            e2 = info.get("ep2")
            ep_tag = f"S{s_no:02d}E{e_no:02d}" + (f"-E{e2:02d}" if e2 else "")
            season_folder = "Specials" if s_no == 0 else f"Season {s_no:02d}"
            season_dir = tv_root / series / season_folder
            season_dir.mkdir(parents=True, exist_ok=True)
            out_file = season_dir / f"{series} - {ep_tag} ({quality}){path.suffix.lower()}"

            if args.dupe_mode != "off":  # noqa
                dup = is_duplicate_in_dir(path, season_dir, args.dupe_mode)
                if dup:
                    print(f"SKIP DUPLICATE: {path} == {dup} [{args.dupe_mode}]")
                    continue

            do_move_or_copy(path, out_file, args.mode, args.dry_run)
            subs = copy_move_sidecars(path, out_file, do_move_or_copy, args.mode, args.dry_run)

            if args.emit_nfo in ("tv","all") and not args.dry_run:
                size, md5 = quick_fingerprint(out_file)
                computed = {
                    "scope":"tv",
                    "showtitle": series,
                    "season": s_no,
                    "episode": e_no,
                    "episode_to": e2,
                    "title": f"{series} S{s_no:02d}E{e_no:02d}" + (f"-E{e2:02d}" if e2 else ""),
                    "quality": quality,
                    "extension": out_file.suffix.lstrip(".").lower(),
                    "size": size,
                    "uniqueid_localhash": md5,
                    "filenameandpath": str(out_file),
                    "originalfilename": path.name,
                    "sourcepath": str(path),
                    "subtitles": subs,
                }
                base_meta = {}
                src_nfo = find_nfo(path)
                if src_nfo:
                    base_meta = merge_first(base_meta, read_nfo_to_meta(src_nfo))
                dest_nfo = nfo_path_for(out_file, "tv", args.nfo_layout)
                if dest_nfo.exists():
                    base_meta = merge_first(base_meta, read_nfo_to_meta(dest_nfo))
                if "subtitles" in base_meta or subs:
                    base_meta["subtitles"] = merge_subtitles(base_meta.get("subtitles"), subs)
                write_episode_nfo(out_file, computed, base_meta, overwrite=args.overwrite_nfo, layout=args.nfo_layout)

        else:
            movie_name, used_nfo = guess_movie_name(path, src_root)
            # rough year for NFO only
            year_guess = None
            m = YEAR_PATTERN.search(path.name) or YEAR_PATTERN.search(path.parent.name)
            if m: year_guess = m.group(0)
            folder_name = f"{movie_name}"
            full_name = f"{folder_name} {f'({year_guess}) ' if year_guess else ''}[{quality}]"
            out_dir = movies_root / folder_name
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"{full_name}{path.suffix.lower()}"

            if args.dupe_mode != "off":
                dup = is_duplicate_in_dir(path, out_dir, args.dupe_mode)
                if dup:
                    print(f"SKIP DUPLICATE: {path} == {dup} [{args.dupe_mode}]")
                    continue

            do_move_or_copy(path, out_file, args.mode, args.dry_run)
            subs = copy_move_sidecars(path, out_file, do_move_or_copy, args.mode, args.dry_run)

            # optional: carry posters through sieve
            if args.carry_posters != "off":
                carry_poster_with_sieve(
                    src_context=path, dst_dir=out_dir, policy=args.carry_posters,
                    min_w=min_w, min_h=min_h, aspect_lo=aspect_lo, aspect_hi=aspect_hi, bad_words=bad_words,
                    mover=do_move_or_copy, mode=args.mode, dry_run=args.dry_run
                )

            if args.emit_nfo in ("movie","all") and not args.dry_run:
                size, md5 = quick_fingerprint(out_file)
                computed = {
                    "scope":"movie",
                    "title": movie_name,
                    "year": year_guess,
                    "quality": quality,
                    "extension": out_file.suffix.lstrip(".").lower(),
                    "size": size,
                    "uniqueid_localhash": md5,
                    "filenameandpath": str(out_file),
                    "originalfilename": path.name,
                    "sourcepath": str(path),
                    "subtitles": subs,
                }
                base_meta = {}
                if used_nfo:
                    base_meta = merge_first(base_meta, read_nfo_to_meta(used_nfo))
                dest_nfo = nfo_path_for(out_file, "movie", args.nfo_layout)
                if dest_nfo.exists():
                    base_meta = merge_first(base_meta, read_nfo_to_meta(dest_nfo))
                if "subtitles" in base_meta or subs:
                    base_meta["subtitles"] = merge_subtitles(base_meta.get("subtitles"), subs)
                write_movie_nfo(out_file, computed, base_meta, overwrite=args.overwrite_nfo, layout=args.nfo_layout)

        if args.mode == "move" and not args.dry_run:
            prune_junk_then_empty_dirs(path.parent, src_root, bad_words)

    print("Done.")
