"""AGORA-SERVANT-REVISIONS-AND-REPORT-20261127: servant workspace revision triggers and weekly/on-demand trader report.

Covers SD_AGORA_SERVANT_CORE_2026-11-27 §9.2, §9.3, §10, §11, §12 T11, and §13 residual 3:
  1. Each §9.2 trigger (new strategy version selected, repeated trader edits 3x in 7d, data or
     governed active regime change) produces one workspace revision proposal with at most one
     pending per workspace; the servant never edits the workspace directly.
  2. The §9.3 trader report is produced weekly after attribution and on demand at most 3 per day;
     every number comes from an owner with ``owner``, ``source`` (``source_id``) and ``as_of``
     and narrative numbers are guarded by ``tool_turn.render_answer``.
  3. The weekly attribution request happens even when no persona cycle runs (§13 residual 3).
  4. Runtime budget as in §9.3 with 26-weekly cap, 3/day on-demand cap, 7-day on-demand retention,
     20 KB size cap, and storage manifest registration.
  5. No persona, meeting, or order route is called (§10).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.agora.store import (
    TRADER_REPORT_MAX_BYTES,
    TRADER_REPORT_ONDEMAND_DAILY_CAP,
    TRADER_REPORT_ONDEMAND_RETENTION_DAYS,
    TRADER_REPORT_WEEKLY_HISTORY_CAP,
)
from services.control_plane.bff.agora.servant import reports as reports_module
from services.control_plane.bff.agora.servant.reports import (
    REQUIRED_REPORT_SECTIONS,
    RUNTIME_BUDGET_SPEC,
    TraderReportService,
)
from services.control_plane.bff.agora.servant.turn_worker import ServantTurnWorker
from services.control_plane.bff.agora.trading_room.router import create_trading_room_router
from services.control_plane.bff.agora.trading_room.service import (
    TradingRoomService,
    get_active_trading_room_service,
)
from services.control_plane.bff.agora.trading_room.store import TradingRoomStore
from services.control_plane.bff.tests import test_agora_servant_sessions as sessions
from services.persona.meeting_triggers import AttributionPort, MeetingTriggers

TENANT = "pantheon-dev"
USER = "agora-test-user"
OTHER_USER = "agora-other-user"
AUTH = sessions.AUTH
OTHER_AUTH = {"Authorization": f"Bearer {OTHER_USER}:operator"}


def _bff_error(status: int, code: Any, message: str, reason: str, **extra: Any) -> HTTPException:
    code_val = getattr(code, "value", str(code))
    return HTTPException(
        status_code=status,
        detail={"code": code_val, "message": message, "reason": reason, **extra},
    )


def _extract_identity(authorization: str | None = None, session_cookie: str | None = None) -> Dict[str, Any]:
    token = (authorization or "").removeprefix("Bearer ").strip() or session_cookie or f"{USER}:trader"
    user_part = token.split(":")[0] if ":" in token else USER
    role_part = token.split(":")[1] if ":" in token else "trader"
    return {
        "tenant_id": TENANT,
        "user_id": user_part,
        "sub": user_part,
        "operator_id": user_part,
        "roles": [role_part, "trader", "operator"],
    }


def _build_trading_room_test_client(clock_fn=None) -> tuple[TestClient, TradingRoomStore, TradingRoomService]:
    store = TradingRoomStore()
    clock = clock_fn or (lambda: "2026-11-27T08:00:00Z")
    app = FastAPI()
    router = create_trading_room_router(
        extract_identity=_extract_identity,
        require_read_role=lambda _id: None,
        require_write_role=lambda _id: None,
        bff_error=_bff_error,
        utc_now=clock,
        trading_room_store=store,
    )
    app.include_router(router)
    return TestClient(app), store, get_active_trading_room_service()


def _bootstrap_workspace(client: TestClient, strategy_id: str = "strat_alpha") -> Dict[str, Any]:
    create_resp = client.post(
        f"/bff/agora/strategies/{strategy_id}/trading-room/proposals",
        headers={**AUTH, "Idempotency-Key": f"init-prop-{strategy_id}"},
        json={"strategyVersion": "v1"},
    )
    assert create_resp.status_code == 201, create_resp.text
    proposal_id = create_resp.json()["data"]["proposalId"]

    accept_resp = client.post(
        f"/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept",
        headers={**AUTH, "Idempotency-Key": f"accept-prop-{strategy_id}"},
        json={"expectedStatus": "preview"},
    )
    assert accept_resp.status_code == 200, accept_resp.text
    return accept_resp.json()["data"]["workspace"]


# ---------------------------------------------------------------------------
# AC1 / SD §9.2: Three workspace revision triggers, <=1 pending, no direct edit
# ---------------------------------------------------------------------------


def test_trigger_1_new_strategy_version_creates_one_pending_proposal_and_never_edits_workspace():
    client, _store, _service = _build_trading_room_test_client()
    ws = _bootstrap_workspace(client, "strat_v_select")
    ws_id = ws["id"]
    initial_dashboard_version = ws["dashboardVersion"]

    trig_resp = client.post(
        f"/bff/agora/trading-room/workspaces/{ws_id}/revision-proposals/triggers",
        headers={**AUTH, "Idempotency-Key": "trig-v2"},
        json={
            "trigger": "strategy_version_selected",
            "previousStrategyVersion": "v1",
            "strategyVersion": "v2",
            "previousStrategySpec": {"universe": ["2330"], "holding_horizon": "5d"},
            "currentStrategySpec": {"universe": ["2330", "2454"], "holding_horizon": "15d", "risk_limit": "2%"},
            "reason": "擴大觀察清單並拉長持有週期",
        },
    )
    assert trig_resp.status_code == 200, trig_resp.text
    payload = trig_resp.json()["data"]
    assert payload["triggered"] is True
    assert payload["trigger"] == "strategy_version_selected"
    assert payload["proposal_kind"] == "workspace_proposal"
    assert payload["pending_count"] == 1
    assert payload["workspace_unchanged"] is True
    assert payload["workspace_dashboard_version"] == initial_dashboard_version
    assert "v1" in payload["change_summary"] and "v2" in payload["change_summary"]

    # Active workspace was NOT edited directly
    ws_after = client.get(f"/bff/agora/trading-room/workspaces/{ws_id}", headers=AUTH).json()["data"]
    assert ws_after["dashboardVersion"] == initial_dashboard_version
    assert ws_after["strategyVersion"] == "v1"


def test_trigger_2_repeated_trader_edits_3_times_in_7_days_proposes_default_widget_revision():
    current_time = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
    client, _store, _service = _build_trading_room_test_client(
        clock_fn=lambda: current_time.isoformat().replace("+00:00", "Z")
    )
    ws = _bootstrap_workspace(client, "strat_repeated_edits")
    ws_id = ws["id"]
    target_widget_id = ws["views"][0]["widgets"][0]["id"]

    # Edit 1 and Edit 2: threshold (3) not yet reached -> no pending revision proposal
    for idx in (1, 2):
        current_time += timedelta(days=1)
        cur_ws_resp = client.get(f"/bff/agora/trading-room/workspaces/{ws_id}", headers=AUTH)
        etag = cur_ws_resp.headers["ETag"]
        patch_resp = client.patch(
            f"/bff/agora/trading-room/workspaces/{ws_id}/widgets/{target_widget_id}",
            headers={**AUTH, "If-Match": etag, "Idempotency-Key": f"edit-widget-{idx}"},
            json={"patch": {"title": f"自訂標題第{idx}次"}},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        listing = client.get(
            f"/bff/agora/trading-room/workspaces/{ws_id}/revision-proposals", headers=AUTH
        ).json()["data"]
        assert listing["pending_count"] == 0

    # Edit 3 within 7 days: automatically triggers 1 servant WidgetRevisionProposal
    current_time += timedelta(days=1)
    cur_ws_resp = client.get(f"/bff/agora/trading-room/workspaces/{ws_id}", headers=AUTH)
    etag = cur_ws_resp.headers["ETag"]
    patch_resp = client.patch(
        f"/bff/agora/trading-room/workspaces/{ws_id}/widgets/{target_widget_id}",
        headers={**AUTH, "If-Match": etag, "Idempotency-Key": "edit-widget-3"},
        json={"patch": {"title": "自訂標題第3次"}},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    listing = client.get(
        f"/bff/agora/trading-room/workspaces/{ws_id}/revision-proposals", headers=AUTH
    ).json()["data"]
    assert listing["pending_count"] == 1
    pending = listing["pending_proposal"]
    assert pending is not None
    assert pending["proposalKind"] == "widget_revision_proposal"
    assert pending["trigger"] == "repeated_trader_edits"
    assert pending["proposal"]["widgetId"] == target_widget_id
    assert pending["proposal"]["status"] == "preview"
    assert pending["proposal"]["proposedSpec"]["query"]["filters"]["servantDefaultedFromRepeatedEdits"] is True


def test_trigger_3_data_freshness_and_governed_regime_change_and_at_most_one_pending():
    client, _store, _service = _build_trading_room_test_client()
    ws = _bootstrap_workspace(client, "strat_data_regime")
    ws_id = ws["id"]
    initial_version = ws["dashboardVersion"]
    first_widget = ws["views"][0]["widgets"][0]

    # 3a. Data source turns stale -> produces 1 pending widget revision proposal
    stale_resp = client.post(
        f"/bff/agora/trading-room/workspaces/{ws_id}/revision-proposals/triggers",
        headers={**AUTH, "Idempotency-Key": "trig-data-stale"},
        json={
            "trigger": "data_freshness_change",
            "dataSource": first_widget["dataSource"],
            "freshnessState": "stale",
            "asOf": "2026-11-26T06:00:00Z",
        },
    )
    assert stale_resp.status_code == 200, stale_resp.text
    stale_data = stale_resp.json()["data"]
    assert stale_data["triggered"] is True
    assert stale_data["pending_count"] == 1
    first_proposal_id = stale_data["proposal"]["id"]

    # 3b. Proposed/candidate (ungoverned) regime record -> MUST NOT trigger a proposal
    proposed_regime_resp = client.post(
        f"/bff/agora/trading-room/workspaces/{ws_id}/revision-proposals/triggers",
        headers={**AUTH, "Idempotency-Key": "trig-regime-proposed"},
        json={
            "trigger": "regime_change",
            "regime": {
                "state": "proposed",
                "model_status": "candidate",
                "axis": "macro",
                "from_state": "risk_on",
                "to_state": "risk_off",
            },
        },
    )
    assert proposed_regime_resp.status_code == 200, proposed_regime_resp.text
    prop_reg_data = proposed_regime_resp.json()["data"]
    assert prop_reg_data["triggered"] is False
    assert prop_reg_data["reason"] == "proposed_regime_record_only"
    # Previous pending proposal remains the single pending proposal
    assert prop_reg_data["pending_count"] == 1
    assert prop_reg_data["pending_proposal"]["proposalId"] == first_proposal_id

    # 3c. Governed active RegimeState transition -> produces new proposal & supersedes prior pending
    active_regime_resp = client.post(
        f"/bff/agora/trading-room/workspaces/{ws_id}/revision-proposals/triggers",
        headers={**AUTH, "Idempotency-Key": "trig-regime-active"},
        json={
            "trigger": "regime_change",
            "regime": {
                "state": "active",
                "model_status": "approved",
                "axis": "macro",
                "from_state": "neutral_range",
                "to_state": "high_volatility_defensive",
                "regime_id": "regime:tw_equities:2026-11-27",
                "as_of": "2026-11-27T05:30:00Z",
            },
        },
    )
    assert active_regime_resp.status_code == 200, active_regime_resp.text
    act_reg_data = active_regime_resp.json()["data"]
    assert act_reg_data["triggered"] is True
    assert act_reg_data["trigger"] == "regime_change"
    assert act_reg_data["pending_count"] == 1
    second_proposal_id = act_reg_data["proposal"]["id"]
    assert second_proposal_id != first_proposal_id

    # Listing confirms first proposal is now 'superseded' and only second is 'preview'
    listing = client.get(
        f"/bff/agora/trading-room/workspaces/{ws_id}/revision-proposals", headers=AUTH
    ).json()["data"]
    assert listing["pending_count"] == 1
    statuses = {p["id"]: p["status"] for p in listing["widget_revision_proposals"]}
    assert statuses[first_proposal_id] == "superseded"
    assert statuses[second_proposal_id] == "preview"

    # Active workspace was never edited directly by any trigger
    ws_now = client.get(f"/bff/agora/trading-room/workspaces/{ws_id}", headers=AUTH).json()["data"]
    assert ws_now["dashboardVersion"] == initial_version

    # Rejecting the second proposal records decision in changeLog and leaves workspace unchanged
    reject_resp = client.post(
        f"/bff/agora/trading-room/widget-revision-proposals/{second_proposal_id}/reject",
        headers={**AUTH, "Idempotency-Key": "reject-regime-prop"},
        json={"reason": "目前維持原判讀視窗即可"},
    )
    assert reject_resp.status_code == 200, reject_resp.text
    rej_data = reject_resp.json()["data"]
    assert rej_data["proposal"]["status"] == "rejected"
    assert rej_data["changeLog"]["status"] == "rejected"
    assert rej_data["changeLog"]["reason"] == "目前維持原判讀視窗即可"


def test_servant_direct_workspace_edit_is_forbidden():
    client, _store, _service = _build_trading_room_test_client()
    ws = _bootstrap_workspace(client, "strat_no_direct_edit")
    ws_id = ws["id"]
    wid = ws["views"][0]["widgets"][0]["id"]
    etag = client.get(f"/bff/agora/trading-room/workspaces/{ws_id}", headers=AUTH).headers["ETag"]

    direct_widget_patch = client.patch(
        f"/bff/agora/trading-room/workspaces/{ws_id}/widgets/{wid}",
        headers={**AUTH, "If-Match": etag, "Idempotency-Key": "servant-direct-1"},
        json={"initiatedBy": "trading_servant", "patch": {"title": "僕人直接偷改"}},
    )
    assert direct_widget_patch.status_code == 409
    assert direct_widget_patch.json()["detail"]["reason"] == "servant_direct_widget_patch_not_allowed"

    direct_layout_patch = client.patch(
        f"/bff/agora/trading-room/workspaces/{ws_id}/layout",
        headers={**AUTH, "If-Match": etag, "Idempotency-Key": "servant-direct-2"},
        json={
            "actorType": "servant",
            "operations": [{"op": "remove", "viewId": ws["views"][0]["id"], "widgetId": wid}],
        },
    )
    assert direct_layout_patch.status_code == 409
    assert direct_layout_patch.json()["detail"]["reason"] == "servant_direct_layout_patch_not_allowed"


# ---------------------------------------------------------------------------
# AC2 & AC4 & SD §9.3 & §10: Weekly & On-Demand Trader Report
# ---------------------------------------------------------------------------


def _assert_every_number_has_owner_source_and_as_of(node: Any, path: str = "root") -> None:
    """Recursively verify that every dict holding any numeric metric in sections/facts/citations
    carries non-empty ``owner``, ``source`` (or ``source_id``), and ``as_of``.
    """
    if isinstance(node, Mapping):
        has_number = any(
            isinstance(v, (int, float)) and not isinstance(v, bool)
            for v in node.values()
        )
        if has_number:
            owner = str(node.get("owner") or "").strip()
            source = str(node.get("source") or node.get("source_id") or "").strip()
            as_of = str(node.get("as_of") or "").strip()
            assert owner, f"Missing owner on numeric node at {path}: {node}"
            assert source, f"Missing source/source_id on numeric node at {path}: {node}"
            assert as_of, f"Missing as_of on numeric node at {path}: {node}"
        for k, v in node.items():
            _assert_every_number_has_owner_source_and_as_of(v, f"{path}.{k}")
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            _assert_every_number_has_owner_source_and_as_of(item, f"{path}[{idx}]")


def test_trader_report_sections_owner_attribution_number_guard_and_daily_cap(monkeypatch, tmp_path):
    sessions.FakeOwnerTable.reset()
    owner = sessions._build_owner()
    current_clock = "2026-11-27T09:00:00Z"
    report_service = TraderReportService(owner=owner, clock=lambda: current_clock)
    monkeypatch.setattr(
        reports_module,
        "build_default_trader_report_service",
        lambda *a, **k: report_service,
    )
    client, _fake = sessions._install_client(monkeypatch, tmp_path, owner=owner, reset_rows=False)

    # 1. Create on-demand report #1 with an unreferenced model number in narrative_override to test number guard
    resp1 = client.post(
        "/bff/agora/servant/reports",
        headers={**AUTH, "Idempotency-Key": "rep-ondemand-1"},
        json={
            "strategy_id": "strat_alpha",
            "trigger": "on_demand",
            "narrative_override": "本週共提供 {{f2}} 筆決策，已接受 {{f4}} 筆，但模型自己瞎編了 99999 元獲利。",
            "owner_inputs": {
                "decisions": {"offered": 12, "accept": 8, "modify": 2, "reject": 1, "shadow": 1, "expired": 0},
                "paper_book": {"fills_count": 10, "positions_count": 3, "realized_pnl": 45000.0, "unrealized_pnl": 12000.0},
            },
        },
    )
    assert resp1.status_code == 201, resp1.text
    report1 = resp1.json()["data"]
    assert set(REQUIRED_REPORT_SECTIONS).issubset(set(report1["sections"].keys()))
    assert report1["no_persona_or_meeting_proof"] == {
        "persona_calls": 0,
        "meeting_calls": 0,
        "order_routes_called": 0,
    }
    # Number guard stripped '99999' and kept cited facts {{f2}}=12 and {{f4}}=8
    assert "99999" not in report1["narrative"]
    assert "〔未引用數字〕" in report1["narrative"]
    assert "99999" in report1["number_guard"]["model_numbers_removed"]

    # Every number in sections, facts, and citations has owner, source/source_id, and as_of
    _assert_every_number_has_owner_source_and_as_of(report1["sections"], "sections")
    _assert_every_number_has_owner_source_and_as_of(report1["facts"], "facts")
    _assert_every_number_has_owner_source_and_as_of(report1["citations"], "citations")

    # 2. On-demand reports #2 and #3 succeed (reaching TRADER_REPORT_ONDEMAND_DAILY_CAP = 3)
    for n in (2, 3):
        r = client.post(
            "/bff/agora/servant/reports",
            headers={**AUTH, "Idempotency-Key": f"rep-ondemand-{n}"},
            json={"strategy_id": "strat_alpha", "trigger": "on_demand"},
        )
        assert r.status_code == 201, r.text
        rep_n = r.json()["data"]
        assert rep_n["number_guard"]["passed"] is True

    # 3. 4th on-demand report on the same day is rejected with 429
    r4 = client.post(
        "/bff/agora/servant/reports",
        headers={**AUTH, "Idempotency-Key": "rep-ondemand-4"},
        json={"strategy_id": "strat_alpha", "trigger": "on_demand"},
    )
    assert r4.status_code == 429, r4.text

    # 4. Weekly post-attribution report is still permitted even when on-demand daily cap is full
    r_weekly = client.post(
        "/bff/agora/servant/reports",
        headers={**AUTH, "Idempotency-Key": "rep-weekly-1"},
        json={
            "strategy_id": "strat_alpha",
            "trigger": "weekly_post_attribution",
            "attribution_run_id": "attr-weekly-2026W48",
        },
    )
    assert r_weekly.status_code == 201, r_weekly.text
    assert r_weekly.json()["data"]["trigger"] == "weekly_post_attribution"

    # 5. List and get by ID are owner-scoped; another trader gets 404
    list_resp = client.get("/bff/agora/servant/reports", headers=AUTH)
    assert list_resp.status_code == 200
    assert list_resp.json()["data"]["total"] == 4
    assert list_resp.json()["data"]["on_demand_daily_used"] == TRADER_REPORT_ONDEMAND_DAILY_CAP

    get_resp = client.get(f"/bff/agora/servant/reports/{report1['report_id']}", headers=AUTH)
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["report_id"] == report1["report_id"]

    foreign_get = client.get(f"/bff/agora/servant/reports/{report1['report_id']}", headers=OTHER_AUTH)
    assert foreign_get.status_code == 404
    foreign_filter = client.get(f"/bff/agora/servant/reports?trader_id={OTHER_USER}", headers=AUTH)
    assert foreign_filter.status_code == 404


def test_command_bar_quick_task_generates_trader_report_in_servant_turn_worker(monkeypatch, tmp_path):
    sessions.FakeOwnerTable.reset()
    owner = sessions._build_owner()
    client, _fake = sessions._install_client(monkeypatch, tmp_path, owner=owner, reset_rows=False)

    ensured = client.post(
        "/bff/agora/servant/ensure",
        headers={**AUTH, "Idempotency-Key": "ens-report-cmd", "X-Request-Id": "req-ens-report-cmd"},
    )
    assert ensured.status_code in (200, 201), ensured.text

    created = client.post(
        "/bff/agora/servant/sessions",
        headers={**AUTH, "Idempotency-Key": "sess-report-cmd", "X-Request-Id": "req-sess-report-cmd"},
        json={"intent": "review_week", "strategy_ref": "strat_alpha"},
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["data"]["session_id"]

    msg_resp = client.post(
        f"/bff/agora/servant/sessions/{session_id}/messages",
        headers={**AUTH, "Idempotency-Key": "msg-report-cmd", "X-Request-Id": "req-msg-report-cmd"},
        json={
            "content": "整理本週報表",
            "context": {
                "page": "trading_room",
                "strategy_id": "strat_alpha",
                "quick_task": "整理本週報表",
            },
        },
    )
    assert msg_resp.status_code == 202, msg_resp.text

    worker = ServantTurnWorker(owner=owner, clock=lambda: "2026-11-27T10:00:00Z")
    assert worker.run_once() == 1
    assert worker.metrics["reports_generated"] == 1

    reports = owner.list_trader_reports(tenant_id=TENANT, user_id=USER, strategy_id="strat_alpha")
    assert len(reports) == 1
    assert reports[0]["trigger"] == "on_demand"


# ---------------------------------------------------------------------------
# AC3 / SD §13 residual 3: Weekly attribution request even without persona cycle
# ---------------------------------------------------------------------------


def test_weekly_attribution_runs_without_persona_cycle_and_produces_weekly_trader_report():
    sessions.FakeOwnerTable.reset()
    owner = sessions._build_owner()
    calls: List[str] = []

    def fake_transport(method: str, path: str, body: dict | None):
        calls.append(f"{method} {path}")
        return 202, {"run_id": f"attr-run-{TENANT}-2026W48", "status": "queued"}

    triggers = MeetingTriggers(
        attribution=AttributionPort(fake_transport),
        now=lambda: datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc),
    )
    # 1. Direct evaluation with zero enrolled personas still runs weekly attribution
    no_cycle_eval = triggers.run_without_persona_cycle(tenant_id=TENANT, snapshots={})
    assert no_cycle_eval["weekly_outlier_run"] is not None
    assert no_cycle_eval["weekly_outlier_run"]["run_id"] == f"attr-run-{TENANT}-2026W48"
    assert calls == ["POST /api/attribution/runs"]

    # 2. ServantTurnWorker.run_weekly_post_attribution_reports triggers attribution & writes report
    worker = ServantTurnWorker(owner=owner, clock=lambda: "2026-11-27T10:05:00Z")
    result = worker.run_weekly_post_attribution_reports(
        tenant_id=TENANT,
        traders=[{"user_id": USER, "strategy_id": "strat_alpha"}],
        snapshots={},
        triggers=triggers,
    )
    assert result["attribution_run_id"] == f"attr-run-{TENANT}-2026W48"
    assert len(result["reports"]) == 1
    assert result["reports"][0]["trigger"] == "weekly_post_attribution"
    assert result["reports"][0]["attribution_run_id"] == f"attr-run-{TENANT}-2026W48"


# ---------------------------------------------------------------------------
# AC4: Runtime budget cap (26 weekly), 7-day on-demand cleanup, and storage manifest
# ---------------------------------------------------------------------------


def test_runtime_budget_26_weekly_cap_7d_ondemand_purge_and_storage_manifest():
    sessions.FakeOwnerTable.reset()
    owner = sessions._build_owner()
    svc = TraderReportService(owner=owner, clock=lambda: "2026-11-27T12:00:00Z")

    # Write 28 weekly reports -> pruned on write to newest 26 (TRADER_REPORT_WEEKLY_HISTORY_CAP)
    for week in range(1, 29):
        dt = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc) + timedelta(days=7 * week)
        svc.clock = lambda d=dt: d.isoformat().replace("+00:00", "Z")
        svc.generate_report(
            tenant_id=TENANT,
            user_id=USER,
            servant_persona_id=f"servant:{TENANT}:{USER}",
            strategy_id="strat_cap",
            trigger="weekly_post_attribution",
            attribution_run_id=f"attr-w{week:02d}",
        )
    retained_weekly = owner.list_trader_reports(
        tenant_id=TENANT,
        user_id=USER,
        strategy_id="strat_cap",
        trigger="weekly_post_attribution",
        limit=50,
    )
    assert len(retained_weekly) == TRADER_REPORT_WEEKLY_HISTORY_CAP
    assert retained_weekly[0]["attribution_run_id"] == "attr-w28"
    assert retained_weekly[-1]["attribution_run_id"] == "attr-w03"

    # Write an on-demand report 10 days ago and verify purge_expired_trader_reports removes it
    old_dt = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    svc.clock = lambda: old_dt.isoformat().replace("+00:00", "Z")
    old_rep = svc.generate_report(
        tenant_id=TENANT,
        user_id=USER,
        servant_persona_id=f"servant:{TENANT}:{USER}",
        strategy_id="strat_cap",
        trigger="on_demand",
    )
    purged = owner.purge_expired_trader_reports(
        now="2026-11-27T12:00:00Z",
        actor_roles=("system",),
    )
    assert purged["reports"] >= 1
    assert owner.get_trader_report(old_rep["report_id"], tenant_id=TENANT, user_id=USER) is None

    # Verify storage-manifest.json registers agora.trader_reports as bounded
    repo_root = Path(__file__).resolve().parents[4]
    manifest = json.loads((repo_root / "docs" / "operations" / "storage-manifest.json").read_text(encoding="utf-8"))
    entry = next((e for e in manifest["entries"] if e.get("name") == "agora.trader_reports"), None)
    assert entry is not None
    assert entry["bound_status"] == "bounded"
    assert entry["task"] == "AGORA-SERVANT-REVISIONS-AND-REPORT-20261127"
    assert RUNTIME_BUDGET_SPEC["max_row_bytes"] == TRADER_REPORT_MAX_BYTES
    assert RUNTIME_BUDGET_SPEC["ondemand_retention_days"] == TRADER_REPORT_ONDEMAND_RETENTION_DAYS
