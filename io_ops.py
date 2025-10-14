from pathlib import Path
import shutil

def safe_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    i = 2
    while True:
        cand = path.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
        i += 1

def do_move_or_copy(src: Path, dst: Path, mode: str, dry_run: bool):
    dst = safe_path(dst)
    action = "COPY" if mode == "copy" else "MOVE"
    print(f"{action}: {src} -> {dst}")
    if dry_run:
        return
    if mode == "copy":
        shutil.copy2(src, dst)
    else:
        try:
            shutil.move(str(src), str(dst))
        except shutil.Error:
            shutil.copy2(src, dst)
            src.unlink(missing_ok=True)
