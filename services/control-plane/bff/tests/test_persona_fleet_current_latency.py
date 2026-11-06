"""BFF-PERSONA-FLEET-CURRENT-LATENCY-20261106: fleet owner reads overlap.

The slim Fleet list used to read league-with-market-defaults and the capital
pools on the request thread after the directory read. Those are independent
owner reads, so a slow owner added its full latency serially. These tests delay
each owner and prove the reads overlap, are issued once per request, stay after
the Paper rank snapshot, and surface owner failures truthfully.
"""
from __future__ import annotations

import threading
import time
from typing import Any, List

import pytest

from services.control_plane.bff.personas import service as persona_service_module
from services.control_plane.bff.tests import test_persona_archived_lifecycle as lifecycle

_DELAY = 0.4


def _slow(ports: Any, name: str, log: List[str], delay: float = _DELAY) -> None:
    original = getattr(ports, name)

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        log.append(f"{name}:{bool(kwargs.get('include_market_persona_defaults'))}")
        time.sleep(delay)
        return original(*args, **kwargs)

    setattr(ports, name, _wrapped)


def _timed_items(ports: Any):
    started = time.perf_counter()
    items = lifecycle._run_with_persona_service(ports, lifecycle._fleet_items)
    return items, time.perf_counter() - started


def test_delayed_defaults_league_and_pools_overlap_instead_of_serializing() -> None:
    ports = lifecycle._archived_ports()
    log: List[str] = []
    _slow(ports, "list_persona_league", log)
    _slow(ports, "list_capital_pools", log)
    _slow(ports, "list_personas", log)

    _, elapsed = _timed_items(ports)

    # records -> league -> (directory | defaults league | pools) is three owner
    # waits; the previous serial order paid five (records, league, directory,
    # defaults league, pools). The post-rank reads start only after the rank
    # snapshot's league read, so ordering is deterministic.
    assert elapsed < _DELAY * 4, elapsed
    assert log.count("list_persona_league:True") == 1
    assert log.count("list_capital_pools:True") == 1


def test_defaults_reads_are_issued_once_per_request_and_after_rank_snapshot() -> None:
    ports = lifecycle._archived_ports()
    log: List[str] = []
    _slow(ports, "list_persona_league", log, delay=0.0)
    _slow(ports, "list_capital_pools", log, delay=0.0)

    rows, _ = _timed_items(ports)

    assert rows
    assert log.index("list_persona_league:False") < log.index("list_persona_league:True")
    assert log.count("list_persona_league:False") == 1
    assert log.count("list_persona_league:True") == 1
    assert log.count("list_capital_pools:True") == 1


def test_owner_failure_in_overlapped_read_is_not_masked() -> None:
    ports = lifecycle._archived_ports()

    def _boom(**_kwargs: Any) -> Any:
        raise RuntimeError("capital pools owner unavailable")

    ports.list_capital_pools = _boom

    with pytest.raises(RuntimeError, match="capital pools owner unavailable"):
        lifecycle._run_with_persona_service(ports, lifecycle._fleet_items)


def test_overlapped_reads_run_in_caller_request_context() -> None:
    ports = lifecycle._archived_ports()
    threads: List[str] = []
    original = ports.list_capital_pools

    def _record(*args: Any, **kwargs: Any) -> Any:
        threads.append(threading.current_thread().name)
        return original(*args, **kwargs)

    ports.list_capital_pools = _record
    items = lifecycle._run_with_persona_service(ports, lifecycle._fleet_items)

    assert items
    assert threads == [t for t in threads if t != threading.main_thread().name]
    assert len(threads) == 1
