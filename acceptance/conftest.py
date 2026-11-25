"""Per-process data directory for acceptance tests that import the BFF app.

``services.control_plane.bff.main`` opens durable state under ``BFF_DATA_DIR``
at import time (for example ``downstream_health.sqlite3`` in SQLite WAL mode).
Its default is the fixed path ``/tmp/pantheon/bff``, so parallel test processes
(the CI Test sweep runs several pytest processes per runner) would all open the
same SQLite file, and one holding it past the 10 s busy timeout makes another
fail with ``database is locked``.

When ``BFF_DATA_DIR`` is unset this conftest gives the process a new temporary
directory and removes it at process exit. An explicitly set ``BFF_DATA_DIR`` is
kept as it is. Standard library only, so a plain subprocess can load this file
with ``runpy`` (see ``test_acceptance_bff_data_dir.py``).
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

BFF_DATA_DIR_ENV = "BFF_DATA_DIR"
OWN_DIR_PREFIX = "pantheon-acceptance-bff-"


def ensure_own_bff_data_dir() -> str | None:
    """Give this process its own BFF data directory unless one is set.

    Returns the directory created for this process, or ``None`` when an
    explicitly set ``BFF_DATA_DIR`` was kept.
    """
    if BFF_DATA_DIR_ENV in os.environ:
        return None
    own = tempfile.mkdtemp(prefix=OWN_DIR_PREFIX)
    os.environ[BFF_DATA_DIR_ENV] = own
    atexit.register(shutil.rmtree, own, ignore_errors=True)
    return own


# Runs when pytest loads this conftest, before any test module in this
# directory is imported, so the BFF reads the per-process value.
OWN_BFF_DATA_DIR = ensure_own_bff_data_dir()
