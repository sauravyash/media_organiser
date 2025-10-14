from pathlib import Path
from typing import Iterable, List, Dict
import re
from media_organiser.constants import SUB_EXTS, SIDE_SUFFIX_RE


_LANG_RE = re.compile(r"(?i)[\.\- _]([a-z]{2,3}(?:-[A-Z]{2})?)(?=($|\.[^.]+))")

def find_related_sidecars(src: Path) -> Iterable[Path]:
    base = re.escape(src.stem)
    # pat = re.compile(SIDE_SUFFIX_RE.pattern.format(base=base))
    pat = re.compile(rf"(?i)^({base})(?P<suffix>(?:[ ._\-](?!S\d{{1,2}}E)\w[\w.\-]*)?)$")
    for p in src.parent.iterdir():
        if p.is_file() and p.suffix.lower() in SUB_EXTS:
            if pat.match(p.stem):
                yield p

def guess_lang_from_suffix(suffix: str) -> str | None:
    m = _LANG_RE.search(suffix)
    return m.group(1) if m else None

def copy_move_sidecars(src_video: Path, dst_video: Path, mover, mode: str, dry_run: bool) -> List[Dict[str, str]]:
    moved = []
    for side in find_related_sidecars(src_video):
        suffix = side.stem[len(src_video.stem):]
        dst = dst_video.with_name(dst_video.stem + suffix + side.suffix)
        mover(side, dst, mode, dry_run)
        moved.append({"file": dst.name, "lang": guess_lang_from_suffix(suffix) or ""})
    return moved
