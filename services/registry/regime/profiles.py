"""Per-strategy regime profile (design R8): primary axis and horizon.

The profile comes from the StrategySpec regime section, else the alpha template's applicable
regimes, else a family default. Macro is a measurement-only axis and is never primary: a spec
that names macro falls back to the family default (the macro state is still recorded).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

PRIMARY_AXES = ("volatility", "trend", "risk_appetite")
HORIZONS = ("short", "medium")

# Axis state -> regime_class (design R8).
STATE_TO_CLASS: dict[str, dict[str, str]] = {
    "volatility": {"high": "high_volatility", "low": "low_volatility"},
    "trend": {"up": "trending_up", "down": "trending_down", "mean_reverting": "mean_reverting"},
    "risk_appetite": {"on": "risk_on", "off": "risk_off"},
}
CLASS_TO_AXIS = {cls: axis for axis, m in STATE_TO_CLASS.items() for cls in m.values()}

# Family defaults (coordinator proposal, calibrated later): a trend axis at the medium horizon.
DEFAULT_PRIMARY_AXIS = "trend"
DEFAULT_HORIZON = "medium"
FAMILY_DEFAULTS: dict[str, tuple[str, str]] = {
    "mean_reversion": ("volatility", "short"),
    "momentum": ("trend", "medium"),
    "trend_following": ("trend", "medium"),
    "cross_sectional": ("trend", "medium"),
    "defensive": ("risk_appetite", "medium"),
}
SHORT_HOLDING_SESSIONS = 20
# Where a StrategySpec's regime section can live on its registry entry: the entry metadata, or the
# spec's own open ``metadata`` object (the spec schema allows no other extra keys at its root).
_REGIME_PATHS_AXIS = (("regime", "primary_axis"), ("strategy_spec", "regime", "primary_axis"),
                      ("strategy_spec", "metadata", "regime", "primary_axis"))
_REGIME_PATHS_HORIZON = (("regime", "horizon"), ("strategy_spec", "regime", "horizon"),
                         ("strategy_spec", "metadata", "regime", "horizon"))


@dataclass(frozen=True)
class RegimeProfile:
    primary_axis: str
    horizon: str
    source: str  # spec_regime | alpha_template | family_default | default
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"primary_axis": self.primary_axis, "horizon": self.horizon, "source": self.source, "note": self.note}


def _dig(meta: Mapping[str, Any], *path: str) -> Any:
    cur: Any = meta
    for key in path:
        if not isinstance(cur, Mapping) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _horizon_from(meta: Mapping[str, Any], fallback: str) -> str:
    for path in _REGIME_PATHS_HORIZON:
        value = _dig(meta, *path)
        if value in HORIZONS:
            return str(value)
    for path in (("holding_horizon_sessions",), ("strategy_spec", "holding_horizon_sessions")):
        value = _dig(meta, *path)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return "short" if value <= SHORT_HOLDING_SESSIONS else "medium"
    return fallback


def _family_default(meta: Mapping[str, Any]) -> tuple[str, str, str]:
    family = _dig(meta, "strategy_spec", "family") or meta.get("family") or meta.get("strategy_family")
    if isinstance(family, str) and family in FAMILY_DEFAULTS:
        axis, horizon = FAMILY_DEFAULTS[family]
        return axis, horizon, "family_default"
    return DEFAULT_PRIMARY_AXIS, DEFAULT_HORIZON, "default"


def profile_for(spec_metadata: Mapping[str, Any] | None) -> RegimeProfile:
    meta: Mapping[str, Any] = spec_metadata or {}
    named = next((v for v in (_dig(meta, *p) for p in _REGIME_PATHS_AXIS) if v), None)
    if isinstance(named, str) and named in PRIMARY_AXES:
        return RegimeProfile(named, _horizon_from(meta, DEFAULT_HORIZON), "spec_regime")
    axis, horizon, source = _family_default(meta)
    note = ""
    if named == "macro":
        note = "macro_primary_fallback_to_default"
    elif named is not None:
        note = f"unknown_primary_axis_ignored:{named}"
    else:
        templates = _dig(meta, "applicable_regimes") or _dig(meta, "strategy_spec", "applicable_regimes")
        axes = {CLASS_TO_AXIS[r] for r in templates if r in CLASS_TO_AXIS} if isinstance(templates, list) else set()
        if len(axes) == 1:
            return RegimeProfile(next(iter(axes)), _horizon_from(meta, horizon), "alpha_template")
    return RegimeProfile(axis, _horizon_from(meta, horizon), source, note)
