from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from services.trade_journey.materializer import JourneyMaterializer

from .models import AdjustmentSuggestion, SuggestionProvenance
from .router import create_performance_router
from .store import PerformanceSuggestionStore


NOW = "2026-07-22T18:00:00Z"
ROOT = Path(__file__).resolve().parents[5]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"
STATIC_SCHEMA_PATH = AGORA_SPECS / "v11/performance_truth.schema.json"
STATIC_MANIFEST_PATH = AGORA_SPECS / "v11/capability_manifest_v1_10.json"
STATIC_BUNDLE_PATH = AGORA_SPECS / "bundle_index.v1_10.json"
STATIC_OPENAPI_PATH = ROOT / "services/control-plane/openapi/agora_v1_10.openapi.yaml"


class _JourneyStore:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self._materializer = None
        if events is not None:
            self._materializer = JourneyMaterializer()
            self._materializer.rebuild(events)

    def materializer(self) -> JourneyMaterializer | None:
        return self._materializer

    def projection_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": "pantheon.trade-journey-projection.v1",
            "generation": 42,
            "projector_owned": True,
            "controller": {
                "controller_id": "canonical-lifecycle-projector",
                "generation": 42,
                "mode": "live",
                "status": "ready",
                "truth_level": "canonical_live",
                "accepted_live": True,
            },
        }


def _identity(authorization: str | None) -> SimpleNamespace:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, detail="authentication required")
    token = authorization.removeprefix("Bearer ")
    user_id, role, tenant_id = token.split(":", 2)
    return SimpleNamespace(
        operator_id=user_id,
        roles=[role],
        claims={
            "sub": user_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "allowed_tenants": [tenant_id],
        },
    )


def _require_read(identity: SimpleNamespace) -> None:
    if not {"viewer", "operator", "reviewer", "approver", "admin"}.intersection(
        identity.roles
    ):
        raise HTTPException(403, detail="read role required")


def _require_write(identity: SimpleNamespace) -> None:
    if not {"operator", "reviewer", "approver", "admin"}.intersection(identity.roles):
        raise HTTPException(403, detail="write role required")


def _bff_error(status: int, _code: Any, message: str, _reason: str, **_: Any) -> HTTPException:
    return HTTPException(status, detail=message)


class _MockWorkshopStore:
    def __init__(self, sessions: Optional[list[dict[str, Any]]] = None) -> None:
        self.sessions = sessions or []

    def list_sessions(
        self,
        *,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], None]:
        results = [
            s
            for s in self.sessions
            if (user_id is None or s.get("user_id") == user_id)
            and (tenant_id is None or s.get("tenant_id") == tenant_id)
        ]
        return results[:limit], None


def _app(
    store: PerformanceSuggestionStore,
    journey_store: _JourneyStore,
    workshop_store: Optional[Any] = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(
        create_performance_router(
            extract_identity=_identity,
            require_read_role=_require_read,
            require_write_role=_require_write,
            bff_error=_bff_error,
            utc_now=lambda: NOW,
            get_trade_journey_store=lambda: journey_store,
            workshop_store=workshop_store,
            suggestion_store=store,
        )
    )
    return app


def _events(*, user_id: str = "alice", tenant_id: str = "tenant-a") -> list[dict[str, Any]]:
    common = {
        "journey_id": "journey-001",
        "tenant_id": tenant_id,
        "environment": "paper",
        "source": "canonical-lifecycle-projector",
        "user_id": user_id,
        "strategy_id": "strategy-alpha",
        "decision_id": "decision-001",
        "order_id": "order-001",
        "fill_id": "fill-001",
        "reconciliation_id": "reconciliation-001",
    }
    return [
        {
            **common,
            "event_id": "event-001",
            "occurred_at": "2026-07-22T17:00:00Z",
            "stage": "trade_decision",
            "stage_status": "succeeded",
            "compliance_metrics": [
                {
                    "metric_id": "strategy-adherence",
                    "label": "Strategy adherence",
                    "value": 0.92,
                    "unit": "ratio",
                    "calculation_id": "calc-001",
                    "source_id": "compliance-projection-001",
                    "as_of": "2026-07-22T17:00:00Z",
                    "evidence_refs": ["event-001"],
                }
            ],
            "intervention": {
                "intervention_id": "intervention-001",
                "kind": "manual_override",
                "status": "closed",
                "occurred_at": "2026-07-22T17:00:00Z",
                "source_id": "intervention-ledger-001",
                "evidence_refs": ["event-001"],
            },
        },
        {
            **common,
            "event_id": "event-002",
            "occurred_at": "2026-07-22T17:10:00Z",
            "stage": "order_submission",
            "stage_status": "succeeded",
        },
        {
            **common,
            "event_id": "event-003",
            "occurred_at": "2026-07-22T17:20:00Z",
            "stage": "fill_management",
            "stage_status": "succeeded",
        },
        {
            **common,
            "event_id": "event-004",
            "occurred_at": "2026-07-22T17:30:00Z",
            "stage": "reconciliation",
            "stage_status": "succeeded",
        },
    ]


def _suggestion() -> AdjustmentSuggestion:
    return AdjustmentSuggestion(
        suggestion_id="suggestion-001",
        strategy_id="strategy-alpha",
        period="latest",
        title="Source-authored adjustment",
        rationale="Source-authored rationale",
        expected_effect=None,
        expected_risk=None,
        provenance=SuggestionProvenance(
            source_id="workshop-evaluation-001",
            source_type="governed_workshop_evaluation",
            source_version="3",
            produced_at="2026-07-22T16:45:00Z",
            evidence_refs=["evaluation-001"],
        ),
        as_of="2026-07-22T16:45:00Z",
    )


def _headers(user: str = "alice", role: str = "operator", tenant: str = "tenant-a") -> dict[str, str]:
    return {"Authorization": f"Bearer {user}:{role}:{tenant}"}


def _performance_url(period: str = "latest") -> str:
    return (
        "/bff/agora/trading-room/strategies/strategy-alpha/performance"
        f"?period={period}&environment=paper"
    )


def test_missing_sources_are_typed_unavailable_without_fabricated_values(tmp_path: Path) -> None:
    client = TestClient(
        _app(PerformanceSuggestionStore(str(tmp_path / "performance.db")), _JourneyStore())
    )
    response = client.get(_performance_url(), headers=_headers())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["availability"] == "unavailable"
    assert data["freshness"]["as_of"] is None
    assert set(data["freshness"]["unavailable_sources"]) == {
        "adjustment_suggestions",
        "compliance",
        "execution_history",
        "interventions",
        "warnings",
    }
    assert data["compliance"] == {
        "availability": {
            "status": "unavailable",
            "as_of": None,
            "source_ids": [],
            "reason": "compliance_metrics_unavailable",
        },
        "metrics": [],
    }
    assert data["execution_history"]["items"] == []
    assert data["adjustment_suggestions"]["items"] == []


def test_projection_preserves_source_values_identities_and_nullable_effects(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    store.upsert_suggestion(
        tenant_id="tenant-a", owner_user_id="alice", suggestion=_suggestion()
    )
    client = TestClient(_app(store, _JourneyStore(_events())))

    response = client.get(_performance_url(), headers=_headers())
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["availability"] == "available"
    assert data["freshness"]["projection_generation"] == 42
    assert data["compliance"]["metrics"][0]["value"] == 0.92
    assert data["compliance"]["metrics"][0]["calculation_id"] == "calc-001"
    assert data["interventions"]["aggregate"] == {
        "total": 1,
        "by_status": {"closed": 1},
    }
    row = data["execution_history"]["items"][0]
    assert row["decision_ids"] == ["decision-001"]
    assert row["order_ids"] == ["order-001"]
    assert row["fill_ids"] == ["fill-001"]
    assert row["reconciliation_ids"] == ["reconciliation-001"]
    suggestion = data["adjustment_suggestions"]["items"][0]
    assert suggestion["expected_effect"] is None
    assert suggestion["expected_risk"] is None
    assert suggestion["provenance"]["source_id"] == "workshop-evaluation-001"
    assert data["no_order_route_proof"] == "agora_performance_read_only"


def test_user_and_tenant_scope_fail_closed_as_unavailable(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    store.upsert_suggestion(
        tenant_id="tenant-a", owner_user_id="alice", suggestion=_suggestion()
    )
    client = TestClient(_app(store, _JourneyStore(_events())))

    other_user = client.get(_performance_url(), headers=_headers(user="bob"))
    other_tenant = client.get(
        _performance_url(), headers=_headers(user="alice", tenant="tenant-b")
    )
    assert other_user.status_code == 200
    assert other_user.json()["data"]["availability"] == "unavailable"
    assert other_user.json()["data"]["adjustment_suggestions"]["items"] == []
    assert other_tenant.status_code == 200
    assert other_tenant.json()["data"]["availability"] == "unavailable"


def test_viewer_can_read_but_cannot_write(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    store.upsert_suggestion(
        tenant_id="tenant-a", owner_user_id="alice", suggestion=_suggestion()
    )
    client = TestClient(_app(store, _JourneyStore(_events())))
    assert client.get(_performance_url(), headers=_headers(role="viewer")).status_code == 200

    response = client.post(
        "/bff/agora/trading-room/strategies/strategy-alpha/performance/"
        "suggestions/suggestion-001/actions",
        headers={**_headers(role="viewer"), "Idempotency-Key": "viewer-write-001"},
        json={"action": "apply", "expected_version": 1},
    )
    assert response.status_code == 403


def test_viewer_cannot_read_live_execution_identities(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    client = TestClient(_app(store, _JourneyStore(_events())))
    response = client.get(
        "/bff/agora/trading-room/strategies/strategy-alpha/performance"
        "?period=latest&environment=live",
        headers=_headers(role="viewer"),
    )
    assert response.status_code == 403


def test_conflicting_strategy_identity_fails_closed(tmp_path: Path) -> None:
    events = _events()
    events[-1] = {**events[-1], "strategy_id": "strategy-beta"}
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    client = TestClient(_app(store, _JourneyStore(events)))
    response = client.get(_performance_url(), headers=_headers())
    assert response.status_code == 200
    assert response.json()["data"]["execution_history"]["availability"]["status"] == "unavailable"
    assert response.json()["data"]["execution_history"]["items"] == []


@pytest.mark.parametrize(
    ("action", "expected_status"),
    [
        ("apply", "applied"),
        ("reject", "rejected"),
        ("return_to_workshop", "returned_to_workshop"),
    ],
)
def test_each_governed_action_returns_durable_receipt(
    tmp_path: Path, action: str, expected_status: str
) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / f"{action}.db"))
    store.upsert_suggestion(
        tenant_id="tenant-a", owner_user_id="alice", suggestion=_suggestion()
    )
    client = TestClient(_app(store, _JourneyStore(_events())))
    response = client.post(
        "/bff/agora/trading-room/strategies/strategy-alpha/performance/"
        "suggestions/suggestion-001/actions",
        headers={**_headers(), "Idempotency-Key": f"{action}-receipt-001"},
        json={"action": action, "expected_version": 1, "reason": "operator decision"},
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["data"]
    assert receipt["status"] == expected_status
    assert receipt["version"] == 2
    assert receipt["authoritative_readback"]["status"] == expected_status
    assert receipt["execution_authority"] == "none"
    assert receipt["no_order_route_proof"] == "agora_suggestion_state_only"


def test_idempotency_cas_audit_and_restart_readback(tmp_path: Path) -> None:
    db_path = tmp_path / "performance.db"
    store = PerformanceSuggestionStore(str(db_path))
    store.upsert_suggestion(
        tenant_id="tenant-a", owner_user_id="alice", suggestion=_suggestion()
    )
    client = TestClient(_app(store, _JourneyStore(_events())))
    url = (
        "/bff/agora/trading-room/strategies/strategy-alpha/performance/"
        "suggestions/suggestion-001/actions"
    )
    headers = {**_headers(), "Idempotency-Key": "apply-suggestion-001"}
    first = client.post(
        url,
        headers=headers,
        json={"action": "apply", "expected_version": 1, "reason": "accepted"},
    )
    replay = client.post(
        url,
        headers=headers,
        json={"action": "apply", "expected_version": 1, "reason": "accepted"},
    )
    conflict = client.post(
        url,
        headers=headers,
        json={"action": "reject", "expected_version": 1, "reason": "different"},
    )
    stale = client.post(
        url,
        headers={**_headers(), "Idempotency-Key": "apply-suggestion-stale"},
        json={"action": "reject", "expected_version": 1},
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["receipt_id"] == first.json()["data"]["receipt_id"]
    assert replay.json()["data"]["idempotent_replay"] is True
    assert replay.json()["meta"]["idempotent_replay"] is True
    assert conflict.status_code == 409
    assert stale.status_code == 409
    audits = store.list_audit_events(
        tenant_id="tenant-a", owner_user_id="alice", suggestion_id="suggestion-001"
    )
    assert len(audits) == 1
    assert audits[0]["receipt_id"] == first.json()["data"]["receipt_id"]

    restarted_store = PerformanceSuggestionStore(str(db_path))
    restarted_client = TestClient(_app(restarted_store, _JourneyStore(_events())))
    receipt_id = first.json()["data"]["receipt_id"]
    readback = restarted_client.get(
        f"/bff/agora/performance/action-receipts/{receipt_id}", headers=_headers()
    )
    projection = restarted_client.get(_performance_url(), headers=_headers())
    cross_user = restarted_client.get(
        f"/bff/agora/performance/action-receipts/{receipt_id}",
        headers=_headers(user="bob"),
    )
    assert readback.status_code == 200, readback.text
    assert readback.json()["data"]["status"] == "applied"
    assert projection.json()["data"]["adjustment_suggestions"]["items"][0][
        "status"
    ] == "applied"
    assert cross_user.status_code == 404


def test_openapi_publishes_typed_performance_contracts(tmp_path: Path) -> None:
    app = _app(
        PerformanceSuggestionStore(str(tmp_path / "performance.db")), _JourneyStore()
    )
    schema = app.openapi()
    read_path = "/bff/agora/trading-room/strategies/{strategy_id}/performance"
    action_path = (
        "/bff/agora/trading-room/strategies/{strategy_id}/performance/"
        "suggestions/{suggestion_id}/actions"
    )
    receipt_path = "/bff/agora/performance/action-receipts/{receipt_id}"
    assert schema["paths"][read_path]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/PerformanceProjectionEnvelope")
    assert schema["paths"][action_path]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/SuggestionActionEnvelope")
    assert schema["paths"][receipt_path]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/SuggestionActionEnvelope")


def test_static_v1_10_bundle_hashes_definitions_and_routes_are_locked() -> None:
    schema = json.loads(STATIC_SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(STATIC_MANIFEST_PATH.read_text(encoding="utf-8"))
    bundle = json.loads(STATIC_BUNDLE_PATH.read_text(encoding="utf-8"))
    parent_path = AGORA_SPECS / "bundle_index.v1_9.json"

    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_9.json",
        "bundle_version": "1.9",
        "bundle_index_sha256": sha256(parent_path),
    }
    expected_files = {
        "specs/agora/v11/performance_truth.schema.json": sha256(STATIC_SCHEMA_PATH),
        "specs/agora/v11/capability_manifest_v1_10.json": sha256(
            STATIC_MANIFEST_PATH
        ),
    }
    for relative_path, expected_hash in expected_files.items():
        assert bundle["files"][relative_path] == expected_hash
    assert bundle["openapi"]["sha256"] == sha256(STATIC_OPENAPI_PATH)
    assert set(schema["definitions"]) <= set(bundle["required_definition_checksums"])
    assert set(schema["definitions"]) <= set(manifest["required_definition_checksums"])
    for name in schema["definitions"]:
        expected = bundle["required_definition_checksums"][name]
        canonical = json.dumps(
            schema["definitions"][name], sort_keys=True, separators=(",", ":")
        )
        assert hashlib.sha256(canonical.encode()).hexdigest() == expected, name

    openapi = yaml.safe_load(STATIC_OPENAPI_PATH.read_text(encoding="utf-8"))
    openapi_routes = {
        f"{method.upper()} {path}"
        for path, item in openapi["paths"].items()
        for method in item
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    manifest_routes = {
        route
        for capability in manifest["capabilities"]
        for route in capability["routes"]
    }
    assert openapi_routes == manifest_routes
    assert openapi["info"]["x-implementation-status"] == "implemented"


def test_static_schema_accepts_live_projection_and_action_receipt(tmp_path: Path) -> None:
    schema = json.loads(STATIC_SCHEMA_PATH.read_text(encoding="utf-8"))
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    store.upsert_suggestion(
        tenant_id="tenant-a", owner_user_id="alice", suggestion=_suggestion()
    )
    client = TestClient(_app(store, _JourneyStore(_events())))
    projection = client.get(_performance_url(), headers=_headers()).json()
    action = client.post(
        "/bff/agora/trading-room/strategies/strategy-alpha/performance/"
        "suggestions/suggestion-001/actions",
        headers={**_headers(), "Idempotency-Key": "static-schema-001"},
        json={"action": "return_to_workshop", "expected_version": 1},
    ).json()

    projection_validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )
    action_schema = {
        "$schema": schema["$schema"],
        "$ref": "#/definitions/SuggestionActionEnvelope",
        "definitions": schema["definitions"],
    }
    action_validator = Draft202012Validator(
        action_schema,
        format_checker=FormatChecker(),
    )
    assert list(projection_validator.iter_errors(projection)) == []
    assert list(action_validator.iter_errors(action)) == []


def test_performance_attribution_empty_sources_typed_unavailable(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    client = TestClient(_app(store, _JourneyStore()))
    response = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy",
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    meta = body["meta"]
    assert data["id"] == "agora-trading-room-performance-attribution-by-strategy"
    assert data["dimensions"] == ["strategy"]
    assert data["items"] == []
    assert data["summary"]["row_count"] == 0
    assert data["summary"]["returned_row_count"] == 0
    assert data["summary"]["runtime_count"] == 0
    assert data["summary"]["basis"] == "owner_scoped_strategy_attribution"
    assert meta["no_order_route_proof"] == "agora_performance_read_only"
    assert meta["scope"] == {"tenant_id": "tenant-a", "owner_user_id": "alice"}
    assert meta["surfaces"]["strategy_directory"] == {
        "status": "unavailable",
        "as_of": None,
        "source_ids": [],
        "reason": "no_strategies_found",
    }
    assert meta["surfaces"]["telemetry"] == {
        "status": "unavailable",
        "as_of": None,
        "source_ids": [],
        "reason": "no_current_rows",
    }
    assert meta["surfaces"]["trade_journeys"] == {
        "status": "unavailable",
        "as_of": None,
        "source_ids": [],
        "reason": "no_journey_records",
    }


def test_performance_attribution_owner_isolation_alice_cannot_observe_bob(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    workshop = _MockWorkshopStore(
        sessions=[
            {
                "workshop_id": "ws-alice-1",
                "strategy_id": "alice-strategy-alpha",
                "title": "Alice Alpha Strategy",
                "user_id": "alice",
                "tenant_id": "tenant-a",
            },
            {
                "workshop_id": "ws-bob-1",
                "strategy_id": "bob-secret-strategy",
                "title": "Bob Top Secret",
                "user_id": "bob",
                "tenant_id": "tenant-a",
            },
        ]
    )
    alice_events = _events(user_id="alice", tenant_id="tenant-a")
    for ev in alice_events:
        ev["strategy_id"] = "alice-strategy-alpha"
    bob_events = _events(user_id="bob", tenant_id="tenant-a")
    for i, ev in enumerate(bob_events):
        ev["strategy_id"] = "bob-secret-strategy"
        ev["journey_id"] = "journey-bob-001"
        ev["event_id"] = f"event-bob-{i:03d}"
        ev["decision_id"] = "decision-bob-001"
        ev["order_id"] = "order-bob-001"
        ev["fill_id"] = "fill-bob-001"
        ev["reconciliation_id"] = "reconciliation-bob-001"

    client = TestClient(_app(store, _JourneyStore(alice_events + bob_events), workshop_store=workshop))

    # 1. Alice query: sees only Alice's strategy
    alice_resp = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy",
        headers=_headers(user="alice"),
    )
    assert alice_resp.status_code == 200, alice_resp.text
    alice_data = alice_resp.json()["data"]
    alice_meta = alice_resp.json()["meta"]
    assert len(alice_data["items"]) == 1
    assert alice_data["items"][0]["dimension_key"] == "alice-strategy-alpha"
    assert alice_data["items"][0]["label"] == "Alice Alpha Strategy"
    assert alice_data["summary"]["row_count"] == 1
    assert alice_meta["scope"] == {"tenant_id": "tenant-a", "owner_user_id": "alice"}
    assert alice_meta["surfaces"]["strategy_directory"]["source_ids"] == ["alice-strategy-alpha"]
    assert "bob-secret-strategy" not in str(alice_resp.json())

    # 2. Alice attempts to probe Bob's strategy_id: returns empty/total 0 (no existence leakage)
    probe_resp = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?strategy_id=bob-secret-strategy",
        headers=_headers(user="alice"),
    )
    assert probe_resp.status_code == 200, probe_resp.text
    probe_data = probe_resp.json()["data"]
    assert len(probe_data["items"]) == 0
    assert probe_data["summary"]["row_count"] == 0

    # 3. Bob query: sees only Bob's strategy
    bob_resp = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy",
        headers=_headers(user="bob"),
    )
    assert bob_resp.status_code == 200, bob_resp.text
    bob_data = bob_resp.json()["data"]
    bob_meta = bob_resp.json()["meta"]
    assert len(bob_data["items"]) == 1
    assert bob_data["items"][0]["dimension_key"] == "bob-secret-strategy"
    assert bob_data["items"][0]["label"] == "Bob Top Secret"
    assert bob_data["summary"]["row_count"] == 1
    assert bob_meta["scope"] == {"tenant_id": "tenant-a", "owner_user_id": "bob"}
    assert bob_meta["surfaces"]["strategy_directory"]["source_ids"] == ["bob-secret-strategy"]
    assert "alice-strategy-alpha" not in str(bob_resp.json())


def test_performance_attribution_metrics_and_period_filtering(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    events = [
        {
            "journey_id": "j-1",
            "tenant_id": "tenant-a",
            "environment": "paper",
            "source": "canonical-lifecycle-projector",
            "user_id": "alice",
            "strategy_id": "strat-1",
            "event_id": "ev-1",
            "occurred_at": "2026-07-22T17:00:00Z",
            "stage": "trade_decision",
            "stage_status": "succeeded",
            "metrics": {
                "pnl": 1250.50,
                "unrealized_pnl": 250.50,
                "realized_pnl": 1000.0,
                "notional": 50000.0,
                "exposure": 15000.0,
                "drawdown": -0.045,
                "fill_rate": 0.98,
                "slippage_bps": 2.5,
            },
        },
        {
            "journey_id": "j-2",
            "tenant_id": "tenant-a",
            "environment": "paper",
            "source": "canonical-lifecycle-projector",
            "user_id": "alice",
            "strategy_id": "strat-2",
            "event_id": "ev-2",
            "occurred_at": "2026-07-22T17:15:00Z",
            "stage": "trade_decision",
            "stage_status": "succeeded",
            "metrics": {
                "pnl": 750.25,
                "unrealized_pnl": 150.25,
                "realized_pnl": 600.0,
                "notional": 25000.0,
                "exposure": 8000.0,
                "drawdown": -0.02,
                "fill_rate": 0.95,
                "slippage_bps": 3.0,
            },
        },
    ]

    client = TestClient(_app(store, _JourneyStore(events)))
    response = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?period=latest",
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["items"]) == 2
    assert data["items"][0]["dimension_key"] == "strat-1"
    assert data["items"][0]["rank"] == 1
    assert data["items"][0]["total_pnl"] == 1250.50
    assert data["items"][0]["metrics"]["runtime_count"] == 0
    assert data["items"][0]["metrics"]["data_confidence"] == "formal"
    assert data["items"][0]["links"] == {
        "strategy": "/agora/strategies/strat-1",
        "performance": "/bff/agora/trading-room/strategies/strat-1/performance",
    }
    assert data["items"][1]["dimension_key"] == "strat-2"
    assert data["items"][1]["rank"] == 2
    assert data["items"][1]["total_pnl"] == 750.25

    summary = data["summary"]
    assert summary["row_count"] == 2
    assert summary["total_pnl"] == 2000.75
    assert summary["total_notional"] == 75000.0


def test_performance_attribution_pagination(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    workshop = _MockWorkshopStore(
        sessions=[
            {"workshop_id": f"ws-{i}", "strategy_id": f"strat-{i:02d}", "title": f"Strategy {i}", "user_id": "alice", "tenant_id": "tenant-a"}
            for i in range(1, 6)
        ]
    )
    client = TestClient(_app(store, _JourneyStore(), workshop_store=workshop))

    page1 = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?pageSize=2",
        headers=_headers(),
    )
    assert page1.status_code == 200, page1.text
    data1 = page1.json()["data"]
    assert len(data1["items"]) == 2
    assert data1["page_info"]["total"] == 5
    assert data1["page_info"]["next_page_token"] == "offset-2"

    page2 = client.get(
        f"/bff/agora/trading-room/performance-attribution/by-strategy?pageSize=2&pageToken={data1['page_info']['next_page_token']}",
        headers=_headers(),
    )
    assert page2.status_code == 200, page2.text
    data2 = page2.json()["data"]
    assert len(data2["items"]) == 2
    assert data2["items"][0]["dimension_key"] != data1["items"][0]["dimension_key"]
    assert data2["page_info"]["next_page_token"] == "offset-4"


def test_openapi_contains_attribution_contract(tmp_path: Path) -> None:
    app = _app(PerformanceSuggestionStore(str(tmp_path / "performance.db")), _JourneyStore())
    schema = app.openapi()
    attr_path = "/bff/agora/trading-room/performance-attribution/by-strategy"
    assert attr_path in schema["paths"]
    assert schema["paths"][attr_path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/TradingRoomPerformanceAttributionEnvelope")

