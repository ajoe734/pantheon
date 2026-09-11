from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from services.trade_journey.materializer import JourneyMaterializer, JourneyProjection

from ...trade_journey_projection_store import (
    MAX_PAGE_SIZE,
    ProjectionPage,
    ProjectionReadUnavailable,
    TimelinePage,
    UnavailableProjectionReader,
)
from .journeys import scan_owner_journeys
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
ATTRIBUTION_URL = "/bff/agora/trading-room/performance-attribution/by-strategy"


class _ProjectionReader:
    """In-memory stand-in for the Postgres ``TradeJourneyProjectionStore``.

    Mirrors the reader contract the performance service depends on: reads are
    tenant/environment scoped, ``strategy_id``/``date_from`` filters narrow
    the page, page rows carry the snapshot but an *empty* timeline,
    ``get_journey`` keeps only the latest event per stage (``DISTINCT ON
    (stage_name)``), and only ``page_timeline`` yields the full durable stage
    history with cursor/total semantics.  Backed by the canonical materializer
    so projections are shaped exactly like the projector output, never by a
    performance-owned store.
    """

    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        *,
        page_limit: int = MAX_PAGE_SIZE,
        generation: int = 42,
    ) -> None:
        self._materializer = JourneyMaterializer()
        self._materializer.rebuild(events or [])
        self.page_limit = page_limit
        self.generation = generation
        self.page_calls: list[dict[str, Any]] = []
        self.journey_calls: list[str] = []
        self.timeline_calls: list[dict[str, Any]] = []

    def _scoped(self, tenant_id: str, environment: str, filters: dict[str, Any]) -> list[JourneyProjection]:
        rows = [
            projection
            for projection in self._materializer.projections
            if projection.tenant_id == tenant_id and projection.environment == environment
        ]
        strategy_id = filters.get("strategy_id")
        if strategy_id:
            rows = [
                projection
                for projection in rows
                if strategy_id in ((projection.snapshot.get("identifiers") or {}).get("strategy_id") or [])
            ]
        date_from = filters.get("date_from")
        if date_from:
            floor = datetime.fromisoformat(str(date_from).replace("Z", "+00:00"))
            rows = [
                projection
                for projection in rows
                if datetime.fromisoformat(str(projection.snapshot["updated_at"]).replace("Z", "+00:00")) >= floor
            ]
        rows.sort(key=lambda item: (str(item.snapshot.get("updated_at")), item.journey_id), reverse=True)
        return rows

    def page_journeys(
        self,
        *,
        tenant_id: str,
        environment: str,
        filters: Optional[dict[str, Any]] = None,
        sort: str = "updated_at_desc",
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> ProjectionPage:
        assert tenant_id and environment, "reader reads must be tenant/environment scoped"
        assert 1 <= page_size <= MAX_PAGE_SIZE
        active = {key: value for key, value in dict(filters or {}).items() if value not in (None, "")}
        self.page_calls.append(
            {
                "tenant_id": tenant_id,
                "environment": environment,
                "filters": active,
                "sort": sort,
                "page_size": page_size,
                "page_token": page_token,
            }
        )
        rows = self._scoped(tenant_id, environment, active)
        offset = int(page_token or 0)
        size = min(page_size, self.page_limit)
        page = rows[offset : offset + size]
        items = [
            JourneyProjection(
                projection.journey_id,
                projection.tenant_id,
                projection.environment,
                [],
                dict(projection.snapshot),
                [],
                list(projection.diagnostics),
            )
            for projection in page
        ]
        next_token = str(offset + size) if offset + size < len(rows) else None
        return ProjectionPage(items=items, next_page_token=next_token, total=len(rows))

    def get_journey(self, *, tenant_id: str, environment: str, journey_id: str) -> JourneyProjection | None:
        """Latest event per stage only, like the Postgres ``DISTINCT ON (stage_name)`` read."""
        self.journey_calls.append(journey_id)
        projection = self._materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
        if projection is None:
            return None
        latest_per_stage: dict[str, dict[str, Any]] = {}
        for event in projection.timeline:
            latest_per_stage[str(event.get("stage"))] = event
        return JourneyProjection(
            projection.journey_id,
            projection.tenant_id,
            projection.environment,
            sorted(latest_per_stage.values(), key=lambda item: str(item.get("stage"))),
            dict(projection.snapshot),
            list(projection.graph_edges),
            list(projection.diagnostics),
        )

    def page_timeline(
        self,
        *,
        tenant_id: str,
        environment: str,
        journey_id: str,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> TimelinePage:
        """Full durable stage history, oldest first, offset cursor, real total."""
        assert tenant_id and environment and journey_id
        assert 1 <= page_size <= MAX_PAGE_SIZE
        self.timeline_calls.append({"journey_id": journey_id, "page_size": page_size, "page_token": page_token})
        projection = self._materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
        events = list(projection.timeline) if projection is not None else []
        offset = int(page_token or 0)
        size = min(page_size, self.page_limit)
        page = [dict(event) for event in events[offset : offset + size]]
        next_token = str(offset + size) if offset + size < len(events) else None
        return TimelinePage(items=page, next_page_token=next_token, total=len(events))

    def controller_freshness(self, *, tenant_id: str, environment: str) -> dict[str, Any]:
        return {
            "controller_id": "canonical-lifecycle-projector",
            "generation": self.generation,
            "status": "ready",
            "source_high_watermark": 7,
            "last_successful_publish_at": NOW,
        }


class _FailingReader(_ProjectionReader):
    """Reader whose upstream fails part-way through a walk."""

    def __init__(self, events: list[dict[str, Any]], *, fail_on_journey: bool) -> None:
        super().__init__(events)
        self.fail_on_journey = fail_on_journey

    def page_journeys(self, **kwargs: Any) -> ProjectionPage:
        if not self.fail_on_journey:
            raise ProjectionReadUnavailable("Postgres projection reader is unavailable")
        return super().page_journeys(**kwargs)

    def page_timeline(self, **kwargs: Any) -> TimelinePage:
        raise ProjectionReadUnavailable("Postgres projection reader is unavailable")


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
    journey_store: Any,
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
        _app(PerformanceSuggestionStore(str(tmp_path / "performance.db")), _ProjectionReader())
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
    client = TestClient(_app(store, _ProjectionReader(_events())))

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
    client = TestClient(_app(store, _ProjectionReader(_events())))

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
    client = TestClient(_app(store, _ProjectionReader(_events())))
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
    client = TestClient(_app(store, _ProjectionReader(_events())))
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
    client = TestClient(_app(store, _ProjectionReader(events)))
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
    client = TestClient(_app(store, _ProjectionReader(_events())))
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
    client = TestClient(_app(store, _ProjectionReader(_events())))
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
    restarted_client = TestClient(_app(restarted_store, _ProjectionReader(_events())))
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
        PerformanceSuggestionStore(str(tmp_path / "performance.db")), _ProjectionReader()
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
    client = TestClient(_app(store, _ProjectionReader(_events())))
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


def test_performance_attribution_real_empty_scope_is_available_with_no_rows(tmp_path: Path) -> None:
    """A reachable reader with zero owner journeys is an empty result, not an outage."""
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    reader = _ProjectionReader()
    client = TestClient(_app(store, reader))
    response = client.get(ATTRIBUTION_URL, headers=_headers())
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
    assert data["summary"]["total_pnl"] is None
    assert data["summary"]["basis"] == "owner_scoped_strategy_attribution"
    assert meta["no_order_route_proof"] == "agora_performance_read_only"
    assert meta["scope"] == {
        "tenant_id": "tenant-a",
        "owner_user_id": "alice",
        "environment": "paper",
    }
    assert meta["availability"] == "available"
    assert meta["unavailable_sources"] == ["strategy_directory", "telemetry"]
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
        "status": "available",
        "as_of": NOW,
        "source_ids": [],
        "reason": None,
    }
    # The read was really made against the configured reader, tenant/environment scoped.
    assert reader.page_calls == [
        {
            "tenant_id": "tenant-a",
            "environment": "paper",
            "filters": {},
            "sort": "updated_at_desc",
            "page_size": MAX_PAGE_SIZE,
            "page_token": None,
        }
    ]


@pytest.mark.parametrize(
    ("reader", "expected_reason"),
    [
        (None, "projection_reader_not_configured"),
        (
            UnavailableProjectionReader("PANTHEON_BFF_TRADE_JOURNEY_PROJECTION_DSN is required"),
            "projection_reader_unavailable:PANTHEON_BFF_TRADE_JOURNEY_PROJECTION_DSN is required",
        ),
        (
            _FailingReader(_events(), fail_on_journey=False),
            "projection_reader_unavailable:Postgres projection reader is unavailable",
        ),
        (
            _FailingReader(_events(), fail_on_journey=True),
            "projection_reader_unavailable:Postgres projection reader is unavailable",
        ),
    ],
    ids=["not_configured", "unavailable_sentinel", "page_read_fails", "timeline_read_fails"],
)
def test_performance_attribution_reader_outage_is_typed_unavailable_not_empty(
    tmp_path: Path, reader: Any, expected_reason: str
) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    workshop = _MockWorkshopStore(
        sessions=[
            {
                "workshop_id": "ws-alice-1",
                "strategy_id": "alice-strategy-alpha",
                "title": "Alice Alpha Strategy",
                "user_id": "alice",
                "tenant_id": "tenant-a",
            }
        ]
    )
    client = TestClient(_app(store, reader, workshop_store=workshop))
    response = client.get(ATTRIBUTION_URL, headers=_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    meta = body["meta"]
    assert meta["availability"] == "unavailable"
    assert meta["surfaces"]["trade_journeys"]["status"] == "unavailable"
    assert meta["surfaces"]["trade_journeys"]["reason"] == expected_reason
    assert meta["surfaces"]["trade_journeys"]["as_of"] is None
    assert meta["surfaces"]["telemetry"] == {
        "status": "unavailable",
        "as_of": None,
        "source_ids": [],
        "reason": "trade_journeys_unavailable",
    }
    assert meta["unavailable_sources"] == ["telemetry", "trade_journeys"]
    # Directory strategies are still listed, but with no fabricated performance.
    row = body["data"]["items"][0]
    assert row["dimension_key"] == "alice-strategy-alpha"
    assert row["data_confidence"] == "unavailable"
    assert row["source_status"] == "unavailable"
    assert row["total_pnl"] is None
    assert row["metrics"]["total_pnl"] is None
    assert row["metrics"]["total_trades"] == 0
    assert body["data"]["summary"]["total_pnl"] is None


def test_performance_attribution_is_tenant_and_environment_scoped(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    paper = _events(user_id="alice", tenant_id="tenant-a")
    live = _events(user_id="alice", tenant_id="tenant-a")
    for index, event in enumerate(live):
        event["environment"] = "live"
        event["journey_id"] = "journey-live-001"
        event["event_id"] = f"event-live-{index:03d}"
        event["strategy_id"] = "strategy-live"
    other_tenant = _events(user_id="alice", tenant_id="tenant-b")
    for index, event in enumerate(other_tenant):
        event["journey_id"] = "journey-b-001"
        event["event_id"] = f"event-b-{index:03d}"
        event["strategy_id"] = "strategy-tenant-b"
    reader = _ProjectionReader(paper + live + other_tenant)
    client = TestClient(_app(store, reader))

    default_paper = client.get(ATTRIBUTION_URL, headers=_headers())
    assert default_paper.status_code == 200, default_paper.text
    assert [row["dimension_key"] for row in default_paper.json()["data"]["items"]] == ["strategy-alpha"]
    assert default_paper.json()["meta"]["surfaces"]["trade_journeys"]["source_ids"] == ["journey-001"]
    assert "strategy-live" not in default_paper.text
    assert "strategy-tenant-b" not in default_paper.text

    operator_live = client.get(f"{ATTRIBUTION_URL}?environment=live", headers=_headers())
    assert operator_live.status_code == 200, operator_live.text
    assert [row["dimension_key"] for row in operator_live.json()["data"]["items"]] == ["strategy-live"]
    assert operator_live.json()["meta"]["scope"]["environment"] == "live"

    viewer_live = client.get(f"{ATTRIBUTION_URL}?environment=live", headers=_headers(role="viewer"))
    assert viewer_live.status_code == 403

    tenant_b = client.get(ATTRIBUTION_URL, headers=_headers(tenant="tenant-b"))
    assert tenant_b.status_code == 200, tenant_b.text
    assert [row["dimension_key"] for row in tenant_b.json()["data"]["items"]] == ["strategy-tenant-b"]
    assert "strategy-alpha" not in tenant_b.text
    assert {call["tenant_id"] for call in reader.page_calls} == {"tenant-a", "tenant-b"}
    assert {call["environment"] for call in reader.page_calls} == {"paper", "live"}


def test_performance_attribution_walks_reader_pages_and_scopes_owner_via_stage_reads(
    tmp_path: Path,
) -> None:
    """Page rows carry no timeline; owner scoping must come from ``get_journey``."""
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    events: list[dict[str, Any]] = []
    for index in range(5):
        owner = "alice" if index % 2 == 0 else "bob"
        for event in _events(user_id=owner, tenant_id="tenant-a"):
            events.append(
                {
                    **event,
                    "journey_id": f"journey-{index:03d}",
                    "event_id": f"{event['event_id']}-{index:03d}",
                    "strategy_id": f"strategy-{index:03d}",
                    "fill_id": f"fill-{index:03d}",
                    "occurred_at": event["occurred_at"].replace("T17", f"T{10 + index:02d}"),
                }
            )
    reader = _ProjectionReader(events, page_limit=2)
    client = TestClient(_app(store, reader))

    response = client.get(ATTRIBUTION_URL, headers=_headers(user="alice"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["dimension_key"] for row in body["data"]["items"]] == [
        "strategy-000",
        "strategy-002",
        "strategy-004",
    ]
    assert "bob" not in response.text
    assert body["meta"]["availability"] == "available"
    assert body["meta"]["surfaces"]["trade_journeys"]["source_ids"] == [
        "journey-000",
        "journey-002",
        "journey-004",
    ]
    # Three reader pages of two, cursor threaded between calls; every row needed
    # its full durable history (two timeline pages of two each, then the tail).
    assert [call["page_token"] for call in reader.page_calls] == [None, "2", "4"]
    assert reader.journey_calls == []
    assert sorted({call["journey_id"] for call in reader.timeline_calls}) == [
        f"journey-{index:03d}" for index in range(5)
    ]
    assert [call["page_token"] for call in reader.timeline_calls if call["journey_id"] == "journey-000"] == [
        None,
        "2",
    ]


def test_performance_attribution_strategy_filter_and_period_narrow_the_reader_query(
    tmp_path: Path,
) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    recent = _events(user_id="alice", tenant_id="tenant-a")
    stale = _events(user_id="alice", tenant_id="tenant-a")
    for index, event in enumerate(stale):
        event["journey_id"] = "journey-stale"
        event["event_id"] = f"event-stale-{index:03d}"
        event["strategy_id"] = "strategy-stale"
        event["occurred_at"] = event["occurred_at"].replace("2026-07-22", "2026-06-01")
    reader = _ProjectionReader(recent + stale)
    client = TestClient(_app(store, reader))

    filtered = client.get(f"{ATTRIBUTION_URL}?strategy_id=strategy-alpha", headers=_headers())
    assert filtered.status_code == 200, filtered.text
    assert [row["dimension_key"] for row in filtered.json()["data"]["items"]] == ["strategy-alpha"]
    assert reader.page_calls[-1]["filters"] == {"strategy_id": "strategy-alpha"}

    seven_days = client.get(f"{ATTRIBUTION_URL}?period=7d", headers=_headers())
    assert seven_days.status_code == 200, seven_days.text
    assert [row["dimension_key"] for row in seven_days.json()["data"]["items"]] == ["strategy-alpha"]
    assert reader.page_calls[-1]["filters"] == {"date_from": "2026-07-15T18:00:00Z"}
    assert seven_days.json()["meta"]["surfaces"]["trade_journeys"]["source_ids"] == ["journey-001"]

    everything = client.get(f"{ATTRIBUTION_URL}?period=all", headers=_headers())
    assert [row["dimension_key"] for row in everything.json()["data"]["items"]] == [
        "strategy-alpha",
        "strategy-stale",
    ]


def test_scan_owner_journeys_reports_truncation_as_partial() -> None:
    events: list[dict[str, Any]] = []
    for index in range(4):
        for event in _events(user_id="alice", tenant_id="tenant-a"):
            events.append(
                {
                    **event,
                    "journey_id": f"journey-{index:03d}",
                    "event_id": f"{event['event_id']}-{index:03d}",
                    "strategy_id": f"strategy-{index:03d}",
                }
            )
    reader = _ProjectionReader(events, page_limit=2)
    scan = scan_owner_journeys(
        reader,
        tenant_id="tenant-a",
        environment="paper",
        owner_user_id="alice",
        period="latest",
        now=datetime(2026, 7, 22, 18, tzinfo=timezone.utc),
        max_journeys=2,
    )
    assert scan.status == "partial"
    assert scan.reason == "journey_scan_truncated"
    assert scan.scanned == 2
    assert scan.scope_total == 4
    assert len(scan.projections) == 2
    assert scan.controller["generation"] == 42


def test_scan_owner_journeys_drops_journeys_whose_history_exceeds_the_timeline_bound() -> None:
    """A journey that cannot be read completely is never scoped from a prefix."""
    reader = _ProjectionReader(_events(), page_limit=2)
    scan = scan_owner_journeys(
        reader,
        tenant_id="tenant-a",
        environment="paper",
        owner_user_id="alice",
        period="latest",
        now=datetime(2026, 7, 22, 18, tzinfo=timezone.utc),
        max_timeline_events=2,
    )
    assert scan.status == "partial"
    assert scan.reason == "journey_timeline_truncated"
    assert scan.projections == []


def test_performance_attribution_counts_two_fills_in_one_stage_exactly_once_each(
    tmp_path: Path,
) -> None:
    """``get_journey`` keeps one event per stage; the durable history must be used."""
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    events = _events(user_id="alice", tenant_id="tenant-a")
    for event in events:
        event.pop("fill_id")
    events.append(
        {
            **events[2],
            "event_id": "event-003-a",
            "occurred_at": "2026-07-22T17:20:00Z",
            "fill_id": "fill-001",
        }
    )
    events.append(
        {
            **events[2],
            "event_id": "event-003-b",
            "occurred_at": "2026-07-22T17:21:00Z",
            "fill_id": "fill-002",
        }
    )
    events.append(
        {
            **events[2],
            "event_id": "event-003-c",
            "occurred_at": "2026-07-22T17:22:00Z",
            "fill_id": "fill-002",
        }
    )
    reader = _ProjectionReader(events)
    # Latest-per-stage read would see only fill-002.
    assert {
        event.get("fill_id")
        for event in reader.get_journey(
            tenant_id="tenant-a", environment="paper", journey_id="journey-001"
        ).timeline
    } == {None, "fill-002"}

    client = TestClient(_app(store, reader))
    response = client.get(ATTRIBUTION_URL, headers=_headers(user="alice"))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["items"]) == 1
    assert data["items"][0]["metrics"]["total_trades"] == 2
    assert data["summary"]["total_trades"] == 2


def test_performance_attribution_owner_conflict_on_earlier_event_stays_invisible(
    tmp_path: Path,
) -> None:
    """A journey Bob touched earlier is not Alice's, even if the latest events look like hers."""
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    events = _events(user_id="alice", tenant_id="tenant-a")
    events.insert(
        2,
        {
            **events[2],
            "event_id": "event-003-bob",
            "occurred_at": "2026-07-22T17:19:00Z",
            "user_id": "bob",
            "fill_id": "fill-bob",
        },
    )
    reader = _ProjectionReader(events)
    latest_per_stage = reader.get_journey(
        tenant_id="tenant-a", environment="paper", journey_id="journey-001"
    )
    assert {event["user_id"] for event in latest_per_stage.timeline} == {"alice"}

    client = TestClient(_app(store, reader))
    for user in ("alice", "bob"):
        response = client.get(ATTRIBUTION_URL, headers=_headers(user=user))
        assert response.status_code == 200, response.text
        assert response.json()["data"]["items"] == []
        assert response.json()["meta"]["surfaces"]["trade_journeys"]["source_ids"] == []
        assert response.json()["meta"]["availability"] == "available"
        assert "fill-bob" not in response.text


def test_performance_attribution_never_exposes_other_owners_counts_or_ids(
    tmp_path: Path,
) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    alice_events = _events(user_id="alice", tenant_id="tenant-a")
    baseline = TestClient(_app(store, _ProjectionReader(alice_events)))
    alone = baseline.get(ATTRIBUTION_URL, headers=_headers(user="alice")).json()

    bob_events: list[dict[str, Any]] = []
    for index in range(3):
        for event in _events(user_id="bob", tenant_id="tenant-a"):
            bob_events.append(
                {
                    **event,
                    "journey_id": f"journey-bob-{index:03d}",
                    "event_id": f"{event['event_id']}-bob-{index:03d}",
                    "strategy_id": f"bob-strategy-{index:03d}",
                    "fill_id": f"fill-bob-{index:03d}",
                }
            )
    shared = TestClient(_app(store, _ProjectionReader(alice_events + bob_events)))
    with_bob = shared.get(ATTRIBUTION_URL, headers=_headers(user="alice"))
    assert with_bob.status_code == 200, with_bob.text
    assert "bob" not in with_bob.text
    # Byte-for-byte identical: Bob's rows change nothing Alice can observe.
    assert with_bob.json() == alone
    surface = with_bob.json()["meta"]["surfaces"]["trade_journeys"]
    assert set(surface) == {"status", "as_of", "source_ids", "reason"}


def test_performance_attribution_truncated_owner_query_stays_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from . import journeys as journeys_module

    monkeypatch.setattr(journeys_module, "MAX_JOURNEY_SCAN", 2)
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    events: list[dict[str, Any]] = []
    for index in range(4):
        for event in _events(user_id="alice", tenant_id="tenant-a"):
            events.append(
                {
                    **event,
                    "journey_id": f"journey-{index:03d}",
                    "event_id": f"{event['event_id']}-{index:03d}",
                    "strategy_id": f"strategy-{index:03d}",
                    "occurred_at": event["occurred_at"].replace("T17", f"T{10 + index:02d}"),
                }
            )
    client = TestClient(_app(store, _ProjectionReader(events, page_limit=2)))
    response = client.get(ATTRIBUTION_URL, headers=_headers(user="alice"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["availability"] == "partial"
    assert body["meta"]["surfaces"]["trade_journeys"]["status"] == "partial"
    assert body["meta"]["surfaces"]["trade_journeys"]["reason"] == "journey_scan_truncated"
    assert body["meta"]["surfaces"]["telemetry"]["status"] == "unavailable"
    assert [row["dimension_key"] for row in body["data"]["items"]] == ["strategy-002", "strategy-003"]


def test_performance_attribution_does_not_invent_fill_rate_or_slippage(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    measured = [
        {
            **event,
            "journey_id": "journey-measured",
            "event_id": f"{event['event_id']}-measured",
            "strategy_id": "strategy-measured",
            "fill_id": "fill-measured",
        }
        for event in _events(user_id="alice", tenant_id="tenant-a")
    ]
    measured[2]["metrics"] = {"pnl": 10.0, "fill_rate": 0.5, "slippage_bps": 4.0}
    client = TestClient(_app(store, _ProjectionReader(_events() + measured)))
    response = client.get(ATTRIBUTION_URL, headers=_headers(user="alice"))
    assert response.status_code == 200, response.text
    rows = {row["dimension_key"]: row for row in response.json()["data"]["items"]}
    unmeasured = rows["strategy-alpha"]["metrics"]
    assert unmeasured["total_trades"] == 1
    assert unmeasured["average_fill_rate"] is None
    assert unmeasured["average_slippage_bps"] is None
    assert rows["strategy-measured"]["metrics"]["average_fill_rate"] == 0.5
    assert rows["strategy-measured"]["metrics"]["average_slippage_bps"] == 4.0
    summary = response.json()["data"]["summary"]
    assert summary["average_fill_rate"] == 0.5
    assert summary["average_slippage_bps"] == 4.0


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

    client = TestClient(_app(store, _ProjectionReader(alice_events + bob_events), workshop_store=workshop))

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
    assert alice_meta["scope"] == {"tenant_id": "tenant-a", "owner_user_id": "alice", "environment": "paper"}
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
    assert bob_meta["scope"] == {"tenant_id": "tenant-a", "owner_user_id": "bob", "environment": "paper"}
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

    client = TestClient(_app(store, _ProjectionReader(events)))
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
    client = TestClient(_app(store, _ProjectionReader(), workshop_store=workshop))

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
    app = _app(PerformanceSuggestionStore(str(tmp_path / "performance.db")), _ProjectionReader())
    schema = app.openapi()
    attr_path = "/bff/agora/trading-room/performance-attribution/by-strategy"
    assert attr_path in schema["paths"]
    assert schema["paths"][attr_path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/TradingRoomPerformanceAttributionEnvelope")


def test_performance_attribution_rejects_unsupported_period_and_accepts_valid(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    client = TestClient(_app(store, _ProjectionReader()))

    # Unsupported period must fail closed with 422 Unprocessable Entity
    unsupported_resp = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?period=unsupported",
        headers=_headers(),
    )
    assert unsupported_resp.status_code == 422, unsupported_resp.text

    invalid_resp = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?period=1d",
        headers=_headers(),
    )
    assert invalid_resp.status_code == 422, invalid_resp.text

    # Valid periods defined in OpenAPI v1.13 enum must all be accepted (200)
    for valid_period in ("latest", "7d", "30d", "all"):
        resp = client.get(
            f"/bff/agora/trading-room/performance-attribution/by-strategy?period={valid_period}",
            headers=_headers(),
        )
        assert resp.status_code == 200, f"period={valid_period} failed: {resp.text}"
        assert resp.json()["data"]["period"] == valid_period


def test_performance_attribution_validates_page_size_and_aliases_identically(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    workshop = _MockWorkshopStore(
        sessions=[
            {"workshop_id": f"ws-{i}", "strategy_id": f"strat-{i:02d}", "title": f"Strategy {i}", "user_id": "alice", "tenant_id": "tenant-a"}
            for i in range(1, 4)
        ]
    )
    client = TestClient(_app(store, _ProjectionReader(), workshop_store=workshop))

    # pageSize=0 and page_size=0 must both fail with 422
    assert client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?pageSize=0",
        headers=_headers(),
    ).status_code == 422

    assert client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?page_size=0",
        headers=_headers(),
    ).status_code == 422

    # Negative and out-of-range pageSize / page_size must fail with 422
    assert client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?pageSize=-1",
        headers=_headers(),
    ).status_code == 422

    assert client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?page_size=-1",
        headers=_headers(),
    ).status_code == 422

    assert client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?pageSize=201",
        headers=_headers(),
    ).status_code == 422

    assert client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?page_size=201",
        headers=_headers(),
    ).status_code == 422

    # Valid values accept and behave identically
    resp_camel = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?pageSize=2",
        headers=_headers(),
    )
    resp_snake = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?page_size=2",
        headers=_headers(),
    )
    assert resp_camel.status_code == 200
    assert resp_snake.status_code == 200
    assert resp_camel.json()["data"]["items"] == resp_snake.json()["data"]["items"]
    assert resp_camel.json()["page_info"]["page_size"] == 2
    assert resp_snake.json()["page_info"]["page_size"] == 2


def test_performance_attribution_3_strategies_pagination_advances_and_terminates(tmp_path: Path) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    workshop = _MockWorkshopStore(
        sessions=[
            {"workshop_id": f"ws-{i}", "strategy_id": f"strat-{i:02d}", "title": f"Strategy {i}", "user_id": "alice", "tenant_id": "tenant-a"}
            for i in range(1, 4)
        ]
    )
    client = TestClient(_app(store, _ProjectionReader(), workshop_store=workshop))

    # Page 1 with pageSize=2
    p1 = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?pageSize=2",
        headers=_headers(),
    )
    assert p1.status_code == 200, p1.text
    d1 = p1.json()["data"]
    assert len(d1["items"]) == 2
    assert p1.json()["page_info"]["total"] == 3
    assert p1.json()["page_info"]["next_page_token"] == "offset-2"

    # Page 2 with pageToken=offset-2 (camelCase)
    p2 = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?pageSize=2&pageToken=offset-2",
        headers=_headers(),
    )
    assert p2.status_code == 200, p2.text
    d2 = p2.json()["data"]
    assert len(d2["items"]) == 1
    assert p2.json()["page_info"]["total"] == 3
    assert p2.json()["page_info"]["next_page_token"] is None

    # Page 2 with page_token=offset-2 (snake_case)
    p2_snake = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?page_size=2&page_token=offset-2",
        headers=_headers(),
    )
    assert p2_snake.status_code == 200, p2_snake.text
    d2_snake = p2_snake.json()["data"]
    assert len(d2_snake["items"]) == 1
    assert d2_snake["items"] == d2["items"]
    assert p2_snake.json()["page_info"]["next_page_token"] is None

    # Verify strategyId and strategy_id aliases both work
    strat_filter_camel = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?strategyId=strat-02",
        headers=_headers(),
    )
    strat_filter_snake = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy?strategy_id=strat-02",
        headers=_headers(),
    )
    assert strat_filter_camel.status_code == 200
    assert strat_filter_snake.status_code == 200
    assert len(strat_filter_camel.json()["data"]["items"]) == 1
    assert strat_filter_camel.json()["data"]["items"][0]["dimension_key"] == "strat-02"
    assert strat_filter_camel.json()["data"]["items"] == strat_filter_snake.json()["data"]["items"]


def test_performance_attribution_single_fill_across_multiple_stages_reports_one_trade(
    tmp_path: Path,
) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    # One projection with 4 timeline events spanning decision, submission, fill, reconciliation
    # and a single fill_id="fill-001"
    events = _events(user_id="alice", tenant_id="tenant-a")
    assert len(events) == 4
    for ev in events:
        ev["strategy_id"] = "strategy-alpha"

    client = TestClient(_app(store, _ProjectionReader(events)))
    response = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy",
        headers=_headers(user="alice"),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["items"]) == 1
    # total_trades in row metrics and summary must be 1, not multiplied by 4 timeline events
    assert data["items"][0]["metrics"]["total_trades"] == 1
    assert data["summary"]["total_trades"] == 1


def test_performance_attribution_deduplicates_trade_identifiers_across_multiple_projections(
    tmp_path: Path,
) -> None:
    store = PerformanceSuggestionStore(str(tmp_path / "performance.db"))
    events1 = _events(user_id="alice", tenant_id="tenant-a")
    for ev in events1:
        ev["strategy_id"] = "strategy-alpha"
        ev["journey_id"] = "journey-001"
        ev["fill_id"] = "fill-001"

    events2 = _events(user_id="alice", tenant_id="tenant-a")
    for i, ev in enumerate(events2):
        ev["strategy_id"] = "strategy-alpha"
        ev["journey_id"] = "journey-002"
        ev["event_id"] = f"event-002-{i}"
        ev["fill_id"] = "fill-001"  # Same fill_id across projections

    events3 = _events(user_id="alice", tenant_id="tenant-a")
    for i, ev in enumerate(events3):
        ev["strategy_id"] = "strategy-alpha"
        ev["journey_id"] = "journey-003"
        ev["event_id"] = f"event-003-{i}"
        ev["fill_id"] = "fill-002"  # Distinct fill_id

    client = TestClient(_app(store, _ProjectionReader(events1 + events2 + events3)))
    response = client.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy",
        headers=_headers(user="alice"),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["items"]) == 1
    # 2 distinct fills ("fill-001", "fill-002") across 3 multi-stage projections (12 events) -> total_trades == 2
    assert data["items"][0]["metrics"]["total_trades"] == 2
    assert data["summary"]["total_trades"] == 2




def test_assembled_bff_app_attribution_route_uses_configured_projection_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the live 500: ``main.py`` injected a retired ``EVENT_STORE``.

    The assembled app must hand the performance router the read-surface's
    configured Postgres projection reader.  Without a DSN that reader is the
    fail-closed sentinel (typed unavailable, not a 500); with the read-surface
    override it serves real owner-scoped rows.
    """
    monkeypatch.setenv("PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND", "postgres")
    monkeypatch.delenv("PANTHEON_BFF_TRADE_JOURNEY_PROJECTION_DSN", raising=False)
    monkeypatch.delenv("TELEMETRY_DB_DSN", raising=False)
    from services.control_plane.bff import main as bff_main

    read_surface = bff_main.app_deps.read_surface
    monkeypatch.setattr(read_surface, "_trade_journey_projection_reader_override", None, raising=False)
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    headers = {"Authorization": "Bearer alice:operator:tenant-a", "X-Tenant-Id": "tenant-a"}

    unconfigured = client.get(ATTRIBUTION_URL, headers=headers)
    assert unconfigured.status_code == 200, unconfigured.text
    meta = unconfigured.json()["meta"]
    assert meta["availability"] == "unavailable"
    assert meta["surfaces"]["trade_journeys"]["reason"].startswith("projection_reader_unavailable:")
    assert unconfigured.json()["data"]["items"] == []

    events = _events(user_id="alice", tenant_id="tenant-a")
    monkeypatch.setattr(
        read_surface,
        "_trade_journey_projection_reader_override",
        _ProjectionReader(events),
        raising=False,
    )
    served = client.get(ATTRIBUTION_URL, headers=headers)
    assert served.status_code == 200, served.text
    body = served.json()
    assert body["meta"]["availability"] == "available"
    assert body["meta"]["scope"] == {
        "tenant_id": "tenant-a",
        "owner_user_id": "alice",
        "environment": "paper",
    }
    assert [row["dimension_key"] for row in body["data"]["items"]] == ["strategy-alpha"]
    assert body["data"]["items"][0]["metrics"]["total_trades"] == 1

    other_user = client.get(
        ATTRIBUTION_URL,
        headers={"Authorization": "Bearer bob:operator:tenant-a", "X-Tenant-Id": "tenant-a"},
    )
    assert other_user.status_code == 200, other_user.text
    assert other_user.json()["data"]["items"] == []
    assert other_user.json()["meta"]["availability"] == "available"
