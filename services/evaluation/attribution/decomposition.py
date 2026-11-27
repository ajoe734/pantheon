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
            "label": "luck",
            "label_zh": "可能是運氣",
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
            "label": "luck" if zero_in else "significant",
            "label_zh": "可能是運氣" if zero_in else "非零區間",
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
        "label": "luck" if zero_in else "significant",
        "label_zh": "可能是運氣" if zero_in else "非零區間",
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


MIN_TRADER_DECISIONS_FOR_IMITATION = 30
MAX_CANDIDATES_PER_TRADER_STRATEGY = 3
LUCK_LABEL = "luck"
LUCK_LABEL_ZH = "可能是運氣"


def _normalize_imitation_action(action: Any) -> str:
    raw = _clean(action).lower()
    if raw in {"enter", "buy", "long", "+1"}:
        return "enter"
    if raw in {"exit", "sell", "short", "reduce", "trim", "-1"}:
        return "exit"
    if raw in {"hold", "modify", "adjust_risk"}:
        return "hold"
    if raw in {"no_trade", "flat", "reject", "rejection", "defer", "deferral", "abstain", "0"}:
        return "no_trade"
    return raw or "no_trade"


def _predict_from_candidate_model(
    snapshot: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    artifact = candidate.get("artifact") if isinstance(candidate.get("artifact"), Mapping) else {}
    # Support policy_weights / artifact.policy (2D linear_softmax from train_bc)
    policy = (
        candidate.get("policy_weights")
        if isinstance(candidate.get("policy_weights"), Mapping)
        else (
            candidate.get("policy")
            if isinstance(candidate.get("policy"), Mapping)
            else (artifact.get("policy") if isinstance(artifact.get("policy"), Mapping) else None)
        )
    )
    if isinstance(policy, Mapping) and isinstance(policy.get("weights"), Sequence) and policy.get("action_labels"):
        action_labels = [str(a).strip() for a in (policy.get("action_labels") or []) if str(a).strip()]
        p_weights = policy.get("weights") or []
        p_bias = policy.get("bias") or [0.0] * len(action_labels)
        conf_raw = snapshot.get("confidence")
        if isinstance(conf_raw, Mapping):
            conf_val = float(_finite(conf_raw.get("value"), 0.75) or 0.75)
        else:
            conf_val = float(_finite(conf_raw, 0.75) or 0.75)
        prob_raw = snapshot.get("probability")
        if isinstance(prob_raw, Mapping):
            prob_val = float(_finite(prob_raw.get("value"), 0.5) or 0.5)
        else:
            prob_val = float(_finite(prob_raw, 0.5) or 0.5)
        ev_raw = snapshot.get("expected_value") if isinstance(snapshot.get("expected_value"), Mapping) else {}
        net_val = float(_finite(ev_raw.get("net"), 0.0) or 0.0)
        down_val = float(_finite(ev_raw.get("downside"), 0.0) or 0.0)
        act_tok = _clean(snapshot.get("suggested_action") or snapshot.get("event_kind")).lower()
        act_code = 1.0 if act_tok in {"enter", "entry", "buy"} else (-1.0 if act_tok in {"exit", "sell", "reject"} else 0.0)
        obs = [conf_val, prob_val, net_val, down_val, act_code]
        logits_2d: Dict[str, float] = {}
        for label, row, b_val in zip(action_labels, p_weights, p_bias):
            if isinstance(row, Sequence):
                dot = sum(float(_finite(w, 0.0) or 0.0) * float(x) for w, x in zip(row, obs))
                logits_2d[label] = dot + float(_finite(b_val, 0.0) or 0.0)
        if logits_2d:
            max_l = max(logits_2d.values())
            exps_2d = {act: math.exp(max(-40.0, min(40.0, val - max_l))) for act, val in logits_2d.items()}
            tot_2d = sum(exps_2d.values()) or 1.0
            probs_2d = {act: round(val / tot_2d, 6) for act, val in exps_2d.items()}
            best_2d = sorted(probs_2d.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            return {
                "action": _normalize_imitation_action(best_2d),
                "confidence": round(float(probs_2d.get(best_2d, 0.5)), 4),
                "probabilities": probs_2d,
                "candidate_id": _clean(candidate.get("candidate_id") or candidate.get("id")) or None,
                "version_id": _clean(
                    candidate.get("version_id")
                    or candidate.get("candidate_version")
                    or artifact.get("version_id")
                    or candidate.get("candidate_id")
                ) or None,
            }

    model = (
        artifact.get("model")
        if isinstance(artifact.get("model"), Mapping)
        else (candidate.get("model") if isinstance(candidate.get("model"), Mapping) else {})
    )
    weights = model.get("weights") if isinstance(model.get("weights"), Mapping) else {}
    biases = model.get("biases") if isinstance(model.get("biases"), Mapping) else {}
    actions = list(model.get("actions") or ["no_trade", "hold", "enter", "exit"])
    if not weights and not biases:
        return None
    features_raw = snapshot.get("features") if isinstance(snapshot.get("features"), Mapping) else snapshot
    numeric_features: Dict[str, float] = {}
    for key, val in dict(features_raw or {}).items():
        num = _finite(val)
        if num is not None:
            numeric_features[str(key)] = float(num)
    if "confidence" not in numeric_features:
        numeric_features["confidence"] = float(_finite(snapshot.get("confidence"), 0.5) or 0.5)
    if "risk_score" not in numeric_features:
        numeric_features["risk_score"] = float(_finite(snapshot.get("risk_score"), 0.25) or 0.25)
    logits: Dict[str, float] = {}
    for action in actions:
        row = weights.get(action) if isinstance(weights.get(action), Mapping) else {}
        score = float(_finite(biases.get(action), 0.0) or 0.0)
        for feat_name, feat_val in numeric_features.items():
            w_val = _finite(row.get(feat_name))
            if w_val is not None:
                score += float(w_val) * feat_val
        logits[str(action)] = score
    if not logits:
        return None
    max_logit = max(logits.values())
    exps = {act: math.exp(max(-40.0, min(40.0, val - max_logit))) for act, val in logits.items()}
    total = sum(exps.values()) or 1.0
    probs = {act: round(val / total, 6) for act, val in exps.items()}
    best_action = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return {
        "action": _normalize_imitation_action(best_action),
        "confidence": round(float(probs.get(best_action, 0.5)), 4),
        "probabilities": probs,
        "candidate_id": _clean(candidate.get("candidate_id")) or None,
        "version_id": _clean(
            candidate.get("version_id")
            or artifact.get("version_id")
            or candidate.get("candidate_id")
        ) or None,
    }


def _extract_settled_arm_return(
    settlement: Optional[Mapping[str, Any]],
    arm_name: str,
) -> Tuple[Optional[float], Optional[Mapping[str, Any]]]:
    if not isinstance(settlement, Mapping):
        return None, None
    arms_raw = settlement.get("arms")
    arm_payload: Optional[Mapping[str, Any]] = None
    if isinstance(arms_raw, Mapping):
        candidate_arm = arms_raw.get(arm_name)
        if isinstance(candidate_arm, Mapping):
            arm_payload = candidate_arm
    elif isinstance(arms_raw, Sequence) and not isinstance(arms_raw, (str, bytes)):
        for item in arms_raw:
            if isinstance(item, Mapping) and _clean(item.get("arm") or item.get("name")) == arm_name:
                arm_payload = item
                break
    if not isinstance(arm_payload, Mapping):
        return None, None
    if arm_payload.get("unavailable") is True or arm_payload.get("available") is False:
        return None, arm_payload
    outcome = arm_payload.get("outcome") if isinstance(arm_payload.get("outcome"), Mapping) else {}
    ret_val = _finite(
        outcome.get("return_pct")
        if "return_pct" in outcome
        else arm_payload.get("net_return_pct", arm_payload.get("return_pct"))
    )
    return ret_val, arm_payload


def compare_trader_imitation(
    locks: Sequence[Mapping[str, Any]],
    *,
    settlements: Optional[Mapping[str, Mapping[str, Any]]] = None,
    candidate: Optional[Mapping[str, Any]] = None,
    candidates: Optional[Sequence[Mapping[str, Any]]] = None,
    decision_count: Optional[int] = None,
    minimum_decisions_required: int = MIN_TRADER_DECISIONS_FOR_IMITATION,
    training_approved: bool = True,
    tenant_id: str = "",
    user_id: str = "",
    strategy_id: str = "",
    resamples: int = 1000,
    confidence_level: float = 0.90,
    seed: int = 20261127,
) -> Dict[str, Any]:
    """Compare how close the trader_imitation behaviour candidate is to human_actual.

    Enforces:
      - At least 30 trader decisions before training/comparison is ready (else ``not_enough_decisions``).
      - At most 3 candidates kept per trader and strategy.
      - 1,000-resample percentile bootstrap intervals on action agreement and outcome gap.
      - Any bootstrap interval containing 0 is labelled ``luck`` (``可能是運氣``) and not stated as a finding.
    """
    min_req = max(1, int(minimum_decisions_required or MIN_TRADER_DECISIONS_FOR_IMITATION))
    clean_strategy = _clean(strategy_id)

    filtered_locks: List[Mapping[str, Any]] = []
    for lock in locks or ():
        if not isinstance(lock, Mapping):
            continue
        if clean_strategy:
            lock_strategy = _clean(
                lock.get("strategy_id")
                or (
                    lock.get("arms", {}).get("base_strategy", {}).get("strategy_id")
                    if isinstance(lock.get("arms"), Mapping)
                    and isinstance(lock.get("arms", {}).get("base_strategy"), Mapping)
                    else ""
                )
            )
            if lock_strategy and lock_strategy != clean_strategy:
                continue
        filtered_locks.append(lock)

    candidate_list: List[Dict[str, Any]] = []
    if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)):
        for item in candidates:
            if isinstance(item, Mapping):
                candidate_list.append(dict(item))
    if isinstance(candidate, Mapping):
        cand_id = _clean(candidate.get("candidate_id"))
        if not any(_clean(c.get("candidate_id")) == cand_id for c in candidate_list):
            candidate_list.insert(0, dict(candidate))
    kept_candidates = candidate_list[:MAX_CANDIDATES_PER_TRADER_STRATEGY]
    active_candidate = dict(candidate) if isinstance(candidate, Mapping) else (
        kept_candidates[0] if kept_candidates else None
    )

    candidate_decision_count = int(
        (active_candidate or {}).get("decision_count")
        or (
            (active_candidate or {}).get("artifact", {}).get("decision_count")
            if isinstance((active_candidate or {}).get("artifact"), Mapping)
            else 0
        )
        or 0
    )
    if decision_count is not None:
        effective_decision_count = max(0, int(decision_count))
    else:
        effective_decision_count = max(len(filtered_locks), candidate_decision_count)

    seed_prefix = f"{_clean(tenant_id)}|{_clean(user_id)}|{clean_strategy or 'default'}"

    if effective_decision_count < min_req and active_candidate is None:
        empty_agreement = bootstrap_luck_interval(
            [],
            resamples=resamples,
            confidence_level=confidence_level,
            seed=seed,
            seed_key=f"{seed_prefix}|agreement",
        )
        empty_gap = bootstrap_luck_interval(
            [],
            resamples=resamples,
            confidence_level=confidence_level,
            seed=seed,
            seed_key=f"{seed_prefix}|outcome_gap",
        )
        return {
            "comparison_state": "not_enough_decisions",
            "status_label_zh": f"決策數不足（至少需 {min_req} 筆決策才啟動僕人模仿訓練）",
            "tenant_id": _clean(tenant_id) or None,
            "user_id": _clean(user_id) or None,
            "strategy_id": clean_strategy or None,
            "decision_count": effective_decision_count,
            "minimum_decisions_required": min_req,
            "remaining_decisions_needed": max(0, min_req - effective_decision_count),
            "training_approved": bool(training_approved),
            "compared_decision_count": 0,
            "settled_comparison_count": 0,
            "agreement_rate": None,
            "agreement_count": 0,
            "disagreement_count": 0,
            "agreement_interval": empty_agreement,
            "agreement_label": empty_agreement["label"],
            "agreement_label_zh": empty_agreement["label_zh"],
            "outcome_gap": None,
            "outcome_gap_interval": empty_gap,
            "outcome_gap_label": empty_gap["label"],
            "outcome_gap_label_zh": empty_gap["label_zh"],
            "finding_stated": False,
            "differences": [],
            "decisions": [],
            "candidate": None,
            "candidates": [],
            "candidate_count": 0,
            "max_candidates_kept": MAX_CANDIDATES_PER_TRADER_STRATEGY,
            "computation_mode": NO_LANGUAGE_MODEL_PROOF,
        }

    if not training_approved and active_candidate is None:
        empty_agreement = bootstrap_luck_interval(
            [],
            resamples=resamples,
            confidence_level=confidence_level,
            seed=seed,
            seed_key=f"{seed_prefix}|agreement",
        )
        empty_gap = bootstrap_luck_interval(
            [],
            resamples=resamples,
            confidence_level=confidence_level,
            seed=seed,
            seed_key=f"{seed_prefix}|outcome_gap",
        )
        return {
            "comparison_state": "awaiting_training_approval",
            "status_label_zh": "待訓練核准（已達 30 筆決策門檻，等待訓練核准）",
            "tenant_id": _clean(tenant_id) or None,
            "user_id": _clean(user_id) or None,
            "strategy_id": clean_strategy or None,
            "decision_count": effective_decision_count,
            "minimum_decisions_required": min_req,
            "remaining_decisions_needed": 0,
            "training_approved": False,
            "compared_decision_count": 0,
            "settled_comparison_count": 0,
            "agreement_rate": None,
            "agreement_count": 0,
            "disagreement_count": 0,
            "agreement_interval": empty_agreement,
            "agreement_label": empty_agreement["label"],
            "agreement_label_zh": empty_agreement["label_zh"],
            "outcome_gap": None,
            "outcome_gap_interval": empty_gap,
            "outcome_gap_label": empty_gap["label"],
            "outcome_gap_label_zh": empty_gap["label_zh"],
            "finding_stated": False,
            "differences": [],
            "decisions": [],
            "candidate": None,
            "candidates": [],
            "candidate_count": 0,
            "max_candidates_kept": MAX_CANDIDATES_PER_TRADER_STRATEGY,
            "computation_mode": NO_LANGUAGE_MODEL_PROOF,
        }

    settlement_map = dict(settlements or {})
    agreement_series: List[float] = []
    gap_series: List[float] = []
    decisions_out: List[Dict[str, Any]] = []
    differences_out: List[Dict[str, Any]] = []
    agreement_count = 0
    disagreement_count = 0

    for lock in filtered_locks:
        decision_id = _clean(
            lock.get("human_decision_id")
            or lock.get("decision_event_id")
            or lock.get("lock_id")
        )
        lock_id = _clean(lock.get("lock_id"))
        arms = lock.get("arms") if isinstance(lock.get("arms"), Mapping) else {}
        human_arm = arms.get("human_actual") if isinstance(arms.get("human_actual"), Mapping) else {}
        imitation_arm = arms.get("trader_imitation") if isinstance(arms.get("trader_imitation"), Mapping) else {}

        settlement = (
            settlement_map.get(decision_id)
            or settlement_map.get(lock_id)
            or (lock.get("settlement") if isinstance(lock.get("settlement"), Mapping) else None)
        )
        human_ret, settled_human = _extract_settled_arm_return(settlement, "human_actual")
        imitation_ret, settled_imitation = _extract_settled_arm_return(settlement, "trader_imitation")

        human_action_raw = (
            human_arm.get("action")
            or (settled_human.get("action") if isinstance(settled_human, Mapping) else None)
            or "no_trade"
        )
        human_action = _normalize_imitation_action(human_action_raw)

        imitation_unavailable = (
            bool(imitation_arm.get("unavailable"))
            or imitation_arm.get("available") is False
            or not _clean(imitation_arm.get("action") or imitation_arm.get("suggestion"))
        )
        imitation_action_raw = (
            (imitation_arm.get("action") or imitation_arm.get("suggestion"))
            if not imitation_unavailable
            else None
        )
        imitation_confidence = _finite(imitation_arm.get("confidence"))
        imitation_candidate_id = _clean(imitation_arm.get("candidate_id")) or None

        if not imitation_action_raw and isinstance(settled_imitation, Mapping) and not settled_imitation.get("unavailable"):
            imitation_action_raw = settled_imitation.get("action")
            if imitation_confidence is None:
                imitation_confidence = _finite(settled_imitation.get("confidence"))

        if not imitation_action_raw and isinstance(active_candidate, Mapping):
            snapshot = (
                lock.get("decision_time_snapshot")
                if isinstance(lock.get("decision_time_snapshot"), Mapping)
                else {}
            )
            pred = _predict_from_candidate_model(snapshot, active_candidate)
            if pred is not None:
                imitation_action_raw = pred.get("action")
                imitation_confidence = _finite(pred.get("confidence"))
                imitation_candidate_id = pred.get("candidate_id")

        if not imitation_action_raw:
            continue

        imitation_action = _normalize_imitation_action(imitation_action_raw)
        agreed = imitation_action == human_action
        agreement_series.append(1.0 if agreed else 0.0)
        if agreed:
            agreement_count += 1
        else:
            disagreement_count += 1

        return_gap_pct: Optional[float] = None
        if human_ret is not None and imitation_ret is not None:
            return_gap_pct = round(float(imitation_ret) - float(human_ret), 6)
            gap_series.append(return_gap_pct)

        row = {
            "decision_event_id": decision_id or None,
            "lock_id": lock_id or None,
            "symbol": _clean(lock.get("symbol")) or None,
            "strategy_id": _clean(lock.get("strategy_id")) or clean_strategy or None,
            "cutoff_at": _clean(lock.get("cutoff_at")) or None,
            "human_action": human_action,
            "trader_imitation_action": imitation_action,
            "trader_imitation_confidence": round(imitation_confidence, 4) if imitation_confidence is not None else None,
            "candidate_id": imitation_candidate_id or _clean((active_candidate or {}).get("candidate_id")) or None,
            "agreed": agreed,
            "human_return_pct": round(human_ret, 6) if human_ret is not None else None,
            "trader_imitation_return_pct": round(imitation_ret, 6) if imitation_ret is not None else None,
            "outcome_gap_pct": return_gap_pct,
        }
        decisions_out.append(row)
        if not agreed:
            differences_out.append(row)

    agreement_interval = bootstrap_luck_interval(
        agreement_series,
        resamples=resamples,
        confidence_level=confidence_level,
        seed=seed,
        seed_key=f"{seed_prefix}|agreement",
    )
    outcome_gap_interval = bootstrap_luck_interval(
        gap_series,
        resamples=resamples,
        confidence_level=confidence_level,
        seed=seed,
        seed_key=f"{seed_prefix}|outcome_gap",
    )

    agreement_rate = (
        round(math.fsum(agreement_series) / len(agreement_series), 6)
        if agreement_series
        else None
    )
    outcome_gap = (
        round(math.fsum(gap_series) / len(gap_series), 6)
        if gap_series
        else None
    )
    gap_is_luck = bool(outcome_gap_interval.get("zero_inside", True))
    finding_stated = bool(gap_series) and not gap_is_luck

    if gap_is_luck:
        status_label_zh = "僕人模仿對照已完成（績效差距區間包含 0：可能是運氣）"
    else:
        status_label_zh = "僕人模仿對照已完成（績效差距區間不含 0）"

    candidate_summary = None
    if isinstance(active_candidate, Mapping):
        artifact = active_candidate.get("artifact") if isinstance(active_candidate.get("artifact"), Mapping) else {}
        candidate_summary = {
            "candidate_id": _clean(active_candidate.get("candidate_id")) or None,
            "version_id": _clean(
                active_candidate.get("version_id")
                or artifact.get("version_id")
                or active_candidate.get("candidate_id")
            ) or None,
            "strategy_id": _clean(active_candidate.get("strategy_id")) or clean_strategy or None,
            "status": _clean(active_candidate.get("status")) or "evaluated",
            "decision_count": int(
                active_candidate.get("decision_count")
                or artifact.get("decision_count")
                or effective_decision_count
            ),
            "updated_at": _clean(active_candidate.get("updated_at") or active_candidate.get("created_at")) or None,
        }

    return {
        "comparison_state": "ready",
        "status_label_zh": status_label_zh,
        "tenant_id": _clean(tenant_id) or None,
        "user_id": _clean(user_id) or None,
        "strategy_id": clean_strategy or (candidate_summary or {}).get("strategy_id"),
        "decision_count": effective_decision_count,
        "minimum_decisions_required": min_req,
        "remaining_decisions_needed": max(0, min_req - effective_decision_count),
        "training_approved": bool(training_approved),
        "compared_decision_count": len(decisions_out),
        "settled_comparison_count": len(gap_series),
        "agreement_rate": agreement_rate,
        "agreement_count": agreement_count,
        "disagreement_count": disagreement_count,
        "agreement_interval": agreement_interval,
        "agreement_label": agreement_interval["label"],
        "agreement_label_zh": agreement_interval["label_zh"],
        "outcome_gap": outcome_gap,
        "outcome_gap_interval": outcome_gap_interval,
        "outcome_gap_label": outcome_gap_interval["label"],
        "outcome_gap_label_zh": outcome_gap_interval["label_zh"],
        "finding_stated": finding_stated,
        "differences": differences_out,
        "decisions": decisions_out,
        "candidate": candidate_summary,
        "candidates": [
            {
                "candidate_id": _clean(c.get("candidate_id")) or None,
                "version_id": _clean(
                    c.get("version_id")
                    or (c.get("artifact", {}).get("version_id") if isinstance(c.get("artifact"), Mapping) else "")
                    or c.get("candidate_id")
                ) or None,
                "strategy_id": _clean(c.get("strategy_id")) or clean_strategy or None,
                "status": _clean(c.get("status")) or "evaluated",
                "updated_at": _clean(c.get("updated_at") or c.get("created_at")) or None,
            }
            for c in kept_candidates
        ],
        "candidate_count": len(kept_candidates),
        "max_candidates_kept": MAX_CANDIDATES_PER_TRADER_STRATEGY,
        "computation_mode": NO_LANGUAGE_MODEL_PROOF,
    }


__all__ = [
    "COMPONENT_NAMES",
    "LUCK_LABEL",
    "LUCK_LABEL_ZH",
    "MAX_CANDIDATES_PER_TRADER_STRATEGY",
    "MIN_TRADER_DECISIONS_FOR_IMITATION",
    "NO_LANGUAGE_MODEL_PROOF",
    "bootstrap_luck_interval",
    "compare_trader_imitation",
    "decompose_observation",
    "decompose_outlier",
    "resolve_regime_context",
]
