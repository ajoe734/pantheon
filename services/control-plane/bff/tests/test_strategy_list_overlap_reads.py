"""GET /bff/strategies: the Persona directory, ranking and runtime-truth reads overlap.

BFF-STRATEGY-LIST-OVERLAP-READS-20261126. Overlap is proven by start order, not
by a wall clock: each fake owner read waits on a barrier that opens only once
every read it must overlap has started (5 second fail-safe). Serial code never
opens the barrier, so a read that ran alone is recorded and the test fails.

The world is the hosted 2026-11-07 shape of test_strategy_operational_status
(141 default-view strategies behind a real ``RegistryService`` summary page,
real Persona records), with the hosted 2026-11-25 runtime binding / telemetry
rows of test_strategy_metrics_truth and a ranking snapshot in the hosted
``RankingSnapshotRecord`` shape. Answers and owner call counts are compared
with the pre-change route, loaded from git at ``BASE``.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters import base as adapter_base
from services.control_plane.bff.core.owner_reads import management_read_deadline
from services.control_plane.bff.strategies import router as strategies_router_mod
from services.control_plane.bff.tests import test_strategy_operational_status as operational_status_tests
from services.control_plane.bff.tests.test_strategy_metrics_truth import _REAL
from services.control_plane.bff.tests.test_strategy_operational_status import (
    HEADERS,
    TENANT,
    _build_app,
    _World,
)
from services.rankings.store import RankingSnapshotRecord

# dev head the task branch started from; collection.py there is the
# sequential route (Persona directory, then ranking, then runtime truth).
BASE = "f0a0eb79f0b141f5a56238bf2d6f72f28f84834b"
COLLECTION_PATH = "services/control-plane/bff/strategies/routes/collection.py"
REPO = Path(__file__).resolve().parents[4]
FAIL_SAFE_SECONDS = 5.0
OBSERVED_AT = "2026-11-07T07:34:52Z"

PERSONA, REGISTRY, RANKING, BINDINGS, TELEMETRY = (
    "persona_directory", "registry_page", "ranking_snapshot", "runtime_bindings", "telemetry",
)
# Persona roster read by runtime truth itself, only when the request's
# directory read is unavailable (unchanged behaviour, counted separately).
PERSONA_ROSTER = "persona_roster"

# Acceptance 2: default view, state, exclude_state and persona_id filters, the
# hosted queries of 2026-11-26, and the prefetched-page path.
QUERIES = (
    "page_size=20",
    "exclude_state=draft&hide_retired_personas=true&page_size=18",
    "exclude_state=draft&page_size=198",
    "state=approved&page_size=20",
    "exclude_state=draft&page_size=20&page_token=20",
    "persona_id={running_persona}&page_size=20",
    "include_test_personas=true&page_size=20",
)


@pytest.fixture(autouse=True)
def _permissive_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "1")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")


def _baseline_build_collection_router() -> Callable[..., Any]:
    source = subprocess.check_output(
        ["git", "show", f"{BASE}:{COLLECTION_PATH}"], cwd=REPO, text=True
    )
    name = "services.control_plane.bff.strategies.routes._collection_before_overlap"
    module = types.ModuleType(name)
    module.__package__ = "services.control_plane.bff.strategies.routes"
    sys.modules[name] = module
    exec(compile(source, f"{BASE}/{COLLECTION_PATH}", "exec"), module.__dict__)
    return module.build_collection_router


def _runtime_rows(world: _World) -> tuple:
    """The hosted binding / telemetry rows of 2026-11-25, bound to running strategies.

    Values are the recorded ones; timestamps move to this world's clock
    (snapshot 2026-11-07T07:40:00Z) so the telemetry is not from its future.
    """
    persona_by_strategy = {
        entry.strategy_id: entry.metadata["persona_ids"][0]
        for entry in world.entries
        if entry.strategy_id in world.running_strategy_ids
    }
    bindings: List[Dict[str, Any]] = []
    telemetry: List[Dict[str, Any]] = []
    for (_, _, binding, pnl, dd, fills), sid in zip(_REAL, world.running_strategy_ids):
        bindings.append({
            "id": binding, "binding_id": binding, "runtime_binding_id": binding,
            "runtime_id": "rt-" + binding, "persona_id": persona_by_strategy[sid],
            "strategy_id": sid, "tenant_id": TENANT, "deployment_stage": "paper",
            "status": "active", "activated_at": "2026-11-06T00:00:00Z", "metadata": {},
        })
        telemetry.append({
            "runtime_id": "rt-" + binding, "runtime_binding_id": binding, "binding_id": binding,
            "tenant_id": TENANT, "deployment_stage": "paper", "window": "1h",
            "collected_at": OBSERVED_AT, "pnl": pnl, "drawdown": dd,
            "ledger_fill_count": fills, "total_trades": fills,
            **({"pnl_at": OBSERVED_AT} if pnl is not None else {"pnl_status": "no_fills"}),
        })
    return bindings, telemetry


def _ranking_snapshot(world: _World) -> Dict[str, Any]:
    items = []
    for idx, sid in enumerate(world.running_strategy_ids):
        entry = next(e for e in world.entries if e.strategy_id == sid)
        items.append({
            "persona_id": entry.metadata["persona_ids"][0],
            "strategy_ids": [sid],
            "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
            "stage": "paper_running",
            "metrics": {
                "pnl": 110.0 - 50.0 * idx, "drawdown": 0.000055 * idx, "fill_rate": 1.0,
                "total_trades": 3, "runtime_count": 1, "avg_slippage_bps": 0.0,
                "latest_telemetry_at": "2026-11-05T17:40:00Z",
                "telemetry_resolution": "fresh",
                "telemetry_evidence_refs": [f"telemetry-ev-{idx:03d}"],
            },
        })
    return RankingSnapshotRecord(
        ranking_snapshot_id="snap-hist-140", surface="quarterly", period="2026-Q4",
        formula_version="pm12-v1", content_digest="sha256:snap-hist-140", items=items,
        evidence_assertion_digests={}, created_at="2026-11-05T17:40:00Z",
    ).to_canonical_dict()


class _Owners:
    """Fake owner reads with call counts, failures, delays and a start barrier.

    ``waits_for`` maps a read to the reads that must have started before it
    proceeds. A read whose barrier does not open within the fail-safe is
    recorded in ``ran_alone`` and then continues, so the request still answers.
    """

    def __init__(
        self,
        world: _World,
        *,
        waits_for: Optional[Mapping[str, FrozenSet[str]]] = None,
        fail: FrozenSet[str] = frozenset(),
        late: FrozenSet[str] = frozenset(),
        fail_safe: float = FAIL_SAFE_SECONDS,
    ) -> None:
        self.world = world
        self.waits_for = dict(waits_for or {})
        self.fail = fail
        self.late = late
        self.fail_safe = fail_safe
        self.calls: Dict[str, int] = {}
        self.started: set = set()
        self.ran_alone: List[str] = []
        self._cond = threading.Condition()
        self.bindings, self.telemetry = _runtime_rows(world)
        self.ranking = _ranking_snapshot(world)
        self.late_sleep = 0.0

    def enter(self, name: str) -> None:
        with self._cond:
            self.calls[name] = self.calls.get(name, 0) + 1
            self.started.add(name)
            self._cond.notify_all()
            needed = self.waits_for.get(name, frozenset())
            if needed and not self._cond.wait_for(
                lambda: needed <= self.started, timeout=self.fail_safe
            ):
                self.ran_alone.append(name)
        if name in self.late:
            time.sleep(self.late_sleep)
        if name in self.fail:
            raise RuntimeError(f"{name} owner down")

    def install(self, monkeypatch: pytest.MonkeyPatch, builder: Optional[Callable] = None) -> Any:
        world, owners = self.world, self
        directory = world.persona_directory

        def _directory(*args: Any, **kwargs: Any) -> Any:
            owners.enter(PERSONA)
            return directory(*args, **kwargs)

        monkeypatch.setattr(world, "persona_directory", _directory)

        def _roster(**_: Any) -> Any:
            owners.enter(PERSONA_ROSTER)
            return [dict(r) for r in world.persona_records.values()]

        def _ranking(**_: Any) -> Any:
            owners.enter(RANKING)
            return owners.ranking

        def _bindings(**_: Any) -> Any:
            owners.enter(BINDINGS)
            return list(owners.bindings)

        def _telemetry() -> Any:
            owners.enter(TELEMETRY)
            return list(owners.telemetry)

        real_ports = operational_status_tests.create_read_surface_ports

        def _ports(*args: Any, **kwargs: Any) -> Any:
            # Owner reads bound on the read store itself, as a wired owner
            # store answers them (no in-memory roster surface in between).
            ports = real_ports(*args, **kwargs)
            ports.list_personas = _roster
            ports.get_latest_ranking_snapshot = _ranking
            ports.list_runtime_bindings = _bindings
            ports.list_telemetry_summaries = _telemetry
            return ports

        monkeypatch.setattr(operational_status_tests, "create_read_surface_ports", _ports)
        if builder is not None:
            monkeypatch.setattr(strategies_router_mod, "build_collection_router", builder)
        app, _ = _build_app(monkeypatch, world)
        owner_http = adapter_base.http_request_json

        def _registry(url: str, **kwargs: Any) -> Any:
            if "view=strategy_summary_page" in url:
                owners.enter(REGISTRY)
            return owner_http(url, **kwargs)

        monkeypatch.setattr(adapter_base, "http_request_json", _registry)
        return app


def _query(world: _World, query: str) -> str:
    running = next(e for e in world.entries if e.strategy_id == world.running_strategy_ids[0])
    return query.format(running_persona=running.metadata["persona_ids"][0])


def _get(app: Any, path: str) -> Dict[str, Any]:
    with TestClient(app) as client:
        resp = client.get(path, headers=HEADERS)
    assert resp.status_code == 200, (path, resp.text)
    return resp.json()


def _read(monkeypatch: pytest.MonkeyPatch, query: str, *, before: bool = False, **owner_kwargs: Any):
    world = _World()
    owners = _Owners(world, **owner_kwargs)
    with monkeypatch.context() as mp:
        app = owners.install(mp, _baseline_build_collection_router() if before else None)
        body = _get(app, f"/bff/strategies?{_query(world, query)}")
    return body, owners


ALL_FOUR = frozenset({PERSONA, REGISTRY, RANKING, BINDINGS})
INDEPENDENT = frozenset({PERSONA, RANKING, BINDINGS})


def test_prefetched_page_persona_ranking_and_runtime_reads_all_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include_test_personas=true: the Registry first page needs nothing from
    the directory, so all four owner reads must be in flight together."""
    body, owners = _read(
        monkeypatch,
        "include_test_personas=true&page_size=20",
        waits_for={name: ALL_FOUR for name in ALL_FOUR},
    )
    assert owners.ran_alone == []
    # The second Registry call is the existing operational-counts walk of the
    # 141-row candidate set (page_size=200), after the first page.
    assert owners.calls == {PERSONA: 1, REGISTRY: 2, RANKING: 1, BINDINGS: 1, TELEMETRY: 1}
    assert body["meta"]["surfaces"]["persona_states"]["status"] == "ok"


@pytest.mark.parametrize(
    "query",
    ["page_size=20", "exclude_state=draft&hide_retired_personas=true&page_size=18"],
)
def test_default_and_hosted_view_overlap_ranking_and_runtime_with_directory_and_registry(
    monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    """The default view sends the directory's test Persona ids to the Registry,
    so its page read follows the directory. The ranking and runtime-truth reads
    must still start with the directory and stay in flight until the Registry
    read has started."""
    body, owners = _read(
        monkeypatch,
        query,
        waits_for={
            PERSONA: INDEPENDENT,
            RANKING: ALL_FOUR,
            BINDINGS: ALL_FOUR,
        },
    )
    assert owners.ran_alone == []
    assert owners.calls[PERSONA] == owners.calls[RANKING] == owners.calls[BINDINGS] == 1
    assert owners.calls[TELEMETRY] == 1
    assert body["meta"]["surfaces"]["persona_states"]["status"] == "ok"


def test_the_barrier_rejects_the_sequential_pre_change_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same barrier against the route at BASE: its reads ran one after another,
    so the barrier records reads that ran alone (short fail-safe to keep the
    run brief; the barrier itself is unchanged)."""
    _, owners = _read(
        monkeypatch,
        "page_size=20",
        before=True,
        waits_for={PERSONA: INDEPENDENT, RANKING: ALL_FOUR, BINDINGS: ALL_FOUR},
        fail_safe=0.5,
    )
    assert owners.ran_alone, "the pre-change route unexpectedly overlapped its reads"


@pytest.mark.parametrize("query", QUERIES)
def test_answers_and_owner_calls_match_the_pre_change_route(
    monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    before, before_owners = _read(monkeypatch, query, before=True)
    after, after_owners = _read(monkeypatch, query)
    assert after["items"] == before["items"]
    assert after["page_info"] == before["page_info"]
    assert after["meta"] == before["meta"]
    assert after == before
    # Not one owner call more than today, per owner.
    for name, count in after_owners.calls.items():
        assert count <= before_owners.calls.get(name, 0), (name, after_owners.calls, before_owners.calls)
    assert after_owners.calls[PERSONA] == 1
    # The recorded hosted rows reached the page where their strategies are listed.
    rows = {row["id"]: row for row in after["items"]}
    matched = 0
    for (_, _, _, pnl, _, fills), sid in zip(_REAL, _World().running_strategy_ids):
        if sid in rows:
            matched += 1
            assert rows[sid]["runtimeStatus"] == "running"
            assert rows[sid]["pnl"] == pnl and rows[sid]["totalTrades"] == fills
    if "page_size=198" in query:
        assert matched == len(_REAL)


@pytest.mark.parametrize(
    "failed",
    [
        frozenset({PERSONA}),
        frozenset({RANKING}),
        frozenset({BINDINGS}),
        frozenset({PERSONA, RANKING, BINDINGS}),
    ],
    ids=["persona", "ranking", "runtime", "all"],
)
def test_failed_reads_keep_todays_truthful_outcome(
    monkeypatch: pytest.MonkeyPatch, failed: FrozenSet[str]
) -> None:
    for query in ("page_size=20", "include_test_personas=true&page_size=20"):
        before, _ = _read(monkeypatch, query, before=True, fail=failed)
        after, owners = _read(monkeypatch, query, fail=failed)
        assert after == before, query
        meta, rows = after["meta"], after["items"]
        assert rows, query
        if PERSONA in failed:
            assert meta["surfaces"]["persona_states"]["status"] == "unavailable"
            counts = meta["operational_counts"]
            # unknown, never zeros: every counted row is unknown
            assert counts is None or {k for k, v in counts.items() if v} == {"unknown"}
            assert all(r["operationalStatus"] == "unknown" for r in rows)
        if BINDINGS in failed:
            assert all(r["runtimeStatus"] == "unknown" for r in rows)
        if RANKING in failed and BINDINGS in failed:
            assert all(r["metricsResolution"] == "unavailable" for r in rows)
        # A failed read is never retried.
        for name in failed:
            assert owners.calls.get(name) == 1, (name, owners.calls)


class _Deadline:
    def __init__(self, app: Any, seconds: float) -> None:
        self.app = app
        self.seconds = seconds

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        tok = management_read_deadline.set(time.monotonic() + self.seconds)
        try:
            await self.app(scope, receive, send)
        finally:
            management_read_deadline.reset(tok)


@pytest.mark.parametrize("late", [RANKING, BINDINGS, PERSONA])
def test_a_read_late_past_the_request_budget_is_unavailable_not_zero_or_500(
    monkeypatch: pytest.MonkeyPatch, late: str
) -> None:
    """Each join is bounded by the request read budget. A late read answers
    its own unavailable outcome; the request is still answered."""
    world = _World()
    owners = _Owners(world, late=frozenset({late}))
    owners.late_sleep = 0.8
    app = owners.install(monkeypatch)
    app.add_middleware(_Deadline, seconds=0.4)
    with TestClient(app, raise_server_exceptions=False) as client:
        # One page holds all 141 rows, so the bound and ranked rows are on it.
        resp = client.get("/bff/strategies?include_test_personas=true&page_size=200", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows, meta = body["items"], body["meta"]
    assert len(rows) == body["page_info"]["total"] == 141
    listed = {r["id"]: r for r in rows}
    bound = world.running_strategy_ids[: len(_REAL)]
    if late == PERSONA:
        assert meta["surfaces"]["persona_states"]["status"] == "unavailable"
        assert all(r["operationalStatus"] == "unknown" for r in rows)
        counts = meta["operational_counts"]
        assert {k: v for k, v in counts.items() if v} == {"unknown": 141}
        # The request budget is spent once the directory join gives up, so
        # runtime truth cannot scope its bindings: unknown, never none / idle.
        assert {r["runtimeStatus"] for r in rows} == {"unknown"}
        assert all(r["runtimeStatusReason"] for r in rows)
    else:
        assert meta["surfaces"]["persona_states"]["status"] == "ok"
    if late == BINDINGS:
        assert {r["runtimeStatus"] for r in rows} == {"unknown"}
        assert {r["runtimeStatusReason"] for r in rows} == {"runtime_bindings_unavailable"}
    elif late == RANKING:
        assert [listed[s]["runtimeStatus"] for s in bound] == ["running"] * len(bound)
        assert [listed[s]["pnl"] for s in bound] == [row[3] for row in _REAL]
        unbound = [r for r in rows if r["id"] not in bound]
        assert {r["metricsResolution"] for r in unbound} == {"unavailable"}
        assert all(r["pnl"] is None for r in unbound)
    assert owners.calls[late] == 1
