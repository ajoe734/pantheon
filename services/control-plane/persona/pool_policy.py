"""Paper pool policy and virtual-book calculations; missing observations stay unknown."""
from __future__ import annotations

import json
import math
import statistics
from datetime import date
from typing import Any, Mapping
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
    # COORDINATOR PROPOSAL (2026-11-15), reviewer to confirm: the design names the
    # trial cap and trial_history_days but not the promotion condition. A trial
    # becomes active at the first re-weight when its own virtual book has at least
    # trial_promotion_min_days and its own drawdown is below trial_promotion_max_drawdown
    # and its approved baseline still resolves.
    trial_promotion_min_days: int = Field(ge=2)
    trial_promotion_max_drawdown: float = Field(gt=0, lt=1)
    # COORDINATOR PROPOSAL: a published weight must move by more than this to republish.
    publish_weight_tolerance: float = Field(ge=0, lt=1)
    coordinator_proposals: list[str]

    @model_validator(mode="after")
    def consistent(self):
        if self.schema_version != "persona_pool_policy.v1" or self.halve_drawdown >= self.remove_drawdown:
            raise ValueError("invalid pool policy schema or drawdown ordering")
        if self.trial_promotion_min_days < self.trial_history_days or self.trial_promotion_max_drawdown > self.halve_drawdown:
            raise ValueError("trial promotion must not be looser than the trial history and halving rules")
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
    if parsed != sorted(set(parsed)) or any(not finite(v) or v < -1 for v in values):
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


ADMISSION_MISSING = "envelope_member_admission_missing"


def admission_only(change: Mapping[str, Any]) -> bool:
    """True when the only reason to publish is carrying member joined_at into the running envelope."""
    reasons = change.get("reasons") or []
    return bool(reasons) and all(r.startswith(ADMISSION_MISSING + ":") for r in reasons)


def trial_publication(member: Mapping[str, Any], trial_risk_cap: float) -> tuple[float, bool]:
    """(risk_weight, virtual_only) a pool member is published with.

    Reading of design sections 4.2 / 5.3 implemented here: a trial that has no weight yet is
    virtual only (observed on paper, no risk budget); a trial that has been given weight by a
    re-weight trades with at most the trial cap of the risk budget until it is promoted to
    active. Active members publish their own weight unchanged.
    """
    if member["stage"] != "trial":
        return member["risk_weight"], False
    weight = min(float(member["risk_weight"]), trial_risk_cap)
    return (weight, False) if weight > 0 else (0, True)


def last_published(history: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The newest publish record that confirmed the runtime binding moved."""
    done = [e for e in ((history or {}).get("published") or {}).values() if e.get("status") == "published"]
    return max(done, key=lambda e: e["pool_version"]) if done else None


def latest_publication(history: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Newest accepted dispatch, including one whose owner readback is outstanding."""
    entries = [e for e in ((history or {}).get("published") or {}).values()
               if e.get("status") in ("published", "dispatched")]
    return max(entries, key=lambda e: int(e["pool_version"])) if entries else None


def publication_change(published: Mapping[str, Any] | None, pool: Mapping[str, Any], policy) -> dict[str, Any]:
    """Would publishing ``pool`` change what runs? A re-weight inside tolerance changes nothing.

    Changed means: a different set of live members (membership), a member whose stage differs,
    or an effective published weight that moved by more than ``publish_weight_tolerance``.
    No publish record at all counts as a membership change (nothing proves what runs).

    Also changed: a running pinned member whose ``joined_at`` is missing or differs from the pool
    version member (``envelope_member_admission_missing:<ref>``). Envelopes published before
    joined_at was carried pin no admission time, so the member virtual book never learns
    admitted_at. Republishing the same membership, stages and weights carries it once; after
    that the pin has it and this reason no longer fires. It is never a membership change.
    """
    live = [m for m in pool.get("members", []) if m["stage"] != "removed"]
    now = {m["artifact_ref"]: (m["stage"], *trial_publication(m, policy.trial_risk_cap)) for m in live}
    joined = {m["artifact_ref"]: m.get("joined_at") for m in live}
    if published is None:
        return {"changed": True, "membership_changed": True, "reasons": ["no_publication_record"]}
    before = {p["artifact_ref"]: p for p in published.get("members") or []}
    reasons = []
    if set(now) != set(before):
        reasons.append("live_membership_changed")
    for ref, (stage, weight, virtual) in sorted(now.items()):
        pin = before.get(ref)
        if pin is None:
            continue
        if pin.get("stage") != stage:
            reasons.append(f"stage_changed:{ref}")
        if bool(pin.get("virtual_only")) != virtual or abs(float(pin["risk_weight"]) - float(weight)) > policy.publish_weight_tolerance + 1e-12:
            reasons.append(f"weight_changed:{ref}")
        if joined[ref] and pin.get("joined_at") != joined[ref]:
            reasons.append(f"{ADMISSION_MISSING}:{ref}")
    return {"changed": bool(reasons), "membership_changed": "live_membership_changed" in reasons,
            "reasons": reasons, "published_pool_version": published.get("pool_version")}


def promotion_verdict(member, stat, policy):
    """Pure promotion condition (baseline resolution is checked by the owner that can read the registry)."""
    if member["stage"] != "trial":
        return {"eligible": False, "reason": "not_a_trial"}
    if stat["days"] < policy.trial_promotion_min_days:
        return {"eligible": False, "reason": f"trial_history_{stat['days']}_of_{policy.trial_promotion_min_days}_days"}
    if stat["drawdown"] >= policy.trial_promotion_max_drawdown - 1e-12:
        return {"eligible": False, "reason": "trial_drawdown_not_below_promotion_threshold"}
    return {"eligible": True, "reason": "trial_history_and_drawdown_met"}


def reweight(members, books, policy, now, previous_statistics=None):
    result = [dict(m) for m in members]
    live = [m for m in result if m["stage"] != "removed"]
    stats = {m["artifact_ref"]: member_statistics(m, books.get(m["artifact_ref"]), policy) for m in live}
    previous_statistics = previous_statistics or {}
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
        stat["drawdown_cap"] = None
        if stat["drawdown"] + 1e-12 >= policy.halve_drawdown:
            prior_cap = previous_statistics.get(ref, {}).get("drawdown_cap")
            stat["drawdown_cap"] = min(prior_cap, member["risk_weight"]) if finite(prior_cap) else member["risk_weight"] * 0.5
            weight = min(weight, stat["drawdown_cap"])
        if member["stage"] == "trial" and stat["days"] < policy.trial_history_days:
            weight = min(weight, policy.trial_risk_cap)
        member["risk_weight"] = weight
    # No redistribution after a haircut or trial cap: unused budget stays idle.
    return result, stats
