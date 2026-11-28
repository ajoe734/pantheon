"""Tests for BFF-PERSONA-ARTIFACT-LIST-SCOPE-20261125.

Verifies:
1. Each GET /bff/personas row has listScope ("current" | "test" | "retired" | "research")
   derived from recorded hosted row shapes, never from name, with default row set,
   order, and paging unchanged.
2. successRate is None when unrecorded or 0.0; create and PATCH write success_rate
   only when the caller sent it; all four projections and the league row agree.
3. Each GET /bff/artifacts row has list_scope ("current" | "test" | "retired" | "unbound")
   for registry rows and "research" for research run outputs, joined via one bounded
   read per page through the persona directory lookup while get_strategy_labels keeps
   its exact present shape.
4. When the persona directory cannot be read, every artifact row has list_scope "unknown"
   and meta reports persona_directory unavailable without dropping any row.
"""
from __future__ import annotations

import copy
import io
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.core.owner_reads import (
    authorization,
    request_read_scope,
    selected_tenant,
)
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.personas import fleet_classification as fleet_cls
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.routes.collection import build_collection_router
from services.control_plane.bff.personas.routes.common import PersonaRouteContext
from services.control_plane.bff.personas.service import (
    PersonaDirectorySnapshot,
    PersonaService,
    ProvisioningRecord,
    deterministic_provisioning_ids,
)
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
)
from services.control_plane.bff.research.router import create_research_router
from services.control_plane.bff.research.service import ResearchRouterService
from services.runtime_auth_inbound import encode_jwt_hs256


TENANT_ID = "pantheon-dev"


def _test_jwt(tenant: str = TENANT_ID) -> str:
    return encode_jwt_hs256(
        {
            "sub": "op-1",
            "tenant": tenant,
            "tenant_id": tenant,
            "roles": ["operator"],
            "exp": 4102444800,
        },
        secret="list-scope-test-secret",
    )


# Recorded hosted persona shapes from hosted dev audit (mgmt-registry-group-audit-20261125
# and acceptance/fixtures/hosted_persona_day_20261118.json).
RECORDED_OLDER_PROBE_BASELINE_NO_DATA_SCOPE: Dict[str, Any] = {
    "id": "persona-d57f6d76254c20418a9e",
    "persona_id": "persona-d57f6d76254c20418a9e",
    "tenant_id": TENANT_ID,
    "name": "Pantheon Dev Paper Baseline 3",
    "mandate": "Continuous dev paper trading baseline persona after #3065",
    "lifecycle_state": "retired",
    "archetype": "momentum",
    "strategy_family": "dev_paper_baseline",
    "risk_profile": "moderate",
    "owner": "pantheon-dev-admin-test",
    "metadata": {
        "notes": "Continuous dev paper trading baseline persona after #3065",
        "owner": "pantheon-dev-admin-test",
        "archetype": "momentum",
        "tenant_id": TENANT_ID,
        "risk_level": "medium",
        "capital_mode": "paper",
        "retired_reason": "Superseded by continuous baseline persona-ee7112a829d38b9135ac",
        "strategy_family": "dev_paper_baseline",
        "deployment_stage": "paper",
        "paper_Write_path": "paper_Write_only",
        "provisioning_status": "retired",
        "provisioning_idempotency_key": "dev-paper-baseline-20260711-v3",
    },
    "created_at": "2026-07-11T08:06:16.516241Z",
    "updated_at": "2026-10-16T02:27:47.177393Z",
}

RECORDED_OLDER_RELEASE_PROBE_KEY_ONLY_NO_DATA_SCOPE: Dict[str, Any] = {
    "id": "persona-21cdbed183c80cf371df",
    "persona_id": "persona-21cdbed183c80cf371df",
    "tenant_id": TENANT_ID,
    "name": "Pantheon Dev Paper Release 34596927306 Attempt 1",
    "mandate": "Continuous dev paper trading release gate for run 34596927306 attempt 1",
    "lifecycle_state": "retired",
    "archetype": "momentum",
    "risk_profile": "moderate",
    "owner": "pantheon-dev-operator-a",
    "metadata": {
        "notes": "Continuous dev paper trading release gate for run 34596927306 attempt 1",
        "owner": "pantheon-dev-operator-a",
        "archetype": "momentum",
        "tenant_id": TENANT_ID,
        "risk_level": "medium",
        "capital_mode": "paper",
        "retired_reason": "Dev paper release verification case cleanup (PANTHEON-DEV-PAPER-CASE-HYGIENE-20261108)",
        "deployment_stage": "paper",
        "provisioning_status": "retired",
        "provisioning_idempotency_key": "dev-paper-release-34596927306-1-1776152936",
    },
    "created_at": "2026-04-14T07:48:58.659656Z",
    "updated_at": "2026-11-08T16:40:27.204388Z",
}

RECORDED_PROBE_WITH_DATA_SCOPE: Dict[str, Any] = {
    "id": "persona-d64835a203ce276b8fbb",
    "persona_id": "persona-d64835a203ce276b8fbb",
    "tenant_id": TENANT_ID,
    "name": "Pantheon Dev Paper Release 43688794313 Attempt 1",
    "mandate": "Continuous dev paper trading release gate for run 43688794313 attempt 1",
    "lifecycle_state": "research_only",
    "state": "paper_running",
    "status": "paper_running",
    "archetype": "momentum",
    "strategy_family": "dev_paper_baseline",
    "risk_profile": "moderate",
    "owner": "pantheon-dev-admin-test",
    "metadata": {
        "notes": "Continuous dev paper trading release gate for run 43688794313 attempt 1",
        "owner": "pantheon-dev-admin-test",
        "archetype": "momentum",
        "tenant_id": TENANT_ID,
        "data_scope": "test",
        "risk_level": "medium",
        "success_rate": 0.0,
        "capital_mode": "paper",
        "strategy_family": "dev_paper_baseline",
        "deployment_stage": "paper",
        "provisioning_status": "active",
        "provisioning_idempotency_key": "dev-paper-release-43688794313-1-1776759469",
    },
    "created_at": "2026-11-21T08:17:49.555436Z",
    "updated_at": "2026-11-21T08:18:22.411200Z",
}

RECORDED_ADVISORY_RISK_OFFICER: Dict[str, Any] = {
    "id": "persona-risk-officer",
    "persona_id": "persona-risk-officer",
    "tenant_id": TENANT_ID,
    "name": "Risk Officer",
    "mandate": "Independent risk reviewer. Never trades. Reviews trial admission, envelope publication and every harness patch; may veto.",
    "lifecycle_state": "draft",
    "archetype": "risk_officer",
    "strategy_family": "risk_governance",
    "risk_profile": "conservative",
    "consult_policy_id": "policy-consult-panel-v1",
    "owner": "operator-bff",
    "metadata": {
        "trading": False,
        "panel_role": "risk_reviewer",
        "consultable": True,
        "persona_class": "consult_panel",
    },
    "created_at": "2026-04-12T10:33:56.826668Z",
    "updated_at": "2026-04-19T14:52:47.185699Z",
}

RECORDED_AGORA_SERVANT: Dict[str, Any] = {
    "id": "agora-servant-fbeaee01e8f34c786555",
    "persona_id": "agora-servant-fbeaee01e8f34c786555",
    "tenant_id": TENANT_ID,
    "name": "Agora Servant",
    "mandate": "user_private_agora_servant",
    "lifecycle_state": "research_only",
    "archetype": "agora_servant",
    "strategy_family": "agora_servant",
    "risk_profile": "conservative",
    "owner": "pantheon-dev-operator-a",
    "metadata": {
        "persona_class": "agora_servant",
        "agora_user_id": "pantheon-dev-operator-a",
        "execution_authority": "none",
        "capital_binding_allowed": False,
        "paper_execution_allowed": False,
        "live_execution_allowed": False,
    },
    "created_at": "2026-04-14T16:17:04.142423Z",
    "updated_at": "2026-04-14T16:17:04.142423Z",
}

RECORDED_LIVE_PAPER_OWNER: Dict[str, Any] = {
    "id": "persona-ee7112a829d38b9135ac",
    "persona_id": "persona-ee7112a829d38b9135ac",
    "tenant_id": TENANT_ID,
    "name": "衡岳｜0050 台股 Paper 學習 R2",
    "mandate": "以 0050.TW 進行每日 paper 動能與均值回歸雙模式學習。",
    "lifecycle_state": "paper_owner",
    "archetype": "momentum",
    "strategy_family": "daily_learning_paper_momentum",
    "risk_profile": "conservative",
    "owner": "pantheon-dev-admin-test",
    "metadata": {
        "owner": "pantheon-dev-admin-test",
        "archetype": "momentum",
        "tenant_id": TENANT_ID,
        "risk_level": "low",
        "success_rate": 0.0,
        "capital_mode": "paper",
        "strategy_family": "daily_learning_paper_momentum",
        "deployment_stage": "paper",
        "provisioning_status": "active",
        "provisioning_idempotency_key": "daily-learning-paper-0050-r2",
    },
    "created_at": "2026-04-15T10:25:01.307508Z",
    "updated_at": "2026-10-16T03:07:38.926165Z",
}

RECORDED_RETIRED_FORMER_LIVE_PERSONA: Dict[str, Any] = {
    "id": "persona-be19a1fac471247f2b2a",
    "persona_id": "persona-be19a1fac471247f2b2a",
    "tenant_id": TENANT_ID,
    "name": "青禾｜2330 台股 Paper 學習 R2",
    "mandate": "以 2330.TW 進行每日 paper 基本面與技術面學習。",
    "lifecycle_state": "retired",
    "archetype": "momentum",
    "strategy_family": "daily_learning_paper_momentum",
    "risk_profile": "conservative",
    "owner": "pantheon-dev-operator-a",
    "metadata": {
        "owner": "pantheon-dev-operator-a",
        "archetype": "momentum",
        "tenant_id": TENANT_ID,
        "risk_level": "low",
        "success_rate": 0.0,
        "capital_mode": "paper",
        "retired_reason": "Retired during R2 fleet consolidation",
        "strategy_family": "daily_learning_paper_momentum",
        "deployment_stage": "paper",
        "provisioning_status": "retired",
        "provisioning_idempotency_key": "daily-learning-paper-2330-r2",
    },
    "created_at": "2026-04-15T10:24:55.552908Z",
    "updated_at": "2026-10-16T02:27:47.177393Z",
}

RECORDED_RESEARCH_HANDOFF_DRAFT_PERSONA: Dict[str, Any] = {
    "id": "persona-strat-tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19-60f9c8a78715",
    "persona_id": "persona-strat-tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19-60f9c8a78715",
    "tenant_id": TENANT_ID,
    "name": "Source15 2330 Alpha research handoff",
    "mandate": "接收本次原 Alpha first-pass 結果供研究教學",
    "lifecycle_state": "draft",
    "archetype": "quant_alpha",
    "risk_profile": "moderate",
    "owner": "pantheon-dev-training-session-persona-authority",
    "metadata": {
        "source_id": "tw_price_daily",
        "symbol": "2330",
    },
    "created_at": "2026-04-16T12:12:41.584476Z",
    "updated_at": "2026-04-16T12:12:41.584476Z",
}


def test_persona_list_scope_recorded_shapes_and_never_from_name() -> None:
    assert fleet_cls.classify_persona_list_scope(RECORDED_OLDER_PROBE_BASELINE_NO_DATA_SCOPE) == "test"
    assert fleet_cls.classify_persona_list_scope(RECORDED_OLDER_RELEASE_PROBE_KEY_ONLY_NO_DATA_SCOPE) == "test"
    assert fleet_cls.classify_persona_list_scope(RECORDED_PROBE_WITH_DATA_SCOPE) == "test"
    assert fleet_cls.classify_persona_list_scope(RECORDED_ADVISORY_RISK_OFFICER) == "current"
    assert fleet_cls.classify_persona_list_scope(RECORDED_AGORA_SERVANT) == "current"
    assert fleet_cls.classify_persona_list_scope(RECORDED_LIVE_PAPER_OWNER) == "current"
    assert fleet_cls.classify_persona_list_scope(RECORDED_RETIRED_FORMER_LIVE_PERSONA) == "retired"
    assert fleet_cls.classify_persona_list_scope(RECORDED_RESEARCH_HANDOFF_DRAFT_PERSONA) == "research"

    # Name alone must never classify a persona as "test".
    deceptive_name_live = copy.deepcopy(RECORDED_LIVE_PAPER_OWNER)
    deceptive_name_live["id"] = "persona-name-check"
    deceptive_name_live["persona_id"] = "persona-name-check"
    deceptive_name_live["name"] = "Pantheon Dev Paper Release Test Baseline Probe"
    assert fleet_cls.classify_persona_list_scope(deceptive_name_live) == "current"


def test_get_bff_personas_exposes_list_scope_and_preserves_row_set_order_and_paging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Replay the 2026-11-27 audited owner identity facts on recorded owner shapes.
    # Unrelated older owner fields are representative; purpose is never injected.
    # Paper ownership must not override the servant's canonical identity metadata.
    servant = copy.deepcopy(RECORDED_AGORA_SERVANT)
    servant.update(id="agora-servant-799a3ce8c021b91cbb54",
                   persona_id="agora-servant-799a3ce8c021b91cbb54",
                   lifecycle_state="paper_owner")
    servant["metadata"]["owner_scope"] = "user_private"
    # This test exercises the owner's unchanged row set; non-owner visibility
    # is covered by test_persona_list_private_visibility.py.
    servant["metadata"]["agora_user_id"] = "op-1"
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
    monkeypatch.setattr(personas_service, "_routed_strategy_counts_by_persona", lambda: {})

    projected_rows = personas_service._project_persona_list_records(copy.deepcopy(recorded_rows))
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
    expected_purposes = ["trading", "trading", "trading", "advisory_assistant",
                         "advisory_assistant", "trading", "trading", "unknown",
                         "advisory_assistant", "trading", "trading"]
    assert [r["id"] for r in projected_rows] == [r["id"] for r in recorded_rows]
    assert [r["listScope"] for r in projected_rows] == expected_scopes

    # Wire GET /bff/personas collection route and verify row set, order, and paging are unchanged.
    snapshot = PersonaDirectorySnapshot(
        tenant_id=TENANT_ID,
        snapshot_at="2026-11-25T00:00:00Z",
        records_by_id={r["id"]: copy.deepcopy(r) for r in recorded_rows},
        catalog_defaults_by_id={},
    )
    snapshot_reads = []

    def read_snapshot(tenant_id, **kw):
        snapshot_reads.append(tenant_id)
        return snapshot

    monkeypatch.setattr(
        "services.control_plane.bff.personas.routes.collection._get_persona_directory_snapshot",
        read_snapshot,
    )
    monkeypatch.setattr(
        "services.control_plane.bff.personas.routes.collection._bff_me_tenant_payload",
        lambda identity, requested_tenant=None: {"id": TENANT_ID},
    )

    service = PersonaService(
        read_store=object(),  # type: ignore[arg-type]
        command_store=object(),  # type: ignore[arg-type]
        write_owner=object(),  # type: ignore[arg-type]
        ranking_write_owner=object(),
    )
    ctx = PersonaRouteContext(
        service=service,
        read_store=None,
        command_store=None,
        ranking_write_owner=None,
        write_owner=None,
        extract_identity=lambda _auth: SimpleNamespace(operator_id="op-1", tenant_id=TENANT_ID),
        require_read_role=lambda _identity: None,
        require_operator_role=lambda _identity: None,
        bff_error=_bff_error,
        utc_now=lambda: "2026-11-25T00:00:00Z",
        page_slice=lambda items, token, size: (
            items[int(token or 0) : int(token or 0) + size],
            str(int(token or 0) + size) if int(token or 0) + size < len(items) else None,
        ),
        snapshot_meta=lambda ts: {"snapshot_at": ts},
        dataset_surface_status=lambda *a, **kw: {"status": "ok"},
        read_surface_meta=lambda *a, **kw: {"status": "ok"},
        raise_if_read_surface_unavailable=lambda *a, **kw: None,
        reject_body_idempotency_key=lambda _p: None,
        resolve_final_idempotency_key=lambda k: str(k or "idem-1"),
        submit_persona_action=None,
    )
    app = FastAPI()
    app.include_router(build_collection_router(ctx))
    client = TestClient(app)

    full_resp = client.get("/bff/personas?page_size=50")
    assert full_resp.status_code == 200
    full_body = full_resp.json()
    assert full_body["page_info"]["total"] == len(recorded_rows)
    assert [row["id"] for row in full_body["data"]] == [r["id"] for r in recorded_rows]
    assert [row["listScope"] for row in full_body["data"]] == expected_scopes
    assert [row["listScope"] for row in full_body["items"]] == expected_scopes

    assert [row["personaPurpose"] for row in full_body["data"]] == expected_purposes
    assert full_body["items"] == full_body["data"]
    assert all("metadata" not in row and "persona_class" not in row for row in full_body["data"])
    assert snapshot_reads == [TENANT_ID]

    page1 = client.get("/bff/personas?page_size=3").json()
    assert [row["id"] for row in page1["data"]] == [r["id"] for r in recorded_rows[:3]]
    assert [row["listScope"] for row in page1["data"]] == expected_scopes[:3]
    assert page1["page_info"]["next_page_token"] == "3"
    assert [row["personaPurpose"] for row in page1["data"]] == expected_purposes[:3]

    page2 = client.get("/bff/personas?page_size=3&page_token=3").json()
    assert [row["id"] for row in page2["data"]] == [r["id"] for r in recorded_rows[3:6]]
    assert [row["listScope"] for row in page2["data"]] == expected_scopes[3:6]

    assert [row["personaPurpose"] for row in page2["data"]] == expected_purposes[3:6]
    assert snapshot_reads == [TENANT_ID] * 3


def test_success_rate_is_null_when_unrecorded_or_zero_and_all_four_projections_and_league_agree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(personas_service, "_routed_strategy_counts_by_persona", lambda: {})
    monkeypatch.setattr(personas_service, "_routed_strategies_for_persona", lambda _pid: 0)
    monkeypatch.setattr(personas_service, "_pm12_persona_capability_summary", lambda _pid, snapshot=None: {})
    monkeypatch.setattr(personas_service, "_pm12_persona_evaluation_summary", lambda _pid, teaching=None: {})
    monkeypatch.setattr(personas_service, "_pm12_persona_memory_summary", lambda _pid: {})
    monkeypatch.setattr(
        personas_service,
        "_bff_me_tenant_payload",
        lambda identity, requested_tenant=None: {"id": TENANT_ID},
    )

    def _league_row(raw: Dict[str, Any]) -> Dict[str, Any]:
        return personas_service._project_persona_league_row(
            raw,
            runtime_bindings=[],
            persona_bindings=[],
            persona_sessions=[],
            authoritative_sessions=[],
            include_route_policy=False,
            include_health=False,
            include_allowed_actions=False,
            routed_strategies=0,
        )

    def _prov_record(persona_id: str, name: str, payload: Dict[str, Any]) -> ProvisioningRecord:
        return ProvisioningRecord(
            tenant_id=TENANT_ID,
            idempotency_key=f"idem-{persona_id}",
            request_hash=f"hash-{persona_id}",
            normalized_name=name.lower().replace(" ", "-"),
            persona_id=persona_id,
            request_payload=payload,
            created_at="2026-11-25T00:00:00Z",
            updated_at="2026-11-25T00:00:00Z",
        )

    # 1. Recorded 0.0 (the filled-in placeholder on hosted personas) -> None across all projections.
    raw_zero = copy.deepcopy(RECORDED_LIVE_PAPER_OWNER)
    assert raw_zero["metadata"]["success_rate"] == 0.0
    prov_zero = _prov_record(raw_zero["id"], raw_zero["name"], {"name": raw_zero["name"]})
    ids_zero = deterministic_provisioning_ids(prov_zero)
    base_prov_meta_zero = personas_service._persona_provisioning_metadata(
        prov_zero,
        ids=ids_zero,
        payload={"name": raw_zero["name"]},
        owner="op-1",
        archetype="momentum",
        risk="low",
        mandate=None,
        strategy_family="daily_learning_paper_momentum",
        traits=None,
        lifecycle_state="provisioning",
    )
    meta_zero_for_create = {
        **base_prov_meta_zero,
        **raw_zero["metadata"],
    }

    dto_zero = personas_service._project_persona_dto(raw_zero, evaluate_provisioning=False)
    create_zero = personas_service._persona_create_response(
        prov_zero,
        persona=raw_zero,
        metadata=meta_zero_for_create,
        payload={"name": raw_zero["name"]},
        snapshot_at="2026-11-25T00:00:00Z",
    )["data"]
    league_zero = _league_row(raw_zero)

    assert dto_zero["successRate"] is None
    assert create_zero["successRate"] is None
    assert league_zero["success_rate"] is None

    # 2. Unrecorded (no success_rate in record or metadata) -> None across all projections.
    raw_unrecorded = copy.deepcopy(RECORDED_ADVISORY_RISK_OFFICER)
    prov_unrec = _prov_record(raw_unrecorded["id"], raw_unrecorded["name"], {"name": raw_unrecorded["name"]})
    ids_unrec = deterministic_provisioning_ids(prov_unrec)
    base_prov_meta_unrec = personas_service._persona_provisioning_metadata(
        prov_unrec,
        ids=ids_unrec,
        payload={"name": raw_unrecorded["name"]},
        owner="op-1",
        archetype="risk_officer",
        risk="low",
        mandate=None,
        strategy_family="risk_governance",
        traits=None,
        lifecycle_state="provisioning",
    )
    meta_unrec_for_create = {
        **base_prov_meta_unrec,
        **raw_unrecorded["metadata"],
    }
    dto_unrecorded = personas_service._project_persona_dto(raw_unrecorded, evaluate_provisioning=False)
    create_unrecorded = personas_service._persona_create_response(
        prov_unrec,
        persona=raw_unrecorded,
        metadata=meta_unrec_for_create,
        payload={"name": raw_unrecorded["name"]},
        snapshot_at="2026-11-25T00:00:00Z",
    )["data"]
    league_unrecorded = _league_row(raw_unrecorded)

    assert dto_unrecorded["successRate"] is None
    assert create_unrecorded["successRate"] is None
    assert league_unrecorded["success_rate"] is None

    # 3. Provisioning metadata helper writes success_rate ONLY when caller sent it.
    rec_omitted = _prov_record("persona-no-sr", "No WinRate Sent", {"name": "No WinRate Sent", "archetype": "momentum"})
    meta_omitted = personas_service._persona_provisioning_metadata(
        rec_omitted,
        ids=deterministic_provisioning_ids(rec_omitted),
        payload={"name": "No WinRate Sent", "archetype": "momentum"},
        owner="op-1",
        archetype="momentum",
        risk="low",
        mandate=None,
        strategy_family="momentum",
        traits=None,
        lifecycle_state="provisioning",
    )
    assert "success_rate" not in meta_omitted

    rec_sent = _prov_record("persona-with-sr", "Explicit WinRate Sent", {"name": "Explicit WinRate Sent", "archetype": "momentum", "successRate": 0.625})
    meta_sent = personas_service._persona_provisioning_metadata(
        rec_sent,
        ids=deterministic_provisioning_ids(rec_sent),
        payload={"name": "Explicit WinRate Sent", "archetype": "momentum", "successRate": 0.625},
        owner="op-1",
        archetype="momentum",
        risk="low",
        mandate=None,
        strategy_family="momentum",
        traits=None,
        lifecycle_state="provisioning",
    )
    assert meta_sent["success_rate"] == 0.625

    # 4. PATCH persona (4th projection) writes success_rate only when caller sent it,
    #    and all four projections + league row agree on both None and explicit values.
    stored_records: Dict[str, Dict[str, Any]] = {
        raw_zero["id"]: copy.deepcopy(raw_zero),
    }

    class _FakeWriteOwner:
        def update_persona(self, persona_id: str, **updates: Any) -> Dict[str, Any]:
            rec = copy.deepcopy(stored_records[persona_id])
            incoming_meta = dict(updates.pop("metadata", None) or {})
            base_meta = dict(rec.get("metadata") or {})
            # Drop the legacy 0.0 placeholder if update did not write success_rate
            if "success_rate" not in incoming_meta and base_meta.get("success_rate") == 0.0:
                base_meta.pop("success_rate", None)
            base_meta.update(incoming_meta)
            rec.update({k: v for k, v in updates.items() if v is not None})
            rec["metadata"] = base_meta
            stored_records[persona_id] = rec
            return rec

    class _FakeReadStore:
        def get_persona(self, persona_id: str) -> Optional[Dict[str, Any]]:
            rec = stored_records.get(persona_id)
            return copy.deepcopy(rec) if rec else None

    service = PersonaService(
        read_store=_FakeReadStore(),  # type: ignore[arg-type]
        command_store=object(),  # type: ignore[arg-type]
        write_owner=_FakeWriteOwner(),  # type: ignore[arg-type]
        ranking_write_owner=object(),
        utc_now_fn=lambda: "2026-11-25T00:00:00Z",
        bff_error_fn=_bff_error,
    )
    identity = SimpleNamespace(operator_id="op-1", tenant_id=TENANT_ID, roles=["operator"])

    # PATCH without successRate: update_metadata sent to write owner has no success_rate,
    # and response successRate is None.
    patch_without = service.patch_persona(
        raw_zero["id"],
        {"name": "衡岳｜0050 台股 Paper 學習 R2 Renamed"},
        identity,
        idempotency_key="idem-patch-no-sr",
    )
    assert patch_without["data"]["successRate"] is None
    assert "success_rate" not in stored_records[raw_zero["id"]]["metadata"]

    # PATCH with explicit successRate=0.625: writes success_rate and all 4 projections + league agree.
    patch_with = service.patch_persona(
        raw_zero["id"],
        {"successRate": 0.625},
        identity,
        idempotency_key="idem-patch-with-sr",
    )
    updated_raw = stored_records[raw_zero["id"]]
    assert updated_raw["metadata"]["success_rate"] == 0.625
    assert patch_with["data"]["successRate"] == 0.625
    assert personas_service._project_persona_dto(updated_raw, evaluate_provisioning=False)["successRate"] == 0.625
    assert (
        personas_service._persona_create_response(
            prov_zero,
            persona=updated_raw,
            metadata={
                **base_prov_meta_zero,
                **updated_raw["metadata"],
            },
            payload={"name": updated_raw["name"], "successRate": 0.625},
            snapshot_at="2026-11-25T00:00:00Z",
        )["data"]["successRate"]
        == 0.625
    )
    assert _league_row(updated_raw)["success_rate"] == 0.625


def _bff_error(status_code: int, code: ErrorCode, message: str, reason: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code.value, "message": message, "reason": reason, **extra},
    )


class _FakeHTTPResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeResearchWriteOwner:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = list(rows)

    def list_research_artifacts(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return [copy.deepcopy(r) for r in self._rows]

    def get_research_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        for row in self._rows:
            if row.get("id") == artifact_id or row.get("artifact_id") == artifact_id:
                return copy.deepcopy(row)
        return None


def test_get_bff_artifacts_joins_list_scope_with_one_bounded_read_per_page_and_preserves_strategy_labels_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persona_records = [
        copy.deepcopy(RECORDED_LIVE_PAPER_OWNER),
        copy.deepcopy(RECORDED_OLDER_PROBE_BASELINE_NO_DATA_SCOPE),
        copy.deepcopy(RECORDED_PROBE_WITH_DATA_SCOPE),
        copy.deepcopy(RECORDED_RETIRED_FORMER_LIVE_PERSONA),
        copy.deepcopy(RECORDED_ADVISORY_RISK_OFFICER),
        copy.deepcopy(RECORDED_AGORA_SERVANT),
    ]

    registry_artifacts = [
        {
            "registry_id": "reg-bundle-current",
            "artifact_type": "execution_bundle",
            "strategy_id": "strat-live-0050",
            "version": "1.2.0",
            "artifact_state": "active",
            "checksum": "sha256:1111",
            "created_by": "pantheon-dev-admin-test",
            "created_at": "2026-11-24T10:00:00Z",
            "metadata": {"tenant_id": TENANT_ID, "risk": "low"},
        },
        {
            "registry_id": "reg-bundle-test",
            "artifact_type": "execution_bundle",
            "strategy_id": "strat-release-probe",
            "version": "1.0.0",
            "artifact_state": "active",
            "checksum": "sha256:2222",
            "created_by": "pantheon-dev-admin-test",
            "created_at": "2026-11-24T09:00:00Z",
            "metadata": {"tenant_id": TENANT_ID, "risk": "medium"},
        },
        {
            "registry_id": "reg-bundle-retired",
            "artifact_type": "execution_bundle",
            "strategy_id": "strat-retired-2330",
            "version": "1.1.0",
            "artifact_state": "deprecated",
            "checksum": "sha256:3333",
            "created_by": "pantheon-dev-operator-a",
            "created_at": "2026-11-24T08:00:00Z",
            "metadata": {"tenant_id": TENANT_ID, "risk": "low"},
        },
        {
            "registry_id": "reg-bundle-unbound",
            "artifact_type": "execution_bundle",
            "strategy_id": "strat-unbound-alpha",
            "version": "0.9.0",
            "artifact_state": "draft",
            "checksum": "sha256:4444",
            "created_by": "pantheon-dev-operator-a",
            "created_at": "2026-11-24T07:00:00Z",
            "metadata": {"tenant_id": TENANT_ID, "risk": "medium"},
        },
    ]

    persona_binding_specs = [
        {
            "registry_id": "reg-spec-live-0050",
            "artifact_type": "strategy_spec",
            "strategy_id": "strat-live-0050",
            "version": "1.2.0",
            "created_at": "2026-11-24T10:00:00Z",
            "metadata": {
                "title": "0050 Daily Learning Momentum",
                "persona_ids": [
                    RECORDED_RETIRED_FORMER_LIVE_PERSONA["id"],
                    RECORDED_LIVE_PAPER_OWNER["id"],
                ],
            },
        },
        {
            "registry_id": "reg-spec-release-probe",
            "artifact_type": "strategy_spec",
            "strategy_id": "strat-release-probe",
            "version": "1.0.0",
            "created_at": "2026-11-24T09:00:00Z",
            "metadata": {
                "title": "Dev Paper Release Gate Strategy",
                "persona_ids": [RECORDED_OLDER_PROBE_BASELINE_NO_DATA_SCOPE["id"]],
            },
        },
        {
            "registry_id": "reg-spec-retired-2330",
            "artifact_type": "strategy_spec",
            "strategy_id": "strat-retired-2330",
            "version": "1.1.0",
            "created_at": "2026-11-24T08:00:00Z",
            "metadata": {
                "title": "2330 Former Live Strategy",
                "persona_ids": [RECORDED_RETIRED_FORMER_LIVE_PERSONA["id"]],
            },
        },
        {
            "registry_id": "reg-spec-unbound-alpha",
            "artifact_type": "strategy_spec",
            "strategy_id": "strat-unbound-alpha",
            "version": "0.9.0",
            "created_at": "2026-11-24T07:00:00Z",
            "metadata": {
                "title": "Unbound Alpha Strategy",
                "persona_ids": [],
            },
        },
    ]

    research_outputs = [
        {
            "artifact_id": "res-out-001",
            "title": "Alpha Factor IC Report",
            "artifact_type": "ic_report",
            "created_at": "2026-11-24T09:30:00Z",
            "created_by": "research-worker",
            "run_id": "orch-run-001",
            "task_id": "task-qlib-001",
            "stage_id": "stage-eval",
            "checksum": "sha256:5555",
            "storage_ref": "gs://pantheon-research-outputs/orch-run-001/report.json",
            "artifact_family": "evaluation",
            "stage_type": "backtest",
            "tenant_id": TENANT_ID,
            "run": {
                "receipt_id": "rcpt-001",
                "artifact_digest": "sha256:5555",
                "status": "completed",
                "completed_at": "2026-11-24T09:30:00Z",
                "provenance": "qlib-runner",
            },
        }
    ]

    http_call_counts = {"persona_binding_view": 0, "strategy_artifacts": 0}

    def _fake_urlopen(req: Any, timeout: Optional[float] = None, *args: Any, **kwargs: Any) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "view=persona_binding" in url:
            http_call_counts["persona_binding_view"] += 1
            body = json.dumps(persona_binding_specs).encode("utf-8")
            return _FakeHTTPResponse(200, body)
        if "/api/registry/strategy-artifacts" in url:
            http_call_counts["strategy_artifacts"] += 1
            body = json.dumps(
                {
                    "items": registry_artifacts,
                    "total": len(registry_artifacts),
                    "next_offset": None,
                }
            ).encode("utf-8")
            return _FakeHTTPResponse(200, body)
        raise AssertionError(f"Unexpected URL in test: {url}")

    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.test")
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    port = DefaultResearchKnowledgeSourcePort(
        registry_service_url="http://registry.test",
        research_write_owner=_FakeResearchWriteOwner(research_outputs),
    )

    # Verify get_strategy_labels keeps its exact present shape {strategy_id: {"title": ..., "name": ...}}.
    tok_auth = authorization.set(f"Bearer {_test_jwt()}")
    tok_tenant = selected_tenant.set(TENANT_ID)
    try:
        labels = port.get_strategy_labels()
    finally:
        authorization.reset(tok_auth)
        selected_tenant.reset(tok_tenant)
    assert labels == {
        "strat-live-0050": {"title": "0050 Daily Learning Momentum", "name": "0050 Daily Learning Momentum"},
        "strat-release-probe": {"title": "Dev Paper Release Gate Strategy", "name": "Dev Paper Release Gate Strategy"},
        "strat-retired-2330": {"title": "2330 Former Live Strategy", "name": "2330 Former Live Strategy"},
        "strat-unbound-alpha": {"title": "Unbound Alpha Strategy", "name": "Unbound Alpha Strategy"},
    }
    for val in labels.values():
        assert set(val.keys()) == {"title", "name"}

    persona_directory_calls = {"count": 0}

    def _persona_directory_snapshot(*, read_store: Optional[Any] = None) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        persona_directory_calls["count"] += 1
        return copy.deepcopy(persona_records), {"status": "ok", "source": "persona_directory"}

    port.persona_directory_snapshot = _persona_directory_snapshot  # type: ignore[attr-defined]
    app = FastAPI()
    app.include_router(
        create_research_router(
            get_read_store=lambda: port,
            extract_identity=lambda _auth: SimpleNamespace(operator_id="op-1", tenant_id=TENANT_ID),
            require_read_role=lambda _identity: None,
            require_operator_role=lambda _identity: None,
            bff_error=_bff_error,
            utc_now=lambda: "2026-11-25T00:00:00Z",
        )
    )
    client = TestClient(app)

    http_call_counts["persona_binding_view"] = 0
    persona_directory_calls["count"] = 0

    tok_auth = authorization.set(f"Bearer {_test_jwt()}")
    tok_tenant = selected_tenant.set(TENANT_ID)
    try:
        resp = client.get("/bff/artifacts?page_size=20")
    finally:
        authorization.reset(tok_auth)
        selected_tenant.reset(tok_tenant)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows_by_id = {row["id"]: row for row in body["data"]}
    assert set(rows_by_id.keys()) == {
        "reg-bundle-current",
        "reg-bundle-test",
        "reg-bundle-retired",
        "reg-bundle-unbound",
        "res-out-001",
    }
    assert rows_by_id["reg-bundle-current"]["list_scope"] == "current"
    assert rows_by_id["reg-bundle-test"]["list_scope"] == "test"
    assert rows_by_id["reg-bundle-retired"]["list_scope"] == "retired"
    assert rows_by_id["reg-bundle-unbound"]["list_scope"] == "unbound"
    assert rows_by_id["res-out-001"]["list_scope"] == "research"

    # Exactly one bounded persona directory read and one bounded persona_binding view read for the page.
    assert persona_directory_calls["count"] == 1
    assert http_call_counts["persona_binding_view"] == 1


def test_get_bff_artifacts_marks_every_row_unknown_and_meta_unavailable_when_persona_directory_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_artifacts = [
        {
            "registry_id": "reg-bundle-current",
            "artifact_type": "execution_bundle",
            "strategy_id": "strat-live-0050",
            "version": "1.2.0",
            "artifact_state": "active",
            "checksum": "sha256:1111",
            "created_by": "pantheon-dev-admin-test",
            "created_at": "2026-11-24T10:00:00Z",
            "metadata": {"tenant_id": TENANT_ID, "risk": "low"},
        },
        {
            "registry_id": "reg-bundle-unbound",
            "artifact_type": "execution_bundle",
            "strategy_id": "strat-unbound-alpha",
            "version": "0.9.0",
            "artifact_state": "draft",
            "checksum": "sha256:4444",
            "created_by": "pantheon-dev-operator-a",
            "created_at": "2026-11-24T07:00:00Z",
            "metadata": {"tenant_id": TENANT_ID, "risk": "medium"},
        },
    ]
    research_outputs = [
        {
            "artifact_id": "res-out-001",
            "title": "Alpha Factor IC Report",
            "artifact_type": "ic_report",
            "created_at": "2026-11-24T09:30:00Z",
            "created_by": "research-worker",
            "run_id": "orch-run-001",
            "task_id": "task-qlib-001",
            "stage_id": "stage-eval",
            "checksum": "sha256:5555",
            "storage_ref": "gs://pantheon-research-outputs/orch-run-001/report.json",
            "artifact_family": "evaluation",
            "stage_type": "backtest",
            "tenant_id": TENANT_ID,
            "run": {
                "receipt_id": "rcpt-001",
                "artifact_digest": "sha256:5555",
                "status": "completed",
                "completed_at": "2026-11-24T09:30:00Z",
                "provenance": "qlib-runner",
            },
        }
    ]

    def _fake_urlopen(req: Any, timeout: Optional[float] = None, *args: Any, **kwargs: Any) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "view=persona_binding" in url:
            body = json.dumps(
                [
                    {
                        "registry_id": "reg-spec-live-0050",
                        "artifact_type": "strategy_spec",
                        "strategy_id": "strat-live-0050",
                        "version": "1.2.0",
                        "created_at": "2026-11-24T10:00:00Z",
                        "metadata": {
                            "title": "0050 Daily Learning Momentum",
                            "persona_ids": [RECORDED_LIVE_PAPER_OWNER["id"]],
                        },
                    }
                ]
            ).encode("utf-8")
            return _FakeHTTPResponse(200, body)
        if "/api/registry/strategy-artifacts" in url:
            body = json.dumps(
                {
                    "items": registry_artifacts,
                    "total": len(registry_artifacts),
                    "next_offset": None,
                }
            ).encode("utf-8")
            return _FakeHTTPResponse(200, body)
        raise AssertionError(f"Unexpected URL in test: {url}")

    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.test")
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    port = DefaultResearchKnowledgeSourcePort(
        registry_service_url="http://registry.test",
        research_write_owner=_FakeResearchWriteOwner(research_outputs),
    )

    def _failing_persona_directory(*, read_store: Optional[Any] = None) -> Any:
        raise RuntimeError("persona owner read timed out")

    port.persona_directory_snapshot = _failing_persona_directory  # type: ignore[attr-defined]
    app = FastAPI()
    app.include_router(
        create_research_router(
            get_read_store=lambda: port,
            extract_identity=lambda _auth: SimpleNamespace(operator_id="op-1", tenant_id=TENANT_ID),
            require_read_role=lambda _identity: None,
            require_operator_role=lambda _identity: None,
            bff_error=_bff_error,
            utc_now=lambda: "2026-11-25T00:00:00Z",
        )
    )
    client = TestClient(app)

    tok_auth = authorization.set(f"Bearer {_test_jwt()}")
    tok_tenant = selected_tenant.set(TENANT_ID)
    try:
        with request_read_scope():
            resp = client.get("/bff/artifacts?page_size=20")
    finally:
        authorization.reset(tok_auth)
        selected_tenant.reset(tok_tenant)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # No row is dropped!
    assert len(body["data"]) == 3
    assert {row["id"] for row in body["data"]} == {"reg-bundle-current", "reg-bundle-unbound", "res-out-001"}
    # Every row has list_scope == "unknown".
    for row in body["data"]:
        assert row["list_scope"] == "unknown"
    # meta reports persona_directory unavailable.
    assert body["meta"]["persona_directory_status"] == "unavailable"
    assert body["meta"]["surfaces"]["persona_directory"]["status"] == "unavailable"
