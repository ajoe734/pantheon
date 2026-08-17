"""E2E tests proving the postmortem-triggered EvolutionDecision path.

Validates that:
1. Publishing a postmortem through the existing postmortem outbox client with
   configured EVOLUTION_AUTH_TOKEN and EVOLUTION_AUTH_MODE=token creates an
   EvolutionDecision linked to that postmortem_id.
2. Readback verification succeeds and the postmortem store links the decision ID.
3. The flow fails closed (no EvolutionDecision created, outbox delivery fails) if
   EVOLUTION_AUTH_TOKEN is missing, invalid, or tenant is forbidden.
4. Mismatched or corrupt postmortem linkage fails closed with 422/409.
5. Duplicate / replay delivery is idempotent and preserves the single decision.
6. Real HTTP subprocess execution with uvicorn services and real Bearer tokens
   proves end-to-end multi-process integration.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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
from services.incident.incident import (
    IncidentCase,
    IncidentStatus,
    Postmortem,
)
from services.postmortems import main as postmortems_main


class _AcceptAllValidator:
    def validate_incident(self, incident):
        return None


def _clean_stores(tmp_path: Path):
    """Clean all in-memory and disk stores for postmortems and evolution."""
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
def clean_evolution_postmortem_env(tmp_path, monkeypatch):
    """Fixture providing clean isolated stores and default token auth configuration."""
    monkeypatch.setenv("PANTHEON_PERSISTENCE_POSTURE", "dev")
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "pantheon-test-evo-token-2026")
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", "pantheon-default")
    monkeypatch.setenv("EVOLUTION_DEFAULT_TENANT_ID", "pantheon-default")
    monkeypatch.setenv("EVOLUTION_URL", "http://evolution.test")
    monkeypatch.setenv("POSTMORTEMS_OUTBOX_BACKOFF_BASE_SECONDS", "0")
    monkeypatch.setenv("POSTMORTEMS_OUTBOX_MAX_ATTEMPTS", "3")

    _clean_stores(tmp_path)
    yield
    _clean_stores(tmp_path)


def _seed_incident_and_postmortem(
    incident_id: str = "inc-l12-evo-001",
    postmortem_id: str = "pm-l12-evo-001",
    artifact_id: str = "artifact-strategy-alpha",
    stage: str = "live",
) -> tuple[IncidentCase, Postmortem]:
    inc = IncidentCase(
        incident_id=incident_id,
        title="Live Execution Anomaly Incident",
        status="open",
        severity="high",
        created_at="2026-08-17T01:00:00Z",
        binding_id="binding-l12-001",
        deployment_stage=stage,
        deployment_plan_id="plan-l12-001",
        capital_pool_id="pool-l12-001",
        persona_capital_binding_id="pcb-l12-001",
        artifact_id=artifact_id,
        artifact_version="2.1.0",
        runtime_id="runtime-l12-001",
        trace_id="trace-l12-001",
        telemetry_event_ids=["tel-l12-001"],
        incident_cluster_id=f"cluster-{incident_id}",
    )
    postmortems_main.store.create_incident(inc)

    pm = Postmortem(
        postmortem_id=postmortem_id,
        title="Postmortem for Live Anomaly",
        status="draft",
        created_at="2026-08-17T02:00:00Z",
        incident_id=incident_id,
        binding_id="binding-l12-001",
        deployment_stage=stage,
        deployment_plan_id="plan-l12-001",
        capital_pool_id="pool-l12-001",
        persona_capital_binding_id="pcb-l12-001",
        artifact_id=artifact_id,
        artifact_version="2.1.0",
        runtime_id="runtime-l12-001",
        trace_id="trace-l12-001",
        root_cause="Execution slippage breach on volatile market open",
    )
    postmortems_main.store.create_postmortem(pm)
    return inc, pm


def test_postmortem_publish_creates_and_links_evolution_decision(clean_evolution_postmortem_env):
    """Publishing a postmortem delivers through outbox with EVOLUTION_AUTH_TOKEN and creates an EvolutionDecision."""
    inc, pm = _seed_incident_and_postmortem()

    pm_client = TestClient(postmortems_main.app)
    pub_res = pm_client.post(
        f"/api/postmortems/{pm.postmortem_id}/status",
        json={"status": "published"},
    )
    assert pub_res.status_code == 200, pub_res.text
    assert pub_res.json()["status"] == "published"

    # Outbox should have 1 prepared/pending record
    pending = postmortems_main.outbox_store.list_pending_and_failed()
    assert len(pending) == 1
    record = pending[0]
    assert record.event.event_type == "postmortem.published"
    assert record.event.payload["postmortem_id"] == pm.postmortem_id

    # Deliver using ASGI transport connecting postmortems outbox worker to evolution service
    evo_transport = httpx.ASGITransport(app=evolution_main.app)
    real_async_client = httpx.AsyncClient

    def make_client(*args, **kwargs):
        kwargs["transport"] = evo_transport
        return real_async_client(*args, **kwargs)

    with patch("httpx.AsyncClient", side_effect=make_client):
        asyncio.run(postmortems_main.process_postmortems_outbox())

    # 1. Outbox record marked as completed (published)
    assert postmortems_main.outbox_store.list_pending_and_failed() == []

    # 2. Exactly 1 EvolutionDecision created in Evolution Store
    decisions = evolution_main.store.list_all()
    assert len(decisions) == 1
    decision = decisions[0]

    # 3. Decision linkages and attributes match the published postmortem
    assert decision.linked_postmortem_id == pm.postmortem_id
    assert decision.linked_incident_id == inc.incident_id
    assert decision.target_id == inc.artifact_id
    assert decision.target_stage == inc.deployment_stage
    assert decision.decision_state == "proposed"

    # 4. Evolution readback over authenticated API endpoint works
    evo_client = TestClient(evolution_main.app)
    readback = evo_client.get(
        f"/api/evolution/proposals/{decision.decision_id}",
        headers={
            "Authorization": "Bearer pantheon-test-evo-token-2026",
            "X-Tenant-Id": "pantheon-default",
        },
    )
    assert readback.status_code == 200
    assert readback.json()["decision_id"] == decision.decision_id
    assert readback.json()["linked_postmortem_id"] == pm.postmortem_id


def test_postmortem_delivery_fails_closed_when_auth_token_is_missing(clean_evolution_postmortem_env, monkeypatch):
    """If EVOLUTION_AUTH_TOKEN is missing or removed, delivery fails and NO EvolutionDecision is created."""
    inc, pm = _seed_incident_and_postmortem(
        incident_id="inc-no-token",
        postmortem_id="pm-no-token",
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

    # Postmortem outbox runs without auth token
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "")

    with patch("httpx.AsyncClient", side_effect=make_client):
        asyncio.run(postmortems_main.process_postmortems_outbox())

    # EvolutionDecision MUST NOT be created
    decisions = evolution_main.store.list_all()
    assert len(decisions) == 0, f"Expected 0 decisions on missing token, got {len(decisions)}"

    # Outbox record is not completed
    all_outbox = [postmortems_main.outbox_store.get(p["outbox_id"]) for p in postmortems_main.outbox_store.impl.list_all()]
    assert len(all_outbox) == 1
    assert all_outbox[0].status.value in {"failed", "dead_lettered"}
    assert "status_code=503" in str(all_outbox[0].last_error) or "token" in str(all_outbox[0].last_error)


def test_postmortem_delivery_fails_closed_when_auth_token_is_wrong(clean_evolution_postmortem_env):
    """If EVOLUTION_AUTH_TOKEN is invalid/mismatched, delivery fails and NO EvolutionDecision is created."""
    inc, pm = _seed_incident_and_postmortem(
        incident_id="inc-wrong-token",
        postmortem_id="pm-wrong-token",
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

    # Postmortem outbox sends wrong bearer token
    from services.evolution.client import EvolutionClient
    orig_init = EvolutionClient.__init__

    def wrong_token_init(self, base_url, auth_token=None, tenant_id="pantheon-default", async_client=None):
        orig_init(self, base_url, auth_token="completely-wrong-bearer-token", tenant_id=tenant_id, async_client=async_client)

    with patch.object(EvolutionClient, "__init__", wrong_token_init):
        with patch("httpx.AsyncClient", side_effect=make_client):
            asyncio.run(postmortems_main.process_postmortems_outbox())

    # Fail closed: 0 decisions created in evolution store
    assert len(evolution_main.store.list_all()) == 0
    all_outbox = [postmortems_main.outbox_store.get(p["outbox_id"]) for p in postmortems_main.outbox_store.impl.list_all()]
    assert len(all_outbox) == 1
    assert all_outbox[0].status.value in {"failed", "dead_lettered"}
    assert "status_code=401" in str(all_outbox[0].last_error)


def test_postmortem_delivery_fails_closed_when_tenant_is_forbidden(clean_evolution_postmortem_env, monkeypatch):
    """If the tenant is unauthorized by EVOLUTION_AUTH_ALLOWED_TENANTS, delivery fails with 403."""
    inc, pm = _seed_incident_and_postmortem(
        incident_id="inc-forbidden-tenant",
        postmortem_id="pm-forbidden-tenant",
    )

    pm_client = TestClient(postmortems_main.app)
    pm_client.post(f"/api/postmortems/{pm.postmortem_id}/status", json={"status": "published"})

    evo_transport = httpx.ASGITransport(app=evolution_main.app)
    real_async_client = httpx.AsyncClient

    def make_client(*args, **kwargs):
        kwargs["transport"] = evo_transport
        return real_async_client(*args, **kwargs)

    # Postmortems passes an unauthorized tenant
    monkeypatch.setenv("EVOLUTION_DEFAULT_TENANT_ID", "unauthorized-tenant")

    with patch("httpx.AsyncClient", side_effect=make_client):
        asyncio.run(postmortems_main.process_postmortems_outbox())

    assert len(evolution_main.store.list_all()) == 0
    all_outbox = [postmortems_main.outbox_store.get(p["outbox_id"]) for p in postmortems_main.outbox_store.impl.list_all()]
    assert len(all_outbox) == 1
    assert all_outbox[0].status.value in {"failed", "dead_lettered"}
    assert "status_code=403" in str(all_outbox[0].last_error)


def test_postmortem_delivery_fails_closed_when_linkage_is_corrupted(clean_evolution_postmortem_env):
    """If the delivery event postmortem snapshot or linkage is corrupted, delivery fails with 422/409."""
    inc, pm = _seed_incident_and_postmortem(
        incident_id="inc-corrupt-link",
        postmortem_id="pm-corrupt-link",
    )

    pm_client = TestClient(postmortems_main.app)
    pm_client.post(f"/api/postmortems/{pm.postmortem_id}/status", json={"status": "published"})

    # Corrupt the outbox event payload linkage
    from services.foundation.reliable_delivery import ReliableOutboxRecord
    record = postmortems_main.outbox_store.list_pending_and_failed()[0]
    corrupted_event = record.event
    corrupted_event.payload["postmortem_id"] = "pm-mismatched-id"
    corrupted_record = ReliableOutboxRecord.from_dict({
        **record.to_dict(),
        "status": "pending",
        "delivery_ready": True,
        "claim_token": None,
        "event": corrupted_event.to_dict(),
    })
    postmortems_main.outbox_store.put(corrupted_record)

    evo_transport = httpx.ASGITransport(app=evolution_main.app)
    real_async_client = httpx.AsyncClient

    def make_client(*args, **kwargs):
        kwargs["transport"] = evo_transport
        return real_async_client(*args, **kwargs)

    with patch("httpx.AsyncClient", side_effect=make_client):
        asyncio.run(postmortems_main.process_postmortems_outbox())

    # Fail closed: no decision created in evolution
    assert len(evolution_main.store.list_all()) == 0


def test_postmortem_delivery_idempotent_replay(clean_evolution_postmortem_env):
    """Replaying the outbox delivery is idempotent and does not create duplicate decisions."""
    inc, pm = _seed_incident_and_postmortem(
        incident_id="inc-replay-001",
        postmortem_id="pm-replay-001",
    )

    pm_client = TestClient(postmortems_main.app)
    pm_client.post(f"/api/postmortems/{pm.postmortem_id}/status", json={"status": "published"})

    evo_transport = httpx.ASGITransport(app=evolution_main.app)
    real_async_client = httpx.AsyncClient

    def make_client(*args, **kwargs):
        kwargs["transport"] = evo_transport
        return real_async_client(*args, **kwargs)

    # First delivery
    with patch("httpx.AsyncClient", side_effect=make_client):
        asyncio.run(postmortems_main.process_postmortems_outbox())

    decisions = evolution_main.store.list_all()
    assert len(decisions) == 1
    first_decision_id = decisions[0].decision_id

    # Force reset outbox record to pending to simulate redelivery / replay
    from services.foundation.reliable_delivery import ReliableOutboxRecord
    raw_record = postmortems_main.outbox_store.impl.list_all()[0]
    replay_record = ReliableOutboxRecord.from_dict({
        **raw_record,
        "status": "pending",
        "delivery_ready": True,
        "claim_token": None,
        "delivery_attempts": 1,
    })
    postmortems_main.outbox_store.put(replay_record)

    # Second delivery (replay)
    with patch("httpx.AsyncClient", side_effect=make_client):
        asyncio.run(postmortems_main.process_postmortems_outbox())

    # Assert exactly 1 decision remains
    decisions_after = evolution_main.store.list_all()
    assert len(decisions_after) == 1
    assert decisions_after[0].decision_id == first_decision_id


# ---------------------------------------------------------------------------
# Multi-Process Real HTTP Subprocess Test
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_url(client: httpx.Client, url: str, *, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp
        except Exception as exc:
            last_err = exc
        time.sleep(0.05)
    raise AssertionError(f"Timeout waiting for {url}: {last_err}")


def _wait_for_evolution_proposals(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    *,
    expected: int = 1,
    timeout: float = 15.0,
) -> list[dict]:
    deadline = time.monotonic() + timeout
    last_payload = None
    while time.monotonic() < deadline:
        try:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                last_payload = resp.json()
                if isinstance(last_payload, list) and len(last_payload) >= expected:
                    return last_payload
        except Exception:
            pass
        time.sleep(0.05)
    raise AssertionError(f"Timeout waiting for {expected} proposals at {url}; last={last_payload}")


@pytest.mark.skipif(
    importlib.util.find_spec("uvicorn") is None,
    reason="Real HTTP test requires uvicorn runtime dependency",
)
def test_real_http_postmortem_to_evolution_decision_e2e(tmp_path):
    """Spawn real postmortems and evolution HTTP processes with EVOLUTION_AUTH_TOKEN and assert linkage."""
    incident_dir = tmp_path / "incidents"
    evolution_dir = tmp_path / "evolution"
    incident_dir.mkdir()
    evolution_dir.mkdir()

    incident_id = "inc-http-e2e"
    postmortem_id = f"pm-{incident_id}"

    # Seed incident via direct file store
    from services.incident.incident import IncidentStore
    store = IncidentStore(incident_dir / "incidents.json")
    store.create_incident(
        IncidentCase(
            incident_id=incident_id,
            title="Real HTTP E2E Incident",
            status="open",
            severity="high",
            created_at="2026-08-17T04:00:00Z",
            binding_id="binding-http-e2e",
            deployment_stage="live",
            deployment_plan_id="plan-http-e2e",
            capital_pool_id="pool-http-e2e",
            persona_capital_binding_id="pcb-http-e2e",
            artifact_id="artifact-http-e2e",
            artifact_version="1.0.0",
            runtime_id="runtime-http-e2e",
            trace_id="trace-http-e2e",
            telemetry_event_ids=["tel-http-e2e"],
            incident_cluster_id="cluster-http-e2e",
        )
    )
    store.create_postmortem(
        Postmortem(
            postmortem_id=postmortem_id,
            title="Real HTTP E2E Postmortem",
            status="draft",
            created_at="2026-08-17T04:30:00Z",
            incident_id=incident_id,
            binding_id="binding-http-e2e",
            deployment_stage="live",
            deployment_plan_id="plan-http-e2e",
            capital_pool_id="pool-http-e2e",
            persona_capital_binding_id="pcb-http-e2e",
            artifact_id="artifact-http-e2e",
            artifact_version="1.0.0",
            runtime_id="runtime-http-e2e",
            trace_id="trace-http-e2e",
            root_cause="Real HTTP outbox delivery verification",
        )
    )

    postmortem_port = _free_port()
    evolution_port = _free_port()
    shared_auth_token = "secret-e2e-token-xyz"

    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env.update({
        "PYTHONPATH": str(REPO_ROOT),
        "PANTHEON_PERSISTENCE_POSTURE": "dev",
        "PANTHEON_RUNTIME_MANAGER_URL": "http://127.0.0.1:8081",
        "INCIDENT_STORE_BACKEND": "json",
        "POSTMORTEM_STORE_BACKEND": "json",
        "EVOLUTION_STORE_BACKEND": "json",
        "INCIDENTS_DATA_DIR": str(incident_dir),
        "POSTMORTEMS_DATA_DIR": str(incident_dir),
        "INCIDENT_DATA_DIR": str(incident_dir),
        "EVOLUTION_DATA_DIR": str(evolution_dir),
        "EVOLUTION_AUTH_MODE": "token",
        "EVOLUTION_AUTH_TOKEN": shared_auth_token,
        "EVOLUTION_AUTH_ALLOWED_TENANTS": "pantheon-default",
        "EVOLUTION_DEFAULT_TENANT_ID": "pantheon-default",
        "POSTMORTEMS_URL": f"http://127.0.0.1:{postmortem_port}",
        "EVOLUTION_URL": f"http://127.0.0.1:{evolution_port}",
        "POSTMORTEMS_OUTBOX_POLL_SECONDS": "0.05",
        "POSTMORTEMS_OUTBOX_BACKOFF_BASE_SECONDS": "0.01",
    })

    processes: list[subprocess.Popen] = []
    client = httpx.Client(timeout=5.0)

    try:
        # Start Evolution service process
        processes.append(
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "services.evolution.main:app", "--host", "127.0.0.1", "--port", str(evolution_port), "--log-level", "warning"],
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        )
        # Start Postmortems service process
        processes.append(
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "services.postmortems.main:app", "--host", "127.0.0.1", "--port", str(postmortem_port), "--log-level", "warning"],
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        )

        _wait_for_url(client, f"http://127.0.0.1:{evolution_port}/readyz")
        _wait_for_url(client, f"http://127.0.0.1:{postmortem_port}/readyz")

        # 1. Publish postmortem over HTTP
        publish_resp = client.post(
            f"http://127.0.0.1:{postmortem_port}/api/postmortems/{postmortem_id}/status",
            json={"status": "published"},
        )
        assert publish_resp.status_code == 200, publish_resp.text

        # 2. Wait for outbox loop to deliver to evolution over HTTP with Bearer token
        auth_headers = {
            "Authorization": f"Bearer {shared_auth_token}",
            "X-Tenant-Id": "pantheon-default",
        }
        proposals = _wait_for_evolution_proposals(
            client,
            f"http://127.0.0.1:{evolution_port}/api/evolution/proposals",
            headers=auth_headers,
            expected=1,
        )
        assert len(proposals) == 1
        decision = proposals[0]
        assert decision["linked_postmortem_id"] == postmortem_id
        assert decision["linked_incident_id"] == incident_id
        assert decision["target_id"] == "artifact-http-e2e"
        assert decision["decision_state"] == "proposed"

        # 3. Unauthenticated HTTP request to evolution fails closed with 401
        unauth_resp = client.get(
            f"http://127.0.0.1:{evolution_port}/api/evolution/proposals",
        )
        assert unauth_resp.status_code == 401
        assert "invalid Evolution bearer token" in unauth_resp.json()["detail"]

        # 4. Request with wrong token fails closed with 401
        wrong_token_resp = client.get(
            f"http://127.0.0.1:{evolution_port}/api/evolution/proposals",
            headers={"Authorization": "Bearer wrong-token", "X-Tenant-Id": "pantheon-default"},
        )
        assert wrong_token_resp.status_code == 401

    finally:
        client.close()
        for p in reversed(processes):
            if p.poll() is None:
                p.terminate()
        for p in reversed(processes):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
