"""Shared test inputs: real models fitted on synthetic governed observations (no mocked model output)."""
from __future__ import annotations

import functools

import pandas as pd

from services.registry.regime import evaluator as E
from services.research.regime.features import BUILDERS
from services.research.regime.models import fit_jump_axis, fit_volatility
from services.research.regime.registration import artifact_id_for, model_version_for
from services.research.tests.test_regime_research_route import small_config, synthetic_observations

CONFIG = small_config()


def visible(observations):
    """Make each observation available on its own event day, so every session is visible at its close."""
    out = []
    for o in observations:
        day = str(o["event_time"])[:10]
        out.append({**o, "available_time": f"{day}T10:00:00Z", "ingest_time": f"{day}T10:00:00Z"})
    return out


@functools.lru_cache(maxsize=1)
def _built():
    obs = visible(synthetic_observations(1500, seed=0)[0])
    days = sorted({str(o["event_time"])[:10] for o in obs if o["dataset"] == "tw_index_daily"})
    session = days[-1]
    models = {}
    for axis in E.MODEL_AXES:
        for horizon in E.HORIZONS:
            inputs = BUILDERS[axis](obs, decision_time=session, horizon=horizon, config=CONFIG)
            assert inputs.available, inputs.reason
            if axis == "volatility":
                model = fit_volatility(inputs.frame, horizon=horizon)
            else:
                model = fit_jump_axis(inputs.frame, inputs.returns, axis=axis, horizon=horizon,
                                      n_states=3 if axis == "trend" else 2, jump_penalty=30.0, config=CONFIG)
            models[(axis, horizon)] = model
    return obs, days, models


def observations():
    return list(_built()[0])


def sessions():
    return list(_built()[1])


def model_entries(state: str = "approved", *, scope: str = "paper", axes=E.MODEL_AXES):
    entries = []
    for (axis, horizon), model in _built()[2].items():
        if axis not in axes:
            continue
        entries.append({
            "registry_id": f"model-{axis}-{horizon}-{state}", "artifact_state": state, "created_at": "2026-11-15T00:00:00Z",
            "metadata": {"regime_classifier": True, "model_family": model.method, "model_version": model_version_for(model),
                         "model_artifact_id": artifact_id_for(model), "axis": axis, "horizon": horizon, "market": "TW",
                         "scope": scope, "model": model.to_dict()}})
    return entries


def strategy(strategy_id="strat-a", tenant="tenant-a", **meta):
    return {"strategy_id": strategy_id, "registry_id": f"reg-{strategy_id}", "metadata": meta, "owner_tenant": tenant}
