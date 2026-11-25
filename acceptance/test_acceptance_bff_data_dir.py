"""acceptance/conftest.py gives every process that imports the BFF app its own data dir.

Regression for Branch CI run 45715715898 (Test sweep shard 5): two sweep
processes imported ``services.control_plane.bff.main`` with the default
``BFF_DATA_DIR=/tmp/pantheon/bff`` and one failed with ``sqlite3.OperationalError:
database is locked`` at ``PRAGMA journal_mode = WAL`` on
``downstream_health.sqlite3``.

The lock race is timing dependent (the store waits 10 s before giving up), so a
plain pair of imports does not fail reliably. The reproduction here makes it
deterministic with the real file and the real import: this test process holds
an exclusive SQLite lock on the shared ``downstream_health.sqlite3`` for longer
than the busy timeout, the way a slower sibling process did in CI. A shared
temporary directory stands in for the fixed default so the test never locks the
real ``/tmp/pantheon/bff`` that other runner processes may use.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFTEST = Path(__file__).resolve().parent / "conftest.py"
IMPORT_TIMEOUT_S = 240

# The child loads acceptance/conftest.py exactly as pytest does (module code
# runs once at load), optionally, then imports the real BFF app.
CHILD = r"""
import json, os, runpy, sys
if sys.argv[1] == "conftest":
    runpy.run_path(sys.argv[2])
from services.control_plane.bff import main as bff_main
print(json.dumps({
    "data_dir": os.environ.get("BFF_DATA_DIR"),
    "state_path": bff_main.downstream_health_monitor.state_path,
}))
"""

CONFTEST_ONLY = r"""
import json, os, runpy, sys
ns = runpy.run_path(sys.argv[1])
own = ns["OWN_BFF_DATA_DIR"]
print(json.dumps({"own": own, "data_dir": os.environ.get("BFF_DATA_DIR"),
                  "exists": bool(own) and os.path.isdir(own)}))
"""


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "BFF_DATA_DIR"}
    env.update(
        PYTHONPATH=os.pathsep.join(filter(None, [str(ROOT / ".orchestrator"), str(ROOT), env.get("PYTHONPATH", "")])),
        RANKING_STORE_DSN="postgresql://test:test@localhost:5432/test",
        RANKING_STORE_BOOTSTRAP="0",
        TMPDIR=str(tmp_path / "tmp"),
    )
    (tmp_path / "tmp").mkdir(exist_ok=True)
    env.update(extra)
    return env


def _start_import(mode: str, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, mode, str(CONFTEST)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _finish(proc: subprocess.Popen) -> tuple[int, str, str]:
    out, err = proc.communicate(timeout=IMPORT_TIMEOUT_S)
    return proc.returncode, out, err


def test_unset_bff_data_dir_gets_a_new_temporary_dir_removed_at_exit(tmp_path):
    env = _env(tmp_path)
    done = subprocess.run([sys.executable, "-c", CONFTEST_ONLY, str(CONFTEST)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    own = Path(seen["own"])
    assert seen["data_dir"] == str(own) and seen["exists"]
    assert own.parent == tmp_path / "tmp" and own.name.startswith("pantheon-acceptance-bff-")
    assert not own.exists(), "the per-process dir must be removed at process exit"
    assert list((tmp_path / "tmp").iterdir()) == []


def test_explicitly_set_bff_data_dir_is_kept(tmp_path):
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    env = _env(tmp_path, BFF_DATA_DIR=str(explicit))
    done = subprocess.run([sys.executable, "-c", CONFTEST_ONLY, str(CONFTEST)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert seen["own"] is None and seen["data_dir"] == str(explicit)
    assert explicit.is_dir(), "an explicitly set dir is not ours to remove"
    assert list((tmp_path / "tmp").iterdir()) == []


def test_two_concurrent_bff_imports_share_a_lock_without_conftest_and_not_with_it(tmp_path):
    shared = tmp_path / "shared-default"  # stands in for the fixed /tmp/pantheon/bff
    shared.mkdir()
    holder = sqlite3.connect(shared / "downstream_health.sqlite3", timeout=0, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")  # a sibling process still writing the shared state
    try:
        with_conftest = [_start_import("conftest", _env(tmp_path)) for _ in range(2)]
        without = [_start_import("plain", _env(tmp_path, BFF_DATA_DIR=str(shared))) for _ in range(2)]
        with_results = [_finish(p) for p in with_conftest]
        without_results = [_finish(p) for p in without]
    finally:
        holder.rollback()
        holder.close()

    # With the conftest: both imports succeed, each on its own state file.
    paths = []
    for code, out, err in with_results:
        assert code == 0, err[-2000:]
        seen = json.loads(out.strip().splitlines()[-1])
        assert Path(seen["state_path"]) == Path(seen["data_dir"]) / "downstream_health.sqlite3"
        assert Path(seen["data_dir"]).parent == tmp_path / "tmp"
        paths.append(seen["state_path"])
    assert len(set(paths)) == 2 and str(shared) not in "".join(paths)
    assert list((tmp_path / "tmp").iterdir()) == [], "per-process dirs must be gone after exit"

    # Without it: the same imports on one shared dir fail exactly as in CI.
    for code, _out, err in without_results:
        assert code != 0
        assert "sqlite3.OperationalError: database is locked" in err, err[-2000:]
        assert "PRAGMA journal_mode = WAL" in err, err[-2000:]
