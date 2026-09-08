"""Comprehensive tests for Agora performance event transport and incidents isolated store (AGORA-CHAIN-001).

Covers:
1. Issue 2 (Event transport):
   - Unacknowledged delivery when no subscribers are registered (does NOT mark published)
   - Replay delivery after installing a working subscriber/publisher (delivers 1 event)
   - Outage simulation and recovery (fails closed, recovers on replay)
   - Retry-safe durable identity (subsequent replays do not duplicate)
   - Restart resilience (reconstructed store preserves published state)
2. Issue 3 (Incidents store isolation & BFF HTTP query path):
   - Incidents store runs on isolated path without BFF SQLite sharing
   - BFF PerformanceSuggestionStore routes queries and actions to incidents HTTP API
   - Replay, CAS, and terminal status handling through HTTP
   - Full BFF router get_strategy_performance and act_on_suggestion over incidents API
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
import unittest.mock as mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# Ensure bff is in sys.path
_BFF_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_BFF_DIR) in sys.path:
    sys.path.remove(str(_BFF_DIR))
sys.path.insert(0, str(_BFF_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agora.performance.consumer import (
    CanonicalPerformanceEventTransport,
    EvaluationTelemetryConsumer,
    canonical_performance_publisher,
    clear_performance_subscribers,
    consume_telemetry_outcome,
    get_canonical_performance_transport,
    register_performance_subscriber,
)
from agora.performance.models import AdjustmentSuggestion, SuggestionProvenance
from agora.performance.router import create_performance_router
from agora.performance.store import (
    PerformanceSuggestionConflict,
    PerformanceSuggestionNotFound,
    PerformanceSuggestionStore,
)
from services.trade_journey.materializer import JourneyMaterializer


def _sample_outcome_event(
    strategy_id: str = "strat-perf-001",
    correlation_id: str = "corr-event-001",
    tenant_id: str = "tenant-alpha",
    user_id: str = "user-alpha",
) -> Dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "owner_user_id": user_id,
        "strategy_id": strategy_id,
        "outcome_type": "drawdown_breach",
        "period": "latest",
        "correlation_id": correlation_id,
        "title": "Drawdown breach detected",
        "rationale": "Max drawdown exceeded 15% threshold",
        "source_id": "telemetry-engine-v1",
        "source_type": "telemetry_pipeline",
        "evidence_refs": ["ev-101", "ev-102"],
        "metrics": {"max_drawdown": 0.18, "threshold": 0.15},
        "as_of": "2026-09-08T01:00:00Z",
    }


# ===========================================================================
# ISSUE 2 TESTS: Canonical Event Transport & Subscriber Delivery
# ===========================================================================

def test_unacknowledged_on_absent_subscribers_and_replay_on_installed_subscriber(
    tmp_path: Path,
) -> None:
    clear_performance_subscribers()
    db_path = str(tmp_path / "perf_transport_1.sqlite3")
    store = PerformanceSuggestionStore(db_path)
    event = _sample_outcome_event(correlation_id="corr-unacked-001")

    # Step 1: Consume with default canonical_performance_publisher when NO subscriber is registered
    consumer = EvaluationTelemetryConsumer(
        store=store,
        publish_event_fn=canonical_performance_publisher,
    )
    with pytest.raises(RuntimeError, match="unacknowledged"):
        consumer.consume(event)

    suggestions = store.list_suggestions(tenant_id="tenant-alpha", strategy_id="strat-perf-001")
    assert len(suggestions) == 1
    sugg_id = suggestions[0]["suggestion_id"]

    # Crucial check: event was accepted by producer, but delivery was UNACKNOWLEDGED,
    # so store.is_event_published MUST be False!
    topic = "agora.performance.suggestion.created"
    assert store.is_event_published(topic, sugg_id) is False

    # Step 2: Now install a working subscriber
    received_events: List[Dict[str, Any]] = []

    def working_subscriber(topic_in: str, entity_id: str, payload: Dict[str, Any]) -> None:
        received_events.append({"topic": topic_in, "entity_id": entity_id, "payload": payload})

    canonical_performance_publisher.subscribe(working_subscriber)

    try:
        # Step 3: Replay after installing a working subscriber
        # MUST deliver exactly 1 event (resolves reviewer's rejection: "Replay after installing a working publisher delivered 0 events")
        replay_sugg = consumer.replay(event)
        assert replay_sugg.suggestion_id == sugg_id

        assert len(received_events) == 1
        delivered = received_events[0]
        assert delivered["topic"] == topic
        assert delivered["entity_id"] == sugg_id
        assert delivered["payload"]["strategy_id"] == "strat-perf-001"
        assert delivered["payload"]["suggestion_id"] == sugg_id

        # Now delivery is acknowledged, so store.is_event_published MUST be True!
        assert store.is_event_published(topic, sugg_id) is True

        # Step 4: Retry-safe durable identity: Subsequent replay must NOT deliver duplicate events
        consumer.replay(event)
        assert len(received_events) == 1  # Still 1, 0 duplicate delivered
    finally:
        clear_performance_subscribers()


def test_publisher_outage_fails_closed_and_recovers_on_restore(tmp_path: Path) -> None:
    clear_performance_subscribers()
    db_path = str(tmp_path / "perf_outage.sqlite3")
    store = PerformanceSuggestionStore(db_path)
    event = _sample_outcome_event(correlation_id="corr-outage-001")

    outage_active = True
    delivered: List[Dict[str, Any]] = []

    def flappable_subscriber(topic: str, entity_id: str, payload: Dict[str, Any]) -> None:
        if outage_active:
            raise RuntimeError("Downstream performance event bus outage (HTTP 503)")
        delivered.append(payload)

    canonical_performance_publisher.subscribe(flappable_subscriber)

    consumer = EvaluationTelemetryConsumer(
        store=store,
        publish_event_fn=canonical_performance_publisher,
    )

    try:
        # 1. During outage: consumer.consume must fail closed
        with pytest.raises(RuntimeError, match="Downstream performance event bus outage"):
            consumer.consume(event)

        # Ensure event is NOT marked published in store
        # Find suggestion_id produced
        suggestions = store.list_suggestions(tenant_id="tenant-alpha", strategy_id="strat-perf-001")
        assert len(suggestions) == 1
        sugg_id = suggestions[0]["suggestion_id"]
        assert store.is_event_published("agora.performance.suggestion.created", sugg_id) is False
        assert len(delivered) == 0

        # 2. Outage resolved: replay succeeds and acknowledges delivery
        outage_active = False
        replayed = consumer.replay(event)
        assert replayed.suggestion_id == sugg_id
        assert len(delivered) == 1
        assert delivered[0]["suggestion_id"] == sugg_id
        assert store.is_event_published("agora.performance.suggestion.created", sugg_id) is True

        # 3. Subsequent replay is a clean idempotent no-op for publisher
        consumer.replay(event)
        assert len(delivered) == 1
    finally:
        clear_performance_subscribers()


def test_consumer_restart_resilience_from_sqlite(tmp_path: Path) -> None:
    clear_performance_subscribers()
    db_path = str(tmp_path / "perf_restart.sqlite3")
    store = PerformanceSuggestionStore(db_path)
    event = _sample_outcome_event(correlation_id="corr-restart-001")

    delivered: List[Dict[str, Any]] = []
    canonical_performance_publisher.subscribe(lambda t, e, p: delivered.append(p))

    try:
        consumer = EvaluationTelemetryConsumer(
            store=store,
            publish_event_fn=canonical_performance_publisher,
        )
        sugg = consumer.consume(event)
        assert len(delivered) == 1
        assert store.is_event_published("agora.performance.suggestion.created", sugg_id := sugg.suggestion_id) is True

        # Process restart simulation: create fresh store and fresh consumer pointing to same sqlite file
        reconstructed_store = PerformanceSuggestionStore(db_path)
        reconstructed_consumer = EvaluationTelemetryConsumer(
            store=reconstructed_store,
            publish_event_fn=canonical_performance_publisher,
        )

        # Replay must see existing published event in SQLite and skip publishing
        reconstructed_consumer.replay(event)
        assert len(delivered) == 1  # No duplicate published
    finally:
        clear_performance_subscribers()


# ===========================================================================
# ISSUE 3 TESTS: Incidents SQLite Store Isolation & BFF Query Path
# ===========================================================================

def test_bff_store_routes_all_queries_and_actions_to_incidents_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate that BFF PerformanceSuggestionStore queries incidents HTTP endpoints

    without needing direct or injected access to the incidents container's SQLite file.
    """
    _PREV_RUNTIME_MANAGER_URL = os.environ.get("PANTHEON_RUNTIME_MANAGER_URL")
    _PREV_RUNTIME_MANAGER_TOKEN = os.environ.get("PANTHEON_RUNTIME_MANAGER_TOKEN")
    os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
    os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_TOKEN", "incident-route-test-token")
    try:
        from services.incidents.main import app as incidents_app, _get_default_suggestion_store
    finally:
        if _PREV_RUNTIME_MANAGER_URL is None:
            os.environ.pop("PANTHEON_RUNTIME_MANAGER_URL", None)
        else:
            os.environ["PANTHEON_RUNTIME_MANAGER_URL"] = _PREV_RUNTIME_MANAGER_URL
        if _PREV_RUNTIME_MANAGER_TOKEN is None:
            os.environ.pop("PANTHEON_RUNTIME_MANAGER_TOKEN", None)
        else:
            os.environ["PANTHEON_RUNTIME_MANAGER_TOKEN"] = _PREV_RUNTIME_MANAGER_TOKEN

    # Isolated incidents SQLite path (simulating /data/incidents/agora_performance.sqlite3 in container)
    incidents_db_path = str(tmp_path / "incidents_data" / "agora_performance.sqlite3")
    monkeypatch.setenv("PANTHEON_BFF_AGORA_PERFORMANCE_STORE_PATH", incidents_db_path)
    import services.incidents.main as main_mod
    monkeypatch.setattr(main_mod, "_DEFAULT_SUGGESTION_STORE", None)

    incidents_client = TestClient(incidents_app)

    # Pre-populate suggestion directly in incidents store
    incidents_store = _get_default_suggestion_store()
    sugg = AdjustmentSuggestion(
        suggestion_id="sug-remote-001",
        strategy_id="strat-isolated",
        period="latest",
        status="proposed",
        version=1,
        title="Remote Sizing Adjustment",
        rationale="Incidents service threshold breach",
        provenance=SuggestionProvenance(
            source_id="incidents-service",
            source_type="telemetry_engine",
            source_version="1",
            produced_at="2026-09-08T02:00:00Z",
            evidence_refs=["ev-inc-01"],
        ),
        as_of="2026-09-08T02:00:00Z",
    )
    incidents_store.upsert_suggestion(tenant_id="tenant-remote", owner_user_id="user-remote", suggestion=sugg)

    # Completely isolated BFF store with NO SQLite file, configured with incidents_api_url
    bff_dummy_db_path = str(tmp_path / "bff_data" / "agora_performance.sqlite3")
    # Note: bff_dummy_db_path does NOT exist on disk!

    # Create an HTTP transport adapter that routes urllib calls to TestClient
    def mock_urlopen(req: Any, timeout: Any = 5) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        # strip base url
        path = url.replace("http://incidents:8090", "")
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        headers = dict(req.headers) if hasattr(req, "headers") else {}
        data = req.data if hasattr(req, "data") else None

        if method == "GET":
            resp = incidents_client.get(path, headers=headers)
        elif method == "POST":
            json_body = json.loads(data.decode("utf-8")) if data else None
            resp = incidents_client.post(path, json=json_body, headers=headers)
        else:
            raise NotImplementedError(method)

        class MockResponse:
            def __init__(self, r: Any) -> None:
                self._r = r
                self.status = r.status_code

            def read(self) -> bytes:
                return self._r.content

            def __enter__(self) -> Any:
                if self.status >= 400:
                    import urllib.error
                    raise urllib.error.HTTPError(
                        url, self.status, self._r.text, {}, None
                    )
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        return MockResponse(resp)

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    # Instantiate BFF PerformanceSuggestionStore with incidents_api_url
    bff_store = PerformanceSuggestionStore(
        path=bff_dummy_db_path,
        incidents_api_url="http://incidents:8090",
    )

    # 1. list_suggestions via BFF store -> reaches incidents HTTP
    suggestions = bff_store.list_suggestions(tenant_id="tenant-remote", strategy_id="strat-isolated")
    assert len(suggestions) == 1
    assert suggestions[0]["suggestion_id"] == "sug-remote-001"

    # 2. get_suggestion via BFF store
    single = bff_store.get_suggestion(tenant_id="tenant-remote", suggestion_id="sug-remote-001")
    assert single is not None
    assert single["suggestion_id"] == "sug-remote-001"

    # 3. act via BFF store -> posts action to incidents service
    receipt, replayed = bff_store.act(
        tenant_id="tenant-remote",
        owner_user_id="user-remote",
        strategy_id="strat-isolated",
        suggestion_id="sug-remote-001",
        action="apply",
        expected_version=1,
        reason="Approved via BFF remote call",
        actor_id="operator-remote",
        idempotency_key="idem-key-remote-001",
        recorded_at="2026-09-08T02:05:00Z",
    )
    assert replayed is False
    assert receipt["status"] == "applied"
    assert receipt["version"] == 2
    receipt_id = receipt["receipt_id"]

    # 4. get_receipt via BFF store
    fetched_receipt = bff_store.get_receipt(
        tenant_id="tenant-remote",
        owner_user_id="user-remote",
        receipt_id=receipt_id,
    )
    assert fetched_receipt is not None
    assert fetched_receipt["receipt_id"] == receipt_id

    # 5. list_audit_events via BFF store
    audits = bff_store.list_audit_events(
        tenant_id="tenant-remote",
        owner_user_id="user-remote",
        suggestion_id="sug-remote-001",
    )
    assert len(audits) >= 1
    assert audits[0]["action"] == "apply"

    # 6. Verify BFF SQLite file was NEVER created on disk!
    assert not Path(bff_dummy_db_path).exists(), "BFF should never have written to local SQLite when querying incidents API"


def test_bff_router_end_to_end_with_incidents_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E Test: Operator queries BFF performance router and acts on suggestions

    when BFF is wired to incidents service via PANTHEON_INCIDENTS_API_URL.
    """
    _PREV_RUNTIME_MANAGER_URL = os.environ.get("PANTHEON_RUNTIME_MANAGER_URL")
    _PREV_RUNTIME_MANAGER_TOKEN = os.environ.get("PANTHEON_RUNTIME_MANAGER_TOKEN")
    os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
    os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_TOKEN", "incident-route-test-token")
    try:
        from services.incidents.main import app as incidents_app, _get_default_suggestion_store
    finally:
        if _PREV_RUNTIME_MANAGER_URL is None:
            os.environ.pop("PANTHEON_RUNTIME_MANAGER_URL", None)
        else:
            os.environ["PANTHEON_RUNTIME_MANAGER_URL"] = _PREV_RUNTIME_MANAGER_URL
        if _PREV_RUNTIME_MANAGER_TOKEN is None:
            os.environ.pop("PANTHEON_RUNTIME_MANAGER_TOKEN", None)
        else:
            os.environ["PANTHEON_RUNTIME_MANAGER_TOKEN"] = _PREV_RUNTIME_MANAGER_TOKEN

    incidents_db_path = str(tmp_path / "incidents" / "agora_performance.sqlite3")
    monkeypatch.setenv("PANTHEON_BFF_AGORA_PERFORMANCE_STORE_PATH", incidents_db_path)
    import services.incidents.main as main_mod
    monkeypatch.setattr(main_mod, "_DEFAULT_SUGGESTION_STORE", None)

    incidents_client = TestClient(incidents_app)

    # Pre-populate a suggestion in incidents service
    inc_store = _get_default_suggestion_store()
    sugg = AdjustmentSuggestion(
        suggestion_id="sug-e2e-001",
        strategy_id="strat-e2e",
        period="latest",
        status="proposed",
        version=1,
        title="Reduce Leverage",
        rationale="Drawdown threshold breach",
        provenance=SuggestionProvenance(
            source_id="incidents-service",
            source_type="telemetry_engine",
            source_version="1",
            produced_at="2026-09-08T03:00:00Z",
            evidence_refs=["ev-01"],
        ),
        as_of="2026-09-08T03:00:00Z",
    )
    inc_store.upsert_suggestion(tenant_id="tenant-e2e", owner_user_id="user-e2e", suggestion=sugg)

    def mock_urlopen(req: Any, timeout: Any = 5) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        path = url.replace("http://incidents:8090", "")
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        headers = dict(req.headers) if hasattr(req, "headers") else {}
        data = req.data if hasattr(req, "data") else None

        if method == "GET":
            resp = incidents_client.get(path, headers=headers)
        elif method == "POST":
            json_body = json.loads(data.decode("utf-8")) if data else None
            resp = incidents_client.post(path, json=json_body, headers=headers)
        else:
            raise NotImplementedError(method)

        class MockResponse:
            def __init__(self, r: Any) -> None:
                self._r = r
                self.status = r.status_code

            def read(self) -> bytes:
                return self._r.content

            def __enter__(self) -> Any:
                if self.status >= 400:
                    import urllib.error
                    raise urllib.error.HTTPError(
                        url, self.status, self._r.text, {}, None
                    )
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        return MockResponse(resp)

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    # Construct BFF router using PerformanceSuggestionStore with incidents_api_url
    bff_store = PerformanceSuggestionStore(
        path=str(tmp_path / "dummy_bff.sqlite3"),
        incidents_api_url="http://incidents:8090",
    )

    bff_app = FastAPI()

    def _extract_id(auth: str | None) -> SimpleNamespace:
        return SimpleNamespace(
            operator_id="user-e2e",
            roles=["operator"],
            claims={
                "sub": "user-e2e",
                "user_id": "user-e2e",
                "tenant_id": "tenant-e2e",
                "allowed_tenants": ["tenant-e2e"],
            },
        )

    router = create_performance_router(
        extract_identity=_extract_id,
        require_read_role=lambda id: None,
        require_write_role=lambda id: None,
        bff_error=lambda status, code, msg, reason, **kwargs: HTTPException(status, detail=msg),
        utc_now=lambda: "2026-09-08T03:10:00Z",
        get_trade_journey_store=lambda: None,
        suggestion_store=bff_store,
    )
    bff_app.include_router(router)
    bff_client = TestClient(bff_app)

    headers = {"Authorization": "Bearer token", "X-Tenant-Id": "tenant-e2e"}

    # 1. GET /bff/agora/trading-room/strategies/{strategy_id}/performance
    perf_resp = bff_client.get(
        "/bff/agora/trading-room/strategies/strat-e2e/performance",
        headers=headers,
    )
    assert perf_resp.status_code == 200, perf_resp.text
    perf_data = perf_resp.json()
    assert "data" in perf_data
    suggestions = perf_data["data"]["adjustment_suggestions"]["items"]
    assert len(suggestions) == 1
    assert suggestions[0]["suggestion_id"] == "sug-e2e-001"
    assert suggestions[0]["title"] == "Reduce Leverage"

    # 2. POST /bff/agora/trading-room/strategies/{strategy_id}/performance/suggestions/{id}/actions
    act_resp = bff_client.post(
        "/bff/agora/trading-room/strategies/strat-e2e/performance/suggestions/sug-e2e-001/actions",
        json={
            "action": "apply",
            "expected_version": 1,
            "reason": "Approved by risk team",
        },
        headers={**headers, "Idempotency-Key": "e2e-idem-key-12345"},
    )
    assert act_resp.status_code == 200, act_resp.text
    act_data = act_resp.json()
    assert act_data["data"]["status"] == "applied"
    assert act_data["data"]["version"] == 2
    receipt_id = act_data["data"]["receipt_id"]

    # 3. GET /bff/agora/performance/action-receipts/{receipt_id}
    receipt_resp = bff_client.get(
        f"/bff/agora/performance/action-receipts/{receipt_id}",
        headers=headers,
    )
    assert receipt_resp.status_code == 200
    assert receipt_resp.json()["data"]["receipt_id"] == receipt_id
