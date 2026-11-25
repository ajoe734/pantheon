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
    bounds={"gross_limit": 2.0, "max_leverage": 2.0, "max_single_name_weight": 0.20, "min_cash_reserve_pct": 0.10},
)

GOOD = {"gross_limit": 0.8, "net_limit": 0.8, "max_leverage": 1.0, "max_single_name_weight": 0.15,
        "min_cash_reserve_pct": 0.12,
        "max_sector_exposure": 0.35,
        "turnover_limit": 1.2,
        "liquidity_constraints": {"min_avg_daily_volume": 500000, "max_order_pct_adv": 0.05},
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
    ({"gross_limit": 2.01}, "outside_bounds:gross_limit=2.01 exceeds low bound 2.0"),
    ({"max_leverage": 2.5}, "outside_bounds:max_leverage=2.5 exceeds low bound 2.0"),
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
    assert "none configured" in build_prompt(DraftInputs(**{**INPUTS.__dict__, "bounds": {}, "profile": "unbounded-x"}))


def test_drafter_prompt_carries_all_four_bounds() -> None:
    prompt = build_prompt(INPUTS)
    for name in ("gross_limit", "max_leverage", "max_single_name_weight", "min_cash_reserve_pct"):
        assert f'"{name}": ' in prompt.split("rejected: ", 1)[1].split(". Return", 1)[0]


def test_capital_validator_and_configured_bounds() -> None:
    base = {"risk_policy_id": "rp", "version": "2.0", "status": "active", **{k: GOOD[k] for k in GOOD if k != "rationale"}}
    assert validate_drafted_pool_risk_policy(base, profile="low")[0] is not None
    assert validate_drafted_pool_risk_policy({**base, "max_single_name_weight": 0.21}, profile="low")[0] is None
    # a profile without configured bounds is still structurally validated but not bounded
    assert validate_drafted_pool_risk_policy({**base, "max_single_name_weight": 0.9}, profile="high")[0] is not None
    assert validate_drafted_pool_risk_policy({**base, "max_single_name_weight": 1.0}, profile="high")[0] is None
    assert validate_drafted_pool_risk_policy({**base, "min_cash_reserve_pct": None}, profile="high")[0] is None
    assert profile_outer_bounds({})["low"] == {"gross_limit": 2.0, "max_leverage": 2.0,
                                               "max_single_name_weight": 0.2, "min_cash_reserve_pct": 0.1}
    # exactly at the bound passes
    at = {**base, "gross_limit": 2.0, "net_limit": 1.0, "max_leverage": 2.0, "max_single_name_weight": 0.2,
          "min_cash_reserve_pct": 0.1}
    assert validate_drafted_pool_risk_policy(at, profile="low")[0] is not None
    model, reason = validate_drafted_pool_risk_policy({**at, "gross_limit": 2.5}, profile="low")
    assert model is None and "gross_limit=2.5" in reason and "bound 2.0" in reason
    ok = profile_outer_bounds({"PANTHEON_PAPER_RISK_PROFILE_BOUNDS": '{"low": {"gross_limit": 1.5, "max_leverage": 1.5}}'})
    assert ok["low"] == {"gross_limit": 1.5, "max_leverage": 1.5}
    for bad in ('{"low": {"gross_limit": "x"}}', '{"low": {"net_limit": 1}}', '[1]', '{bad'):
        with pytest.raises(Exception):
            profile_outer_bounds({"PANTHEON_PAPER_RISK_PROFILE_BOUNDS": bad})
    custom = profile_outer_bounds({"PANTHEON_PAPER_RISK_PROFILE_BOUNDS": '{"high": {"max_single_name_weight": 0.5}}'})
    assert "low" not in custom and custom["high"] == {"max_single_name_weight": 0.5}
    with pytest.raises(Exception):
        profile_outer_bounds({"PANTHEON_PAPER_RISK_PROFILE_BOUNDS": '{"low": {"gross": 1}}'})


# --- agent existence through the real turn path (HTTP faked at urllib.request.urlopen only) ---------------------
import io
import urllib.error
import urllib.request

import services.persona.risk_policy_draft as agent_client
from services.persona.risk_policy_draft import make_drafter


class _FakeHosted:
    """Gateway /v1/responses answers 400 for an unknown agent; the adapter persona ensure route creates it."""

    def __init__(self, ensure_status: int = 201) -> None:
        self.agents: set[str] = set()
        self.ensure_calls: list[dict] = []
        self.turn_models: list[str] = []
        self.ensure_status = ensure_status

    def __call__(self, req, timeout=None):
        url, body = req.full_url, json.loads(req.data.decode("utf-8"))
        if url.endswith("/v1/responses"):
            self.turn_models.append(body["model"])
            if body["model"].split("/", 1)[1] not in self.agents:
                raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(b'{"error":"unknown agent"}'))
            return _Resp(200, {"model": "mu", "provider": "p",
                               "output": [{"content": [{"type": "output_text", "text": json.dumps(GOOD)}]}]})
        assert url.endswith("/api/openclaw-adapter/agents/persona/ensure"), url
        self.ensure_calls.append({"body": body, "headers": dict(req.header_items())})
        if self.ensure_status >= 400:
            raise urllib.error.HTTPError(url, self.ensure_status, "x", {}, io.BytesIO(b'{"message":"nope"}'))
        pid = body["persona"]["persona_id"]
        self.agents.add(pid)
        return _Resp(self.ensure_status, {"status": "ok", "agent": {"agent_id": pid, "status": "created"}})


class _Resp:
    def __init__(self, status, payload):
        self.status, self._raw = status, json.dumps(payload).encode("utf-8")

    def getcode(self):
        return self.status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def hosted(monkeypatch):
    monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "http://gw:18789")
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://adapter:8104")
    monkeypatch.setenv("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", "tok")
    monkeypatch.setattr(agent_client, "_ensured_risk_officer", {})
    fake = _FakeHosted()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


def test_without_ensure_the_real_turn_path_fails_like_hosted(hosted) -> None:
    from services.persona.risk_policy_draft import default_turn_runner

    result = draft_pool_risk_policy(INPUTS, turn_runner=default_turn_runner)
    assert not result.ok
    assert result.reason == "risk_officer_turn_failed:/v1/responses HTTP 400"


def test_drafter_ensures_own_read_only_agent_once_then_drafts(hosted) -> None:
    drafter = make_drafter()
    first, second = drafter(INPUTS), drafter(INPUTS)
    assert first.ok and second.ok, (first.reason, second.reason)
    assert first.policy["metadata"]["risk_officer_draft"]["risk_persona_id"] == RISK_PERSONA_ID
    assert len(hosted.ensure_calls) == 1  # idempotent: one ensure per agent identity/digest per process
    assert hosted.ensure_calls[0]["body"]["persona"]["persona_id"] == RISK_PERSONA_ID
    assert hosted.turn_models == [f"openclaw/{RISK_PERSONA_ID}"] * 2


def test_ensure_failure_fails_closed_with_readable_reason_and_no_turn(hosted) -> None:
    hosted.ensure_status = 503
    result = make_drafter()(INPUTS)
    assert not result.ok and result.policy is None
    assert result.reason.startswith("risk_officer_turn_failed:risk_officer_agent_ensure_failed")
    assert hosted.turn_models == []


def test_ensure_is_not_called_for_other_personas(hosted) -> None:
    from services.persona.risk_policy_draft import ensuring_turn_runner

    seen = []
    ensuring_turn_runner(lambda pid, prompt, **k: seen.append(pid))("persona-a", "x")
    assert seen == ["persona-a"] and hosted.ensure_calls == []


def test_prompt_states_units_and_bounds_in_the_same_units() -> None:
    prompt = build_prompt(INPUTS)
    for needle in ("multiples of the pool's equity", "1.0 = 100% of equity", "fractions of equity",
                   "context for sizing only", "gross_limit at most 2.0 x equity", "max_leverage at most 2.0 x equity",
                   "max_single_name_weight at most 0.2 of equity", "min_cash_reserve_pct at least 0.1 of equity"):
        assert needle in prompt
    assert "Your previous draft was refused" not in prompt
    assert "gross_limit" in build_prompt(INPUTS, "x") and "Your previous draft was refused" in build_prompt(INPUTS, "x")


HOSTED_BAD = {**GOOD, "gross_limit": 10000, "net_limit": 10000, "max_leverage": 1.0, "max_single_name_weight": 0.1,
              "min_cash_reserve_pct": 0.2}


def _sequence_runner(payloads, calls):
    items = list(payloads)

    def run(pid, prompt, **kwargs):
        calls.append(prompt)
        return _runner(items[min(len(calls) - 1, len(items) - 1)])(pid, prompt)
    return run


def test_refused_draft_is_retried_once_with_reason_then_accepted() -> None:
    calls: list = []
    result = draft_pool_risk_policy(INPUTS, turn_runner=_sequence_runner([HOSTED_BAD, GOOD], calls))
    assert result.ok, result.reason
    assert len(calls) == 2
    assert "Your previous draft was refused" not in calls[0]
    assert "gross_limit=10000" in calls[1] and "bound 2.0" in calls[1]


def test_two_refusals_fail_closed_after_exactly_two_turns() -> None:
    calls: list = []
    result = draft_pool_risk_policy(INPUTS, turn_runner=_sequence_runner([HOSTED_BAD, HOSTED_BAD, GOOD], calls))
    assert not result.ok and result.policy is None and "gross_limit=10000" in result.reason
    assert len(calls) == 2


def test_malformed_first_answer_is_retried_and_failed_turn_is_not() -> None:
    calls: list = []
    assert draft_pool_risk_policy(INPUTS, turn_runner=_sequence_runner(["not json", GOOD], calls)).ok
    assert len(calls) == 2
    calls = []
    failed = draft_pool_risk_policy(INPUTS, turn_runner=_sequence_runner(
        [{}], calls) if False else (lambda pid, prompt, **k: calls.append(prompt) or _runner(GOOD, status="error")(pid, prompt)))
    assert not failed.ok and len(calls) == 1


def test_blueprint_limits_draft_values_explicit_omission_with_reason_and_outer_bounds() -> None:
    # 1. Explicit values are carried into the validated policy.
    res_val = draft_pool_risk_policy(INPUTS, turn_runner=_runner(GOOD))
    assert res_val.ok and res_val.policy is not None
    assert res_val.policy["max_sector_exposure"] == 0.35
    assert res_val.policy["turnover_limit"] == 1.2
    assert res_val.policy["liquidity_constraints"] == {
        "min_avg_daily_volume": 500000.0,
        "max_order_pct_adv": 0.05,
    }

    # 2. Explicit null with reason records omission reason and omits the limit from policy.
    with_omitted = {
        **GOOD,
        "max_sector_exposure": {"24": 0.30, "*": 0.25},
        "turnover_limit": None,
        "liquidity_constraints": {"none": True, "reason": "single liquid large-cap mandate on 2317.TW"},
        "omitted_limits": {
            "turnover_limit": "daily rebalance cadence bounded by single-name cap on 100000 TWD pool",
        },
    }
    res_omit = draft_pool_risk_policy(INPUTS, turn_runner=_runner(with_omitted))
    assert res_omit.ok and res_omit.policy is not None
    assert res_omit.policy["max_sector_exposure"] == {"24": 0.30, "*": 0.25}
    assert "turnover_limit" not in res_omit.policy
    assert "liquidity_constraints" not in res_omit.policy
    omitted_meta = res_omit.policy["metadata"]["risk_officer_draft"]["omitted_limits"]
    assert "100000 TWD" in omitted_meta["turnover_limit"]
    assert "2317.TW" in omitted_meta["liquidity_constraints"]

    # 3. Missing blueprint limit key or null without reason is rejected.
    no_sector_key = {k: v for k, v in GOOD.items() if k != "max_sector_exposure"}
    res_no_key = draft_pool_risk_policy(INPUTS, turn_runner=_runner(no_sector_key))
    assert not res_no_key.ok and "missing_blueprint_limit:max_sector_exposure" in res_no_key.reason

    null_no_reason = {**GOOD, "turnover_limit": None}
    res_no_reason = draft_pool_risk_policy(INPUTS, turn_runner=_runner(null_no_reason))
    assert not res_no_reason.ok and "missing_omission_reason:turnover_limit" in res_no_reason.reason

    # 4. Configured operator outer bounds for sector, turnover, and liquidity are enforced.
    bounded_inputs = DraftInputs(
        **{
            **INPUTS.__dict__,
            "bounds": {
                **INPUTS.bounds,
                "max_sector_exposure": 0.30,
                "turnover_limit": 1.0,
                "max_order_pct_adv": 0.03,
                "min_avg_daily_volume": 600000.0,
            },
        }
    )
    prompt = build_prompt(bounded_inputs)
    assert "max_sector_exposure at most 0.3 of equity" in prompt
    assert "turnover_limit at most 1.0 x equity" in prompt
    assert "max_order_pct_adv at most 0.03 of 20d ADV" in prompt
    assert "min_avg_daily_volume at least 600000.0 20d ADV" in prompt

    res_exceeds_sector = draft_pool_risk_policy(bounded_inputs, turn_runner=_runner(GOOD))
    assert not res_exceeds_sector.ok and "outside_bounds:max_sector_exposure" in res_exceeds_sector.reason

