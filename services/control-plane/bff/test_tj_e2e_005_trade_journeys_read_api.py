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

import os
import sys
import time

BFF_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BFF_DIR)

import main as bff_main  # noqa: E402
import trade_journeys as tj  # noqa: E402
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


def _direct_client(events, *, raw_events=None):
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
    admin = _StubIdentity(["admin"], {})
    unscoped_viewer = _StubIdentity(["viewer"], {})

    assert tj._tenant_allowed(scoped_viewer, "tenant-a") is True
    assert tj._tenant_allowed(scoped_viewer, "tenant-b") is False
    assert tj._tenant_allowed(admin, "tenant-z") is True
    assert tj._tenant_allowed(unscoped_viewer, "tenant-anything") is True


def test_tj_e2e_005_live_capital_is_masked_for_non_operator_roles() -> None:
    def scenario():
        events = [_event("e1", "tj_1", "signal_generation", "succeeded", 1, price=101.5, environment="live", tenant="tenant-a")]
        client, _ = _client_with(events)
        viewer_headers = {"Authorization": "Bearer op-tj-005-live-viewer:viewer"}
        resp = client.get(
            "/bff/management/trade-journeys/tj_1?tenant_id=tenant-a&environment=live",
            headers=viewer_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["quantity"] == "***"
        assert data["price"] == "***"
        assert data["live_capital_masked"] is True

        operator_resp = client.get(
            "/bff/management/trade-journeys/tj_1?tenant_id=tenant-a&environment=live",
            headers=OPERATOR_HEADERS,
        )
        operator_data = operator_resp.json()["data"]
        assert operator_data["quantity"] == 10
        assert operator_data["price"] == 101.5

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
