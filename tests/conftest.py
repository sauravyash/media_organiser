import sys
from pathlib import Path
import random

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_tree(tmp_path: Path):
    """Returns a tiny factory to create test files with content and return paths."""
    def _make(rel: str, data: bytes | str = b"x" * 1024) -> Path:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode("utf-8")
        p.write_bytes(data)
        return p
    return _make

@pytest.fixture
def rnd_bytes():
    def _n(n=4096):
        random.seed(42)
        return bytes(random.getrandbits(8) for _ in range(n))
    return _n
