"""Tests for shadow-arm settlement per decision (Acceptance 1, Design A4 & §12.3/§12.5)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient
import pytest

from services.agora.shadow_mode import build_decision_lock
from services.evaluation.attribution.settlement import (
    A4_DISPLAY_LABELS,
    CostModelConfig,
    settle_decision_lock,
)
from services.evaluation.attribution.store import AttributionStore
from services.evaluation.main import AttributionOwnerRuntime, create_evaluation_app

RECORDED_TWSE_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "research"
    / "tests"
    / "fixtures"
    / "pool_admission_tw_recorded.json"
)
SERVICE_TOKEN = "test-evaluation-service-token"


def load_recorded_twse_rows() -> List[Dict[str, Any]]:
    payload = json.loads(RECORDED_TWSE_FIXTURE.read_text(encoding="utf-8"))
    rows: List[Dict[str, Any]] = []
    for item in payload["responses"]:
        sym = f"{item['symbol']}.TWSE"
        for raw in item["response"]["data"]:
            y, m, d = map(int, raw[0].split("/"))
            num = lambda v: float(str(v).replace(",", ""))
            rows.append(
                {
                    "symbol": sym,
                    "date": f"{y + 1911:04d}-{m:02d}-{d:02d}",
                    "open": num(raw[3]),
                    "high": num(raw[4]),
                    "low": num(raw[5]),
                    "close": num(raw[6]),
                    "volume": num(raw[1]),
                    "source_ref": item["source_url"],
                }
            )
    return rows


def test_settle_four_arms_shares_cutoff_cost_model_and_window_with_a4_labels() -> None:
    market_rows = load_recorded_twse_rows()
    lock = {
        "decision_id": "dec-2330-001",
        "tenant_id": "tenant-alpha",
        "agora_user_id": "trader-lin",
        "account_id": "acct-8801",
        "broker_ids": ["tw-fubon-01"],
        "target_instrument": "2330.TWSE",
        "decision_time": "2024-10-01T06:00:00Z",
        "data_cutoff": "2024-10-01T05:30:00Z",
        "raw_text": "看好台積電法說前營收動能，進場建立部位",
        "arms": {
            "human_actual": {
                "action": "enter",
                "quantity": 100,
                "price": None,
                "execution_observation_kind": "pantheon_paper_proxy",
            },
            "private_servant": {
                "suggestion": "reduce",
                "persona_id": "servant-lin",
            },
            "base_strategy": {
                "suggestion": "enter",
                "strategy_ref": "strat-tw-semi-v2",
            },
            "committee_variant": {
                "suggestion": "no_trade",
                "meeting_id": "mtg-20241001",
            },
        },
        "unavailable_arms": [],
        "created_at": "2024-10-01T06:00:00Z",
    }
    paper_fills = [
        {
            "fill_id": "paper-fill-001",
            "decision_id": "dec-2330-001",
            "arm": "human_actual",
            "fill_price": 970.0,
            "settlement_source": "pantheon_paper_proxy",
            "slippage_bps": 4.0,
        }
    ]
    cost_model = CostModelConfig(slippage_bps=5.0)
    settlement = settle_decision_lock(
        lock,
        market_rows=market_rows,
        paper_fills=paper_fills,
        cost_model=cost_model,
        observation_window="5d",
        settled_at="2024-10-10T08:00:00Z",
    ).to_dict()

    assert settlement["decision_id"] == "dec-2330-001"
    assert settlement["data_cutoff"] == "2024-10-01T05:30:00Z"
    assert settlement["cost_model"] == cost_model.model_id
    assert settlement["observation_window"] == "5d"

    arms = {a["arm"]: a for a in settlement["arms"]}
    assert set(arms) == {"human_actual", "private_servant", "base_strategy", "committee_variant"}

    for arm_name, arm in arms.items():
        assert arm["unavailable_reason"] is None, arm_name
        assert arm["settled_at"] == "2024-10-10T08:00:00Z"
        out = arm["outcome"]
        assert out is not None
        assert out["data_cutoff"] == "2024-10-01T05:30:00Z"
        assert out["cost_model"] == cost_model.model_id
        assert out["observation_window"] == "5d"
        assert out["window_sessions"] == 5

    # Paper proxy is NEVER labelled as an actual fill (A4 §1).
    human = arms["human_actual"]
    assert human["settlement_source"] == "pantheon_paper_proxy"
    assert human["outcome"]["actual_fill_claimed"] is False
    assert human["outcome"]["execution_display_label"] == A4_DISPLAY_LABELS["pantheon_paper_proxy"]
    assert human["outcome"]["entry_price"] == 970.0
    assert human["outcome"]["fill_refs"] == ["paper-fill-001"]

    # Committee variant declared no_trade -> 0 net return, declared_no_trade source
    committee = arms["committee_variant"]
    assert committee["settlement_source"] == "declared_no_trade"
    assert committee["outcome"]["return_pct"] == 0.0
    assert committee["outcome"]["cost_pct"] == 0.0
    assert committee["outcome"]["execution_display_label"] == A4_DISPLAY_LABELS["declared_no_trade"]


def test_settle_a4_raw_decision_lock_and_supersede_with_verified_broker_fill() -> None:
    market_rows = load_recorded_twse_rows()
    raw_lock = build_decision_lock(
        human_decision_id="hdec-2317-01",
        decision_record_id="drec-2317-01",
        event={
            "strategy_id": "strat-honhai",
            "strategy_version_id": "v1",
            "suggested_action": "enter",
            "event_kind": "entry",
            "data_cutoff": "2024-10-07T05:30:00Z",
            "subject": {"symbol": "2317.TWSE"},
            "servant_suggestion": {"action": "reduce", "persona_id": "servant-chen"},
        },
        decision_event_id="evt-2317-01",
        tenant_id="tenant-alpha",
        user_id="trader-chen",
        body_decision="approve",
        rationale="鴻海 AI 伺服器出貨成長",
        modifications=None,
        decision_time="2024-10-07T06:00:00Z",
        account_id="acct-2317",
        broker_ids=["tw-yuanta"],
    )

    store = AttributionStore()
    first = settle_decision_lock(raw_lock, market_rows=market_rows, observation_window="5d")
    stored_first = store.put_shadow_settlement(first)
    arms_first = {a["arm"]: a for a in stored_first["arms"]}
    assert arms_first["human_actual"]["settlement_source"] == "pantheon_paper_proxy"
    assert arms_first["human_actual"]["outcome"]["actual_fill_claimed"] is False
    assert arms_first["committee_variant"]["outcome"] is None
    assert arms_first["committee_variant"]["unavailable_reason"] == "no_institutional_view_on_decision_event"
    first_outcome_id = arms_first["human_actual"]["outcome_id"]

    # Now a verified broker fill arrives: must create a superseding outcome without losing history (A4 §3).
    second = settle_decision_lock(
        raw_lock,
        market_rows=market_rows,
        paper_fills=[
            {
                "fill_id": "broker-fill-99",
                "decision_id": "hdec-2317-01",
                "arm": "human_actual",
                "fill_price": 195.5,
                "settlement_source": "verified_broker_fill",
                "verified": True,
            }
        ],
        observation_window="5d",
        prior_settlement=stored_first,
    )
    stored_second = store.put_shadow_settlement(second)
    arms_second = {a["arm"]: a for a in stored_second["arms"]}
    assert arms_second["human_actual"]["settlement_source"] == "verified_broker_fill"
    assert arms_second["human_actual"]["outcome"]["actual_fill_claimed"] is True
    assert arms_second["human_actual"]["outcome"]["execution_display_label"] == A4_DISPLAY_LABELS["verified_broker_fill"]
    assert arms_second["human_actual"]["supersedes_outcome_id"] == first_outcome_id
    assert len(stored_second.get("outcome_history") or []) == 1


@pytest.mark.parametrize(
    "case_name,lock_override,rows_mod,expected_reason",
    [
        (
            "missing_cutoff",
            {"data_cutoff": None, "available_data_cutoff": None},
            lambda rows: rows,
            "missing_data_cutoff",
        ),
        (
            "missing_instrument",
            {"target_instrument": None},
            lambda rows: rows,
            "missing_target_instrument",
        ),
        (
            "missing_market_data",
            {"target_instrument": "9999.TWSE"},
            lambda rows: rows,
            "missing_market_data",
        ),
        (
            "missing_entry_open_price",
            {},
            lambda rows: [
                {**r, "open": None} if r["symbol"] == "2330.TWSE" and r["date"] > "2024-10-01" else r
                for r in rows
            ],
            "missing_entry_open_price",
        ),
        (
            "missing_observation_window_price",
            {"data_cutoff": "2024-12-30T05:30:00Z"},
            lambda rows: rows,
            "missing_observation_window_price",
        ),
        (
            "missing_close_in_window",
            {},
            lambda rows: [
                {**r, "close": None} if r["symbol"] == "2330.TWSE" and r["date"] == "2024-10-07" else r
                for r in rows
            ],
            "missing_price_in_observation_window",
        ),
    ],
)
def test_missing_price_or_cutoff_leaves_arm_unsettled_with_reason(
    case_name: str,
    lock_override: Dict[str, Any],
    rows_mod: Any,
    expected_reason: str,
) -> None:
    base_rows = load_recorded_twse_rows()
    lock: Dict[str, Any] = {
        "decision_id": f"dec-{case_name}",
        "tenant_id": "tenant-alpha",
        "agora_user_id": "trader-lin",
        "target_instrument": "2330.TWSE",
        "decision_time": "2024-10-01T06:00:00Z",
        "data_cutoff": "2024-10-01T05:30:00Z",
        "arms": {
            "human_actual": {"action": "enter", "execution_observation_kind": "pantheon_paper_proxy"},
            "private_servant": {"suggestion": "enter", "persona_id": "servant-1"},
            "base_strategy": {"suggestion": "enter", "strategy_ref": "strat-1"},
            "committee_variant": None,
        },
        **lock_override,
    }
    settled = settle_decision_lock(lock, market_rows=rows_mod(base_rows), observation_window="5d").to_dict()
    arms = {a["arm"]: a for a in settled["arms"]}
    assert arms["human_actual"]["outcome"] is None
    assert arms["human_actual"]["settlement_source"] is None
    assert arms["human_actual"]["settled_at"] is None
    assert arms["human_actual"]["unavailable_reason"] == expected_reason
    assert arms["committee_variant"]["outcome"] is None
    assert arms["committee_variant"]["unavailable_reason"] == "no_committee_variant_recorded"


def test_shadow_route_returns_settled_arms_and_404_when_unknown() -> None:
    market_rows = load_recorded_twse_rows()
    lock = {
        "decision_id": "dec-route-01",
        "tenant_id": "tenant-alpha",
        "agora_user_id": "trader-lin",
        "target_instrument": "2330.TWSE",
        "decision_time": "2024-10-01T06:00:00Z",
        "data_cutoff": "2024-10-01T05:30:00Z",
        "arms": {
            "human_actual": {"action": "enter", "execution_observation_kind": "pantheon_paper_proxy"},
            "private_servant": {"suggestion": "exit", "persona_id": "servant-lin"},
            "base_strategy": {"suggestion": "enter", "strategy_ref": "strat-1"},
            "committee_variant": {"suggestion": "enter", "meeting_id": "mtg-1"},
        },
    }
    rt = AttributionOwnerRuntime(
        service_tokens=[SERVICE_TOKEN],
        market_rows=market_rows,
        decision_locks=[lock],
    )
    client = TestClient(create_evaluation_app(rt))
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}", "X-Tenant-Id": "tenant-alpha"}

    resp = client.get("/api/attribution/shadow/dec-route-01", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision_id"] == "dec-route-01"
    assert len(body["arms"]) == 4
    arms = {a["arm"]: a for a in body["arms"]}
    assert arms["human_actual"]["settlement_source"] == "pantheon_paper_proxy"
    assert arms["human_actual"]["outcome"]["return_pct"] is not None

    missing = client.get("/api/attribution/shadow/unknown-decision-999", headers=headers)
    assert missing.status_code == 404
    assert client.get("/api/attribution/shadow/dec-route-01").status_code == 401
