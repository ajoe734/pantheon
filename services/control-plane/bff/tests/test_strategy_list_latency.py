"""Regression tests for BFF-STRATEGY-LIST-LATENCY-20261106.

Covers production-scale (>500 strategies, 140 historical ranking snapshots with
544 persona items) behavior for ``GET /bff/strategies``:
1. Full tenant-scoped strategy population (>500 records) is preserved with exact
   ``total``, deterministic multi-page ``page_token`` / ``page_size`` pagination,
   and exact ``state`` and ``persona_id`` filtering without truncating or
   silently dropping records.
2. Running metrics match the latest authoritative ranking snapshot while
   unavailable/missing/ambiguous values remain ``null`` (``None``) and real zero
   (``0.0``) telemetry values are preserved.
3. Expensive owner reads are bounded:
   - Canonical summaries (with ``current_spec_version_id``) perform zero
     per-strategy ``get_strategy_spec_detail`` calls.
   - Thin summaries (without ``current_spec_version_id``) bound
     ``get_strategy_spec_detail`` enrichment strictly to the sliced page
     (``<= page_size``) rather than all >500 strategies.
   - Ranking snapshot reads query ``LIMIT 1`` on Postgres-backed stores and
     decode only the single latest snapshot candidate rather than all historical
     snapshots.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.ports.read_surface_ports import (
    ReadSurfacePorts,
    create_read_surface_ports,
)
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
)
from services.control_plane.bff.strategies import service as strategies_service_mod
from services.control_plane.bff.strategies.router import create_strategies_router
from services.control_plane.bff.strategies.routes.common import (
    default_page_slice,
    default_read_surface_meta,
)
import services.rankings.store as rankings_store_mod
from services.rankings.store import RankingSnapshotRecord

HEADERS = {"Authorization": "Bearer op-latency:operator:tenant-dev", "X-Tenant-Id": "tenant-dev"}

_LIFECYCLE_MAP = {
    "draft": "draft",
    "candidate": "review",
    "review": "review",
    "approved": "approved",
    "active": "deployed",
    "deployed": "deployed",
    "retired": "retired",
}


def _normalize_lifecycle_state(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _LIFECYCLE_MAP.get(text, "draft")


def _normalize_risk_level(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"info", "low", "medium", "high", "critical"} else "medium"


def _build_544_persona_ranking_items(*, latest: bool) -> List[Dict[str, Any]]:
    """Build a 544-persona ranking item list matching the hosted Dev snapshot scale."""
    items: List[Dict[str, Any]] = []
    pnl_running = 110.0 if latest else 15.0
    for idx in range(1, 545):
        persona_id = f"persona-scale-{idx:04d}"
        strategy_id = f"strat-scale-{idx:04d}"
        if idx == 1:
            # Positive running PnL with 0.0 drawdown
            items.append(
                {
                    "persona_id": persona_id,
                    "strategy_ids": [strategy_id],
                    "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
                    "stage": "paper_running",
                    "metrics": {
                        "pnl": pnl_running,
                        "drawdown": 0.0,
                        "fill_rate": 1.0,
                        "total_trades": 3,
                        "runtime_count": 1,
                        "avg_slippage_bps": 0.0,
                        "latest_telemetry_at": "2026-11-05T17:40:00Z",
                        "telemetry_resolution": "fresh",
                        "telemetry_evidence_refs": ["telemetry-ev-001"],
                    },
                }
            )
        elif idx == 2:
            # Negative PnL with fractional drawdown
            items.append(
                {
                    "persona_id": persona_id,
                    "strategy_ids": [strategy_id],
                    "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
                    "stage": "paper_running",
                    "metrics": {
                        "pnl": -5.5,
                        "drawdown": 0.000055,
                        "fill_rate": 1.0,
                        "total_trades": 2,
                        "runtime_count": 1,
                        "avg_slippage_bps": 0.0,
                        "latest_telemetry_at": "2026-11-05T17:40:00Z",
                        "telemetry_resolution": "fresh",
                        "telemetry_evidence_refs": ["telemetry-ev-002"],
                    },
                }
            )
        elif idx == 3:
            # Real zero PnL and real zero drawdown
            items.append(
                {
                    "persona_id": persona_id,
                    "strategy_ids": [strategy_id],
                    "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
                    "stage": "paper_running",
                    "metrics": {
                        "pnl": 0.0,
                        "drawdown": 0.0,
                        "fill_rate": 1.0,
                        "total_trades": 1,
                        "runtime_count": 1,
                        "avg_slippage_bps": 0.0,
                        "latest_telemetry_at": "2026-11-05T17:40:00Z",
                        "telemetry_resolution": "fresh",
                        "telemetry_evidence_refs": ["telemetry-ev-003"],
                    },
                }
            )
        elif idx == 4:
            # Chain persona with stage == "not_running" but fresh telemetry
            items.append(
                {
                    "persona_id": persona_id,
                    "strategy_ids": [strategy_id],
                    "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
                    "stage": "not_running",
                    "metrics": {
                        "pnl": 110.0,
                        "drawdown": 0.0,
                        "fill_rate": 1.0,
                        "total_trades": 3,
                        "runtime_count": 1,
                        "avg_slippage_bps": 0.0,
                        "latest_telemetry_at": "2026-11-05T17:40:00Z",
                        "telemetry_resolution": "fresh",
                        "telemetry_evidence_refs": ["telemetry-ev-004"],
                    },
                }
            )
        elif idx in (5, 6):
            # Both persona 5 and persona 6 map to strat-scale-0005 -> ambiguous
            items.append(
                {
                    "persona_id": persona_id,
                    "strategy_ids": ["strat-scale-0005"],
                    "capital_pool_id": "pool-persona-paper-fd1281847a3b1c6a9735",
                    "stage": "paper_running",
                    "metrics": {
                        "pnl": 42.0,
                        "drawdown": 0.01,
                        "fill_rate": 1.0,
                        "total_trades": 4,
                        "runtime_count": 1,
                        "avg_slippage_bps": 0.0,
                        "latest_telemetry_at": "2026-11-05T17:40:00Z",
                        "telemetry_resolution": "fresh",
                        "telemetry_evidence_refs": [f"telemetry-ev-{idx:03d}"],
                    },
                }
            )
        else:
            # Not running / missing telemetry
            items.append(
                {
                    "persona_id": persona_id,
                    "strategy_ids": [strategy_id],
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
                }
            )
    return items


def _build_140_historical_snapshots() -> List[Dict[str, Any]]:
    """Build 140 ranking_snapshot payloads matching the hosted Dev table count."""
    older_template = RankingSnapshotRecord(
        ranking_snapshot_id="snap-hist-001",
        surface="quarterly",
        period="2026-Q4",
        formula_version="pm12-v1",
        content_digest="sha256:snap-hist-001",
        items=_build_544_persona_ranking_items(latest=False),
        evidence_assertion_digests={"persona-scale-0001": ["digest-0001"]},
        created_at="2026-11-01T00:01:00Z",
    ).to_canonical_dict()

    latest_payload = RankingSnapshotRecord(
        ranking_snapshot_id="snap-hist-140",
        surface="quarterly",
        period="2026-Q4",
        formula_version="pm12-v1",
        content_digest="sha256:snap-hist-140",
        items=_build_544_persona_ranking_items(latest=True),
        evidence_assertion_digests={"persona-scale-0001": ["digest-0001-latest"]},
        created_at="2026-11-05T17:40:00Z",
    ).to_canonical_dict()

    snapshots: List[Dict[str, Any]] = []
    for idx in range(1, 140):
        day = 1 + (idx // 60)
        minute = idx % 60
        snapshots.append(
            {
                **older_template,
                "ranking_snapshot_id": f"snap-hist-{idx:03d}",
                "created_at": f"2026-11-{day:02d}T00:{minute:02d}:00Z",
            }
        )
    snapshots.append(latest_payload)
    return snapshots


def _build_600_strategy_specs() -> Dict[str, Dict[str, Any]]:
    """Build 600 non-retired strategies (+ 20 retired) across states and personas."""
    specs: Dict[str, Dict[str, Any]] = {}
    for idx in range(1, 621):
        sid = f"strat-scale-{idx:04d}"
        if idx <= 400:
            lifecycle_state = "approved"
        elif idx <= 520:
            lifecycle_state = "draft"
        elif idx <= 600:
            lifecycle_state = "candidate"
        else:
            lifecycle_state = "retired"
        persona_ids = [f"persona-scale-{idx:04d}"] if idx <= 544 else []
        pool_id = "pool-persona-paper-fd1281847a3b1c6a9735" if idx <= 544 else ""
        meta = {"capital_pool_id": pool_id} if pool_id else {}
        ts = "2026-11-05T13:00:00Z" if idx <= 10 else "2026-11-05T12:00:00Z"
        specs[sid] = {
            "strategy_id": sid,
            "title": f"Scale Strategy {idx:04d}",
            "name": f"Scale Strategy {idx:04d}",
            "lifecycle_state": lifecycle_state,
            "risk": "medium",
            "persona_ids": list(persona_ids),
            "metadata": dict(meta),
            "current_spec_version_id": f"reg-{sid}-v1",
            "current_spec_version": "1.0.0",
            "versions": [
                {
                    "spec_version_id": f"reg-{sid}-v1",
                    "spec_version": "1.0.0",
                    "title": f"Scale Strategy {idx:04d}",
                    "lifecycle_state": lifecycle_state,
                    "risk": "medium",
                    "persona_ids": list(persona_ids),
                    "metadata": dict(meta),
                    "created_at": ts,
                    "updated_at": ts,
                }
            ],
        }
    return specs


class _CountingRecordsTable:
    def __init__(self, payloads: List[Dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.list_all_calls = 0

    def list_all(self) -> List[Dict[str, Any]]:
        self.list_all_calls += 1
        return list(self._payloads)


class _CountingRankingStore:
    def __init__(self, payloads: List[Dict[str, Any]]) -> None:
        self._records_table = _CountingRecordsTable(payloads)


class _CountingResearchKnowledgeSourcePort(DefaultResearchKnowledgeSourcePort):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.detail_calls: List[str] = []

    def get_strategy_spec_detail(
        self,
        strategy_id: str,
        version_selector: Optional[str] = "current",
    ) -> Optional[Dict[str, Any]]:
        self.detail_calls.append(strategy_id)
        return super().get_strategy_spec_detail(
            strategy_id,
            version_selector=version_selector,
        )


def test_strategy_list_production_scale_555_plus_inventory_pagination_filters_and_metrics_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = _build_600_strategy_specs()
    snapshots = _build_140_historical_snapshots()
    ranking_store = _CountingRankingStore(snapshots)

    rks_port = _CountingResearchKnowledgeSourcePort(strategy_specs_store=specs)
    rks_port._has_injected_strategy_specs = True
    read_ports = create_read_surface_ports(
        research_knowledge_source=rks_port,
        ranking_store=ranking_store,
    )

    decode_calls: List[str] = []
    real_decode = rankings_store_mod._decode_ranking_snapshot

    def _counting_decode(payload: Dict[str, Any]):
        decode_calls.append(str(payload.get("ranking_snapshot_id") or ""))
        return real_decode(payload)

    monkeypatch.setattr(rankings_store_mod, "_decode_ranking_snapshot", _counting_decode)

    app = FastAPI()
    app.include_router(
        create_strategies_router(
            read_surface=read_ports,
            list_strategy_summaries=read_ports.list_strategy_specs,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-11-05T17:45:00Z",
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
            normalize_lifecycle_state=_normalize_lifecycle_state,
            normalize_risk_level=_normalize_risk_level,
        )
    )
    app.add_middleware(OwnerReadContextMiddleware)
    client = TestClient(app)

    # 1. First page at hosted probe page_size=100
    resp_p1 = client.get("/bff/strategies?page_size=100", headers=HEADERS)
    assert resp_p1.status_code == 200, resp_p1.text
    body_p1 = resp_p1.json()
    assert body_p1["page_info"]["total"] == 600
    assert body_p1["meta"]["total"] == 600
    assert body_p1["page_info"]["next_page_token"] == "100"
    assert len(body_p1["data"]) == 100

    # Zero per-strategy detail fanout for canonical summaries and only 1 snapshot decoded (not 140)
    assert rks_port.detail_calls == []
    assert ranking_store._records_table.list_all_calls == 1
    assert decode_calls == ["snap-hist-140"]

    by_id_p1 = {item["id"]: item for item in body_p1["data"]}
    # Running positive PnL from latest snapshot (110.0, not older 15.0)
    s1 = by_id_p1["strat-scale-0001"]
    assert s1["pnl"] == 110.0
    assert s1["drawdown"] == 0.0
    assert s1["totalTrades"] == 3
    assert s1["metricsAsOf"] == "2026-11-05T17:40:00Z"
    assert s1["metricsResolution"] == "fresh"
    assert s1["metricsPersonaId"] == "persona-scale-0001"
    assert s1["pnl30d"] is None
    assert s1["sharpe"] is None

    # Running negative PnL
    s2 = by_id_p1["strat-scale-0002"]
    assert s2["pnl"] == -5.5
    assert s2["drawdown"] == 0.000055
    assert s2["totalTrades"] == 2
    assert s2["metricsResolution"] == "fresh"

    # Real zero PnL and drawdown preserved as 0.0
    s3 = by_id_p1["strat-scale-0003"]
    assert s3["pnl"] == 0.0
    assert s3["drawdown"] == 0.0
    assert s3["totalTrades"] == 1
    assert s3["metricsResolution"] == "fresh"

    # Chain persona with stage == "not_running" and fresh telemetry
    s4 = by_id_p1["strat-scale-0004"]
    assert s4["pnl"] == 110.0
    assert s4["drawdown"] == 0.0
    assert s4["totalTrades"] == 3
    assert s4["metricsResolution"] == "fresh"

    # Ambiguous multi-persona mapping -> null metrics with "ambiguous"
    s5 = by_id_p1["strat-scale-0005"]
    assert s5["pnl"] is None
    assert s5["drawdown"] is None
    assert s5["totalTrades"] is None
    assert s5["metricsResolution"] == "ambiguous"

    # Unlinked strategy (strat-scale-0006 had its persona reassigned to strat-scale-0005)
    s6 = by_id_p1["strat-scale-0006"]
    assert s6["pnl"] is None
    assert s6["drawdown"] is None
    assert s6["totalTrades"] is None
    assert s6["metricsResolution"] == "missing"

    # Not-running strategy with missing telemetry -> null metrics
    s7 = by_id_p1["strat-scale-0007"]
    assert s7["pnl"] is None
    assert s7["drawdown"] is None
    assert s7["totalTrades"] is None
    assert s7["metricsResolution"] == "missing"

    # 2. Traverse all remaining pages and confirm all 600 strategies are returned without loss
    collected_ids = [item["id"] for item in body_p1["data"]]
    token = body_p1["page_info"]["next_page_token"]
    pages = 1
    while token is not None:
        page_resp = client.get(
            f"/bff/strategies?page_size=100&page_token={token}",
            headers=HEADERS,
        )
        assert page_resp.status_code == 200, page_resp.text
        page_body = page_resp.json()
        assert page_body["page_info"]["total"] == 600
        collected_ids.extend(item["id"] for item in page_body["data"])
        token = page_body["page_info"]["next_page_token"]
        pages += 1

    assert pages == 6
    assert len(collected_ids) == 600
    assert len(set(collected_ids)) == 600
    assert rks_port.detail_calls == []
    assert len(decode_calls) == 6  # Exactly 1 decode per request across 6 requests

    # 3. Exact state filtering across the >500 population
    approved_resp = client.get("/bff/strategies?state=approved&page_size=100", headers=HEADERS)
    assert approved_resp.status_code == 200
    approved_body = approved_resp.json()
    assert approved_body["page_info"]["total"] == 400
    assert len(approved_body["data"]) == 100
    assert all(item["state"] == "approved" for item in approved_body["data"])

    draft_resp = client.get("/bff/strategies?state=draft&page_size=100", headers=HEADERS)
    assert draft_resp.status_code == 200
    draft_body = draft_resp.json()
    assert draft_body["page_info"]["total"] == 120
    assert len(draft_body["data"]) == 100
    assert draft_body["page_info"]["next_page_token"] == "100"
    assert all(item["state"] == "draft" for item in draft_body["data"])

    review_resp = client.get("/bff/strategies?state=review&page_size=100", headers=HEADERS)
    assert review_resp.status_code == 200
    review_body = review_resp.json()
    assert review_body["page_info"]["total"] == 80
    assert len(review_body["data"]) == 80
    assert review_body["page_info"]["next_page_token"] is None
    assert all(item["state"] == "review" for item in review_body["data"])

    # 4. Exact persona_id filtering
    persona_resp = client.get(
        "/bff/strategies?persona_id=persona-scale-0001&page_size=100",
        headers=HEADERS,
    )
    assert persona_resp.status_code == 200
    persona_body = persona_resp.json()
    assert persona_body["page_info"]["total"] == 1
    assert [item["id"] for item in persona_body["data"]] == ["strat-scale-0001"]


def test_strategy_list_thin_summaries_bounds_detail_enrichment_to_requested_page() -> None:
    specs = _build_600_strategy_specs()
    rks_port = _CountingResearchKnowledgeSourcePort(strategy_specs_store=specs)
    rks_port._has_injected_strategy_specs = True
    ranking_store = _CountingRankingStore([_build_140_historical_snapshots()[-1]])
    read_ports = create_read_surface_ports(
        research_knowledge_source=rks_port,
        ranking_store=ranking_store,
    )

    # Return 600 thin summaries WITHOUT current_spec_version_id so detail enrichment is required
    def _thin_summaries() -> List[Dict[str, Any]]:
        return [
            {
                "strategy_id": f"strat-scale-{idx:04d}",
                "title": f"Scale Strategy {idx:04d}",
                "lifecycle_state": "approved" if idx <= 400 else ("draft" if idx <= 520 else "review"),
                "persona_ids": [f"persona-scale-{idx:04d}"] if idx <= 544 else [],
                "risk": "medium",
            }
            for idx in range(1, 601)
        ]

    app = FastAPI()
    app.include_router(
        create_strategies_router(
            read_surface=read_ports,
            list_strategy_summaries=_thin_summaries,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-11-05T17:45:00Z",
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
            normalize_lifecycle_state=_normalize_lifecycle_state,
            normalize_risk_level=_normalize_risk_level,
        )
    )
    app.add_middleware(OwnerReadContextMiddleware)
    client = TestClient(app)

    resp = client.get("/bff/strategies?page_size=25&page_token=50", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_info"]["total"] == 600
    assert body["page_info"]["next_page_token"] == "75"
    assert len(body["data"]) == 25
    expected_page_ids = [f"strat-scale-{idx:04d}" for idx in range(51, 76)]
    assert [item["id"] for item in body["data"]] == expected_page_ids
    # Detail enrichment is bounded strictly to the 25 items on the requested page, not all 600
    assert rks_port.detail_calls == expected_page_ids

    rks_port.detail_calls.clear()
    filtered_resp = client.get("/bff/strategies?state=draft&page_size=15", headers=HEADERS)
    assert filtered_resp.status_code == 200, filtered_resp.text
    filtered_body = filtered_resp.json()
    assert filtered_body["page_info"]["total"] == 120
    assert len(filtered_body["data"]) == 15
    assert len(rks_port.detail_calls) == 15
    assert rks_port.detail_calls == [f"strat-scale-{idx:04d}" for idx in range(401, 416)]


class _FakePostgresCursor:
    def __init__(self, latest_payload: Dict[str, Any], queries: List[Tuple[str, Any]]) -> None:
        self._latest_payload = latest_payload
        self._queries = queries

    def __enter__(self) -> "_FakePostgresCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self._queries.append((" ".join(sql.split()), params))

    def fetchone(self) -> Tuple[Dict[str, Any]]:
        return (self._latest_payload,)


class _FakePostgresConnection:
    def __init__(self, latest_payload: Dict[str, Any], queries: List[Tuple[str, Any]]) -> None:
        self._latest_payload = latest_payload
        self._queries = queries

    def cursor(self) -> _FakePostgresCursor:
        return _FakePostgresCursor(self._latest_payload, self._queries)


class _FakePostgresRecordsTable:
    table = '"rankings"."rankings"'

    def __init__(self, latest_payload: Dict[str, Any]) -> None:
        self._latest_payload = latest_payload
        self.queries: List[Tuple[str, Any]] = []
        self.ensure_schema_calls = 0

    def ensure_schema(self) -> None:
        self.ensure_schema_calls += 1

    @contextmanager
    def _use_conn(self):
        yield _FakePostgresConnection(self._latest_payload, self.queries)

    def list_all(self) -> List[Dict[str, Any]]:
        raise AssertionError("Unbounded list_all() must not be called on Postgres ranking store")


class _FakePostgresRankingStore:
    def __init__(self, latest_payload: Dict[str, Any]) -> None:
        self._records_table = _FakePostgresRecordsTable(latest_payload)


def test_postgres_ranking_store_reads_single_latest_row_with_limit_1() -> None:
    specs = _build_600_strategy_specs()
    latest_snapshot = _build_140_historical_snapshots()[-1]
    pg_ranking_store = _FakePostgresRankingStore(latest_snapshot)

    rks_port = _CountingResearchKnowledgeSourcePort(strategy_specs_store=specs)
    rks_port._has_injected_strategy_specs = True
    read_ports = create_read_surface_ports(
        research_knowledge_source=rks_port,
        ranking_store=pg_ranking_store,
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
            utc_now=lambda: "2026-11-05T17:45:00Z",
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
            normalize_lifecycle_state=_normalize_lifecycle_state,
            normalize_risk_level=_normalize_risk_level,
        )
    )
    app.add_middleware(OwnerReadContextMiddleware)
    client = TestClient(app)

    resp = client.get("/bff/strategies?page_size=100", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_info"]["total"] == 600
    assert len(pg_ranking_store._records_table.queries) == 1
    executed_sql, executed_params = pg_ranking_store._records_table.queries[0]
    assert 'SELECT payload FROM "rankings"."rankings"' in executed_sql
    assert "ORDER BY COALESCE(payload ->> 'created_at', '') DESC, updated_at DESC LIMIT 1" in executed_sql
    assert executed_params == ("ranking_snapshot", "quarterly")

    by_id = {item["id"]: item for item in body["data"]}
    assert by_id["strat-scale-0001"]["pnl"] == 110.0
    assert by_id["strat-scale-0003"]["pnl"] == 0.0
    assert by_id["strat-scale-0003"]["drawdown"] == 0.0


def test_strategy_list_never_silently_drops_malformed_summary_even_off_page() -> None:
    specs = _build_600_strategy_specs()
    rks_port = _CountingResearchKnowledgeSourcePort(strategy_specs_store=specs)
    rks_port._has_injected_strategy_specs = True
    read_ports = ReadSurfacePorts(research_knowledge_source=rks_port)

    def _summaries_with_off_page_corrupt_entry() -> List[Dict[str, Any]]:
        rows = read_ports.list_strategy_specs()
        rows[520] = {"title": "Corrupt Off-Page Entry Without ID"}
        return rows

    app = FastAPI()
    app.include_router(
        create_strategies_router(
            read_surface=read_ports,
            list_strategy_summaries=_summaries_with_off_page_corrupt_entry,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-11-05T17:45:00Z",
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
            normalize_lifecycle_state=_normalize_lifecycle_state,
            normalize_risk_level=_normalize_risk_level,
        )
    )
    app.add_middleware(OwnerReadContextMiddleware)
    client = TestClient(app)

    resp = client.get("/bff/strategies?page_size=20", headers=HEADERS)
    assert resp.status_code == 503, resp.text
    assert strategies_service_mod.list_strategy_summaries(read_ports)
