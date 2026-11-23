from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.capital.risk_policy import (
    profile_outer_bounds,
    validate_drafted_pool_risk_policy,
)
from services.persona.consult_triggers import RISK_PERSONA_ID
from services.persona.risk_policy_draft import DraftInputs, build_prompt, draft_pool_risk_policy

INPUTS = DraftInputs(
    pool_id="pool-persona-paper-x", tenant_id="tenant-a", persona_id="persona-a", profile="low",
    budget=100000, currency="TWD", instruments=("2317.TW",), mandate="steady TW large caps",
    evidence=("2026-11-19 buy 452 x 2317 @ 221 would use 100,034 TWD cash",),
    policy_id="rp-pool-persona-paper-x", version="2.0",
    bounds={"max_single_name_weight": 0.20, "min_cash_reserve_pct": 0.10},
)

GOOD = {"gross_limit": 0.8, "net_limit": 0.8, "max_leverage": 1.0, "max_single_name_weight": 0.15,
        "min_cash_reserve_pct": 0.12,
        "rationale": "low profile on a 100000 TWD pool trading 2317.TW; the 452-share cash rejection shows a full position is unsafe"}


def _runner(payload, *, status="completed", persona_id=RISK_PERSONA_ID, calls=None):
    def run(pid, prompt, **kwargs):
        if calls is not None:
            calls.append((pid, prompt))
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return SimpleNamespace(persona_id=persona_id, status=status, decision=text, model="m", model_used="mu",
                               provider="p", error=None if status == "completed" else "boom")
    return run


def test_valid_draft_carries_risk_officer_provenance_and_inputs_in_prompt() -> None:
    calls: list = []
    result = draft_pool_risk_policy(INPUTS, turn_runner=_runner(GOOD, calls=calls))
    assert result.ok, result.reason
    assert calls[0][0] == RISK_PERSONA_ID
    for needle in ("pool-persona-paper-x", "TWD", "100000", "2317.TW", "low", "452", "steady TW"):
        assert needle in calls[0][1]
    policy = result.policy
    assert policy["version"] == "2.0" and policy["max_single_name_weight"] == 0.15
    assert 0 < policy["min_cash_reserve_pct"] < 1
    prov = policy["metadata"]["risk_officer_draft"]
    assert prov["source"] == "risk_officer_draft" and prov["risk_persona_id"] == RISK_PERSONA_ID
    assert prov["input_digest"] == INPUTS.input_digest and prov["turn_digest"]
    assert result.policy_digest


@pytest.mark.parametrize("mutate,reason", [
    ({"max_single_name_weight": 0.5}, "outside_bounds:max_single_name_weight"),
    ({"min_cash_reserve_pct": 0.05}, "outside_bounds:min_cash_reserve_pct"),
    ({"max_single_name_weight": 1.0}, "single_name_not_below_1"),
    ({"min_cash_reserve_pct": 0.0}, "not_positive"),
    ({"gross_limit": None}, "missing_or_non_numeric:gross_limit"),
    ({"max_leverage": "2"}, "missing_or_non_numeric:max_leverage"),
    ({"rationale": "ok"}, "rationale_missing"),
    ({"rationale": "a perfectly fine but unrelated explanation text"}, "rationale_cites_no_input"),
])
def test_invalid_or_out_of_bounds_draft_is_rejected_with_reason(mutate, reason) -> None:
    payload = {**GOOD, **mutate}
    result = draft_pool_risk_policy(INPUTS, turn_runner=_runner(payload))
    assert not result.ok and result.policy is None and reason in result.reason


def test_missing_limit_key_and_garbage_and_failed_turn_never_fall_back_to_numbers() -> None:
    missing = {k: v for k, v in GOOD.items() if k != "min_cash_reserve_pct"}
    for runner, reason in (
        (_runner(missing), "missing_or_non_numeric:min_cash_reserve_pct"),
        (_runner("not json at all"), "invalid_json"),
        (_runner(GOOD, status="error"), "risk_officer_turn_failed"),
        (_runner(GOOD, persona_id="persona-someone-else"), "risk_officer_turn_failed"),
    ):
        result = draft_pool_risk_policy(INPUTS, turn_runner=runner)
        assert not result.ok and result.policy is None and reason in result.reason


def test_runner_exception_fails_closed() -> None:
    def boom(*_a, **_k):
        raise RuntimeError("gateway down")

    result = draft_pool_risk_policy(INPUTS, turn_runner=boom)
    assert not result.ok and "risk_officer_turn_failed:RuntimeError" in result.reason


def test_input_digest_is_stable_and_input_sensitive() -> None:
    assert INPUTS.input_digest == DraftInputs(**{**INPUTS.__dict__}).input_digest
    other = DraftInputs(**{**INPUTS.__dict__, "evidence": ()})
    assert other.input_digest != INPUTS.input_digest
    assert "none configured" in build_prompt(DraftInputs(**{**INPUTS.__dict__, "bounds": {}}))


def test_capital_validator_and_configured_bounds() -> None:
    base = {"risk_policy_id": "rp", "version": "2.0", "status": "active", **{k: GOOD[k] for k in GOOD if k != "rationale"}}
    assert validate_drafted_pool_risk_policy(base, profile="low")[0] is not None
    assert validate_drafted_pool_risk_policy({**base, "max_single_name_weight": 0.21}, profile="low")[0] is None
    # a profile without configured bounds is still structurally validated but not bounded
    assert validate_drafted_pool_risk_policy({**base, "max_single_name_weight": 0.9}, profile="high")[0] is not None
    assert validate_drafted_pool_risk_policy({**base, "max_single_name_weight": 1.0}, profile="high")[0] is None
    assert validate_drafted_pool_risk_policy({**base, "min_cash_reserve_pct": None}, profile="high")[0] is None
    assert profile_outer_bounds({})["low"] == {"max_single_name_weight": 0.2, "min_cash_reserve_pct": 0.1}
    custom = profile_outer_bounds({"PANTHEON_PAPER_RISK_PROFILE_BOUNDS": '{"high": {"max_single_name_weight": 0.5}}'})
    assert "low" not in custom and custom["high"] == {"max_single_name_weight": 0.5}
    with pytest.raises(Exception):
        profile_outer_bounds({"PANTHEON_PAPER_RISK_PROFILE_BOUNDS": '{"low": {"gross": 1}}'})
