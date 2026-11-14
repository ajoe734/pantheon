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
