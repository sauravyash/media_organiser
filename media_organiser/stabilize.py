from pathlib import Path
import time

def is_file_size_stable(path: Path, interval: float = 1.0) -> bool:
    """
    Return True if file size is stable across `interval` seconds.
    Useful for skipping incomplete uploads (e.g., via vsftpd).
    """
    try:
        size1 = path.stat().st_size
        time.sleep(interval)
        size2 = path.stat().st_size
        return size1 == size2
    except FileNotFoundError:
        return False