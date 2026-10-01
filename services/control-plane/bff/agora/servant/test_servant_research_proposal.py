"""Servant research proposals: data-only draft -> 'draft' plan, nothing dispatched until approval."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.research.dispatcher import AdapterRegistry
from services.control_plane.bff.agora.research.router import create_research_router
from services.control_plane.bff.agora.research.store import MemoryResearchPlanStore
from services.control_plane.bff.agora.servant import research_proposal
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient
from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore
from services.control_plane.bff.personas.service import _bff_error, _require_operator_role, _require_read_role

_WORKSHOP = "ws-servant-proposal"
_URL = f"/bff/agora/workshops/{_WORKSHOP}/research-plans/servant-proposal"
_VALID = {
    "spec_version": "1.0",
    "strategy_id": "strategy-servant",
    "strategy_spec_registry_id": "registry-servant-v1",
    "stages": [{"stage_type": "prototype_backtest"}],
}


class _FakeClient:
    invoke_structured_extraction = OpenClawOpsClient.invoke_structured_extraction

    draft: Any = _VALID
    calls: list[dict] = []
    plain_invokes = 0

    def _assistant_timeout_seconds(self):
        return 5.0

    def _request(self, method, path, **kwargs):
        type(self).calls.append({"method": method, "path": path, **kwargs})
        return {"status": "ok", "data": {"output": {"structured_data": type(self).draft}}}

    def invoke_assistant_provider(self, **kwargs):  # pragma: no cover - must never be used
        type(self).plain_invokes += 1
        raise AssertionError("servant proposal must use the structured, data-only path")


@pytest.fixture()
def fake(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.plain_invokes = 0
    _FakeClient.draft = dict(_VALID)
    monkeypatch.setattr(research_proposal, "OpenClawOpsClient", _FakeClient)
    return _FakeClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    workshops = MemoryWorkshopStore()
    workshops.create_session({"workshop_id": _WORKSHOP, "tenant_id": "tenant-a", "user_id": "owner"})
    identity = SimpleNamespace(operator_id="owner", roles=["operator"], claims={"sub": "owner", "tenant_id": "tenant-a"})
    router = create_research_router(
        extract_identity=lambda _: identity,
        require_read_role=_require_read_role,
        require_write_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=lambda: "2026-09-30T00:00:00Z",
        research_plan_store=MemoryResearchPlanStore(),
        workshop_store=workshops,
        adapter_registry=AdapterRegistry(),
    )
    monkeypatch.setattr(router.service, "_publish_research_event", lambda *args: None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _headers(key=None, if_match=None):
    headers = {"X-Tenant-Id": "tenant-a"}
    if key:
        headers["Idempotency-Key"] = key
    if if_match:
        headers["If-Match"] = if_match
    return headers


def _plans(client) -> list:
    return client.get(f"/bff/agora/workshops/{_WORKSHOP}/research-plans", headers=_headers()).json()["items"]


def test_schema_offers_only_the_data_contract():
    schema = research_proposal.research_plan_extraction_schema()
    assert "tools" not in schema
    assert schema["properties"]["stages"]["items"]["properties"]["stage_type"]["enum"]


def test_valid_draft_creates_draft_plan_that_cannot_dispatch(client, fake):
    response = client.post(_URL, headers=_headers("servant-prop-ok"), json={"prompt": "test momentum"})
    assert response.status_code == 201, response.text
    created = response.json()
    plan_id = created["data"]["plan_id"]
    assert created["data"]["status"] == "draft"
    assert len(fake.calls) == 1 and fake.plain_invokes == 0
    call = fake.calls[0]
    assert call["path"].endswith("/providers/openclaw/structured")
    assert set(call["body"]) == {"mode", "prompt", "extraction_schema"}

    dispatch = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers("servant-prop-dispatch", created["meta"]["etag"]),
    )
    assert dispatch.status_code == 409, dispatch.text
    runs = client.get(f"/bff/agora/research-plans/{plan_id}/runs", headers=_headers())
    assert runs.json()["items"] == []

    approve = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers("servant-prop-approve", created["meta"]["etag"]),
    )
    assert approve.status_code == 200, approve.text


@pytest.mark.parametrize(
    "draft",
    [
        {**_VALID, "stages": []},
        {**_VALID, "spec_version": "2.0"},
        {**_VALID, "stages": [{"stage_type": "place_live_order"}]},
        {**_VALID, "unexpected": True},
        {"spec_version": "1.0"},
        "not-an-object",
    ],
)
def test_invalid_draft_creates_no_plan(client, fake, draft):
    fake.draft = draft
    response = client.post(_URL, headers=_headers("servant-prop-bad"), json={"prompt": "bad"})
    assert response.status_code in {422, 502}, response.text
    assert _plans(client) == []
