"""Tests for Agora performance suggestion routes in services/incidents (AGORA-CHAIN-001)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

# Ensure imports work
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BFF_DIR = _REPO_ROOT / "services" / "control-plane" / "bff"
if str(_BFF_DIR) not in sys.path:
    sys.path.insert(0, str(_BFF_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_PREV_RUNTIME_MANAGER_URL = os.environ.get("PANTHEON_RUNTIME_MANAGER_URL")
_PREV_RUNTIME_MANAGER_TOKEN = os.environ.get("PANTHEON_RUNTIME_MANAGER_TOKEN")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_TOKEN", "incident-route-test-token")
try:
    from services.incidents.main import app, _get_default_suggestion_store
finally:
    if _PREV_RUNTIME_MANAGER_URL is None:
        os.environ.pop("PANTHEON_RUNTIME_MANAGER_URL", None)
    else:
        os.environ["PANTHEON_RUNTIME_MANAGER_URL"] = _PREV_RUNTIME_MANAGER_URL
    if _PREV_RUNTIME_MANAGER_TOKEN is None:
        os.environ.pop("PANTHEON_RUNTIME_MANAGER_TOKEN", None)
    else:
        os.environ["PANTHEON_RUNTIME_MANAGER_TOKEN"] = _PREV_RUNTIME_MANAGER_TOKEN

if str(_BFF_DIR) in sys.path:
    sys.path.remove(str(_BFF_DIR))
sys.path.insert(0, str(_BFF_DIR))

from agora.performance.models import AdjustmentSuggestion, SuggestionProvenance
from agora.performance.store import PerformanceSuggestionStore


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = str(tmp_path / "agora_performance.sqlite3")
    monkeypatch.setenv("PANTHEON_BFF_AGORA_PERFORMANCE_STORE_PATH", db_path)
    import services.incidents.main as main_mod
    monkeypatch.setattr(main_mod, "_DEFAULT_SUGGESTION_STORE", None)
    return TestClient(app)


def _sample_suggestion(
    suggestion_id: str = "sug-incidents-001",
    strategy_id: str = "strat-alpha",
    tenant_id: str = "tenant-inc",
    owner_user_id: str = "user-inc",
) -> AdjustmentSuggestion:
    return AdjustmentSuggestion(
        suggestion_id=suggestion_id,
        strategy_id=strategy_id,
        period="latest",
        status="proposed",
        version=1,
        title="Reduce Position Sizing",
        rationale="Drawdown breached risk threshold",
        provenance=SuggestionProvenance(
            source_id="telemetry-pipeline-v1",
            source_type="telemetry_engine",
            source_version="1",
            produced_at="2026-09-08T00:00:00Z",
            evidence_refs=["ev-001", "ev-002"],
        ),
        as_of="2026-09-08T00:00:00Z",
    )


def test_list_and_get_suggestions_via_incidents_api(client: TestClient, tmp_path: Path) -> None:
    store = _get_default_suggestion_store()
    sugg = _sample_suggestion()
    store.upsert_suggestion(tenant_id="tenant-inc", owner_user_id="user-inc", suggestion=sugg)

    # List suggestions
    resp = client.get(
        "/api/incidents/agora/performance/suggestions",
        params={"tenant_id": "tenant-inc", "strategy_id": "strat-alpha"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "suggestions" in data
    assert len(data["suggestions"]) == 1
    assert data["suggestions"][0]["suggestion_id"] == "sug-incidents-001"

    # Get single suggestion
    resp = client.get(
        "/api/incidents/agora/performance/suggestions/sug-incidents-001",
        params={"tenant_id": "tenant-inc", "strategy_id": "strat-alpha"},
    )
    assert resp.status_code == 200
    assert resp.json()["suggestion"]["suggestion_id"] == "sug-incidents-001"

    # 404 on missing
    resp = client.get("/api/incidents/agora/performance/suggestions/sug-missing")
    assert resp.status_code == 404


def test_suggestion_action_and_receipt_lifecycle(client: TestClient) -> None:
    store = _get_default_suggestion_store()
    sugg = _sample_suggestion(suggestion_id="sug-act-001")
    store.upsert_suggestion(tenant_id="tenant-inc", owner_user_id="user-inc", suggestion=sugg)

    # 1. Reject invalid short idempotency key
    bad_resp = client.post(
        "/api/incidents/agora/performance/suggestions/sug-act-001/actions",
        json={"action": "apply", "expected_version": 1},
        headers={"Idempotency-Key": "short"},
    )
    assert bad_resp.status_code == 400

    # 2. Apply action
    apply_resp = client.post(
        "/api/incidents/agora/performance/suggestions/sug-act-001/actions",
        json={
            "tenant_id": "tenant-inc",
            "owner_user_id": "user-inc",
            "strategy_id": "strat-alpha",
            "action": "apply",
            "expected_version": 1,
            "reason": "Risk limits approved",
            "actor_id": "ops-lead",
        },
        headers={"Idempotency-Key": "idem-apply-key-001"},
    )
    assert apply_resp.status_code == 200
    apply_data = apply_resp.json()
    assert apply_data["idempotent_replay"] is False
    receipt = apply_data["receipt"]
    assert receipt["status"] == "applied"
    assert receipt["version"] == 2
    assert receipt["action"] == "apply"
    receipt_id = receipt["receipt_id"]

    # 3. Idempotent replay with same key
    replay_resp = client.post(
        "/api/incidents/agora/performance/suggestions/sug-act-001/actions",
        json={
            "tenant_id": "tenant-inc",
            "owner_user_id": "user-inc",
            "strategy_id": "strat-alpha",
            "action": "apply",
            "expected_version": 1,
            "reason": "Risk limits approved",
            "actor_id": "ops-lead",
        },
        headers={"Idempotency-Key": "idem-apply-key-001"},
    )
    assert replay_resp.status_code == 200
    assert replay_resp.json()["idempotent_replay"] is True

    # 4. Conflict: same key, different action
    conflict_key_resp = client.post(
        "/api/incidents/agora/performance/suggestions/sug-act-001/actions",
        json={
            "tenant_id": "tenant-inc",
            "owner_user_id": "user-inc",
            "strategy_id": "strat-alpha",
            "action": "reject",
            "expected_version": 1,
        },
        headers={"Idempotency-Key": "idem-apply-key-001"},
    )
    assert conflict_key_resp.status_code == 409

    # 5. Conflict: suggestion is already terminal
    terminal_resp = client.post(
        "/api/incidents/agora/performance/suggestions/sug-act-001/actions",
        json={
            "tenant_id": "tenant-inc",
            "owner_user_id": "user-inc",
            "strategy_id": "strat-alpha",
            "action": "reject",
            "expected_version": 2,
        },
        headers={"Idempotency-Key": "idem-apply-key-002"},
    )
    assert terminal_resp.status_code == 409

    # 6. Query receipt by ID
    rcpt_resp = client.get(
        f"/api/incidents/agora/performance/action-receipts/{receipt_id}",
        params={"tenant_id": "tenant-inc", "owner_user_id": "user-inc"},
    )
    assert rcpt_resp.status_code == 200
    assert rcpt_resp.json()["receipt"]["receipt_id"] == receipt_id

    # 7. Query audit events
    audit_resp = client.get(
        "/api/incidents/agora/performance/suggestions/sug-act-001/audit-events",
        params={"tenant_id": "tenant-inc", "owner_user_id": "user-inc"},
    )
    assert audit_resp.status_code == 200
    audits = audit_resp.json()["audit_events"]
    assert len(audits) >= 1
    assert audits[0]["action"] == "apply"
