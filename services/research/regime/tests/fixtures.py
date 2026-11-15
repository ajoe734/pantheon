"""Synthetic governed-shape observations for unit tests of the protocol.

These series prove the algorithms and fail-closed rules only. They are never evidence of how
the models behave on Taiwan data; the integration test over recorded rows is in
``services/research/tests/test_regime_research_route.py``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.research.regime.features import TAIEX_ENTITY, RegimeConfig

DECISION_TIME = "2030-01-01T00:00:00Z"
ACQUIRED = "2029-12-31T00:00:00Z"


def small_config(**overrides) -> RegimeConfig:
    base = dict(min_training_years=4.0, sessions_per_year=245, refit_sessions=150, min_input_sessions=300,
                jump_penalty_grid=(30.0,), jump_n_init=2, min_refits=2)
    base.update(overrides)
    return RegimeConfig(**base)


def _obs(dataset, field, entity, day, value):
    return {"schema_version": "feature_observation.v1", "dataset": dataset, "field": field, "entity": entity,
            "event_time": day, "available_time": ACQUIRED, "ingest_time": ACQUIRED,
            "availability_lag_days": 0, "value": float(value)}


def regime_path(n: int, seed: int = 0, stay: float = 0.985):
    rng = np.random.default_rng(seed)
    state = np.zeros(n, int)
    for i in range(1, n):
        state[i] = state[i - 1] if rng.random() < stay else 1 - state[i - 1]
    ret = np.where(state == 0, rng.normal(0.0006, 0.007, n), rng.normal(-0.001, 0.02, n))
    return state, ret


def synthetic_observations(n: int = 1500, seed: int = 0, *, with_breadth=True, with_chip=True, with_macro=True):
    state, ret = regime_path(n, seed)
    days = pd.bdate_range("2018-01-01", periods=n)
    close = 10000 * np.exp(np.cumsum(ret))
    rng = np.random.default_rng(seed + 1)
    obs = [_obs("tw_index_daily", "close", TAIEX_ENTITY, d.date().isoformat(), c) for d, c in zip(days, close)]
    if with_breadth:
        adv = np.where(state == 0, 700, 400) + rng.integers(-60, 60, n)
        dec = 1500 - adv
        for d, a, b in zip(days, adv, dec):
            day = d.date().isoformat()
            obs.append(_obs("tw_market_breadth_daily", "whole_market_advancers", "TW", day, a))
            obs.append(_obs("tw_market_breadth_daily", "whole_market_decliners", "TW", day, b))
    if with_chip:
        for d, s in zip(days, state):
            day = d.date().isoformat()
            sign = 1 if s == 0 else -1
            for ds, f, v in (
                ("tw_market_institutional_flows", "foreign_net_shares", sign * 1e8 + rng.normal(0, 5e7)),
                ("tw_market_institutional_flows", "trust_net_shares", rng.normal(0, 1e7)),
                ("tw_market_institutional_flows", "dealer_net_shares", rng.normal(0, 1e7)),
                ("tw_market_margin_balances", "margin_balance", 1e9 + rng.normal(0, 1e7)),
                ("taifex_market_positions", "tx_foreign_net_open_interest", sign * 1e4 + rng.normal(0, 3e3)),
                ("taifex_market_put_call", "open_interest_ratio", 100 - sign * 10 + rng.normal(0, 4)),
            ):
                obs.append(_obs(ds, f, "TW", day, v))
    if with_macro:
        for i, month in enumerate(pd.period_range("2017-06", periods=96, freq="M")):
            day = (month.to_timestamp() + pd.offsets.MonthBegin(0)).date().isoformat()
            obs.append(_obs("tw_macro_ndc", "monitoring_score", "TW", day, 10 + (i * 7) % 28))
            obs.append(_obs("tw_macro_ndc", "leading_index", "TW", day, 100 + (i % 5) - 2))
    return obs, state, ret


class FakeRegistry:
    """In-memory registry client recording every call; enforces the draft -> candidate -> approved order."""

    def __init__(self):
        self.entries: dict[str, dict] = {}
        self.calls: list[tuple] = []

    def create_entry(self, payload, *, idempotency_key):
        self.calls.append(("create", payload["artifact_state"], idempotency_key))
        registry_id = f"reg-{len(self.entries) + 1}"
        self.entries[registry_id] = {**payload, "registry_id": registry_id}
        return {"entry": {"registry_id": registry_id, "artifact_state": payload["artifact_state"]}}

    def advance(self, registry_id, body):
        entry = self.entries[registry_id]
        assert body["expected_artifact_state"] == entry["artifact_state"]
        allowed = {"draft": "candidate", "candidate": "approved"}
        assert allowed[entry["artifact_state"]] == body["target_state"]
        entry["artifact_state"] = body["target_state"]
        self.calls.append(("advance", body["target_state"], body.get("approver")))
        return {"entry": {"registry_id": registry_id, "artifact_state": body["target_state"]}}
