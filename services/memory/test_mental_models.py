"""Tests for market-regime standing questions and H13 boundaries in services/memory/mental_models.py."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from integrations.openclaw.persona_agent_sync import (
    PERSONA_AGENT_TOOL_PROFILE,
    PERSONA_MENTAL_MODEL_READ_TOOL,
)
from services.memory import hindsight_index as hs
from services.memory import main, mental_models
from services.memory.test_main import (
    JWT_SECRET,
    FakeMentalModelHindsight,
    _declare,
    _make_jwt,
    _persona_auth_payload,
    _questions_env,
    _read,
    memory_client,
)


def load_persona_tool_broker():
    path = Path(__file__).resolve().parents[1] / "openclaw-gateway-adapter" / "persona_tool_broker.py"
    spec = importlib.util.spec_from_file_location("pantheon_test_persona_tool_broker", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _observed_regime(regime_class: str = "high_volatility", *, strategy_id: str = "momentum",
                     regime_id: str = "reg-20261116-001") -> dict:
    return {
        "status": "observed",
        "reason": None,
        "session": "2026-11-16",
        "regime_state_ref": f"regime-state://{regime_id}",
        "regime_tag": regime_class,
        "records": {
            strategy_id: {
                "regime_id": regime_id,
                "regime_class": regime_class,
                "confidence": 0.84,
            }
        },
        "missing_strategies": [],
        "macro": None,
    }


@pytest.mark.parametrize("families,regime", [
    (["momentum"], None),
    (["momentum"], {}),
    (["momentum"], {"status": "unavailable", "reason": "no_active_record_for_pool_strategies",
                    "session": "2026-11-16", "regime_state_ref": None, "regime_tag": "", "records": {}}),
    (["momentum"], {"status": "unavailable", "reason": "evaluate_refused:409:session_data_incomplete",
                    "session": "2026-11-16", "regime_state_ref": "regime-state://reg-1",
                    "regime_tag": "high_volatility",
                    "records": {"momentum": {"regime_id": "reg-1", "regime_class": "high_volatility"}}}),
    (["momentum"], {"status": "observed", "regime_state_ref": None,
                    "records": {"momentum": {"regime_id": "reg-1", "regime_class": "high_volatility"}}}),
    (["momentum"], {"status": "observed", "regime_state_ref": "regime-state://reg-1",
                    "records": {"carry": {"regime_id": "reg-1", "regime_class": "high_volatility"}}}),
    (["momentum"], {"status": "observed", "regime_state_ref": "regime-state://reg-1",
                    "records": {"momentum": {"regime_id": "", "regime_class": "high_volatility"}}}),
    (["momentum"], {"regime_id": "reg-1", "strategy_id": "momentum",
                    "regime_class": "high_volatility", "state": "proposed"}),
    (["momentum"], {"regime_id": "reg-1", "strategy_id": "momentum",
                    "regime_class": "high_volatility", "state": "superseded"}),
    (["momentum"], {"regime_id": "reg-1", "strategy_id": "momentum",
                    "regime_class": "high_volatility", "state": "invalidated"}),
    (["momentum"], {"regime_id": "reg-1", "strategy_id": "carry",
                    "regime_class": "high_volatility", "state": "active"}),
    ([], _observed_regime("high_volatility")),
])
def test_regime_standing_question_omitted_without_active_pool_strategy_record(families, regime):
    assert mental_models.active_regime_classes(families, regime) == []
    assert mental_models.regime_standing_question(families, regime) is None
    questions = mental_models.standing_questions(families, regime)
    assert not any(q["key"] == mental_models.REGIME_QUESTION_KEY or "regime" in q["key"] for q in questions)


def test_standing_questions_includes_market_regime_when_active_pool_record_exists_and_stays_deterministic():
    regime_a = {
        "status": "observed",
        "regime_state_ref": "regime-state://reg-1",
        "records": {
            "momentum": {"regime_id": "reg-1", "regime_class": "High_Volatility"},
            "carry": {"regime_id": "reg-2", "regime_class": "risk_off"},
            "outside_pool": {"regime_id": "reg-3", "regime_class": "trending_up"},
        },
    }
    regime_b = {
        "status": "observed",
        "regime_state_ref": "regime-state://reg-2",
        "records": {
            "carry": {"regime_id": "reg-2", "regime_class": "RISK_OFF"},
            "momentum": {"regime_id": "reg-1", "regime_class": "high_volatility"},
        },
    }
    first = mental_models.standing_questions(["Carry", "momentum", "MOMENTUM"], regime_a)
    second = mental_models.standing_questions(["momentum", "carry"], regime_b)
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert [q["key"] for q in first] == ["family:carry", "family:momentum", "risk_lessons", "market_regime"]
    regime_q = first[-1]
    assert regime_q["key"] == "market_regime"
    assert regime_q["anchors"] == ["regime:high_volatility", "regime:risk_off"]
    assert "high_volatility, risk_off" in regime_q["text"]


@pytest.mark.parametrize("questions,reason", [
    ([{"key": "market_regime", "text": "Regime question without anchors?"}], "regime_anchor_required"),
    ([{"key": "market_regime", "text": "Wrong anchor kind?", "anchors": ["family:momentum"]}], "regime_anchor_required"),
    ([{"key": "market_regime", "text": "Mixed anchor kinds?",
       "anchors": ["regime:high_volatility", "family:momentum"]}], "regime_anchor_required"),
    ([{"key": "market_regime", "text": "With memory_types?",
       "anchors": ["regime:high_volatility"], "memory_types": ["strategy_lesson"]}], "regime_anchor_required"),
    ([{"key": "market_regime", "text": "Unknown regime class?",
       "anchors": ["regime:invented_regime"]}], "invalid_regime_anchor"),
])
def test_normalize_questions_rejects_market_regime_without_governed_regime_anchor(memory_client, monkeypatch, questions, reason):
    hs_mod, fake = _questions_env(memory_client, monkeypatch)
    try:
        response = _declare(memory_client, questions=questions)
        assert response.status_code == 422
        assert response.json()["detail"]["reason"] == reason
        assert fake.banks == {}
    finally:
        hs_mod.set_hindsight_client(None)


def test_declare_creates_updates_and_retires_market_regime_standing_question(memory_client, monkeypatch):
    hs_mod, fake = _questions_env(memory_client, monkeypatch)
    try:
        with_high_vol = mental_models.standing_questions(["momentum"], _observed_regime("high_volatility"))
        created = _declare(memory_client, questions=with_high_vol)
        assert created.status_code == 200, created.text
        body = created.json()
        assert sorted(body["created"]) == ["family:momentum", "market_regime", "risk_lessons"]
        bank = hs_mod.persona_bank("tenant-alpha", "persona-alpha")
        models = {m["name"]: m for m in fake.banks[bank].values()}
        assert models["market_regime"]["tags"] == ["anchor:regime:high_volatility"]
        assert models["market_regime"]["trigger"]["tags_match"] == "any_strict"
        assert models["market_regime"]["trigger"]["refresh_after_consolidation"] is True
        assert models["market_regime"]["trigger"]["exclude_mental_models"] is True

        unchanged = _declare(memory_client, questions=with_high_vol).json()
        assert unchanged["created"] == [] and unchanged["updated"] == [] and unchanged["retired"] == []
        assert sorted(unchanged["unchanged"]) == ["family:momentum", "market_regime", "risk_lessons"]

        with_trend_down = mental_models.standing_questions(["momentum"], _observed_regime("trending_down"))
        updated = _declare(memory_client, questions=with_trend_down).json()
        assert updated["updated"] == ["market_regime"] and updated["retired"] == []
        assert {m["name"]: m for m in fake.banks[bank].values()}["market_regime"]["tags"] == [
            "anchor:regime:trending_down"
        ]

        without_regime = mental_models.standing_questions(
            ["momentum"],
            {"status": "unavailable", "reason": "no_active_record_for_pool_strategies",
             "session": "2026-11-17", "regime_state_ref": None, "regime_tag": "", "records": {}},
        )
        retired = _declare(memory_client, questions=without_regime).json()
        assert retired["retired"] == ["market_regime"]
        assert sorted(m["name"] for m in fake.banks[bank].values()) == ["family:momentum", "risk_lessons"]
    finally:
        hs_mod.set_hindsight_client(None)


def test_agents_cannot_create_or_edit_standing_questions_and_answers_are_readable_only_by_owning_persona(
    memory_client, monkeypatch
):
    hs_mod, fake = _questions_env(memory_client, monkeypatch)
    broker_mod = load_persona_tool_broker()
    try:
        questions = mental_models.standing_questions(["momentum"], _observed_regime("high_volatility"))
        for non_owner_roles in (("persona_session",), ("consultation_session",), ("researcher",), ("operator",)):
            assert _declare(memory_client, roles=non_owner_roles, questions=questions).status_code == 403

        mine = _persona_auth_payload(
            persona_id="persona-alpha",
            relevance_scope="persona_private",
            content={"summary": "High volatility regime requires wider stops", "tags": ["regime:high_volatility"]},
        )
        theirs = _persona_auth_payload(
            persona_id="persona-beta",
            source_event_id="PM-BETA",
            relevance_scope="persona_private",
            content={"summary": "BETA private regime lesson", "tags": ["regime:high_volatility"]},
        )
        token_a = _make_jwt(tenant_id="tenant-alpha", roles=["operator"])
        for entry in (mine, theirs):
            entry["tenant_id"] = "tenant-alpha"
            assert memory_client.post(
                "/api/memory/persona-entries", json=entry, headers={"Authorization": f"Bearer {token_a}"}
            ).status_code == 201
        hs_mod.flush_mirrors()
        hs_mod.set_hindsight_client(fake)

        for pid in ("persona-alpha", "persona-beta"):
            assert _declare(memory_client, persona=pid, questions=questions).status_code == 200
        alpha_bank = hs_mod.persona_bank("tenant-alpha", "persona-alpha")
        beta_bank = hs_mod.persona_bank("tenant-alpha", "persona-beta")
        fake.answer(alpha_bank, "market_regime", "ALPHA REGIME ANSWER", [mine["memory_id"]])
        fake.answer(beta_bank, "market_regime", "BETA REGIME SECRET", [theirs["memory_id"]])

        # Direct memory route: owning persona session can read only its own persona_id.
        own_resp = memory_client.get(
            "/api/memory/persona-standing-questions",
            params={
                "persona_id": "persona-alpha",
                "actor_id": "persona-alpha",
                "actor_roles": ["system", "persona_session"],
                "session_id": "cycle-1",
                "session_persona_id": "persona-alpha",
            },
            headers={"Authorization": f"Bearer {_make_jwt(sub='persona-alpha', tenant_id='tenant-alpha', roles=['system', 'persona_session'])}"},
        )
        assert own_resp.status_code == 200, own_resp.text
        by_key = {a["key"]: a for a in own_resp.json()["answers"]}
        assert by_key["market_regime"]["status"] == "answered"
        assert by_key["market_regime"]["text"] == "ALPHA REGIME ANSWER"
        assert by_key["market_regime"]["citations"] == [mine["memory_id"]]
        assert "BETA REGIME SECRET" not in own_resp.text

        cross_persona = memory_client.get(
            "/api/memory/persona-standing-questions",
            params={
                "persona_id": "persona-alpha",
                "actor_id": "persona-beta",
                "actor_roles": ["system", "persona_session"],
                "session_id": "cycle-2",
                "session_persona_id": "persona-beta",
            },
            headers={"Authorization": f"Bearer {_make_jwt(sub='persona-beta', tenant_id='tenant-alpha', roles=['system', 'persona_session'])}"},
        )
        assert cross_persona.status_code == 403
        assert "ALPHA REGIME ANSWER" not in cross_persona.text

        foreign_tenant = _read(memory_client, tenant="tenant-beta", persona="persona-alpha")
        assert foreign_tenant.status_code == 200 and foreign_tenant.json()["answers"] == []
        assert "ALPHA REGIME ANSWER" not in foreign_tenant.text

        committee = _read(memory_client, roles=("consultation_session",), session_persona_id="persona-alpha")
        assert committee.status_code == 403

        # Broker boundary: persona agents have read-only standing-question access and cannot create or edit questions.
        def memory_get(path, params, *, actor_roles=None):
            resp = memory_client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {_make_jwt(sub=params['actor_id'], tenant_id='tenant-alpha', roles=list(actor_roles or ['operator']))}"},
            )
            if resp.status_code != 200:
                detail = resp.json().get("detail") if resp.headers.get("content-type", "").startswith("application/json") else {}
                reason = detail.get("reason") if isinstance(detail, dict) else f"http_{resp.status_code}"
                raise broker_mod.MemoryCallError(resp.status_code, str(reason))
            return resp.json()

        broker = broker_mod.PersonaToolBroker(
            broker_mod.BrokerConfig(enabled=True, token="broker-secret"),
            agent_lookup=lambda aid: {"id": aid, "tools": dict(PERSONA_AGENT_TOOL_PROFILE)}
            if aid in {"persona-alpha", "persona-beta"} else None,
            memory_get=memory_get,
        )
        broker.register_run("persona-alpha", broker_mod.PURPOSE_DAILY_CYCLE, "u-alpha")
        broker.register_run("persona-beta", broker_mod.PURPOSE_DAILY_CYCLE, "u-beta")
        broker.register_run("persona-alpha", broker_mod.PURPOSE_PANEL_INTERPRETATION, "u-panel")

        def authorize_then_call(*, body):
            # As the plugin does: authorize in the hook, then call with the same ids; a refusal at authorize ends it.
            authorized = broker.handle(authorization="Bearer broker-secret", body=body, authorize_only=True)
            if authorized.status_code != 200:
                return authorized
            return broker.handle(authorization="Bearer broker-secret", body=body)

        for forbidden_tool in ("persona_standing_questions_declare", "persona_mental_model_write", "persona_mental_model_edit"):
            # tool_outside_profile is asserted at authorize (the hook step); a call is never reached.
            refused = broker.handle(
                authorization="Bearer broker-secret",
                body={"tool": forbidden_tool, "agent_id": "persona-alpha", "run_id": "r-alpha",
                      "session_key": "agent:persona-alpha:u-alpha", "tool_call_id": "tc-" + forbidden_tool,
                      "params": {}},
                authorize_only=True,
            )
            assert refused.status_code == 403 and refused.body["reason"] == "tool_outside_profile"

        edit_attempt = authorize_then_call(
            body={"tool": PERSONA_MENTAL_MODEL_READ_TOOL, "agent_id": "persona-alpha", "run_id": "r-alpha",
                  "session_key": "agent:persona-alpha:u-alpha", "tool_call_id": "tc-x",
                  "params": {"persona_id": "persona-beta", "questions": questions}},
        )
        assert edit_attempt.status_code == 422 and edit_attempt.body["reason"] == "invalid_parameters"

        alpha_read = authorize_then_call(
            body={"tool": PERSONA_MENTAL_MODEL_READ_TOOL, "agent_id": "persona-alpha", "run_id": "r-alpha",
                  "session_key": "agent:persona-alpha:u-alpha", "tool_call_id": "tc-x", "params": {}},
        )
        assert alpha_read.status_code == 200
        alpha_answers = {a["key"]: a for a in alpha_read.body["result"]["answers"]}
        assert alpha_answers["market_regime"]["text"] == "ALPHA REGIME ANSWER"
        assert "BETA REGIME SECRET" not in json.dumps(alpha_read.body)

        beta_read = authorize_then_call(
            body={"tool": PERSONA_MENTAL_MODEL_READ_TOOL, "agent_id": "persona-beta", "run_id": "r-beta",
                  "session_key": "agent:persona-beta:u-beta", "tool_call_id": "tc-x", "params": {}},
        )
        assert beta_read.status_code == 200
        assert "ALPHA REGIME ANSWER" not in json.dumps(beta_read.body)

        # tool_not_allowed_for_purpose is asserted at authorize; authorize_then_call returns the authorize refusal.
        panel_read = authorize_then_call(
            body={"tool": PERSONA_MENTAL_MODEL_READ_TOOL, "agent_id": "persona-alpha", "run_id": "r-panel",
                  "session_key": "agent:persona-alpha:u-panel", "tool_call_id": "tc-x", "params": {}},
        )
        assert panel_read.status_code == 403 and panel_read.body["reason"] == "tool_not_allowed_for_purpose"
    finally:
        hs_mod.set_hindsight_client(None)
