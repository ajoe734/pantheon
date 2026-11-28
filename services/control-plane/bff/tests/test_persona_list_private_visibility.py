"""Tests for BFF-PERSONA-LIST-USER-PRIVATE-VISIBILITY-20261128.

Verifies:
1. GET /bff/personas drops every record rejected by _persona_fleet_record_visible_to
   for the viewer resolved by _fleet_viewer_user_id before state/archetype filters,
   before paging, and before total / filtered_total / canonical_total / meta.total.
2. Owner sees its own user_private servant; another operator and a viewer-role caller
   do not; a record without owner_scope or visibility_scope stays visible; a failed
   scope resolution hides only private records; no name or id pattern is used.
3. GET /bff/personas/{persona_id} answers a user_private record for a non-owner viewer
   with the exact same 404 RESOURCE_NOT_FOUND body as a missing persona, while the
   owner still gets 200.
4. personaPurpose and listScope projections remain intact and at most one Agora scope
   resolution and no new owner read occur per request.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.agora.identity import scope as agora_scope
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.routes import collection as collection_routes
from services.control_plane.bff.personas.routes import detail as detail_routes
from services.control_plane.bff.personas.routes.collection import build_collection_router
from services.control_plane.bff.personas.routes.common import PersonaRouteContext
from services.control_plane.bff.personas.routes.detail import build_detail_router
from services.control_plane.bff.personas.service import (
    PersonaDirectorySnapshot,
    PersonaService,
)

TENANT_ID = "pantheon-dev"
ADMIN_TEST_USER = "pantheon-dev-admin-test"
OPERATOR_A_USER = "pantheon-dev-operator-a"
OPERATOR_B_USER = "pantheon-dev-operator-b"
PROBE_VIEWER_USER = "pantheon-dev-probe-viewer"

SERVANT_ADMIN_TEST = "agora-servant-799a3ce8c021b91cbb54"
SERVANT_OPERATOR_A = "agora-servant-fbeaee01e8f34c786555"
SERVANT_OPERATOR_B = "agora-servant-4eef59cbdf80a6b9fea0"


def _bff_error(status_code: int, code: ErrorCode, message: str, reason: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code.value, "message": message, "reason": reason, **extra},
    )


def _make_trader_record(persona_id: str, *, state: str = "paper_owner", archetype: str = "momentum") -> Dict[str, Any]:
    return {
        "id": persona_id,
        "persona_id": persona_id,
        "tenant_id": TENANT_ID,
        "name": f"Trader {persona_id}",
        "mandate": "Daily learning paper momentum",
        "lifecycle_state": state,
        "archetype": archetype,
        "strategy_family": "daily_learning_paper_momentum",
        "risk_profile": "conservative",
        "owner": ADMIN_TEST_USER,
        "metadata": {
            "owner": ADMIN_TEST_USER,
            "archetype": archetype,
            "tenant_id": TENANT_ID,
            "risk_level": "low",
            "capital_mode": "paper",
            "strategy_family": "daily_learning_paper_momentum",
            "deployment_stage": "paper",
            "provisioning_status": "active",
            "provisioning_idempotency_key": f"daily-learning-{persona_id}",
        },
        "created_at": "2026-04-15T10:25:01.307508Z",
        "updated_at": "2026-10-16T03:07:38.926165Z",
    }


def _make_private_servant_record(
    persona_id: str,
    agora_user_id: str,
    *,
    lifecycle_state: str = "paper_owner",
    archetype: str = "agora_servant",
    owner_scope: Optional[str] = "user_private",
    visibility_scope: Optional[str] = "private",
    name: str = "Agora Servant",
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "persona_class": "agora_servant",
        "agora_user_id": agora_user_id,
        "execution_authority": "none",
        "capital_binding_allowed": False,
        "paper_execution_allowed": False,
        "live_execution_allowed": False,
    }
    if owner_scope is not None:
        meta["owner_scope"] = owner_scope
    if visibility_scope is not None:
        meta["visibility_scope"] = visibility_scope
    return {
        "id": persona_id,
        "persona_id": persona_id,
        "tenant_id": TENANT_ID,
        "name": name,
        "mandate": "user_private_agora_servant",
        "lifecycle_state": lifecycle_state,
        "archetype": archetype,
        "strategy_family": "agora_servant",
        "risk_profile": "conservative",
        "owner": agora_user_id,
        "metadata": meta,
        "created_at": "2026-04-14T16:17:04.142423Z",
        "updated_at": "2026-04-14T16:17:04.142423Z",
    }


def _identity_for_header(authorization: Optional[str]) -> Any:
    raw = str(authorization or "").strip()
    if raw.startswith("Bearer "):
        raw = raw[len("Bearer ") :].strip()
    if raw == "viewer-role":
        return SimpleNamespace(operator_id=PROBE_VIEWER_USER, tenant_id=TENANT_ID, roles=["viewer"])
    if raw == "broken-scope":
        # Missing operator_id causes resolve_agora_user_scope to raise AgoraScopeResolutionError
        return SimpleNamespace(operator_id="", tenant_id=TENANT_ID, roles=["viewer"])
    user = raw or ADMIN_TEST_USER
    return SimpleNamespace(operator_id=user, tenant_id=TENANT_ID, roles=["operator"])


def _build_test_client(
    monkeypatch: pytest.MonkeyPatch,
    records: List[Dict[str, Any]],
) -> tuple[TestClient, Dict[str, int]]:
    counts = {
        "directory_reads": 0,
        "detail_reads": 0,
        "scope_resolutions": 0,
    }
    records_by_id = {r["id"]: copy.deepcopy(r) for r in records}
    snapshot = PersonaDirectorySnapshot(
        tenant_id=TENANT_ID,
        snapshot_at="2026-11-28T00:00:00Z",
        records_by_id=records_by_id,
        catalog_defaults_by_id={"cat-1": {"id": "cat-1"}},
    )

    def _read_directory(tenant_id: str, **kwargs: Any) -> PersonaDirectorySnapshot:
        counts["directory_reads"] += 1
        return snapshot

    def _read_detail(persona_id: str, caller_tenant: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        counts["detail_reads"] += 1
        rec = records_by_id.get(persona_id)
        return copy.deepcopy(rec) if rec is not None else None

    real_resolve_scope = agora_scope.resolve_agora_user_scope

    def _counted_resolve_scope(*args: Any, **kwargs: Any) -> Any:
        counts["scope_resolutions"] += 1
        return real_resolve_scope(*args, **kwargs)

    monkeypatch.setattr(personas_service, "_routed_strategy_counts_by_persona", lambda: {})
    monkeypatch.setattr(collection_routes, "_get_persona_directory_snapshot", _read_directory)
    monkeypatch.setattr(
        collection_routes,
        "_bff_me_tenant_payload",
        lambda identity, requested_tenant=None: {"id": TENANT_ID},
    )
    monkeypatch.setattr(detail_routes, "_get_persona_record_for_detail", _read_detail)
    monkeypatch.setattr(
        detail_routes,
        "_bff_me_tenant_payload",
        lambda identity, requested_tenant=None: {"id": TENANT_ID},
    )
    monkeypatch.setattr(detail_routes, "_resolve_active_runtime_binding_overlay", lambda raw: None)
    monkeypatch.setattr(detail_routes, "_routed_strategies_for_persona", lambda pid: 0)
    monkeypatch.setattr(agora_scope, "resolve_agora_user_scope", _counted_resolve_scope)

    service = PersonaService(
        read_store=object(),  # type: ignore[arg-type]
        command_store=object(),  # type: ignore[arg-type]
        write_owner=object(),  # type: ignore[arg-type]
        ranking_write_owner=object(),
    )
    monkeypatch.setattr(service, "get_persona_containment", lambda pid: None)
    monkeypatch.setattr(service, "read_strategy_pool", lambda *a, **kw: ({"current": None}, {}))
    monkeypatch.setattr(service, "read_persona_cycle", lambda *a, **kw: ({"dayStatus": None}, {}))

    ctx = PersonaRouteContext(
        service=service,
        read_store=None,
        command_store=None,
        ranking_write_owner=None,
        write_owner=None,
        extract_identity=_identity_for_header,
        require_read_role=lambda _identity: None,
        require_operator_role=lambda _identity: None,
        bff_error=_bff_error,
        utc_now=lambda: "2026-11-28T00:00:00Z",
        page_slice=lambda items, token, size: (
            items[int(token or 0) : int(token or 0) + size],
            str(int(token or 0) + size) if int(token or 0) + size < len(items) else None,
        ),
        snapshot_meta=lambda ts: {"snapshot_at": ts},
        dataset_surface_status=lambda *a, **kw: {"status": "ok"},
        read_surface_meta=lambda _ds, _surface, *, snapshot_at, total=None, **kw: (
            {"status": "ok", "snapshot_at": snapshot_at, "total": total}
            if total is not None
            else {"status": "ok", "snapshot_at": snapshot_at}
        ),
        raise_if_read_surface_unavailable=lambda *a, **kw: None,
        reject_body_idempotency_key=lambda _p: None,
        resolve_final_idempotency_key=lambda k: str(k or "idem-1"),
        submit_persona_action=None,
    )
    app = FastAPI()
    app.include_router(build_collection_router(ctx))
    app.include_router(build_detail_router(ctx))
    return TestClient(app), counts


def test_list_personas_enforces_user_private_visibility_before_filters_paging_and_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    traders = [
        _make_trader_record("persona-2448c7053e33ea48da47"),
        _make_trader_record("persona-ee7112a829d38b9135ac"),
        _make_trader_record("persona-6507750312870d03a241"),
    ]
    # Unscoped record with servant-like id/name but neither owner_scope nor visibility_scope stays visible.
    unscoped_servant_shape = _make_private_servant_record(
        "agora-servant-unscoped-public",
        OPERATOR_A_USER,
        owner_scope=None,
        visibility_scope=None,
    )
    # Non-servant id/name with visibility_scope="private" is still gated by owner (no id/name pattern).
    private_non_servant_id = {
        **_make_trader_record("persona-private-alpha-01"),
        "metadata": {
            **_make_trader_record("persona-private-alpha-01")["metadata"],
            "visibility_scope": "private",
            "agora_user_id": OPERATOR_B_USER,
        },
    }
    records = [
        *traders,
        _make_private_servant_record(SERVANT_ADMIN_TEST, ADMIN_TEST_USER),
        _make_private_servant_record(SERVANT_OPERATOR_A, OPERATOR_A_USER),
        _make_private_servant_record(SERVANT_OPERATOR_B, OPERATOR_B_USER),
        unscoped_servant_shape,
        private_non_servant_id,
    ]
    client, counts = _build_test_client(monkeypatch, records)

    # 1. admintest sees its own servant, the 3 traders, and the unscoped record,
    #    and sees neither operator-a's nor operator-b's private records.
    resp_admin = client.get(
        "/bff/personas?page_size=200",
        headers={"Authorization": f"Bearer {ADMIN_TEST_USER}"},
    )
    assert resp_admin.status_code == 200
    body_admin = resp_admin.json()
    ids_admin = [r["id"] for r in body_admin["data"]]
    assert ids_admin == [
        "persona-2448c7053e33ea48da47",
        "persona-ee7112a829d38b9135ac",
        "persona-6507750312870d03a241",
        SERVANT_ADMIN_TEST,
        "agora-servant-unscoped-public",
    ]
    assert SERVANT_OPERATOR_A not in ids_admin
    assert SERVANT_OPERATOR_B not in ids_admin
    assert "persona-private-alpha-01" not in ids_admin
    assert body_admin["page_info"]["total"] == 5
    assert body_admin["page_info"]["filtered_total"] == 5
    assert body_admin["page_info"]["canonical_total"] == 5
    assert body_admin["meta"]["total"] == 5
    assert counts["directory_reads"] == 1
    assert counts["scope_resolutions"] == 1

    # Verify personaPurpose and listScope projections remain intact on visible rows.
    by_id_admin = {r["id"]: r for r in body_admin["data"]}
    assert by_id_admin[SERVANT_ADMIN_TEST]["personaPurpose"] == "advisory_assistant"
    assert by_id_admin[SERVANT_ADMIN_TEST]["listScope"] == "current"
    assert by_id_admin["persona-ee7112a829d38b9135ac"]["personaPurpose"] == "trading"
    assert by_id_admin["persona-ee7112a829d38b9135ac"]["listScope"] == "current"

    # 2. Another operator (operator-a) sees only its own servant + public records.
    resp_op_a = client.get(
        "/bff/personas?page_size=200",
        headers={"Authorization": f"Bearer {OPERATOR_A_USER}"},
    )
    assert resp_op_a.status_code == 200
    body_op_a = resp_op_a.json()
    ids_op_a = [r["id"] for r in body_op_a["data"]]
    assert ids_op_a == [
        "persona-2448c7053e33ea48da47",
        "persona-ee7112a829d38b9135ac",
        "persona-6507750312870d03a241",
        SERVANT_OPERATOR_A,
        "agora-servant-unscoped-public",
    ]
    assert SERVANT_ADMIN_TEST not in ids_op_a
    assert SERVANT_OPERATOR_B not in ids_op_a
    assert body_op_a["page_info"]["total"] == 5
    assert body_op_a["page_info"]["canonical_total"] == 5

    # 3. A viewer-role caller (probe viewer) sees no user's private servant.
    resp_viewer = client.get(
        "/bff/personas?page_size=200",
        headers={"Authorization": "Bearer viewer-role"},
    )
    assert resp_viewer.status_code == 200
    body_viewer = resp_viewer.json()
    ids_viewer = [r["id"] for r in body_viewer["data"]]
    assert ids_viewer == [
        "persona-2448c7053e33ea48da47",
        "persona-ee7112a829d38b9135ac",
        "persona-6507750312870d03a241",
        "agora-servant-unscoped-public",
    ]
    assert body_viewer["page_info"]["total"] == 4
    assert body_viewer["page_info"]["filtered_total"] == 4
    assert body_viewer["page_info"]["canonical_total"] == 4
    assert body_viewer["meta"]["total"] == 4

    # 4. Failed scope resolution fails closed to empty viewer and hides only private records.
    resp_broken = client.get(
        "/bff/personas?page_size=200",
        headers={"Authorization": "Bearer broken-scope"},
    )
    assert resp_broken.status_code == 200
    body_broken = resp_broken.json()
    assert [r["id"] for r in body_broken["data"]] == [
        "persona-2448c7053e33ea48da47",
        "persona-ee7112a829d38b9135ac",
        "persona-6507750312870d03a241",
        "agora-servant-unscoped-public",
    ]
    assert body_broken["page_info"]["canonical_total"] == 4
    assert body_broken["page_info"]["total"] == 4

    # 5. Visibility filtering runs before archetype/state filters and paging.
    resp_filtered = client.get(
        "/bff/personas?archetype=agora_servant&page_size=1",
        headers={"Authorization": f"Bearer {ADMIN_TEST_USER}"},
    )
    assert resp_filtered.status_code == 200
    body_filtered = resp_filtered.json()
    # Visible agora_servant rows for admintest: SERVANT_ADMIN_TEST and agora-servant-unscoped-public (2 total),
    # while canonical_total is all 5 records visible to admintest across all archetypes.
    assert [r["id"] for r in body_filtered["data"]] == [SERVANT_ADMIN_TEST]
    assert body_filtered["page_info"]["total"] == 2
    assert body_filtered["page_info"]["filtered_total"] == 2
    assert body_filtered["page_info"]["canonical_total"] == 5
    assert body_filtered["page_info"]["next_page_token"] == "1"


def test_get_persona_detail_hides_other_users_private_servant_with_identical_404_and_allows_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _make_trader_record("persona-ee7112a829d38b9135ac"),
        _make_private_servant_record(SERVANT_ADMIN_TEST, ADMIN_TEST_USER),
        _make_private_servant_record(SERVANT_OPERATOR_A, OPERATOR_A_USER),
    ]
    client, counts = _build_test_client(monkeypatch, records)

    # Missing persona baseline 404 body for comparison.
    missing_resp = client.get(
        f"/bff/personas/{SERVANT_OPERATOR_A}",
        headers={"Authorization": f"Bearer {ADMIN_TEST_USER}"},
    )
    real_missing_resp = client.get(
        "/bff/personas/nonexistent-persona-id",
        headers={"Authorization": f"Bearer {ADMIN_TEST_USER}"},
    )
    assert missing_resp.status_code == 404
    assert real_missing_resp.status_code == 404
    assert missing_resp.json()["detail"]["code"] == ErrorCode.RESOURCE_NOT_FOUND.value
    assert missing_resp.json()["detail"]["message"] == real_missing_resp.json()["detail"]["message"] == "Persona not found"
    assert missing_resp.json()["detail"]["reason"] == f"Persona {SERVANT_OPERATOR_A} does not exist"

    # Viewer-role caller and broken-scope caller also receive 404 for private servant.
    viewer_resp = client.get(
        f"/bff/personas/{SERVANT_ADMIN_TEST}",
        headers={"Authorization": "Bearer viewer-role"},
    )
    assert viewer_resp.status_code == 404
    assert viewer_resp.json()["detail"] == {
        "code": ErrorCode.RESOURCE_NOT_FOUND.value,
        "message": "Persona not found",
        "reason": f"Persona {SERVANT_ADMIN_TEST} does not exist",
    }

    broken_resp = client.get(
        f"/bff/personas/{SERVANT_ADMIN_TEST}",
        headers={"Authorization": "Bearer broken-scope"},
    )
    assert broken_resp.status_code == 404

    # Owner still receives 200 with at most one scope resolution and one detail read per request.
    counts["detail_reads"] = 0
    counts["scope_resolutions"] = 0
    owner_resp = client.get(
        f"/bff/personas/{SERVANT_ADMIN_TEST}",
        headers={"Authorization": f"Bearer {ADMIN_TEST_USER}"},
    )
    assert owner_resp.status_code == 200
    assert owner_resp.json()["data"]["id"] == SERVANT_ADMIN_TEST
    assert counts["detail_reads"] == 1
    assert counts["scope_resolutions"] == 1


def test_persona_purpose_and_list_scope_rows_from_day_probe_task_unchanged_for_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.control_plane.bff.tests.test_list_scope_classification import (
        RECORDED_ADVISORY_RISK_OFFICER,
        RECORDED_AGORA_SERVANT,
        RECORDED_LIVE_PAPER_OWNER,
        RECORDED_OLDER_PROBE_BASELINE_NO_DATA_SCOPE,
        RECORDED_OLDER_RELEASE_PROBE_KEY_ONLY_NO_DATA_SCOPE,
        RECORDED_PROBE_WITH_DATA_SCOPE,
        RECORDED_RESEARCH_HANDOFF_DRAFT_PERSONA,
        RECORDED_RETIRED_FORMER_LIVE_PERSONA,
    )

    servant = copy.deepcopy(RECORDED_AGORA_SERVANT)
    servant.update(
        id=SERVANT_ADMIN_TEST,
        persona_id=SERVANT_ADMIN_TEST,
        lifecycle_state="paper_owner",
        owner=ADMIN_TEST_USER,
    )
    servant["metadata"]["owner_scope"] = "user_private"
    servant["metadata"]["agora_user_id"] = ADMIN_TEST_USER
    other_traders = []
    for pid in ("persona-2448c7053e33ea48da47", "persona-6507750312870d03a241"):
        trader = copy.deepcopy(RECORDED_LIVE_PAPER_OWNER)
        trader.update(id=pid, persona_id=pid)
        other_traders.append(trader)
    recorded_rows = [
        copy.deepcopy(RECORDED_OLDER_PROBE_BASELINE_NO_DATA_SCOPE),
        copy.deepcopy(RECORDED_OLDER_RELEASE_PROBE_KEY_ONLY_NO_DATA_SCOPE),
        copy.deepcopy(RECORDED_PROBE_WITH_DATA_SCOPE),
        copy.deepcopy(RECORDED_ADVISORY_RISK_OFFICER),
        copy.deepcopy(RECORDED_AGORA_SERVANT),
        copy.deepcopy(RECORDED_LIVE_PAPER_OWNER),
        copy.deepcopy(RECORDED_RETIRED_FORMER_LIVE_PERSONA),
        copy.deepcopy(RECORDED_RESEARCH_HANDOFF_DRAFT_PERSONA),
        servant,
        *other_traders,
    ]
    expected_scopes = [
        "test",
        "test",
        "test",
        "current",
        "current",
        "current",
        "retired",
        "research",
        "current",
        "current",
        "current",
    ]
    expected_purposes = [
        "trading",
        "trading",
        "trading",
        "advisory_assistant",
        "advisory_assistant",
        "trading",
        "trading",
        "unknown",
        "advisory_assistant",
        "trading",
        "trading",
    ]
    client, _ = _build_test_client(monkeypatch, recorded_rows)
    resp = client.get(
        "/bff/personas?page_size=50",
        headers={"Authorization": f"Bearer {ADMIN_TEST_USER}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [row["id"] for row in body["data"]] == [r["id"] for r in recorded_rows]
    assert [row["listScope"] for row in body["data"]] == expected_scopes
    assert [row["personaPurpose"] for row in body["data"]] == expected_purposes

