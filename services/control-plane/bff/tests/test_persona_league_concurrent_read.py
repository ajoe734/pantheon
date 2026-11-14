"""BFF-LEAGUE-RANKING-CONCURRENT-READ-20261115.

persona-league, persona-league/rankings and quarterly-ranking share one
single-flight + short tenant-keyed memo of the unfiltered league rows.
"""
from __future__ import annotations

import contextvars
import copy
import tempfile
import threading
import time
from typing import Any, Dict, List

import pytest

from services.control_plane.bff.personas import service as ps
from services.control_plane.bff.tests.test_bff_pm12_persona_league import (
    HEADERS,
    _CountingPm12LeagueTestStore,
    _canonical_persona_owner_fakes,  # noqa: F401  (autouse owner fakes)
    _fresh_client,
)

ROUTES = (
    "/bff/management/persona-league",
    "/bff/management/persona-league/rankings",
    "/bff/management/quarterly-ranking",
    "/bff/management/quarterly-ranking/recommendations",
)


@pytest.fixture(autouse=True)
def _clean_memo():
    ps._pm12_league_rows_cache_clear()
    yield
    ps._pm12_league_rows_cache_clear()


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in ("snapshot_at", "generated_at", "observed_time", "last_known_at")}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _direct(*, tenant_id):
    return ps._pm12_persona_league_rows(
        tenant_id=tenant_id,
        include_route_policy=False,
        include_health=False,
        include_allowed_actions=False,
    )


def test_bodies_identical_to_unmemoized_code(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td, store=_CountingPm12LeagueTestStore(fallback=True))
        memo_bodies = {}
        for route in ROUTES:
            ps._pm12_league_rows_cache_clear()
            r = client.get(route, headers=HEADERS)
            assert r.status_code == 200, r.text
            memo_bodies[route] = _scrub(r.json())
            # second read is a memo hit and must equal the first
            r2 = client.get(route, headers=HEADERS)
            assert _scrub(r2.json()) == memo_bodies[route]
        monkeypatch.setattr(ps, "_pm12_persona_league_rows_shared", lambda *, tenant_id: _direct(tenant_id=tenant_id))
        for route in ROUTES:
            r = client.get(route, headers=HEADERS)
            assert r.status_code == 200, r.text
            assert _scrub(r.json()) == memo_bodies[route], route


def _service_ctx():
    return contextvars.copy_context()


def _fake_rows(monkeypatch, calls: List[str], *, fail: Dict[str, bool] | None = None, tag: Dict[str, str] | None = None):
    def fake(*, tenant_id=None, **_kw):
        calls.append(str(tenant_id))
        if fail and fail.get("on"):
            raise RuntimeError("boom")
        return [{"persona_id": "p", "owner": (tag or {}).get(str(tenant_id), str(tenant_id)), "n": len(calls)}]

    monkeypatch.setattr(ps, "_pm12_persona_league_rows", fake)


def test_memo_is_tenant_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        _fresh_client(td, store=_CountingPm12LeagueTestStore())
        calls: List[str] = []
        _fake_rows(monkeypatch, calls)
        a1 = ps._pm12_persona_league_rows_shared(tenant_id="tenant-a")
        b1 = ps._pm12_persona_league_rows_shared(tenant_id="tenant-b")
        a2 = ps._pm12_persona_league_rows_shared(tenant_id="tenant-a")
        b2 = ps._pm12_persona_league_rows_shared(tenant_id="tenant-b")
        assert [r["owner"] for r in a1 + a2] == ["tenant-a", "tenant-a"]
        assert [r["owner"] for r in b1 + b2] == ["tenant-b", "tenant-b"]
        assert calls == ["tenant-a", "tenant-b"]  # each tenant computed once


def test_callers_get_independent_copies(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        _fresh_client(td, store=_CountingPm12LeagueTestStore())
        _fake_rows(monkeypatch, [])
        first = ps._pm12_persona_league_rows_shared(tenant_id="t")
        first[0]["owner"] = "mutated"
        assert ps._pm12_persona_league_rows_shared(tenant_id="t")[0]["owner"] == "t"


def test_reuse_window_is_bounded_and_store_change_visible_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    assert 0 < ps._PM12_LEAGUE_ROWS_REUSE_SECONDS <= 30.0
    with tempfile.TemporaryDirectory() as td:
        _fresh_client(td, store=_CountingPm12LeagueTestStore())
        calls: List[str] = []
        _fake_rows(monkeypatch, calls)
        now = [1000.0]
        monkeypatch.setattr(ps.time, "monotonic", lambda: now[0])
        first = ps._pm12_persona_league_rows_shared(tenant_id="t")
        now[0] += ps._PM12_LEAGUE_ROWS_REUSE_SECONDS - 0.5
        assert ps._pm12_persona_league_rows_shared(tenant_id="t") == first
        assert len(calls) == 1
        now[0] += 1.0  # past the window: the "store change" is now visible
        fresh = ps._pm12_persona_league_rows_shared(tenant_id="t")
        assert len(calls) == 2 and fresh[0]["n"] == 2


def test_failed_computation_is_never_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        _fresh_client(td, store=_CountingPm12LeagueTestStore())
        calls: List[str] = []
        fail = {"on": True}
        _fake_rows(monkeypatch, calls, fail=fail)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                ps._pm12_persona_league_rows_shared(tenant_id="t")
        assert len(calls) == 2  # the failure was recomputed, not replayed
        fail["on"] = False
        assert ps._pm12_persona_league_rows_shared(tenant_id="t")[0]["n"] == 3
        assert not ps._pm12_league_rows_inflight


def test_concurrent_readers_share_one_computation(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        _fresh_client(td, store=_CountingPm12LeagueTestStore())
        calls: List[str] = []
        gate = threading.Event()

        def slow(*, tenant_id=None, **_kw):
            calls.append(str(tenant_id))
            gate.wait(5)
            return [{"persona_id": "p"}]

        monkeypatch.setattr(ps, "_pm12_persona_league_rows", slow)
        results: List[Any] = []

        def run():
            results.append(ps._pm12_persona_league_rows_shared(tenant_id="t"))

        threads = [threading.Thread(target=_service_ctx().run, args=(run,)) for _ in range(5)]
        for t in threads:
            t.start()
        time.sleep(0.3)
        gate.set()
        for t in threads:
            t.join(10)
        assert len(calls) == 1 and len(results) == 5


# --- rerunnable before/after measurement (AC1/AC3) --------------------------
# LEAGUE_BENCH=after|before PERSONAS=166 ROUNDS=20 \
#   python -m pytest -q -s services/control-plane/bff/tests/test_persona_league_concurrent_read.py -k bench
# 'before' swaps the memo for the direct pre-change computation.
class _SerialPool:
    """Stand-in for ThreadPoolExecutor that runs submissions inline (the pre-change serial order)."""

    def __init__(self, *a, **k):
        pass

    def submit(self, fn, *a, **k):
        from concurrent.futures import Future

        f = Future()
        try:
            f.set_result(fn(*a, **k))
        except BaseException as exc:  # noqa: BLE001
            f.set_exception(exc)
        return f

    def shutdown(self, *a, **k):
        pass


def _league_store(n: int = 12, fail_on: str | None = None):
    store = _CountingPm12LeagueTestStore(fallback=True)
    for i in range(n):
        store.create_persona(persona_id=f"p-{i:03d}", name=f"Persona {i:03d}", archetype="trend")

    def allowed(persona_id: str):
        time.sleep(0.01)
        if persona_id == fail_on:
            raise RuntimeError("allowed-actions-down")
        k = sum(map(ord, persona_id))
        return {"a": True, "b": k % 2 == 0, "c": False}

    store.get_persona_allowed_actions = allowed  # type: ignore[assignment]
    return store


def test_allowed_actions_parallel_matches_serial_order_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td, store=_league_store())
        par = client.get("/bff/management/persona-league", headers=HEADERS, params={"page_size": 50})
        assert par.status_code == 200, par.text
        ps._pm12_league_rows_cache_clear()
        monkeypatch.setattr(ps, "ThreadPoolExecutor", _SerialPool)
        ser = client.get("/bff/management/persona-league", headers=HEADERS, params={"page_size": 50})
        assert ser.status_code == 200
        assert _scrub(par.json()) == _scrub(ser.json())
        items = par.json()["data"]["items"]
        assert any(i["allowed_action_summary"]["enabled_count"] for i in items)


def test_allowed_actions_item_failure_same_as_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = []
    for serial in (False, True):
        ps._pm12_league_rows_cache_clear()
        with monkeypatch.context() as m:
            if serial:
                m.setattr(ps, "ThreadPoolExecutor", _SerialPool)
            with tempfile.TemporaryDirectory() as td:
                client = _fresh_client(td, store=_league_store(fail_on="persona-alpha"))
                try:
                    r = client.get("/bff/management/persona-league", headers=HEADERS, params={"page_size": 50})
                    outcomes.append((r.status_code, _scrub(r.json())))
                except RuntimeError as exc:
                    outcomes.append(("raised", str(exc)))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] != 200  # the failure is surfaced exactly as the serial loop did, not swallowed


def test_allowed_actions_concurrency_is_bounded() -> None:
    active = {"now": 0, "max": 0}
    lock = threading.Lock()
    store = _league_store(n=30)

    def allowed(persona_id: str):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.02)
        with lock:
            active["now"] -= 1
        return {"a": True}

    store.get_persona_allowed_actions = allowed  # type: ignore[assignment]
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td, store=store)
        r = client.get("/bff/management/persona-league", headers=HEADERS, params={"page_size": 30})
        assert r.status_code == 200, r.text
    assert 1 < active["max"] <= 8


import json as _json
import os as _os
from concurrent.futures import ThreadPoolExecutor as _TPE

_COCKPIT_ROUTES = (
    "/bff/management/persona-league",
    "/bff/management/persona-league/rankings",
    "/bff/management/quarterly-ranking",
    "/bff/management/quarterly-ranking/formula",
    "/bff/management/persona-fleet",
)


@pytest.mark.skipif(not _os.environ.get("LEAGUE_BENCH"), reason="measurement only; set LEAGUE_BENCH=before|after")
def test_cockpit_concurrent_bench(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _os.environ["LEAGUE_BENCH"]
    personas = int(_os.environ.get("PERSONAS", "166"))
    rounds = int(_os.environ.get("ROUNDS", "20"))
    if mode == "before":
        monkeypatch.setattr(ps, "_pm12_persona_league_rows_shared", lambda *, tenant_id: _direct(tenant_id=tenant_id))
    with tempfile.TemporaryDirectory() as td:
        store = _CountingPm12LeagueTestStore(fallback=True)
        for n in range(personas):
            store.create_persona(persona_id=f"p-{n:03d}", name=f"Persona {n:03d}", archetype=("trend", "mean_reversion", "carry")[n % 3])
        delay = float(_os.environ.get("STORE_DELAY_MS", "0")) / 1000.0
        if delay > 0:
            # Per-call downstream latency on the bulk/per-persona store reads the
            # league composition makes (stand-in for hosted downstream latency).
            for name in ("list_bindings", "list_runtime_bindings", "list_persona_league", "get_persona_allowed_actions"):
                orig = getattr(store, name, None)
                if callable(orig):
                    def _slow(*a, __o=orig, **k):
                        time.sleep(delay)
                        return __o(*a, **k)
                    monkeypatch.setattr(store, name, _slow, raising=False)
        client = _fresh_client(td, store=store)
        lat: Dict[str, List[float]] = {r: [] for r in _COCKPIT_ROUTES}

        def hit(route: str) -> None:
            t0 = time.perf_counter()
            resp = client.get(route, headers=HEADERS, params={"quarter": "2026-Q1"})
            assert resp.status_code == 200, (route, resp.text[:200])
            lat[route].append(time.perf_counter() - t0)

        for _ in range(rounds):
            ps._pm12_league_rows_cache_clear()  # each round is a cold cockpit page load
            with _TPE(max_workers=5) as pool:
                list(pool.map(hit, _COCKPIT_ROUTES))

        def pct(xs: List[float], q: float) -> float:
            xs = sorted(xs)
            return round(xs[min(len(xs) - 1, int(q * len(xs)))], 3)

        out = {r: {"p50": pct(v, 0.5), "p95": pct(v, 0.95), "max": round(max(v), 3)} for r, v in lat.items()}
        print("BENCH_RESULT " + _json.dumps({"mode": mode, "personas": personas, "rounds": rounds, "routes": out}))


class _TelemetryStore:
    def __init__(self, fail_on: str | None = None, delay: float = 0.0) -> None:
        self.calls: List[str] = []
        self.fail_on = fail_on
        self.delay = delay
        self._lock = threading.Lock()

    def get_telemetry_summary(self, runtime_id: str):
        with self._lock:
            self.calls.append(runtime_id)
        if self.delay:
            time.sleep(self.delay)
        if runtime_id == self.fail_on:
            raise RuntimeError("telemetry down")
        return None


def _runtime_rows(n: int) -> List[Dict[str, Any]]:
    # Two personas share each runtime; runtime_resolution marks authoritative ids.
    return [
        {"persona_id": f"p-{i}", "runtime_resolution": "active", "runtime_ids": [f"rt-{i // 2}"], "state": "active"}
        for i in range(n)
    ]


def test_quarterly_telemetry_prefetch_dedupes_and_is_concurrent(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _TelemetryStore(delay=0.05)
    monkeypatch.setattr(ps, "_get_active_read_store", lambda: store)
    cache: Dict[str, Any] = {}
    t0 = time.perf_counter()
    ps._pm12_prefetch_runtime_telemetry(_runtime_rows(40), cache)
    elapsed = time.perf_counter() - t0
    assert sorted(store.calls) == sorted({f"rt-{i // 2}" for i in range(40)})  # each runtime read once
    assert set(cache) == set(store.calls)
    assert elapsed < 20 * 0.05 * 0.6  # serial would be 20 * 50ms


def test_quarterly_telemetry_prefetch_failure_not_cached_and_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _TelemetryStore(fail_on="rt-1")
    monkeypatch.setattr(ps, "_get_active_read_store", lambda: store)
    cache: Dict[str, Any] = {}
    ps._pm12_prefetch_runtime_telemetry(_runtime_rows(6), cache)
    assert "rt-1" not in cache and "rt-0" in cache
    # The serial path re-reads the failed runtime and raises, as before the change.
    with pytest.raises(RuntimeError):
        ps._pm12_persona_telemetry_metrics(_runtime_rows(6)[2], telemetry_cache=cache)
