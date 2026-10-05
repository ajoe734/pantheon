"""Decision authority at the mounted router and shared service entry points."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.identity.scope import resolve_agora_user_scope
from services.control_plane.bff.agora.research.router import create_research_router
from services.control_plane.bff.agora.research.store import MemoryResearchPlanStore
from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore
from services.control_plane.bff.personas.service import _bff_error, _require_operator_role, _require_read_role


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    store = MemoryResearchPlanStore()
    workshops = MemoryWorkshopStore()
    workshops.create_session({"workshop_id": "ws", "tenant_id": "tenant-a", "user_id": "owner"})
    # The plan author is deliberately different from the workshop owner.
    store.create_plan({"plan_id": "plan", "workshop_id": "ws", "tenant_id": "tenant-a",
                       "user_id": "author", "status": "draft", "lock_version": 1})
    events = []

    def build(user, role, tenant, injected=True):
        identity = SimpleNamespace(operator_id=user, roles=[role],
                                   claims={"sub": user, "tenant_id": tenant})
        router = create_research_router(
            extract_identity=lambda _: identity,
            require_read_role=_require_read_role,
            require_write_role=_require_operator_role if injected else None,
            bff_error=_bff_error,
            utc_now=lambda: "2026-09-30T00:00:00Z",
            research_plan_store=store,
            workshop_store=workshops,
        )
        monkeypatch.setattr(router.service, "_publish_research_event", lambda *args: events.append(args))
        app = FastAPI()
        app.include_router(router)
        scope = resolve_agora_user_scope(identity, utc_now=lambda: "now", requested_tenant_id=tenant)
        return TestClient(app), router.service, scope

    return store, workshops, events, build


@pytest.mark.parametrize("action", ["approve", "cancel"])
@pytest.mark.parametrize("entry", ["mounted", "service"])
@pytest.mark.parametrize("user,role,tenant,expected", [
    ("owner", "reviewer", "tenant-a", 200),
    ("other", "operator", "tenant-a", 200),
    ("author", "approver", "tenant-a", 403),
    ("other", "reviewer", "tenant-a", 403),
    ("other", "admin", "tenant-a", 403),
    ("owner", "viewer", "tenant-a", 403),
    ("other", "viewer", "tenant-a", 403),
    ("owner", "reviewer", "tenant-b", 404),
    ("other", "operator", "tenant-b", 404),
])
def test_decision_authority(setup, action, entry, user, role, tenant, expected):
    store, _, events, build = setup
    client, service, scope = build(user, role, tenant)
    before = store.get_plan("plan")
    if entry == "mounted":
        response = client.post(f"/bff/agora/research-plans/plan/{action}", headers={
            "X-Tenant-Id": tenant, "If-Match": "*", "Idempotency-Key": "decision",
        })
        assert response.status_code == expected, response.text
    elif expected != 200:
        with pytest.raises(HTTPException) as exc:
            getattr(service, f"{action}_plan")("plan", scope=scope, if_match="*")
        assert exc.value.status_code == expected
    else:
        getattr(service, f"{action}_plan")("plan", scope=scope, if_match="*")
    if expected == 200:
        plan = store.get_plan("plan")
        assert plan["status"] == {"approve": "approved", "cancel": "cancelled"}[action]
        assert plan["lock_version"] == 2
        assert plan["user_id"] == "author"
        if action == "approve":
            assert plan["approval"]["decided_by"] == user
        assert store.list_audit_actions()[0]["user_id"] == user
        assert len(events) == 1
    else:
        assert store.get_plan("plan") == before
        assert store.list_audit_actions() == []
        assert events == []


@pytest.mark.parametrize("injected", [True, False])
@pytest.mark.parametrize("path", [
    "/bff/agora/research-plans/plan/approve",
    "/bff/agora/research-plans/plan/cancel",
    "/bff/agora/workshops/ws/research-plans",
])
def test_stub_viewer_never_receives_write_scope(setup, injected, path):
    _, _, _, build = setup
    client, _, _ = build("owner", "viewer", "tenant-a", injected)
    response = client.post(path, headers={"X-Tenant-Id": "tenant-a", "If-Match": "*",
                                          "Idempotency-Key": "viewer"},
                           json={"spec_version": "1.0", "strategy_id": "s",
                                 "strategy_spec_registry_id": "r",
                                 "stages": [{"stage_type": "prototype_backtest"}]})
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("action", ["approve", "cancel"])
@pytest.mark.parametrize("invalid", ["missing-plan", "unscoped-plan", "foreign-workshop"])
def test_decision_rejects_missing_or_inconsistent_tenant(setup, action, invalid):
    store, workshops, events, build = setup
    if invalid == "unscoped-plan":
        store.update_plan("plan", {"tenant_id": None})
    elif invalid == "foreign-workshop":
        workshops.create_session({"workshop_id": "ws", "tenant_id": "tenant-b", "user_id": "owner"})
    client, _, _ = build("other", "operator", "tenant-a")
    plan_id = "missing" if invalid == "missing-plan" else "plan"
    response = client.post(f"/bff/agora/research-plans/{plan_id}/{action}", headers={
        "X-Tenant-Id": "tenant-a", "If-Match": "*", "Idempotency-Key": "invalid",
    })
    assert response.status_code == 404, response.text
    assert store.get_plan("plan")["status"] == "draft"
    assert store.list_audit_actions() == events == []
