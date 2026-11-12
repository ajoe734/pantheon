"""Integration: the real provisioning entry provisions the persona's own agent.

Only the OpenClaw adapter HTTP is faked (``urllib.request.urlopen``). The
coordinator, the BFF provisioning hook, the OpenClaw ops client, the persona
runtime profile and the owner transport fixture are the production code paths.
"""
from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from services.control_plane.bff.personas import service as persona_service
from services.persona.harness import HarnessVersion, digest

_SIBLING = Path(__file__).resolve().parents[1] / "test_persona_provisioning_coordinator.py"
_spec = importlib.util.spec_from_file_location("_coordinator_fixtures", _SIBLING)
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)

SURFACE = {
    "identity_inputs": ["Paper research persona; propose evidence-cited changes."],
    "guardrails": {"allowed_tools": ["research.read"], "forbidden_solo_actions": ["trial_admission"]},
    "consult_policy": [{"action": "trial_admission", "required_roles": ["risk_persona"]}],
    "tool_guidance": {"research.read": "Read governed research."},
    "skills": [],
    "workflow": {"cadence_seconds": 3600, "steps": ["observe", "orient", "propose"]},
    "cycle_thresholds": {"minimum_evidence_count": 3, "minimum_confidence": 0.8},
    "memory_retrieval_policy": {"scopes": ["own_private"], "max_items": 10, "minimum_confidence": 0.8},
}


def _persona_owner_record(persona_id: str) -> dict:
    version = HarnessVersion(
        persona_id=persona_id, version=1, parent_version=None, patch_id=None,
        created_at="2026-11-12T00:00:00Z", created_by="owner-actor",
        content_digest=digest(SURFACE), surface=SURFACE,
    ).model_dump(mode="json")
    return {
        "persona_id": persona_id,
        "name": "Trader A",
        "mandate": "Paper-only momentum research",
        "strategy_family": "momentum",
        "lifecycle_state": "paper_owner",
        "metadata": {"persona_harness": {"active_version": 1, "versions": [version]}},
    }


class _Owner:
    def __init__(self, record):
        self.record = record

    def get_persona(self, persona_id):
        return self.record if persona_id == self.record["persona_id"] else None


class _Response:
    def __init__(self, status: int, body: dict):
        self.status, self.body = status, json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return self.body


def _provision(adapter, monkeypatch, record):
    """Run the real coordinator; return (ledger record, adapter calls)."""
    monkeypatch.setenv("PANTHEON_OPENCLAW_ADAPTER_URL", "http://adapter.test")
    monkeypatch.setenv("OPENCLAW_ADAPTER_URL", "http://adapter.test")
    store, ledger = fixtures._record_and_store()
    calls: list[dict] = []

    def urlopen(request, timeout=None):
        calls.append({
            "url": request.full_url,
            "headers": {k.lower(): v for k, v in request.header_items()},
            "body": json.loads(request.data.decode()) if request.data else None,
        })
        return adapter(request)

    coordinator = fixtures.PersonaProvisioningCoordinator(
        store=store,
        transport=fixtures.FakeOwnerTransport(),
        schedule_registrar=fixtures._schedule_receipt,
        lease_owner="test-worker",
    )
    with mock.patch.object(persona_service, "_get_active_write_owner", return_value=_Owner(record)), \
            mock.patch.object(urllib.request, "urlopen", urlopen):
        return coordinator.coordinate(ledger), calls


def test_provisioning_entry_creates_persona_own_agent_from_owner_record(monkeypatch):
    record = _persona_owner_record("persona-a")

    def adapter(request):
        body = json.loads(request.data.decode())
        pid = body["persona"]["persona_id"]
        return _Response(201, {"status": "ok", "agent": {
            "status": "created", "agent_id": pid, "model": "m",
            "memory": {"status": "materialized"},
        }})

    result, calls = _provision(adapter, monkeypatch, record)

    assert result.state == "provisioning" and result.current_step == "schedule_registered"
    agent = result.result["openclaw_agent"]
    assert agent["status"] == "ready" and agent["agent_id"] == "persona-a"
    assert agent["agent_status"] == "created"
    (call,) = calls
    assert call["url"] == "http://adapter.test/api/openclaw-adapter/agents/persona/ensure"
    # The adapter receives the owner record including the active harness version,
    # and a workspace derived from the persona id (its own, not shared `main`).
    assert call["body"]["persona"]["metadata"]["persona_harness"]["active_version"] == 1
    assert call["body"]["workspace_ref"].endswith("/persona-a")
    assert call["headers"]["idempotency-key"] and call["headers"]["x-request-id"]


@pytest.mark.parametrize("failure", ["http_503", "unconfigured_owner", "mismatched_agent"])
def test_agent_failure_is_readable_degraded_and_never_blocks_paper(monkeypatch, failure):
    record = _persona_owner_record("persona-a")

    def adapter(request):
        if failure == "mismatched_agent":
            return _Response(200, {"status": "ok", "agent": {"agent_id": "someone-else"}})
        raise urllib.error.HTTPError(
            request.full_url, 503, "unavailable", {},
            io.BytesIO(json.dumps({"error_code": "OPENCLAW_PERSONA_AGENT_SYNC_FAILED",
                                   "message": "gateway down"}).encode()),
        )

    if failure == "unconfigured_owner":
        record = {**record, "persona_id": "other-persona"}  # owner returns no record for persona-a
    result, _ = _provision(adapter, monkeypatch, record)

    # Paper admission is untouched: same terminal-free state, schedule admitted.
    assert result.state == "provisioning" and result.current_step == "schedule_registered"
    assert result.result["status"] == "dispatch_admitted"
    assert result.error is None and result.compensation is None
    agent = result.result["openclaw_agent"]
    assert agent["status"] == "degraded" and agent["reason"]


def test_missing_persona_owner_is_degraded_with_reason(monkeypatch):
    store, ledger = fixtures._record_and_store()
    coordinator = fixtures.PersonaProvisioningCoordinator(
        store=store, transport=fixtures.FakeOwnerTransport(),
        schedule_registrar=fixtures._schedule_receipt, lease_owner="test-worker",
    )
    with mock.patch.object(persona_service, "_get_active_write_owner", return_value=None):
        result = coordinator.coordinate(ledger)
    assert result.result["openclaw_agent"] == {
        "status": "degraded", "reason": "RuntimeError: persona_owner_unavailable_for_agent_provisioning",
    }
    assert result.current_step == "schedule_registered"
