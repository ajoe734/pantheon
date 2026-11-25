"""Stale temp sweep for the incident stores (RC3: killed write leaves a temp)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from services.foundation.reliable_delivery import sweep_stale_temp_siblings
from services.incident.incident import IncidentStore
from services.incidents.consumer import (
    DurableDeliveredIncidentsStore,
    DurableDownstreamWorkStore,
)

HEX = "0123456789abcdef0123456789abcdef"


def _age(path: Path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def _leftover(path: Path, name: str, *, older: bool) -> Path:
    temp = path.with_name(name)
    temp.write_text("partial")
    base = path.stat().st_mtime_ns if path.exists() else 10**18
    _age(temp, base - 10**9 if older else base + 10**9)
    return temp


def _seed(path: Path) -> IncidentStore:
    store = IncidentStore(path=path)
    with store._write_guard():
        store._save(aggregate_type="incident", record_id="x", expected_snapshot=None)
    assert path.exists()
    return store


def _files(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir() if not p.name.endswith((".lock", ".flock"))}


def test_incident_store_restart_sweeps_killed_write(tmp_path):
    path = tmp_path / "incidents.json"
    _seed(path)
    killed = [
        _leftover(path, f".incidents.json.{HEX}.tmp", older=True),
        _leftover(path, ".incidents.json.tmp", older=True),
    ]
    IncidentStore(path=path)
    assert _files(tmp_path) == {"incidents.json"}
    assert not any(p.exists() for p in killed)


def test_incident_store_keeps_newer_temp_and_foreign_and_symlink(tmp_path):
    path = tmp_path / "incidents.json"
    _seed(path)
    newer = _leftover(path, f".incidents.json.{HEX}.tmp", older=False)
    foreign = _leftover(path, f".other.json.{HEX}.tmp", older=True)
    similar = _leftover(path, "incidents.json.bak", older=True)
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    link = tmp_path / f".incidents.json.{'f' * 32}.tmp"
    link.symlink_to(target)
    IncidentStore(path=path)
    assert newer.exists() and foreign.exists() and similar.exists()
    assert link.is_symlink() and target.read_text() == "keep"
    assert path.exists()


def test_incident_store_concurrent_writers_are_not_broken(tmp_path):
    path = tmp_path / "incidents.json"
    stores = [IncidentStore(path=path) for _ in range(4)]
    errors: list[BaseException] = []

    def work(store):
        try:
            for _ in range(20):
                with store._write_guard():
                    store._save(
                        aggregate_type="incident",
                        record_id="x",
                        expected_snapshot=None,
                    )
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    json.loads(path.read_text())
    assert _files(tmp_path) == {"incidents.json"}


def _consumer_store_cases(tmp_path):
    delivered = tmp_path / "delivered.json"
    work = tmp_path / "work.json"
    return [
        (delivered, lambda: DurableDeliveredIncidentsStore(delivered), lambda s: s.add("inc-1")),
        (work, lambda: DurableDownstreamWorkStore(work), lambda s: s.record_pending_work({"incident_id": "i"})),
    ]


def test_consumer_stores_killed_write_then_restart_leaves_one_real_file(tmp_path):
    for path, make, write in _consumer_store_cases(tmp_path):
        write(make())
        old_style = _leftover(path, path.with_suffix(".tmp").name, older=True)
        legacy = _leftover(path, f"{path.name}.{HEX}.tmp", older=True)
        write(make())  # restart then write
        assert not old_style.exists() and not legacy.exists()
        assert path.exists()
    assert _files(tmp_path) == {"delivered.json", "work.json"}


def test_consumer_stores_keep_newer_temp_and_foreign_file(tmp_path):
    for path, make, write in _consumer_store_cases(tmp_path):
        write(make())
        newer = _leftover(path, f"{path.name}.{HEX}.tmp", older=False)
        foreign = _leftover(path, f"{path.name}.{HEX}.bak", older=True)
        write(make())
        assert newer.exists() and foreign.exists()


def test_consumer_store_concurrent_writers(tmp_path):
    path = tmp_path / "delivered.json"
    stores = [DurableDeliveredIncidentsStore(path) for _ in range(4)]

    def work(i, store):
        for n in range(15):
            store.add(f"inc-{i}-{n}")

    threads = [threading.Thread(target=work, args=(i, s)) for i, s in enumerate(stores)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert isinstance(json.loads(path.read_text()), list)
    assert _files(tmp_path) == {"delivered.json"}


def test_helper_never_removes_real_file_or_directories(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{}")
    (tmp_path / f".s.json.{HEX}.tmp").mkdir()
    assert sweep_stale_temp_siblings(path, keep_newer=True) == []
    assert path.read_text() == "{}"
