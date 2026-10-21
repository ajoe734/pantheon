"""Regression tests for PM12-PERSONA-READS-BATCH-20261020.

Proves that within a single request across >= 90 personas (including 78 retired
and >= 12 active/paper_running personas):
1. Each owner collection (personas, capital_pools, bindings, runtime_bindings,
   paper_fleet_sessions, telemetry_summaries, capability_snapshots, sessions,
   route_policies, strategy_specs) is read at most once.
2. Retired personas skip live owner enrichment while still appearing in
   responses (including fleet archived views).
3. ``state`` and ``q`` filters run before per-persona enrichment; ``page_size``
   and ``page_token`` bound the enriched set where the contract allows.
4. ``persona-health``, ``quarterly-ranking``, ``promotion-reviews``, and the
   ``ppl-alloc-009`` proof path reuse request-scoped reads, and
   ``persona-health`` reads ``list_persona_league`` only once.
5. Owner call counts do not grow when persona count increases from 90 to 150.
"""
from __future__ import annotations

import json
import sys
import threading
import types
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256
from services.control_plane.bff.core.app_factory import compose_bff_app
from services.control_plane.bff.core.owner_reads import (
    authorization as owner_authorization,
    evict_request_read_keys,
    request_read_scope,
    selected_tenant as owner_selected_tenant,
)
from services.control_plane.bff.personas.service import (
    _PPL_ALLOC_009_ELIGIBILITY_IDEMPOTENCY_KEY,
    _PPL_ALLOC_009_ELIGIBILITY_RUN_KEY,
    _current_persona_service,
    _extract_identity,
    _pm12_persona_league_ranking_item,
    _pm12_persona_league_rows,
    _ppl_alloc_009_paper_eligibility_context,
)
from services.control_plane.bff.tests.bff_compose_stand_ins import (
    resolve_with_stand_ins,
)

_JWT_SECRET = "test-secret-pm12-batch-reads"
_JWT_ISSUER = "https://auth.pantheon.local"
_JWT_AUDIENCE = "pantheon-bff"


class _FakeCursor:
    def __init__(self, conn: "_FakeConnection") -> None:
        self._conn = conn
        self._one: Any = None
        self._all: List[Any] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO ranking_snapshots"):
            snapshot_id, payload_json = params
            self._conn.rows[snapshot_id] = json.loads(payload_json)
            self._one = (snapshot_id,)
        elif "WHERE snapshot_id = %s" in normalized:
            row = self._conn.rows.get(params[0])
            self._one = (row,) if row is not None else None
        elif "ORDER BY generated_at DESC" in normalized:
            limit = int(params[-1])
            self._all = [(r,) for r in list(self._conn.rows.values())[:limit]]

    def fetchone(self) -> Any:
        return self._one

    def fetchall(self) -> List[Any]:
        return self._all


class _FakeConnection:
    rows: Dict[str, Dict[str, Any]] = {}

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        return None


def _fake_psycopg() -> types.ModuleType:
    module = types.ModuleType("psycopg")
    module.connect = lambda dsn: _FakeConnection()  # type: ignore[attr-defined]
    return module


def _make_auth_header(
    tenant_id: str = "tenant-dev",
    roles: tuple[str, ...] = ("read_only", "operator", "approver"),
) -> str:
    claims = {
        "sub": f"user-{tenant_id}",
        "roles": list(roles),
        "exp": 4102444800,
        "iss": _JWT_ISSUER,
        "aud": _JWT_AUDIENCE,
        "tenant_id": tenant_id,
        "tenants": [tenant_id],
        "mfa": True,
    }
    token = encode_jwt_hs256(claims, secret=_JWT_SECRET)
    return f"Bearer {token}"


def _build_fixture_dataset(
    total_personas: int = 90,
    active_personas: int = 12,
    tenant_id: str = "tenant-dev",
) -> Dict[str, List[Dict[str, Any]]]:
    assert total_personas >= active_personas
    retired_personas = total_personas - active_personas
    personas: List[Dict[str, Any]] = []
    capital_pools: List[Dict[str, Any]] = []
    bindings: List[Dict[str, Any]] = []
    runtime_bindings: List[Dict[str, Any]] = []
    sessions: List[Dict[str, Any]] = []
    paper_sessions: List[Dict[str, Any]] = []
    telemetry_summaries: List[Dict[str, Any]] = []
    capability_snapshots: List[Dict[str, Any]] = []

    for idx in range(active_personas):
        pid = f"persona-active-{idx:03d}"
        pool_id = f"pool-active-{idx:03d}"
        bind_id = f"bind-active-{idx:03d}"
        rt_id = f"rt-active-{idx:03d}"
        rb_id = f"rb-active-{idx:03d}"
        sess_id = f"sess-active-{idx:03d}"
        personas.append(
            {
                "id": pid,
                "persona_id": pid,
                "tenant_id": tenant_id,
                "name": f"Active Persona {idx:03d}",
                "lifecycle_state": "active",
                "state": "paper_running",
                "strategy_family": "momentum",
                "required_data_sources": ["twse"],
                "metadata": {
                    "tenant_id": tenant_id,
                    "owner": "team-alpha",
                    "archetype": "momentum",
                    "capital_mode": "paper",
                    "deployment_stage": "paper",
                    "paper_ledger_id": f"paper-ledger-{pid}",
                    "runtime_id": rt_id,
                    "runtime_binding_id": rb_id,
                    "binding_id": bind_id,
                },
            }
        )
        capital_pools.append(
            {
                "id": pool_id,
                "capital_pool_id": pool_id,
                "tenant_id": tenant_id,
                "name": f"Pool {idx:03d}",
                "status": "active",
                "mode": "paper",
            }
        )
        bindings.append(
            {
                "id": bind_id,
                "binding_id": bind_id,
                "persona_capital_binding_id": bind_id,
                "persona_id": pid,
                "capital_pool_id": pool_id,
                "tenant_id": tenant_id,
                "status": "active",
                "validity": "active",
                "deployment_stage": "paper",
                "allowed_deployment_scope": "paper",
            }
        )
        runtime_bindings.append(
            {
                "id": rb_id,
                "runtime_binding_id": rb_id,
                "binding_id": bind_id,
                "persona_capital_binding_id": bind_id,
                "runtime_id": rt_id,
                "persona_id": pid,
                "capital_pool_id": pool_id,
                "tenant_id": tenant_id,
                "status": "running",
                "state": "running",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "updated_at": "2026-10-20T10:00:00Z",
            }
        )
        sess_record = {
            "id": sess_id,
            "session_id": sess_id,
            "persona_id": pid,
            "runtime_id": rt_id,
            "runtime_binding_id": rb_id,
            "tenant_id": tenant_id,
            "status": "active",
            "state": "running",
            "session_type": "paper_runtime_monitoring",
            "deployment_stage": "paper",
            "last_heartbeat_at": "2026-10-20T10:00:00Z",
        }
        sessions.append(sess_record)
        paper_sessions.append(dict(sess_record))
        telemetry_summaries.append(
            {
                "id": f"tel-{idx:03d}",
                "runtime_id": rt_id,
                "persona_id": pid,
                "tenant_id": tenant_id,
                "pnl": 0.12 + idx * 0.01,
                "drawdown": 0.03,
                "max_drawdown": 0.03,
                "fill_rate": 0.98,
                "avg_slippage_bps": 1.2,
                "total_trades": 25,
                "collected_at": "2026-10-20T10:00:00Z",
            }
        )
        capability_snapshots.append(
            {
                "id": f"cap-{idx:03d}",
                "snapshot_id": f"cap-{idx:03d}",
                "persona_id": pid,
                "tenant_id": tenant_id,
                "effective_skills": ["skill-a"],
                "effective_tools": ["tool-a"],
                "effective_workflows": ["wf-a"],
                "generated_at": "2026-10-20T09:00:00Z",
            }
        )

    for idx in range(retired_personas):
        pid = f"persona-retired-{idx:03d}"
        personas.append(
            {
                "id": pid,
                "persona_id": pid,
                "tenant_id": tenant_id,
                "name": f"Retired Persona {idx:03d}",
                "lifecycle_state": "retired",
                "state": "retired",
                "strategy_family": "archive",
                "required_data_sources": ["twse"],
                "metadata": {
                    "tenant_id": tenant_id,
                    "owner": "team-archive",
                    "archetype": "archive",
                },
            }
        )

    return {
        "personas": personas,
        "capital_pools": capital_pools,
        "bindings": bindings,
        "runtime_bindings": runtime_bindings,
        "sessions": sessions,
        "paper_sessions": paper_sessions,
        "telemetry_summaries": telemetry_summaries,
        "capability_snapshots": capability_snapshots,
    }


class CountingOwnerServer:
    """HTTP owner stub that counts collection and per-item hits."""

    def __init__(self, dataset: Dict[str, List[Dict[str, Any]]]) -> None:
        self.dataset = dataset
        self.counts: Counter[str] = Counter()
        self.raw_paths: List[str] = []
        self._lock = threading.Lock()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port: int = 0

    def reset_counts(self) -> None:
        with self._lock:
            self.counts.clear()
            self.raw_paths.clear()

    def snapshot_counts(self) -> Dict[str, int]:
        with self._lock:
            return dict(self.counts)

    def _record(self, key: str, raw_path: str) -> None:
        with self._lock:
            self.counts[key] += 1
            self.raw_paths.append(raw_path)

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return None

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)
                tenant = self.headers.get("x-tenant-id")

                def _by_tenant(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                    return [
                        item
                        for item in items
                        if tenant is None or item.get("tenant_id") in (None, tenant)
                    ]

                if path == "/api/personas":
                    outer._record("personas_collection", self.path)
                    self.send_json(200, _by_tenant(outer.dataset["personas"]))
                elif path.startswith("/api/personas/"):
                    outer._record("persona_single", self.path)
                    pid = path.rsplit("/", 1)[-1]
                    match = next(
                        (
                            p
                            for p in _by_tenant(outer.dataset["personas"])
                            if p.get("persona_id") == pid
                        ),
                        None,
                    )
                    self.send_json(200 if match else 404, match or {"error": "not found"})
                elif path == "/api/sessions":
                    if "persona_id" in query:
                        outer._record("sessions_per_persona", self.path)
                        pid = query["persona_id"][0]
                        res = [
                            s
                            for s in _by_tenant(outer.dataset["sessions"])
                            if s.get("persona_id") == pid
                        ]
                    else:
                        outer._record("sessions_collection", self.path)
                        res = _by_tenant(outer.dataset["sessions"])
                    self.send_json(200, res)
                elif path == "/api/capability-snapshots":
                    if "persona_id" in query:
                        outer._record("capability_snapshot_per_persona", self.path)
                        pid = query["persona_id"][0]
                        res = [
                            s
                            for s in _by_tenant(outer.dataset["capability_snapshots"])
                            if s.get("persona_id") == pid
                        ]
                    else:
                        outer._record("capability_snapshots_collection", self.path)
                        res = _by_tenant(outer.dataset["capability_snapshots"])
                    self.send_json(200, {"snapshots": res})
                elif path == "/api/capital-pools":
                    outer._record("capital_pools_collection", self.path)
                    self.send_json(200, _by_tenant(outer.dataset["capital_pools"]))
                elif path.startswith("/api/capital-pools/"):
                    outer._record("capital_pool_single", self.path)
                    pool_id = path.rsplit("/", 1)[-1]
                    match = next(
                        (
                            p
                            for p in _by_tenant(outer.dataset["capital_pools"])
                            if p.get("capital_pool_id") == pool_id
                        ),
                        None,
                    )
                    self.send_json(200 if match else 404, match or {"error": "not found"})
                elif path == "/api/bindings":
                    outer._record("bindings_collection", self.path)
                    self.send_json(200, _by_tenant(outer.dataset["bindings"]))
                elif path == "/api/runtime-bindings":
                    outer._record("runtime_bindings_collection", self.path)
                    self.send_json(
                        200,
                        {"bindings": _by_tenant(outer.dataset["runtime_bindings"])},
                    )
                elif path in ("/api/v1/paper-fleet/sessions", "/api/fleet/state"):
                    outer._record("paper_fleet_sessions_collection", self.path)
                    res = _by_tenant(outer.dataset["paper_sessions"])
                    self.send_json(
                        200,
                        {"sessions": res, "monitoring_sessions": res},
                    )
                elif path in ("/api/telemetry/summaries", "/api/telemetry/runtime-summaries"):
                    outer._record("telemetry_summaries_collection", self.path)
                    res = _by_tenant(outer.dataset["telemetry_summaries"])
                    self.send_json(
                        200,
                        {"items": res, "summaries": res},
                    )
                elif path == "/api/registry/strategy-specs":
                    outer._record("strategy_specs_collection", self.path)
                    self.send_json(200, [])
                elif path in ("/api/evolution/proposals", "/api/evolution/decisions"):
                    outer._record("evolution_decisions_collection", self.path)
                    self.send_json(200, [])
                elif path == "/api/evolution/programs":
                    outer._record("evolution_programs_collection", self.path)
                    self.send_json(200, [])
                elif path == "/api/governance/approvals":
                    outer._record("governance_approvals_collection", self.path)
                    self.send_json(200, [])
                elif path == "/api/incidents":
                    outer._record("incidents_collection", self.path)
                    self.send_json(200, [])
                elif path == "/api/source-ingest/evidence/items":
                    outer._record("evidence_items_collection", self.path)
                    self.send_json(200, {"items": []})
                elif path == "/api/source-ingest/connectors/registry":
                    outer._record("source_connector_registry", self.path)
                    self.send_json(200, {"connectors": []})
                elif path == "/api/source-ingest/health-usage-snapshot":
                    outer._record("source_health_snapshot", self.path)
                    self.send_json(200, {"sources": []})
                elif path == "/api/deployment/plans":
                    outer._record("deployment_plans_collection", self.path)
                    self.send_json(
                        200,
                        _by_tenant(outer.dataset.get("deployment_plans", [])),
                    )
                else:
                    outer._record(f"other:{path}", self.path)
                    self.send_json(200, {"items": []})

            def send_json(self, status: int, body: Any) -> None:
                raw = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", _JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", _JWT_AUDIENCE)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("PANTHEON_ENV", "test")
    monkeypatch.setattr(_FakeConnection, "rows", {})
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg())


def _build_client(server: CountingOwnerServer, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    owner_url = f"http://127.0.0.1:{server.port}"
    monkeypatch.setenv("PANTHEON_PERSONA_URL", owner_url)
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", owner_url)
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_URL", owner_url)
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_PAPER_FLEET_RECONCILER_URL", owner_url)
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", owner_url)
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "test-evo-token")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_INCIDENT_API_URL", owner_url)
    app = compose_bff_app(dependency_resolver=resolve_with_stand_ins)
    return TestClient(app)


def _assert_bounded_owner_reads(counts: Dict[str, int]) -> None:
    for collection_key in (
        "personas_collection",
        "capital_pools_collection",
        "bindings_collection",
        "runtime_bindings_collection",
        "paper_fleet_sessions_collection",
        "telemetry_summaries_collection",
        "capability_snapshots_collection",
        "sessions_collection",
        "strategy_specs_collection",
    ):
        assert counts.get(collection_key, 0) <= 1, (
            f"Expected at most 1 owner call for {collection_key}, got {counts}"
        )
    for per_item_key in (
        "persona_single",
        "capital_pool_single",
        "sessions_per_persona",
        "capability_snapshot_per_persona",
    ):
        assert counts.get(per_item_key, 0) == 0, (
            f"Expected 0 per-persona owner calls for {per_item_key}, got {counts}"
        )


def test_pm12_routes_read_each_owner_collection_at_most_once_and_scale_invariantly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Across 90 and 150 personas, owner call counts are <= 1 per collection and invariant."""
    server_90 = CountingOwnerServer(_build_fixture_dataset(total_personas=90, active_personas=12))
    server_150 = CountingOwnerServer(_build_fixture_dataset(total_personas=150, active_personas=20))
    server_90.start()
    server_150.start()
    try:
        auth = {"Authorization": _make_auth_header("tenant-dev")}
        routes = [
            "/bff/management/persona-league",
            "/bff/management/persona-fleet",
            "/bff/management/quarterly-ranking",
            "/bff/management/promotion-reviews",
            "/bff/v5/execution/persona-health",
        ]
        counts_90: Dict[str, Dict[str, int]] = {}
        client_90 = _build_client(server_90, monkeypatch)
        for route in routes:
            server_90.reset_counts()
            resp = client_90.get(route, headers=auth)
            assert resp.status_code == 200, f"{route} failed: {resp.text}"
            snap = server_90.snapshot_counts()
            _assert_bounded_owner_reads(snap)
            counts_90[route] = snap

        client_150 = _build_client(server_150, monkeypatch)
        for route in routes:
            server_150.reset_counts()
            resp = client_150.get(route, headers=auth)
            assert resp.status_code == 200, f"{route} failed: {resp.text}"
            snap_150 = server_150.snapshot_counts()
            _assert_bounded_owner_reads(snap_150)
            assert snap_150 == counts_90[route], (
                f"Owner call counts for {route} grew with persona count: "
                f"90={counts_90[route]} vs 150={snap_150}"
            )
    finally:
        server_90.stop()
        server_150.stop()


def test_retired_personas_skip_enrichment_and_filters_and_pagination_bound_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retired personas remain visible in fleet/league but skip live owner enrichment;
    state/q filters run before enrichment, page_size bounds per-page enrichment, and
    filtered persona-league requests preserve the full-league ranking_snapshot_id."""
    dataset = _build_fixture_dataset(total_personas=90, active_personas=12)
    server = CountingOwnerServer(dataset)
    server.start()
    try:
        client = _build_client(server, monkeypatch)
        auth = {"Authorization": _make_auth_header("tenant-dev")}

        route_policy_calls: List[str] = []
        allowed_action_calls: List[str] = []
        read_store = client.app.state.persona_service._read_store
        orig_route_policy = read_store.get_route_policy_for_persona
        orig_allowed_actions = read_store.get_persona_allowed_actions
        monkeypatch.setattr(
            read_store,
            "get_route_policy_for_persona",
            lambda pid: route_policy_calls.append(str(pid)) or orig_route_policy(pid),
        )
        monkeypatch.setattr(
            read_store,
            "get_persona_allowed_actions",
            lambda pid, **kw: allowed_action_calls.append(str(pid)) or orig_allowed_actions(pid, **kw),
        )

        # 0. Capture full-league ranking_snapshot_id from unfiltered request
        resp_unfiltered = client.get("/bff/management/persona-league?page_size=20", headers=auth)
        assert resp_unfiltered.status_code == 200
        unfiltered_payload = resp_unfiltered.json()
        full_snapshot_id = unfiltered_payload["data"]["summary"]["ranking_snapshot_id"]
        assert full_snapshot_id.startswith("ranking-rolling-short-cycle-")
        assert unfiltered_payload["data"]["ranking_snapshot_id"] == full_snapshot_id
        assert unfiltered_payload["meta"]["ranking_snapshot_id"] == full_snapshot_id
        assert all(
            item["ranking_snapshot_id"] == full_snapshot_id
            for item in unfiltered_payload["data"]["items"]
        )

        # 1. state=retired on persona-league preserves full-league ranking_snapshot_id
        #    and skips per-persona route_policy and allowed_actions enrichment;
        #    state=retired on movers skips live owner collections entirely
        route_policy_calls.clear()
        allowed_action_calls.clear()
        server.reset_counts()
        resp_retired = client.get(
            "/bff/management/persona-league?state=retired&page_size=20",
            headers=auth,
        )
        assert resp_retired.status_code == 200
        retired_payload = resp_retired.json()
        body_retired = retired_payload["data"]
        assert body_retired["summary"]["persona_count"] == 78
        assert len(body_retired["items"]) == 20
        assert all(item["state"] == "retired" for item in body_retired["items"])
        assert body_retired["ranking_snapshot_id"] == full_snapshot_id
        assert body_retired["summary"]["ranking_snapshot_id"] == full_snapshot_id
        assert retired_payload["meta"]["ranking_snapshot_id"] == full_snapshot_id
        assert all(item["ranking_snapshot_id"] == full_snapshot_id for item in body_retired["items"])
        assert route_policy_calls == []
        assert allowed_action_calls == []
        _assert_bounded_owner_reads(server.snapshot_counts())

        server.reset_counts()
        tok_auth = owner_authorization.set(auth["Authorization"])
        tok_tenant = owner_selected_tenant.set("tenant-dev")
        tok_svc = client.app.state.persona_service
        from services.control_plane.bff.personas.service import _current_persona_service
        tok_ctx = _current_persona_service.set(tok_svc)
        try:
            with request_read_scope():
                retired_rows = _pm12_persona_league_rows(
                    state="retired",
                    tenant_id="tenant-dev",
                )
                assert len(retired_rows) == 78
        finally:
            _current_persona_service.reset(tok_ctx)
            owner_selected_tenant.reset(tok_tenant)
            owner_authorization.reset(tok_auth)
        retired_counts = server.snapshot_counts()
        assert retired_counts.get("personas_collection") == 1
        assert retired_counts.get("runtime_bindings_collection", 0) == 0
        assert retired_counts.get("paper_fleet_sessions_collection", 0) == 0
        assert retired_counts.get("telemetry_summaries_collection", 0) == 0
        assert retired_counts.get("capability_snapshots_collection", 0) == 0
        assert retired_counts.get("strategy_specs_collection", 0) == 0

        # 2. Fleet archived view keeps returning retired personas
        server.reset_counts()
        resp_fleet_retired = client.get(
            "/bff/management/persona-fleet?state=retired&page_size=100",
            headers=auth,
        )
        assert resp_fleet_retired.status_code == 200
        fleet_items = resp_fleet_retired.json()["data"]["items"]
        assert len(fleet_items) == 78
        assert all(item["state"] == "retired" for item in fleet_items)

        # 3. q filter runs before per-persona enrichment and preserves full-league ranking_snapshot_id
        route_policy_calls.clear()
        allowed_action_calls.clear()
        server.reset_counts()
        resp_q = client.get(
            "/bff/management/persona-league?q=persona-active-005",
            headers=auth,
        )
        assert resp_q.status_code == 200
        q_payload = resp_q.json()
        q_items = q_payload["data"]["items"]
        assert [item["persona_id"] for item in q_items] == ["persona-active-005"]
        assert q_payload["data"]["ranking_snapshot_id"] == full_snapshot_id
        assert q_payload["data"]["summary"]["ranking_snapshot_id"] == full_snapshot_id
        assert q_payload["meta"]["ranking_snapshot_id"] == full_snapshot_id
        assert q_items[0]["ranking_snapshot_id"] == full_snapshot_id
        assert route_policy_calls == ["persona-active-005"]
        assert allowed_action_calls == ["persona-active-005"]
        _assert_bounded_owner_reads(server.snapshot_counts())

        # 4. page_size bounds per-page route_policy / allowed_actions enrichment and preserves ranking_snapshot_id
        route_policy_calls.clear()
        allowed_action_calls.clear()
        resp_page = client.get(
            "/bff/management/persona-league?state=active&page_size=5",
            headers=auth,
        )
        assert resp_page.status_code == 200
        page_payload = resp_page.json()
        page_body = page_payload["data"]
        assert page_body["summary"]["persona_count"] == 12
        assert len(page_body["items"]) == 5
        assert page_body["ranking_snapshot_id"] == full_snapshot_id
        assert page_body["summary"]["ranking_snapshot_id"] == full_snapshot_id
        assert page_payload["meta"]["ranking_snapshot_id"] == full_snapshot_id
        assert all(item["ranking_snapshot_id"] == full_snapshot_id for item in page_body["items"])
        assert len(route_policy_calls) == 5
        assert len(allowed_action_calls) == 5
    finally:
        server.stop()


def test_persona_health_reads_league_once_and_ppl_alloc_009_reuses_request_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """persona-health reads list_persona_league once; ppl-alloc-009 reuses request scope
    and evicts stale telemetry/league cache keys on refresh."""
    dataset = _build_fixture_dataset(total_personas=90, active_personas=12)
    proof_pid = "persona-ppl-alloc-009"
    proof_pool_id = "pool-ppl-alloc-009"
    proof_bind_id = "bind-ppl-alloc-009"
    proof_rt_id = "rt-ppl-alloc-009"
    proof_rb_id = "rb-ppl-alloc-009"
    proof_sess_id = "sess-ppl-alloc-009"
    proof_plan_id = "plan-ppl-alloc-009"
    proof_strategy_id = "strat-ppl-alloc-009"
    dataset["personas"].append(
        {
            "id": proof_pid,
            "persona_id": proof_pid,
            "tenant_id": "tenant-dev",
            "name": f"PPL ALLOC 009 {_PPL_ALLOC_009_ELIGIBILITY_RUN_KEY}",
            "lifecycle_state": "paper_running",
            "state": "paper_running",
            "strategy_family": "momentum",
            "required_data_sources": ["twse"],
            "metadata": {
                "tenant_id": "tenant-dev",
                "owner": "team-alpha",
                "archetype": "momentum",
                "provisioning_idempotency_key": "ppl-alloc-009-30095677466-persona-create",
                "capital_mode": "paper",
                "deployment_stage": "paper",
                "paper_ledger_id": f"paper-ledger-{proof_pid}",
                "live_capital_enabled": False,
                "live_write_enabled": False,
                "order_side_effects_allowed": False,
                "capital_side_effects_allowed": False,
                "runtime_id": proof_rt_id,
                "runtime_binding_id": proof_rb_id,
                "binding_id": proof_bind_id,
            },
        }
    )
    dataset["capital_pools"].append(
        {
            "id": proof_pool_id,
            "capital_pool_id": proof_pool_id,
            "tenant_id": "tenant-dev",
            "name": "PPL ALLOC 009 Paper Pool",
            "status": "active",
            "mode": "paper",
            "metadata": {
                "internal": True,
                "execution_context": "paper",
                "persona_id": proof_pid,
            },
        }
    )
    dataset["bindings"].append(
        {
            "id": proof_bind_id,
            "binding_id": proof_bind_id,
            "persona_capital_binding_id": proof_bind_id,
            "persona_id": proof_pid,
            "capital_pool_id": proof_pool_id,
            "tenant_id": "tenant-dev",
            "role": "paper_owner",
            "status": "active",
            "validity": "active",
            "deployment_stage": "paper",
            "allowed_deployment_scope": "paper",
        }
    )
    dataset["runtime_bindings"].append(
        {
            "id": proof_rb_id,
            "runtime_binding_id": proof_rb_id,
            "binding_id": proof_bind_id,
            "persona_capital_binding_id": proof_bind_id,
            "runtime_id": proof_rt_id,
            "persona_id": proof_pid,
            "capital_pool_id": proof_pool_id,
            "tenant_id": "tenant-dev",
            "status": "running",
            "state": "running",
            "deployment_mode": "paper",
            "deployment_stage": "paper",
            "artifact_id": "art-ppl-alloc-009",
            "artifact_version": "1.0.0",
            "plan_id": proof_plan_id,
            "strategy_id": proof_strategy_id,
            "effective_at": "2026-10-20T09:00:00Z",
            "updated_at": "2026-10-20T10:00:00Z",
        }
    )
    proof_sess = {
        "id": proof_sess_id,
        "session_id": proof_sess_id,
        "persona_id": proof_pid,
        "runtime_id": proof_rt_id,
        "runtime_binding_id": proof_rb_id,
        "tenant_id": "tenant-dev",
        "status": "active",
        "state": "running",
        "session_type": "paper_runtime_monitoring",
        "deployment_stage": "paper",
        "last_heartbeat_at": "2026-10-20T10:00:00Z",
    }
    dataset["sessions"].append(proof_sess)
    dataset["paper_sessions"].append(dict(proof_sess))
    proof_telemetry = {
        "id": "tel-ppl-alloc-009",
        "runtime_id": proof_rt_id,
        "persona_id": proof_pid,
        "tenant_id": "tenant-dev",
        "pnl": 0.18,
        "drawdown": 0.02,
        "max_drawdown": 0.02,
        "fill_rate": 0.99,
        "avg_slippage_bps": 1.1,
        "total_trades": 30,
        "collected_at": "2026-10-20T10:00:00Z",
    }
    dataset["telemetry_summaries"].append(proof_telemetry)
    dataset["capability_snapshots"].append(
        {
            "id": "cap-ppl-alloc-009",
            "snapshot_id": "cap-ppl-alloc-009",
            "persona_id": proof_pid,
            "tenant_id": "tenant-dev",
            "effective_skills": ["skill-a"],
            "effective_tools": ["tool-a"],
            "effective_workflows": ["wf-a"],
            "generated_at": "2026-10-20T09:00:00Z",
        }
    )
    dataset["deployment_plans"] = [
        {
            "id": proof_plan_id,
            "plan_id": proof_plan_id,
            "tenant_id": "tenant-dev",
            "strategy_id": proof_strategy_id,
            "capital_pool_id": proof_pool_id,
            "status": "active",
        }
    ]
    server = CountingOwnerServer(dataset)
    server.start()
    try:
        client = _build_client(server, monkeypatch)
        auth = {"Authorization": _make_auth_header("tenant-dev")}
        read_store = client.app.state.persona_service._read_store

        league_calls = 0
        orig_list_league = read_store.list_persona_league

        def _counting_list_league(*args: Any, **kwargs: Any) -> Any:
            nonlocal league_calls
            league_calls += 1
            return orig_list_league(*args, **kwargs)

        monkeypatch.setattr(read_store, "list_persona_league", _counting_list_league)
        resp_health = client.get("/bff/v5/execution/persona-health", headers=auth)
        assert resp_health.status_code == 200
        assert league_calls == 1, f"Expected persona-health to read league once, got {league_calls}"

        # Verify ppl-alloc-009 context + refresh inside a single request_read_scope
        server.reset_counts()
        identity = _extract_identity(auth["Authorization"])
        tok_auth = owner_authorization.set(auth["Authorization"])
        tok_tenant = owner_selected_tenant.set("tenant-dev")
        tok_ctx = _current_persona_service.set(client.app.state.persona_service)
        try:
            with request_read_scope():
                ctx = _ppl_alloc_009_paper_eligibility_context(
                    persona_id=proof_pid,
                    identity=identity,
                    observed_at="2026-10-20T10:00:00Z",
                )
                assert ctx["strategy_id"] == proof_strategy_id
                assert ctx["paper_session_id"] == proof_sess_id
                assert ctx["paper_ledger_id"] == f"paper-ledger-{proof_pid}"
                assert ctx["capital"]["binding_id"] == proof_bind_id
                assert ctx["capital"]["capital_pool_id"] == proof_pool_id
                assert ctx["runtime_binding"]["runtime_binding_id"] == proof_rb_id
                assert ctx["ranking_item"]["persona_id"] == proof_pid
                assert ctx["ranking_item"]["runtime_resolution"] == "active"
                assert ctx["ranking_item"]["session_resolution"] == "active"
                assert ctx["ranking_item"]["stage"] == "paper_running"
                assert ctx["ranking_item"]["capital_scope"] == "paper_ledger"

                counts_after_helper = server.snapshot_counts()
                assert counts_after_helper.get("personas_collection", 0) == 1
                assert counts_after_helper.get("bindings_collection", 0) == 1
                assert counts_after_helper.get("runtime_bindings_collection", 0) == 1
                assert counts_after_helper.get("capital_pools_collection", 0) == 1
                assert counts_after_helper.get("sessions_collection", 0) == 1
                assert counts_after_helper.get("paper_fleet_sessions_collection", 0) == 1
                assert counts_after_helper.get("telemetry_summaries_collection", 0) == 1
                assert counts_after_helper.get("capability_snapshots_collection", 0) == 1
                assert counts_after_helper.get("strategy_specs_collection", 0) == 1
                assert counts_after_helper.get("deployment_plans_collection", 0) == 1

                reused_rows = _pm12_persona_league_rows(
                    q=proof_pid,
                    tenant_id="tenant-dev",
                    include_route_policy=False,
                    include_health=False,
                    include_allowed_actions=False,
                )
                assert len(reused_rows) == 1
                reused_item = _pm12_persona_league_ranking_item(reused_rows[0])
                assert reused_item["metrics"]["pnl"] == pytest.approx(0.18)
                counts_before_evict = server.snapshot_counts()
                assert counts_before_evict == counts_after_helper

                proof_telemetry["pnl"] = 0.42
                evict_request_read_keys("telemetry", "pm12_league_rows")
                refreshed_rows = _pm12_persona_league_rows(
                    q=proof_pid,
                    tenant_id="tenant-dev",
                    include_route_policy=False,
                    include_health=False,
                    include_allowed_actions=False,
                )
                assert len(refreshed_rows) == 1
                refreshed_item = _pm12_persona_league_ranking_item(refreshed_rows[0])
                assert refreshed_item["metrics"]["pnl"] == pytest.approx(0.42)
                counts_after_evict = server.snapshot_counts()
                assert counts_after_evict.get("personas_collection", 0) == 1
                assert counts_after_evict.get("bindings_collection", 0) == 1
                assert counts_after_evict.get("runtime_bindings_collection", 0) == 1
                assert counts_after_evict.get("capital_pools_collection", 0) == 1
                assert counts_after_evict.get("sessions_collection", 0) == 1
                assert counts_after_evict.get("paper_fleet_sessions_collection", 0) == 1
                assert counts_after_evict.get("capability_snapshots_collection", 0) == 1
                assert counts_after_evict.get("strategy_specs_collection", 0) == 1
                assert counts_after_evict.get("deployment_plans_collection", 0) == 1
                assert counts_after_evict.get("telemetry_summaries_collection", 0) == 2
        finally:
            _current_persona_service.reset(tok_ctx)
            owner_selected_tenant.reset(tok_tenant)
            owner_authorization.reset(tok_auth)
    finally:
        server.stop()
