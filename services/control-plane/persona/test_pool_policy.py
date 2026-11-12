import copy
import math
from datetime import date, timedelta

import pytest

from pool_policy import load_policy, pool_health, reweight

BASIS = "underlying_exposure_fraction_net_taiwan_costs_equal_notional"


def book(returns, checksum="sum"):
    return {"source_ref": "test:observed-book", "checksum": checksum, "basis": BASIS,
            "since_inception": True, "dates": [(date(2026, 1, 1) + timedelta(days=i)).isoformat()
                                               for i in range(len(returns))], "returns": returns}


def member(ref, **kw):
    return {"artifact_ref": ref, "checksum": "sum", "stage": "active", "risk_weight": .5, **kw}


def observations(values):
    return [{"date": d, "source_ref": "test:observed-risk", "gross_notional": v,
             "gross_limit": 100, "drawdown": .02, "drawdown_limit": .1}
            for d, v in zip(book(values)["dates"], values)]


def test_health_window_utilisation_idle_days_and_correlation_spectrum():
    policy = load_policy()
    one = [.01, -.01, .01, -.01] * 5
    two = [.01, .01, -.01, -.01] * 5
    result = pool_health([member("a"), member("b")], {"a": book(one), "b": book(two)}, observations([0, 100] * 10), policy)
    assert result["gross_utilisation"]["value"] == .5
    assert result["drawdown_utilisation"]["value"] == pytest.approx(.2)
    assert result["idle_day_share"]["value"] == .5
    assert result["effective_bets"]["value"] == 2
    assert result["demand_reasons"] == ["low_risk_budget_utilisation"]
    result = pool_health([member("a"), member("b")], {"a": book(one), "b": book(one)}, observations([0] * 20), policy)
    assert result["effective_bets"]["value"] == pytest.approx(1)
    assert "too_few_independent_bets" in result["demand_reasons"]


def test_each_missing_measure_is_unknown_and_alignment_is_by_date():
    policy = load_policy()
    rows = observations([10] * 20)
    rows[0].pop("gross_limit")
    result = pool_health([member("a")], {}, rows, policy)
    assert result["gross_utilisation"]["status"] == "unknown"
    assert result["drawdown_utilisation"]["status"] == "known"
    assert result["effective_bets"]["reason"] == "virtual_book_source_missing"
    assert result["idle_day_share"]["status"] == "known"
    shifted = book([.01, -.01] * 10)
    shifted["dates"] = book([0] * 21)["dates"][1:]
    assert pool_health([member("a")], {"a": shifted}, observations([10] * 20), policy)["effective_bets"]["status"] == "unknown"
    assert all(pool_health([], {}, [], policy)[key]["status"] == "unknown" for key in
               ("gross_utilisation", "drawdown_utilisation", "effective_bets", "idle_day_share"))


def test_risk_parity_tilt_trial_cap_and_haircuts_leave_idle_budget():
    policy = load_policy().model_copy(update={"tilt_sharpe_scale": 0.0})
    values = [.01, -.005] * 10
    members = [member("a"), member("b", stage="trial")]
    result, _ = reweight(members, {"a": book(values), "b": book([v * 2 for v in values])}, policy, "now")
    assert result[0]["risk_weight"] == pytest.approx(2/3)
    assert result[1]["risk_weight"] == pytest.approx(1/3)  # 20 distinct observed days, cap lifted
    result, _ = reweight(members, {"a": book(values[:18]), "b": book(values[:18])}, policy, "now")
    assert result[1]["risk_weight"] == .1 and sum(m["risk_weight"] for m in result) == .6
    halved, stats = reweight([member("a")], {"a": book([.01, .02, -.05])}, policy, "now")
    repeated, _ = reweight(halved, {"a": book([.01, .02, -.05])}, policy, "later", stats)
    assert halved[0]["risk_weight"] == repeated[0]["risk_weight"] == .25
    removed, _ = reweight([member("a")], {"a": book([.01, .02, -.1])}, policy, "now")
    assert removed[0]["stage"] == "removed" and removed[0]["risk_weight"] == 0
    assert removed[0]["removal_reason"] == "own_high_water_drawdown"
    tilted, _ = reweight([member("a"), member("b")], {"a": book(values), "b": book([-v for v in values])}, load_policy(), "now")
    assert tilted[0]["risk_weight"] > tilted[1]["risk_weight"]


@pytest.mark.parametrize("mutate", [lambda b: b.pop("since_inception"), lambda b: b.update(returns=[math.nan, .01]),
                                    lambda b: b.update(dates=["2026-01-01"] * 2), lambda b: b.update(checksum="wrong"),
                                    lambda b: b.update(returns=[0, 0]), lambda b: b.pop("source_ref")])
def test_bad_books_do_not_become_weights(mutate):
    observed = book([.01, -.01])
    mutate(observed)
    with pytest.raises(ValueError):
        reweight([member("a")], {"a": observed}, load_policy(), "now")
