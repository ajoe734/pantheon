"""Small cross-process locking primitive for service-owned JSONL transactions."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator


@contextmanager
def exclusive_file_lock(lock_path: str | Path, local_lock: RLock) -> Iterator[None]:
    """Serialize one transaction across threads and independent processes.

    A sidecar lock file is used instead of the JSONL data file so the lock
    remains stable when the data file does not exist yet.  The caller owns the
    durability work for its data file while this context owns only exclusion.
    """

    path = Path(lock_path)
    with local_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
