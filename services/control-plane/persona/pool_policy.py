"""Paper pool policy and virtual-book calculations; missing observations stay unknown."""
from __future__ import annotations

import json
import math
import statistics
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PoolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    schema_version: str
    health_window_days: int = Field(ge=2)
    low_utilisation: float = Field(gt=0, le=1)
    minimum_effective_bets: float = Field(ge=1)
    correlation_cutoff: float = Field(gt=-1, le=1)
    trial_risk_cap: float = Field(gt=0, le=1)
    trial_history_days: int = Field(ge=2)
    halve_drawdown: float = Field(gt=0, lt=1)
    remove_drawdown: float = Field(gt=0, lt=1)
    member_ceiling: int = Field(ge=1)
    tilt_min: float = Field(gt=0, le=1)
    tilt_max: float = Field(ge=1)
    tilt_sharpe_scale: float = Field(ge=0)
    base_sharpe_threshold: float = Field(ge=0)
    trial_penalty_scale: float = Field(ge=0)
    minimum_sharpe_improvement: float = Field(ge=0)
    periods_per_year: int = Field(ge=1)
    minimum_oos_days: int = Field(ge=2)
    in_sample_fraction: float = Field(gt=0, lt=1)
    budget_full: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def consistent(self):
        if self.schema_version != "persona_pool_policy.v1" or self.halve_drawdown >= self.remove_drawdown:
            raise ValueError("invalid pool policy schema or drawdown ordering")
        return self


def load_policy(path=None):
    return PoolPolicy.model_validate(json.loads(Path(path or Path(__file__).with_name("config") / "pool_policy.json").read_text()))


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def dated_series(book, checksum=None):
    """Require unique ordered trading dates and provenance, never align by array index."""
    if not isinstance(book, dict) or not book.get("source_ref"):
        raise ValueError("virtual_book_source_missing")
    if checksum is not None and book.get("checksum") != checksum:
        raise ValueError("virtual_book_checksum_mismatch")
    days, values = book.get("dates"), book.get("returns")
    if not isinstance(days, list) or not isinstance(values, list) or len(days) != len(values) or len(days) < 2:
        raise ValueError("virtual_book_history_missing")
    try:
        parsed = [date.fromisoformat(day) for day in days]
    except (ValueError, TypeError):
        raise ValueError("virtual_book_dates_invalid") from None
    if parsed != sorted(set(parsed)) or any(not finite(v) or v <= -1 for v in values):
        raise ValueError("virtual_book_observations_invalid")
    if book.get("basis") != "underlying_exposure_fraction_net_taiwan_costs_equal_notional":
        raise ValueError("virtual_book_return_basis_unknown")
    return dict(zip(days, values))


def sharpe(values, periods):
    deviation = statistics.stdev(values) if len(values) > 1 else 0
    if deviation <= 0:
        raise ValueError("insufficient_or_constant_returns")
    return statistics.mean(values) / deviation * math.sqrt(periods)


def correlation(one, two):
    if len(one) != len(two) or len(one) < 2:
        raise ValueError("return_windows_not_aligned")
    if statistics.stdev(one) <= 0 or statistics.stdev(two) <= 0:
        raise ValueError("correlation_undefined_constant_returns")
    return statistics.correlation(one, two)


def _known(value):
    return {"status": "known", "value": value, "reason": None}


def _unknown(reason):
    return {"status": "unknown", "value": None, "reason": reason}


def pool_health(members, books, observations, policy):
    window = policy.health_window_days
    result = {"window_trading_days": window, "window_start": None, "window_end": None}
    # A stated ordered observation calendar is required even when one measure is missing.
    rows = observations[-window:] if isinstance(observations, list) else []
    try:
        dates = [r["date"] for r in rows]
        parsed = [date.fromisoformat(d) for d in dates]
        if len(rows) != window or parsed != sorted(set(parsed)) or not all(r.get("source_ref") for r in rows):
            raise ValueError("health_window_missing_or_invalid")
        result.update(window_start=dates[0], window_end=dates[-1])
    except (KeyError, TypeError, ValueError):
        return {**result, **{key: _unknown("health_window_missing_or_invalid") for key in
                ("gross_utilisation", "drawdown_utilisation", "idle_day_share", "effective_bets")},
                "demand_reasons": ["health_inputs_unknown"]}
    for key, numerator, denominator in (("gross_utilisation", "gross_notional", "gross_limit"),
                                       ("drawdown_utilisation", "drawdown", "drawdown_limit")):
        if all(finite(r.get(numerator)) and r[numerator] >= 0 and finite(r.get(denominator))
               and r[denominator] > 0 for r in rows):
            result[key] = _known(statistics.mean(r[numerator] / r[denominator] for r in rows))
        else:
            result[key] = _unknown(f"{numerator}_or_{denominator}_missing")
    result["idle_day_share"] = (_known(sum(r["gross_notional"] == 0 for r in rows) / window)
        if all(finite(r.get("gross_notional")) and r["gross_notional"] >= 0 for r in rows)
        else _unknown("gross_notional_missing"))
    live = [m for m in members if m["stage"] != "removed"]
    try:
        if not live:
            raise ValueError("no_live_members")
        series = [dated_series(books.get(m["artifact_ref"]), m["checksum"]) for m in live]
        if any(not set(dates) <= set(s) for s in series):
            raise ValueError("virtual_book_window_missing")
        arrays = [[s[d] for d in dates] for s in series]
        # Spectral participation ratio: (tr C)^2 / tr(C^2). Equals n for
        # independent members and 1 for identical (or perfectly opposite) bets.
        # This is explicitly a correlation-spectrum measure, not entropy ENB.
        mass = sum(correlation(a, b) ** 2 for a in arrays for b in arrays)
        result["effective_bets"] = {**_known(len(live) ** 2 / mass),
                                    "method": "correlation_spectral_participation_ratio"}
    except ValueError as exc:
        result["effective_bets"] = _unknown(str(exc))
    reasons = []
    if result["gross_utilisation"]["status"] == "known" and result["gross_utilisation"]["value"] < policy.low_utilisation:
        reasons.append("low_risk_budget_utilisation")
    if len(live) >= 2 and result["effective_bets"]["status"] == "known" and result["effective_bets"]["value"] < policy.minimum_effective_bets:
        reasons.append("too_few_independent_bets")
    if any(result[key]["status"] == "unknown" for key in ("gross_utilisation", "drawdown_utilisation", "idle_day_share", "effective_bets")):
        reasons.append("health_inputs_unknown")
    return {**result, "demand_reasons": reasons}


def member_statistics(member, book, policy):
    values = list(dated_series(book, member["checksum"]).values())
    # The owner supplies the complete since-inception book; a rolling slice
    # cannot establish a member's own high-water mark.
    if book.get("since_inception") is not True:
        raise ValueError("virtual_book_high_water_history_missing")
    equity = peak = 1.0
    for value in values:
        equity *= 1 + value
        peak = max(peak, equity)
    return {"days": len(values), "volatility": statistics.stdev(values),
            "sharpe": sharpe(values, policy.periods_per_year), "drawdown": max(0, 1 - equity / peak)}


def reweight(members, books, policy, now):
    result = [dict(m) for m in members]
    live = [m for m in result if m["stage"] != "removed"]
    stats = {m["artifact_ref"]: member_statistics(m, books.get(m["artifact_ref"]), policy) for m in live}
    scores = {}
    for member in live:
        stat = stats[member["artifact_ref"]]
        if stat["drawdown"] + 1e-12 >= policy.remove_drawdown:
            member.update(stage="removed", risk_weight=0.0, removed_at=now, removal_reason="own_high_water_drawdown")
            continue
        tilt = 1.0
        if stat["days"] >= policy.trial_history_days:
            tilt = min(policy.tilt_max, max(policy.tilt_min, 1 + stat["sharpe"] * policy.tilt_sharpe_scale))
        scores[member["artifact_ref"]] = tilt / stat["volatility"]
    total = sum(scores.values())
    for member in live:
        ref = member["artifact_ref"]
        if ref not in scores:
            continue
        stat = stats[ref]
        weight = scores[ref] / total
        if stat["drawdown"] + 1e-12 >= policy.halve_drawdown:
            weight *= 0.5
        if member["stage"] == "trial" and stat["days"] < policy.trial_history_days:
            weight = min(weight, policy.trial_risk_cap)
        member["risk_weight"] = weight
    # No redistribution after a haircut or trial cap: unused budget stays idle.
    return result, stats
