"""Real HTTP create entry provisions the persona's own agent (AC1/AC5/AC6).

Only the OpenClaw adapter HTTP (``urllib.request.urlopen``) is faked; the
Persona is created by the real create-paper-bundle entry, so the agent sync must
run after the owner record exists and its status must be exposed and retained.
"""
from __future__ import annotations

import io
import json
import tempfile
import urllib.error
import urllib.request
from unittest import mock

from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.tests import test_bff_persona_create_paper_bundle as bundle
from services.control_plane.bff.tests.test_bff_persona_create_paper_bundle import (  # noqa: F401
    _isolate_persona_create_service_clients,
)

PAYLOAD = {
    "name": "Alpha Trader",
    "archetype": "mean_reversion",
    "risk": "low",
    "mandate": "Trade TW equities using daily pricing",
    "market": "TW",
    "symbols": ["2330.TW"],
}


class _Resp:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def getcode(self):
        return 201

    def read(self):
        return self.body


def _create(monkeypatch, adapter, key):
    monkeypatch.setenv("PANTHEON_OPENCLAW_ADAPTER_URL", "http://adapter.test")
    monkeypatch.setenv("OPENCLAW_ADAPTER_URL", "http://adapter.test")
    calls = []

    def urlopen(request, timeout=None):
        calls.append(json.loads(request.data.decode()))
        return adapter(request)

    td = tempfile.TemporaryDirectory()
    client, store = bundle._fresh_client(td.name)
    with mock.patch.object(urllib.request, "urlopen", urlopen):
        resp = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=PAYLOAD,
            headers={**bundle.HEADERS, "Idempotency-Key": key},
        )
    return td, client, store, resp, calls


def test_fresh_create_makes_adapter_call_and_exposes_agent(monkeypatch):
    def adapter(request):
        pid = json.loads(request.data.decode())["persona"]["persona_id"]
        return _Resp({"status": "ok", "agent": {
            "status": "created", "agent_id": pid, "model": "m",
            "memory": {"status": "materialized"},
        }})

    td, client, store, resp, calls = _create(monkeypatch, adapter, "agent-http-1")
    with td:
        assert resp.status_code == 201, resp.text
        persona_id = resp.json()["data"]["id"]
        # No pre-existing Persona: the owner record existed when the sync ran.
        (call,) = calls
        assert call["persona"]["persona_id"] == persona_id
        agent = resp.json()["meta"]["openclaw_agent"]
        assert agent["status"] == "ready" and agent["agent_id"] == persona_id
        stored = store.get_persona(persona_id)["metadata"]["openclaw_agent"]
        assert stored["status"] == "ready"


def test_degraded_agent_is_exposed_persisted_and_never_blocks_paper(monkeypatch):
    def adapter(request):
        raise urllib.error.HTTPError(
            request.full_url, 503, "unavailable", {},
            io.BytesIO(json.dumps({"error_code": "OPENCLAW_PERSONA_AGENT_SYNC_FAILED",
                                   "message": "gateway down"}).encode()),
        )

    td, client, store, resp, calls = _create(monkeypatch, adapter, "agent-http-2")
    with td:
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data"]["state"] == "provisioning"  # paper admission unaffected
        assert body["meta"]["openclaw_agent"]["status"] == "degraded"
        assert body["meta"]["openclaw_agent"]["reason"]
        persona_id = body["data"]["id"]
        metadata = store.get_persona(persona_id)["metadata"]
        assert metadata["openclaw_agent"]["status"] == "degraded"

        # A later readback rebuild of provisioning metadata keeps the status.
        with mock.patch.object(personas_service, "_get_active_write_owner", return_value=store):
            projected, _ = personas_service._persona_record_for_provisioning(
                _first_record(), payload=PAYLOAD, owner="op",
                existing=store.get_persona(persona_id),
            )
        assert projected["metadata"]["openclaw_agent"]["status"] == "degraded"


def _first_record():
    return personas_service._PERSONA_PROVISIONING_STORE.list_all()[0]
