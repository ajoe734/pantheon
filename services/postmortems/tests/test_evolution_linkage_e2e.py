"""E2E and integration tests for postmortem-to-evolution outbox delivery and linkage.

Validates that:
1. Postmortem publish creates an outbox proposal with full aggregate and snapshot metadata.
2. Outbox worker invokes EvolutionClient with EVOLUTION_AUTH_TOKEN and EVOLUTION_DEFAULT_TENANT_ID.
3. Successful delivery creates an EvolutionDecision linked to the postmortem_id and marks outbox completed.
4. Mismatched or missing EVOLUTION_AUTH_TOKEN fails closed (outbox fails, no decision in evolution).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure required environment variables exist prior to service imports
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:8081")
os.environ.setdefault("PANTHEON_PERSISTENCE_POSTURE", "dev")

import httpx
import pytest
from fastapi.testclient import TestClient

from services.evolution import main as evolution_main
from services.foundation.reliable_delivery import ReliableOutboxRecord
from services.incident.incident import IncidentCase, Postmortem
from services.postmortems import main as postmortems_main


class _AcceptAllValidator:
    def validate_incident(self, incident):
        return None


def _clean_stores(tmp_path: Path):
    postmortems_main.reference_validator = _AcceptAllValidator()

    if postmortems_main.store._path and postmortems_main.store._path.exists():
        try:
            postmortems_main.store._path.unlink()
        except Exception:
            pass
    postmortems_main.store._loaded_mtime_ns = None
    postmortems_main.store._incidents.clear()
    postmortems_main.store._postmortems.clear()

    if hasattr(postmortems_main.outbox_store.impl, "path") and postmortems_main.outbox_store.impl.path.exists():
        try:
            postmortems_main.outbox_store.impl.path.unlink()
        except Exception:
            pass
    if hasattr(postmortems_main.inbox_store.impl, "path") and postmortems_main.inbox_store.impl.path.exists():
        try:
            postmortems_main.inbox_store.impl.path.unlink()
        except Exception:
            pass

    evolution_main.store._decisions.clear()
    if evolution_main.store._storage_path and evolution_main.store._storage_path.exists():
        try:
            evolution_main.store._storage_path.unlink()
        except Exception:
            pass
    evolution_main.proposal_inbox.clear()


@pytest.fixture
def clean_postmortem_evolution_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_PERSISTENCE_POSTURE", "dev")
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "pantheon-postmortem-test-token-2026")
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", "pantheon-default")
    monkeypatch.setenv("EVOLUTION_DEFAULT_TENANT_ID", "pantheon-default")
    monkeypatch.setenv("EVOLUTION_URL", "http://evolution.test")
    monkeypatch.setenv("POSTMORTEMS_OUTBOX_BACKOFF_BASE_SECONDS", "0")
    monkeypatch.setenv("POSTMORTEMS_OUTBOX_MAX_ATTEMPTS", "3")

    _clean_stores(tmp_path)
    yield
    _clean_stores(tmp_path)


def _seed_incident_and_postmortem(
    incident_id: str = "inc-pm-link-001",
    postmortem_id: str = "pm-link-001",
    artifact_id: str = "artifact-risk-guard",
) -> tuple[IncidentCase, Postmortem]:
    inc = IncidentCase(
        incident_id=incident_id,
        title="Execution Engine Risk Breach",
        status="open",
        severity="critical",
        created_at="2026-08-17T03:00:00Z",
        binding_id="binding-risk-001",
        deployment_stage="live",
        deployment_plan_id="plan-risk-001",
        capital_pool_id="pool-risk-001",
        persona_capital_binding_id="pcb-risk-001",
        artifact_id=artifact_id,
        artifact_version="3.0.0",
        runtime_id="runtime-risk-001",
        trace_id="trace-risk-001",
        telemetry_event_ids=["tel-risk-001"],
        incident_cluster_id=f"cluster-{incident_id}",
    )
    postmortems_main.store.create_incident(inc)

    pm = Postmortem(
        postmortem_id=postmortem_id,
        title="Postmortem for Critical Risk Breach",
        status="draft",
        created_at="2026-08-17T03:30:00Z",
        incident_id=incident_id,
        binding_id="binding-risk-001",
        deployment_stage="live",
        deployment_plan_id="plan-risk-001",
        capital_pool_id="pool-risk-001",
        persona_capital_binding_id="pcb-risk-001",
        artifact_id=artifact_id,
        artifact_version="3.0.0",
        runtime_id="runtime-risk-001",
        trace_id="trace-risk-001",
        root_cause="Drawdown threshold breach triggering automatic freeze",
    )
    postmortems_main.store.create_postmortem(pm)
    return inc, pm


def test_postmortem_publish_outbox_delivery_e2e(clean_postmortem_evolution_env):
    """Publishing a postmortem delivers through outbox to evolution and creates EvolutionDecision."""
    inc, pm = _seed_incident_and_postmortem()

    pm_client = TestClient(postmortems_main.app)
    pub_res = pm_client.post(
        f"/api/postmortems/{pm.postmortem_id}/status",
        json={"status": "published"},
    )
    assert pub_res.status_code == 200
    assert pub_res.json()["status"] == "published"

    # Outbox worker delivers to evolution service via ASGITransport
    evo_transport = httpx.ASGITransport(app=evolution_main.app)
    real_async_client = httpx.AsyncClient

    def make_client(*args, **kwargs):
        kwargs["transport"] = evo_transport
        return real_async_client(*args, **kwargs)

    with patch("httpx.AsyncClient", side_effect=make_client):
        asyncio.run(postmortems_main.process_postmortems_outbox())

    # Outbox completed
    assert postmortems_main.outbox_store.list_pending_and_failed() == []

    # EvolutionDecision created and linked
    decisions = evolution_main.store.list_all()
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.linked_postmortem_id == pm.postmortem_id
    assert decision.linked_incident_id == inc.incident_id
    assert decision.target_id == inc.artifact_id
    assert decision.decision_state == "proposed"


def test_postmortem_publish_fails_closed_without_valid_auth_token(clean_postmortem_evolution_env):
    """Postmortem outbox delivery fails closed if token is invalid or missing."""
    inc, pm = _seed_incident_and_postmortem(
        incident_id="inc-auth-fail",
        postmortem_id="pm-auth-fail",
    )

    pm_client = TestClient(postmortems_main.app)
    pub_res = pm_client.post(
        f"/api/postmortems/{pm.postmortem_id}/status",
        json={"status": "published"},
    )
    assert pub_res.status_code == 200

    evo_transport = httpx.ASGITransport(app=evolution_main.app)
    real_async_client = httpx.AsyncClient

    def make_client(*args, **kwargs):
        kwargs["transport"] = evo_transport
        return real_async_client(*args, **kwargs)

    # Postmortem outbox sends mismatched/bad token to evolution service
    from services.evolution.client import EvolutionClient
    orig_init = EvolutionClient.__init__

    def wrong_token_init(self, base_url, auth_token=None, tenant_id="pantheon-default", async_client=None):
        orig_init(self, base_url, auth_token="invalid-unauthorized-token", tenant_id=tenant_id, async_client=async_client)

    with patch.object(EvolutionClient, "__init__", wrong_token_init):
        with patch("httpx.AsyncClient", side_effect=make_client):
            asyncio.run(postmortems_main.process_postmortems_outbox())

    # Fail closed: 0 decisions created
    assert len(evolution_main.store.list_all()) == 0
    all_outbox = [postmortems_main.outbox_store.get(p["outbox_id"]) for p in postmortems_main.outbox_store.impl.list_all()]
    assert len(all_outbox) == 1
    assert all_outbox[0].status.value in {"failed", "dead_lettered"}
    assert "status_code=401" in str(all_outbox[0].last_error)
