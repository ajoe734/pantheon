"""Regression tests for STRATEGY-METRICS-TRUTH-20261104.

Proves using the real hosted ranking snapshot row shapes and registry metadata shapes:
1. project_strategy_dto no longer emits a hardcoded zero; pnl30d, sharpe, and
   drawdown are null (None) whenever no owner supplied a value, and strategy
   create and PATCH cannot default or set these fields.
2. Strategy list and detail read the latest ranking snapshot at most once per
   request and match rows through strategy_ids; when a unique row has telemetry,
   pnl, drawdown, totalTrades, metricsAsOf, metricsResolution, and
   metricsPersonaId are exposed unchanged.
3. pnl30d and sharpe stay null; pnl is the absolute owner value (never converted
   to a percentage); drawdown is the owner's dimensionless peak-to-trough ratio.
4. Ambiguous multi-row mappings return null metrics with metricsResolution
   "ambiguous"; failed ranking reads return null metrics with metricsResolution
   "unavailable".
5. Missing telemetry (telemetry_resolution == "missing") returns null metrics
   and not zero; chain personas with stage == "not_running" and
   telemetry_resolution == "fresh" still expose real metrics (decided from
   telemetry_resolution, not stage).
6. capitalPoolId is populated from registry metadata.capital_pool_id when
   execution_profile, governance, and summary lookups are empty, and is empty
   only when the owner truly has none.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import urllib.parse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.command_adapters import base as adapter_base
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.ports.rankings import RankingSnapshotReadPort
from services.control_plane.bff.ports.read_surface_ports import (
    ReadSurfacePorts,
    create_read_surface_ports,
)
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
)
from services.control_plane.bff.strategies.router import create_strategies_router
from services.rankings.store import RankingSnapshotRecord


REAL_SNAPSHOT_ITEMS: List[Dict[str, Any]] = [
    {
        "persona_id": "persona-paper-running-01",
        "strategy_ids": ["strat-paper-running-01"],
        "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
        "stage": "paper_running",
        "metrics": {
            "pnl": 110.0,
            "drawdown": 0.0,
            "fill_rate": 1.0,
            "total_trades": 3,
            "runtime_count": 1,
            "avg_slippage_bps": 0.0,
            "latest_telemetry_at": "2026-11-04T01:16:27Z",
            "telemetry_resolution": "fresh",
            "telemetry_evidence_refs": ["telemetry-ev-001"],
        },
    },
    {
        "persona_id": "persona-paper-neg-02",
        "strategy_ids": ["strat-neg-pnl-02"],
        "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
        "stage": "paper_running",
        "metrics": {
            "pnl": -5.5,
            "drawdown": 0.000055,
            "fill_rate": 1.0,
            "total_trades": 2,
            "runtime_count": 1,
            "avg_slippage_bps": 0.0,
            "latest_telemetry_at": "2026-11-04T01:16:27Z",
            "telemetry_resolution": "fresh",
            "telemetry_evidence_refs": ["telemetry-ev-002"],
        },
    },
    {
        "persona_id": "persona-paper-zero-03",
        "strategy_ids": ["strat-zero-pnl-03"],
        "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
        "stage": "paper_running",
        "metrics": {
            "pnl": 0.0,
            "drawdown": 0.0,
            "fill_rate": 1.0,
            "total_trades": 1,
            "runtime_count": 1,
            "avg_slippage_bps": 0.0,
            "latest_telemetry_at": "2026-11-04T01:16:27Z",
            "telemetry_resolution": "fresh",
            "telemetry_evidence_refs": ["telemetry-ev-003"],
        },
    },
    {
        "persona_id": "persona-chain-fresh-04",
        "strategy_ids": ["strat-chain-persona-04"],
        "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
        "stage": "not_running",
        "metrics": {
            "pnl": 110.0,
            "drawdown": 0.0,
            "fill_rate": 1.0,
            "total_trades": 3,
            "runtime_count": 1,
            "avg_slippage_bps": 0.0,
            "latest_telemetry_at": "2026-11-04T01:16:27Z",
            "telemetry_resolution": "fresh",
            "telemetry_evidence_refs": ["telemetry-ev-004"],
        },
    },
    {
        "persona_id": "persona-not-running-05",
        "strategy_ids": ["strat-not-running-05"],
        "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
        "stage": "not_running",
        "metrics": {
            "pnl": None,
            "drawdown": None,
            "fill_rate": None,
            "total_trades": 0,
            "runtime_count": 0,
            "avg_slippage_bps": None,
            "latest_telemetry_at": None,
            "telemetry_resolution": "missing",
            "telemetry_evidence_refs": [],
        },
    },
    {
        "persona_id": "persona-unlinked-06",
        "strategy_ids": [],
        "capital_pool_id": None,
        "stage": "not_running",
        "metrics": {
            "pnl": None,
            "drawdown": None,
            "fill_rate": None,
            "total_trades": 0,
            "runtime_count": 0,
            "avg_slippage_bps": None,
            "latest_telemetry_at": None,
            "telemetry_resolution": "missing",
            "telemetry_evidence_refs": [],
        },
    },
]


class _FakeRecordsTable:
    def __init__(self, payloads: List[Dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.list_all_calls = 0

    def list_all(self) -> List[Dict[str, Any]]:
        self.list_all_calls += 1
        return list(self._payloads)


class _FakeRankingStore:
    def __init__(self, payloads: List[Dict[str, Any]]) -> None:
        self._records_table = _FakeRecordsTable(payloads)


class _InMemoryStrategyWriteOwner:
    def __init__(self, port: DefaultResearchKnowledgeSourcePort) -> None:
        self._port = port
        self.upserted: List[Dict[str, Any]] = []

    def upsert_strategy(self, record: Dict[str, Any]) -> Dict[str, Any]:
        self.upserted.append(dict(record))
        sid = str(record.get("strategy_id") or record.get("id") or "")
        self._port._strategy_specs[sid] = {
            "strategy_id": sid,
            "title": record.get("name") or sid,
            "name": record.get("name") or sid,
            "lifecycle_state": record.get("state") or "draft",
            "risk": record.get("risk") or "medium",
            "persona_ids": list(record.get("personaIds") or []),
            "capital_pool_id": record.get("capitalPoolId") or "",
            "current_spec_version_id": f"spec-{sid}-v1",
            "current_spec_version": "1.0.0",
            "versions": [
                {
                    "spec_version_id": f"spec-{sid}-v1",
                    "spec_version": "1.0.0",
                    "title": record.get("name") or sid,
                    "lifecycle_state": record.get("state") or "draft",
                    "risk": record.get("risk") or "medium",
                    "persona_ids": list(record.get("personaIds") or []),
                }
            ],
        }
        return dict(record)


def _make_snapshot_payload(
    snapshot_id: str,
    created_at: str,
    items: List[Dict[str, Any]],
    *,
    surface: str = "quarterly",
) -> Dict[str, Any]:
    record = RankingSnapshotRecord(
        ranking_snapshot_id=snapshot_id,
        surface=surface,
        period="2026-Q4",
        formula_version="pm12-v1",
        content_digest=f"sha256:{snapshot_id}",
        items=items,
        evidence_assertion_digests={
            str(item["persona_id"]): [f"digest-{item['persona_id']}"]
            for item in items
            if item.get("persona_id")
        },
        created_at=created_at,
    )
    return record.to_canonical_dict()


def _build_strategy_specs() -> Dict[str, Dict[str, Any]]:
    specs: Dict[str, Dict[str, Any]] = {}
    defs = [
        ("strat-paper-running-01", "Running Strategy 01", "pool-persona-paper-fd1281847a3b1c6a9735"),
        ("strat-neg-pnl-02", "Negative PnL Strategy 02", "pool-persona-paper-fd1281847a3b1c6a9735"),
        ("strat-zero-pnl-03", "Zero PnL Strategy 03", "pool-persona-paper-fd1281847a3b1c6a9735"),
        ("strat-chain-persona-04", "Chain Persona Strategy 04", "pool-persona-paper-fd1281847a3b1c6a9735"),
        ("strat-not-running-05", "Not Running Strategy 05", ""),
        ("strat-unlinked-06", "Unlinked Strategy 06", ""),
    ]
    for sid, title, metadata_pool_id in defs:
        meta = {"capital_pool_id": metadata_pool_id} if metadata_pool_id else {}
        specs[sid] = {
            "strategy_id": sid,
            "title": title,
            "name": title,
            "lifecycle_state": "approved",
            "risk": "medium",
            "metadata": dict(meta),
            "current_spec_version_id": f"reg-{sid}-v1",
            "current_spec_version": "1.0.0",
            "versions": [
                {
                    "spec_version_id": f"reg-{sid}-v1",
                    "spec_version": "1.0.0",
                    "title": title,
                    "lifecycle_state": "approved",
                    "risk": "medium",
                    "metadata": dict(meta),
                }
            ],
        }
    return specs


def _build_test_client(
    *,
    specs: Dict[str, Dict[str, Any]],
    ranking_store: Any = None,
    latest_ranking_snapshot_reader: Optional[Any] = None,
) -> tuple[TestClient, DefaultResearchKnowledgeSourcePort, _InMemoryStrategyWriteOwner]:
    rks_port = DefaultResearchKnowledgeSourcePort(strategy_specs_store=specs)
    rks_port._has_injected_strategy_specs = True
    write_owner = _InMemoryStrategyWriteOwner(rks_port)
    if latest_ranking_snapshot_reader is not None:
        read_ports = ReadSurfacePorts(
            research_knowledge_source=rks_port,
            latest_ranking_snapshot_reader=latest_ranking_snapshot_reader,
        )
    else:
        read_ports = create_read_surface_ports(
            research_knowledge_source=rks_port,
            ranking_store=ranking_store,
        )
    app = FastAPI()
    app.include_router(
        create_strategies_router(
            read_surface=read_ports,
            strategy_write_owner=write_owner,
            list_strategy_summaries=read_ports.list_strategy_specs,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-11-04T01:20:00Z",
        )
    )
    app.add_middleware(OwnerReadContextMiddleware)
    return TestClient(app), rks_port, write_owner


HEADERS = {"Authorization": "Bearer op-metrics:operator:tenant-dev", "X-Tenant-Id": "tenant-dev"}


def test_strategy_list_and_detail_project_real_ranking_snapshot_and_read_once_per_request() -> None:
    older_snapshot = _make_snapshot_payload(
        "snap-2026-q4-older",
        "2026-11-04T01:01:27Z",
        [
            {
                **REAL_SNAPSHOT_ITEMS[0],
                "metrics": {**REAL_SNAPSHOT_ITEMS[0]["metrics"], "pnl": 25.0},
            }
        ],
    )
    latest_snapshot = _make_snapshot_payload(
        "snap-2026-q4-latest",
        "2026-11-04T01:16:27Z",
        REAL_SNAPSHOT_ITEMS,
    )
    fake_store = _FakeRankingStore([older_snapshot, latest_snapshot])
    client, _, _ = _build_test_client(specs=_build_strategy_specs(), ranking_store=fake_store)

    list_resp = client.get("/bff/strategies", headers=HEADERS)
    assert list_resp.status_code == 200, list_resp.text
    # 6 strategies in list, but the ranking snapshot table is read exactly once for the request
    assert fake_store._records_table.list_all_calls == 1

    by_id = {item["id"]: item for item in list_resp.json()["data"]}

    # 1. Running row (pnl: 110.0, drawdown: 0.0, total_trades: 3, fresh)
    s1 = by_id["strat-paper-running-01"]
    assert s1["pnl"] == 110.0
    assert s1["drawdown"] == 0.0
    assert s1["totalTrades"] == 3
    assert s1["metricsAsOf"] == "2026-11-04T01:16:27Z"
    assert s1["metricsResolution"] == "fresh"
    assert s1["metricsPersonaId"] == "persona-paper-running-01"
    assert s1["pnl30d"] is None
    assert s1["sharpe"] is None
    assert s1["capitalPoolId"] == "pool-persona-paper-fd1281847a3b1c6a9735"

    # 2. Negative PnL with fractional drawdown (pnl: -5.5, drawdown: 0.000055)
    s2 = by_id["strat-neg-pnl-02"]
    assert s2["pnl"] == -5.5
    assert s2["drawdown"] == 0.000055
    assert s2["totalTrades"] == 2
    assert s2["metricsAsOf"] == "2026-11-04T01:16:27Z"
    assert s2["metricsResolution"] == "fresh"
    assert s2["metricsPersonaId"] == "persona-paper-neg-02"
    assert s2["pnl30d"] is None
    assert s2["sharpe"] is None

    # 3. Real zero PnL and zero drawdown with fresh telemetry stay 0.0 (not null)
    s3 = by_id["strat-zero-pnl-03"]
    assert s3["pnl"] == 0.0
    assert s3["drawdown"] == 0.0
    assert s3["totalTrades"] == 1
    assert s3["metricsResolution"] == "fresh"
    assert s3["metricsPersonaId"] == "persona-paper-zero-03"
    assert s3["pnl30d"] is None
    assert s3["sharpe"] is None

    # 4. Chain persona with stage == "not_running" and telemetry_resolution == "fresh"
    s4 = by_id["strat-chain-persona-04"]
    assert s4["pnl"] == 110.0
    assert s4["drawdown"] == 0.0
    assert s4["totalTrades"] == 3
    assert s4["metricsAsOf"] == "2026-11-04T01:16:27Z"
    assert s4["metricsResolution"] == "fresh"
    assert s4["metricsPersonaId"] == "persona-chain-fresh-04"

    # 5. Not running row with telemetry_resolution == "missing" and pnl: null -> null metrics, not 0.0
    s5 = by_id["strat-not-running-05"]
    assert s5["pnl"] is None
    assert s5["drawdown"] is None
    assert s5["pnl30d"] is None
    assert s5["sharpe"] is None
    assert s5["totalTrades"] is None
    assert s5["metricsAsOf"] is None
    assert s5["metricsResolution"] == "missing"
    assert s5["capitalPoolId"] == ""

    # 6. Strategy with no matching strategy_ids in snapshot -> null metrics, not 0.0
    s6 = by_id["strat-unlinked-06"]
    assert s6["pnl"] is None
    assert s6["drawdown"] is None
    assert s6["pnl30d"] is None
    assert s6["sharpe"] is None
    assert s6["totalTrades"] is None
    assert s6["metricsAsOf"] is None
    assert s6["metricsResolution"] == "missing"
    assert s6["metricsPersonaId"] is None
    assert s6["capitalPoolId"] == ""

    # Detail endpoint reads the latest ranking snapshot once per request as well
    detail_resp = client.get("/bff/strategies/strat-paper-running-01", headers=HEADERS)
    assert detail_resp.status_code == 200, detail_resp.text
    assert fake_store._records_table.list_all_calls == 2
    detail_dto = detail_resp.json()["data"]
    assert detail_dto["pnl"] == 110.0
    assert detail_dto["drawdown"] == 0.0
    assert detail_dto["totalTrades"] == 3
    assert detail_dto["metricsAsOf"] == "2026-11-04T01:16:27Z"
    assert detail_dto["metricsResolution"] == "fresh"
    assert detail_dto["metricsPersonaId"] == "persona-paper-running-01"
    assert detail_dto["pnl30d"] is None
    assert detail_dto["sharpe"] is None
    assert detail_dto["capitalPoolId"] == "pool-persona-paper-fd1281847a3b1c6a9735"


def test_ambiguous_multi_row_mapping_and_unavailable_ranking_read() -> None:
    ambiguous_items = [
        {
            **REAL_SNAPSHOT_ITEMS[0],
            "persona_id": "persona-a",
            "strategy_ids": ["strat-paper-running-01"],
        },
        {
            **REAL_SNAPSHOT_ITEMS[1],
            "persona_id": "persona-b",
            "strategy_ids": ["strat-paper-running-01"],
        },
    ]
    ambiguous_snapshot = _make_snapshot_payload(
        "snap-ambiguous",
        "2026-11-04T01:16:27Z",
        ambiguous_items,
    )
    fake_store = _FakeRankingStore([ambiguous_snapshot])
    client, _, _ = _build_test_client(specs=_build_strategy_specs(), ranking_store=fake_store)

    resp = client.get("/bff/strategies/strat-paper-running-01", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    dto = resp.json()["data"]
    assert dto["pnl"] is None
    assert dto["drawdown"] is None
    assert dto["pnl30d"] is None
    assert dto["sharpe"] is None
    assert dto["totalTrades"] is None
    assert dto["metricsAsOf"] is None
    assert dto["metricsPersonaId"] is None
    assert dto["metricsResolution"] == "ambiguous"

    # Now simulate a ranking store failure -> metrics stay null and metricsResolution == "unavailable"
    call_count = 0

    def _failing_reader(surface: str = "quarterly") -> Dict[str, Any]:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("rankings store connection failed")

    fail_client, _, _ = _build_test_client(
        specs=_build_strategy_specs(),
        latest_ranking_snapshot_reader=_failing_reader,
    )
    list_resp = fail_client.get("/bff/strategies", headers=HEADERS)
    assert list_resp.status_code == 200, list_resp.text
    assert call_count == 1
    for item in list_resp.json()["data"]:
        assert item["pnl"] is None
        assert item["drawdown"] is None
        assert item["pnl30d"] is None
        assert item["sharpe"] is None
        assert item["totalTrades"] is None
        assert item["metricsAsOf"] is None
        assert item["metricsPersonaId"] is None
        assert item["metricsResolution"] == "unavailable"


def test_create_and_patch_strategy_cannot_default_or_set_pnl30d_sharpe_drawdown() -> None:
    fake_store = _FakeRankingStore([])
    client, _, _ = _build_test_client(specs=_build_strategy_specs(), ranking_store=fake_store)

    create_resp = client.post(
        "/bff/strategies",
        headers={**HEADERS, "Idempotency-Key": "create-metrics-truth-01"},
        json={
            "name": "Created Strategy",
            "pnl30d": 42.5,
            "sharpe": 2.1,
            "drawdown": 0.15,
            "pnl": 999.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()["data"]
    assert created["pnl"] is None
    assert created["pnl30d"] is None
    assert created["sharpe"] is None
    assert created["drawdown"] is None
    assert created["totalTrades"] is None
    assert created["metricsAsOf"] is None
    assert created["metricsResolution"] == "missing"
    assert created["metricsPersonaId"] is None

    strategy_id = created["id"]
    patch_resp = client.patch(
        f"/bff/strategies/{strategy_id}",
        headers={**HEADERS, "Idempotency-Key": "patch-metrics-truth-01"},
        json={
            "name": "Patched Strategy Name",
            "pnl30d": 88.0,
            "sharpe": 3.4,
            "drawdown": 0.25,
            "pnl": 555.0,
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patched = patch_resp.json()["data"]
    assert patched["name"] == "Patched Strategy Name"
    assert patched["pnl"] is None
    assert patched["pnl30d"] is None
    assert patched["sharpe"] is None
    assert patched["drawdown"] is None


def test_capital_pool_id_populated_from_registry_metadata_capital_pool_id(monkeypatch: Any) -> None:
    registry_entries = [
        {
            "registry_id": "reg-spec-with-pool",
            "artifact_type": "strategy_spec",
            "strategy_id": "strat-registry-pool",
            "version": "1.0.0",
            "artifact_state": "approved",
            "owner_tenant": "tenant-dev",
            "created_at": "2026-11-04T00:00:00Z",
            "updated_at": "2026-11-04T01:00:00Z",
            "metadata": {
                "tenant_id": "tenant-dev",
                "title": "Registry Pool Strategy",
                "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
                "strategy_spec": {
                    "strategy_id": "strat-registry-pool",
                    "spec_version": "1.0.0",
                    "title": "Registry Pool Strategy",
                    "lifecycle_state": "approved",
                    "execution_profile": {},
                    "governance": {},
                },
            },
        },
        {
            "registry_id": "reg-spec-without-pool",
            "artifact_type": "strategy_spec",
            "strategy_id": "strat-registry-no-pool",
            "version": "1.0.0",
            "artifact_state": "approved",
            "owner_tenant": "tenant-dev",
            "created_at": "2026-11-04T00:00:00Z",
            "updated_at": "2026-11-04T01:00:00Z",
            "metadata": {
                "tenant_id": "tenant-dev",
                "title": "Registry No Pool Strategy",
                "strategy_spec": {
                    "strategy_id": "strat-registry-no-pool",
                    "spec_version": "1.0.0",
                    "title": "Registry No Pool Strategy",
                    "lifecycle_state": "approved",
                    "execution_profile": {},
                    "governance": {},
                },
            },
        },
    ]

    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.local")

    def _fake_http_request_json(url: str, **kwargs: Any) -> Any:
        parsed = urllib.parse.urlparse(url)
        if parsed.path.endswith("/strat-registry-pool/strategy-specs"):
            return [registry_entries[0]]
        if parsed.path.endswith("/strat-registry-no-pool/strategy-specs"):
            return [registry_entries[1]]
        return list(registry_entries)

    monkeypatch.setattr(adapter_base, "http_request_json", _fake_http_request_json)

    rks_port = DefaultResearchKnowledgeSourcePort()
    read_ports = create_read_surface_ports(
        research_knowledge_source=rks_port,
        ranking_store=_FakeRankingStore([]),
    )
    app = FastAPI()
    app.include_router(
        create_strategies_router(
            read_surface=read_ports,
            list_strategy_summaries=read_ports.list_strategy_specs,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-11-04T01:20:00Z",
        )
    )
    app.add_middleware(OwnerReadContextMiddleware)
    client = TestClient(app)

    list_resp = client.get("/bff/strategies", headers=HEADERS)
    assert list_resp.status_code == 200, list_resp.text
    by_id = {item["id"]: item for item in list_resp.json()["data"]}
    assert by_id["strat-registry-pool"]["capitalPoolId"] == "pool-persona-paper-fd1281847a3b1c6a9735"
    assert by_id["strat-registry-no-pool"]["capitalPoolId"] == ""

    detail_resp = client.get("/bff/strategies/strat-registry-pool", headers=HEADERS)
    assert detail_resp.status_code == 200, detail_resp.text
    assert detail_resp.json()["data"]["capitalPoolId"] == "pool-persona-paper-fd1281847a3b1c6a9735"


def test_ranking_snapshot_read_port_selects_latest_snapshot_by_surface() -> None:
    snap_q3 = _make_snapshot_payload("snap-q3", "2026-11-04T01:00:00Z", REAL_SNAPSHOT_ITEMS[:1], surface="quarterly")
    snap_q4 = _make_snapshot_payload("snap-q4", "2026-11-04T01:16:27Z", REAL_SNAPSHOT_ITEMS, surface="quarterly")
    snap_other = _make_snapshot_payload("snap-monthly", "2026-11-04T02:00:00Z", [], surface="monthly")
    port = RankingSnapshotReadPort(_FakeRankingStore([snap_q3, snap_q4, snap_other]))

    latest = port.get_latest_ranking_snapshot(surface="quarterly")
    assert latest is not None
    assert latest["ranking_snapshot_id"] == "snap-q4"
    assert len(latest["items"]) == len(REAL_SNAPSHOT_ITEMS)


def test_strategy_alpha_comes_only_from_market_scope_alpha_and_agrees_between_list_and_detail() -> None:
    specs = _build_strategy_specs()
    # Add source_kind and hypothesis without market_scope.alpha -> alpha must be ""
    specs["strat-paper-running-01"]["versions"][0]["source_kind"] = "workflow"
    specs["strat-paper-running-01"]["versions"][0]["hypothesis"] = "Intraday momentum persists."
    specs["strat-paper-running-01"]["versions"][0]["market_scope"] = {"markets": ["TWSE"]}
    # Add explicit market_scope.alpha -> alpha must equal market_scope.alpha on both list and detail
    specs["strat-zero-pnl-03"]["versions"][0]["source_kind"] = "workflow"
    specs["strat-zero-pnl-03"]["versions"][0]["hypothesis"] = "Cross-sectional value."
    specs["strat-zero-pnl-03"]["versions"][0]["market_scope"] = {"markets": ["TWSE"], "alpha": "value-factor-tw"}

    latest_snapshot = _make_snapshot_payload(
        "snap-2026-q4-alpha",
        "2026-11-04T01:16:27Z",
        REAL_SNAPSHOT_ITEMS,
    )
    client, _, _ = _build_test_client(specs=specs, ranking_store=_FakeRankingStore([latest_snapshot]))

    list_resp = client.get("/bff/strategies", headers=HEADERS)
    assert list_resp.status_code == 200, list_resp.text
    by_id = {item["id"]: item for item in list_resp.json()["data"]}

    detail_01 = client.get("/bff/strategies/strat-paper-running-01", headers=HEADERS)
    assert detail_01.status_code == 200, detail_01.text
    assert by_id["strat-paper-running-01"]["alpha"] == ""
    assert detail_01.json()["data"]["alpha"] == ""
    assert by_id["strat-paper-running-01"]["pnl"] == 110.0

    detail_03 = client.get("/bff/strategies/strat-zero-pnl-03", headers=HEADERS)
    assert detail_03.status_code == 200, detail_03.text
    assert by_id["strat-zero-pnl-03"]["alpha"] == "value-factor-tw"
    assert detail_03.json()["data"]["alpha"] == "value-factor-tw"
    assert by_id["strat-zero-pnl-03"]["pnl"] == 0.0
    assert detail_03.json()["data"]["pnl"] == 0.0

