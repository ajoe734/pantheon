"""Unit and contract tests for Strategy Reconstruction engine & worker."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

from services.control_plane.bff.agora.strategy_workshop.reconstruction import (
    StrategyReconstructionResult,
    reconstruct_strategy_from_events,
)


def test_strategy_reconstruction_from_basic_messages() -> None:
    workshop_id = "ws-test-recon-001"
    messages = [
        "I want to create a momentum strategy for BTC and ETH.",
        "The signal will be a 20-day moving average crossover with 2% stop loss.",
    ]
    events = [
        {"event_id": "evt-1", "sequence_no": 1, "created_at": "2026-08-13T12:00:00Z"},
        {"event_id": "evt-2", "sequence_no": 2, "created_at": "2026-08-13T12:01:00Z"},
    ]

    result = reconstruct_strategy_from_events(
        workshop_id=workshop_id,
        sequence_no=2,
        events=events,
        messages_content=messages,
    )

    assert isinstance(result, StrategyReconstructionResult)
    assert result.workshop_id == workshop_id
    assert result.based_on_sequence_no == 2

    # The validator does not interpret messages or invent semantic facts.
    assert result.strategy_map.universe.status == "missing"
    assert result.strategy_map.signal_definition.status == "missing"
    assert result.strategy_map.risk_controls.status == "missing"
    assert result.completeness.grade == "insufficient"
    assert result.explicit_facts == []
    assert result.next_best_question is not None
    assert isinstance(result.next_best_question.text, str)
    assert len(result.next_best_question.resolves) > 0

    # Completeness check
    assert result.completeness.grade in {"draftable", "researchable", "trading_room_ready", "insufficient"}


def test_strategy_reconstruction_nbq_uniqueness_and_completeness_derivation() -> None:
    workshop_id = "ws-test-recon-002"
    messages = ["Hello, I want to trade."]
    result = reconstruct_strategy_from_events(
        workshop_id=workshop_id,
        sequence_no=1,
        events=[{"event_id": "evt-1", "sequence_no": 1}],
        messages_content=messages,
    )

    assert result.completeness.grade == "insufficient"
    assert "Semantic reconstruction unavailable" in result.completeness.blockers
    assert result.next_best_question is not None
    # NBQ must resolve hypothesis
    assert "hypothesis.summary" in result.next_best_question.resolves


def test_reconstruct_endpoint_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from fastapi import FastAPI, HTTPException
    from services.control_plane.bff.agora.strategy_workshop.router import create_strategy_workshop_router
    from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore
    from types import SimpleNamespace

    store = MemoryWorkshopStore()
    
    def stub_extract_identity(auth_header: str | None, mfa_token: str | None = None):
        return SimpleNamespace(
            user_id="user-test",
            tenant_id="tenant-test",
            roles=["read", "write"],
            token_kind="stub",
            mfa_verified=True,
        )

    def stub_require_role(identity):
        pass

    def stub_error(status_code, code, message, reason=None, **kwargs):
        return HTTPException(status_code=status_code, detail=message)

    def stub_utc_now():
        return "2026-08-13T12:00:00Z"

    # Patch scope resolution to avoid top-level models import dependency in unit test
    import services.control_plane.bff.agora.identity.scope as scope_module
    monkeypatch.setattr(
        scope_module,
        "resolve_agora_user_scope",
        lambda identity, utc_now=None, requested_tenant_id=None: SimpleNamespace(
            user_id=identity.user_id, tenant_id=identity.tenant_id
        ),
    )

    router = create_strategy_workshop_router(
        extract_identity=stub_extract_identity,
        require_read_role=stub_require_role,
        require_write_role=stub_require_role,
        bff_error=stub_error,
        utc_now=stub_utc_now,
        workshop_store=store,
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    session = store.create_session({
        "workshop_id": "ws-test-recon-123",
        "tenant_id": "tenant-test",
        "user_id": "user-test",
    })
    ws_id = session["workshop_id"]

    resp = client.post(
        f"/bff/agora/workshops/{ws_id}/reconstruct",
        headers={"Authorization": "Bearer user-test:tenant-test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert body["data"]["workshop_id"] == ws_id
    assert "strategy_map" in body["data"]
    assert "next_best_question" in body["data"]

    from .semantic_provider import OpenClawOpsClient, OpenClawOpsClientError
    def unavailable(*args, **kwargs):
        raise OpenClawOpsClientError("private upstream error", status_code=503, error_code="UNAVAILABLE")
    monkeypatch.setattr(OpenClawOpsClient, "_request", unavailable)
    store.create_event({"workshop_id": ws_id, "event_type": "message", "actor_type": "operator",
                        "redacted_summary": "I have not defined an entry rule"})
    failed = client.post(f"/bff/agora/workshops/{ws_id}/reconstruct",
                         headers={"Authorization": "Bearer user-test:tenant-test"})
    assert failed.status_code == 503
    assert "private upstream error" not in failed.text



# --------------------------------------------------------------------------- #
# AGORA-WORKSHOP-RECONSTRUCTION-HOSTED-20261127: message admission only queues.
# --------------------------------------------------------------------------- #
_DRAFT = {
    "strategy_map": {"hypothesis": {"status": "confirmed", "summary": "Momentum alpha",
                                    "details": {"message_numbers": [1]}}},
    "explicit_facts": ["Momentum alpha"], "inferences": [], "assumptions": [],
    "contradictions": [], "strategy_spec": None,
    "next_best_question": {"question_id": "q1", "text": "Which signal?",
                           "resolves": ["signal_definition"], "why_now": "Signal is missing"},
}


def _route_client(monkeypatch, provider):
    # The shared BFF harness imports `agora.*` relative to the BFF root.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from services.control_plane.bff.agora.strategy_workshop.semantic_provider import OpenClawOpsClient
    from services.control_plane.bff.tests import test_agora_strategy_workshop as harness

    calls: list = []

    def request(self, method, path, **kwargs):
        calls.append(path)
        return provider()

    monkeypatch.setattr(OpenClawOpsClient, "_request", request)
    client = harness._workshop_client(monkeypatch)
    created = client.post("/bff/agora/workshops", headers={
        "Authorization": harness._OPERATOR_AUTH, "Idempotency-Key": "recon-hosted-create",
    }, json={"initial_message": "Hypothesis: momentum alpha."})
    assert created.status_code == 201, created.text
    workshop_id = created.json()["data"]["workshop_id"]

    def post_message(text: str, key: str):
        return client.post(f"/bff/agora/workshops/{workshop_id}/messages", headers={
            "Authorization": harness._OPERATOR_AUTH, "Idempotency-Key": key,
            "If-Match": harness._get_current_etag(client, workshop_id),
        }, json={"content": text})

    def reconstruct():
        return client.post(f"/bff/agora/workshops/{workshop_id}/reconstruct",
                           headers={"Authorization": harness._OPERATOR_AUTH})

    def card():
        listed = client.app_instance.workshop_store.list_workshop_cards(workshop_id)
        return next(c for c in listed if c["card_type"] == "strategy_reconstruction")

    return calls, post_message, reconstruct, card


def _envelope():
    return {"status": "ok", "data": {"provider": "openclaw", "status": "completed",
                                        "output": {"structured_data": _DRAFT}}}


def test_message_route_queues_the_job_without_running_the_provider(monkeypatch):
    from services.control_plane.bff.agora.strategy_workshop.routes import session as session_routes

    deferred: list = []
    monkeypatch.setattr(session_routes, "run_reconstruction_job", lambda **kw: deferred.append(kw))
    monkeypatch.setenv("PANTHEON_WORKSHOP_RECONSTRUCTION_WAIT_SECONDS", "0")
    calls, post_message, reconstruct, card = _route_client(monkeypatch, _envelope)

    response = post_message("Universe: SPY equities, daily.", "recon-hosted-msg-1")
    assert response.status_code == 202, response.text
    job = response.json()["data"]["reconstruction_job"]
    assert job["job_status"] == "queued" and job["sequence_no"] == response.json()["data"]["sequence_no"]
    assert calls == [], "message admission must not call the provider"
    assert [kw["job_id"] for kw in deferred] == [job["job_id"]]
    assert card()["payload"]["job_status"] == "queued"
    # /reconstruct while the job is queued returns it (202) without a call.
    pending = reconstruct()
    assert pending.status_code == 202, pending.text
    assert pending.json()["meta"]["job_status"] == "queued"
    assert pending.json()["meta"]["job_id"] == job["job_id"]
    assert calls == []


def test_one_provider_call_per_message_and_reconstruct_replays_it(monkeypatch):
    calls, post_message, reconstruct, card = _route_client(monkeypatch, _envelope)
    for index in range(2):
        response = post_message(f"Universe step {index}", f"recon-hosted-msg-{index}")
        assert response.status_code == 202, response.text
        # The queued job ran after the response (background task).
        assert len(calls) == index + 1
        assert card()["status"] == "completed"
        result = reconstruct()
        assert result.status_code == 200, result.text
        assert result.json()["meta"]["job_status"] == "replayed"
        assert result.json()["data"]["reconstruction_id"]
        assert result.json()["data"]["based_on_sequence_no"] == response.json()["data"]["sequence_no"]
        assert len(calls) == index + 1, "a second /reconstruct for the same message must not call the provider"


def test_failed_job_card_and_api_carry_the_typed_reason(monkeypatch):
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClientError

    def refused():
        raise OpenClawOpsClientError("tool call argument 'root' declares unsupported schema keyword(s) ['$defs']",
                                     status_code=422, error_code="OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH")

    calls, post_message, reconstruct, card = _route_client(monkeypatch, refused)
    response = post_message("A long strategy description.", "recon-hosted-fail")
    assert response.status_code == 202, response.text
    failed = card()
    assert failed["status"] == "failed"
    assert failed["payload"]["failure_reason"] == "provider_output_schema_mismatch"
    assert failed["payload"]["adapter_error_code"] == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
    api = reconstruct()
    assert api.status_code == 502, api.text
    details = api.json()["error"]["details"]
    assert details["reason"] == "provider_output_schema_mismatch"
    assert details["adapter_error_code"] == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
    assert "$defs" not in api.text and "$defs" not in str(failed)
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# AGORA-WORKSHOP-TWELVE-BLOCK-COMPLETENESS-20261127: twelve V10 blocks,       #
# branch vs non-branch naming, 5 states, notes, citations, next decision,     #
# and POST /bff/agora/workshops/{id}/decisions -> next reconstruction.        #
# --------------------------------------------------------------------------- #
_EXPECTED_V10_BLOCK_ORDER = [
    "market_scope",
    "insider_branch_mapping",
    "winner_branch_scoring",
    "migration_reverse_flow",
    "event_lead",
    "signal_formation",
    "entry_holding",
    "add_reduce_exit",
    "sizing_leverage",
    "cost_liquidity_capacity",
    "validation_backtest_refutation",
    "monitoring_update",
]

_EXPECTED_BRANCH_TITLES_ZH = [
    "研究對象與市場範圍",
    "關係人與分點映射",
    "贏家分點評分",
    "分點遷移與反向流",
    "事件領先關聯",
    "信號形成",
    "進場與持有週期",
    "加碼、減碼與出場",
    "部位與槓桿",
    "成本、流動性與容量",
    "驗證、回測與反證",
    "監控與策略更新",
]


def test_branch_vs_non_branch_twelve_blocks_and_five_states(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from services.control_plane.bff.agora.strategy_workshop.semantic_provider import OpenClawOpsClient
    from services.control_plane.bff.tests import test_agora_strategy_workshop as harness

    branch_draft = {
        "strategy_map": {
            "universe": {
                "status": "confirmed",
                "summary": "台股上市櫃高流動性股票，聚焦有關係人持股與活躍券商分點之標的",
                "details": {"message_numbers": [1]},
            },
            "data_requirements": {
                "status": "partial",
                "summary": "以關係人持股變化與分點逐日買賣建立機率映射",
            },
            "hypothesis": {
                "status": "confirmed",
                "summary": "識別具備持續獲利能力的贏家分點作為選股核心",
                "details": {"message_numbers": [1]},
            },
            "regime_invalidation": {
                "status": "partial",
                "summary": "分點買入後若另一分點突然大量賣出需降權",
                "details": {"missing_definitions": ["跨分點轉倉降權門檻"]},
            },
            "risk_controls": {
                "status": "partial",
                "summary": "使用公告後才可見的財報資料作為事前領先信號",
                "details": {"conflict": True},
            },
            "signal_definition": {
                "status": "missing",
                "summary": None,
            },
        },
        "explicit_facts": ["台股贏家分點與關係人持股策略"],
        "inferences": ["分點群組以歷史共現與同券商關係建立"],
        "assumptions": [],
        "contradictions": ["risk_controls: 若使用公告後可見資料，不能宣稱是事前領先訊號"],
        "strategy_spec": None,
        "next_best_question": {
            "question_id": "define_winner_branch_signal",
            "text": "請裁示贏家分點異動轉化為進場信號的確認條件：",
            "resolves": ["signal_definition"],
            "why_now": "信號形成目前尚缺，確認後才能建立候選池與回測",
        },
    }

    non_branch_draft = {
        "strategy_map": {
            "universe": {
                "status": "confirmed",
                "summary": "AAPL、MSFT、NVDA、2330.TW 等大型科技龍頭，日頻資料",
                "details": {"message_numbers": [1]},
            },
            "hypothesis": {
                "status": "confirmed",
                "summary": "60 日截面動能在大型科技股具 20 日延續性",
                "details": {"message_numbers": [1]},
            },
            "signal_definition": {
                "status": "partial",
                "summary": "60d/20d 報酬 0.7/0.3 加權排名取前 3 名",
                "details": {"missing_definitions": ["VIX 過濾門檻"]},
            },
            "entry_rules": {
                "status": "partial",
                "summary": "週一開盤限價進場，持有 20～40 個交易日",
            },
        },
        "explicit_facts": ["跨市場大型科技股截面動能輪動"],
        "inferences": ["以每週為再平衡節奏"],
        "assumptions": [],
        "contradictions": [],
        "strategy_spec": None,
        "next_best_question": {
            "question_id": "nbq_momentum_signal",
            "text": "VIX 過濾門檻要固定在 30 還是以歷史分位數動態調整？",
            "resolves": ["signal_definition"],
            "why_now": "確認波動過濾條件後即可產生第一版可回測草稿",
        },
    }

    current_payload = {"data": branch_draft}
    monkeypatch.setattr(
        OpenClawOpsClient,
        "_request",
        lambda self, *a, **kw: {
            "status": "ok",
            "data": {
                "provider": "openclaw",
                "status": "completed",
                "output": {"structured_data": current_payload["data"]},
            },
        },
    )
    client = harness._workshop_client(monkeypatch)

    # 1. Branch-based strategy workshop
    created_branch = client.post(
        "/bff/agora/workshops",
        headers={"Authorization": harness._OPERATOR_AUTH, "Idempotency-Key": "twelve-branch-create"},
        json={"initial_message": "從關係人持股與券商分點進出建立贏家分點評分與分點遷移模型。"},
    )
    assert created_branch.status_code == 201, created_branch.text
    branch_ws_id = created_branch.json()["data"]["workshop_id"]

    recon_branch = client.post(
        f"/bff/agora/workshops/{branch_ws_id}/reconstruct",
        headers={"Authorization": harness._OPERATOR_AUTH},
    )
    assert recon_branch.status_code == 200, recon_branch.text

    comp_branch_resp = client.get(
        f"/bff/agora/workshops/{branch_ws_id}/completeness",
        headers={"Authorization": harness._OPERATOR_AUTH},
    )
    assert comp_branch_resp.status_code == 200, comp_branch_resp.text
    comp_branch = comp_branch_resp.json()["data"]
    assert comp_branch is not None
    assert comp_branch["is_branch_strategy"] is True
    assert comp_branch["strategy_family"] == "winner_branch"

    blocks = comp_branch["blocks"]
    assert len(blocks) == 12
    assert [b["order"] for b in blocks] == list(range(1, 13))
    assert [b["v10_block_id"] for b in blocks] == _EXPECTED_V10_BLOCK_ORDER
    assert [b["block_id"] for b in blocks] == _EXPECTED_V10_BLOCK_ORDER
    assert [b["name"] for b in blocks] == _EXPECTED_BRANCH_TITLES_ZH

    by_v10 = {b["v10_block_id"]: b for b in blocks}
    assert by_v10["market_scope"]["state"] == "confirmed"
    assert by_v10["market_scope"]["state_label_zh"] == "已確認"
    assert by_v10["market_scope"]["source_citations"], "confirmed block must carry source citations"
    assert by_v10["insider_branch_mapping"]["state"] == "inferred"
    assert by_v10["insider_branch_mapping"]["state_label_zh"] == "助理推定"
    assert by_v10["migration_reverse_flow"]["state"] == "weak"
    assert by_v10["migration_reverse_flow"]["state_label_zh"] == "薄弱"
    assert by_v10["event_lead"]["state"] == "conflict"
    assert by_v10["event_lead"]["state_label_zh"] == "衝突"
    assert by_v10["signal_formation"]["state"] == "missing"
    assert by_v10["signal_formation"]["state_label_zh"] == "尚缺"
    for b in blocks:
        assert b["state"] in {"confirmed", "inferred", "missing", "weak", "conflict"}
        assert isinstance(b["note"], str) and b["note"].strip() and "\n" not in b["note"]
        assert isinstance(b["source_citations"], list)

    # completeness_update card carries the same 12 blocks and next_decision
    cards_branch = client.get(
        f"/bff/agora/workshops/{branch_ws_id}/cards",
        headers={"Authorization": harness._OPERATOR_AUTH},
    ).json()["data"]
    comp_card = next(c for c in cards_branch if c["card_type"] == "completeness_update")
    assert [b["v10_block_id"] for b in comp_card["payload"]["blocks"]] == _EXPECTED_V10_BLOCK_ORDER
    assert comp_card["payload"]["next_decision"]["decision_id"] == "define_winner_branch_signal"
    assert 2 <= len(comp_card["payload"]["next_decision"]["options"]) <= 4
    assert comp_card["payload"]["next_decision"]["servant_default"]["option_id"]

    # 2. Non-branch strategy workshop: generic names, zero branch terms
    current_payload["data"] = non_branch_draft
    created_generic = client.post(
        "/bff/agora/workshops",
        headers={"Authorization": harness._OPERATOR_AUTH, "Idempotency-Key": "twelve-generic-create"},
        json={"initial_message": "跨市場大型科技股 60 日與 20 日動能輪動策略，每週再平衡。"},
    )
    assert created_generic.status_code == 201, created_generic.text
    generic_ws_id = created_generic.json()["data"]["workshop_id"]

    recon_generic = client.post(
        f"/bff/agora/workshops/{generic_ws_id}/reconstruct",
        headers={"Authorization": harness._OPERATOR_AUTH},
    )
    assert recon_generic.status_code == 200, recon_generic.text

    comp_generic = client.get(
        f"/bff/agora/workshops/{generic_ws_id}/completeness",
        headers={"Authorization": harness._OPERATOR_AUTH},
    ).json()["data"]
    assert comp_generic is not None
    assert comp_generic["is_branch_strategy"] is False
    assert comp_generic["strategy_family"] == "generic"
    generic_blocks = comp_generic["blocks"]
    assert len(generic_blocks) == 12
    assert [b["order"] for b in generic_blocks] == list(range(1, 13))
    assert [b["v10_block_id"] for b in generic_blocks] == _EXPECTED_V10_BLOCK_ORDER
    for b in generic_blocks:
        assert "分點" not in b["name"] and "關係人" not in b["name"]
        assert "branch" not in b["label_en"].lower()
        assert "branch" not in b["block_id"].lower()


def test_answering_next_decision_records_event_and_updates_next_reconstruction(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from services.control_plane.bff.agora.strategy_workshop.semantic_provider import OpenClawOpsClient
    from services.control_plane.bff.tests import test_agora_strategy_workshop as harness

    monkeypatch.setattr(OpenClawOpsClient, "_request", lambda self, *a, **kw: _envelope())
    client = harness._workshop_client(monkeypatch)
    created = client.post(
        "/bff/agora/workshops",
        headers={"Authorization": harness._OPERATOR_AUTH, "Idempotency-Key": "decision-flow-create"},
        json={"initial_message": "從關係人持股與贏家分點買賣超建立波段策略。"},
    )
    assert created.status_code == 201, created.text
    workshop_id = created.json()["data"]["workshop_id"]

    first_recon = client.post(
        f"/bff/agora/workshops/{workshop_id}/reconstruct",
        headers={"Authorization": harness._OPERATOR_AUTH},
    )
    assert first_recon.status_code == 200, first_recon.text
    first_recon_data = first_recon.json()["data"]

    comp_before = client.get(
        f"/bff/agora/workshops/{workshop_id}/completeness",
        headers={"Authorization": harness._OPERATOR_AUTH},
    ).json()["data"]
    assert comp_before is not None
    next_decision = comp_before["next_decision"]
    assert next_decision["decision_id"] == "q1"
    assert 2 <= len(next_decision["options"]) <= 4
    assert next_decision["servant_default"] is not None
    signal_block_before = next(b for b in comp_before["blocks"] if b["v10_block_id"] == "signal_formation")
    assert signal_block_before["state"] == "missing"

    # Missing If-Match -> 428; missing Idempotency-Key -> 400
    etag = harness._get_current_etag(client, workshop_id)
    chosen_opt = next_decision["options"][0]
    no_if_match = client.post(
        f"/bff/agora/workshops/{workshop_id}/decisions",
        headers={"Authorization": harness._OPERATOR_AUTH, "Idempotency-Key": "dec-missing-if"},
        json={"decision_id": next_decision["decision_id"], "option_id": chosen_opt["option_id"]},
    )
    assert no_if_match.status_code == 428

    no_idem = client.post(
        f"/bff/agora/workshops/{workshop_id}/decisions",
        headers={"Authorization": harness._OPERATOR_AUTH, "If-Match": etag},
        json={"decision_id": next_decision["decision_id"], "option_id": chosen_opt["option_id"]},
    )
    assert no_idem.status_code == 400

    # Unknown option_id -> 422 without burning idempotency key
    bad_opt = client.post(
        f"/bff/agora/workshops/{workshop_id}/decisions",
        headers={"Authorization": harness._OPERATOR_AUTH, "If-Match": etag, "Idempotency-Key": "dec-1"},
        json={"decision_id": next_decision["decision_id"], "option_id": "nonexistent_option_999"},
    )
    assert bad_opt.status_code == 422

    # Valid decision write -> 201 and records 1 workshop event
    dec_resp = client.post(
        f"/bff/agora/workshops/{workshop_id}/decisions",
        headers={"Authorization": harness._OPERATOR_AUTH, "If-Match": etag, "Idempotency-Key": "dec-1"},
        json={"decision_id": next_decision["decision_id"], "option_id": chosen_opt["option_id"]},
    )
    assert dec_resp.status_code == 201, dec_resp.text
    dec_data = dec_resp.json()["data"]
    assert dec_data["decision_id"] == "q1"
    assert dec_data["option_id"] == chosen_opt["option_id"]
    assert dec_data["option_label"] == chosen_opt["label"]
    assert dec_data["sequence_no"] == 2

    # Duplicate Idempotency-Key with fresh ETag -> 409 IDEMPOTENCY_CONFLICT
    fresh_etag = dec_resp.headers["etag"]
    dup = client.post(
        f"/bff/agora/workshops/{workshop_id}/decisions",
        headers={"Authorization": harness._OPERATOR_AUTH, "If-Match": fresh_etag, "Idempotency-Key": "dec-1"},
        json={"decision_id": next_decision["decision_id"], "option_id": chosen_opt["option_id"]},
    )
    assert dup.status_code == 409

    # Next reconstruction reads the recorded decision event and updates the strategy
    second_recon = client.post(
        f"/bff/agora/workshops/{workshop_id}/reconstruct",
        headers={"Authorization": harness._OPERATOR_AUTH},
    )
    assert second_recon.status_code == 200, second_recon.text
    second_recon_data = second_recon.json()["data"]
    assert second_recon_data["based_on_sequence_no"] == 2
    assert second_recon_data["reconstruction_id"] != first_recon_data["reconstruction_id"]
    assert second_recon_data["strategy_map"]["signal_definition"]["status"] == "confirmed"
    assert chosen_opt["label"] in second_recon_data["strategy_map"]["signal_definition"]["summary"]

    comp_after = client.get(
        f"/bff/agora/workshops/{workshop_id}/completeness",
        headers={"Authorization": harness._OPERATOR_AUTH},
    ).json()["data"]
    signal_block_after = next(b for b in comp_after["blocks"] if b["v10_block_id"] == "signal_formation")
    assert signal_block_after["state"] == "confirmed"
    assert chosen_opt["label"] in signal_block_after["note"]
    assert any(c.get("ref_id") == dec_data["event_id"] for c in signal_block_after["source_citations"])
    # Next high-value decision advanced past the answered question
    assert comp_after["next_decision"]["decision_id"] != "q1"

