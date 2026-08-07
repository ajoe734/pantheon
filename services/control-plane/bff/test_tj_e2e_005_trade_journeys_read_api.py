"""Contract tests for TJ-E2E-005: canonical Trade Journey BFF read API.

Covers list/detail/timeline/graph/resolve/evidence/replay/metrics under
`/bff/management/trade-journeys`: OpenAPI/DTO envelope shape, authorization
and row-level (tenant) scope, identifier-existence protection, route
resolution (no shadowing between `resolve`/`metrics` and `{journey_id}`),
degraded/unavailable semantics, and a pagination performance smoke test.

Pattern mirrors `tests/test_bff_lineage_contract.py`: inject a seeded
materializer via `trade_journeys.EVENT_STORE` replacement, restore after.
"""
from __future__ import annotations

import json
import os
import sys
import time

BFF_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BFF_DIR)

import main as bff_main  # noqa: E402
import trade_journeys as tj  # noqa: E402
from trade_journey_projection_store import (  # noqa: E402
    InvalidPageToken,
    PageTokenCodec,
    ProjectionPage,
    TradeJourneyProjectionStore,
)
from fastapi.testclient import TestClient  # noqa: E402
from services.trade_journey.materializer import JourneyMaterializer  # noqa: E402


OPERATOR_HEADERS = {"Authorization": "Bearer op-tj-005:operator,reviewer"}


def _event(event_id, journey_id, stage, status, minute, *, tenant="tenant-a", environment="paper", **extra):
    return {
        "event_id": event_id,
        "journey_id": journey_id,
        "tenant_id": tenant,
        "environment": environment,
        "occurred_at": f"2026-07-12T00:{minute:02d}:00Z",
        "source": "test",
        "stage": stage,
        "stage_status": status,
        "persona_id": "persona-1",
        "strategy_id": "strategy-1",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 10,
        "broker_order_id": "bo-1",
        "client_order_id": "co-1",
        **extra,
    }


def _materializer_with(events) -> JourneyMaterializer:
    materializer = JourneyMaterializer()
    materializer.rebuild(events)
    return materializer


class _StubIdentity:
    """Minimal OperatorIdentity-shaped stub for direct-store scope tests."""

    def __init__(self, roles, claims=None):
        self.roles = roles
        self.claims = claims or {}


def _seeded_store(events, *, raw_events=None):
    store = tj.TradeJourneyEventStore()
    materializer = _materializer_with(events)
    store.materializer = lambda: materializer
    store.events = lambda: (raw_events if raw_events is not None else events)
    return store


def _client_with(events, *, raw_events=None):
    store = _seeded_store(events, raw_events=raw_events)
    tj.EVENT_STORE = store
    return TestClient(bff_main.app), store


def _direct_client(events, *, raw_events=None, projection_reader=None):
    """Isolated app wired straight to `create_trade_journeys_router` with a
    test-double identity extractor.

    The production `main.py` stub-auth token format (`operator_id:roles[:mfa[:capabilities]]`)
    has no support for tenant-scoping claims, so row-level tenant scope can't
    be exercised end-to-end through `bff_main.app` with a bearer token alone.
    This builds a minimal FastAPI app around the same router factory with a
    fake `extract_identity` that reads `tenant_ids` from the token
    (`operator_id:roles:tenant1,tenant2`), to test the router's own DI
    contract directly instead of the shared stub-auth limitation.
    """
    from fastapi import FastAPI, HTTPException
    from models import OperatorIdentity

    store = _seeded_store(events, raw_events=raw_events)

    def extract_identity(authorization, mfa_token=None, session_cookie=None):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = authorization[len("Bearer "):]
        parts = token.split(":")
        operator_id = parts[0]
        roles = parts[1].split(",") if len(parts) > 1 and parts[1] else []
        tenant_ids = parts[2].split(",") if len(parts) > 2 and parts[2] else None
        claims = {"tenant_ids": tenant_ids} if tenant_ids else {}
        return OperatorIdentity(operator_id=operator_id, roles=roles, claims=claims)

    def require_read_role(identity):
        if not {"viewer", "operator", "approver", "admin", "reviewer"}.intersection(identity.roles):
            raise HTTPException(status_code=403, detail="Read access requires viewer-level role")

    app = FastAPI()
    app.include_router(tj.create_trade_journeys_router(
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        get_event_store=lambda: store,
        get_projection_reader=lambda: projection_reader,
    ))
    return TestClient(app, raise_server_exceptions=False), store


_BASE_EVENTS = [
    _event("e1", "tj_1", "signal_generation", "succeeded", 1, decision_id="dec-1"),
    _event("e2", "tj_1", "trade_decision", "succeeded", 2),
    _event("e3", "tj_1", "risk_evaluation", "succeeded", 3),
    _event("e4", "tj_1", "order_submission", "succeeded", 4),
    _event("e5", "tj_1", "broker_acknowledgement", "succeeded", 5),
    _event("e6", "tj_1", "fill_management", "succeeded", 6),
    _event("e7", "tj_1", "ledger_booking", "succeeded", 7),
    _event("e8", "tj_1", "reconciliation", "succeeded", 8),
]


def _run(fn):
    original = tj.EVENT_STORE
    try:
        fn()
    finally:
        tj.EVENT_STORE = original


# --------------------------------------------------------------------------- #
# OpenAPI / DTO
# --------------------------------------------------------------------------- #

_REQUIRED_ENDPOINTS = {
    "/bff/management/trade-journeys": "TradeJourneyListEnvelope",
    "/bff/management/trade-journeys/{journey_id}": "TradeJourneyDetailEnvelope",
    "/bff/management/trade-journeys/{journey_id}/timeline": "TradeJourneyListEnvelope",
    "/bff/management/trade-journeys/{journey_id}/graph": "TradeJourneyDetailEnvelope",
    "/bff/management/trade-journeys/resolve": "TradeJourneyDetailEnvelope",
    "/bff/management/trade-journeys/{journey_id}/evidence": "TradeJourneyDetailEnvelope",
    "/bff/management/trade-journeys/{journey_id}/replay": "TradeJourneyDetailEnvelope",
    "/bff/management/trade-journeys/metrics": "TradeJourneyDetailEnvelope",
}


def _response_schema_ref(schema: dict, path: str) -> str:
    response_schema = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    if "$ref" in response_schema:
        return response_schema["$ref"].rsplit("/", 1)[-1]
    if "allOf" in response_schema and response_schema["allOf"]:
        return response_schema["allOf"][0]["$ref"].rsplit("/", 1)[-1]
    raise AssertionError(f"{path} does not publish a component response schema: {response_schema}")


def test_tj_e2e_005_openapi_publishes_typed_envelopes_for_every_endpoint() -> None:
    bff_main.app.openapi_schema = None
    schema = TestClient(bff_main.app).get("/openapi.json").json()

    components = schema["components"]["schemas"]
    for component_name in ("TradeJourneyListEnvelope", "TradeJourneyDetailEnvelope", "TradeJourneyMeta", "TradeJourneyFreshness"):
        assert component_name in components

    for path, expected_component in _REQUIRED_ENDPOINTS.items():
        assert path in schema["paths"], f"missing route {path}"
        assert _response_schema_ref(schema, path) == expected_component


def test_tj_e2e_005_meta_schema_requires_read_state_enum() -> None:
    bff_main.app.openapi_schema = None
    schema = bff_main.app.openapi()
    meta_schema = schema["components"]["schemas"]["TradeJourneyMeta"]
    assert set(meta_schema["properties"]["read_state"]["enum"]) == {"formal", "partial", "degraded", "unavailable"}
    freshness_schema = schema["components"]["schemas"]["TradeJourneyFreshness"]
    assert {
        "projection_schema_version",
        "generation",
        "projector_owned",
        "projection_mode",
        "truth_level",
        "accepted_live",
        "controller",
    } <= set(freshness_schema["properties"])


# --------------------------------------------------------------------------- #
# Route resolution (no shadowing between resolve/metrics and {journey_id})
# --------------------------------------------------------------------------- #

def test_tj_e2e_005_static_siblings_are_registered_before_journey_id_param_route() -> None:
    from starlette.routing import Route

    paths_in_order = [
        route.path
        for route in bff_main.app.routes
        if isinstance(route, Route) and route.path.startswith("/bff/management/trade-journeys")
    ]
    resolve_idx = paths_in_order.index("/bff/management/trade-journeys/resolve")
    metrics_idx = paths_in_order.index("/bff/management/trade-journeys/metrics")
    detail_idx = paths_in_order.index("/bff/management/trade-journeys/{journey_id}")
    assert resolve_idx < detail_idx
    assert metrics_idx < detail_idx


def test_tj_e2e_005_resolve_and_metrics_do_not_resolve_as_journey_id() -> None:
    def scenario():
        client, _ = _client_with(_BASE_EVENTS)
        resolve_resp = client.get(
            "/bff/management/trade-journeys/resolve?q=dec-1&tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        metrics_resp = client.get(
            "/bff/management/trade-journeys/metrics?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert resolve_resp.status_code == 200, resolve_resp.text
        assert resolve_resp.json()["data"]["id"] == "trade-journey-resolve"
        assert metrics_resp.status_code == 200, metrics_resp.text
        assert metrics_resp.json()["data"]["id"] == "trade-journey-metrics"

    _run(scenario)


# --------------------------------------------------------------------------- #
# List / detail / timeline / graph / evidence — canonical envelope shape
# --------------------------------------------------------------------------- #

def test_tj_e2e_005_list_returns_server_composed_row_with_no_cross_domain_join_needed() -> None:
    def scenario():
        client, _ = _client_with(_BASE_EVENTS)
        resp = client.get(
            "/bff/management/trade-journeys?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["page_info"]["total"] == 1
        row = payload["data"]["items"][0]
        for field in (
            "journey_id", "environment", "status", "current_stage", "severity",
            "persona_id", "strategy_id", "symbol", "side", "quantity",
            "decision_id", "broker_order_id", "created_at", "updated_at",
            "stage_elapsed_seconds", "total_elapsed_seconds", "flags", "completeness",
            "read_state", "revision",
        ):
            assert field in row, f"missing list-row field {field}"
        assert row["status"] == "completed"
        assert row["read_state"] == "formal"
        assert payload["meta"]["read_state"] == "formal"
        assert payload["meta"]["freshness"]["materializer_revision"] == 1

    _run(scenario)


def test_tj_e2e_005_detail_includes_stages_diagnostics_and_identifiers() -> None:
    def scenario():
        client, _ = _client_with(_BASE_EVENTS)
        resp = client.get(
            "/bff/management/trade-journeys/tj_1?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["journey_id"] == "tj_1"
        assert set(data["stages"].keys()) >= {"signal_generation", "reconciliation"}
        assert data["identifiers"]["decision_id"] == ["dec-1"]
        # A "missing_stages" diagnostic for the pre-signal stages (1-6) is
        # expected and structural (they never carry this journey_id, see
        # contract.md section 5.1) — it must not count as an anomaly.
        diagnostic_codes = {item["code"] for item in data["diagnostics"]}
        assert diagnostic_codes <= {"missing_stages"}
        assert data["read_state"] == "formal"
        assert data["event_count"] == 8

    _run(scenario)


def test_tj_e2e_005_timeline_is_cursor_paginated_and_ordered() -> None:
    def scenario():
        client, _ = _client_with(_BASE_EVENTS)
        resp = client.get(
            "/bff/management/trade-journeys/tj_1/timeline?tenant_id=tenant-a&environment=paper&page_size=3",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["page_info"]["total"] == 8
        assert payload["page_info"]["returned"] == 3
        assert payload["page_info"]["has_more"] is True
        assert [item["event_id"] for item in payload["data"]["items"]] == ["e1", "e2", "e3"]

        next_token = payload["page_info"]["next_page_token"]
        resp2 = client.get(
            f"/bff/management/trade-journeys/tj_1/timeline?tenant_id=tenant-a&environment=paper&page_size=3&page_token={next_token}",
            headers=OPERATOR_HEADERS,
        )
        assert [item["event_id"] for item in resp2.json()["data"]["items"]] == ["e4", "e5", "e6"]

    _run(scenario)


def test_tj_e2e_005_graph_includes_replace_chain_edges() -> None:
    def scenario():
        events = [
            _event("e1", "tj_1", "order_submission", "succeeded", 1, order_id="order-1"),
            _event(
                "e2", "tj_1", "broker_acknowledgement", "succeeded", 2,
                order_id="order-2",
                graph_edges=[{"from": "order-1", "to": "order-2", "type": "replaced_by"}],
            ),
        ]
        client, _ = _client_with(events)
        resp = client.get(
            "/bff/management/trade-journeys/tj_1/graph?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert {"from": "order-1", "to": "order-2", "type": "replaced_by"} in data["edges"]
        node_ids = {node["id"] for node in data["nodes"]}
        assert {"tj_1", "order-1", "order-2"} <= node_ids

    _run(scenario)


def test_tj_e2e_005_evidence_groups_refs_by_stage() -> None:
    def scenario():
        events = [
            _event(
                "e1", "tj_1", "risk_evaluation", "succeeded", 1,
                evidence_refs=["ref-1"], policy_refs=["policy-1"],
            ),
        ]
        client, _ = _client_with(events)
        resp = client.get(
            "/bff/management/trade-journeys/tj_1/evidence?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        by_stage = resp.json()["data"]["by_stage"]
        assert by_stage["risk_evaluation"]["evidence_refs"] == ["ref-1"]
        assert by_stage["risk_evaluation"]["policy_refs"] == ["policy-1"]

    _run(scenario)


# --------------------------------------------------------------------------- #
# Resolve — ambiguity-aware, never picks first silently
# --------------------------------------------------------------------------- #

def test_tj_e2e_005_resolve_reports_ambiguity_across_multiple_journeys() -> None:
    def scenario():
        events = [
            _event("e1", "tj_1", "order_submission", "succeeded", 1, order_id="shared-id"),
            _event("e2", "tj_2", "order_submission", "succeeded", 1, order_id="shared-id"),
        ]
        client, _ = _client_with(events)
        resp = client.get(
            "/bff/management/trade-journeys/resolve?q=shared-id&tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["ambiguous"] is True
        assert sorted(data["journey_ids"]) == ["tj_1", "tj_2"]

    _run(scenario)


def test_tj_e2e_005_resolve_single_match_is_unambiguous() -> None:
    def scenario():
        client, _ = _client_with(_BASE_EVENTS)
        resp = client.get(
            "/bff/management/trade-journeys/resolve?q=dec-1&tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        data = resp.json()["data"]
        assert data["ambiguous"] is False
        assert data["journey_ids"] == ["tj_1"]

    _run(scenario)


def test_tj_e2e_005_resolve_covers_every_claimed_identifier_from_research_to_reconciliation() -> None:
    def scenario():
        identifiers_data = {
            "research_journey_id": "rj-unique-123",
            "strategy_lifecycle_id": "sl-unique-123",
            "persona_id": "persona-unique-123",
            "strategy_id": "strategy-unique-123",
            "signal_id": "sig-unique-123",
            "decision_id": "dec-unique-123",
            "risk_decision_id": "risk-unique-123",
            "client_order_id": "co-unique-123",
            "order_id": "ord-unique-123",
            "broker_order_id": "bo-unique-123",
            "fill_id": "fill-unique-123",
            "broker_trade_id": "bt-unique-123",
            "ledger_entry_id": "le-unique-123",
            "reconciliation_id": "recon-unique-123",
        }
        # Ingest one event containing all of them
        events = [_event("e_all", "tj_1", "reconciliation", "succeeded", 1, **identifiers_data)]
        client, _ = _client_with(events)

        # Test each identifier type resolves correctly to tj_1
        for id_type, id_val in identifiers_data.items():
            resp = client.get(
                f"/bff/management/trade-journeys/resolve?q={id_val}&tenant_id=tenant-a&environment=paper",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, f"failed for {id_type}: {resp.text}"
            data = resp.json()["data"]
            assert data["ambiguous"] is False, f"ambiguity failed for {id_type}"
            assert data["journey_ids"] == ["tj_1"], f"resolve failed for {id_type}"

    _run(scenario)



# --------------------------------------------------------------------------- #
# Replay — read-only, honors as_of, avoids the TJ-E2E-004 precision bug class
# --------------------------------------------------------------------------- #

def test_tj_e2e_005_replay_returns_state_as_of_a_historical_timestamp() -> None:
    def scenario():
        client, _ = _client_with(_BASE_EVENTS, raw_events=_BASE_EVENTS)
        resp = client.get(
            "/bff/management/trade-journeys/tj_1/replay"
            "?tenant_id=tenant-a&environment=paper&as_of=2026-07-12T00:03:30Z",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["exists_at_as_of"] is True
        assert set(data["stages"].keys()) == {"signal_generation", "trade_decision", "risk_evaluation"}
        assert "order_submission" not in data["stages"]

    _run(scenario)


def test_tj_e2e_005_replay_before_journey_existed_reports_not_yet_existing() -> None:
    def scenario():
        client, _ = _client_with(_BASE_EVENTS, raw_events=_BASE_EVENTS)
        resp = client.get(
            "/bff/management/trade-journeys/tj_1/replay"
            "?tenant_id=tenant-a&environment=paper&as_of=2026-07-11T23:59:00Z",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["exists_at_as_of"] is False

    _run(scenario)


def test_tj_e2e_005_replay_rejects_malformed_as_of() -> None:
    def scenario():
        client, _ = _client_with(_BASE_EVENTS, raw_events=_BASE_EVENTS)
        resp = client.get(
            "/bff/management/trade-journeys/tj_1/replay?tenant_id=tenant-a&environment=paper&as_of=not-a-date",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_FAILED"

    _run(scenario)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

def test_tj_e2e_005_metrics_reports_diagnostics_rate_and_stage_latency() -> None:
    def scenario():
        events = [
            _event(
                "e1", "tj_1", "risk_evaluation", "rejected", 1,
                order_id="orphan", client_order_id=None, broker_order_id=None,
                recorded_at="2026-07-12T00:01:00.500000Z",
            ),
            _event("e2", "tj_1", "reconciliation", "succeeded", 2, client_order_id=None, broker_order_id=None),
        ]
        client, _ = _client_with(events)
        resp = client.get(
            "/bff/management/trade-journeys/metrics?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total_journeys"] == 1
        assert data["diagnostics_counts"].get("orphan_identifier") == 1
        assert data["diagnostics_rate"]["orphan_identifier"] == 1.0
        assert "risk_evaluation" in data["stage_latency_ms"]
        assert data["stage_latency_ms"]["risk_evaluation"]["sample_count"] == 1

    _run(scenario)


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #

def test_tj_e2e_005_list_requires_read_auth() -> None:
    def scenario():
        client, _ = _client_with(_BASE_EVENTS)
        resp = client.get(
            "/bff/management/trade-journeys?tenant_id=tenant-a&environment=paper",
        )
        assert resp.status_code == 401

    _run(scenario)


def test_tj_e2e_005_cross_tenant_list_access_is_forbidden() -> None:
    client, _ = _direct_client(_BASE_EVENTS)
    resp = client.get(
        "/bff/management/trade-journeys?tenant_id=tenant-b&environment=paper",
        headers={"Authorization": "Bearer viewer-1:viewer:tenant-a"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_tj_e2e_005_same_tenant_scoped_viewer_can_list() -> None:
    client, _ = _direct_client(_BASE_EVENTS)
    resp = client.get(
        "/bff/management/trade-journeys?tenant_id=tenant-a&environment=paper",
        headers={"Authorization": "Bearer viewer-1:viewer:tenant-a"},
    )
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- #
# Identifier-existence protection: unknown id and cross-tenant id both 404
# --------------------------------------------------------------------------- #

def test_tj_e2e_005_unknown_journey_id_returns_404() -> None:
    def scenario():
        client, _ = _client_with(_BASE_EVENTS)
        resp = client.get(
            "/bff/management/trade-journeys/does-not-exist?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    _run(scenario)


def test_tj_e2e_005_out_of_scope_tenant_detail_returns_identical_404() -> None:
    """A journey that exists but is outside the caller's tenant scope must not
    be distinguishable from one that never existed (gap-spec section 12)."""
    client, _ = _direct_client(_BASE_EVENTS)
    headers = {"Authorization": "Bearer viewer-1:viewer:tenant-a"}
    real_resp = client.get(
        "/bff/management/trade-journeys/does-not-exist?tenant_id=tenant-a&environment=paper",
        headers=headers,
    )
    scoped_out_resp = client.get(
        "/bff/management/trade-journeys/tj_1?tenant_id=tenant-b&environment=paper",
        headers=headers,
    )
    assert real_resp.status_code == scoped_out_resp.status_code == 404
    assert real_resp.json() == scoped_out_resp.json()


# --------------------------------------------------------------------------- #
# Degraded / unavailable semantics
# --------------------------------------------------------------------------- #

def test_tj_e2e_005_unavailable_store_returns_200_with_explicit_unavailable_state() -> None:
    def scenario():
        store = tj.TradeJourneyEventStore()
        store.materializer = lambda: None
        tj.EVENT_STORE = store
        client = TestClient(bff_main.app)
        resp = client.get(
            "/bff/management/trade-journeys?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["data"]["items"] == []
        assert payload["meta"]["read_state"] == "unavailable"
        assert payload["page_info"]["total"] == 0

    _run(scenario)


def test_tj_e2e_005_backfill_only_projector_store_exposes_controller_and_downgrades_formal(
    tmp_path, monkeypatch
) -> None:
    store_file = tmp_path / "trade_journey_events.json"
    controller = {
        "controller_id": "canonical-lifecycle-projector",
        "controller_name": "canonical-lifecycle-projector",
        "deployment_sha": "abc123",
        "status": "repair_only",
        "mode": "backfill",
        "truth_level": "backfill_only",
        "accepted_live": False,
        "checkpoint": 0,
        "source_high_watermark": 8,
        "backlog": 8,
        "generation": 4,
    }
    store_file.write_text(
        json.dumps(
            {
                "schema_version": "pantheon.trade-journey-projection.v1",
                "generation": 4,
                "controller": controller,
                "events": _BASE_EVENTS,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE", str(store_file))

    def scenario():
        store = tj.TradeJourneyEventStore()
        tj.EVENT_STORE = store
        client = TestClient(bff_main.app)
        response = client.get(
            "/bff/management/trade-journeys?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert response.status_code == 200, response.text
        meta = response.json()["meta"]
        assert meta["read_state"] == "degraded"
        freshness = meta["freshness"]
        assert freshness["projector_owned"] is True
        assert freshness["projection_schema_version"] == "pantheon.trade-journey-projection.v1"
        assert freshness["generation"] == 4
        assert freshness["projection_mode"] == "backfill"
        assert freshness["truth_level"] == "backfill_only"
        assert freshness["accepted_live"] is False
        assert freshness["controller"] == controller
        assert store.projector_owned() is True

    _run(scenario)


def test_tj_e2e_005_live_projector_store_can_report_formal(tmp_path, monkeypatch) -> None:
    store_file = tmp_path / "trade_journey_events.json"
    store_file.write_text(
        json.dumps(
            {
                "schema_version": "pantheon.trade-journey-projection.v1",
                "generation": 5,
                "controller": {
                    "controller_id": "canonical-lifecycle-projector",
                    "status": "ready",
                    "mode": "live",
                    "truth_level": "canonical_live",
                    "accepted_live": True,
                    "checkpoint": 8,
                    "source_high_watermark": 8,
                    "backlog": 0,
                },
                "events": _BASE_EVENTS,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE", str(store_file))

    def scenario():
        tj.EVENT_STORE = tj.TradeJourneyEventStore()
        response = TestClient(bff_main.app).get(
            "/bff/management/trade-journeys?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert response.status_code == 200, response.text
        meta = response.json()["meta"]
        assert meta["read_state"] == "formal"
        assert meta["freshness"]["accepted_live"] is True
        assert meta["freshness"]["projection_mode"] == "live"

    _run(scenario)


def test_tj_e2e_005_degraded_projector_cannot_reuse_historic_live_acceptance(
    tmp_path, monkeypatch
) -> None:
    store_file = tmp_path / "trade_journey_events.json"
    store_file.write_text(
        json.dumps(
            {
                "schema_version": "pantheon.trade-journey-projection.v1",
                "generation": 6,
                "controller": {
                    "controller_id": "canonical-lifecycle-projector",
                    "status": "degraded",
                    "mode": "live",
                    "truth_level": "canonical_live",
                    # A previous accepted live checkpoint is diagnostic history,
                    # not proof that this degraded generation is formally live.
                    "accepted_live": True,
                    "checkpoint": 8,
                    "source_high_watermark": 8,
                    "backlog": 0,
                },
                "events": _BASE_EVENTS,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE", str(store_file))

    def scenario():
        tj.EVENT_STORE = tj.TradeJourneyEventStore()
        response = TestClient(bff_main.app).get(
            "/bff/management/trade-journeys?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert response.status_code == 200, response.text
        meta = response.json()["meta"]
        assert meta["read_state"] == "degraded"
        assert meta["freshness"]["accepted_live"] is True
        assert meta["freshness"]["controller"]["status"] == "degraded"

    _run(scenario)


def test_tj_e2e_005_conflicting_diagnostics_report_degraded_read_state() -> None:
    def scenario():
        events = [
            _event("e1", "tj_1", "risk_evaluation", "rejected", 1),
            _event("e2", "tj_1", "reconciliation", "succeeded", 2),
        ]
        client, _ = _client_with(events)
        resp = client.get(
            "/bff/management/trade-journeys/tj_1?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["read_state"] == "degraded"
        assert data["status"] == "incomplete"

    _run(scenario)


def test_tj_e2e_005_journey_still_in_flight_with_no_gap_is_formal() -> None:
    """A journey that has only reached its first observable stage, with no
    skipped stage before it, is not "incomplete data" — it is simply early in
    its lifecycle. `read_state` reflects data quality, not lifecycle stage."""

    def scenario():
        events = [_event("e1", "tj_1", "signal_generation", "succeeded", 1)]
        client, _ = _client_with(events)
        resp = client.get(
            "/bff/management/trade-journeys/tj_1?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        data = resp.json()["data"]
        assert data["read_state"] == "formal"

    _run(scenario)


def test_tj_e2e_005_skipped_observable_stage_is_partial_not_degraded() -> None:
    """A genuine gap between reached stages (here: jumping straight to
    risk_evaluation with no trade_decision event) is a real completeness
    gap, distinct from an anomaly (conflicting/orphan/duplicate)."""

    def scenario():
        events = [_event("e1", "tj_1", "risk_evaluation", "succeeded", 1)]
        client, _ = _client_with(events)
        resp = client.get(
            "/bff/management/trade-journeys/tj_1?tenant_id=tenant-a&environment=paper",
            headers=OPERATOR_HEADERS,
        )
        data = resp.json()["data"]
        assert "trade_decision" in data["completeness"]["missing_stages"]
        assert data["read_state"] == "partial"

    _run(scenario)


# --------------------------------------------------------------------------- #
# DTO-level: row-level scope enforcement without a live BFF request
# --------------------------------------------------------------------------- #

def test_tj_e2e_005_tenant_allowed_helper_scopes_by_claim() -> None:
    scoped_viewer = _StubIdentity(["viewer"], {"tenant_ids": ["tenant-a"]})
    dev_login_viewer = _StubIdentity(
        ["viewer"],
        {"tenant_id": "tenant-a", "allowed_tenants": ["tenant-a"]},
    )
    primary_tenant_viewer = _StubIdentity(["viewer"], {"tenant_id": "tenant-a"})
    admin = _StubIdentity(["admin"], {})
    unscoped_viewer = _StubIdentity(["viewer"], {})

    assert tj._tenant_allowed(scoped_viewer, "tenant-a") is True
    assert tj._tenant_allowed(scoped_viewer, "tenant-b") is False
    assert tj._tenant_allowed(dev_login_viewer, "tenant-a") is True
    assert tj._tenant_allowed(dev_login_viewer, "tenant-b") is False
    assert tj._tenant_allowed(primary_tenant_viewer, "tenant-a") is True
    assert tj._tenant_allowed(primary_tenant_viewer, "tenant-b") is False
    assert tj._tenant_allowed(admin, "tenant-z") is True
    assert tj._tenant_allowed(unscoped_viewer, "tenant-anything") is True


def test_tj_e2e_005_live_sensitive_values_and_identifier_probes_are_masked_for_viewers() -> None:
    def scenario():
        events = [
            _event(
                "e1",
                "tj_1",
                "signal_generation",
                "succeeded",
                1,
                price=101.5,
                account_id="acct-secret",
                capital_account_id="capital-secret",
                order_id="ord-secret",
                client_order_id="client-secret",
                broker_order_id="broker-secret",
                graph_edges=[{"from": "tj_1", "to": "ord-secret", "type": "submitted"}],
                environment="live",
                tenant="tenant-a",
            )
        ]
        client, _ = _client_with(events)
        viewer_headers = {"Authorization": "Bearer op-tj-005-live-viewer:viewer"}

        list_resp = client.get(
            "/bff/management/trade-journeys?tenant_id=tenant-a&environment=live",
            headers=viewer_headers,
        )
        assert list_resp.status_code == 200, list_resp.text
        list_row = list_resp.json()["data"]["items"][0]
        for field in ("order_id", "client_order_id", "broker_order_id", "quantity", "price"):
            assert list_row[field] == "***"
        assert list_row["live_capital_masked"] is True

        resp = client.get(
            "/bff/management/trade-journeys/tj_1?tenant_id=tenant-a&environment=live",
            headers=viewer_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["quantity"] == "***"
        assert data["price"] == "***"
        assert data["order_id"] == "***"
        assert data["client_order_id"] == "***"
        assert data["broker_order_id"] == "***"
        assert data["identifiers"]["order_id"] == ["***"]
        stage_event = data["stage_events"]["signal_generation"]
        assert stage_event["account_id"] == "***"
        assert stage_event["capital_account_id"] == "***"
        assert data["live_capital_masked"] is True

        for query in (
            "q=ord-secret",
            "order_id=ord-secret",
            "broker_order_id=broker-secret",
        ):
            search_resp = client.get(
                f"/bff/management/trade-journeys?tenant_id=tenant-a&environment=live&{query}",
                headers=viewer_headers,
            )
            assert search_resp.status_code == 200, search_resp.text
            assert search_resp.json()["data"]["items"] == []

        viewer_resolve = client.get(
            "/bff/management/trade-journeys/resolve"
            "?q=ord-secret&identifier_type=order_id&tenant_id=tenant-a&environment=live",
            headers=viewer_headers,
        )
        assert viewer_resolve.status_code == 200, viewer_resolve.text
        assert viewer_resolve.json()["data"]["candidates"] == []
        assert viewer_resolve.json()["data"]["journey_ids"] == []

        graph_resp = client.get(
            "/bff/management/trade-journeys/tj_1/graph?tenant_id=tenant-a&environment=live",
            headers=viewer_headers,
        )
        assert graph_resp.status_code == 200, graph_resp.text
        assert "ord-secret" not in json.dumps(graph_resp.json()["data"])

        operator_resp = client.get(
            "/bff/management/trade-journeys/tj_1?tenant_id=tenant-a&environment=live",
            headers=OPERATOR_HEADERS,
        )
        operator_data = operator_resp.json()["data"]
        assert operator_data["quantity"] == 10
        assert operator_data["price"] == 101.5
        assert operator_data["order_id"] == "ord-secret"
        assert operator_data["stage_events"]["signal_generation"]["account_id"] == "acct-secret"

        operator_resolve = client.get(
            "/bff/management/trade-journeys/resolve"
            "?q=ord-secret&identifier_type=order_id&tenant_id=tenant-a&environment=live",
            headers=OPERATOR_HEADERS,
        )
        assert operator_resolve.status_code == 200, operator_resolve.text
        assert operator_resolve.json()["data"]["journey_ids"] == ["tj_1"]

    _run(scenario)


# --------------------------------------------------------------------------- #
# Performance smoke test: list pagination over a larger read model stays fast
# --------------------------------------------------------------------------- #

def test_tj_e2e_005_list_pagination_handles_many_journeys_within_budget() -> None:
    def scenario():
        events = []
        for i in range(500):
            journey_id = f"tj_{i}"
            events.append(_event(f"e{i}-1", journey_id, "signal_generation", "succeeded", 0))
            events.append(_event(f"e{i}-2", journey_id, "trade_decision", "succeeded", 1))
        client, _ = _client_with(events)

        started = time.monotonic()
        resp = client.get(
            "/bff/management/trade-journeys?tenant_id=tenant-a&environment=paper&page_size=50&sort=updated_at_desc",
            headers=OPERATOR_HEADERS,
        )
        elapsed = time.monotonic() - started

        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["page_info"]["total"] == 500
        assert payload["page_info"]["returned"] == 50
        assert elapsed < 2.0, f"list over 500 journeys took {elapsed:.3f}s, expected < 2.0s"

    _run(scenario)


def test_tj_e2e_005_publish_events_appends_and_saves_to_store_file(tmp_path) -> None:
    store_file = tmp_path / "trade_journey_events.json"
    # Seed empty list
    store_file.write_text("[]", encoding="utf-8")

    # We set the environment variable so the route finds the temp file
    original_store_env = os.environ.get("PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE")
    os.environ["PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE"] = str(store_file)

    try:
        client = TestClient(bff_main.app)
        new_event = {
            "event_id": "test-evt-123",
            "journey_id": "tj-123",
            "tenant_id": "tenant-a",
            "environment": "paper",
            "occurred_at": "2026-07-12T00:00:00Z",
            "stage": "signal_generation",
            "stage_status": "succeeded",
        }

        # 1. Test authorization check (no header)
        resp_unauth = client.post("/bff/management/trade-journeys/events", json=[new_event])
        assert resp_unauth.status_code == 401

        # 2. Test empty batch error
        resp_empty = client.post("/bff/management/trade-journeys/events", json=[], headers=OPERATOR_HEADERS)
        assert resp_empty.status_code == 400
        assert "batch cannot be empty" in resp_empty.text

        # 3. Test validation error (missing required field)
        invalid_event = {**new_event}
        invalid_event.pop("tenant_id")
        resp_invalid = client.post("/bff/management/trade-journeys/events", json=[invalid_event], headers=OPERATOR_HEADERS)
        assert resp_invalid.status_code == 400
        assert "VALIDATION_FAILED" in resp_invalid.text

        # 4. Test validation error (non-timezone-aware timestamp)
        invalid_ts_event = {**new_event, "occurred_at": "2026-07-12 00:00:00"}
        resp_invalid_ts = client.post("/bff/management/trade-journeys/events", json=[invalid_ts_event], headers=OPERATOR_HEADERS)
        assert resp_invalid_ts.status_code == 400
        assert "must be timezone-aware" in resp_invalid_ts.text

        # 5. Test validation error (conflicting duplicate event in the batch)
        dup_batch_event_1 = {**new_event, "event_id": "dup-batch"}
        dup_batch_event_2 = {**new_event, "event_id": "dup-batch", "occurred_at": "2026-07-12T00:05:00Z"}
        resp_dup_batch = client.post(
            "/bff/management/trade-journeys/events",
            json=[dup_batch_event_1, dup_batch_event_2],
            headers=OPERATOR_HEADERS
        )
        assert resp_dup_batch.status_code == 400
        assert "CONFLICTING_DUPLICATE" in resp_dup_batch.text

        # 6. Test successful publish
        resp = client.post("/bff/management/trade-journeys/events", json=[new_event], headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "ok", "count": 1}

        # 7. Test validation error (conflicting duplicate event in the store)
        conflicting_store_event = {**new_event, "occurred_at": "2026-07-12T00:05:00Z"}
        resp_store_conflict = client.post(
            "/bff/management/trade-journeys/events",
            json=[conflicting_store_event],
            headers=OPERATOR_HEADERS
        )
        assert resp_store_conflict.status_code == 409
        assert "CONFLICTING_DUPLICATE" in resp_store_conflict.text

        # 8. Test tenant scope rejection (403)
        tenant_constrained_headers = {"Authorization": "Bearer op-tj-005:operator:tenant-b"}
        resp_forbidden = client.post("/bff/management/trade-journeys/events", json=[new_event], headers=tenant_constrained_headers)
        assert resp_forbidden.status_code == 403

        # Verify it was saved to the temp file
        content = json.loads(store_file.read_text(encoding="utf-8"))["events"]
        assert len(content) == 1
        assert content[0]["event_id"] == "test-evt-123"

    finally:
        if original_store_env is not None:
            os.environ["PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE"] = original_store_env
        else:
            os.environ.pop("PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE", None)


def test_tj_e2e_005_publish_rejects_projector_owned_store_without_mutation(
    tmp_path, monkeypatch
) -> None:
    store_file = tmp_path / "trade_journey_events.json"
    payload = {
        "schema_version": "pantheon.trade-journey-projection.v1",
        "generation": 2,
        "controller": {
            "controller_id": "canonical-lifecycle-projector",
            "status": "ready",
            "mode": "live",
            "truth_level": "canonical_live",
            "accepted_live": True,
        },
        "events": _BASE_EVENTS,
    }
    original = json.dumps(payload, sort_keys=True)
    store_file.write_text(original, encoding="utf-8")
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE", str(store_file))

    response = TestClient(bff_main.app).post(
        "/bff/management/trade-journeys/events",
        headers=OPERATOR_HEADERS,
        json=[
            {
                "event_id": "must-not-write",
                "journey_id": "tj_1",
                "tenant_id": "tenant-a",
                "environment": "paper",
                "occurred_at": "2026-07-12T00:09:00Z",
                "stage": "reconciliation",
                "stage_status": "succeeded",
            }
        ],
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "PROJECTOR_OWNED_STORE"
    assert store_file.read_text(encoding="utf-8") == original


# --------------------------------------------------------------------------- #
# LIFECYCLE-PROJ-BFF-001: Postgres reader routing and cursor integrity
# --------------------------------------------------------------------------- #


def test_lifecycle_proj_bff_reader_tokens_reject_scope_and_filter_reuse() -> None:
    codec = PageTokenCodec("reader-token-secret-is-long-enough")
    token = codec.encode(
        {
            "v": 1,
            "kind": "journeys",
            "tenant_id": "tenant-a",
            "environment": "paper",
            "sort": "updated_at_desc",
            "filters": {"status": "open"},
            "after": ["2026-08-01T00:00:00Z", "tj-1"],
        }
    )
    accepted = codec.decode(
        token,
        expected={
            "v": 1,
            "kind": "journeys",
            "tenant_id": "tenant-a",
            "environment": "paper",
            "sort": "updated_at_desc",
            "filters": {"status": "open"},
        },
    )
    assert accepted["after"] == ["2026-08-01T00:00:00Z", "tj-1"]

    for incompatible in (
        {"tenant_id": "tenant-b"},
        {"environment": "live"},
        {"sort": "created_at_desc"},
        {"filters": {"status": "completed"}},
    ):
        expected = {
            "v": 1,
            "kind": "journeys",
            "tenant_id": "tenant-a",
            "environment": "paper",
            "sort": "updated_at_desc",
            "filters": {"status": "open"},
            **incompatible,
        }
        try:
            codec.decode(token, expected=expected)
        except InvalidPageToken:
            pass
        else:  # pragma: no cover - documents the security assertion
            raise AssertionError("scope-incompatible page token was accepted")


def test_lifecycle_proj_bff_list_uses_selected_postgres_reader() -> None:
    projection = _materializer_with(_BASE_EVENTS).projections[0]

    class Reader:
        def __init__(self):
            self.calls = []

        def page_journeys(self, **kwargs):
            self.calls.append(kwargs)
            return ProjectionPage(items=[projection], next_page_token="signed-next", total=1)

        def controller_freshness(self, **kwargs):
            return {
                "generation": 7,
                "checkpoint": 11,
                "status": "ready",
                "mode": "live",
                "accepted_live": True,
            }

    reader = Reader()
    client, _ = _direct_client(_BASE_EVENTS, projection_reader=reader)
    client = TestClient(client.app)
    response = client.get(
        "/bff/management/trade-journeys?tenant_id=tenant-a&environment=paper&status=open",
        headers={"Authorization": "Bearer scoped:viewer:tenant-a"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["page_info"]["next_page_token"] == "signed-next"
    assert payload["meta"]["freshness"]["rebuild_status"] == "postgres_projection_reader"
    assert reader.calls == [
        {
            "tenant_id": "tenant-a",
            "environment": "paper",
            "filters": {
                "q": None,
                "persona_id": None,
                "strategy_id": None,
                "decision_id": None,
                "order_id": None,
                "broker_order_id": None,
                "stage": None,
                "status": "open",
                "waiting_human": None,
                "reconciliation_state": None,
                "date_from": None,
                "date_to": None,
            },
            "sort": "updated_at_desc",
            "page_size": 50,
            "page_token": None,
        }
    ]


def test_lifecycle_proj_bff_postgres_page_query_is_scoped_and_bounded() -> None:
    queries = []
    journey_columns = [
        "tenant_id", "environment", "journey_id", "status", "stage_coverage",
        "is_terminal", "first_occurred_at", "last_occurred_at",
        "current_identity_summary", "evidence_summary", "diagnostic_summary",
        "loop_run_id", "projection_revision", "created_at", "updated_at",
    ]
    journey_row = (
        "tenant-a", "paper", "tj-1", "open", {}, False,
        "2026-08-01T00:00:00Z", "2026-08-01T00:01:00Z", {}, {}, {},
        "", 3, "2026-08-01T00:00:00Z", "2026-08-01T00:01:00Z",
    )

    class Cursor:
        def __init__(self):
            self.description = []
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            queries.append((sql, tuple(params)))
            if "COUNT(*) AS total" in sql:
                self.description = [("total",)]
                self.rows = [(1,)]
            else:
                self.description = [(name,) for name in journey_columns]
                self.rows = [journey_row]

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return Cursor()

    reader = TradeJourneyProjectionStore(
        "postgresql://unit",
        token_secret="reader-token-secret-is-long-enough",
        connect=lambda dsn: Connection(),
    )
    result = reader.page_journeys(
        tenant_id="tenant-a",
        environment="paper",
        filters={"status": "open"},
        page_size=200,
    )

    assert [item.journey_id for item in result.items] == ["tj-1"]
    page_sql, page_params = queries[0]
    assert "tenant_id=%s AND environment=%s AND status=%s" in page_sql
    assert "ORDER BY updated_at DESC, journey_id DESC LIMIT %s" in page_sql
    assert page_params[-1] == 201
    assert page_params[:3] == ("tenant-a", "paper", "open")
    count_sql, count_params = queries[1]
    assert "tenant_id=%s AND environment=%s AND status=%s" in count_sql
    assert " LIMIT " not in count_sql
    assert count_params == ("tenant-a", "paper", "open")
