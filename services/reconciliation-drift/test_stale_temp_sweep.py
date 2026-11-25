"""Stale temp sweep for reconciliation-drift JSON stores (RC3)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVICE_DIR.parents[1]
HEX = "0123456789abcdef0123456789abcdef"


def _load(name: str):
    for entry in (str(SERVICE_DIR), str(REPO_ROOT)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    spec = importlib.util.spec_from_file_location(f"rd_stale_{name}", SERVICE_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


store_mod = _load("store")
consumer_mod = _load("consumer")
listener_mod = _load("incident_listener")


def _leftover(path: Path, name: str, *, older: bool) -> Path:
    temp = path.with_name(name)
    temp.write_text("partial")
    base = path.stat().st_mtime_ns
    ns = base - 10**9 if older else base + 10**9
    os.utime(temp, ns=(ns, ns))
    return temp


def _files(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir() if not p.name.endswith(".lock")}


def _json_store(tmp_path):
    s = store_mod.ReconciliationDriftStore(tmp_path)
    return s.evaluations_path, s, lambda s: s.put_evaluation({"evaluation_id": "e1"})


def _consumer_write(state):
    with state._locked():
        state._write_locked()


def _consumer_state(tmp_path):
    path = tmp_path / "consumer-state.json"
    def make():
        return consumer_mod.ConsumerWorkerState(path)
    return path, make, _consumer_write


def _listener_state(tmp_path):
    path = tmp_path / "listener-state.json"
    def make():
        return listener_mod.IncidentListenerState(path)
    return path, make, lambda s: s.save()


def _cases(tmp_path):
    p1, s1, w1 = _json_store(tmp_path)
    return [
        (p1, lambda: store_mod.ReconciliationDriftStore(tmp_path), w1),
        _consumer_state(tmp_path),
        _listener_state(tmp_path),
    ]


def test_killed_write_then_restart_leaves_one_real_file_and_no_temp(tmp_path):
    for path, make, write in _cases(tmp_path):
        write(make())
        leftovers = [
            _leftover(path, f".{path.name}.{HEX}.tmp", older=True),
            _leftover(path, f".{path.name}.4242.{HEX}.tmp", older=True),
            _leftover(path, f"{path.name}.tmp", older=True),
        ]
        write(make())  # restart + first write
        assert not any(p.exists() for p in leftovers)
        assert path.exists()
    assert not [n for n in _files(tmp_path) if n.endswith(".tmp")]


def test_newer_temp_and_foreign_files_are_kept(tmp_path):
    for path, make, write in _cases(tmp_path):
        write(make())
        newer = _leftover(path, f".{path.name}.{HEX}.tmp", older=False)
        foreign = _leftover(path, f".other-{path.name}.{HEX}.tmp", older=True)
        similar = _leftover(path, f"{path.name}.bak", older=True)
        link_target = tmp_path / f"target-{path.name}"
        link_target.write_text("keep")
        link = tmp_path / f"{path.name}.{'e' * 32}.tmp"
        link.symlink_to(link_target)
        write(make())
        assert newer.exists() and foreign.exists() and similar.exists()
        assert link.is_symlink() and link_target.read_text() == "keep"
        assert path.exists()


def test_concurrent_writers_are_never_broken(tmp_path):
    for path, make, write in _cases(tmp_path):
        states = [make() for _ in range(4)]
        errors: list[BaseException] = []

        def work(state):
            try:
                for _ in range(10):
                    write(state)
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(s,)) for s in states]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        json.loads(path.read_text())
        assert not [n for n in _files(tmp_path) if n.startswith(path.name) and n.endswith(".tmp")]
