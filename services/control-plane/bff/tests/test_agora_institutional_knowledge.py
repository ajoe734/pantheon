"""HTTP tests for Agora servant institutional knowledge and Trading Room system suggestions (Design §12.7)."""
from __future__ import annotations

import copy
import io
import json
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.agora.servant.router import create_servant_router
from services.control_plane.bff.agora.trading_room.service import reset_default_trading_room_store
from services.control_plane.bff.agora.trading_room.store import make_trading_room_store
from services.runtime_auth_inbound import encode_jwt_hs256

NOW = "2026-11-16T08:00:00Z"
TENANT = "tenant-ik-001"
TRADER_USER = "trader-ik-001"
OTHER_USER = "trader-ik-other"

TRADER_IDENTITY = {
    "user_id": TRADER_USER,
    "operator_id": TRADER_USER,
    "tenant_id": TENANT,
    "roles": ["trader", "operator"],
    "granted_capabilities": [
        "agora.identity.v1",
        "agora.servant.v1",
        "agora.session.v1",
        "agora.trading.v1",
    ],
}

OTHER_USER_IDENTITY = {
    "user_id": OTHER_USER,
    "operator_id": OTHER_USER,
    "tenant_id": TENANT,
    "roles": ["trader", "operator"],
    "granted_capabilities": [
        "agora.identity.v1",
        "agora.servant.v1",
        "agora.trading.v1",
    ],
}


def _make_decision_event(
    event_id: str = "evt-ik-2330",
    *,
    strategy_id: str = "strat-001",
    strategy_spec_registry_id: str = "spec-001",
    symbol: str = "2330.TW",
    event_kind: str = "entry",
    suggested_action: str = "enter",
    tenant_id: str = TENANT,
    user_id: str = TRADER_USER,
) -> Dict[str, Any]:
    return {
        "spec_version": "1.0",
        "decision_event_id": event_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "event_kind": event_kind,
        "origin": "strategy_signal",
        "strategy_id": strategy_id,
        "strategy_spec_registry_id": strategy_spec_registry_id,
        "subject": {"symbol": symbol, "asset_class": "tw_equity", "venue": "TWSE"},
        "state": "pending_review",
        "triggered_at": "2026-11-16T07:30:00Z",
        "confidence": {"value": 0.78, "basis": "model", "calibration_state": "calibrated", "sample_size": 120},
        "probability": {"target_outcome": "up_5d", "horizon": "5d", "value": 0.64, "ci_lower": 0.54, "ci_upper": 0.74},
        "expected_value": {"horizon": "5d", "unit": "pct_return", "gross": 0.032, "cost": 0.002, "net": 0.030, "downside": -0.018},
        "rationale": [{"claim": "Momentum breakout with volume confirmation", "confidence": 0.78, "evidence_refs": []}],
        "risk_notes": [],
        "evidence_refs": [],
        "invalidation": {"conditions": ["close_below_ma20"], "current_state": "valid", "last_checked_at": "2026-11-16T07:29:00Z"},
        "suggested_action": suggested_action,
        "no_order_route_proof": "agora_decision_support_only",
    }


def _seed_owner_fixtures() -> Dict[str, Any]:
    return {
        "attribution_records": [
            {
                "record_id": "attr-001", "tenant_id": TENANT, "subject_kind": "trader", "subject_id": TRADER_USER,
                "window": "2026-W46", "direction": "underperform",
                "metrics": {"sharpe": -0.45, "hit_rate": 0.38, "symbol": "2330.TW", "strategy_id": "strat-001"},
                "decomposition": {
                    "selection": {"contribution_bps": 15.0, "sample_size": 24},
                    "entry_timing": {"contribution_bps": -125.0, "sample_size": 24},
                    "sizing": {"contribution_bps": -10.0, "sample_size": 24},
                    "exit": {"contribution_bps": -5.0, "sample_size": 24},
                    "costs": {"contribution_bps": -8.0, "sample_size": 24},
                },
                "sample_size": 24, "luck_interval": {"ci_95_bps": [-180.0, -60.0], "p_value": 0.008, "resamples": 1000},
                "regime_ref": "macro:expansion-high-vol", "meeting_id": "mtg-mt4-001", "created_at": NOW,
                "hypotheses": [
                    {
                        "hypothesis_id": "hyp-valid-001", "demand_id": "demand-valid-001", "component": "entry_timing",
                        "symbol": "2330.TW", "strategy_id": "strat-001", "action": "entry",
                        "hypothesis": "2330.TW 早盤開盤前 15 分鐘追價進場導致 -125 bps 進場時機拖累，等待首根 15m K 線確認可顯著改善",
                    },
                    {
                        "hypothesis_id": "hyp-unval-001", "demand_id": "demand-unval-001", "component": "sizing",
                        "symbol": "2330.TW", "strategy_id": "strat-001", "action": "entry",
                        "hypothesis": "未驗證假說：加碼倍率過高（尚在排隊驗證中，絕不可作為建議顯示）",
                    },
                    {
                        "hypothesis_id": "hyp-luck-001", "demand_id": "demand-luck-001", "component": "exit",
                        "symbol": "2330.TW", "strategy_id": "strat-001", "action": "entry", " outcome": "luck_or_unsupported",
                        "hypothesis": "運氣或無支持假說：尾盤出場時間差（已被判定為 luck_or_unsupported，絕不可顯示）",
                    },
                ],
            },
            {
                "record_id": "attr-002-luck", "tenant_id": TENANT, "subject_kind": "trader", "subject_id": TRADER_USER,
                "window": "2026-W46", "direction": "outperform", "status": "luck_or_unsupported", "luck_or_unsupported": True,
                "metrics": {"sharpe": 2.1, "symbol": "2330.TW", "strategy_id": "strat-001"},
                "decomposition": {"selection": {"contribution_bps": 90.0, "sample_size": 4}},
                "sample_size": 4, "luck_interval": {"ci_95_bps": [-110.0, 240.0], "p_value": 0.42, "status": "luck_or_unsupported"},
                "regime_ref": "macro:expansion-high-vol", "meeting_id": "mtg-mt4-002", "created_at": NOW,
            },
            {
                "record_id": "attr-003-other-symbol", "tenant_id": TENANT, "subject_kind": "persona", "subject_id": "persona-other",
                "window": "2026-W46", "direction": "underperform",
                "metrics": {"sharpe": -0.6, "symbol": "2317.TW", "strategy_id": "strat-999"},
                "decomposition": {"selection": {"contribution_bps": -95.0, "sample_size": 30}},
                "sample_size": 30, "luck_interval": {"ci_95_bps": [-140.0, -40.0], "p_value": 0.01},
                "regime_ref": "macro:contraction", "meeting_id": "mtg-mt4-003", "created_at": NOW,
            },
        ],
        "research_demands": [
            {
                "demand_id": "demand-valid-001", "tenant_id": TENANT, "persona_id": "persona-alpha",
                "attribution_record_id": "attr-001", "meeting_id": "mtg-mt4-001", "component": "entry_timing",
                "symbol": "2330.TW", "strategy_id": "strat-001", "tried_families": ["momentum"],
                "status": "satisfied", "satisfied_by": "val-res-001", "validation_result_id": "val-res-001",
                "hypothesis": "2330.TW 早盤開盤前 15 分鐘追價進場導致 -125 bps 進場時機拖累，等待首根 15m K 線確認可顯著改善",
                "validation_result": {
                    "validation_result_id": "val-res-001", "status": "validated", "passed": True,
                    "oos_sharpe": 1.48, "p_value": 0.004, "sample_size": 68, "effect_bps": 74.0,
                    "summary": "OOS 驗證通過：避開開盤前 15 分鐘進場可提升 +74 bps 期望報酬",
                },
                "created_at": NOW, "updated_at": NOW,
            },
            {
                "demand_id": "demand-unval-001", "tenant_id": TENANT, "persona_id": "persona-alpha",
                "attribution_record_id": "attr-001", "meeting_id": "mtg-mt4-001", "component": "sizing",
                "symbol": "2330.TW", "strategy_id": "strat-001", "status": "open", "validation_status": "unvalidated",
                "hypothesis": "未驗證假說：加碼倍率過高（尚在排隊驗證中，絕不可作為建議顯示）",
                "created_at": NOW, "updated_at": NOW,
            },
            {
                "demand_id": "demand-luck-001", "tenant_id": TENANT, "persona_id": "persona-alpha",
                "attribution_record_id": "attr-001", "meeting_id": "mtg-mt4-001", "component": "exit",
                "symbol": "2330.TW", "strategy_id": "strat-001", "status": "rejected", "validation_status": "luck_or_unsupported",
                "hypothesis": "運氣或無支持假說：尾盤出場時間差（已被判定為 luck_or_unsupported，絕不可顯示）",
                "validation_result": {"validation_result_id": "val-luck-001", "status": "luck_or_unsupported", "passed": False, "p_value": 0.61},
                "created_at": NOW, "updated_at": NOW,
            },
            {
                "demand_id": "demand-other-2317", "tenant_id": TENANT, "persona_id": "persona-other",
                "attribution_record_id": "attr-003-other-symbol", "meeting_id": "mtg-mt4-003", "component": "selection",
                "symbol": "2317.TW", "strategy_id": "strat-999", "status": "satisfied", "satisfied_by": "val-res-2317",
                "hypothesis": "2317.TW 專屬選股過濾條件（與 2330.TW 無關）",
                "validation_result": {"validation_result_id": "val-res-2317", "status": "validated", "passed": True, "oos_sharpe": 1.22},
                "created_at": NOW, "updated_at": NOW,
            },
        ],
        "replication_entries": [
            {
                "admission_id": "adm-strat-001", "tenant_id": TENANT, "strategy_id": "strat-001",
                "strategy_spec_id": "spec-001", "spec_version": "1.0.0", "demand_ref": "demand-valid-001",
                "attribution_record_id": "attr-001", "symbol": "2330.TW", "strategy_family": "momentum",
                "admission_status": "admitted", "queue_status": "completed", "approval_decision_id": "gov-dec-001",
                "authority_run_ids": ["arun-001"], "experiment_run_ids": ["rrun-001"],
                "summary": "已驗證策略事實：spec-001 動能突破策略在 2330.TW 需搭配首 15m 量價過濾與波動率上限",
                "oos_sharpe": 1.62, "created_at": NOW,
            },
            {
                "admission_id": "adm-strat-pending", "tenant_id": TENANT, "strategy_id": "strat-001",
                "strategy_spec_id": "spec-001-pending", "spec_version": "1.1.0", "symbol": "2330.TW",
                "admission_status": "admitted", "queue_status": "pending",
                "summary": "尚未完成複製驗證之策略（queue_status=pending，絕不可作為建議顯示）",
                "created_at": NOW,
            },
            {
                "admission_id": "adm-strat-failed", "tenant_id": TENANT, "strategy_id": "strat-001",
                "strategy_spec_id": "spec-001-failed", "spec_version": "1.2.0", "symbol": "2330.TW",
                "admission_status": "admitted", "queue_status": "failed", "validation_status": "luck_or_unsupported",
                "summary": "複製驗證失敗之策略（luck_or_unsupported，絕不可作為建議顯示）",
                "created_at": NOW,
            },
        ],
        "trade_lessons": [
            {
                "lesson_candidate_id": "lesson-endorsed-001", "reflection_id": "refl-001",
                "trade_episode_ids": ["ep-101", "ep-102", "ep-103"], "persona_id": "persona-alpha",
                "tenant_id": TENANT, "scope": "strategy", "symbol": "2330.TW", "strategy_id": "strat-001",
                "attribution_record_id": "attr-001", "validation_result_id": "demand-valid-001",
                "proposed_change": "已背書交易教訓：2330.TW 進場前確認 15m VWAP 支撐，避免開盤跳空直接追高",
                "confidence": 0.88, "review_state": "endorsed", "created_at": NOW, "updated_at": NOW,
                "expiry": "2027-11-16T00:00:00Z", "reflection_version": "v1", "target_env": "paper",
                "receipt": {"operator_id": "gov-reviewer", "decided_at": NOW, "action": "endorse", "reason": "Validated OOS", "audit_receipt_id": "audit-lesson-001"},
            },
            {
                "lesson_candidate_id": "lesson-proposed-unval", "reflection_id": "refl-002",
                "trade_episode_ids": ["ep-104"], "persona_id": "persona-alpha", "tenant_id": TENANT,
                "scope": "strategy", "symbol": "2330.TW", "strategy_id": "strat-001",
                "proposed_change": "未背書候選教訓（review_state=proposed，絕不可顯示）",
                "confidence": 0.55, "review_state": "proposed", "created_at": NOW, "updated_at": NOW,
                "expiry": "2027-11-16T00:00:00Z", "reflection_version": "v1", "target_env": "paper",
            },
            {
                "lesson_candidate_id": "lesson-rejected-luck", "reflection_id": "refl-003",
                "trade_episode_ids": ["ep-105"], "persona_id": "persona-alpha", "tenant_id": TENANT,
                "scope": "strategy", "symbol": "2330.TW", "strategy_id": "strat-001",
                "proposed_change": "已退回運氣教訓（review_state=rejected / luck_or_unsupported，絕不可顯示）",
                "confidence": 0.30, "review_state": "rejected", "validation_status": "luck_or_unsupported",
                "created_at": NOW, "updated_at": NOW, "expiry": "2027-11-16T00:00:00Z", "reflection_version": "v1", "target_env": "paper",
            },
        ],
        "memory_entries": [],
    }


@pytest.fixture()
def http_edge_world(monkeypatch: pytest.MonkeyPatch):
    reset_default_trading_room_store()
    monkeypatch.setenv("PANTHEON_EVALUATION_API_URL", "http://evaluation.test")
    monkeypatch.setenv("PANTHEON_EVALUATION_SERVICE_TOKEN", "eval-svc-tok")
    monkeypatch.setenv("PANTHEON_RESEARCH_API_URL", "http://research.test")
    monkeypatch.setenv("PANTHEON_RESEARCH_SERVICE_TOKEN", "research-svc-tok")
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory.test")
    monkeypatch.setenv("PANTHEON_MEMORY_SERVICE_TOKEN", "memory-svc-tok")

    state = _seed_owner_fixtures()
    state["http_calls"] = []
    state["outage_owner"] = None
    state["http_error_owner"] = None

    def fake_urlopen(req: Any, timeout: float = 3.0) -> Any:
        url = req.full_url
        method = req.get_method()
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        path = parsed.path
        headers = {k.lower(): v for k, v in req.header_items()}
        state["http_calls"].append({"method": method, "url": url, "host": host, "path": path, "headers": headers})
        assert method == "GET", f"Servant institutional knowledge must be strictly read-only GET, got {method}"
        if state["outage_owner"] == host:
            raise URLError(f"{host} connection refused")
        if state["http_error_owner"] == host:
            raise HTTPError(url, 503, "Service Unavailable", hdrs=None, fp=io.BytesIO(b'{"detail":"down"}'))

        if host == "evaluation.test" and path == "/api/attribution/records":
            body: Any = {"items": state["attribution_records"], "next_cursor": None}
        elif host == "research.test" and path == "/api/research/persona-demands":
            body = {"demands": state["research_demands"], "count": len(state["research_demands"])}
        elif host == "research.test" and path == "/api/research-orchestrator/alpha-replication/entries":
            body = state["replication_entries"]
        elif host == "memory.test" and path == "/api/memory/trade-lessons":
            body = {"candidates": state["trade_lessons"], "count": len(state["trade_lessons"])}
        elif host == "memory.test" and path == "/api/memory/entries":
            body = {"entries": state["memory_entries"], "count": len(state["memory_entries"])}
        else:
            raise HTTPError(url, 404, "Not Found", hdrs=None, fp=io.BytesIO(b'{"detail":"not found"}'))

        stream = io.BytesIO(json.dumps(body).encode("utf-8"))
        stream.status = 200  # type: ignore[attr-defined]
        return stream

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    tr_store = make_trading_room_store()
    tr_store.upsert_decision_event(_make_decision_event("evt-ik-2330"))
    tr_store.upsert_decision_event(
        _make_decision_event("evt-other-user", user_id=OTHER_USER, symbol="2330.TW", strategy_id="strat-001")
    )
    active_identity: Dict[str, Any] = {"current": copy.deepcopy(TRADER_IDENTITY)}

    def bff_error(status_code: int, code: Any, message: str, reason: str, **kwargs: Any) -> HTTPException:
        code_str = code.value if hasattr(code, "value") else str(code)
        detail: Dict[str, Any] = {"code": code_str, "message": message, "reason": reason, **kwargs}
        if isinstance(kwargs.get("details_extra"), dict):
            for k in ("status", "owner", "reason", "fallback_used"):
                if k in kwargs["details_extra"]:
                    detail[k] = kwargs["details_extra"][k]
        return HTTPException(status_code=status_code, detail=detail)

    app = FastAPI()
    app.include_router(
        create_servant_router(
            extract_identity=lambda _auth, session_cookie=None: copy.deepcopy(active_identity["current"]),
            require_read_role=lambda _id: None,
            require_write_role=lambda _id: None,
            bff_error=bff_error,
            utc_now=lambda: NOW,
            get_read_store=lambda: None,
            sync_servant_agent=lambda _p: {},
            trading_room_store=tr_store,
        )
    )
    yield {"client": TestClient(app), "state": state, "tr_store": tr_store, "active_identity": active_identity}
    reset_default_trading_room_store()


def test_servant_reads_validated_institutional_facts_with_attribution_and_validation_links(http_edge_world):
    client: TestClient = http_edge_world["client"]
    state = http_edge_world["state"]
    resp = client.get("/bff/agora/servant/institutional-knowledge", headers={"Authorization": "Bearer caller-jwt"})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "ok"
    data = payload["data"]
    assert data["read_only"] is True and data["advice_only"] is True
    assert data["execution_authority"] == "none" and data["environment_ceiling"] == "paper"

    items = data["items"]
    assert {item["fact_kind"] for item in items} == {"validated_attribution_finding", "endorsed_lesson", "validated_strategy_fact"}
    for item in items:
        assert item["links"]["attribution_record"].startswith("/api/attribution/records/")
        assert item["links"]["validation_result"].startswith("/api/")
        assert item["attribution_record_id"] and item["validation_result_id"]
        assert item["evidence"]["evidence_refs"]

    all_text = json.dumps(items, ensure_ascii=False)
    for forbidden in (
        "hyp-unval-001", "demand-unval-001", "hyp-luck-001", "demand-luck-001", "attr-002-luck",
        "adm-strat-pending", "spec-001-pending", "adm-strat-failed", "spec-001-failed",
        "lesson-proposed-unval", "lesson-rejected-luck",
    ):
        assert forbidden not in all_text
    assert data["counts"]["excluded_unvalidated"] >= 3
    assert data["counts"]["excluded_luck_or_unsupported"] >= 3
    assert all(c["method"] == "GET" for c in state["http_calls"])


def test_trading_room_returns_relevant_system_suggestions_with_evidence_and_executes_nothing(http_edge_world):
    client: TestClient = http_edge_world["client"]
    tr_store = http_edge_world["tr_store"]
    state = http_edge_world["state"]

    resp = client.get(
        "/bff/agora/trading-room/decision-events/evt-ik-2330/system-suggestions",
        headers={"Authorization": "Bearer caller-jwt"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["decision_event_id"] == "evt-ik-2330"
    assert data["read_only"] is True and data["advice_only"] is True
    assert data["execution_authority"] == "none" and data["no_order_route_proof"] == "agora_decision_support_only"

    suggestions = data["suggestions"]
    assert len(suggestions) == 3
    assert {s["fact_kind"] for s in suggestions} == {"validated_attribution_finding", "endorsed_lesson", "validated_strategy_fact"}
    for sug in suggestions:
        assert sug["decision_event_id"] == "evt-ik-2330"
        assert sug["advice_only"] is True and sug["read_only"] is True and sug["execution_authority"] == "none"
        assert sug["attribution_record_id"] == "attr-001"
        assert sug["links"]["attribution_record"] == "/api/attribution/records/attr-001"
        assert sug["links"]["validation_result"].startswith("/api/")
        assert sug["evidence_refs"]
        assert "symbol_match" in sug["relevance_reasons"] and "strategy_match" in sug["relevance_reasons"]

    sug_text = json.dumps(suggestions, ensure_ascii=False)
    for excluded in ("2317.TW", "demand-other-2317", "attr-003-other-symbol", "hyp-unval-001", "hyp-luck-001", "lesson-proposed-unval", "lesson-rejected-luck"):
        assert excluded not in sug_text
    assert tr_store._intents == {} and tr_store._handoffs == {} and tr_store._trader_decisions == {}
    assert tr_store.get_decision_event("evt-ik-2330")["state"] == "pending_review"
    assert all(c["method"] == "GET" for c in state["http_calls"])

    tr_resp = client.get(
        "/bff/agora/trading-room/system-suggestions?symbol=2330.TW&strategy_id=strat-001&action=entry",
        headers={"Authorization": "Bearer caller-jwt"},
    )
    assert tr_resp.status_code == 200
    assert len(tr_resp.json()["data"]["suggestions"]) == 3


def test_decision_event_suggestions_enforce_user_and_tenant_scope(http_edge_world):
    client: TestClient = http_edge_world["client"]
    resp = client.get(
        "/bff/agora/trading-room/decision-events/evt-other-user/system-suggestions",
        headers={"Authorization": "Bearer caller-jwt"},
    )
    assert resp.status_code == 404
    missing = client.get(
        "/bff/agora/trading-room/decision-events/evt-does-not-exist/system-suggestions",
        headers={"Authorization": "Bearer caller-jwt"},
    )
    assert missing.status_code == 404


@pytest.mark.parametrize(
    ("offline_host", "expected_owner"),
    [("evaluation.test", "attribution"), ("research.test", "research"), ("memory.test", "memory")],
)
def test_owner_outage_fails_closed_without_fallback(http_edge_world, offline_host: str, expected_owner: str):
    client: TestClient = http_edge_world["client"]
    http_edge_world["state"]["outage_owner"] = offline_host
    for path in (
        "/bff/agora/servant/institutional-knowledge",
        "/bff/agora/trading-room/decision-events/evt-ik-2330/system-suggestions",
    ):
        resp = client.get(path, headers={"Authorization": "Bearer caller-jwt"})
        assert resp.status_code == 503, resp.text
        detail = resp.json()["detail"]
        assert detail["status"] == "unavailable" and detail["owner"] == expected_owner
        assert detail["reason"] == f"{expected_owner}_owner_unavailable" and detail["fallback_used"] is False


def test_owner_http_503_and_unconfigured_url_and_tenant_mismatch_fail_closed(http_edge_world, monkeypatch):
    client: TestClient = http_edge_world["client"]
    state = http_edge_world["state"]
    state["http_error_owner"] = "research.test"
    r1 = client.get("/bff/agora/trading-room/system-suggestions", headers={"Authorization": "Bearer caller-jwt"})
    assert r1.status_code == 503 and r1.json()["detail"]["owner"] == "research"
    assert r1.json()["detail"]["reason"] == "research_owner_http_503"
    state["http_error_owner"] = None

    monkeypatch.delenv("PANTHEON_EVALUATION_API_URL", raising=False)
    r2 = client.get("/bff/agora/servant/institutional-knowledge", headers={"Authorization": "Bearer caller-jwt"})
    assert r2.status_code == 503 and r2.json()["detail"]["reason"] == "attribution_owner_unconfigured"
    monkeypatch.setenv("PANTHEON_EVALUATION_API_URL", "http://evaluation.test")

    state["attribution_records"][0]["tenant_id"] = "tenant-foreign"
    r3 = client.get("/bff/agora/servant/institutional-knowledge", headers={"Authorization": "Bearer caller-jwt"})
    assert r3.status_code == 503 and r3.json()["detail"]["reason"] == "attribution_record_tenant_mismatch"


def test_real_research_and_memory_owners_integration_with_c3_attribution_double(tmp_path, monkeypatch):
    reset_default_trading_room_store()
    secret = "test-only-institutional-knowledge-jwt-secret"
    for prefix in ("PANTHEON_BFF", "PANTHEON_MEMORY", "PANTHEON_GOVERNANCE", "RESEARCH"):
        monkeypatch.setenv(f"{prefix}_AUTH_MODE", "strict")
        monkeypatch.setenv(f"{prefix}_JWT_SECRET", secret)
        monkeypatch.setenv(f"{prefix}_JWT_ISSUER", "")
        monkeypatch.setenv(f"{prefix}_JWT_AUDIENCE", "")
    for dir_env in ("PANTHEON_MEMORY_DATA_DIR", "GOVERNANCE_DATA_DIR", "BFF_DATA_DIR", "ALPHA_REPLICATION_DATA_DIR"):
        monkeypatch.setenv(dir_env, str(tmp_path / dir_env))
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "inst_memory.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "persona_memory.json"))
    monkeypatch.setenv("PANTHEON_TRADE_LESSON_CANDIDATE_STORE", str(tmp_path / "trade_lessons.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_STORE_BACKEND", "json")
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE_BACKEND", "json")
    monkeypatch.setenv("PANTHEON_EVALUATION_API_URL", "http://evaluation.test")
    monkeypatch.setenv("PANTHEON_EVALUATION_SERVICE_TOKEN", "svc-eval-token")
    monkeypatch.setenv("PANTHEON_RESEARCH_API_URL", "http://research.test")
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory.test")

    from services.memory import main as memory_main
    from services.research import main as research_main
    from services.research.alpha_replication.admission import ReplicationAdmissionStore
    from services.research.alpha_replication.queue import AlphaReplicationQueue
    from services.research.research_demand import ResearchDemandStore

    demand_store = ResearchDemandStore(tmp_path / "research_demands.json")
    adm_store = ReplicationAdmissionStore(tmp_path / "alpha_rep")
    q_store = AlphaReplicationQueue(tmp_path / "alpha_rep")
    monkeypatch.setattr(research_main, "research_demand_store", demand_store)
    monkeypatch.setattr(research_main, "alpha_replication_admission_store", adm_store)
    monkeypatch.setattr(research_main, "alpha_replication_queue", q_store)

    demand_row = demand_store.open(
        TENANT,
        {
            "persona_id": "persona-alpha", "mandate": "TW semiconductor entry timing",
            "missing_datasets": [], "tried_families": ["momentum"],
            "short_measure": "effective_bets", "pool_version": 1,
            "evidence_ref": "meeting:mtg-real-001:attr-real-001",
        },
    )
    demand_store.reconcile(TENANT, "persona-alpha", [{"artifact_ref": "spec-real-001", "joined_at": "2099-01-01T00:00:00Z"}])
    adm_store.create_admission({
        "admission_id": "adm-real-001", "tenant_id": TENANT, "persona_id": "persona-alpha",
        "demand_ref": demand_row["demand_id"], "strategy_spec_id": "spec-real-001", "strategy_id": "strat-001",
        "spec_version": "1.0.0", "checksum": "sha256:" + "1" * 64, "approval_decision_id": "gov-real-001",
        "approver": "gate-reviewer", "approved_at": NOW, "requested_by": "research-orchestrator",
        "review_ref": "review://gov-real-001", "mode": "initial", "status": "admitted",
    })
    q_store.enqueue(
        {
            "tenant_id": TENANT, "persona_id": "persona-alpha", "demand_ref": demand_row["demand_id"],
            "strategy_spec_id": "spec-real-001", "strategy_id": "strat-001", "spec_version": "1.0.0",
            "artifact_state": "approved", "checksum": "sha256:" + "1" * 64,
            "approval_decision_id": "gov-real-001", "approver": "gate-reviewer", "approved_at": NOW,
        },
        enqueued_by="test",
    )
    claimed = q_store.claim_next_pending(TENANT, claimant="worker-1")
    assert claimed is not None
    assert q_store.mark_revalidated(
        TENANT, "spec-real-001", claim_token=claimed["claim_token"],
        authority_task_id="atask-1", authority_run_id="arun-real-1",
        experiment_task_id="rtask-1", experiment_run_id="rrun-real-1", status="completed",
    )

    memory_client = TestClient(memory_main.app)
    research_client = TestClient(research_main.app)
    jwt_tok = encode_jwt_hs256(
        {"sub": "persona-pool-cycle", "roles": ["operator", "viewer", "service", "memory-writer", "memory-reader"], "tenant_id": TENANT, "exp": 4102444800},
        secret=secret,
    )
    auth_header = f"Bearer {jwt_tok}"

    endorsed_id = "11111111-1111-4111-8111-111111111101"
    proposed_id = "11111111-1111-4111-8111-111111111102"
    for cid, text in ((endorsed_id, "真實 Memory Owner 已背書教訓：2330.TW 突破需搭配量能確認"), (proposed_id, "真實 Memory Owner 未背書教訓（不應顯示）")):
        r = memory_client.post(
            "/api/memory/trade-lessons",
            headers={"Authorization": auth_header},
            json={
                "lesson_candidate_id": cid, "reflection_id": "22222222-2222-4222-8222-222222222201",
                "trade_episode_ids": ["33333333-3333-4333-8333-333333333301"], "persona_id": "persona-alpha",
                "scope": "strategy", "proposed_change": text, "confidence": 0.85, "review_state": "proposed",
                "created_at": NOW, "updated_at": NOW, "expiry": "2099-01-01T00:00:00Z",
                "reflection_version": "v1", "target_env": "paper",
            },
        )
        assert r.status_code == 201, r.text

    cand_record = memory_main._candidate_store().get(endorsed_id)
    assert cand_record is not None
    cand_record["review_state"] = "endorsed"
    cand_record["receipt"] = {
        "operator_id": "persona-pool-cycle", "decided_at": NOW, "action": "endorse",
        "reason": "Validated against attr-real-001 and demand-real-001",
        "audit_receipt_id": "44444444-4444-4444-8444-444444444401",
    }
    memory_main._candidate_store().update(cand_record)

    c3_records = [{
        "record_id": "attr-real-001", "tenant_id": TENANT, "subject_kind": "trader", "subject_id": TRADER_USER,
        "window": "2026-W46", "direction": "underperform",
        "metrics": {"sharpe": -0.5, "symbol": "2330.TW", "strategy_id": "strat-001"},
        "decomposition": {"entry_timing": {"contribution_bps": -110.0, "sample_size": 22}},
        "sample_size": 22, "luck_interval": {"ci_95_bps": [-170.0, -50.0], "p_value": 0.01, "resamples": 1000},
        "regime_ref": "macro:expansion", "meeting_id": "mtg-real-001", "created_at": NOW,
    }]

    def bridge_urlopen(req: Any, timeout: float = 3.0) -> Any:
        u = urlsplit(req.full_url)
        host = u.hostname or ""
        path_with_query = u.path + (f"?{u.query}" if u.query else "")
        headers = {k: v for k, v in req.header_items()}
        if host == "evaluation.test" and u.path == "/api/attribution/records":
            stream = io.BytesIO(json.dumps({"items": c3_records, "next_cursor": None}).encode("utf-8"))
            stream.status = 200  # type: ignore[attr-defined]
            return stream
        r = (research_client if host == "research.test" else memory_client).request(req.method, path_with_query, headers=headers)
        if r.status_code >= 400:
            raise HTTPError(req.full_url, r.status_code, "upstream error", {}, io.BytesIO(r.content))
        stream = io.BytesIO(r.content)
        stream.status = r.status_code  # type: ignore[attr-defined]
        return stream

    monkeypatch.setattr("urllib.request.urlopen", bridge_urlopen)
    tr_store = make_trading_room_store()
    tr_store.upsert_decision_event(_make_decision_event("evt-real-1", strategy_spec_registry_id="spec-real-001"))

    app = FastAPI()
    app.include_router(
        create_servant_router(
            extract_identity=lambda _a, session_cookie=None: copy.deepcopy(TRADER_IDENTITY),
            require_read_role=lambda _i: None,
            require_write_role=lambda _i: None,
            bff_error=lambda status, code, msg, reason, **kw: HTTPException(status_code=status, detail={"reason": reason, **kw}),
            utc_now=lambda: NOW,
            get_read_store=lambda: None,
            sync_servant_agent=lambda _p: {},
            trading_room_store=tr_store,
        )
    )
    client = TestClient(app)
    resp = client.get("/bff/agora/trading-room/decision-events/evt-real-1/system-suggestions", headers={"Authorization": auth_header})
    assert resp.status_code == 200, resp.text
    suggestions = resp.json()["data"]["suggestions"]
    assert {s["fact_kind"] for s in suggestions} == {"validated_attribution_finding", "endorsed_lesson", "validated_strategy_fact"}
    raw_text = json.dumps(suggestions, ensure_ascii=False)
    assert endorsed_id in raw_text and "spec-real-001" in raw_text and proposed_id not in raw_text
    reset_default_trading_room_store()
