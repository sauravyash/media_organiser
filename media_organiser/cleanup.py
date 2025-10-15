# media_organiser/cleanup.py
from pathlib import Path

JUNK_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".nfo", ".txt", ".url", ".webloc", ".lnk"}
# ^ keep images + typical scene cruft that we consider removable if they contain bad words

def is_ignored_junk(file: Path, bad_words: list[str]) -> bool:
    if not file.is_file():
        return False
    name = file.name.lower()
    # delete only if it matches any bad word AND is a junky suffix
    return any(w in name for w in bad_words) and file.suffix.lower() in JUNK_SUFFIXES

def prune_junk_then_empty_dirs(start: Path, stop: Path, bad_words: list[str]):
    """
    From `start` up to (but not including) `stop`, delete known junk files that
    match `bad_words`, then remove empty dirs. Stop when a dir isn't empty.
    """
    cur = start
    while cur != stop and cur != cur.parent:
        # remove junk posters/etc first
        for child in list(cur.iterdir()):
            try:
                if is_ignored_junk(child, bad_words):
                    child.unlink(missing_ok=True)
            except OSError:
                pass

        # try to remove the directory if now empty
        try:
            cur.rmdir()  # only succeeds if empty
            cur = cur.parent
        except OSError:
            break  # not empty; stop