"""Evaluator over real fitted models and synthetic governed observations (design R5, R8, R10, R12)."""
from __future__ import annotations

import copy
import json

import pytest

from services.registry.regime import evaluator as E
from services.registry.regime.profiles import RegimeProfile
from services.registry.regime.store import RegimeStore
from services.registry.regime.tests import support as S


def _evaluator(store, observations=None, calls=None):
    obs = S.observations() if observations is None else observations

    def load():
        if calls is not None:
            calls.append(1)
        return obs

    return E.RegimeEvaluator(store, load, clock=lambda: "2026-11-16T09:00:00Z", config=S.CONFIG)


def _run(store, session, models=None, strategies=None, **kw):
    ev = kw.pop("evaluator", None) or _evaluator(store)
    return ev.evaluate(session=session, models=S.model_entries() if models is None else models,
                       strategies=strategies if strategies is not None else [S.strategy()], **kw)


def test_every_axis_reports_filtered_state_and_no_smoothed_value():
    store = RegimeStore()
    session = S.sessions()[-1]
    _run(store, session)
    ms = store.list_market_states("TW", session)[0]
    for axis in E.MODEL_AXES:
        for horizon in E.HORIZONS:
            d = ms["axes"][axis][horizon]
            assert d["available"], d
            assert sum(d["probabilities"].values()) == pytest.approx(1.0)
            for key in ("state", "sessions_in_state", "expected_durations", "transition_matrix", "last_switch_session", "model"):
                assert key in d
    assert ms["axes"]["macro"]["available"]
    assert "smoothed" not in json.dumps(ms)


def test_strategy_record_is_schema_valid_and_derived_from_profile():
    store = RegimeStore()
    session = S.sessions()[-1]
    _run(store, session, strategies=[S.strategy("vol", regime={"primary_axis": "volatility", "horizon": "short"}),
                                      S.strategy("trend")])
    vol = store.list_regimes(strategy_id="vol")[0]["record"]
    trend = store.list_regimes(strategy_id="trend")[0]["record"]
    validator = E._schema_validator()
    for rec in (vol, trend):
        assert not list(validator.iter_errors(rec))
        assert rec["model_ref"]["model_version"] in (rec["feature_snapshot"]["axes"][rec["feature_snapshot"]["profile"]["primary_axis"]]["model"],)
        assert set(rec["regime_scores"]) <= {c for m in E.STATE_TO_CLASS.values() for c in m.values()}
        assert sum(rec["regime_scores"].values()) <= 1.0 + 1e-9
        assert -1.0 <= rec["feature_snapshot"]["trend_strength"] <= 1.0
    assert vol["feature_snapshot"]["profile"]["primary_axis"] == "volatility"
    assert vol["regime_class"] in ("high_volatility", "low_volatility", "regime_transition", "mixed")
    assert vol["model_ref"]["model_family"] == "markov_switching" and trend["model_ref"]["model_family"] == "custom"
    assert vol["feature_snapshot"]["volatility_regime"] in ("low", "elevated")


def test_evaluate_is_idempotent_and_reads_governed_data_once():
    store, calls = RegimeStore(), []
    ev = _evaluator(store, calls=calls)
    session = S.sessions()[-1]
    first = _run(store, session, evaluator=ev)
    second = _run(store, session, evaluator=ev)
    assert first["sets"][0]["computed"] is True and second["sets"][0]["computed"] is False
    assert len(calls) == 1  # the second call never reads governed data
    assert second["sets"][0]["new_regime_ids"] == []
    assert len(store.list_regimes()) == 1 and len(store.list_market_states("TW")) == 1


def test_incomplete_session_stores_nothing():
    store = RegimeStore()
    session = S.sessions()[-1]
    obs = [o for o in S.observations() if not (o["dataset"] == "tw_index_daily" and str(o["event_time"])[:10] == session)]
    with pytest.raises(E.EvaluationRefused) as exc:
        _run(store, session, evaluator=_evaluator(store, obs))
    assert exc.value.code == "session_data_incomplete"
    assert store.list_regimes(served_only=False) == [] and store.list_market_states("TW", served_only=False) == []
    # Once the data is complete the same call computes.
    assert _run(store, session)["sets"][0]["computed"] is True


def test_no_model_refuses():
    with pytest.raises(E.EvaluationRefused) as exc:
        _run(RegimeStore(), S.sessions()[-1], models=[])
    assert exc.value.code == "no_regime_model"


def test_candidate_records_are_proposed_and_never_served():
    store = RegimeStore()
    session = S.sessions()[-1]
    out = _run(store, session, models=S.model_entries("candidate"))
    assert out["sets"][0]["model_status"] == "candidate"
    assert [r["record"]["state"] for r in store.list_regimes(served_only=False)] == ["proposed"]
    assert store.list_regimes() == [] and store.list_market_states("TW") == []


def test_approved_record_moves_validated_active_then_superseded():
    store = RegimeStore()
    prev, last = S.sessions()[-2], S.sessions()[-1]
    _run(store, prev)
    first = store.list_regimes()[0]["record"]
    assert first["state"] == "active"
    assert [h["state"] for h in first["metadata"]["lifecycle"]] == ["validated", "active"]
    _run(store, last)
    rows = {r["session"]: r["record"] for r in store.list_regimes()}
    assert rows[last]["state"] == "active"
    assert rows[prev]["state"] == "superseded" and rows[prev]["superseded_by"] == rows[last]["regime_id"]
    assert not list(E._schema_validator().iter_errors(rows[prev]))


def test_late_backfill_never_displaces_a_later_active_record():
    store = RegimeStore()
    prev, last = S.sessions()[-2], S.sessions()[-1]
    _run(store, last)
    _run(store, prev)
    rows = {r["session"]: r["record"] for r in store.list_regimes()}
    assert rows[last]["state"] == "active" and rows[prev]["state"] == "superseded"
    assert rows[prev]["superseded_by"] == rows[last]["regime_id"]


def test_revised_inputs_invalidate_and_recompute_when_verified():
    store = RegimeStore()
    session = S.sessions()[-1]
    _run(store, session)
    revised = copy.deepcopy(S.observations())
    for o in revised:
        if o["dataset"] == "tw_index_daily" and str(o["event_time"])[:10] == S.sessions()[-5]:
            o["value"] = o["value"] * 1.01
            o["available_time"] = "2026-11-16T08:00:00Z"
    unchanged = _run(store, session, evaluator=_evaluator(store, revised))  # no verify: stored result returned
    assert unchanged["sets"][0]["computed"] is False
    out = _run(store, session, evaluator=_evaluator(store, revised), verify_inputs=True)["sets"][0]
    assert out["revision"] == 2 and out["computed"] and len(out["invalidated"]) == 1
    states = {r["revision"]: r["record"]["state"] for r in store.list_regimes()}
    assert states == {1: "invalidated", 2: "active"}
    # Verifying again with unchanged inputs is stable.
    again = _run(store, session, evaluator=_evaluator(store, revised), verify_inputs=True)["sets"][0]
    assert again["revision"] == 2 and again["computed"] is False


def test_missing_primary_axis_gives_no_record_and_missing_secondary_is_recorded():
    store = RegimeStore()
    session = S.sessions()[-1]
    no_breadth = [o for o in S.observations() if o["dataset"] != "tw_market_breadth_daily"]
    out = _run(store, session, evaluator=_evaluator(store, no_breadth),
               strategies=[S.strategy("trendy"), S.strategy("volly", regime={"primary_axis": "volatility", "horizon": "short"})])
    assert [s["strategy_id"] for s in out["sets"][0]["skipped"]] == ["trendy"]
    assert out["sets"][0]["skipped"][0]["reason"].startswith("primary_axis_unavailable:trend/medium")
    assert [r["record"]["strategy_id"] for r in store.list_regimes()] == ["volly"]
    axes = store.list_regimes()[0]["record"]["feature_snapshot"]["axes"]
    assert axes["trend"]["available"] is False and "breadth" in axes["trend"]["reason"]
    assert "probabilities" not in axes["trend"]  # no placeholder or synthetic value


def test_axis_without_model_is_recorded_unavailable():
    store = RegimeStore()
    _run(store, S.sessions()[-1], models=S.model_entries(axes=("volatility",)),
         strategies=[S.strategy("v", regime={"primary_axis": "volatility", "horizon": "short"})])
    axes = store.list_regimes()[0]["record"]["feature_snapshot"]["axes"]
    assert axes["trend"] == {"available": False, "reason": "no_approved_model:trend:short", "model": None}
    assert "trend_strength" not in store.list_regimes()[0]["record"]["feature_snapshot"]


# -- derivation rules (R8) on controlled axis states ---------------------------------------------

def _state(primary_probs, primary_axis="trend", sessions_in_state=30, last_switch="2026-01-01", macro="expansion"):
    def axis(probs, **kw):
        labels = list(probs)
        return {"available": True, "horizon": "medium", "state": max(probs, key=probs.get), "probabilities": probs,
                "transition_matrix": [[1.0 if i == j else 0.0 for j in range(len(labels))] for i in range(len(labels))],
                "expected_durations": {k: 20.0 for k in labels}, "sessions_in_state": sessions_in_state,
                "last_switch_session": last_switch, "as_of_session": "2026-11-13", "window_start": "2020-01-01",
                "feature_values": {"x": 1.0}, "input_refs": ["tw_index_daily.close:TAIEX"], "model": "m@1/a",
                "model_family": "custom", "model_artifact_id": "a", "model_registry_id": "r"}
    ms = {"market": "TW", "session": "2026-11-13", "model_set_version": "set-abcdef0123456789", "model_status": "approved",
          "revision": 1, "input_refs": ["tw_index_daily.close:TAIEX"],
          "axes": {"volatility": {h: axis({"low": 0.9, "high": 0.1}) for h in E.HORIZONS},
                   "trend": {h: axis({"up": 0.2, "mean_reverting": 0.1, "down": 0.7}) for h in E.HORIZONS},
                   "risk_appetite": {h: axis({"on": 0.6, "off": 0.4}) for h in E.HORIZONS},
                   "macro": {"available": True, "state": macro, "as_of_session": "2026-10", "sessions_in_state": 3,
                             "last_switch_session": None, "monitoring_score": 20.0, "leading_index": 100.0,
                             "leading_index_change": 0.1, "model": E.MACRO_MODEL, "model_family": "rule_based"}}}
    ms["axes"][primary_axis]["medium"] = axis(primary_probs)
    return ms


def _derive(ms, axis="trend"):
    return E.derive_record(S.strategy(), RegimeProfile(axis, "medium", "spec_regime"), ms, now="2026-11-16T09:00:00Z")


@pytest.mark.parametrize("axis,probs,expected", [
    ("volatility", {"low": 0.2, "high": 0.8}, "high_volatility"), ("volatility", {"low": 0.9, "high": 0.1}, "low_volatility"),
    ("trend", {"up": 0.8, "mean_reverting": 0.1, "down": 0.1}, "trending_up"),
    ("trend", {"up": 0.1, "mean_reverting": 0.1, "down": 0.8}, "trending_down"),
    ("trend", {"up": 0.1, "mean_reverting": 0.8, "down": 0.1}, "mean_reverting"),
    ("risk_appetite", {"on": 0.7, "off": 0.3}, "risk_on"), ("risk_appetite", {"on": 0.3, "off": 0.7}, "risk_off")])
def test_axis_state_maps_to_regime_class(axis, probs, expected):
    rec, why = _derive(_state(probs, axis, last_switch=None), axis)
    assert why == "" and rec["regime_class"] == expected and rec["confidence"] == max(probs.values())
    assert not list(E._schema_validator().iter_errors(rec))


def test_recent_switch_is_regime_transition_and_no_state_at_half_is_mixed():
    rec, _ = _derive(_state({"up": 0.8, "mean_reverting": 0.1, "down": 0.1}, sessions_in_state=3, last_switch="2026-11-10"))
    assert rec["regime_class"] == "regime_transition"
    rec, _ = _derive(_state({"up": 0.45, "mean_reverting": 0.3, "down": 0.25}))
    assert rec["regime_class"] == "mixed" and rec["confidence"] == 0.45
    rec, _ = _derive(_state({"up": 0.34, "mean_reverting": 0.33, "down": 0.33}))
    assert rec["regime_class"] == "mixed" and rec["confidence"] <= 0.5  # low-confidence schema rule holds
    assert not list(E._schema_validator().iter_errors(rec))


def test_feature_snapshot_layout():
    rec, _ = _derive(_state({"up": 0.1, "mean_reverting": 0.2, "down": 0.7}, macro="contraction"))
    snap = rec["feature_snapshot"]
    assert snap["macro_environment"] == "recession" and snap["momentum_signal"] == "negative"
    assert snap["trend_strength"] == pytest.approx(-0.6) and snap["volatility_regime"] == "low"
    assert set(snap["axes"]) == {"volatility", "trend", "risk_appetite", "macro"}
    assert snap["axes"]["trend"]["transition_probabilities"].keys() == {"up", "mean_reverting", "down"}


def test_invalid_scores_fail_the_record():
    ms = _state({"up": 0.8, "mean_reverting": 0.1, "down": 0.1})
    ms["axes"]["trend"]["medium"]["probabilities"] = {"up": 0.9, "mean_reverting": 0.5, "down": 0.1}
    with pytest.raises(ValueError, match="regime_scores_invalid"):
        _derive(ms)
