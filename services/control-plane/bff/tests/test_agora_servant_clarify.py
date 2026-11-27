"""AGORA-SERVANT-CLARIFY-20261127: Agora servant asks a typed clarification outside the workshop.

SD_AGORA_SERVANT_CORE_2026-11-27 §5 and §12 T3. Verifies:
1. A servant turn may return a typed clarification card with a question, 2 to 4 options,
   a provisional default, and why it matters, matching the workshop decision shape of
   AGORA-WORKSHOP-TWELVE-BLOCK-COMPLETENESS-20261127 (WorkshopNextDecisionV114).
2. The servant asks only when the answer changes the data selected or the result, and
   otherwise states its assumption and answers (both model-direct and BFF-suppressed paths).
3. At most one clarification per turn and at most two in a row before answering with
   stated assumptions.
4. The trader's choice is a durable session event (servant.clarification.answered); style
   choices (horizon, universe, risk_tolerance) emit habit facts for
   AGORA-TRADER-HABIT-PROFILE-20261127 while non-style data choices do not.
5. End-to-end proof: one ambiguous request yields a clarification and choosing different
   options changes the owner data selected, the cited facts, and the final answer.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from services.control_plane.bff.agora.servant.clarification import (
    MAX_CLARIFICATIONS_PER_TURN,
    MAX_CONSECUTIVE_CLARIFICATIONS,
    STYLE_DIMENSIONS,
    collect_clarification_habit_facts,
    normalize_clarification_card,
)
from services.control_plane.bff.agora.servant.tool_turn import SERVANT_TURN_SCHEMA
from services.control_plane.bff.agora.strategy_workshop.readiness import (
    build_next_high_value_decision,
    build_twelve_strategy_blocks,
)
from services.control_plane.bff.tests import test_agora_servant_data_tools as dt
from services.control_plane.bff.tests import test_agora_servant_sessions as sessions

AUTH = sessions.AUTH
REPO_ROOT = Path(__file__).resolve().parents[4]

# Reusable fixture shared with test_agora_servant_data_tools
broker_store = dt.broker_store


def _clarification_payload(
    *,
    decision_id: str = "clarify_2330_horizon_side",
    question: str = "請問您分析 2330 分點籌碼的交易週期（horizon）與觀察方向為何？",
    why_it_matters: str = "不同持有週期與買賣方向會選取不同區間與方向的券商分點資料，直接改變進場結論。",
    style_dimension: str = "horizon",
    changes_data_or_result: bool = True,
) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "question": question,
        "why_it_matters": why_it_matters,
        "changes_data_or_result": changes_data_or_result,
        "style_dimension": style_dimension,
        "resolves": ["entry_holding", "holding_period"],
        "target_blocks": ["entry_holding"],
        "options": [
            {
                "option_id": "opt_a",
                "label": "A. 短線當日買方動能（最新交易日買超前 3 大分點）",
                "description": "查詢最新單日 side=buy 分點買超集中度",
                "is_default": False,
            },
            {
                "option_id": "opt_b",
                "label": "B. 短線當日賣方出貨壓力（最新交易日賣超前 3 大分點）",
                "description": "查詢最新單日 side=sell 分點賣超集中度",
                "is_default": False,
            },
            {
                "option_id": "opt_c",
                "label": "C. 波段近 2 日買賣雙向對照（2026-11-25 至 2026-11-26，預設）",
                "description": "查詢近 2 日雙向分點延續性",
                "is_default": True,
            },
        ],
        "default_option_id": "opt_c",
        "provisional_default": "預設以近 2 個交易日買賣雙向分點比對主力延續性。",
    }


def _send_choice(client, session_id: str, key: str, *, choice: dict[str, Any] | None = None, content: str = "", context: dict[str, Any] | None = None):
    body: dict[str, Any] = {}
    if content:
        body["content"] = content
    if choice is not None:
        body["clarification_choice"] = choice
    if context is not None:
        body["context"] = context
    return client.post(
        f"/bff/agora/servant/sessions/{session_id}/messages",
        json=body,
        headers={**AUTH, "Idempotency-Key": key, "X-Request-Id": f"req-{key}", "X-Trace-Id": f"trace-{key}"},
    )


# --------------------------------------------------------------------------- AC1: shape matches workshop decision


def test_clarification_card_shape_matches_workshop_decision_v114(monkeypatch, tmp_path, broker_store):
    openapi = json.loads((REPO_ROOT / "services/control-plane/openapi/agora_v1_14.openapi.yaml").read_text(encoding="utf-8"))
    workshop_decision_schema = {
        **openapi["components"]["schemas"]["WorkshopNextDecisionV114"],
        "components": openapi["components"],
    }

    model = dt.ScriptedModel(
        lambda packet, n: {
            "action": "clarify",
            "clarification": _clarification_payload(),
        }
    )
    client, session_id = dt._session(monkeypatch, tmp_path, model)
    accepted = dt._send(client, session_id, "請幫我看 2330 的分點動向與進場建議", "clar-shape-001")
    assert accepted.status_code == 202
    dt._run(client, model, dt._tools(broker_store, []))

    event = dt._answer_event(client, session_id)
    assert event["provider_status"] == "completed"
    assert event["answer_mode"] == "clarification"
    card = event["clarification"]
    assert isinstance(card, dict)
    assert len(event["cards"]) == 1 and event["cards"][0]["card_type"] == "clarification"

    # Validate against the OpenAPI WorkshopNextDecisionV114 schema
    jsonschema.Draft7Validator(workshop_decision_schema).validate(card)

    # Compare key contract directly with the workshop build_next_high_value_decision output
    snap = {"strategy_version_id": "sv-1", "completeness_json": {}}
    blocks = build_twelve_strategy_blocks(snapshot=snap, is_branch_strategy=True)
    workshop_decision = build_next_high_value_decision(
        snapshot=snap,
        blocks=blocks,
        is_branch_strategy=True,
    )
    assert set(workshop_decision.keys()) <= set(card.keys())
    assert 2 <= len(card["options"]) <= 4
    assert card["decision_id"] == card["question_id"] == "clarify_2330_horizon_side"
    assert card["question"] == card["text"]
    assert card["why_it_matters"] == card["why_now"]
    assert card["default_option_id"] == "opt_c"
    assert card["servant_default"] == {
        "option_id": "opt_c",
        "label": "C. 波段近 2 日買賣雙向對照（2026-11-25 至 2026-11-26，預設）",
        "summary": "預設以近 2 個交易日買賣雙向分點比對主力延續性。",
        "provisional_assumption": "預設以近 2 個交易日買賣雙向分點比對主力延續性。",
    }
    assert sum(1 for o in card["options"] if o["is_default"]) == 1

    # Also exposed on GET /bff/agora/servant/sessions/{session_id}
    detail = client.get(f"/bff/agora/servant/sessions/{session_id}", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["data"]["pending_clarification"]["decision_id"] == "clarify_2330_horizon_side"


def test_clarification_option_bounds_2_to_4_and_adapter_schema_contract():
    import sys

    adapter_dir = REPO_ROOT / "services" / "openclaw-gateway-adapter"
    if str(adapter_dir) not in sys.path:
        sys.path.insert(0, str(adapter_dir))
    from assistant_openclaw_provider import (
        OpenClawProviderError,
        _validate_extraction_arguments,
        unsupported_schema_keyword_paths,
    )

    assert unsupported_schema_keyword_paths(SERVANT_TURN_SCHEMA) == []

    valid_clarify = {
        "action": "clarify",
        "tool_calls": [],
        "answer_text": "",
        "cited_fact_ids": [],
        "missing_data": [],
        "clarification": _clarification_payload(),
    }
    _validate_extraction_arguments(valid_clarify, SERVANT_TURN_SCHEMA)
    assert jsonschema.Draft7Validator(SERVANT_TURN_SCHEMA).is_valid(valid_clarify)

    # Fewer than 2 options is rejected by both adapter validator and normalizer
    one_option = {
        **valid_clarify,
        "clarification": {**_clarification_payload(), "options": [_clarification_payload()["options"][0]]},
    }
    with pytest.raises(OpenClawProviderError):
        _validate_extraction_arguments(one_option, SERVANT_TURN_SCHEMA)
    assert normalize_clarification_card(one_option["clarification"]) is None

    # More than 4 options is rejected by adapter validator and bounded to 4 by normalizer
    five_options = {
        **valid_clarify,
        "clarification": {
            **_clarification_payload(),
            "options": [
                {"option_id": f"opt_{i}", "label": f"選項 {i}"} for i in range(5)
            ],
        },
    }
    with pytest.raises(OpenClawProviderError):
        _validate_extraction_arguments(five_options, SERVANT_TURN_SCHEMA)
    normalized_five = normalize_clarification_card(five_options["clarification"])
    assert normalized_five is not None and len(normalized_five["options"]) == 4


# --------------------------------------------------------------------------- AC2: asks only when choice changes data or result


def test_servant_asks_clarification_when_answer_changes_data_or_result(monkeypatch, tmp_path, broker_store):
    owner_calls: list[str] = []
    model = dt.ScriptedModel(
        lambda packet, n: {
            "action": "clarify",
            "clarification": _clarification_payload(changes_data_or_result=True),
        }
    )
    client, session_id = dt._session(monkeypatch, tmp_path, model)
    dt._send(client, session_id, "2330 現在分點主力在買還是在賣？", "ac2-clarify-001")
    dt._run(client, model, dt._tools(broker_store, owner_calls))

    event = dt._answer_event(client, session_id)
    assert event["answer_mode"] == "clarification"
    assert event["clarification"] is not None
    assert event["stated_assumptions"] == []
    assert owner_calls == []  # did not guess an arbitrary query before clarifying


def test_servant_states_assumption_and_answers_when_choice_does_not_change_data_or_result(
    monkeypatch, tmp_path, broker_store
):
    owner_calls: list[str] = []

    # Path A: Model attempts action="clarify" with changes_data_or_result=False (e.g. display format).
    # BFF suppresses the clarification card, records the provisional default as a stated assumption,
    # re-prompts with clarification_allowed=False, runs the tool, and answers directly.
    def script_suppressed(packet, n):
        if n == 1:
            assert packet["clarification_allowed"] is True
            return {
                "action": "clarify",
                "clarification": {
                    "question": "請問您希望分點資料用表格還是條列呈現？",
                    "why_it_matters": "僅影響排版格式，不改變選取的分點資料或結果。",
                    "changes_data_or_result": False,
                    "style_dimension": "other",
                    "options": [
                        {"option_id": "opt_a", "label": "A. 表格"},
                        {"option_id": "opt_b", "label": "B. 條列（預設）", "is_default": True},
                    ],
                    "default_option_id": "opt_b",
                    "provisional_default": "以條列格式呈現最新交易日買賣超前 3 大分點",
                },
            }
        if n == 2:
            assert packet["clarification_allowed"] is False
            assert "以條列格式呈現最新交易日買賣超前 3 大分點" in packet["forced_assumptions"]
            return {
                "action": "call_tools",
                "tool_calls": [{"tool": "broker_branch_flow", "arguments": {"symbols": ["2330"]}}],
            }
        first = packet["facts"][0]["id"]
        return {
            "action": "answer",
            "answer_text": f"2330 最新交易日買超第一名淨買 {{{{{first}}}}}。",
            "cited_fact_ids": [first],
        }

    model_a = dt.ScriptedModel(script_suppressed)
    client_a, session_a = dt._session(monkeypatch, tmp_path, model_a)
    dt._send(client_a, session_a, "給我看 2330 最新分點", "ac2-suppress-001")
    dt._run(client_a, model_a, dt._tools(broker_store, owner_calls))

    event_a = dt._answer_event(client_a, session_a)
    assert event_a["answer_mode"] == "model"
    assert event_a["clarification"] is None
    assert event_a["cards"] == []
    assert event_a["stated_assumptions"] == ["以條列格式呈現最新交易日買賣超前 3 大分點"]
    assert "（推定假設：以條列格式呈現最新交易日買賣超前 3 大分點）" in event_a["answer"]
    assert "60,000股" in event_a["answer"]

    # Path B: Model directly answers with stated_assumption when ambiguity does not change result.
    def script_direct(packet, n):
        if n == 1:
            return {
                "action": "call_tools",
                "tool_calls": [{"tool": "broker_branch_flow", "arguments": {"symbols": ["2330"]}}],
            }
        first = packet["facts"][0]["id"]
        return {
            "action": "answer",
            "stated_assumption": "假設以最新收盤日（2026-11-26）股數為單位統計",
            "answer_text": f"2330 買超第一名淨買 {{{{{first}}}}}。",
            "cited_fact_ids": [first],
        }

    model_b = dt.ScriptedModel(script_direct)
    client_b, session_b = dt._session(monkeypatch, tmp_path, model_b)
    dt._send(client_b, session_b, "2330 最新一日買超第一名是多少", "ac2-direct-001")
    dt._run(client_b, model_b, dt._tools(broker_store, []))

    event_b = dt._answer_event(client_b, session_b)
    assert event_b["answer_mode"] == "model"
    assert event_b["clarification"] is None
    assert event_b["stated_assumptions"] == ["假設以最新收盤日（2026-11-26）股數為單位統計"]
    assert "（推定假設：假設以最新收盤日（2026-11-26）股數為單位統計）" in event_b["answer"]
    assert "60,000股" in event_b["answer"]


# --------------------------------------------------------------------------- AC3: at most 1 per turn, at most 2 in a row


def test_at_most_one_clarification_per_turn(monkeypatch, tmp_path, broker_store):
    assert MAX_CLARIFICATIONS_PER_TURN == 1
    # Even if a sequence of two clarification dicts is passed to normalize_clarification_card,
    # only the first card is normalized.
    c1 = _clarification_payload(decision_id="c1", question="問題一？")
    c2 = _clarification_payload(decision_id="c2", question="問題二？")
    normalized = normalize_clarification_card([c1, c2])
    assert normalized is not None and normalized["decision_id"] == "c1"

    calls = 0

    def script(packet, n):
        nonlocal calls
        calls += 1
        return {"action": "clarify", "clarification": c1}

    model = dt.ScriptedModel(script)
    client, session_id = dt._session(monkeypatch, tmp_path, model)
    dt._send(client, session_id, "幫我分析", "one-per-turn-001")
    dt._run(client, model, dt._tools(broker_store, []))

    event = dt._answer_event(client, session_id)
    assert calls == 1  # turn finishes immediately after one clarification
    assert isinstance(event["clarification"], dict)
    assert len(event["cards"]) == 1


def test_at_most_two_clarifications_in_a_row_before_answering_with_stated_assumptions(
    monkeypatch, tmp_path, broker_store
):
    assert MAX_CONSECUTIVE_CLARIFICATIONS == 2

    def script(packet, _call_no):
        msg = packet["trader_message"]
        if msg == "我想看 2330 進場機會":
            assert packet["clarification_allowed"] is True
            assert packet["consecutive_clarifications"] == 0
            return {
                "action": "clarify",
                "clarification": _clarification_payload(
                    decision_id="clarify_1_horizon",
                    question="第一問：請問持有週期是短線或波段？",
                    style_dimension="horizon",
                ),
            }
        if msg == "那還要考慮什麼？":
            assert packet["clarification_allowed"] is True
            assert packet["consecutive_clarifications"] == 1
            return {
                "action": "clarify",
                "clarification": _clarification_payload(
                    decision_id="clarify_2_risk",
                    question="第二問：請問單筆風險承受度（停損幅度）為何？",
                    why_it_matters="不同風險承受度會改變建議部位上限與停損條件。",
                    style_dimension="risk_tolerance",
                ),
            }
        # Turn 3: 2 clarifications have already occurred in a row.
        assert packet["clarification_allowed"] is False
        assert packet["consecutive_clarifications"] == 2
        if not packet["facts"] and not packet["forced_assumptions"]:
            pass
        if not packet["facts"] and _call_no == 3:
            # Model stubbornly tries a 3rd clarification in a row anyway:
            return {
                "action": "clarify",
                "clarification": _clarification_payload(
                    decision_id="clarify_3_universe",
                    question="第三問：請問選股範圍？",
                    style_dimension="universe",
                ),
            }
        if not packet["facts"]:
            assert packet["forced_assumptions"]
            return {
                "action": "call_tools",
                "tool_calls": [{"tool": "broker_branch_flow", "arguments": {"symbols": ["2330"]}}],
            }
        first = packet["facts"][0]["id"]
        return {
            "action": "answer",
            "answer_text": f"2330 最新交易日買超第一名淨買 {{{{{first}}}}}。",
            "cited_fact_ids": [first],
        }

    model = dt.ScriptedModel(script)
    client, session_id = dt._session(monkeypatch, tmp_path, model)

    # Turn 1 -> Clarification 1
    dt._send(client, session_id, "我想看 2330 進場機會", "streak-t1")
    dt._run(client, model, dt._tools(broker_store, []))
    ev1 = dt._answer_event(client, session_id)
    assert ev1["answer_mode"] == "clarification"
    assert ev1["clarification"]["decision_id"] == "clarify_1_horizon"

    # Turn 2 -> Clarification 2
    dt._send(client, session_id, "那還要考慮什麼？", "streak-t2")
    dt._run(client, model, dt._tools(broker_store, []))
    ev2 = dt._answer_event(client, session_id)
    assert ev2["answer_mode"] == "clarification"
    assert ev2["clarification"]["decision_id"] == "clarify_2_risk"

    # Turn 3 -> Must answer with stated assumptions (3rd consecutive clarification blocked!)
    dt._send(client, session_id, "還是不太確定，直接幫我分析 2330", "streak-t3")
    dt._run(client, model, dt._tools(broker_store, []))
    ev3 = dt._answer_event(client, session_id)
    assert ev3["answer_mode"] == "model"
    assert ev3["clarification"] is None
    assert ev3["cards"] == []
    assert ev3["stated_assumptions"]
    assert "（推定假設：" in ev3["answer"]
    assert "60,000股" in ev3["answer"]
    stored_t3 = dt._stored_answer(client, session_id)
    assert stored_t3["metadata"]["tool_turn"]["consecutive_clarifications"] == 0


# --------------------------------------------------------------------------- AC4: session event & style habit facts


def test_trader_choice_is_session_event_and_style_choices_emit_habit_facts(
    monkeypatch, tmp_path, broker_store
):
    assert STYLE_DIMENSIONS == ("horizon", "universe", "risk_tolerance")
    specs = [
        (
            "horizon",
            "clarify_style_horizon",
            "請問您的持有週期（horizon）偏好？",
            [
                {"option_id": "opt_short", "label": "A. 短線（1–5 日分點動能）"},
                {"option_id": "opt_swing", "label": "B. 波段（10–20 日主力建倉，預設）", "is_default": True},
            ],
            "opt_short",
            "holding_period",
            "entry_holding",
        ),
        (
            "universe",
            "clarify_style_universe",
            "請問您的選股市場範圍（universe）？",
            [
                {"option_id": "opt_tw50", "label": "A. 台灣 50 權值成分股"},
                {"option_id": "opt_mid", "label": "B. 上市櫃高流動性中小型股（預設）", "is_default": True},
            ],
            "opt_tw50",
            "rejected_candidates",
            "market_scope",
        ),
        (
            "risk_tolerance",
            "clarify_style_risk",
            "請問您的單筆部位與風險承受度（risk tolerance）？",
            [
                {"option_id": "opt_cons", "label": "A. 保守（單筆上限 5%、跌破 3% 停損）"},
                {"option_id": "opt_bal", "label": "B. 穩健（單筆上限 10%、跌破 5% 停損，預設）", "is_default": True},
            ],
            "opt_cons",
            "position_size",
            "sizing_leverage",
        ),
        (
            "data_scope",
            "clarify_non_style_data",
            "請問本次查詢要看哪一檔代號的分點？",
            [
                {"option_id": "opt_2330", "label": "A. 2330 台積電"},
                {"option_id": "opt_2317", "label": "B. 2317 鴻海（預設）", "is_default": True},
            ],
            "opt_2330",
            None,
            None,
        ),
    ]

    step = 0

    def script(packet, round_in_turn):
        nonlocal step
        if packet.get("active_clarification_choice"):
            choice = packet["active_clarification_choice"]
            return {
                "action": "answer",
                "answer_text": f"已收到您的選擇：{choice['option_label']}。",
            }
        spec = specs[step]
        step += 1
        return {
            "action": "clarify",
            "clarification": {
                "decision_id": spec[1],
                "question": spec[2],
                "why_it_matters": "不同選擇會改變選取的資料範圍與策略結論。",
                "changes_data_or_result": True,
                "style_dimension": spec[0],
                "options": spec[3],
                "default_option_id": spec[3][-1]["option_id"],
                "provisional_default": f"預設採用 {spec[3][-1]['label']}",
            },
        }

    model = dt.ScriptedModel(script)
    client, session_id = dt._session(monkeypatch, tmp_path, model)

    for idx, (style_dim, decision_id, _q, _opts, chosen_opt, expected_feature, expected_block) in enumerate(specs):
        # 1. Ask -> returns clarification card
        dt._send(client, session_id, f"問題 {idx}", f"ask-{idx}")
        dt._run(client, model, dt._tools(broker_store, []))
        clar_ev = dt._answer_event(client, session_id)
        assert clar_ev["answer_mode"] == "clarification"
        assert clar_ev["clarification"]["decision_id"] == decision_id

        # 2. Invalid option_id on explicit clarification_choice is rejected with 422
        bad = _send_choice(
            client,
            session_id,
            f"bad-choice-{idx}",
            choice={"decision_id": decision_id, "option_id": "opt_nonexistent"},
        )
        assert bad.status_code == 422

        # 3. Trader submits valid choice
        resp = _send_choice(
            client,
            session_id,
            f"choice-{idx}",
            choice={"decision_id": decision_id, "option_id": chosen_opt},
        )
        assert resp.status_code == 202, resp.text
        resp_data = resp.json()["data"]
        assert resp_data["clarification_choice"]["decision_id"] == decision_id
        assert resp_data["clarification_choice"]["option_id"] == chosen_opt
        assert resp_data["clarification_choice"]["event_type"] == "servant.clarification.answered"

        if expected_feature is not None:
            hfact = resp_data["habit_fact"]
            assert hfact["kind"] == "trader_style_choice"
            assert hfact["style_dimension"] == style_dim
            assert hfact["feature_key"] == expected_feature
            assert hfact["target_block"] == expected_block
            assert hfact["option_id"] == chosen_opt
            assert hfact["owner"] == "agora"
            assert hfact["freshness"] == "fresh"
        else:
            assert "habit_fact" not in resp_data

        dt._run(client, model, dt._tools(broker_store, []))
        ans_ev = dt._answer_event(client, session_id)
        assert ans_ev["answer_mode"] == "model"

    # Verify SSE stream contains all 4 servant.clarification.answered session events
    stream_events = sessions._stream_events(client, session_id)
    answered_events = [e for e in stream_events if e["type"] == "servant.clarification.answered"]
    assert len(answered_events) == 4
    assert [e["data"]["decision_id"] for e in answered_events] == [s[1] for s in specs]
    assert [e["data"]["style_dimension"] for e in answered_events] == [
        "horizon",
        "universe",
        "risk_tolerance",
        None,
    ]
    assert all(e["data"]["habit_fact"] is not None for e in answered_events[:3])
    assert answered_events[3]["data"]["habit_fact"] is None

    # Verify collect_clarification_habit_facts returns the 3 style habit facts for T5
    emitted_facts = collect_clarification_habit_facts(
        client.servant_owner, tenant_id="pantheon-dev", user_id="agora-test-user"
    )
    assert len(emitted_facts) == 3
    assert [f["style_dimension"] for f in emitted_facts] == ["horizon", "universe", "risk_tolerance"]
    assert [f["feature_key"] for f in emitted_facts] == [
        "holding_period",
        "rejected_candidates",
        "position_size",
    ]
    # And the last turn packet carried all 3 style habit facts
    assert len(model.packets[-1]["style_habit_facts"]) == 3


# --------------------------------------------------------------------------- AC5: ambiguous request -> clarification -> chosen option changes answer


def test_ambiguous_request_yields_clarification_and_chosen_option_changes_answer(
    monkeypatch, tmp_path, broker_store
):
    """End-to-end walkthrough proof:
    Same ambiguous request ('請分析 2330 的分點主力動向') yields a typed clarification card.
    Choosing opt_a (短線買方動能) queries side=buy and returns BuyBroker-01 (60,000股);
    choosing opt_b (短線賣方出貨壓力) queries side=sell and returns SellBroker-01 (-65,000股).
    """

    def make_script():
        def script(packet, _call_no):
            choice = packet.get("active_clarification_choice")
            if choice is None:
                return {
                    "action": "clarify",
                    "clarification": _clarification_payload(),
                }
            if packet["tool_rounds_used"] == 0:
                if choice["option_id"] == "opt_a":
                    side = "buy"
                    date_from = "2026-11-26"
                elif choice["option_id"] == "opt_b":
                    side = "sell"
                    date_from = "2026-11-26"
                else:
                    side = "both"
                    date_from = "2026-11-25"
                return {
                    "action": "call_tools",
                    "tool_calls": [
                        {
                            "tool": "broker_branch_flow",
                            "arguments": {"symbols": ["2330"], "side": side, "date_from": date_from},
                        }
                    ],
                }
            first = packet["facts"][0]
            direction = "買超主力" if choice["option_id"] == "opt_a" else "賣超主力"
            return {
                "action": "answer",
                "answer_text": f"依您選擇的「{choice['option_label']}」，2330 {direction}第一名為 {{{{{first['id']}}}}}。",
                "cited_fact_ids": [first["id"]],
            }

        return script

    # --- Branch A: trader chooses opt_a (buy side) ---
    calls_a: list[str] = []
    model_a = dt.ScriptedModel(make_script())
    client_a, session_a = dt._session(monkeypatch, tmp_path, model_a)
    dt._send(client_a, session_a, "請分析 2330 的分點主力動向", "walk-ask-a")
    dt._run(client_a, model_a, dt._tools(broker_store, calls_a))
    clar_a = dt._answer_event(client_a, session_a)
    assert clar_a["answer_mode"] == "clarification"
    assert clar_a["clarification"]["decision_id"] == "clarify_2330_horizon_side"

    # Implicit option selection via content="opt_a" also works as a clarification choice
    dt._send(client_a, session_a, "opt_a", "walk-choose-a")
    dt._run(client_a, model_a, dt._tools(broker_store, calls_a))
    ans_a = dt._answer_event(client_a, session_a)

    # --- Branch B: trader chooses opt_b (sell side) ---
    calls_b: list[str] = []
    model_b = dt.ScriptedModel(make_script())
    client_b, session_b = dt._session(monkeypatch, tmp_path, model_b)
    dt._send(client_b, session_b, "請分析 2330 的分點主力動向", "walk-ask-b")
    dt._run(client_b, model_b, dt._tools(broker_store, calls_b))
    clar_b = dt._answer_event(client_b, session_b)
    assert clar_b["answer_mode"] == "clarification"

    _send_choice(
        client_b,
        session_b,
        "walk-choose-b",
        choice={"decision_id": "clarify_2330_horizon_side", "option_id": "opt_b"},
    )
    dt._run(client_b, model_b, dt._tools(broker_store, calls_b))
    ans_b = dt._answer_event(client_b, session_b)

    # The chosen option changed the owner query, the cited facts, and the rendered answer:
    assert len(calls_a) == 1 and "side=buy" in calls_a[0]
    assert len(calls_b) == 1 and "side=sell" in calls_b[0]
    assert ans_a["answer"] != ans_b["answer"]
    assert "60,000股" in ans_a["answer"] and "買超主力" in ans_a["answer"]
    assert "-60,000股" in ans_b["answer"] and "賣超主力" in ans_b["answer"]
    assert all("買超" in c["label"] for c in ans_a["citations"])
    assert all("賣超" in c["label"] for c in ans_b["citations"])
    assert model_a.persona_calls == [] and model_b.persona_calls == []


def test_v1_14_bundle_records_servant_clarify_contract():
    bundle = json.loads((REPO_ROOT / "services/control-plane/specs/agora/bundle_index.v1_14.json").read_text(encoding="utf-8"))
    openapi_path = REPO_ROOT / "services/control-plane/openapi/agora_v1_14.openapi.yaml"
    cap_path = REPO_ROOT / "services/control-plane/specs/agora/v15/capability_manifest_v1_14.json"
    assert bundle["openapi"]["sha256"] == hashlib.sha256(openapi_path.read_bytes()).hexdigest()
    assert bundle["files"]["specs/agora/v15/capability_manifest_v1_14.json"] == hashlib.sha256(cap_path.read_bytes()).hexdigest()
    assert bundle["route_families"]["agora.servant.clarify.v1"] == [
        "POST /bff/agora/servant/sessions/{session_id}/messages",
        "GET /bff/agora/servant/sessions/{session_id}/stream",
    ]
    openapi = json.loads(openapi_path.read_text(encoding="utf-8"))
    schemas = openapi["components"]["schemas"]
    completed_props = schemas["ServantMessageCompletedEventData"]["properties"]
    assert "clarification" in completed_props and "cards" in completed_props and "stated_assumptions" in completed_props
    assert "clarification" in completed_props["answer_mode"]["enum"]
    assert "ServantStyleHabitFactV114" in schemas
    assert "ServantClarificationAnsweredEventData" in schemas
