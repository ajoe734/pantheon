"""Point-in-time feature construction for the regime axes (design R3, R4, R7).

Every input is read through ``read_feature_series`` over governed observations; nothing is
filled, interpolated across a missing governed row, or synthesised. A missing or too-short
input makes the whole axis ``unavailable`` with a recorded reason, so no model is fitted on a
partial picture.

All transforms are causal: the value at session ``t`` uses rows with event time ``<= t`` only.
Values are first read at one decision time through the governed reader, which applies each
field's availability lag and drops observations that were not yet available.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from services.source_ingestion.feature_catalogue import parse_time, read_feature_series

TAIEX_ENTITY = "發行量加權股價指數"
HORIZONS = ("short", "medium")
AXES = ("volatility", "trend", "risk_appetite", "macro")


@dataclass(frozen=True)
class RegimeConfig:
    """Horizons and thresholds. Defaults are the design values or coordinator proposals.

    ``PROPOSALS`` names every default the design does not fix; evidence lists them.
    """

    # R4: about a 10-session and a 60-session half-life.
    half_life_sessions: Mapping[str, int] = field(default_factory=lambda: {"short": 10, "medium": 60})
    # Volatility medium horizon fits non-overlapping returns over this many sessions (proposal).
    volatility_return_window: Mapping[str, int] = field(default_factory=lambda: {"short": 1, "medium": 5})
    # R11: at least 10 years of TAIEX history, about 245 sessions a year.
    min_training_years: float = 10.0
    sessions_per_year: int = 245
    # R11: refit every six months.
    refit_sessions: int = 122
    # Minimum sessions of every non-index input needed for its axis (proposal).
    min_input_sessions: int = 500
    trend_states: int = 3
    risk_appetite_states: int = 2
    # Continuous jump model (proposals; the penalty is chosen on a validation window).
    jump_penalty_grid: Sequence[float] = (10.0, 30.0, 100.0)
    jump_grid_size: float = 0.05
    jump_n_init: int = 5
    # Walk-forward acceptance gates (proposals, to be calibrated by results).
    signal_delay_sessions: int = 1
    max_switches_per_year: float = 6.0
    min_expected_duration_sessions: float = 5.0
    min_label_stability: float = 0.8
    min_refits: int = 3
    stability_window_sessions: int = 60
    changepoint_tolerance_sessions: int = 10
    changepoint_penalty: float = 10.0
    # Macro rules over the NDC monitoring score (official light thresholds) and leading index.
    macro_expansion_score: float = 23.0
    macro_contraction_score: float = 16.0

    @property
    def min_training_sessions(self) -> int:
        return int(round(self.min_training_years * self.sessions_per_year))

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["jump_penalty_grid"] = list(self.jump_penalty_grid)
        return raw


PROPOSALS: tuple[str, ...] = (
    "volatility_return_window.medium=5", "min_training_years=10 (design R11 proposal)",
    "refit_sessions=122 (six months, R11 proposal)", "min_input_sessions=500",
    "jump_penalty_grid=(10,30,100)", "jump_grid_size=0.05", "jump_n_init=5",
    "max_switches_per_year=6.0", "min_expected_duration_sessions=5.0", "min_label_stability=0.8",
    "min_refits=3", "stability_window_sessions=60", "changepoint_tolerance_sessions=10",
    "changepoint_penalty=10.0", "macro_expansion_score=23", "macro_contraction_score=16",
    "half_life_sessions short=10 medium=60 (R4 'about')",
)


@dataclass
class AxisInputs:
    """One axis' inputs at one decision time, or the reason it cannot be built."""

    axis: str
    horizon: str | None
    available: bool
    reason: str = ""
    frame: pd.DataFrame | None = None  # model features, one row per session, causal
    returns: pd.Series | None = None  # the index return series used for state sorting
    input_refs: tuple[str, ...] = ()
    sessions: int = 0


def _unavailable(axis: str, horizon: str | None, reason: str, refs: Iterable[str] = ()) -> AxisInputs:
    return AxisInputs(axis=axis, horizon=horizon, available=False, reason=reason, input_refs=tuple(sorted(set(refs))))


def _day(value: Any) -> date:
    return parse_time(value).date()


def read_series(observations: Sequence[Mapping[str, Any]], dataset: str, field_name: str, *,
                decision_time: Any, entity: str | None = None) -> pd.Series:
    """Governed point-in-time read as a float series indexed by event date.

    Non-finite or non-numeric values make the read fail closed (the whole series is empty and
    the caller reports the input as missing); nothing is dropped silently.
    """
    rows = read_feature_series(observations, dataset, field_name, decision_time=decision_time, entity=entity)
    values: dict[pd.Timestamp, float] = {}
    for row in rows:
        value = row.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return pd.Series(dtype=float)
        values[pd.Timestamp(_day(row["event_time"]))] = float(value)
    return pd.Series(values, dtype=float).sort_index()


def _ref(dataset: str, field_name: str, entity: str | None = None) -> str:
    return f"feature://{dataset}/{field_name}" + (f"?entity={entity}" if entity else "")


def _ewm_downside_deviation(returns: pd.Series, half_life: int) -> pd.Series:
    downside = returns.clip(upper=0.0)
    return np.sqrt((downside ** 2).ewm(halflife=half_life, min_periods=half_life).mean())


def _zscore_trailing(series: pd.Series, window: int) -> pd.Series:
    """Trailing standardisation: uses only the preceding ``window`` sessions and the current one."""
    mean = series.rolling(window, min_periods=window // 2).mean()
    std = series.rolling(window, min_periods=window // 2).std()
    return (series - mean) / std.replace(0.0, np.nan)


def index_returns(observations: Sequence[Mapping[str, Any]], *, decision_time: Any,
                  config: RegimeConfig, entity: str = TAIEX_ENTITY) -> tuple[pd.Series | None, str, list[str]]:
    close = read_series(observations, "tw_index_daily", "close", decision_time=decision_time, entity=entity)
    refs = [_ref("tw_index_daily", "close", entity)]
    if close.empty:
        return None, "tw_index_daily.close_missing", refs
    if not (close > 0).all():
        return None, "tw_index_daily.close_not_positive", refs
    if len(close) < config.min_training_sessions:
        return None, f"tw_index_daily.close_history_short:{len(close)}<{config.min_training_sessions}", refs
    returns = np.log(close).diff().dropna()
    return returns, "", refs


def build_volatility_inputs(observations: Sequence[Mapping[str, Any]], *, decision_time: Any, horizon: str,
                            config: RegimeConfig | None = None, entity: str = TAIEX_ENTITY) -> AxisInputs:
    """Volatility axis: TAIEX returns (the Taiwan VIX joins only when archived history exists)."""
    config = config or RegimeConfig()
    returns, reason, refs = index_returns(observations, decision_time=decision_time, config=config, entity=entity)
    if returns is None:
        return _unavailable("volatility", horizon, reason, refs)
    window = int(config.volatility_return_window[horizon])
    if window > 1:
        # Non-overlapping window returns, scaled to a daily unit; anchored from the first row so
        # the sampling grid never depends on the decision date.
        grouped = returns.groupby(np.arange(len(returns)) // window)
        sampled = grouped.sum()[grouped.size() == window] / math.sqrt(window)
        ends = returns.index[np.arange(window - 1, len(returns), window)][: len(sampled)]
        series = pd.Series(sampled.to_numpy(), index=ends)
    else:
        series = returns
    frame = pd.DataFrame({"return_pct": series * 100.0})
    return AxisInputs("volatility", horizon, True, frame=frame, returns=returns, input_refs=tuple(refs),
                      sessions=len(frame))


def _market_series(observations, dataset, field_name, decision_time, refs, entity=None):
    series = read_series(observations, dataset, field_name, decision_time=decision_time, entity=entity)
    refs.append(_ref(dataset, field_name, entity))
    return series


def _breadth_ratio(observations, decision_time, refs, group="whole_market") -> pd.Series:
    adv = _market_series(observations, "tw_market_breadth_daily", f"{group}_advancers", decision_time, refs, "TW")
    dec = _market_series(observations, "tw_market_breadth_daily", f"{group}_decliners", decision_time, refs, "TW")
    if adv.empty or dec.empty:
        return pd.Series(dtype=float)
    both = pd.concat([adv, dec], axis=1, join="inner").dropna()
    total = both.sum(axis=1)
    return ((both.iloc[:, 0] - both.iloc[:, 1]) / total.replace(0.0, np.nan)).dropna()


def build_trend_inputs(observations: Sequence[Mapping[str, Any]], *, decision_time: Any, horizon: str,
                       config: RegimeConfig | None = None, entity: str = TAIEX_ENTITY) -> AxisInputs:
    """Trend axis: TAIEX returns at the horizon's half-life, downside deviation, and market breadth."""
    config = config or RegimeConfig()
    returns, reason, refs = index_returns(observations, decision_time=decision_time, config=config, entity=entity)
    if returns is None:
        return _unavailable("trend", horizon, reason, refs)
    refs = list(refs)
    breadth = _breadth_ratio(observations, decision_time, refs)
    if breadth.empty:
        return _unavailable("trend", horizon, "tw_market_breadth_daily.history_missing", refs)
    if len(breadth) < config.min_input_sessions:
        return _unavailable("trend", horizon, f"tw_market_breadth_daily.history_short:{len(breadth)}<{config.min_input_sessions}", refs)
    half = int(config.half_life_sessions[horizon])
    ewm_ret = returns.ewm(halflife=half, min_periods=half).mean()
    downside = _ewm_downside_deviation(returns, half)
    sortino = ewm_ret / downside.replace(0.0, np.nan)
    breadth_ewm = breadth.ewm(halflife=half, min_periods=half).mean()
    frame = pd.concat({"ewm_return": ewm_ret * 100.0, "downside_deviation": downside * 100.0,
                       "sortino": sortino, "breadth": breadth_ewm}, axis=1, join="inner").dropna()
    if len(frame) < config.min_input_sessions:
        return _unavailable("trend", horizon, f"trend_frame_short:{len(frame)}<{config.min_input_sessions}", refs)
    return AxisInputs("trend", horizon, True, frame=frame, returns=returns.reindex(frame.index),
                      input_refs=tuple(sorted(set(refs))), sessions=len(frame))


# Risk appetite inputs: (key, dataset, field, entity). Every one is required (R7: a missing input
# makes the axis unavailable); the catalogue names are the governed ones.
RISK_APPETITE_INPUTS: tuple[tuple[str, str, str, str | None], ...] = (
    ("foreign_net", "tw_market_institutional_flows", "foreign_net_shares", "TW"),
    ("trust_net", "tw_market_institutional_flows", "trust_net_shares", "TW"),
    ("dealer_net", "tw_market_institutional_flows", "dealer_net_shares", "TW"),
    ("margin_balance", "tw_market_margin_balances", "margin_balance", "TW"),
    ("tx_foreign_oi", "taifex_market_positions", "tx_foreign_net_open_interest", "TW"),
    ("put_call_oi", "taifex_market_put_call", "open_interest_ratio", "TW"),
)


def build_risk_appetite_inputs(observations: Sequence[Mapping[str, Any]], *, decision_time: Any, horizon: str,
                               config: RegimeConfig | None = None, entity: str = TAIEX_ENTITY) -> AxisInputs:
    """Risk-appetite axis: institutional flows, margin change, TAIFEX positions, put/call and breadth."""
    config = config or RegimeConfig()
    returns, reason, refs = index_returns(observations, decision_time=decision_time, config=config, entity=entity)
    if returns is None:
        return _unavailable("risk_appetite", horizon, reason, refs)
    refs = list(refs)
    half = int(config.half_life_sessions[horizon])
    window = max(half * 4, 40)
    parts: dict[str, pd.Series] = {}
    missing: list[str] = []
    for key, dataset, field_name, ent in RISK_APPETITE_INPUTS:
        series = _market_series(observations, dataset, field_name, decision_time, refs, ent)
        if series.empty:
            missing.append(f"{dataset}.{field_name}")
        elif len(series) < config.min_input_sessions:
            missing.append(f"{dataset}.{field_name}:short:{len(series)}<{config.min_input_sessions}")
        else:
            parts[key] = series
    breadth = _breadth_ratio(observations, decision_time, refs)
    if len(breadth) < config.min_input_sessions:
        missing.append("tw_market_breadth_daily.history")
    else:
        parts["breadth"] = breadth
    if missing:
        return _unavailable("risk_appetite", horizon, "inputs_missing:" + ",".join(missing), refs)
    flows = pd.concat([parts["foreign_net"], parts["trust_net"], parts["dealer_net"]], axis=1, join="inner").sum(axis=1)
    margin_change = parts["margin_balance"].pct_change()
    frame = pd.concat({
        "flow_z": _zscore_trailing(flows, window).ewm(halflife=half, min_periods=half).mean(),
        "margin_change": margin_change.ewm(halflife=half, min_periods=half).mean() * 100.0,
        "tx_foreign_oi_z": _zscore_trailing(parts["tx_foreign_oi"], window).ewm(halflife=half, min_periods=half).mean(),
        "put_call_z": _zscore_trailing(parts["put_call_oi"], window).ewm(halflife=half, min_periods=half).mean(),
        "breadth": parts["breadth"].ewm(halflife=half, min_periods=half).mean(),
    }, axis=1, join="inner").replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < config.min_input_sessions:
        return _unavailable("risk_appetite", horizon, f"risk_appetite_frame_short:{len(frame)}<{config.min_input_sessions}", refs)
    return AxisInputs("risk_appetite", horizon, True, frame=frame, returns=returns.reindex(frame.index),
                      input_refs=tuple(sorted(set(refs))), sessions=len(frame))


def macro_state(monitoring_score: float, leading_index_change: float, config: RegimeConfig | None = None) -> str:
    """Rule over the NDC monitoring score and the leading index trend (R6). No model, no language model.

    - expansion: score at or above the expansion level and the leading index not falling
    - contraction: score at or below the contraction level
    - slowdown: everything else (including a high score with a falling leading index)
    """
    config = config or RegimeConfig()
    if monitoring_score <= config.macro_contraction_score:
        return "contraction"
    if monitoring_score >= config.macro_expansion_score and leading_index_change >= 0:
        return "expansion"
    return "slowdown"


@dataclass
class MacroSession:
    month: str
    state: str
    monitoring_score: float
    leading_index: float
    leading_index_change: float


def build_macro_inputs(observations: Sequence[Mapping[str, Any]], *, decision_time: Any,
                       config: RegimeConfig | None = None) -> tuple[list[MacroSession], AxisInputs]:
    """Macro axis: monthly rules. Returns the per-month states and the availability record."""
    config = config or RegimeConfig()
    refs = [_ref("tw_macro_ndc", "monitoring_score"), _ref("tw_macro_ndc", "leading_index")]
    score = read_series(observations, "tw_macro_ndc", "monitoring_score", decision_time=decision_time)
    leading = read_series(observations, "tw_macro_ndc", "leading_index", decision_time=decision_time)
    if score.empty or leading.empty:
        return [], _unavailable("macro", None, "tw_macro_ndc.history_missing", refs)
    both = pd.concat([score.rename("score"), leading.rename("leading")], axis=1, join="inner").dropna()
    both["change"] = both["leading"].diff()
    both = both.dropna()
    if both.empty:
        return [], _unavailable("macro", None, "tw_macro_ndc.history_short", refs)
    sessions = [MacroSession(idx.strftime("%Y-%m"), macro_state(float(r.score), float(r.change), config),
                             float(r.score), float(r.leading), float(r.change)) for idx, r in both.iterrows()]
    return sessions, AxisInputs("macro", None, True, input_refs=tuple(refs), sessions=len(sessions))


BUILDERS = {"volatility": build_volatility_inputs, "trend": build_trend_inputs,
            "risk_appetite": build_risk_appetite_inputs}
