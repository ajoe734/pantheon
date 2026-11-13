"""Integration tests for the Agora persona lab routes (AGORA-PERSONA-LAB-RESTORE-20261112).

Each route is driven through the mounted router. The owners are stood in by a
recording transport so every assertion is about what the BFF forwards; the BFF
holds no store of its own.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.persona_lab.router import create_persona_lab_router

P = "/bff/agora/persona-lab/persona-a"
H = {"Authorization": "Bearer t", "X-Tenant-Id": "tenant-1"}
HARNESS = {
    "active_version": 2,
    "versions": [{"version": 1, "patch_id": None}, {"version": 2, "patch_id": "p1"}],
    "patches": {"p1": {
        "status": "committed", "proposal": {"evidence_refs": [{"ref_type": "packet", "ref_id": "k1"}]},
        "comparisons": [{"status": "complete", "decision": "KEEP_BASE", "reasons": ["candidate_not_better_keep_base"],
                         "criteria": {"decisions_differing": 1},
                         "bundles": [{"packet_id": "k1", "base": {"a": 1}, "candidate": {"a": 2},
                                      "decisions_differ": True, "virtual_book_delta": None}]}],
    }, "p2": {"status": "governance_required", "proposal": {}, "governance_proposal_ref": "gov-1"}},
    "outbox": {"gov-1": {"status": "delivered", "receipt": {"decision_id": "gov-1"}}},
}


@pytest.fixture
def lab(monkeypatch):
    for name in ("PANTHEON_PERSONA_API_URL", "PANTHEON_MEMORY_API_URL"):
        monkeypatch.setenv(name, "http://owner")
    calls = []
    responses = {}

    def transport(method, url, payload, headers):
        calls.append((method, url.replace("http://owner", ""), payload, headers))
        return responses.get((method, url.replace("http://owner", "")), (200, {"ok": True}))

    def error(status, code, message, reason, **kw):
        return HTTPException(status_code=status, detail={"code": str(code), "reason": reason})

    router = create_persona_lab_router(
        extract_identity=lambda authorization, mfa_token=None: {"operator_id": "u1", "tenant_id": "tenant-1"},
        require_read_role=lambda i: None, require_write_role=lambda i: None,
        bff_error=error, utc_now=lambda: "2026-11-13T00:00:00Z", http_request=transport)
    app = FastAPI()
    app.include_router(router)
    responses[("GET", "/api/personas/persona-a/harness")] = (200, HARNESS)
    return TestClient(app), calls, responses


def test_draft_and_skill_coaching_forward_to_owner_patches(lab):
    client, calls, _ = lab
    assert client.post(P + "/patches", json={"patch_type": "threshold"}, headers=H).status_code == 201
    assert calls[-1][:2] == ("POST", "/api/personas/persona-a/harness/patches")
    assert calls[-1][3]["X-Tenant-Id"] == "tenant-1"
    assert client.post(P + "/skill-coaching/patches", json={"x": 1}, headers=H).status_code == 201
    assert calls[-1][2]["patch_type"] == "skill"
    bad = client.post(P + "/skill-coaching/patches", json={"patch_type": "workflow"}, headers=H)
    assert bad.status_code == 422


def test_shadow_run_prechecks_then_runs_owner_shadow(lab):
    client, calls, _ = lab
    assert client.post(P + "/patches/p1/shadow-runs", headers=H).status_code == 201
    assert [c[1] for c in calls] == ["/api/personas/persona-a/harness/patches/p1/precheck",
                                     "/api/personas/persona-a/harness/patches/p1/shadow"]
    # an owner without the shadow route fails closed with a recorded reason
    lab[2][("POST", "/api/personas/persona-a/harness/patches/p1/shadow")] = (404, {"detail": "Not Found"})
    refused = client.post(P + "/patches/p1/shadow-runs", headers=H)
    assert refused.status_code == 501 and refused.json()["detail"]["reason"] == "shadow_owner_route_unavailable"


def test_submit_hands_off_to_management_review_or_fails_closed(lab):
    client, calls, _ = lab
    ok = client.post(P + "/patches/p2/submit", json={}, headers=H).json()["data"]
    assert ok["handoff_kind"] == "trainer_feedback_to_persona_update"
    assert ok["review_ref"] == "gov-1" and ok["review_status"] == "delivered"
    done = client.post(P + "/patches/p1/submit", json={"adoption_ref": "memo-1"}, headers=H)
    assert done.status_code == 200 or done.status_code == 201
    assert calls[-1][1].endswith("/p1/activate") and calls[-1][2] == {"adoption_ref": "memo-1"}
    # no review reference at all: refuse, never fabricate one
    lab[2][("GET", "/api/personas/persona-a/harness")] = (200, {**HARNESS, "patches": {"p3": {"proposal": {}}}})
    assert client.post(P + "/patches/p3/submit", json={}, headers=H).status_code == 409


def test_evaluation_suites_runs_and_shadow_view(lab):
    client, _, _ = lab
    assert client.get(P + "/evaluation/suites", headers=H).json()["data"] == [{"suite_id": "p1", "runs": 1}]
    runs = client.get(P + "/evaluation/runs", headers=H).json()["data"]
    assert runs[0]["run_id"] == "p1:0" and runs[0]["decision"] == "KEEP_BASE"
    view = client.get(P + "/patches/p1/shadow-comparison", headers=H).json()["data"]
    assert view["arms"] == ["base", "candidate"]
    assert view["rows"][0]["decision_time"] == "k1" and view["rows"][0]["base"] != view["rows"][0]["candidate"]
    assert client.get(P + "/patches/p2/shadow-comparison", headers=H).status_code == 404


def test_change_log_lists_versions_with_evidence_and_actions(lab):
    client, _, _ = lab
    data = client.get(P + "/change-log", headers=H).json()["data"]
    entry = data["entries"][1]
    assert data["active_version"] == 2 and entry["active"] and entry["evidence_refs"][0]["ref_id"] == "k1"
    assert entry["actions"] == ["accept", "undo", "never_again", "tell_why"]
    assert client.get(P + "/harness", headers=H).json()["data"]["active_version"] == 2


def test_accept_and_undo_forward_and_require_inputs(lab):
    client, calls, _ = lab
    assert client.post(P + "/change-log/p1/accept", json={}, headers=H).status_code == 422
    assert client.post(P + "/change-log/p1/accept", json={"adoption_ref": "m"}, headers=H).status_code == 200
    assert calls[-1][1].endswith("/p1/activate")
    assert client.post(P + "/change-log/p1/undo", json={}, headers=H).status_code == 422
    assert client.post(P + "/change-log/p1/undo", json={"reason": "worse"}, headers=H).status_code == 200
    assert calls[-1][:3] == ("POST", "/api/personas/persona-a/harness/rollback", {"patch_id": "p1", "reason": "worse"})


@pytest.mark.parametrize("route,kind", [("never-again", "never_again"), ("tell-why", "operator_reason")])
def test_never_again_and_tell_why_create_memory_candidates(lab, route, kind):
    client, calls, _ = lab
    body = {"reflection_id": "r1", "trade_episode_ids": ["e1"], "reflection_version": "1",
            "text": "too aggressive", "expiry": "2027-01-01T00:00:00Z"}
    assert client.post(f"{P}/change-log/p1/{route}", json=body, headers=H).status_code == 201
    method, path, payload, _h = calls[-1]
    assert (method, path) == ("POST", "/api/memory/trade-lessons")
    assert payload["proposed_change"].startswith(f"[{kind}]") and payload["review_state"] == "proposed"
    assert payload["target_env"] == "paper"
    # missing provenance fails closed, nothing is sent
    before = len(calls)
    assert client.post(f"{P}/change-log/p1/{route}", json={"text": "x"}, headers=H).status_code == 422
    assert len(calls) == before


def test_unconfigured_owner_and_owner_refusal_fail_closed(lab, monkeypatch):
    client, _, responses = lab
    responses[("GET", "/api/personas/persona-a/harness")] = (403, {"detail": "exact_persona_tenant_required"})
    r = client.get(P + "/change-log", headers=H)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "exact_persona_tenant_required"
    monkeypatch.delenv("PANTHEON_PERSONA_API_URL")
    assert client.get(P + "/change-log", headers=H).status_code == 503


def test_routes_are_mounted_in_app_factory_and_hold_no_store():
    import inspect
    from services.control_plane.bff.core import app_factory
    from services.control_plane.bff.agora.persona_lab import router as lab_router
    assert "create_persona_lab_router(" in inspect.getsource(app_factory)
    assert "Store(" not in inspect.getsource(lab_router)
