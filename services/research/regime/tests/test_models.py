"""Axis models: statsmodels switching variance, vendored continuous jump model, filtered/online only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import services.research.regime as regime
from services.research.regime import features as F
from services.research.regime import models as M
from services.research.regime.tests.fixtures import DECISION_TIME, regime_path, small_config, synthetic_observations

STATE, RET = regime_path(1800, seed=4)
INDEX = pd.bdate_range("2018-01-01", periods=1800)
VOL_FRAME = pd.DataFrame({"return_pct": RET * 100}, index=INDEX)


def test_locked_dependencies_and_vendored_jumpmodels_load():
    """Import test for the deployed research image: statsmodels, ruptures and vendored jumpmodels."""
    import ruptures
    import statsmodels

    assert statsmodels.__version__ == "0.15.0" and ruptures.__version__.lstrip("v") == "1.1.10"
    jump = regime.jump_module()
    assert jump.JumpModel.__module__.startswith("pantheon_vendor_jumpmodels")
    assert regime.JUMPMODELS_VERSION == "0.1.1"
    assert (regime.VENDOR_DIR / "jumpmodels" / "LICENSE").read_text().lstrip().startswith("Apache License")
    # Pinned and vendored, never taken from site-packages.
    import importlib.util
    assert importlib.util.find_spec("jumpmodels") is None or "site-packages" not in str(regime.jump_module().__file__)


def test_volatility_orders_states_by_variance_and_reports_filtered_sessions():
    model = M.fit_volatility(VOL_FRAME, horizon="short")
    assert model.labels == ("low", "high") and model.method == "markov_switching"
    sessions = model.infer(VOL_FRAME)
    assert len(sessions) == 1800
    last = sessions[-1]
    assert last.state in ("low", "high")
    assert sum(last.probabilities.values()) == pytest.approx(1.0)
    assert all(abs(sum(r) - 1) < 1e-9 for r in last.transition_matrix)
    assert set(last.expected_durations) == {"low", "high"} and min(last.expected_durations.values()) > 1
    assert last.sessions_in_state >= 1
    # Fitted states agree with the generating regimes far better than chance.
    agree = np.mean([s.state == ("high" if STATE[i] else "low") for i, s in enumerate(sessions)])
    assert agree > 0.8


def test_volatility_filtered_state_ignores_future_rows():
    model = M.fit_volatility(VOL_FRAME.iloc[:1200], horizon="short")
    full = model.infer(VOL_FRAME)
    prefix = model.infer(VOL_FRAME.iloc[:1500])
    for i in (200, 900, 1499):
        assert full[i].probabilities == pytest.approx(prefix[i].probabilities, abs=1e-9)


def test_volatility_never_reads_smoothed_probabilities(monkeypatch):
    from statsmodels.tsa.regime_switching import markov_switching as ms
    import sys

    original = ms.MarkovSwitchingResults.__getattribute__

    def guarded(self, name):
        caller = sys._getframe(1).f_code.co_filename.replace("\\", "/")
        if "smoothed" in name and "/services/research/regime/" in caller:
            raise AssertionError(f"smoothed read: {name}")
        return original(self, name)

    monkeypatch.setattr(ms.MarkovSwitchingResults, "__getattribute__", guarded)
    model = M.fit_volatility(VOL_FRAME.iloc[:900], horizon="short")
    out = [s.to_dict() for s in model.infer(VOL_FRAME.iloc[:900])]
    assert not any("smoothed" in key for d in out for key in d)


def test_volatility_fit_fails_closed_on_short_or_bad_input():
    with pytest.raises(M.RegimeModelError, match="history_short"):
        M.fit_volatility(VOL_FRAME.iloc[:50], horizon="short")
    bad = VOL_FRAME.copy()
    bad.iloc[5, 0] = np.nan
    with pytest.raises(M.RegimeModelError, match="input_invalid"):
        M.fit_volatility(bad, horizon="short")


def _trend_inputs():
    obs, _, _ = synthetic_observations(1500, seed=2)
    return F.build_trend_inputs(obs, decision_time=DECISION_TIME, horizon="short", config=small_config())


def test_jump_trend_has_three_ordered_states_and_probability_vectors():
    inputs = _trend_inputs()
    model = M.fit_jump_axis(inputs.frame, inputs.returns, axis="trend", horizon="short", n_states=3,
                            jump_penalty=30.0, config=small_config())
    assert model.labels == ("up", "mean_reverting", "down") and model.method == "jumpmodels-cjm"
    assert model.library == "jumpmodels 0.1.1"
    sessions = model.infer(inputs.frame)
    assert len(sessions) == len(inputs.frame)
    assert all(abs(sum(s.probabilities.values()) - 1) < 1e-9 for s in sessions)
    assert len(sessions[-1].probabilities) == 3 and len(sessions[-1].transition_matrix) == 3
    # states are ordered by a fixed statistic: the 'up' centre (state 0) has the highest return feature
    ret_col = inputs.frame.columns.get_loc("ewm_return")
    assert model.centers[0][ret_col] >= model.centers[-1][ret_col]


def test_jump_online_prediction_uses_only_earlier_rows():
    inputs = _trend_inputs()
    model = M.fit_jump_axis(inputs.frame, inputs.returns, axis="trend", horizon="short", n_states=3,
                            jump_penalty=30.0, config=small_config())
    full = model.infer(inputs.frame)
    prefix = model.infer(inputs.frame.iloc[:900])
    for i in (100, 500, 899):
        assert full[i].probabilities == pytest.approx(prefix[i].probabilities, abs=1e-12)


def test_jump_model_round_trips_through_serialisation():
    inputs = _trend_inputs()
    model = M.fit_jump_axis(inputs.frame, inputs.returns, axis="risk_appetite", horizon="short", n_states=2,
                            jump_penalty=30.0, config=small_config())
    clone = M.model_from_dict(model.to_dict())
    assert clone.fingerprint() == model.fingerprint()
    assert [s.state for s in clone.infer(inputs.frame)] == [s.state for s in model.infer(inputs.frame)]
    vol = M.fit_volatility(VOL_FRAME.iloc[:900], horizon="short")
    assert M.model_from_dict(vol.to_dict()).fingerprint() == vol.fingerprint()


def test_jump_fit_fails_closed_on_nonfinite_or_short_features():
    inputs = _trend_inputs()
    bad = inputs.frame.copy()
    bad.iloc[3, 0] = np.inf
    with pytest.raises(M.RegimeModelError, match="nonfinite"):
        M.fit_jump_axis(bad, inputs.returns, axis="trend", horizon="short", n_states=3, jump_penalty=30.0)
    with pytest.raises(M.RegimeModelError, match="history_short"):
        M.fit_jump_axis(inputs.frame.iloc[:100], inputs.returns, axis="trend", horizon="short", n_states=3,
                        jump_penalty=30.0)
    with pytest.raises(M.RegimeModelError, match="unsupported"):
        M.fit_jump_axis(inputs.frame, inputs.returns, axis="trend", horizon="short", n_states=4, jump_penalty=30.0)


def test_no_language_model_or_network_import_in_regime_package():
    from pathlib import Path

    text = "\n".join(p.read_text() for p in Path(regime.__file__).parent.glob("*.py"))
    for needle in ("openai", "anthropic", "langchain", "requests", "urllib.request"):
        assert needle not in text, needle


def test_library_loads_when_the_research_directory_shadows_statsmodels():
    """The service runs with services/research on sys.path, where a local package named statsmodels
    exists. A plain ``import statsmodels`` there loads the adapter package; the regime code must not."""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(regime.__file__).resolve().parents[3]
    code = (
        "import statsmodels, sys;"
        "assert 'services/research/statsmodels' in statsmodels.__file__.replace('\\\\','/'), 'shadow not reproduced';"
        "import services.research.regime.models as m;"
        "import pandas as pd, numpy as np;"
        "r=np.random.default_rng(0).normal(0,1,400); r[200:]*=4;"
        "f=pd.DataFrame({'return_pct':r}, index=pd.bdate_range('2020-01-01',periods=400));"
        "model=m.fit_volatility(f,horizon='short'); print(model.library, model.infer(f)[-1].state);"
        "assert 'site-packages' in sys.modules['statsmodels'].__file__, sys.modules['statsmodels'].__file__"
    )
    env = {"PYTHONPATH": f"{repo}:{repo / 'services' / 'research'}", "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd="/", timeout=120)
    assert out.returncode == 0, out.stderr[-1500:]
    assert out.stdout.startswith("statsmodels 0.15.0 high")
