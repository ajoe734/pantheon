"""Deterministic 5-component outlier decomposition, bootstrap luck intervals, and regime context (§12.5).

Decomposes each outlier's return into five additive components:
  - ``selection``: instrument and direction choice at baseline sizing from cutoff to window close
  - ``entry_timing``: entry execution price vs decision-cutoff price at baseline sizing
  - ``sizing``: actual position sizing/exposure weight vs baseline weight
  - ``exit``: actual exit price vs standard observation-window close at baseline sizing
  - ``costs``: transaction costs (commission, statutory tax, slippage)

Every number is computed deterministically in pure Python math — no language model
is ever invoked.
"""
from __future__ import annotations

import hashlib
import math
import random
import statistics
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

COMPONENT_NAMES: Tuple[str, ...] = (
    "selection",
    "entry_timing",
    "sizing",
    "exit",
    "costs",
)

NO_LANGUAGE_MODEL_PROOF = "deterministic_python_math_only"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _finite(value: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return num if math.isfinite(num) else default


def _finite_pos(value: Any) -> Optional[float]:
    num = _finite(value)
    return num if num is not None and num > 0.0 else None


def decompose_observation(
    obs: Mapping[str, Any],
    *,
    benchmark_return: Optional[float] = None,
    baseline_weight: float = 1.0,
) -> Dict[str, float]:
    """Decompose a single decision/trade observation into the 5 additive components.

    Satisfies the exact telescoping identity:
      selection + entry_timing + exit + sizing + costs == net_return - baseline_weight * benchmark_return
    """
    existing = obs.get("decomposition")
    if isinstance(existing, Mapping) and all(k in existing for k in COMPONENT_NAMES):
        return {
            k: round(float(_finite(existing.get(k), 0.0) or 0.0), 6)
            for k in COMPONENT_NAMES
        }

    # Check if observation has explicit per-component fields
    if all(k in obs for k in COMPONENT_NAMES):
        return {
            k: round(float(_finite(obs.get(k), 0.0) or 0.0), 6)
            for k in COMPONENT_NAMES
        }

    outcome = obs.get("outcome") if isinstance(obs.get("outcome"), Mapping) else obs
    bench_ret = _finite(
        benchmark_return if benchmark_return is not None else outcome.get("benchmark_return_pct"),
        0.0,
    ) or 0.0
    w_base = max(0.0, float(_finite(outcome.get("baseline_weight"), baseline_weight) or baseline_weight))

    direction_raw = outcome.get("direction")
    if direction_raw in ("LONG", "long", "enter", "buy", "+1", 1):
        direction = 1.0
    elif direction_raw in ("SHORT", "short", "exit", "sell", "-1", -1):
        direction = -1.0
    elif direction_raw in ("FLAT", "flat", "no_trade", "hold", "0", 0):
        direction = 0.0
    else:
        exposure_val = _finite(outcome.get("exposure"), None)
        if exposure_val is not None:
            direction = 1.0 if exposure_val > 0 else (-1.0 if exposure_val < 0 else 0.0)
        else:
            ret_hint = _finite(outcome.get("return_pct") or outcome.get("net_return") or outcome.get("return"), 0.0) or 0.0
            direction = 0.0 if ret_hint == 0.0 and outcome.get("action") == "no_trade" else 1.0

    weight_val = _finite(outcome.get("weight"))
    if weight_val is None:
        exp_val = _finite(outcome.get("exposure"))
        weight_val = abs(exp_val) if exp_val is not None else (0.0 if direction == 0.0 else w_base)
    weight = max(0.0, float(weight_val))

    p_entry = _finite_pos(outcome.get("entry_price") or outcome.get("fill_price") or outcome.get("open"))
    p_dec = _finite_pos(outcome.get("decision_price") or outcome.get("prev_close")) or p_entry
    p_exit = _finite_pos(outcome.get("exit_price") or outcome.get("close") or outcome.get("price"))
    p_bench_exit = _finite_pos(outcome.get("benchmark_exit_price") or outcome.get("window_close")) or p_exit

    cost_mag = abs(
        _finite(
            outcome.get("cost_pct")
            if "cost_pct" in outcome
            else outcome.get("cost", outcome.get("costs", 0.0)),
            0.0,
        )
        or 0.0
    )
    costs_comp = -cost_mag

    if p_entry is not None and p_exit is not None and p_dec is not None and p_bench_exit is not None:
        if direction == 0.0 or weight == 0.0:
            # Declared no-trade or flat position: selection captures counterfactual benchmark relative move
            selection_comp = -w_base * bench_ret
            entry_timing_comp = 0.0
            exit_comp = 0.0
            sizing_comp = 0.0
        else:
            # Exact telescoping decomposition:
            # unit_dec_to_bench + unit_entry_timing + unit_exit == direction * (p_exit / p_entry - 1)
            unit_dec_to_bench = direction * ((p_bench_exit / p_dec) - 1.0)
            unit_entry_timing = direction * ((p_bench_exit / p_entry) - (p_bench_exit / p_dec))
            unit_exit = direction * ((p_exit - p_bench_exit) / p_entry)
            unit_gross = direction * ((p_exit / p_entry) - 1.0)

            selection_comp = w_base * (unit_dec_to_bench - bench_ret)
            entry_timing_comp = w_base * unit_entry_timing
            exit_comp = w_base * unit_exit
            sizing_comp = (weight - w_base) * unit_gross
    else:
        # Fallback when only return series / gross & net returns are present
        net_ret = _finite(
            outcome.get("return_pct")
            if "return_pct" in outcome
            else outcome.get("net_return", outcome.get("return", 0.0)),
            0.0,
        ) or 0.0
        gross_ret = _finite(outcome.get("gross_return_pct") or outcome.get("gross_return"), net_ret + cost_mag) or (
            net_ret + cost_mag
        )
        if "cost_pct" not in outcome and "cost" not in outcome and "costs" not in outcome:
            costs_comp = round(net_ret - gross_ret, 6)
        unit_gross = (gross_ret / weight) if weight > 0 else gross_ret
        entry_timing_comp = _finite(outcome.get("entry_timing"), 0.0) or 0.0
        exit_comp = _finite(outcome.get("exit"), 0.0) or 0.0
        sizing_comp = (weight - w_base) * unit_gross if weight > 0 else 0.0
        selection_comp = gross_ret - sizing_comp - entry_timing_comp - exit_comp - (w_base * bench_ret)

    return {
        "selection": round(selection_comp, 6),
        "entry_timing": round(entry_timing_comp, 6),
        "sizing": round(sizing_comp, 6),
        "exit": round(exit_comp, 6),
        "costs": round(costs_comp, 6),
    }


def bootstrap_luck_interval(
    values: Sequence[float],
    *,
    resamples: int = 1000,
    confidence_level: float = 0.90,
    seed: int = 20261116,
    seed_key: str = "",
) -> Dict[str, Any]:
    """Compute a deterministic percentile bootstrap confidence interval for the sample mean."""
    clean_vals = [float(v) for v in values if _finite(v) is not None]
    n = len(clean_vals)
    level = min(0.999, max(0.50, float(confidence_level)))
    n_resamples = max(1, int(resamples))

    if n == 0:
        return {
            "low": None,
            "high": None,
            "mean": None,
            "level": round(level, 4),
            "resamples": 0,
            "sample_size": 0,
            "zero_inside": True,
            "is_luck_candidate": True,
            "unavailable_reason": "empty_sample",
        }

    mean_val = math.fsum(clean_vals) / n
    if n == 1 or all(v == clean_vals[0] for v in clean_vals):
        val = round(clean_vals[0], 6)
        zero_in = val <= 0.0 <= val
        return {
            "low": val,
            "high": val,
            "mean": val,
            "level": round(level, 4),
            "resamples": n_resamples,
            "sample_size": n,
            "zero_inside": zero_in,
            "is_luck_candidate": zero_in or n < 2,
        }

    digest = hashlib.sha256(f"{seed}|{seed_key}|{n}".encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))

    boot_means: List[float] = []
    inv_n = 1.0 / n
    for _ in range(n_resamples):
        total = 0.0
        for _ in range(n):
            total += clean_vals[rng.randrange(n)]
        boot_means.append(total * inv_n)

    boot_means.sort()
    alpha_tail = (1.0 - level) / 2.0
    low_idx = max(0, min(n_resamples - 1, int(math.floor(alpha_tail * (n_resamples - 1)))))
    high_idx = max(0, min(n_resamples - 1, int(math.ceil((1.0 - alpha_tail) * (n_resamples - 1)))))
    low = round(boot_means[low_idx], 6)
    high = round(boot_means[high_idx], 6)
    zero_in = bool(low <= 0.0 <= high)

    return {
        "low": low,
        "high": high,
        "mean": round(mean_val, 6),
        "level": round(level, 4),
        "resamples": n_resamples,
        "sample_size": n,
        "zero_inside": zero_in,
        "is_luck_candidate": zero_in,
    }


def resolve_regime_context(
    regime_state: Optional[Mapping[str, Any]],
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Extract governed RegimeState reference and context when one exists."""
    if not isinstance(regime_state, Mapping) or not regime_state:
        return None, {
            "available": False,
            "unavailable_reason": "no_governed_regime_state",
        }

    if regime_state.get("available") is False or regime_state.get("status") == "unavailable":
        return None, {
            "available": False,
            "unavailable_reason": _clean(
                regime_state.get("reason")
                or regime_state.get("unavailable_reason")
                or "regime_state_unavailable"
            ),
        }

    regime_id = _clean(regime_state.get("regime_id") or regime_state.get("id"))
    raw_ref = _clean(regime_state.get("regime_state_ref") or regime_state.get("regime_ref"))
    if not regime_id and not raw_ref:
        return None, {
            "available": False,
            "unavailable_reason": "no_governed_regime_state",
        }

    regime_ref = raw_ref or (
        regime_id if regime_id.startswith("regime-state://") else f"regime-state://{regime_id}"
    )
    if not regime_id:
        regime_id = regime_ref.removeprefix("regime-state://")

    axes = regime_state.get("axes") if isinstance(regime_state.get("axes"), Mapping) else {}
    macro_obj = regime_state.get("macro") if isinstance(regime_state.get("macro"), Mapping) else axes.get("macro")
    macro_state = (
        _clean(macro_obj.get("state"))
        if isinstance(macro_obj, Mapping)
        else _clean(regime_state.get("macro_state"))
    ) or None

    primary_axis = _clean(regime_state.get("primary_axis")) or None
    regime_class = _clean(
        regime_state.get("regime_class")
        or regime_state.get("regime_tag")
        or regime_state.get("primary_state")
        or regime_state.get("state")
    ) or None
    session = _clean(regime_state.get("session") or regime_state.get("as_of_session")) or None
    confidence = _finite(regime_state.get("confidence"))

    return regime_ref, {
        "available": True,
        "regime_id": regime_id,
        "regime_ref": regime_ref,
        "regime_class": regime_class,
        "primary_axis": primary_axis,
        "macro_state": macro_state,
        "session": session,
        "confidence": round(confidence, 4) if confidence is not None else None,
    }


def decompose_outlier(
    observations: Sequence[Mapping[str, Any]],
    *,
    subject_id: str,
    window_end: str = "",
    benchmark_return: float = 0.0,
    baseline_weight: float = 1.0,
    resamples: int = 1000,
    confidence_level: float = 0.90,
    seed: int = 20261116,
    regime_state: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Decompose an outlier's observations into 5 components with sample size, luck interval, and regime context."""
    per_obs = [
        decompose_observation(
            obs,
            benchmark_return=_finite(obs.get("benchmark_return_pct"), benchmark_return),
            baseline_weight=baseline_weight,
        )
        for obs in observations
        if isinstance(obs, Mapping)
    ]
    sample_size = len(per_obs)

    if sample_size == 0:
        decomposition = {k: 0.0 for k in COMPONENT_NAMES}
        total_series: List[float] = []
    else:
        # Mean per-decision contribution across the sample (total also available in component_attribution)
        decomposition = {
            k: round(statistics.fmean(item[k] for item in per_obs), 6)
            for k in COMPONENT_NAMES
        }
        total_series = [
            math.fsum(item[k] for k in COMPONENT_NAMES)
            for item in per_obs
        ]

    overall_luck = bootstrap_luck_interval(
        total_series,
        resamples=resamples,
        confidence_level=confidence_level,
        seed=seed,
        seed_key=f"{subject_id}|{window_end}|total",
    )

    component_intervals: Dict[str, Dict[str, Any]] = {}
    component_attribution: Dict[str, Dict[str, Any]] = {}
    for comp in COMPONENT_NAMES:
        comp_series = [item[comp] for item in per_obs]
        comp_luck = bootstrap_luck_interval(
            comp_series,
            resamples=resamples,
            confidence_level=confidence_level,
            seed=seed,
            seed_key=f"{subject_id}|{window_end}|{comp}",
        )
        component_intervals[comp] = comp_luck
        component_attribution[comp] = {
            "value": decomposition[comp],
            "total_contribution": round(math.fsum(comp_series), 6) if comp_series else 0.0,
            "sample_size": sample_size,
            "luck_interval": comp_luck,
        }

    overall_luck["components"] = component_intervals
    regime_ref, regime_context = resolve_regime_context(regime_state)

    return {
        "decomposition": decomposition,
        "component_attribution": component_attribution,
        "sample_size": sample_size,
        "luck_interval": overall_luck,
        "regime_ref": regime_ref,
        "regime_context": regime_context,
        "computation_mode": NO_LANGUAGE_MODEL_PROOF,
    }


__all__ = [
    "COMPONENT_NAMES",
    "NO_LANGUAGE_MODEL_PROOF",
    "bootstrap_luck_interval",
    "decompose_observation",
    "decompose_outlier",
    "resolve_regime_context",
]
