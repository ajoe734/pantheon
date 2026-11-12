from __future__ import annotations

import copy
import hashlib
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from typing import Any

from services.control_plane.bff.agora.router import create_agora_router
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.personas.service import (
    _extract_identity,
    _require_read_role,
    _require_operator_role,
    _bff_error,
)
from services.control_plane.bff.agora.interaction.worker import AgoraInteractionWorker
from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore
try:
    from agora.service import _AGORA_SIGNAL_WRITE_ROLES, _AGORA_BULK_FEEDBACK_ROLES
except ImportError:
    from services.control_plane.bff.agora.service import (
        _AGORA_SIGNAL_WRITE_ROLES,
        _AGORA_BULK_FEEDBACK_ROLES,
    )


def _require_agora_signal_write_role(identity: Any) -> None:
    if not _AGORA_SIGNAL_WRITE_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora signal creation requires analyst-level role",
            "Operator does not hold the required analyst, operator, reviewer, approver, or admin role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with analyst-level Agora write access",
        )


def _require_agora_bulk_feedback_role(identity: Any) -> None:
    if not _AGORA_BULK_FEEDBACK_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora feedback access requires analyst role",
            "Operator does not hold the required Agora feedback role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with analyst, operator, reviewer, approver, or admin role",
        )


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


AUTH = {"Authorization": "Bearer interaction-user:operator", "Idempotency-Key": "idem-context-1"}


class FakeReadStore:
    def list_personas(self, **kwargs):
        return [
            {"persona_id": "ready", "tenant_id": "pantheon-dev", "display_name": "Ready", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "paper-running", "tenant_id": "pantheon-dev", "display_name": "Paper Running", "lifecycle_state": "paper_running", "metadata": {"deployment_stage": "paper", "environment_ceiling": "paper"}},
            {"persona_id": "research-only", "tenant_id": "pantheon-dev", "display_name": "Research Only", "lifecycle_state": "research_only", "environment_ceiling": "paper"},
            {"persona_id": "consultable", "tenant_id": "pantheon-dev", "display_name": "Consultable", "lifecycle_state": "consultable", "environment_ceiling": "paper"},
            {"persona_id": "paper-owner", "tenant_id": "pantheon-dev", "display_name": "Paper Owner", "lifecycle_state": "paper_owner", "environment_ceiling": "paper"},
            {"persona_id": "live-owner", "tenant_id": "pantheon-dev", "display_name": "Live Owner", "lifecycle_state": "live_owner", "environment_ceiling": "paper"},
            {"persona_id": "draft", "tenant_id": "pantheon-dev", "display_name": "Draft", "lifecycle_state": "draft", "environment_ceiling": "paper"},
            {"persona_id": "frozen", "tenant_id": "pantheon-dev", "display_name": "Frozen", "lifecycle_state": "frozen", "environment_ceiling": "paper"},
            {"persona_id": "retired", "tenant_id": "pantheon-dev", "display_name": "Retired", "lifecycle_state": "retired", "environment_ceiling": "paper"},
            {"persona_id": "research", "tenant_id": "pantheon-dev", "display_name": "Research", "lifecycle_state": "active", "environment_ceiling": "research"},
            {"persona_id": "unscoped", "tenant_id": None, "display_name": "Unscoped", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "snapshot-missing", "tenant_id": "pantheon-dev", "display_name": "No Snapshot", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "capability-missing", "tenant_id": "pantheon-dev", "display_name": "No Capability", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "pointer-mismatch", "tenant_id": "pantheon-dev", "display_name": "Mismatched Snapshot", "lifecycle_state": "active", "environment_ceiling": "paper", "metadata": {"capability_snapshot_id": "snap-wrong-persona"}},
            {"persona_id": "deployed", "tenant_id": "pantheon-dev", "display_name": "Generic Deployed", "lifecycle_state": "deployed", "metadata": {"deployment_stage": "paper"}},
            {"persona_id": "stage-only", "tenant_id": "pantheon-dev", "display_name": "Stage Only", "lifecycle_state": "active", "metadata": {"deployment_stage": "paper"}},
            {"persona_id": "ceiling-unknown", "tenant_id": "pantheon-dev", "display_name": "Unknown Ceiling", "lifecycle_state": "active"},
        ]

    def get_capability_snapshot_for_persona(self, persona_id):
        if persona_id == "snapshot-missing":
            return None
        if persona_id == "capability-missing":
            return {"snapshot_id": f"snap-{persona_id}", "capabilities": ["research_only"]}
        return {"snapshot_id": f"snap-{persona_id}", "capabilities": ["persona_opinion"]}

    def get_persona(self, persona_id):
        return next(
            (row for row in self.list_personas() if row.get("persona_id") == persona_id),
            None,
        )

    def get_strategy_spec_detail(self, strategy_id, *, version_selector=None):
        if strategy_id != "strategy-1" or version_selector != "v1":
            return None
        return {
            "strategy_id": strategy_id,
            "strategy_spec_registry_id": version_selector,
        }

    def get_capability_snapshot(self, snapshot_id):
        if snapshot_id == "snap-wrong-persona":
            return {
                "snapshot_id": snapshot_id,
                "persona_id": "another-persona",
                "capabilities": ["persona_opinion"],
            }
        return None

    def list_approval_decisions(self):
        return []

    def list_decision_journal_entries(self):
        return [{"id": "entry-7", "tenant_id": "pantheon-dev", "owner_user_id": "interaction-user"}]


read_store = FakeReadStore()
router = create_agora_router(
    extract_identity=_extract_identity,
    require_read_role=_require_read_role,
    require_write_role=_require_operator_role,
    require_operator_role=_require_operator_role,
    require_journal_write_role=_require_operator_role,
    require_agora_signal_write_role=_require_agora_signal_write_role,
    require_agora_bulk_feedback_role=_require_agora_bulk_feedback_role,
    bff_error=_bff_error,
    utc_now=_utc_now_rfc3339,
    get_read_store=lambda: read_store,
    read_surface=lambda: read_store,
    sync_servant_agent=lambda p: {},
)
interaction_lifecycle = router.interaction_lifecycle
workshop_store = router.workshop_store

app = FastAPI()

@app.exception_handler(HTTPException)
@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request, exc):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}})

app.include_router(router)


def client(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    global read_store
    read_store = FakeReadStore()
    if interaction_lifecycle.backend == "memory":
        with interaction_lifecycle._lock:
            interaction_lifecycle._requests.clear()
            interaction_lifecycle._idempotency.clear()
            interaction_lifecycle._invocations.clear()
            interaction_lifecycle._syntheses.clear()
            interaction_lifecycle._outbox.clear()
            interaction_lifecycle._candidate_links.clear()
            interaction_lifecycle._audits.clear()
            interaction_lifecycle._retry_commands.clear()
            interaction_lifecycle._context_bindings.clear()
            interaction_lifecycle._context_binding_latest.clear()
    if hasattr(workshop_store, "_sessions"):
        workshop_store._sessions.clear()
        workshop_store._events.clear()
        workshop_store._cards.clear()
    return TestClient(app, raise_server_exceptions=False)


def context_payload(version="v1"):
    return {"environment": "paper", "context_refs": [
        {"type": "strategy", "id": "strategy-1", "version_id": version},
        {"type": "decision_event", "id": "decision-1"},
    ]}


def _institutional_cohort(monkeypatch):
    def persona(pid, **fields):
        return {"persona_id": pid, "tenant_id": "pantheon-dev", "status": "active",
                "lifecycle_state": "paper_running", "environment_ceiling": "paper", **fields}

    # The private role must be recognised even with an ordinary Persona ID/name.
    rows = [
        persona("focus"),
        persona("ordinary-id", archetype="agora_servant", mandate="user_private_agora_servant",
                strategy_family="agora_servant", metadata={"persona_class": "agora_servant",
                    "owner_scope": "user_private", "visibility_scope": "private",
                    "memory_scope": "private_user", "agora_user_id": "interaction-user"}),
        persona("peer", paper_runtime_state="paused", metadata={"runtime_status": "paused"}),
        persona("foreign", tenant_id="other-tenant"),
        persona("archived", status="archived", paper_runtime_state="retired",
                metadata={"lifecycle_state": "paper_running"}),
        persona("suspended", status="suspended"),
        persona("retired", lifecycle_state="retired"),
    ]
    monkeypatch.setattr(read_store, "list_personas", lambda **kwargs: copy.deepcopy(rows))
    return rows


def _resolve_persona_entry(c, *, ids=None, mode="compare", workshop_id=None, source_kind="persona"):
    focused_id = "focus" if source_kind == "persona" else workshop_id
    route = (f"/management/personas/{focused_id}" if source_kind == "persona"
             else f"/agora/strategy-workshop/{focused_id}")
    payload = {"context_refs": [{"type": source_kind, "id": focused_id}],
               "source_route": route, "return_route": route,
               "focused_object": {"kind": source_kind, "id": focused_id},
               "evidence_cutoff": _utc_now_rfc3339(), "selected_persona_ids": ids or ["focus"],
               "initial_mode": mode}
    if workshop_id:
        payload["workshop_id"] = workshop_id
    return c.post("/bff/agora/interactions/context:resolve",
                  headers={**AUTH, "Idempotency-Key": uuid.uuid4().hex}, json=payload)


def _eligible(c, workshop_id, mode="compare"):
    response = c.post("/bff/agora/interactions/participants:eligible", headers=AUTH,
                      json={"workshop_id": workshop_id, "mode": mode, "environment": "research"})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _daily_submission(binding, snapshots):
    now = _utc_now_rfc3339()
    text = "Compare the frozen Paper evidence; advisory only."
    return {"workshop_id": binding["workshop_id"],
            "human_request": {"request_id": uuid.uuid4().hex, "operator_id": "interaction-user",
                "mode": binding["initial_mode"], "request_text": text, "submitted_at": now,
                "request_sha256": hashlib.sha256(text.encode()).hexdigest()},
            "context_snapshot": {**{key: binding[key] for key in (
                "tenant_id", "source_route", "focused_object", "context_refs", "strategy_ref",
                "decision_ref", "journal_ref", "position_risk_snapshot_refs", "evidence_cutoff",
                "selected_persona_ids", "initial_mode", "return_route")}, "captured_at": now},
            "participants": [snapshots[pid] for pid in binding["selected_persona_ids"]]}


@pytest.mark.parametrize("mode", ["compare", "consult"])
def test_management_compare_eligible_pool_is_institutional_and_owner_active(monkeypatch, mode):
    c = client(monkeypatch)
    _institutional_cohort(monkeypatch)
    resolution = _resolve_persona_entry(c)
    assert resolution.status_code == 200, resolution.text
    pool = _eligible(c, resolution.json()["data"]["workshop_id"], mode)
    assert [row["persona_id"] for row in pool["included"]] == ["focus", "peer"]
    excluded = {row["persona_id"]: row["reasons"] for row in pool["excluded"]}
    assert "persona_not_institutional" in excluded["ordinary-id"]
    assert "tenant_mismatch" in excluded["foreign"]
    for pid in ("archived", "suspended", "retired"):
        assert "persona_not_active" in excluded[pid]


def test_management_compare_without_peer_does_not_fall_back_to_private_servant(monkeypatch):
    c = client(monkeypatch)
    rows = _institutional_cohort(monkeypatch)
    rows[:] = [row for row in rows if row["persona_id"] != "peer"]
    resolution = _resolve_persona_entry(c)
    assert resolution.status_code == 200, resolution.text
    pool = _eligible(c, resolution.json()["data"]["workshop_id"])
    assert [row["persona_id"] for row in pool["included"]] == ["focus"]
    assert all(row["participant_snapshot"] is None for row in pool["excluded"])


@pytest.mark.parametrize("status", ["archived", "suspended", "inactive", "disabled"])
def test_owner_inactive_status_overrides_stale_operational_alias(monkeypatch, status):
    c = client(monkeypatch)
    rows = _institutional_cohort(monkeypatch)
    rows[2]["status"] = status
    resolution = _resolve_persona_entry(c, mode="ask")
    pool = _eligible(c, resolution.json()["data"]["workshop_id"], "ask")
    assert "peer" not in {row["persona_id"] for row in pool["included"]}
    assert "archived" not in {row["persona_id"] for row in pool["included"]}


@pytest.mark.parametrize("mode", ["ask", "reflect"])
def test_management_talk_and_reflect_keep_exact_focused_selection(monkeypatch, mode):
    c = client(monkeypatch)
    _institutional_cohort(monkeypatch)
    resolution = _resolve_persona_entry(c, mode=mode)
    assert resolution.status_code == 200, resolution.text
    binding = resolution.json()["data"]["context_binding"]
    assert binding["selected_persona_ids"] == ["focus"]
    assert binding["initial_mode"] == mode
    snapshots = {row["persona_id"]: row["participant_snapshot"]
                 for row in _eligible(c, binding["workshop_id"], mode)["included"]}
    submitted = c.post("/bff/agora/interactions", json=_daily_submission(binding, snapshots),
                       headers={**AUTH, "Idempotency-Key": uuid.uuid4().hex})
    assert submitted.status_code == 202, submitted.text
    data = submitted.json()["data"]
    assert [row["persona_id"] for row in data["participants"]] == ["focus"]
    assert data["authority"]["execution_authority"] == "none"
    assert data["authority"]["broker_called"] is False


def test_management_compare_freeze_rejects_private_selected_peer(monkeypatch):
    c = client(monkeypatch)
    _institutional_cohort(monkeypatch)
    response = _resolve_persona_entry(c, ids=["focus", "ordinary-id"])
    assert response.status_code == 422, response.text
    assert "participant_eligibility_failed" in response.text


def test_private_agora_workshop_preserves_own_servant_and_denies_other_user(monkeypatch):
    c = client(monkeypatch)
    rows = _institutional_cohort(monkeypatch)
    other = copy.deepcopy(rows[1])
    other["persona_id"] = "other-private"
    other["metadata"]["agora_user_id"] = "another-user"
    rows.append(other)
    initial = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload())
    wid = initial.json()["data"]["workshop_id"]
    resolution = _resolve_persona_entry(c, ids=["focus", "ordinary-id"], workshop_id=wid,
                                         source_kind="workshop")
    assert resolution.status_code == 200, resolution.text
    pool = _eligible(c, wid)
    assert "ordinary-id" in {row["persona_id"] for row in pool["included"]}
    assert "other-private" not in {row["persona_id"] for row in pool["included"]}
    denied = _resolve_persona_entry(c, ids=["focus", "other-private"], workshop_id=wid,
                                    source_kind="workshop")
    assert denied.status_code == 403, denied.text


def test_submit_uses_matching_management_receipt_even_after_new_agora_receipt(monkeypatch):
    c = client(monkeypatch)
    rows = _institutional_cohort(monkeypatch)
    # An explicit immutable version keeps the participant snapshot unchanged;
    # only the authoritative role/receipt check can reject this stale choice.
    rows[2]["persona_version"] = "v1"
    resolution = _resolve_persona_entry(c, ids=["focus", "peer"])
    assert resolution.status_code == 200, resolution.text
    binding = resolution.json()["data"]["context_binding"]
    pool = _eligible(c, binding["workshop_id"])
    snapshots = {row["persona_id"]: row["participant_snapshot"] for row in pool["included"]}
    body = _daily_submission(binding, snapshots)
    # The old institution changes role after freezing; a newer receipt from
    # another tab must not remove the old Management receipt's restrictions.
    rows[2].update({key: copy.deepcopy(rows[1][key]) for key in ("archetype", "mandate", "strategy_family", "metadata")})
    newer = _resolve_persona_entry(c, ids=["focus", "peer"], workshop_id=binding["workshop_id"],
                                  source_kind="workshop")
    assert newer.status_code == 200, newer.text
    assert "peer" in {row["persona_id"] for row in _eligible(c, binding["workshop_id"])["included"]}
    response = c.post("/bff/agora/interactions", json=body,
                      headers={**AUTH, "Idempotency-Key": uuid.uuid4().hex})
    assert response.status_code == 422, response.text
    assert "participant_eligibility_failed" in response.text
    assert interaction_lifecycle.list("pantheon-dev", "interaction-user") == []


@pytest.mark.parametrize("field,value", [
    ("archetype", "agora_servant"), ("mandate", "user_private_agora_servant"),
    ("strategy_family", "agora_servant"), ("persona_class", "agora_servant"),
    ("owner_scope", "user_private"), ("visibility_scope", "private"), ("memory_scope", "private_user"),
])
@pytest.mark.parametrize("location", ["record", "metadata"])
def test_management_compare_uses_canonical_role_and_privacy_fields(monkeypatch, field, value, location):
    c = client(monkeypatch)
    rows = _institutional_cohort(monkeypatch)
    private = rows[1]
    for key in ("archetype", "mandate", "strategy_family", "metadata"):
        private.pop(key, None)
    private["metadata"] = {"agora_user_id": "interaction-user"}
    (private if location == "record" else private["metadata"])[field] = value
    rows[2]["persona_id"] = "agora-servant-name-is-not-authority"
    resolution = _resolve_persona_entry(c)
    pool = _eligible(c, resolution.json()["data"]["workshop_id"])
    assert [row["persona_id"] for row in pool["included"]] == ["focus", "agora-servant-name-is-not-authority"]
    assert "persona_not_institutional" in next(
        row["reasons"] for row in pool["excluded"] if row["persona_id"] == "ordinary-id")


@pytest.mark.parametrize("owners", [{}, {"agora_user_id": "another-user"},
                                     {"agora_user_id": "interaction-user", "user_id": "another-user"}])
def test_private_agora_eligibility_fails_closed_for_missing_or_conflicting_user(monkeypatch, owners):
    c = client(monkeypatch)
    rows = _institutional_cohort(monkeypatch)
    rows[1]["metadata"].pop("agora_user_id")
    rows[1]["metadata"].update(owners)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload())
    pool = _eligible(c, resolved.json()["data"]["workshop_id"])
    assert "persona_user_mismatch" in next(
        row["reasons"] for row in pool["excluded"] if row["persona_id"] == "ordinary-id")


def test_management_compare_rejects_forged_source_route_and_legacy_private_submit(monkeypatch):
    c = client(monkeypatch)
    _institutional_cohort(monkeypatch)
    resolution = _resolve_persona_entry(c)
    binding = resolution.json()["data"]["context_binding"]
    private_submit = c.post("/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": uuid.uuid4().hex}, json={
            "workshop_id": binding["workshop_id"], "mode": "consult", "environment": "research",
            "topic": "Compare", "participant_persona_ids": ["focus", "ordinary-id"],
            "context_refs": [{"type": "persona", "id": "focus"}]})
    assert private_submit.status_code == 422, private_submit.text
    assert "participant_eligibility_failed" in private_submit.text
    resolution = _resolve_persona_entry(c, ids=["focus", "peer"])
    binding = resolution.json()["data"]["context_binding"]
    pool = _eligible(c, binding["workshop_id"])
    body = _daily_submission(binding, {row["persona_id"]: row["participant_snapshot"] for row in pool["included"]})
    body["context_snapshot"]["source_route"] = f"/agora/strategy-workshop/{binding['workshop_id']}"
    body["context_snapshot"]["return_route"] = body["context_snapshot"]["source_route"]
    forged = c.post("/bff/agora/interactions", json=body,
                    headers={**AUTH, "Idempotency-Key": uuid.uuid4().hex})
    assert forged.status_code == 409, forged.text
    assert "context_binding_missing" in forged.text
    assert interaction_lifecycle.list("pantheon-dev", "interaction-user") == []


def test_context_resolution_is_idempotent_and_versioned(monkeypatch):
    c = client(monkeypatch)
    first = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload())
    second = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload())
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["data"] == second.json()["data"]
    assert first.json()["data"]["verified"] is True
    workshop_id = first.json()["data"]["workshop_id"]
    workshop = c.get(f"/bff/agora/workshops/{workshop_id}", headers=AUTH)
    assert workshop.status_code == 200, workshop.text
    session = workshop.json()["data"]
    assert session["strategy_id"] == "strategy-1"
    assert session["active_strategy_spec_registry_id"] == "v1"
    assert session["selected_version_id"] is None
    invalid = c.post("/bff/agora/interactions/context:resolve", headers={**AUTH, "Idempotency-Key": "invalid"},
                     json={"context_refs": [{"type": "strategy", "id": "strategy-1"}]})
    assert invalid.status_code == 422


def test_context_resolution_rejects_strategy_or_registry_version_mismatch(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "pointer-authority"},
        json=context_payload(),
    ).json()["data"]

    for key, strategy_id, version_id in (
        ("wrong-strategy", "strategy-other", "v1"),
        ("wrong-version", "strategy-1", "v2"),
    ):
        response = c.post(
            "/bff/agora/interactions/context:resolve",
            headers={**AUTH, "Idempotency-Key": key},
            json={
                "workshop_id": resolved["workshop_id"],
                "environment": "paper",
                "context_refs": [
                    {"type": "strategy", "id": strategy_id, "version_id": version_id},
                ],
            },
        )
        assert response.status_code == 409, response.text
        assert "strategy_version_mismatch" in response.text


def test_submission_uses_strategy_registry_pointer_not_workshop_version_alias(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "registry-pointer-submit"},
        json=context_payload(),
    ).json()["data"]
    original_get_session = MemoryWorkshopStore.get_session

    def get_session_with_unrelated_workshop_version(self, workshop_id):
        session = original_get_session(self, workshop_id)
        if session is not None and workshop_id == resolved["workshop_id"]:
            session["selected_version_id"] = "workshop-version-unrelated"
        return session

    monkeypatch.setattr(MemoryWorkshopStore, "get_session", get_session_with_unrelated_workshop_version)

    response = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": "registry-pointer-interaction"},
        json={
            "workshop_id": resolved["workshop_id"],
            "mode": "consult",
            "environment": "paper",
            "topic": "Review immutable strategy registry pointer",
            "participant_persona_ids": ["ready"],
            "context_refs": context_payload()["context_refs"],
        },
    )

    assert response.status_code == 202, response.text


def test_eligibility_includes_and_excludes_with_reasons(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload()).json()["data"]
    response = c.post("/bff/agora/interactions/participants:eligible", headers={"Authorization": AUTH["Authorization"]}, json={
        "workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "required_capability": "persona_opinion",
    })
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert [x["persona_id"] for x in body["included"]] == [
        "ready",
        "paper-running",
        "research-only",
        "consultable",
        "paper-owner",
        "live-owner",
    ]
    excluded = {x["persona_id"]: x["reasons"] for x in body["excluded"]}
    assert "persona_not_active" in excluded["draft"]
    assert "persona_not_active" in excluded["frozen"]
    assert "persona_not_active" in excluded["retired"]
    assert "environment_ceiling_exceeded" in excluded["research"]
    assert "tenant_mismatch" in excluded["unscoped"]
    assert "capability_snapshot_unavailable" in excluded["snapshot-missing"]
    assert "required_capability_missing" in excluded["capability-missing"]
    assert "capability_snapshot_persona_mismatch" in excluded["pointer-mismatch"]
    assert "persona_not_active" in excluded["deployed"]
    assert "environment_ceiling_exceeded" in excluded["stage-only"]
    assert "environment_ceiling_exceeded" in excluded["ceiling-unknown"]


def test_unknown_environment_ceiling_fails_closed_for_research(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "unknown-ceiling-context"},
        json={**context_payload(), "environment": "research"},
    ).json()["data"]
    response = c.post(
        "/bff/agora/interactions/participants:eligible",
        headers={"Authorization": AUTH["Authorization"]},
        json={
            "workshop_id": resolved["workshop_id"],
            "mode": "consult",
            "environment": "research",
            "required_capability": "persona_opinion",
        },
    )
    assert response.status_code == 200, response.text
    excluded = {x["persona_id"]: x["reasons"] for x in response.json()["data"]["excluded"]}
    assert "environment_ceiling_exceeded" in excluded["ceiling-unknown"]


def test_typed_submission_is_idempotent_and_has_no_write_authority(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload()).json()["data"]
    payload = {"workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "topic": "Review risk", "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    headers = {**AUTH, "Idempotency-Key": "interaction-1"}
    first = c.post("/bff/agora/interactions", headers=headers, json=payload)
    second = c.post("/bff/agora/interactions", headers=headers, json=payload)
    assert first.status_code == second.status_code == 202, first.text
    assert first.json()["data"] == second.json()["data"]
    assert first.json()["data"]["execution_authority"] == "none"
    AgoraInteractionWorker(
        lifecycle_store=interaction_lifecycle,
        workshop_store=workshop_store,
        read_store=read_store,
    ).run_once()
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    cards = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/cards", headers=AUTH).json()["data"]
    assert len(events) == 3
    assert len([card for card in cards if card["card_type"] == "consult_result"]) == 1


def test_submission_same_key_different_body_conflicts_without_duplicate_side_effects(monkeypatch):
    c = client(monkeypatch)
    key = f"interaction-fingerprint-{uuid.uuid4().hex}"
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"context-{key}"},
        json=context_payload(),
    ).json()["data"]
    body = {"workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "topic": "Review risk", "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    assert c.post("/bff/agora/interactions", headers={**AUTH, "Idempotency-Key": key}, json=body).status_code == 202
    changed = {**body, "topic": "Different request"}
    assert c.post("/bff/agora/interactions", headers={**AUTH, "Idempotency-Key": key}, json=changed).status_code == 409
    AgoraInteractionWorker(
        lifecycle_store=interaction_lifecycle,
        workshop_store=workshop_store,
        read_store=read_store,
    ).run_once()
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    assert [event["event_type"] for event in events] == [
        "opinion_requested", "provider_invocation_failed", "thread_closed"
    ]


def test_partial_side_effect_failure_replays_to_exactly_one_event_set(monkeypatch):
    c = client(monkeypatch)
    key = f"interaction-recovery-{uuid.uuid4().hex}"
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"context-{key}"},
        json=context_payload(),
    ).json()["data"]
    original = MemoryWorkshopStore.create_event
    calls = {"count": 0, "failed": False}

    def fail_after_first(self, event):
        calls["count"] += 1
        if calls["count"] == 2 and not calls["failed"]:
            calls["failed"] = True
            raise RuntimeError("simulated crash after first event")
        return original(self, event)

    monkeypatch.setattr(MemoryWorkshopStore, "create_event", fail_after_first)
    body = {"workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "topic": "Recover synthesis", "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    headers = {**AUTH, "Idempotency-Key": key}
    assert c.post("/bff/agora/interactions", headers=headers, json=body).status_code == 202
    worker = AgoraInteractionWorker(
        lifecycle_store=interaction_lifecycle,
        workshop_store=workshop_store,
        read_store=read_store,
    )
    worker.run_once()
    assert c.post("/bff/agora/interactions:recover", headers=AUTH).status_code == 202
    worker.run_once()
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    assert [event["event_type"] for event in events] == [
        "opinion_requested", "provider_invocation_failed", "thread_closed"
    ]
    cards = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/cards", headers=AUTH).json()["data"]
    assert len([card for card in cards if card["card_type"] == "consult_result"]) == 1


def test_explicit_interaction_id_cannot_be_reused_for_different_content(monkeypatch):
    c = client(monkeypatch)
    suffix = uuid.uuid4().hex
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"context-collision-{suffix}"},
        json=context_payload(),
    ).json()["data"]
    body = {"interaction_id": f"explicit-{suffix}", "workshop_id": resolved["workshop_id"],
        "mode": "consult", "environment": "paper", "topic": "Original topic",
        "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    assert c.post("/bff/agora/interactions", headers={**AUTH, "Idempotency-Key": f"first-{suffix}"}, json=body).status_code == 202
    collision = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": f"second-{suffix}"},
        json={**body, "topic": "Conflicting topic"},
    )
    assert collision.status_code == 409
    AgoraInteractionWorker(
        lifecycle_store=interaction_lifecycle,
        workshop_store=workshop_store,
        read_store=read_store,
    ).run_once()
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    assert len(events) == 3


def test_propose_action_collision_does_not_create_a_dangling_second_proposal(monkeypatch):
    c = client(monkeypatch)
    suffix = uuid.uuid4().hex
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"context-proposal-collision-{suffix}"},
        json=context_payload(),
    ).json()["data"]
    body = {"interaction_id": f"proposal-explicit-{suffix}", "workshop_id": resolved["workshop_id"],
        "mode": "propose_action", "environment": "paper", "topic": "Original measure",
        "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    first = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": f"proposal-first-{suffix}"},
        json=body,
    )
    assert first.status_code == 202, first.text
    proposal_id = first.json()["data"]["proposal_id"]
    collision = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": f"proposal-second-{suffix}"},
        json={**body, "topic": "Conflicting measure"},
    )
    assert collision.status_code == 409
    revisions = c.get(
        f"/bff/agora/proposals/{proposal_id}/revisions",
        headers={"Authorization": AUTH["Authorization"]},
    )
    assert revisions.status_code == 200
    assert len(revisions.json()["data"]) == 1
    AgoraInteractionWorker(
        lifecycle_store=interaction_lifecycle,
        workshop_store=workshop_store,
        read_store=read_store,
    ).run_once()
    cards = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/cards", headers=AUTH).json()["data"]
    assert len([card for card in cards if card["card_type"] == "governed_proposal"]) == 1


def test_propose_action_creates_canonical_governed_proposal_and_card(monkeypatch):
    c = client(monkeypatch)
    read_store.list_approval_decisions = lambda: [
        {
            "decision_id": "approval-risk-valid",
            "state": "decided",
            "outcome": "approved",
            "target_type": "strategy_spec",
            "target_id": "strategy-1",
            "target_version": "v1",
            "tenant_id": "pantheon-dev",
            "owner_user_id": "interaction-user",
            "reviewer": "risk-reviewer",
            "actor_role": "risk_owner",
        },
        {
            "decision_id": "approval-self",
            "state": "decided",
            "outcome": "approved",
            "target_type": "strategy_spec",
            "target_id": "strategy-1",
            "target_version": "v1",
            "tenant_id": "pantheon-dev",
            "owner_user_id": "interaction-user",
            "reviewer": "interaction-user",
            "actor_role": "risk_owner",
        },
        {
            "decision_id": "approval-wrong-target",
            "state": "decided",
            "outcome": "approved",
            "target_type": "strategy_spec",
            "target_id": "strategy-other",
            "target_version": "v1",
            "tenant_id": "tenant-other",
            "owner_user_id": "interaction-user",
            "reviewer": "risk-reviewer",
            "actor_role": "risk_owner",
        },
    ]
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "proposal-context"},
        json=context_payload(),
    ).json()["data"]
    response = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": "proposal-interaction"},
        json={
            "workshop_id": resolved["workshop_id"],
            "mode": "propose_action",
            "environment": "paper",
            "topic": "Reduce risk budget to eight percent",
            "participant_persona_ids": ["ready"],
            "context_refs": context_payload()["context_refs"],
        },
    )

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    proposal = data["proposal"]
    assert data["execution_authority"] == "none"
    assert data["proposal_ref"] == proposal["proposal_id"]
    assert proposal["target_id"] == "strategy-1"
    assert proposal["target_version"] == "v1"
    assert proposal["state"] == "draft"
    assert proposal["execution_authority"] == "none"
    assert proposal["available_approval_decision_refs"] == []
    assert proposal["approval_decision_readiness"]["reason"] == "proposal_not_validated"
    assert proposal["approval_decision_refs_authority"] == "canonical_read_store"

    readback = c.get(
        f"/bff/agora/proposals/{proposal['proposal_id']}",
        headers={"Authorization": AUTH["Authorization"]},
    )
    assert readback.status_code == 200, readback.text
    assert readback.headers["etag"] == data["proposal_etag"]
    assert readback.json()["data"]["available_approval_decision_refs"] == []

    AgoraInteractionWorker(
        lifecycle_store=interaction_lifecycle,
        workshop_store=workshop_store,
        read_store=read_store,
    ).run_once()

    cards = c.get(
        f"/bff/agora/workshops/{resolved['workshop_id']}/cards",
        headers=AUTH,
    ).json()["data"]
    governed = next(card for card in cards if card["card_type"] == "governed_proposal")
    assert governed["payload"]["proposal_id"] == proposal["proposal_id"]
    assert governed["payload"]["approval_refs"] == []
    assert not any(card["card_type"] == "consult_result" for card in cards)


def test_propose_action_requires_strategy_context_and_rejects_injected_refs(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "proposal-no-strategy-context"},
        json={
            "environment": "paper",
            "context_refs": [{"type": "decision_event", "id": "decision-1"}],
        },
    ).json()["data"]
    payload = {
        "workshop_id": resolved["workshop_id"],
        "mode": "propose_action",
        "environment": "paper",
        "topic": "Unbound candidate",
        "participant_persona_ids": ["ready"],
        "context_refs": [{"type": "decision_event", "id": "decision-1"}],
    }
    missing_strategy = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": "proposal-no-strategy"},
        json=payload,
    )
    assert missing_strategy.status_code == 422
    assert "strategy context" in missing_strategy.text

    injected = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": "proposal-injected-ref"},
        json={**payload, "approval_refs": ["payload-controlled"]},
    )
    assert injected.status_code == 422


def test_ineligible_participant_and_cross_tenant_fail_closed(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload()).json()["data"]
    payload = {"workshop_id": resolved["workshop_id"], "mode": "ask", "environment": "paper", "topic": "Explain",
        "participant_persona_ids": ["draft"], "context_refs": context_payload()["context_refs"]}
    assert c.post("/bff/agora/interactions", headers={**AUTH, "Idempotency-Key": "bad-persona"}, json=payload).status_code == 422
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "pantheon-dev")
    response = c.post("/bff/agora/interactions/participants:eligible",
        headers={"Authorization": AUTH["Authorization"], "X-Tenant-Id": "other"},
        json={"workshop_id": resolved["workshop_id"], "mode": "ask"})
    assert response.status_code == 403


def test_synthesis_schema_accepted_by_real_adapter_validator(monkeypatch):
    import sys
    from pathlib import Path

    from services.control_plane.bff.agora.interaction.runner import _SYNTHESIS_SCHEMA

    adapter_dir = str((Path(__file__).resolve().parents[3] / "openclaw-gateway-adapter"))
    monkeypatch.syspath_prepend(adapter_dir)
    import assistant_openclaw_provider as module
    result = {
        "status": "no_consensus", "summary": "s", "agreements": ["a"], "evidence_refs": [],
        "disagreements": [{"opinion_ids": ["o1", "o2"], "cause": "c", "detail": "d"}],
    }
    module._validate_extraction_arguments(result, _SYNTHESIS_SCHEMA)


def test_original_workshop_daily_context_resolve_uses_registry_http_owner(monkeypatch):
    import urllib.error
    from services.control_plane.bff.command_adapters import base as adapter_base
    from services.control_plane.bff.core import owner_reads
    from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort

    c = client(monkeypatch)
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.internal")

    workshop_id = "b06627a9-4711-4baa-be58-23fa9a5400bc"
    strategy_id = "strategy-persona-aa060d53ff712cbba704"
    registry_id = "reg-strategy-spec-persona-aa060d53ff712cbba704"
    persona_id = "persona-132519698e6199e80326"
    tenant_id = "tenant-dev"

    real_port = DefaultResearchKnowledgeSourcePort()
    assert real_port._strategy_specs == {}
    read_store.get_strategy_spec_detail = real_port.get_strategy_spec_detail
    orig_list_personas = read_store.list_personas
    read_store.list_personas = lambda **kw: orig_list_personas(**kw) + [
        {
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "display_name": "Original Workshop Persona",
            "lifecycle_state": "active",
            "environment_ceiling": "paper",
        }
    ]
    workshop_store.create_session(
        {
            "workshop_id": workshop_id,
            "tenant_id": tenant_id,
            "user_id": "interaction-user",
            "strategy_id": strategy_id,
            "active_strategy_spec_registry_id": registry_id,
            "selected_version_id": "v1",
            "status": "open",
        }
    )

    def make_registry_entry(owner_tenant: str = tenant_id, entry_reg_id: str = registry_id) -> dict:
        return {
            "registry_id": entry_reg_id,
            "artifact_type": "strategy_spec",
            "artifact_state": "approved",
            "version": "1.0.0",
            "strategy_id": strategy_id,
            "owner_tenant": owner_tenant,
            "lineage": {
                "source_strategy_spec_id": strategy_id,
                "parent_registry_ids": ["reg-strategy-spec-persona-baseline-aa060d53ff712cbba704"],
            },
            "metadata": {
                "title": "Original Workshop Strategy Spec",
                "persona_id": persona_id,
                "strategy_spec": {
                    "strategy_id": strategy_id,
                    "spec_version": "1.0",
                    "lifecycle_state": "candidate",
                    "title": "Original Workshop Strategy Spec",
                    "metadata": {"tenant_id": owner_tenant, "persona_id": persona_id},
                },
            },
        }

    daily_payload = {
        "workshop_id": workshop_id,
        "environment": "research",
        "source_route": f"/agora/strategy-workshop/{workshop_id}",
        "return_route": f"/agora/strategy-workshop/{workshop_id}",
        "focused_object": {"kind": "workshop", "id": workshop_id, "version": "v1"},
        "evidence_cutoff": "2026-10-18T12:00:00Z",
        "selected_persona_ids": [persona_id],
        "initial_mode": "ask",
        "context_refs": [
            {"type": "workshop", "id": workshop_id, "version_id": "v1"},
            {"type": "strategy", "id": strategy_id, "version_id": registry_id},
        ],
    }
    auth_token = f"Bearer interaction-user:operator:{tenant_id}"
    headers_base = {"Authorization": auth_token, "X-Tenant-Id": tenant_id}

    tok_auth = owner_reads.authorization.set(auth_token)
    tok_tenant = owner_reads.selected_tenant.set(tenant_id)
    try:
        # 1. Owner entry present while BFF local store is empty -> 200 OK
        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [make_registry_entry()])
        ok_resp = c.post(
            "/bff/agora/interactions/context:resolve",
            headers={**headers_base, "Idempotency-Key": "orig-ws-ok"},
            json=daily_payload,
        )
        assert ok_resp.status_code == 200, ok_resp.text
        ok_data = ok_resp.json()["data"]
        assert ok_data["verified"] is True
        assert ok_data["workshop_id"] == workshop_id
        assert ok_data["context_binding"]["strategy_ref"] == {
            "strategy_id": strategy_id,
            "version_id": registry_id,
        }
        assert real_port._strategy_specs == {}

        # 2. Missing entry in Registry -> 409 strategy_not_found
        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [])
        missing_resp = c.post(
            "/bff/agora/interactions/context:resolve",
            headers={**headers_base, "Idempotency-Key": "orig-ws-missing"},
            json=daily_payload,
        )
        assert missing_resp.status_code == 409, missing_resp.text
        assert "strategy_not_found" in missing_resp.text

        # 3. Wrong version in Registry -> 409 strategy_not_found
        monkeypatch.setattr(
            adapter_base,
            "http_request_json",
            lambda *a, **k: [make_registry_entry(entry_reg_id="reg-strategy-spec-other-version")],
        )
        wrong_ver_resp = c.post(
            "/bff/agora/interactions/context:resolve",
            headers={**headers_base, "Idempotency-Key": "orig-ws-wrong-ver"},
            json=daily_payload,
        )
        assert wrong_ver_resp.status_code == 409, wrong_ver_resp.text
        assert "strategy_not_found" in wrong_ver_resp.text

        # 4. Foreign tenant in Registry -> 403 FORBIDDEN
        monkeypatch.setattr(
            adapter_base,
            "http_request_json",
            lambda *a, **k: [make_registry_entry(owner_tenant="tenant-other")],
        )
        forbidden_resp = c.post(
            "/bff/agora/interactions/context:resolve",
            headers={**headers_base, "Idempotency-Key": "orig-ws-forbidden"},
            json=daily_payload,
        )
        assert forbidden_resp.status_code == 403, forbidden_resp.text
        assert forbidden_resp.json()["error"]["code"] == "FORBIDDEN"

        # 5. Registry owner unavailable (503 or untyped 404 route error) -> 503 DEPENDENCY_UNAVAILABLE
        def raise_404(*a, **k):
            raise urllib.error.HTTPError("http://registry.internal", 404, "Not Found", hdrs=None, fp=None)

        monkeypatch.setattr(adapter_base, "http_request_json", raise_404)
        http_404_resp = c.post(
            "/bff/agora/interactions/context:resolve",
            headers={**headers_base, "Idempotency-Key": "orig-ws-http-404"},
            json=daily_payload,
        )
        assert http_404_resp.status_code == 503, http_404_resp.text
        assert http_404_resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

        def raise_503(*a, **k):
            raise urllib.error.HTTPError("http://registry.internal", 503, "Unavailable", hdrs=None, fp=None)

        monkeypatch.setattr(adapter_base, "http_request_json", raise_503)
        unavail_resp = c.post(
            "/bff/agora/interactions/context:resolve",
            headers={**headers_base, "Idempotency-Key": "orig-ws-unavail"},
            json=daily_payload,
        )
        assert unavail_resp.status_code == 503, unavail_resp.text
        assert unavail_resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    finally:
        owner_reads.authorization.reset(tok_auth)
        owner_reads.selected_tenant.reset(tok_tenant)


def test_selected_persona_interaction_freezes_bounded_operating_evidence_before_invocation(monkeypatch):
    import json
    from services.control_plane.bff.agora.interaction.provider import authority_boundary
    from services.control_plane.bff import trade_journal as trade_journal_mod

    c = client(monkeypatch)
    persona_id = "persona-be19a1fac471247f2b2a"
    runtime_id = "rb-677cd34c70a2b20c64b4"
    plan_id = "plan-be19a1fac471247f2b2a"
    artifact_id = "artifact-qinghe-r2-v1"
    pool_id = "pool-paper-qinghe-r2"
    cap_binding_id = "pcb-qinghe-r2"

    orig_list_personas = read_store.list_personas
    read_store.list_personas = lambda **kw: orig_list_personas(**kw) + [
        {
            "persona_id": persona_id,
            "tenant_id": "pantheon-dev",
            "display_name": "青禾 R2",
            "lifecycle_state": "research_only",
            "paper_runtime_state": "paper_running",
            "strategy_family": "momentum",
            "environment_ceiling": "paper",
            "persona_version": "rev-qinghe-2",
            "metadata": {
                "environment_ceiling": "paper",
                "paper_runtime_state": "paper_running",
                "persona_capital_binding_id": cap_binding_id,
                "capital_pool_id": pool_id,
                "runtime_binding_id": runtime_id,
                "strategy_artifact_id": artifact_id,
            },
        }
    ]
    read_store.list_runtime_bindings = lambda: [
        # Foreign tenant binding must be ignored fail-closed
        {
            "runtime_id": "rb-foreign-tenant",
            "tenant_id": "other-tenant",
            "persona_id": persona_id,
            "status": "active",
            "environment": "paper",
        },
        # Matching active paper runtime binding
        {
            "runtime_id": runtime_id,
            "binding_id": runtime_id,
            "tenant_id": "pantheon-dev",
            "persona_id": persona_id,
            "plan_id": plan_id,
            "status": "active",
            "health_status": "healthy",
            "environment": "paper",
            "capital_pool_id": pool_id,
            "persona_capital_binding_id": cap_binding_id,
            "strategy_artifact_id": artifact_id,
            "updated_at": "2026-11-07T08:00:00Z",
            "metadata": {
                "object_store": {
                    "artifact_id": artifact_id,
                    "version": "1.0.0",
                },
                "authoritative_loader_attestation": {
                    "artifact_id": artifact_id,
                    "artifact_sha256": "sha256:exact-qinghe-r2",
                },
            },
        },
    ]
    read_store.read_registry_strategy_artifact_detail = lambda aid: (
        {
            "artifact_id": artifact_id,
            "tenant_id": "pantheon-dev",
            "persona_id": persona_id,
            "version": "1.0.0",
            "parameters": {
                "lookback_bars": 2,
                "momentum_threshold": 0.0,
                "rebalance_interval_bars": 1,
                "target_weight": 0.1,
            },
        }
        if aid == artifact_id
        else None
    )
    read_store.get_telemetry_summary = lambda rid: (
        {
            "runtime_id": runtime_id,
            "tenant_id": "pantheon-dev",
            "persona_id": persona_id,
            "mark_price": 2135.0,
            "mark_as_of": "2026-11-07T08:14:00Z",
            "mark_source_ref": "twse:mark:2330:2026-11-07T08:14:00Z",
            "last_fill": "2026-11-07T08:10:00Z",
            "total_trades": 6,
            "pnl": 915.0,
            "realized_pnl": None,
            "unrealized_pnl": 915.0,
            "return_percent": None,
            "drawdown": 0.012,
            "fill_rate": 1.0,
            "updated_at": "2026-11-07T08:14:00Z",
            "positions": [
                {
                    "symbol": "2330.TWSE",
                    "side": "LONG",
                    "quantity": 6,
                    "avg_entry_price": 1982.5,
                    "mark_price": 2135.0,
                    "mark_as_of": "2026-11-07T08:14:00Z",
                    "unrealized_pnl": 915.0,
                    "realized_pnl": None,
                    "snapshot_ref": "pos-snap-2330-twse-01",
                }
            ],
        }
        if rid == runtime_id
        else None
    )

    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry.internal")
    monkeypatch.setattr(
        trade_journal_mod,
        "_http_call",
        lambda url, data, headers, method="POST": (
            200,
            {
                "projections": [
                    {
                        "trade_episode_id": "ep-2330-open-1",
                        "tenant_id": "pantheon-dev",
                        "persona_id": persona_id,
                        "instrument_id": "2330.TWSE",
                        "side": "LONG",
                        "status": "open",
                        "environment": "paper",
                        "runtime_binding_id": runtime_id,
                        "filled_quantity": 6,
                        "vwap": 1982.5,
                        "mark_price": 2135.0,
                        "mark_as_of": "2026-11-07T08:14:00Z",
                        "realized_pnl": None,
                        "unrealized_pnl": 915.0,
                        "return_percent": None,
                        "order_ids": ["ord-2330-1", "ord-2330-2"],
                        "fill_ids": ["fill-2330-1", "fill-2330-2"],
                        "position_snapshot_refs": ["pos-snap-2330-twse-01"],
                        "entry_at": "2026-11-07T07:30:00Z",
                        "updated_at": "2026-11-07T08:10:00Z",
                    },
                ]
            },
        ),
    )

    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "qinghe-ctx-1"},
        json=context_payload(),
    ).json()["data"]

    submit_resp = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": "qinghe-submit-1"},
        json={
            "workshop_id": resolved["workshop_id"],
            "mode": "ask",
            "environment": "paper",
            "topic": "Summarize your current 2330.TWSE paper position, recent fills, and strategy parameters",
            "participant_persona_ids": [persona_id],
            "context_refs": context_payload()["context_refs"],
        },
    )
    assert submit_resp.status_code == 202, submit_resp.text
    queued_data = submit_resp.json()["data"]
    interaction_id = queued_data["interaction_id"]
    assert queued_data["status"] == "queued"
    assert queued_data["execution_authority"] == "none"

    # Readback BEFORE OpenClaw invocation must already have frozen operating_context
    stored_before = interaction_lifecycle.get(interaction_id, "pantheon-dev", "interaction-user")
    assert stored_before is not None
    assert stored_before["status"] == "queued"
    assert "pos-snap-2330-twse-01" in stored_before["context_snapshot"]["position_risk_snapshot_refs"]
    assert stored_before["context_snapshot"]["journal_ref"] == "ep-2330-open-1"

    frozen_op = stored_before["_operating_context"][persona_id]
    assert frozen_op["schema_version"] == "agora.persona-operating-context.v1"
    assert frozen_op["persona_id"] == persona_id
    assert frozen_op["tenant_id"] == "pantheon-dev"
    assert frozen_op["authority"] == authority_boundary()
    assert frozen_op["lifecycle_and_execution"] == {
        "lifecycle_maturity": "research_only",
        "execution_mode": "paper",
        "operational_state": "paper_running",
        "paper_runtime_state": "paper_running",
    }
    assert frozen_op["strategy_parameters"]["status"] == "bound_artifact_verified"
    assert frozen_op["strategy_parameters"]["artifact_id"] == artifact_id
    assert frozen_op["strategy_parameters"]["parameters"] == {
        "lookback_bars": 2,
        "momentum_threshold": 0.0,
        "rebalance_interval_bars": 1,
        "target_weight": 0.1,
    }
    assert frozen_op["telemetry_and_positions"]["status"] == "partial"
    assert frozen_op["telemetry_and_positions"]["mark_price"] == 2135.0
    assert frozen_op["telemetry_and_positions"]["positions"][0]["symbol"] == "2330.TWSE"
    assert frozen_op["telemetry_and_positions"]["positions"][0]["quantity"] == 6
    assert "realized_pnl" in frozen_op["telemetry_and_positions"]["not_provided_fields"]
    assert "return_percent" in frozen_op["telemetry_and_positions"]["not_provided_fields"]

    assert frozen_op["trade_journal"]["status"] == "partial"
    assert frozen_op["trade_journal"]["episode_count"] == 1
    assert frozen_op["trade_journal"]["open_episode_count"] == 1
    ep = frozen_op["trade_journal"]["episodes"][0]
    assert ep["episode_id"] == "ep-2330-open-1"
    assert ep["symbol"] == "2330.TWSE"
    assert ep["filled_quantity"] == 6
    assert ep["vwap"] == 1982.5
    assert ep["order_ids"] == ["ord-2330-1", "ord-2330-2"]
    assert "realized_pnl" in ep["not_provided_fields"]
    assert "return_percent" in ep["not_provided_fields"]

    # Timeline readback while queued also exposes the frozen operating_context
    timeline = c.get(f"/bff/agora/interactions/{interaction_id}/timeline", headers=AUTH).json()["data"]
    assert timeline[0]["projection_kind"] == "interaction_queued"
    assert timeline[0]["payload"]["operating_context"][persona_id] == frozen_op

    # Mutate live read_store before worker runs to prove worker uses the pre-frozen operating_context
    read_store.get_telemetry_summary = lambda rid: None
    monkeypatch.setattr(
        trade_journal_mod,
        "_http_call",
        lambda url, data, headers, method="POST": (503, {"error": "unavailable"}),
    )

    captured_calls = []

    class RecordingClient:
        def ensure_persona_opinion_agent(self, admission, *, persona_profile):
            return {"execution_authority": "none", "agent_id": admission["agent_id"]}

        def invoke_assistant_provider(
            self,
            *,
            provider,
            mode,
            prompt,
            context_pack,
            operator_id,
            metadata,
            trace_id,
            agent_id=None,
            persona_admission=None,
            idempotency_key=None,
            **kwargs,
        ):
            if (metadata or {}).get("purpose") == "persona_opinion":
                captured_calls.append({
                    "agent_id": agent_id,
                    "prompt": prompt,
                    "context_pack": context_pack,
                    "persona_admission": persona_admission,
                })
                opinion = {
                    "conclusion": "support",
                    "rationale": "Grounded in 2330.TWSE open paper episode (qty 6, mark 2135.0) and lookback_bars=2.",
                    "confidence": 0.85,
                    "uncertainty": ["realized_pnl and return_percent are not_provided in bounded telemetry"],
                    "risks": ["Single-instrument concentration in 2330.TWSE"],
                    "invalidation_conditions": ["2330.TWSE mark falls below vwap 1982.5"],
                    "evidence_refs": [
                        {
                            "ref_type": "journal_entry",
                            "ref_id": "ep-2330-open-1",
                            "version": "2026-11-07T08:10:00Z",
                            "observed_at": "2026-11-07T08:14:00Z",
                            "data_cutoff": "2026-11-07T08:14:00Z",
                            "freshness": "fresh",
                            "summary": "Open 2330.TWSE paper episode with filled_quantity=6",
                        }
                    ],
                    "recommended_measures": [],
                }
                return {
                    "status": "completed",
                    "output": {
                        "request_id": "req-qinghe-1",
                        "agent_id": agent_id,
                        "json_events": [{"item": {"text": json.dumps(opinion)}}],
                    },
                }
            return {
                "status": "completed",
                "output": {
                    "request_id": "synth-qinghe-1",
                    "structured_data": {
                        "status": "consensus",
                        "summary": "Grounded in frozen operating context.",
                        "agreements": ["2330.TWSE position verified"],
                        "disagreements": [],
                        "evidence_refs": ["ep-2330-open-1"],
                    },
                },
            }

    AgoraInteractionWorker(
        lifecycle_store=interaction_lifecycle,
        workshop_store=workshop_store,
        read_store=read_store,
        client_factory=RecordingClient,
    ).run_once()

    assert len(captured_calls) == 1
    sent_pack = captured_calls[0]["context_pack"]
    assert sent_pack["operating_context"] == frozen_op
    assert sent_pack["frozen_persona_profile"]["operating_context"] == frozen_op
    assert "IMMUTABLE_TYPED_CONTEXT.operating_context" in captured_calls[0]["prompt"]
    assert "never invent indicator/MA parameters from strategy_family" in captured_calls[0]["prompt"]


def test_selected_persona_interaction_reports_unavailable_and_not_provided_without_fabricating_ma_params(monkeypatch):
    from services.control_plane.bff import trade_journal as trade_journal_mod

    c = client(monkeypatch)
    persona_id = "persona-9d4fb3f44cbc5e7cb84f"

    orig_list_personas = read_store.list_personas
    read_store.list_personas = lambda **kw: orig_list_personas(**kw) + [
        {
            "persona_id": persona_id,
            "tenant_id": "pantheon-dev",
            "display_name": "衡岳",
            "lifecycle_state": "research_only",
            "paper_runtime_state": "paper_running",
            "strategy_family": "trend_following",
            "environment_ceiling": "paper",
            "metadata": {
                "environment_ceiling": "paper",
                "paper_runtime_state": "paper_running",
            },
        }
    ]
    read_store.list_runtime_bindings = lambda: []
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry.internal")
    monkeypatch.setattr(
        trade_journal_mod,
        "_http_call",
        lambda url, data, headers, method="POST": (503, {"error": "telemetry_down"}),
    )

    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "hengyue-ctx-1"},
        json=context_payload(),
    ).json()["data"]

    submit_resp = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": "hengyue-submit-1"},
        json={
            "workshop_id": resolved["workshop_id"],
            "mode": "challenge",
            "environment": "paper",
            "topic": "Challenge your current parameters and trade history",
            "participant_persona_ids": [persona_id],
            "context_refs": context_payload()["context_refs"],
        },
    )
    assert submit_resp.status_code == 202, submit_resp.text
    interaction_id = submit_resp.json()["data"]["interaction_id"]
    stored = interaction_lifecycle.get(interaction_id, "pantheon-dev", "interaction-user")
    frozen_op = stored["_operating_context"][persona_id]

    assert frozen_op["strategy_parameters"]["status"] == "not_provided"
    assert frozen_op["strategy_parameters"]["parameters"] is None
    assert "Do not infer or fabricate indicator/MA parameters from strategy_family" in frozen_op["strategy_parameters"]["guidance"]
    assert frozen_op["telemetry_and_positions"]["status"] == "unavailable"
    assert frozen_op["telemetry_and_positions"]["positions"] is None
    assert frozen_op["trade_journal"]["status"] == "unavailable"
    assert frozen_op["trade_journal"]["episode_count"] is None
    assert frozen_op["coverage"]["missing_semantics"] == "not_provided_or_unavailable_never_proves_zero_records"

    # Cross-tenant or mismatched persona rows from telemetry owner must be rejected fail-closed
    monkeypatch.setattr(
        trade_journal_mod,
        "_http_call",
        lambda url, data, headers, method="POST": (
            200,
            {
                "projections": [
                    {
                        "trade_episode_id": "ep-foreign-1",
                        "tenant_id": "other-tenant",
                        "persona_id": persona_id,
                        "instrument_id": "2330.TWSE",
                        "status": "open",
                    }
                ]
            },
        ),
    )
    cross_tenant_res = trade_journal_mod.read_persona_operating_episodes(
        persona_id, None, "pantheon-dev"
    )
    assert cross_tenant_res["status"] == "unavailable"
    assert cross_tenant_res["reason"] == "cross_tenant_or_persona_mismatch_rejected"
    assert cross_tenant_res["episodes"] == []


