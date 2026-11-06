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
import json
import os
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.ports import rankings as rankings_port_mod
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
from services.foundation.postgres_json_store import PostgresJsonOwnerStore
import services.rankings.store as rankings_store_mod
from services.rankings.store import RankingSnapshotRecord, RankingWriteStore
from services.rankings.test_store import _FakeConnection

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
    monkeypatch.setattr(rankings_port_mod, "_decode_ranking_snapshot", _counting_decode)

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

    class _LegacyWholeListDetailReadSurface:
        def get_strategy_spec_detail(self, strategy_id: str, version_selector: str = "current") -> Optional[Dict[str, Any]]:
            return read_ports.get_strategy_spec_detail(strategy_id, version_selector=version_selector)

        def get_latest_ranking_snapshot(self) -> Optional[Dict[str, Any]]:
            return read_ports.get_latest_ranking_snapshot()

    app = FastAPI()
    app.include_router(
        create_strategies_router(
            read_surface=_LegacyWholeListDetailReadSurface(),
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


def test_postgres_ranking_store_reads_single_latest_row_with_limit_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = _build_600_strategy_specs()
    snapshots = _build_140_historical_snapshots()
    _FakeConnection.rows.clear()
    _FakeConnection.statements.clear()
    conn = _FakeConnection()
    bucket = _FakeConnection.rows.setdefault('"rankings"."rankings"', {})
    for payload in snapshots:
        snap_id = str(payload["ranking_snapshot_id"])
        bucket[f"ranking_snapshot:{snap_id}"] = dict(payload)
    # Seed a newer monthly snapshot to prove default surface="quarterly" filtering at SQL level
    newer_monthly = dict(snapshots[-1])
    newer_monthly["ranking_snapshot_id"] = "snap-monthly-999"
    newer_monthly["surface"] = "monthly"
    newer_monthly["created_at"] = "2026-11-05T18:00:00Z"
    bucket["ranking_snapshot:snap-monthly-999"] = newer_monthly

    executed_queries: List[Tuple[str, Any]] = []
    real_execute = conn.execute

    def _recording_execute(query: str, params: Tuple[Any, ...] = ()):
        executed_queries.append((" ".join(query.split()), params))
        return real_execute(query, params)

    monkeypatch.setattr(conn, "execute", _recording_execute)

    @contextmanager
    def _fake_connect(self: PostgresJsonOwnerStore):
        yield conn

    monkeypatch.setattr(PostgresJsonOwnerStore, "_connect", _fake_connect)
    monkeypatch.setenv("RANKING_STORE_DSN", "postgresql://test/rankings")
    monkeypatch.setenv("RANKING_STORE_TABLE", "rankings.rankings")

    decode_calls: List[str] = []
    real_decode = rankings_store_mod._decode_ranking_snapshot

    def _counting_decode(payload: Dict[str, Any]):
        decode_calls.append(str(payload.get("ranking_snapshot_id") or ""))
        return real_decode(payload)

    monkeypatch.setattr(rankings_store_mod, "_decode_ranking_snapshot", _counting_decode)
    monkeypatch.setattr(rankings_port_mod, "_decode_ranking_snapshot", _counting_decode)

    rks_port = _CountingResearchKnowledgeSourcePort(strategy_specs_store=specs)
    rks_port._has_injected_strategy_specs = True
    read_ports = create_read_surface_ports(
        research_knowledge_source=rks_port,
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
    assert len(executed_queries) == 1
    executed_sql, executed_params = executed_queries[0]
    assert "CREATE SCHEMA" not in executed_sql
    assert "CREATE TABLE" not in executed_sql
    assert 'SELECT payload FROM "rankings"."rankings"' in executed_sql
    assert "ORDER BY COALESCE(payload ->> 'created_at', '') DESC, updated_at DESC LIMIT 1" in executed_sql
    assert executed_params == ("ranking_snapshot", "quarterly")
    assert decode_calls == ["snap-hist-140"]

    by_id = {item["id"]: item for item in body["data"]}
    assert by_id["strat-scale-0001"]["pnl"] == 110.0
    assert by_id["strat-scale-0003"]["pnl"] == 0.0
    assert by_id["strat-scale-0003"]["drawdown"] == 0.0
    assert by_id["strat-scale-0005"]["pnl"] is None
    assert by_id["strat-scale-0005"]["metricsResolution"] == "ambiguous"


def test_live_postgres_ranking_read_store_strategy_list_when_dsn_set() -> None:
    dsn = os.getenv("RANKING_TEST_DSN")
    if not dsn:
        pytest.skip("RANKING_TEST_DSN is not set; skipping live PostgreSQL strategy-list latency test.")
    psycopg = pytest.importorskip("psycopg")
    schema = f"rankings_latency_{uuid.uuid4().hex[:12]}"
    table = f"{schema}.rankings"
    try:
        write_store = RankingWriteStore(dsn=dsn, table=table, bootstrap=True)
        older = RankingSnapshotRecord(
            ranking_snapshot_id="snap-pg-001",
            surface="quarterly",
            period="2026-Q4",
            formula_version="pm12-v1",
            content_digest="sha256:snap-pg-001",
            items=_build_544_persona_ranking_items(latest=False),
            evidence_assertion_digests={"persona-scale-0001": ["digest-0001"]},
            created_at="2026-11-01T00:01:00Z",
        )
        latest = RankingSnapshotRecord(
            ranking_snapshot_id="snap-pg-002",
            surface="quarterly",
            period="2026-Q4",
            formula_version="pm12-v1",
            content_digest="sha256:snap-pg-002",
            items=_build_544_persona_ranking_items(latest=True),
            evidence_assertion_digests={"persona-scale-0001": ["digest-0002"]},
            created_at="2026-11-05T17:40:00Z",
        )
        write_store.create_ranking_snapshot(older)
        write_store.create_ranking_snapshot(latest)
        read_store = rankings_store_mod.RankingReadStore(dsn=dsn, table=table, bootstrap=False)
        resolved = read_store.get_latest_ranking_snapshot(surface="quarterly")
        assert resolved == latest
    finally:
        with psycopg.connect(dsn, autocommit=True) as cleanup_conn:
            cleanup_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_strategy_list_never_silently_drops_malformed_summary_even_off_page() -> None:
    specs = _build_600_strategy_specs()
    rks_port = _CountingResearchKnowledgeSourcePort(strategy_specs_store=specs)
    rks_port._has_injected_strategy_specs = True
    read_ports = ReadSurfacePorts(research_knowledge_source=rks_port)

    def _summaries_with_off_page_corrupt_entry() -> List[Dict[str, Any]]:
        rows = read_ports.list_strategy_specs()
        rows[520] = {"title": "Corrupt Off-Page Entry Without ID"}
        return rows

    class _LegacyWholeListReadSurface:
        def list_strategy_specs(self) -> List[Dict[str, Any]]:
            return read_ports.list_strategy_specs()

    app = FastAPI()
    app.include_router(
        create_strategies_router(
            read_surface=_LegacyWholeListReadSurface(),
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


def _build_real_5031_registry_entries() -> List[Any]:
    """Build the exact 5031 strategy_spec rows (~2.4 KB each -> ~12 MB total) across 4900 families:
    - 3209 strat-tw-* draft families
    - 1554 strat-dev-* draft families
    - 132 strategy-persona-* approved families (131 of which also have an older 1.0.0 draft revision -> +131 rows)
    - 1 strat-approved-extra-0133 approved family (with market_scope.alpha = 'cross-sectional-momentum')
    - 1 strat-candidate-0001 candidate family
    - 1 strat-other-draft-0001 draft family
    - 2 strat-retired-* retired families
    Total families = 4764 draft + 133 approved + 1 candidate + 2 retired = 4900 (4898 non-retired).
    Total rows = 4900 + 131 = 5031.
    """
    from services.registry.models import ArtifactState, ArtifactType, Lineage, RegistryEntry, StorageBackend, StorageRef

    entries: List[RegistryEntry] = []
    heavy_body = "x" * 1600

    def _make_entry(
        rid: str,
        sid: str,
        version: str,
        state: ArtifactState,
        *,
        persona_ids: List[str],
        updated_at: str,
        alpha: str = "",
        pool_id: str = "",
    ) -> RegistryEntry:
        market_scope: Dict[str, Any] = {"markets": ["TWSE"], "universe": ["2330.TW", "2317.TW"]}
        if alpha:
            market_scope["alpha"] = alpha
        inner_spec: Dict[str, Any] = {
            "strategy_id": sid,
            "spec_version": version,
            "title": f"Real Scale {sid} {version}",
            "lifecycle_state": state.value,
            "source_kind": "workflow",
            "hypothesis": f"Hypothesis for {sid}: {heavy_body}",
            "objective": {"summary": "Maximize risk-adjusted return", "notes": heavy_body[:400]},
            "market_scope": market_scope,
            "execution_profile": {"mode": "paper", **({"capital_pool_id": pool_id} if pool_id else {})},
            "governance": {"risk_level": "medium", "owner": "operator-tw"},
            "persona_ids": list(persona_ids),
            "provenance": {"source_kind": "workflow", "created_at": updated_at, "created_by": "op-latency"},
        }
        meta: Dict[str, Any] = {
            "tenant_id": "tenant-dev",
            "title": inner_spec["title"],
            "source_kind": "workflow",
            "persona_ids": list(persona_ids),
            "strategy_spec": inner_spec,
        }
        if pool_id:
            meta["capital_pool_id"] = pool_id
        return RegistryEntry(
            registry_id=rid,
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=sid,
            version=version,
            artifact_state=state,
            lineage=Lineage(source_run_ids=["run-scale"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum=f"sha256:{rid}",
            metadata=meta,
            created_at=updated_at,
            updated_at=updated_at,
            owner_tenant="tenant-dev",
        )

    # 132 strategy-persona-* approved families (+ 131 prior v1.0.0 draft rows)
    for idx in range(1, 133):
        sid = f"strategy-persona-{idx:04d}"
        pid = f"persona-scale-{idx:04d}"
        pool_id = "pool-persona-paper-fd1281847a3b1c6a9735"
        if idx <= 131:
            entries.append(
                _make_entry(
                    f"reg-{sid}-v1",
                    sid,
                    "1.0.0",
                    ArtifactState.DRAFT,
                    persona_ids=[pid],
                    updated_at="2026-11-05T11:00:00Z",
                    pool_id=pool_id,
                )
            )
        entries.append(
            _make_entry(
                f"reg-{sid}-v2",
                sid,
                "1.1.0",
                ArtifactState.APPROVED,
                persona_ids=[pid],
                updated_at="2026-11-05T15:30:00Z" if idx == 1 else "2026-11-05T15:00:00Z",
                pool_id=pool_id,
            )
        )

    # 1 extra approved family with explicit market_scope.alpha (total approved = 133)
    entries.append(
        _make_entry(
            "reg-strat-approved-extra-0133-v1",
            "strat-approved-extra-0133",
            "1.0.0",
            ArtifactState.APPROVED,
            persona_ids=["persona-scale-0133"],
            updated_at="2026-11-05T16:00:00Z",
            alpha="cross-sectional-momentum",
            pool_id="pool-persona-paper-fd1281847a3b1c6a9735",
        )
    )

    # 1 candidate family (state=review)
    entries.append(
        _make_entry(
            "reg-strat-candidate-0001-v1",
            "strat-candidate-0001",
            "1.0.0",
            ArtifactState.CANDIDATE,
            persona_ids=["persona-scale-0134"],
            updated_at="2026-11-05T14:30:00Z",
        )
    )

    # 3209 strat-tw-* draft families
    for idx in range(1, 3210):
        sid = f"strat-tw-{idx:04d}"
        entries.append(
            _make_entry(
                f"reg-{sid}-v1",
                sid,
                "1.0.0",
                ArtifactState.DRAFT,
                persona_ids=[],
                updated_at="2026-11-05T12:00:00Z",
            )
        )

    # 1554 strat-dev-* draft families
    for idx in range(1, 1555):
        sid = f"strat-dev-{idx:04d}"
        entries.append(
            _make_entry(
                f"reg-{sid}-v1",
                sid,
                "1.0.0",
                ArtifactState.DRAFT,
                persona_ids=[],
                updated_at="2026-11-05T11:30:00Z",
            )
        )

    # 1 additional draft family -> 3209 + 1554 + 1 = 4764 draft families
    entries.append(
        _make_entry(
            "reg-strat-other-draft-0001-v1",
            "strat-other-draft-0001",
            "1.0.0",
            ArtifactState.DRAFT,
            persona_ids=[],
            updated_at="2026-11-05T11:15:00Z",
        )
    )

    # 2 retired families -> 4900 total families (4898 non-retired), 5031 total rows
    for idx in (1, 2):
        sid = f"strat-retired-{idx:04d}"
        entries.append(
            _make_entry(
                f"reg-{sid}-v1",
                sid,
                "1.0.0",
                ArtifactState.RETIRED,
                persona_ids=[],
                updated_at="2026-11-05T10:00:00Z",
            )
        )

    assert len(entries) == 5031
    return entries


def test_real_5031_row_4898_strategy_scale_list_owner_boundary_bytes_rows_filters_and_alpha_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.parse
    import services.control_plane.bff.command_adapters.base as adapter_base
    from services.registry.split_api import RegistryService
    from services.registry.storage import RegistryStore

    entries = _build_real_5031_registry_entries()
    reg_store = RegistryStore()
    for entry in entries:
        reg_store.put(entry)
    reg_service = RegistryService(reg_store)

    owner_http_calls: List[Tuple[str, int, int]] = []

    def _owner_http_stub(url: str, **kwargs: Any) -> Any:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        tenant_id = str(kwargs.get("tenant_id") or "tenant-dev")
        visible = frozenset({tenant_id, "__builtin__"})
        if parsed.path == "/api/registry/strategy-specs" and qs.get("view") == ["strategy_summary_page"]:
            payload = reg_service.strategy_spec_summary_page(
                visible_tenants=visible,
                artifact_type="strategy_spec",
                state=qs["state"][0] if "state" in qs else None,
                exclude_state=qs["exclude_state"][0] if "exclude_state" in qs else None,
                persona_id=qs["persona_id"][0] if "persona_id" in qs else None,
                page_token=qs["page_token"][0] if "page_token" in qs else None,
                page_size=int(qs["page_size"][0]) if "page_size" in qs else 20,
            )
            raw_bytes = len(json.dumps(payload).encode("utf-8"))
            owner_http_calls.append((f"{parsed.path}?{parsed.query}", len(payload["items"]), raw_bytes))
            return payload
        if parsed.path.startswith("/api/registry/strategies/") and parsed.path.endswith("/strategy-specs"):
            sid = urllib.parse.unquote(parsed.path.split("/")[4])
            views = [
                {"entry": v.entry.to_dict(), "deployment_stage": v.deployment_stage.value}
                for v in reg_service.list_by_strategy(sid)
                if v.entry.owner_tenant in visible
            ]
            raw_bytes = len(json.dumps(views).encode("utf-8"))
            owner_http_calls.append((parsed.path, len(views), raw_bytes))
            return views
        raise AssertionError(f"Unexpected owner HTTP call: {url}")

    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.local")
    monkeypatch.setattr(adapter_base, "http_request_json", _owner_http_stub)

    rks_port = _CountingResearchKnowledgeSourcePort()
    ranking_store = _CountingRankingStore([_build_140_historical_snapshots()[-1]])
    read_ports = create_read_surface_ports(
        research_knowledge_source=rks_port,
        ranking_store=ranking_store,
    )

    app = FastAPI()
    app.include_router(
        create_strategies_router(
            read_surface=read_ports,
            list_strategy_summaries=lambda: strategies_service_mod.list_strategy_summaries(read_ports),
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

    # 1. Default page (page_size=20) over 5031 rows / 4898 non-retired strategies
    resp = client.get("/bff/strategies?page_size=20", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_info"]["total"] == 4898
    assert body["meta"]["total"] == 4898
    assert body["page_info"]["next_page_token"] == "20"
    assert len(body["data"]) == 20
    assert rks_port.detail_calls == []

    # Verify owner boundary: 1 call to view=strategy_summary_page returning 20 rows (< 30 KB, not 5031 rows / 12 MB)
    assert len(owner_http_calls) == 1
    call_path, call_rows, call_bytes = owner_http_calls[0]
    assert call_path == "/api/registry/strategy-specs?view=strategy_summary_page&page_size=20"
    assert call_rows == 20
    assert call_bytes < 30_000

    # Verify alpha parity on list:
    # - strat-approved-extra-0133 has market_scope.alpha == 'cross-sectional-momentum'
    # - strategy-persona-0001 has source_kind == 'workflow' and hypothesis, but no market_scope.alpha -> alpha == ""
    by_id = {item["id"]: item for item in body["data"]}
    assert by_id["strat-approved-extra-0133"]["alpha"] == "cross-sectional-momentum"
    assert by_id["strategy-persona-0001"]["alpha"] == ""
    assert all("spec" not in item for item in body["data"])

    # 2. Owner-filtered state=approved (exact total 133)
    owner_http_calls.clear()
    resp_approved = client.get("/bff/strategies?state=approved&page_size=20", headers=HEADERS)
    assert resp_approved.status_code == 200
    body_approved = resp_approved.json()
    assert body_approved["page_info"]["total"] == 133
    assert len(body_approved["data"]) == 20
    assert all(item["state"] == "approved" for item in body_approved["data"])
    assert owner_http_calls[0][0] == "/api/registry/strategy-specs?view=strategy_summary_page&page_size=20&state=approved"
    assert owner_http_calls[0][1] == 20

    # 3. Owner-filtered state=draft (exact total 4764)
    owner_http_calls.clear()
    resp_draft = client.get("/bff/strategies?state=draft&page_size=20", headers=HEADERS)
    assert resp_draft.status_code == 200
    body_draft = resp_draft.json()
    assert body_draft["page_info"]["total"] == 4764
    assert len(body_draft["data"]) == 20
    assert all(item["state"] == "draft" for item in body_draft["data"])
    assert owner_http_calls[0][0] == "/api/registry/strategy-specs?view=strategy_summary_page&page_size=20&state=draft"
    assert owner_http_calls[0][1] == 20

    # 4. Owner-filtered state=review (exact total 1)
    owner_http_calls.clear()
    resp_review = client.get("/bff/strategies?state=review&page_size=20", headers=HEADERS)
    assert resp_review.status_code == 200
    body_review = resp_review.json()
    assert body_review["page_info"]["total"] == 1
    assert [item["id"] for item in body_review["data"]] == ["strat-candidate-0001"]

    # 5. Owner-filtered persona_id=persona-scale-0001 (exact total 1)
    owner_http_calls.clear()
    resp_persona = client.get("/bff/strategies?persona_id=persona-scale-0001&page_size=20", headers=HEADERS)
    assert resp_persona.status_code == 200
    body_persona = resp_persona.json()
    assert body_persona["page_info"]["total"] == 1
    assert [item["id"] for item in body_persona["data"]] == ["strategy-persona-0001"]
    assert owner_http_calls[0][0] == (
        "/api/registry/strategy-specs?view=strategy_summary_page&page_size=20&persona_id=persona-scale-0001"
    )
    assert owner_http_calls[0][1] == 1

    # 6. Detail endpoint reads only that strategy (2 version rows) in a single owner HTTP request and agrees on alpha
    owner_http_calls.clear()
    resp_detail_1 = client.get("/bff/strategies/strategy-persona-0001", headers=HEADERS)
    assert resp_detail_1.status_code == 200, resp_detail_1.text
    detail_1 = resp_detail_1.json()["data"]
    assert detail_1["id"] == "strategy-persona-0001"
    assert detail_1["alpha"] == ""
    assert detail_1["alpha"] == by_id["strategy-persona-0001"]["alpha"]
    assert detail_1["spec"]["spec_version_id"] == "reg-strategy-persona-0001-v2"
    assert detail_1["spec"]["spec_version"] == "1.1.0"
    assert detail_1["spec"]["market_scope"] == {"markets": ["TWSE"], "universe": ["2330.TW", "2317.TW"]}
    assert len(owner_http_calls) == 1
    assert owner_http_calls[0][0] == "/api/registry/strategies/strategy-persona-0001/strategy-specs"
    assert owner_http_calls[0][1] == 2

    owner_http_calls.clear()
    resp_detail_alpha = client.get("/bff/strategies/strat-approved-extra-0133", headers=HEADERS)
    assert resp_detail_alpha.status_code == 200, resp_detail_alpha.text
    detail_alpha = resp_detail_alpha.json()["data"]
    assert detail_alpha["alpha"] == "cross-sectional-momentum"
    assert detail_alpha["alpha"] == by_id["strat-approved-extra-0133"]["alpha"]
    assert len(owner_http_calls) == 1
    assert owner_http_calls[0][0] == "/api/registry/strategies/strat-approved-extra-0133/strategy-specs"
    assert owner_http_calls[0][1] == 1


def test_strategies_service_equivalent_named_wrapper_and_lambda_both_use_bounded_owner_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equivalent callables (lambda vs named def vs bound method) passed as
    list_strategy_summaries must all route GET /bff/strategies to the bounded
    owner summary page when read_surface exposes list_strategy_summaries_page.
    """
    import urllib.parse
    import services.control_plane.bff.command_adapters.base as adapter_base
    from services.control_plane.bff.strategies.service import list_strategy_summaries

    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.scale.internal")
    owner_http_paths: List[str] = []

    def _http_stub(url: str, **kwargs: Any) -> Any:
        parsed = urllib.parse.urlparse(url)
        path_with_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        owner_http_paths.append(path_with_query)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if parsed.path == "/api/registry/strategy-specs" and query.get("view") == "strategy_summary_page":
            return {
                "items": [
                    {
                        "strategy_id": "strat-equiv-0001",
                        "tenant_id": "tenant-dev",
                        "title": "Equivalent Callable Strategy",
                        "name": "Equivalent Callable Strategy",
                        "lifecycle_state": "approved",
                        "risk": "medium",
                        "persona_ids": ["persona-scale-0001"],
                        "current_spec_version_id": "reg-strat-equiv-0001-v1",
                        "strategy_spec_registry_id": "reg-strat-equiv-0001-v1",
                        "current_spec_version": "1.0.0",
                        "version_count": 1,
                        "last_modified_at": "2026-11-05T17:00:00Z",
                    }
                ],
                "total": 1,
                "next_page_token": None,
            }
        raise AssertionError(f"Full-list endpoint must not be called when owner summary page is available: {path_with_query}")

    monkeypatch.setattr(adapter_base, "http_request_json", _http_stub)

    rks_port = DefaultResearchKnowledgeSourcePort()
    read_ports = create_read_surface_ports(
        research_knowledge_source=rks_port,
        ranking_store=_CountingRankingStore([]),
    )

    def named_wrapper() -> List[Dict[str, Any]]:
        return list_strategy_summaries(read_ports)

    for label, summary_callable in (
        ("lambda", lambda: list_strategy_summaries(read_ports)),
        ("named_wrapper", named_wrapper),
        ("bound_rks_method", rks_port.list_strategy_specs),
        ("none", None),
    ):
        owner_http_paths.clear()
        app = FastAPI()
        app.include_router(
            create_strategies_router(
                read_surface=read_ports,
                list_strategy_summaries=summary_callable,
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
        assert resp.status_code == 200, f"{label} failed: {resp.text}"
        body = resp.json()
        assert body["page_info"]["total"] == 1, label
        assert [item["id"] for item in body["data"]] == ["strat-equiv-0001"], label
        assert owner_http_paths == ["/api/registry/strategy-specs?view=strategy_summary_page&page_size=20"], label


def test_strategy_summary_page_rejects_unexpected_raw_list_owner_shape_fail_closed_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When GET /api/registry/strategy-specs?view=strategy_summary_page returns an
    unexpected raw list instead of its typed page-envelope dict, BFF must fail
    closed with 503 DEPENDENCY_UNAVAILABLE rather than importing Registry storage
    projection or reprocessing a full list in BFF.
    """
    import services.control_plane.bff.command_adapters.base as adapter_base

    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.scale.internal")
    monkeypatch.setattr(
        adapter_base,
        "http_request_json",
        lambda *a, **k: [
            {
                "registry_id": "reg-raw-list-1",
                "artifact_type": "strategy_spec",
                "strategy_id": "strat-raw-list-1",
                "version": "1.0.0",
                "artifact_state": "approved",
                "owner_tenant": "tenant-dev",
                "metadata": {"tenant_id": "tenant-dev", "title": "Raw List Entry"},
            }
        ],
    )

    rks_port = DefaultResearchKnowledgeSourcePort()
    read_ports = create_read_surface_ports(
        research_knowledge_source=rks_port,
        ranking_store=_CountingRankingStore([]),
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

    resp = client.get("/bff/strategies?page_size=20", headers=HEADERS)
    assert resp.status_code == 503, resp.text


