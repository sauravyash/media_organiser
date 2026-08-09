"""Apply the audit's recommendations, reversibly.

:mod:`media_organiser.library` only *describes* what is wrong; this module is
the write side. Every change goes through :func:`apply_actions`, which records
each operation in an append-only journal so :func:`undo_batch` can put the
library back exactly as it was.

Three rules make that guarantee hold:

* **Nothing is deleted.** A "delete" moves the file into ``.trash/<batch>/``
  under the library root — same filesystem, so it is a rename rather than a
  multi-gigabyte copy, and it can be moved straight back.
* **Nothing is clobbered.** A rename whose target already exists is refused,
  not silently renumbered. ``safe_path``'s ``(2)`` suffix is what produced most
  of the duplicate pairs the audit now reports; the fix path must not add more.
* **Nothing is applied blind.** Each action carries the size and mtime observed
  when it was planned. If the file changed in between, the action is skipped.

Actions are applied in a fixed order because they feed each other: trashing
duplicates first avoids renaming files that are about to disappear, folders are
renamed before the files inside them (the expected filename is derived from the
folder name), and NFOs are written last because they record the final path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .audit import VERB_RENAME_FILE, VERB_RENAME_FOLDER, VERB_TRASH, VERB_WRITE_NFO
from .duplicates import quick_fingerprint
from .library import get_movies_dir
from .naming import (
    detect_quality,
    guess_year_for_movie,
    movie_part_suffix,
    normalise_movie_title_for_display,
)
from .nfo import read_nfo_to_meta, write_movie_nfo

TRASH_DIR_NAME = ".trash"
STATE_DIR_NAME = ".media_organiser"
JOURNAL_NAME = "journal.jsonl"

# Applied low number first. Duplicates go before renames so we never rename a
# file we are about to trash; folders go before the files inside them; NFOs go
# last because they record the final filename and path.
_APPLY_ORDER = {
    VERB_TRASH: 0,
    VERB_RENAME_FOLDER: 1,
    VERB_RENAME_FILE: 2,
    VERB_WRITE_NFO: 3,
}

VERBS = tuple(_APPLY_ORDER)

# Issue kinds this module can carry out without a human decision.
MECHANICAL_KINDS = (
    "filename-mismatch",
    "messy-folder-name",
    "missing-nfo",
    "stale-nfo-path",
)

_BATCH_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
# The " (2)" tail ``safe_path`` appends when a destination is taken. Between two
# otherwise equal candidates the un-numbered one is the canonical name, so it is
# the copy triage should offer to keep.
_NUMBERED_COPY_RE = re.compile(r"\s\(\d+\)$")


def _root() -> Path:
    """Directory holding ``movies/``; trash and journal live beside it.

    Anchoring on the movies root's parent rather than ``LIB_DIR`` keeps the
    trash on the same filesystem even when ``MOVIES_DIR`` points somewhere else
    entirely, which is what makes trashing a rename instead of a copy.
    """
    return get_movies_dir().parent


def get_trash_dir() -> Path:
    override = os.environ.get("TRASH_DIR")
    return Path(override).expanduser().resolve() if override else _root() / TRASH_DIR_NAME


def get_state_dir() -> Path:
    return _root() / STATE_DIR_NAME


def journal_path() -> Path:
    return get_state_dir() / JOURNAL_NAME


def _ensure_trash_dir() -> Path:
    """Create the trash and mark it so media servers skip it.

    Jellyfin (and Emby) ignore any directory containing a ``.ignore`` file, so
    trashed movies do not reappear in the library while they await undo.
    """
    trash = get_trash_dir()
    trash.mkdir(parents=True, exist_ok=True)
    marker = trash / ".ignore"
    if not marker.exists():
        try:
            marker.write_text("", encoding="utf-8")
        except OSError:
            pass
    return trash


def new_batch_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{os.urandom(3).hex()}"


def action_id(verb: str, src: str, dst: str) -> str:
    """Stable handle for an action, so the UI can select what to apply."""
    digest = hashlib.sha1(f"{verb}|{src}|{dst}".encode("utf-8")).hexdigest()
    return digest[:12]


@dataclass
class JournalEntry:
    """One applied operation. ``src``/``dst`` are relative to :func:`_root`."""
    batch: str
    seq: int
    ts: str
    verb: str
    src: str
    dst: str
    size: Optional[int] = None
    mtime: Optional[float] = None
    undone: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActionResult:
    id: str
    verb: str
    src: str
    dst: str
    status: str  # applied | skipped | error
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Path handling
# --------------------------------------------------------------------------

def rel_to_root(path: Path) -> str:
    """``path`` as a root-relative posix string, or absolute if it sits outside."""
    try:
        return path.resolve().relative_to(_root()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def resolve_under_root(value: str) -> Optional[Path]:
    """Resolve a client-supplied path, rejecting anything outside the root.

    Both absolute paths (as the audit payload reports them) and root-relative
    ones are accepted; traversal outside the library is not.
    """
    if not value or not isinstance(value, str):
        return None
    root = _root()
    try:
        candidate = Path(value)
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    except (OSError, ValueError):
        return None
    if resolved == root or root in resolved.parents:
        return resolved
    return None


def _stat_or_none(path: Path) -> Optional[os.stat_result]:
    try:
        return path.stat()
    except OSError:
        return None


def _is_stale(path: Path, size: Optional[int], mtime: Optional[float]) -> bool:
    """Whether the file changed since the action was planned.

    Only size and mtime are compared: hashing every candidate again would cost
    a full read of the library for what is a guard, not a decision.
    """
    if size is None and mtime is None:
        return False
    st = _stat_or_none(path)
    if st is None:
        return True
    if size is not None and st.st_size != size:
        return True
    if mtime is not None and abs(st.st_mtime - mtime) > 1.0:
        return True
    return False


# --------------------------------------------------------------------------
# Journal
# --------------------------------------------------------------------------

def append_journal(entries: Iterable[JournalEntry]) -> None:
    entries = list(entries)
    if not entries:
        return
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")


def read_journal() -> list[dict]:
    path = journal_path()
    if not path.is_file():
        return []
    records: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    # A torn final line (killed mid-write) must not hide the
                    # rest of the history.
                    continue
    except OSError:
        return []
    return records


def _rewrite_journal(records: list[dict]) -> None:
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _mark_undone(batch: str, seqs: set[int]) -> None:
    records = read_journal()
    for record in records:
        if record.get("batch") == batch and record.get("seq") in seqs:
            record["undone"] = True
    _rewrite_journal(records)


def list_batches() -> list[dict]:
    """Applied batches, newest first, with what each still holds in the trash."""
    batches: dict[str, dict] = {}
    for record in read_journal():
        batch = record.get("batch")
        if not batch:
            continue
        info = batches.setdefault(batch, {
            "batch": batch,
            "ts": record.get("ts", ""),
            "operations": 0,
            "undone": 0,
            "trashed": 0,
            "reclaimable": 0,
            "verbs": {},
        })
        info["operations"] += 1
        if record.get("undone"):
            info["undone"] += 1
        verb = record.get("verb", "?")
        info["verbs"][verb] = info["verbs"].get(verb, 0) + 1
        if verb == VERB_TRASH and not record.get("undone"):
            info["trashed"] += 1
            info["reclaimable"] += record.get("size") or 0
        if record.get("ts", "") < info["ts"]:
            info["ts"] = record["ts"]

    ordered = sorted(batches.values(), key=lambda b: b["ts"], reverse=True)
    for info in ordered:
        info["fully_undone"] = info["undone"] >= info["operations"]
    return ordered


def trash_summary() -> dict:
    batches = list_batches()
    return {
        "trash_dir": str(get_trash_dir()),
        "journal": str(journal_path()),
        "batches": batches,
        "total_reclaimable": sum(b["reclaimable"] for b in batches),
        "total_trashed": sum(b["trashed"] for b in batches),
    }


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------

def _normalise_action(raw: dict) -> Optional[dict]:
    """Validate one client-supplied action into a trusted internal form."""
    if not isinstance(raw, dict):
        return None
    verb = raw.get("verb")
    if verb not in _APPLY_ORDER:
        return None
    src = resolve_under_root(raw.get("src") or "")
    dst_raw = raw.get("dst") or ""
    dst = resolve_under_root(dst_raw) if dst_raw else None
    if verb == VERB_TRASH:
        if src is None:
            return None
    elif verb == VERB_WRITE_NFO:
        # src is the video the NFO describes; dst is the NFO to write.
        if src is None or dst is None:
            return None
    else:
        if src is None or dst is None:
            return None
    size = raw.get("size")
    mtime = raw.get("mtime")
    return {
        "id": raw.get("id") or action_id(verb, str(src), str(dst or "")),
        "verb": verb,
        "src": src,
        "dst": dst,
        "size": int(size) if isinstance(size, (int, float)) else None,
        "mtime": float(mtime) if isinstance(mtime, (int, float)) else None,
        "kind": raw.get("kind") or "",
    }


def _remap(path: Optional[Path], renames: list[tuple[Path, Path]]) -> Optional[Path]:
    """Rewrite a path through renames already applied in this batch.

    Every action was planned against the library as it looked during the scan,
    so an earlier rename in the same batch can move the ground under a later
    one — twice over:

    * a file rename planned as ``Foo [YTS]/x.mp4 -> Foo [YTS]/y.mp4`` still
      names the old *directory* once the folder itself has been renamed;
    * an NFO write planned against ``x.mp4`` still names the old *file* once
      that file has been renamed to ``y.mp4``.

    Matching on the path itself covers the second case and matching on parents
    covers the first.
    """
    if path is None:
        return None
    for old, new in renames:
        if path == old:
            path = new
        elif old in path.parents:
            path = new / path.relative_to(old)
    return path


def _apply_trash(action: dict, batch: str, seq: int) -> tuple[ActionResult, Optional[JournalEntry]]:
    src: Path = action["src"]
    ident = action["id"]
    if not src.is_file():
        return ActionResult(ident, VERB_TRASH, str(src), "", "skipped", "file is gone"), None
    if _is_stale(src, action["size"], action["mtime"]):
        return ActionResult(ident, VERB_TRASH, str(src), "", "skipped",
                            "changed on disk since the scan — rescan first"), None

    st = _stat_or_none(src)
    trash = _ensure_trash_dir()
    dest = trash / batch / rel_to_root(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return ActionResult(ident, VERB_TRASH, str(src), str(dest), "skipped",
                            "already present in this batch's trash"), None
    try:
        shutil.move(str(src), str(dest))
    except (OSError, shutil.Error) as exc:
        return ActionResult(ident, VERB_TRASH, str(src), str(dest), "error", str(exc)), None

    entry = JournalEntry(
        batch=batch, seq=seq, ts=_now(), verb=VERB_TRASH,
        src=rel_to_root(src), dst=rel_to_root(dest),
        size=st.st_size if st else None, mtime=st.st_mtime if st else None,
    )
    return ActionResult(ident, VERB_TRASH, str(src), str(dest), "applied"), entry


def _apply_rename(action: dict, batch: str, seq: int) -> tuple[ActionResult, Optional[JournalEntry]]:
    verb = action["verb"]
    src: Path = action["src"]
    dst: Path = action["dst"]
    ident = action["id"]

    if src == dst:
        return ActionResult(ident, verb, str(src), str(dst), "skipped", "already named correctly"), None
    exists = src.is_dir() if verb == VERB_RENAME_FOLDER else src.is_file()
    if not exists:
        return ActionResult(ident, verb, str(src), str(dst), "skipped", "source is gone"), None
    if verb == VERB_RENAME_FILE and _is_stale(src, action["size"], action["mtime"]):
        return ActionResult(ident, verb, str(src), str(dst), "skipped",
                            "changed on disk since the scan — rescan first"), None
    # Never renumber onto a busy target: that is what created the duplicates.
    # A case-only rename is the exception — the source *is* the target on a
    # case-insensitive filesystem, so exists() is not evidence of a collision.
    if dst.exists() and str(dst).lower() != str(src).lower():
        return ActionResult(ident, verb, str(src), str(dst), "skipped",
                            f"target already exists: {dst.name}"), None

    st = _stat_or_none(src)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    except OSError as exc:
        return ActionResult(ident, verb, str(src), str(dst), "error", str(exc)), None

    entry = JournalEntry(
        batch=batch, seq=seq, ts=_now(), verb=verb,
        src=rel_to_root(src), dst=rel_to_root(dst),
        size=st.st_size if (st and verb == VERB_RENAME_FILE) else None,
        mtime=st.st_mtime if (st and verb == VERB_RENAME_FILE) else None,
    )
    return ActionResult(ident, verb, str(src), str(dst), "applied"), entry


def build_nfo_payload(video: Path) -> dict:
    """The ``computed`` dict :func:`write_movie_nfo` expects, matching the CLI."""
    size, md5 = quick_fingerprint(video)
    folder = video.parent
    return {
        "scope": "movie",
        "title": normalise_movie_title_for_display(folder.name),
        "year": guess_year_for_movie(video),
        "quality": detect_quality(video.name),
        "extension": video.suffix.lstrip(".").lower(),
        "size": size,
        "uniqueid_localhash": md5,
        "filenameandpath": str(video),
        "originalfilename": video.name,
        "sourcepath": str(video),
    }


# Fields that describe the file as it is right now rather than what the movie
# is. ``merge_first`` lets the existing NFO win, so these have to be dropped
# from it or a refresh would faithfully preserve the stale path it was meant to
# correct. Everything else — title, year, quality, import provenance — is kept,
# because it may have been edited by hand.
_REFRESHED_NFO_FIELDS = ("filenameandpath", "size", "uniqueid_localhash", "extension")


def _apply_write_nfo(action: dict, batch: str, seq: int) -> tuple[list[ActionResult], list[JournalEntry]]:
    """Write an NFO, trashing any existing one first so undo can restore it."""
    video: Path = action["src"]
    dst: Path = action["dst"]
    ident = action["id"]
    results: list[ActionResult] = []
    entries: list[JournalEntry] = []

    if not video.is_file():
        return [ActionResult(ident, VERB_WRITE_NFO, str(video), str(dst), "skipped", "video is gone")], []

    # Read before trashing: afterwards there is nothing left to carry over.
    base_meta = read_nfo_to_meta(dst) if dst.exists() else {}
    for stale_field in _REFRESHED_NFO_FIELDS:
        base_meta.pop(stale_field, None)

    if dst.exists():
        replaced, entry = _apply_trash(
            {"id": ident, "verb": VERB_TRASH, "src": dst, "dst": None, "size": None, "mtime": None},
            batch, seq,
        )
        if replaced.status == "applied" and entry is not None:
            entries.append(entry)
            seq += 1
        elif replaced.status != "applied":
            return [ActionResult(ident, VERB_WRITE_NFO, str(video), str(dst), "skipped",
                                 f"could not set aside the old NFO: {replaced.reason}")], entries

    try:
        write_movie_nfo(
            video,
            build_nfo_payload(video),
            base_meta,
            overwrite=True,
            layout="kodi" if dst.name == "movie.nfo" else "same-stem",
        )
    except OSError as exc:
        return results + [ActionResult(ident, VERB_WRITE_NFO, str(video), str(dst), "error", str(exc))], entries

    entries.append(JournalEntry(
        batch=batch, seq=seq, ts=_now(), verb=VERB_WRITE_NFO,
        src=rel_to_root(video), dst=rel_to_root(dst),
    ))
    results.append(ActionResult(ident, VERB_WRITE_NFO, str(video), str(dst), "applied"))
    return results, entries


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def apply_actions(raw_actions: list[dict], dry_run: bool = False) -> dict:
    """Carry out ``raw_actions`` in dependency order, journalled as one batch.

    Returns a per-action report. Anything that cannot be done safely is
    ``skipped`` with a reason rather than forced — a partially applied batch is
    fine, because every applied operation is individually reversible.
    """
    actions = [a for a in (_normalise_action(r) for r in raw_actions or []) if a]
    if not actions:
        return {"batch": None, "applied": 0, "skipped": 0, "errors": 0, "results": [], "dry_run": dry_run}

    actions.sort(key=lambda a: (_APPLY_ORDER[a["verb"]], str(a["src"])))

    if dry_run:
        results = [ActionResult(a["id"], a["verb"], str(a["src"]), str(a["dst"] or ""), "planned").to_dict()
                   for a in actions]
        return {"batch": None, "applied": 0, "skipped": 0, "errors": 0,
                "results": results, "dry_run": True}

    batch = new_batch_id()
    renames: list[tuple[Path, Path]] = []
    results: list[ActionResult] = []
    entries: list[JournalEntry] = []
    seq = 0

    for action in actions:
        action = dict(action)
        action["src"] = _remap(action["src"], renames)
        action["dst"] = _remap(action["dst"], renames)
        if action["verb"] == VERB_WRITE_NFO and action["dst"] is not None:
            # An NFO always sits beside its video, so derive the target from the
            # (possibly just renamed) video rather than trusting the planned
            # name. ``movie.nfo`` is folder-level and keeps its name.
            if action["dst"].name != "movie.nfo":
                action["dst"] = action["src"].with_suffix(".nfo")

        if action["verb"] == VERB_TRASH:
            result, entry = _apply_trash(action, batch, seq)
            results.append(result)
            if entry:
                entries.append(entry)
                seq += 1
        elif action["verb"] in (VERB_RENAME_FILE, VERB_RENAME_FOLDER):
            result, entry = _apply_rename(action, batch, seq)
            results.append(result)
            if entry:
                entries.append(entry)
                seq += 1
                renames.append((action["src"], action["dst"]))
        else:
            written, new_entries = _apply_write_nfo(action, batch, seq)
            results.extend(written)
            entries.extend(new_entries)
            seq += len(new_entries)

    append_journal(entries)

    counts = {"applied": 0, "skipped": 0, "error": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return {
        "batch": batch if entries else None,
        "applied": counts.get("applied", 0),
        "skipped": counts.get("skipped", 0),
        "errors": counts.get("error", 0),
        "results": [r.to_dict() for r in results],
        "dry_run": False,
    }


# --------------------------------------------------------------------------
# Undo
# --------------------------------------------------------------------------

def undo_batch(batch_id: str) -> dict:
    """Reverse an applied batch, newest operation first.

    Reverse order matters: a folder rename recorded after the file renames
    inside it has to be unwound before those files can be found again.
    """
    if not _BATCH_ID_RE.match(batch_id or ""):
        return {"batch": batch_id, "restored": 0, "skipped": 0, "results": [],
                "error": "unknown batch"}

    records = [r for r in read_journal() if r.get("batch") == batch_id and not r.get("undone")]
    if not records:
        return {"batch": batch_id, "restored": 0, "skipped": 0, "results": [],
                "error": "nothing left to undo in this batch"}

    records.sort(key=lambda r: r.get("seq", 0), reverse=True)
    results: list[dict] = []
    undone_seqs: set[int] = set()

    for record in records:
        verb = record.get("verb")
        seq = record.get("seq", 0)
        src = resolve_under_root(record.get("src", ""))
        dst = resolve_under_root(record.get("dst", ""))

        if verb == VERB_WRITE_NFO:
            # The NFO we created; any previous one is a separate trash record
            # restored later in this same reverse pass.
            if dst is not None and dst.exists():
                try:
                    dst.unlink()
                except OSError as exc:
                    results.append({"seq": seq, "verb": verb, "status": "error", "reason": str(exc)})
                    continue
            undone_seqs.add(seq)
            results.append({"seq": seq, "verb": verb, "status": "restored", "path": str(dst or "")})
            continue

        if src is None or dst is None:
            results.append({"seq": seq, "verb": verb, "status": "skipped", "reason": "path outside the library"})
            continue
        if not dst.exists():
            results.append({"seq": seq, "verb": verb, "status": "skipped",
                            "reason": f"{dst.name} is no longer where it was put"})
            continue
        if verb == VERB_TRASH and _is_stale(dst, record.get("size"), record.get("mtime")):
            results.append({"seq": seq, "verb": verb, "status": "skipped",
                            "reason": "the trashed file changed since it was trashed"})
            continue
        if src.exists():
            results.append({"seq": seq, "verb": verb, "status": "skipped",
                            "reason": f"something else now occupies {src.name}"})
            continue

        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
        except (OSError, shutil.Error) as exc:
            results.append({"seq": seq, "verb": verb, "status": "error", "reason": str(exc)})
            continue

        undone_seqs.add(seq)
        results.append({"seq": seq, "verb": verb, "status": "restored", "path": str(src)})

    if undone_seqs:
        _mark_undone(batch_id, undone_seqs)
        _prune_empty_trash_dirs(batch_id)

    restored = sum(1 for r in results if r["status"] == "restored")
    return {
        "batch": batch_id,
        "restored": restored,
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }


def _prune_empty_trash_dirs(batch_id: str) -> None:
    base = get_trash_dir() / batch_id
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    try:
        base.rmdir()
    except OSError:
        pass


def empty_batch(batch_id: str) -> dict:
    """Permanently delete one batch's trashed files. Not reversible."""
    if not _BATCH_ID_RE.match(batch_id or ""):
        return {"batch": batch_id, "deleted": 0, "error": "unknown batch"}
    base = get_trash_dir() / batch_id
    freed = 0
    deleted = 0
    if base.is_dir():
        for path in base.rglob("*"):
            if path.is_file():
                st = _stat_or_none(path)
                freed += st.st_size if st else 0
                deleted += 1
        shutil.rmtree(base, ignore_errors=True)

    records = read_journal()
    for record in records:
        if record.get("batch") == batch_id and record.get("verb") == VERB_TRASH and not record.get("undone"):
            record["undone"] = True
            record["note"] = "emptied from trash"
    _rewrite_journal(records)
    return {"batch": batch_id, "deleted": deleted, "freed": freed}


# --------------------------------------------------------------------------
# Planning mechanical fixes from an audit payload
# --------------------------------------------------------------------------

def plan_mechanical(entries: list[dict], kinds: Optional[Iterable[str]] = None) -> dict:
    """Turn audited issues into applyable actions, grouped by issue kind.

    Only issues the audit attached an ``action`` to are included, so this never
    re-derives a target name — the audit already computed it.
    """
    wanted = set(kinds) if kinds else set(MECHANICAL_KINDS)
    groups: dict[str, dict] = {}

    for entry in entries or []:
        for issue in entry.get("issues") or []:
            action = issue.get("action")
            kind = issue.get("kind")
            if not action or kind not in wanted:
                continue
            verb = action.get("verb")
            if verb not in _APPLY_ORDER:
                continue

            src = action.get("src") or ""
            dst = action.get("dst") or ""
            src_path = resolve_under_root(src)
            dst_path = resolve_under_root(dst) if dst else None
            # An existing target blocks a rename, but is the normal case for an
            # NFO refresh — the old one is set aside and rewritten, not clobbered.
            collision = (
                verb != VERB_WRITE_NFO
                and bool(dst_path and dst_path.exists() and dst_path != src_path)
            )

            record = {
                "id": action_id(verb, src, dst),
                "verb": verb,
                "kind": kind,
                "folder": entry.get("folder"),
                "src": src,
                "dst": dst,
                "src_label": Path(src).name if src else "",
                "dst_label": Path(dst).name if dst else "",
                "size": action.get("size"),
                "mtime": action.get("mtime"),
                "message": issue.get("message", ""),
                "collision": collision,
                "missing": src_path is None or not src_path.exists(),
            }
            group = groups.setdefault(kind, {
                "kind": kind,
                "verb": verb,
                "actions": [],
                "count": 0,
                "collisions": 0,
                "order": _APPLY_ORDER[verb],
            })
            group["actions"].append(record)
            group["count"] += 1
            if collision:
                group["collisions"] += 1

    ordered = sorted(groups.values(), key=lambda g: (g["order"], g["kind"]))
    return {
        "groups": ordered,
        "total": sum(g["count"] for g in ordered),
        "collisions": sum(g["collisions"] for g in ordered),
    }


# --------------------------------------------------------------------------
# Duplicate triage
# --------------------------------------------------------------------------

def _fingerprint_or_none(path: Path) -> Optional[str]:
    try:
        return quick_fingerprint(path)[1]
    except OSError:
        return None


def inspect_folder(folder: Path) -> dict:
    """Per-file specs for one folder, with byte-identical files grouped.

    Fingerprints are only taken where two files already share a size, so a
    triage pass reads a couple of megabytes per candidate rather than the whole
    library.
    """
    from .constants import VIDEO_EXTS  # local: avoids a cycle at import time

    videos: list[dict] = []
    try:
        children = sorted(p for p in folder.rglob("*") if p.is_file())
    except OSError as exc:
        return {"folder": folder.name, "path": str(folder), "videos": [], "error": str(exc)}

    for child in children:
        if child.suffix.lower() not in VIDEO_EXTS:
            continue
        st = _stat_or_none(child)
        videos.append({
            "name": child.name,
            "relpath": str(child.relative_to(folder)).replace("\\", "/"),
            "path": str(child),
            "container": child.suffix.lstrip(".").lower(),
            "size": st.st_size if st else 0,
            "mtime": st.st_mtime if st else None,
            "quality": detect_quality(child.name),
            "year": guess_year_for_movie(child),
            "nfo": child.with_suffix(".nfo").name if child.with_suffix(".nfo").exists() else None,
            # A CD1/CD2 half is not a duplicate of its other half; the UI keeps
            # every part so a two-disc rip cannot be triaged down to one file.
            "part": movie_part_suffix(child).strip(),
            "fingerprint": None,
            "identical_group": None,
        })

    by_size: dict[int, list[dict]] = {}
    for video in videos:
        if video["size"]:
            by_size.setdefault(video["size"], []).append(video)

    group_no = 0
    for size, sharing in by_size.items():
        if len(sharing) < 2:
            continue
        for video in sharing:
            video["fingerprint"] = _fingerprint_or_none(Path(video["path"]))
        by_fp: dict[str, list[dict]] = {}
        for video in sharing:
            if video["fingerprint"]:
                by_fp.setdefault(video["fingerprint"], []).append(video)
        for identical in by_fp.values():
            if len(identical) < 2:
                continue
            group_no += 1
            for video in identical:
                video["identical_group"] = group_no

    # Biggest first — the keeper is usually the larger encode — and between two
    # of the same size, the one that is not a renumbered copy.
    videos.sort(key=lambda v: (
        -v["size"],
        bool(_NUMBERED_COPY_RE.search(Path(v["name"]).stem)),
        v["name"].lower(),
    ))
    return {
        "folder": folder.name,
        "path": str(folder),
        "videos": videos,
        "identical_groups": group_no,
    }
