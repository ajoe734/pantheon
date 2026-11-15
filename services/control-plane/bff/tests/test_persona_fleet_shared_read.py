"""BFF-PERSONA-FLEET-SHARED-READ-20261115: one shared persona-fleet computation."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

import pytest

from services.control_plane.bff.personas import service as ps


class _Store:
    pass


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    store = _Store()
    monkeypatch.setattr(ps, "_get_active_read_store", lambda: store)
    monkeypatch.setattr(ps, "_persona_provisioning_store", lambda: store)
    ps._persona_fleet_shared_cache_clear()
    yield store
    ps._persona_fleet_shared_cache_clear()


class _Harness:
    def __init__(self, delay: float = 0.0, fail_first: bool = False) -> None:
        self.delay = delay
        self.fail_first = fail_first
        self.slim_calls = 0
        self.health_calls = 0
        self.lock = threading.Lock()

    def slim(self, **kw: Any) -> Dict[str, Any]:
        with self.lock:
            self.slim_calls += 1
            n = self.slim_calls
        time.sleep(self.delay)
        if self.fail_first and n == 1:
            raise RuntimeError("boom")
        return {
            "meta": {"snapshot_at": kw["snapshot_at"]},
            "data": {"items": [{"persona_id": "p1"}, {"persona_id": "p2"}]},
        }

    def health(self, tenant_id: str, ids: List[str]) -> Dict[str, Any]:
        with self.lock:
            self.health_calls += 1
        return {pid: {"ok": pid} for pid in ids}

    def call(self, **over: Any) -> Dict[str, Any]:
        args: Dict[str, Any] = dict(
            tenant_id="t1", state=None, health=None, deployment_stage=None,
            market_scope=None, q=None, page_token=None, page_size=20,
            purpose=None, data_scope=None, archived=None, viewer_user_id="u1",
        )
        args.update(over)
        return ps._persona_fleet_shared_payload(
            read_cycle_health=self.health, slim_payload=self.slim, **args
        )


def test_concurrent_identical_calls_compute_once() -> None:
    h = _Harness(delay=1.5)
    results: List[Dict[str, Any]] = []
    threads = [threading.Thread(target=lambda: results.append(h.call())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 8
    assert h.slim_calls == 1
    assert h.health_calls == 1
    assert results[0]["data"]["items"][0]["persona_cycle_health"] == {"ok": "p1"}
    results[0]["data"]["items"].clear()
    assert len(results[1]["data"]["items"]) == 2  # independent copies


def test_back_to_back_calls_reuse_and_keep_snapshot_at() -> None:
    h = _Harness()
    first = h.call()
    others = [h.call() for _ in range(18)]
    assert h.slim_calls == 1
    assert all(o["meta"]["snapshot_at"] == first["meta"]["snapshot_at"] for o in others)


def test_different_filters_or_viewer_do_not_share() -> None:
    h = _Harness()
    h.call()
    h.call(state="active")
    h.call(viewer_user_id="u2")
    h.call(page_size=50)
    h.call(tenant_id="t2")
    assert h.slim_calls == 5


def test_failed_computation_is_not_reused() -> None:
    h = _Harness(fail_first=True)
    with pytest.raises(RuntimeError):
        h.call()
    assert h.call()["data"]["items"]
    assert h.slim_calls == 2
    h.call()
    assert h.slim_calls == 2


def test_call_after_window_recomputes(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _Harness()
    monkeypatch.setattr(ps, "_PERSONA_FLEET_REUSE_SECONDS", 0.05)
    h.call()
    time.sleep(0.1)
    h.call()
    assert h.slim_calls == 2
