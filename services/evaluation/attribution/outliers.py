"""Deterministic outlier detection for traders, institutional personas, and pool members (§12.5).

Ranks risk-adjusted returns over a weekly window against peers and against each
subject's own baseline, selects the top and bottom decile (subject to minimum
sample size), detects disagreement cases where shadow arms diverged and the
outcome gap exceeds the configured standard-deviation threshold, and attaches
the 5-component decomposition, bootstrap luck interval, and governed RegimeState
context.

No language model computes any number.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
import hashlib
import math
import os
import statistics
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .decomposition import NO_LANGUAGE_MODEL_PROOF, decompose_observation, decompose_outlier
from .settlement import (
    CANONICAL_SHADOW_ARMS,
    CostModelConfig,
    ShadowDecisionSettlement,
    settle_decision_lock,
)

SUBJECT_KINDS: Tuple[str, ...] = ("trader", "persona", "pool_member")
DIRECTIONS: Tuple[str, ...] = ("outstanding", "underperform", "disagreement")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def iso_week_for_date(window_end: str) -> str:
    text = _clean(window_end)[:10]
    try:
        d = date.fromisoformat(text)
    except ValueError:
        d = datetime.now(timezone.utc).date()
    year, week, _ = d.isocalendar()
    return f"{year:04d}-W{week:02d}"


def build_window(window_end: str, *, window_start: Optional[str] = None, window_days: int = 7) -> Dict[str, str]:
    end_text = _clean(window_end)[:10]
    try:
        end_date = date.fromisoformat(end_text)
    except ValueError:
        end_date = datetime.now(timezone.utc).date()
        end_text = end_date.isoformat()
    if window_start and _clean(window_start):
        start_text = _clean(window_start)[:10]
    else:
        start_date = end_date - timedelta(days=max(1, int(window_days) - 1))
        start_text = start_date.isoformat()
    return {
        "start": start_text,
        "end": end_text,
        "iso_week": iso_week_for_date(end_text),
    }


@dataclass(frozen=True)
class OutlierAttributionConfig:
    """Configuration for weekly outlier ranking, disagreement detection, and decomposition."""

    min_sample_size: int = 10
    top_quantile: float = 0.10
    bottom_quantile: float = 0.10
    disagreement_std_multiplier: float = 1.0
    disagreement_min_abs_gap: float = 0.005
    baseline_delta_threshold: float = 0.50
    bootstrap_resamples: int = 1000
    bootstrap_confidence_level: float = 0.90
    bootstrap_seed: int = 20261116
    observation_window: str = "5d"
    window_days: int = 7
    periods_per_year: int = 252
    drawdown_removal_limit: float = 0.10

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "OutlierAttributionConfig":
        src = os.environ if env is None else env
        kwargs: Dict[str, Any] = {}
        for f in fields(cls):
            env_key = f"PANTHEON_ATTRIBUTION_{f.name.upper()}"
            raw = src.get(env_key)
            if raw is None or str(raw).strip() == "":
                continue
            text = str(raw).strip()
            try:
                if f.type in (int, "int"):
                    kwargs[f.name] = int(text)
                elif f.type in (float, "float"):
                    kwargs[f.name] = float(text)
                else:
                    kwargs[f.name] = text
            except ValueError:
                continue
        return cls(**kwargs)


def _extract_return(obs: Mapping[str, Any]) -> Optional[float]:
    outcome = obs.get("outcome") if isinstance(obs.get("outcome"), Mapping) else obs
    for key in ("return_pct", "net_return", "return", "pnl_pct"):
        val = _finite(outcome.get(key))
        if val is not None:
            return val
    return None


def compute_risk_adjusted_metrics(
    returns: Sequence[float],
    *,
    periods_per_year: int = 252,
) -> Dict[str, Any]:
    """Compute compound total return, mean return, volatility, max drawdown, and risk-adjusted return."""
    clean = [float(r) for r in returns if _finite(r) is not None]
    n = len(clean)
    if n == 0:
        return {
            "sample_size": 0,
            "total_return": 0.0,
            "mean_return": 0.0,
            "volatility": 0.0,
            "risk_adjusted_return": 0.0,
            "max_drawdown": 0.0,
        }

    mean_ret = math.fsum(clean) / n
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in clean:
        equity *= max(1e-6, 1.0 + r)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    total_ret = equity - 1.0

    if n > 1:
        vol = statistics.stdev(clean)
    else:
        vol = abs(mean_ret) if mean_ret != 0.0 else 0.0

    denom = max(vol, 1e-4)
    scale = math.sqrt(max(1, int(periods_per_year))) if n > 1 and vol > 0 else 1.0
    rar = (mean_ret / denom) * scale if denom > 0 else 0.0

    return {
        "sample_size": n,
        "total_return": round(total_ret, 6),
        "mean_return": round(mean_ret, 6),
        "volatility": round(vol, 6),
        "risk_adjusted_return": round(rar, 6),
        "max_drawdown": round(max_dd, 6),
    }


def _resolve_baseline_metrics(
    subject: Mapping[str, Any],
    *,
    periods_per_year: int = 252,
) -> Dict[str, Any]:
    """Resolve a subject's own baseline risk-adjusted return and total return."""
    baseline_raw = subject.get("baseline")
    if isinstance(baseline_raw, Mapping):
        base_rets = baseline_raw.get("returns") or baseline_raw.get("return_series")
        if isinstance(base_rets, Sequence) and not isinstance(base_rets, (str, bytes)):
            num_rets = [float(r) for r in base_rets if _finite(r) is not None]
            if num_rets:
                m = compute_risk_adjusted_metrics(num_rets, periods_per_year=periods_per_year)
                return {
                    "available": True,
                    "baseline_risk_adjusted_return": m["risk_adjusted_return"],
                    "baseline_total_return": m["total_return"],
                    "baseline_mean_return": m["mean_return"],
                    "baseline_sample_size": m["sample_size"],
                }
        rar = _finite(
            baseline_raw.get("risk_adjusted_return")
            if "risk_adjusted_return" in baseline_raw
            else baseline_raw.get("sharpe_ratio")
        )
        tot = _finite(
            baseline_raw.get("total_return")
            if "total_return" in baseline_raw
            else baseline_raw.get("return_pct"),
            0.0,
        )
        mean_r = _finite(baseline_raw.get("mean_return"), 0.0)
        if rar is not None:
            return {
                "available": True,
                "baseline_risk_adjusted_return": round(rar, 6),
                "baseline_total_return": round(tot or 0.0, 6),
                "baseline_mean_return": round(mean_r or 0.0, 6),
                "baseline_sample_size": int(_finite(baseline_raw.get("sample_size") or baseline_raw.get("observation_count"), 0) or 0),
            }

    base_obs = subject.get("baseline_observations")
    if isinstance(base_obs, Sequence) and not isinstance(base_obs, (str, bytes)):
        num_rets = [r for obs in base_obs if isinstance(obs, Mapping) for r in [_extract_return(obs)] if r is not None]
        if num_rets:
            m = compute_risk_adjusted_metrics(num_rets, periods_per_year=periods_per_year)
            return {
                "available": True,
                "baseline_risk_adjusted_return": m["risk_adjusted_return"],
                "baseline_total_return": m["total_return"],
                "baseline_mean_return": m["mean_return"],
                "baseline_sample_size": m["sample_size"],
            }

    return {
        "available": False,
        "baseline_risk_adjusted_return": 0.0,
        "baseline_total_return": 0.0,
        "baseline_mean_return": 0.0,
        "baseline_sample_size": 0,
    }


def _record_id(
    tenant_id: str,
    subject_kind: str,
    subject_id: str,
    direction: str,
    window_end: str,
    extra: str = "",
) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}|{subject_kind}|{subject_id}|{direction}|{window_end}|{extra}".encode("utf-8")
    ).hexdigest()[:14]
    return f"attr-{subject_kind}-{direction}-{digest}"


def detect_cohort_outliers(
    subjects: Sequence[Mapping[str, Any]],
    *,
    subject_kind: str,
    window: Mapping[str, str],
    config: Optional[OutlierAttributionConfig] = None,
    tenant_id: str = "default",
    regime_state: Optional[Mapping[str, Any]] = None,
    regime_resolver: Optional[Callable[[str, str], Optional[Mapping[str, Any]]]] = None,
    created_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Rank subjects of one subject_kind against peers and own baseline, selecting top/bottom deciles."""
    if subject_kind not in SUBJECT_KINDS:
        raise ValueError(f"Unsupported subject_kind: {subject_kind!r}")
    cfg = config or OutlierAttributionConfig()
    stamp = _clean(created_at) or _utc_now()
    window_end = _clean(window.get("end"))

    evaluated: List[Dict[str, Any]] = []
    loss_triggered_underperform: List[Dict[str, Any]] = []

    for subj in subjects or ():
        if not isinstance(subj, Mapping):
            continue
        sid = _clean(
            subj.get("subject_id")
            or subj.get("persona_id")
            or subj.get("member_id")
            or subj.get("agora_user_id")
            or subj.get("user_id")
        )
        if not sid:
            continue
        raw_obs = subj.get("observations") or subj.get("decisions") or subj.get("fills") or ()
        obs_list: List[Mapping[str, Any]] = [o for o in raw_obs if isinstance(o, Mapping)]
        if not obs_list and isinstance(subj.get("returns"), Sequence):
            obs_list = [{"return_pct": float(r)} for r in subj["returns"] if _finite(r) is not None]

        returns = [r for o in obs_list for r in [_extract_return(o)] if r is not None]
        metrics = compute_risk_adjusted_metrics(returns, periods_per_year=cfg.periods_per_year)
        base_info = _resolve_baseline_metrics(subj, periods_per_year=cfg.periods_per_year)
        baseline_rar = base_info["baseline_risk_adjusted_return"]
        baseline_tot = base_info["baseline_total_return"]
        baseline_delta = round(metrics["risk_adjusted_return"] - baseline_rar, 6)
        excess_return = round(metrics["total_return"] - baseline_tot, 6)

        entry = {
            "subject": subj,
            "subject_id": sid,
            "observations": obs_list,
            "returns": returns,
            "sample_size": metrics["sample_size"],
            "metrics": {
                **metrics,
                "baseline_available": base_info["available"],
                "baseline_risk_adjusted_return": baseline_rar,
                "baseline_total_return": baseline_tot,
                "baseline_delta": baseline_delta,
                "excess_return": excess_return,
            },
        }
        if metrics["sample_size"] >= cfg.min_sample_size:
            evaluated.append(entry)
        elif subj.get("loss_trigger") or (
            metrics["sample_size"] > 0 and metrics["max_drawdown"] >= cfg.drawdown_removal_limit
        ):
            loss_triggered_underperform.append(entry)

    # Sort eligible peers descending by risk_adjusted_return, then total_return, then subject_id
    evaluated.sort(
        key=lambda item: (
            -item["metrics"]["risk_adjusted_return"],
            -item["metrics"]["total_return"],
            item["subject_id"],
        )
    )

    n_peers = len(evaluated)
    peer_rars = [item["metrics"]["risk_adjusted_return"] for item in evaluated]
    peer_median_rar = statistics.median(peer_rars) if peer_rars else 0.0

    for rank_idx, item in enumerate(evaluated, start=1):
        percentile = round((n_peers - rank_idx) / (n_peers - 1), 6) if n_peers > 1 else 0.5
        item["metrics"]["peer_rank"] = rank_idx
        item["metrics"]["peer_count"] = n_peers
        item["metrics"]["peer_percentile"] = percentile
        item["metrics"]["peer_median_risk_adjusted_return"] = round(peer_median_rar, 6)
        item["metrics"]["peer_excess_risk_adjusted_return"] = round(
            item["metrics"]["risk_adjusted_return"] - peer_median_rar, 6
        )

    selected: List[Tuple[str, Dict[str, Any]]] = []
    selected_ids: set[Tuple[str, str]] = set()

    if n_peers >= 2:
        k_top = max(1, int(math.ceil(n_peers * cfg.top_quantile)))
        k_bottom = max(1, int(math.ceil(n_peers * cfg.bottom_quantile)))
        for item in evaluated[:k_top]:
            if item["metrics"]["risk_adjusted_return"] >= peer_median_rar:
                selected.append(("outstanding", item))
                selected_ids.add((item["subject_id"], "outstanding"))
        for item in evaluated[max(k_top, n_peers - k_bottom) :]:
            if item["metrics"]["risk_adjusted_return"] <= peer_median_rar:
                selected.append(("underperform", item))
                selected_ids.add((item["subject_id"], "underperform"))

    # Also check each eligible subject against its own baseline or loss trigger
    for item in evaluated:
        sid = item["subject_id"]
        b_delta = item["metrics"]["baseline_delta"]
        has_base = item["metrics"]["baseline_available"]
        if (
            has_base
            and b_delta >= cfg.baseline_delta_threshold
            and item["metrics"]["total_return"] > 0
            and (sid, "outstanding") not in selected_ids
            and (sid, "underperform") not in selected_ids
        ):
            selected.append(("outstanding", item))
            selected_ids.add((sid, "outstanding"))
        elif (
            (has_base and b_delta <= -cfg.baseline_delta_threshold and item["metrics"]["total_return"] < 0)
            or item["subject"].get("loss_trigger")
            or item["metrics"]["max_drawdown"] >= cfg.drawdown_removal_limit
        ) and (sid, "underperform") not in selected_ids and (sid, "outstanding") not in selected_ids:
            selected.append(("underperform", item))
            selected_ids.add((sid, "underperform"))

    for item in loss_triggered_underperform:
        sid = item["subject_id"]
        if (sid, "underperform") not in selected_ids:
            item["metrics"]["peer_rank"] = None
            item["metrics"]["peer_count"] = n_peers
            item["metrics"]["peer_percentile"] = None
            item["metrics"]["loss_trigger"] = _clean(item["subject"].get("loss_trigger") or "drawdown_limit_breach")
            selected.append(("underperform", item))
            selected_ids.add((sid, "underperform"))

    records: List[Dict[str, Any]] = []
    for direction, item in selected:
        subj = item["subject"]
        sid = item["subject_id"]
        subj_regime = subj.get("regime_state")
        if subj_regime is None and regime_resolver is not None:
            subj_regime = regime_resolver(sid, window_end)
        if subj_regime is None:
            subj_regime = regime_state

        decomp = decompose_outlier(
            item["observations"],
            subject_id=sid,
            window_end=window_end,
            resamples=cfg.bootstrap_resamples,
            confidence_level=cfg.bootstrap_confidence_level,
            seed=cfg.bootstrap_seed,
            regime_state=subj_regime,
        )
        rid = _clean(subj.get("record_id")) or _record_id(tenant_id, subject_kind, sid, direction, window_end)
        rec: Dict[str, Any] = {
            "record_id": rid,
            "tenant_id": _clean(subj.get("tenant_id")) or tenant_id,
            "subject_kind": subject_kind,
            "subject_id": sid,
            "window": dict(window),
            "direction": direction,
            "metrics": item["metrics"],
            "decomposition": decomp["decomposition"],
            "component_attribution": decomp["component_attribution"],
            "sample_size": item["sample_size"],
            "luck_interval": decomp["luck_interval"],
            "regime_ref": decomp["regime_ref"],
            "regime_context": decomp["regime_context"],
            "meeting_id": _clean(subj.get("meeting_id")) or None,
            "created_at": stamp,
            "computation_mode": NO_LANGUAGE_MODEL_PROOF,
        }
        # Preserve trader identity, account_id, broker_ids, and raw_text per operator decision (§12.2/§12.4)
        if subject_kind == "trader":
            rec["agora_user_id"] = _clean(subj.get("agora_user_id") or subj.get("user_id") or sid)
            rec["trader_identity"] = (
                dict(subj["trader_identity"])
                if isinstance(subj.get("trader_identity"), Mapping)
                else {"tenant_id": rec["tenant_id"], "user_id": rec["agora_user_id"], "agora_user_id": rec["agora_user_id"]}
            )
            rec["account_id"] = subj.get("account_id")
            rec["broker_ids"] = list(subj.get("broker_ids") or [])
            if subj.get("raw_text") is not None:
                rec["raw_text"] = str(subj.get("raw_text"))
        elif subject_kind == "pool_member":
            if subj.get("persona_id"):
                rec["persona_id"] = _clean(subj.get("persona_id"))
        records.append(rec)

    return records


def detect_disagreement_outliers(
    settled_decisions: Sequence[ShadowDecisionSettlement | Mapping[str, Any]],
    *,
    window: Mapping[str, str],
    config: Optional[OutlierAttributionConfig] = None,
    tenant_id: str = "default",
    subject_outcome_stds: Optional[Mapping[str, float]] = None,
    regime_state: Optional[Mapping[str, Any]] = None,
    regime_resolver: Optional[Callable[[str, str], Optional[Mapping[str, Any]]]] = None,
    created_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Detect decisions where shadow arms diverged and the outcome gap exceeds the threshold."""
    cfg = config or OutlierAttributionConfig()
    stamp = _clean(created_at) or _utc_now()
    window_end = _clean(window.get("end"))

    normalized_decisions: List[Dict[str, Any]] = []
    returns_by_subject: Dict[str, List[float]] = {}
    obs_by_subject: Dict[str, List[Mapping[str, Any]]] = {}

    for item in settled_decisions or ():
        d_dict = item.to_dict() if isinstance(item, ShadowDecisionSettlement) else dict(item)
        arms_by_name: Dict[str, Mapping[str, Any]] = {}
        for arm_entry in d_dict.get("arms") or ():
            if isinstance(arm_entry, Mapping) and _clean(arm_entry.get("arm")):
                arms_by_name[_clean(arm_entry["arm"])] = arm_entry
        human_arm = arms_by_name.get("human_actual") or arms_by_name.get("trader")
        if not isinstance(human_arm, Mapping):
            continue
        human_outcome = human_arm.get("outcome")
        if not isinstance(human_outcome, Mapping):
            continue
        human_ret = _finite(human_outcome.get("return_pct"))
        if human_ret is None:
            continue

        sid = (
            _clean(d_dict.get("agora_user_id"))
            or _clean(d_dict.get("user_id"))
            or _clean(d_dict.get("subject_id"))
            or _clean((d_dict.get("trader_identity") or {}).get("user_id"))
            or "unknown-trader"
        )
        returns_by_subject.setdefault(sid, []).append(human_ret)
        obs_by_subject.setdefault(sid, []).append(human_outcome)
        normalized_decisions.append(
            {
                "decision": d_dict,
                "subject_id": sid,
                "arms_by_name": arms_by_name,
                "human_arm": human_arm,
                "human_outcome": human_outcome,
                "human_return": human_ret,
            }
        )

    computed_stds: Dict[str, float] = {}
    for sid, rets in returns_by_subject.items():
        if subject_outcome_stds and sid in subject_outcome_stds:
            computed_stds[sid] = float(subject_outcome_stds[sid])
        elif len(rets) > 1:
            computed_stds[sid] = statistics.stdev(rets)
        else:
            computed_stds[sid] = 0.0

    records: List[Dict[str, Any]] = []
    for entry in normalized_decisions:
        d_dict = entry["decision"]
        decision_id = _clean(d_dict.get("decision_id"))
        sid = entry["subject_id"]
        human_outcome = entry["human_outcome"]
        human_ret = entry["human_return"]
        human_dir = human_outcome.get("direction", 0)
        human_act = _clean(human_outcome.get("action"))

        subj_std = computed_stds.get(sid, 0.0)
        threshold = max(cfg.disagreement_min_abs_gap, cfg.disagreement_std_multiplier * subj_std)

        diverging: List[Dict[str, Any]] = []
        for counterpart_name in ("private_servant", "base_strategy", "committee_variant", "institutional_view"):
            c_arm = entry["arms_by_name"].get(counterpart_name)
            if not isinstance(c_arm, Mapping):
                continue
            c_out = c_arm.get("outcome")
            if not isinstance(c_out, Mapping):
                continue
            c_ret = _finite(c_out.get("return_pct"))
            if c_ret is None:
                continue
            c_dir = c_out.get("direction", 0)
            c_act = _clean(c_out.get("action"))
            # Arms diverged when their chosen direction or action differs
            if c_dir == human_dir and c_act == human_act:
                continue
            gap = human_ret - c_ret
            abs_gap = abs(gap)
            if abs_gap > threshold or (subj_std == 0.0 and abs_gap >= threshold and abs_gap > 0.0):
                diverging.append(
                    {
                        "arm": counterpart_name,
                        "action": c_act,
                        "direction": c_dir,
                        "return_pct": round(c_ret, 6),
                        "outcome_gap": round(gap, 6),
                        "abs_outcome_gap": round(abs_gap, 6),
                        "settlement_source": c_arm.get("settlement_source"),
                        "outcome": c_out,
                    }
                )

        if not diverging:
            continue

        diverging.sort(key=lambda x: (-x["abs_outcome_gap"], x["arm"]))
        primary = diverging[0]

        # Decompose the disagreement gap (human_actual observation relative to counterpart return as benchmark)
        human_decomp = decompose_observation(human_outcome, benchmark_return=0.0)
        counter_decomp = decompose_observation(primary["outcome"], benchmark_return=0.0)
        gap_decomp = {
            k: round(human_decomp[k] - counter_decomp[k], 6)
            for k in human_decomp
        }

        subj_obs = obs_by_subject.get(sid) or [human_outcome]
        subj_regime = d_dict.get("regime_state")
        if subj_regime is None and regime_resolver is not None:
            subj_regime = regime_resolver(sid, window_end)
        if subj_regime is None:
            subj_regime = regime_state

        decomp_bundle = decompose_outlier(
            subj_obs,
            subject_id=f"{sid}:{decision_id}",
            window_end=window_end,
            resamples=cfg.bootstrap_resamples,
            confidence_level=cfg.bootstrap_confidence_level,
            seed=cfg.bootstrap_seed,
            regime_state=subj_regime,
        )

        subj_kind = _clean(d_dict.get("subject_kind")) or "trader"
        rid = _record_id(tenant_id, subj_kind, sid, "disagreement", window_end, extra=decision_id)
        rec_tenant = _clean(d_dict.get("tenant_id")) or tenant_id
        rec: Dict[str, Any] = {
            "record_id": rid,
            "tenant_id": rec_tenant,
            "subject_kind": subj_kind,
            "subject_id": sid,
            "decision_id": decision_id,
            "window": dict(window),
            "direction": "disagreement",
            "metrics": {
                "decision_id": decision_id,
                "subject_arm": "human_actual",
                "subject_arm_action": human_act,
                "subject_arm_return": round(human_ret, 6),
                "counterpart_arm": primary["arm"],
                "counterpart_arm_action": primary["action"],
                "counterpart_arm_return": primary["return_pct"],
                "outcome_gap": primary["outcome_gap"],
                "abs_outcome_gap": primary["abs_outcome_gap"],
                "excess_return": primary["outcome_gap"],
                "subject_outcome_std": round(subj_std, 6),
                "disagreement_threshold": round(threshold, 6),
                "diverging_arms": [
                    {k: v for k, v in d.items() if k != "outcome"}
                    for d in diverging
                ],
            },
            "decomposition": gap_decomp,
            "subject_decomposition": decomp_bundle["decomposition"],
            "component_attribution": decomp_bundle["component_attribution"],
            "sample_size": len(subj_obs),
            "luck_interval": decomp_bundle["luck_interval"],
            "regime_ref": decomp_bundle["regime_ref"],
            "regime_context": decomp_bundle["regime_context"],
            "meeting_id": _clean(d_dict.get("meeting_id")) or None,
            "created_at": stamp,
            "computation_mode": NO_LANGUAGE_MODEL_PROOF,
        }
        if subj_kind == "trader":
            rec["agora_user_id"] = sid
            rec["trader_identity"] = (
                dict(d_dict["trader_identity"])
                if isinstance(d_dict.get("trader_identity"), Mapping)
                else {"tenant_id": rec_tenant, "user_id": sid, "agora_user_id": sid}
            )
            rec["account_id"] = d_dict.get("account_id")
            rec["broker_ids"] = list(d_dict.get("broker_ids") or [])
            if d_dict.get("raw_text") is not None:
                rec["raw_text"] = str(d_dict.get("raw_text"))
        records.append(rec)

    return records


def build_subjects_from_inputs(
    *,
    settled_decisions: Sequence[ShadowDecisionSettlement | Mapping[str, Any]] = (),
    journal_entries: Sequence[Mapping[str, Any]] = (),
    persona_inputs: Sequence[Mapping[str, Any]] = (),
    pool_member_inputs: Sequence[Mapping[str, Any]] = (),
    paper_fills: Sequence[Mapping[str, Any]] = (),
    member_virtual_books: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, List[Dict[str, Any]]]:
    """Assemble trader, institutional persona, and pool_member subject cohorts from governed inputs."""
    traders_by_id: Dict[str, Dict[str, Any]] = {}
    for item in settled_decisions or ():
        d_dict = item.to_dict() if isinstance(item, ShadowDecisionSettlement) else dict(item)
        uid = (
            _clean(d_dict.get("agora_user_id"))
            or _clean(d_dict.get("user_id"))
            or _clean((d_dict.get("trader_identity") or {}).get("user_id"))
        )
        if not uid:
            continue
        arms = {
            _clean(a.get("arm")): a
            for a in (d_dict.get("arms") or ())
            if isinstance(a, Mapping) and _clean(a.get("arm"))
        }
        human_arm = arms.get("human_actual") or arms.get("trader")
        if not isinstance(human_arm, Mapping) or not isinstance(human_arm.get("outcome"), Mapping):
            continue
        bucket = traders_by_id.setdefault(
            uid,
            {
                "subject_id": uid,
                "agora_user_id": uid,
                "tenant_id": _clean(d_dict.get("tenant_id")) or "default",
                "trader_identity": dict(d_dict.get("trader_identity") or {"user_id": uid, "agora_user_id": uid}),
                "account_id": d_dict.get("account_id"),
                "broker_ids": list(d_dict.get("broker_ids") or []),
                "raw_text": d_dict.get("raw_text"),
                "observations": [],
                "baseline_observations": [],
                "journal_entries": [],
            },
        )
        if d_dict.get("account_id") and not bucket.get("account_id"):
            bucket["account_id"] = d_dict.get("account_id")
        if d_dict.get("broker_ids") and not bucket.get("broker_ids"):
            bucket["broker_ids"] = list(d_dict.get("broker_ids") or [])
        if d_dict.get("raw_text") and not bucket.get("raw_text"):
            bucket["raw_text"] = d_dict.get("raw_text")
        bucket["observations"].append(dict(human_arm["outcome"]))
        base_arm = arms.get("base_strategy")
        if isinstance(base_arm, Mapping) and isinstance(base_arm.get("outcome"), Mapping):
            bucket["baseline_observations"].append(dict(base_arm["outcome"]))

    for j_entry in journal_entries or ():
        if not isinstance(j_entry, Mapping):
            continue
        uid = _clean(j_entry.get("agora_user_id") or j_entry.get("user_id"))
        if uid and uid in traders_by_id:
            traders_by_id[uid]["journal_entries"].append(dict(j_entry))
            if j_entry.get("raw_text") and not traders_by_id[uid].get("raw_text"):
                traders_by_id[uid]["raw_text"] = str(j_entry["raw_text"])

    personas_by_id: Dict[str, Dict[str, Any]] = {}
    for p in persona_inputs or ():
        if isinstance(p, Mapping):
            pid = _clean(p.get("subject_id") or p.get("persona_id"))
            if pid:
                personas_by_id[pid] = {
                    **dict(p),
                    "subject_id": pid,
                    "observations": list(p.get("observations") or p.get("decisions") or p.get("fills") or []),
                }

    members_by_id: Dict[str, Dict[str, Any]] = {}
    for m in pool_member_inputs or ():
        if isinstance(m, Mapping):
            mid = _clean(m.get("subject_id") or m.get("member_id"))
            if mid:
                members_by_id[mid] = {
                    **dict(m),
                    "subject_id": mid,
                    "observations": list(m.get("observations") or m.get("decisions") or m.get("fills") or []),
                }

    # Incorporate member_virtual_books (from services/execution/lean_runtime/member_virtual_book.py)
    for book in member_virtual_books or ():
        if not isinstance(book, Mapping):
            continue
        mid = _clean(book.get("member_id") or book.get("subject_id"))
        pid = _clean(book.get("persona_id"))
        net_block = book.get("net_of_costs") if isinstance(book.get("net_of_costs"), Mapping) else {}
        net_returns = list(net_block.get("returns") or book.get("returns") or [])
        gross_returns = list(book.get("returns") or net_returns)
        book_fills = [f for f in (book.get("fills") or []) if isinstance(f, Mapping) and f.get("filled", True)]

        obs_from_book: List[Dict[str, Any]] = []
        if book_fills and len(book_fills) == len(net_returns):
            for idx, (fill, net_r) in enumerate(zip(book_fills, net_returns)):
                gross_r = gross_returns[idx] if idx < len(gross_returns) else net_r
                cost_r = max(0.0, float(gross_r) - float(net_r))
                exp = _finite(fill.get("exposure_after") or fill.get("decided_exposure"), 1.0) or 1.0
                obs_from_book.append(
                    {
                        "return_pct": float(net_r),
                        "gross_return_pct": float(gross_r),
                        "cost_pct": round(cost_r, 6),
                        "exposure": exp,
                        "weight": abs(exp),
                        "direction": 1 if exp > 0 else (-1 if exp < 0 else 0),
                        "entry_price": _finite(fill.get("price")),
                        "decision_price": _finite(fill.get("decision_price") or fill.get("prev_close")),
                        "exit_price": _finite(fill.get("close") or fill.get("exit_price")),
                    }
                )
        else:
            for idx, net_r in enumerate(net_returns):
                if _finite(net_r) is None:
                    continue
                gross_r = float(gross_returns[idx]) if idx < len(gross_returns) and _finite(gross_returns[idx]) is not None else float(net_r)
                obs_from_book.append(
                    {
                        "return_pct": float(net_r),
                        "gross_return_pct": gross_r,
                        "cost_pct": round(max(0.0, gross_r - float(net_r)), 6),
                        "weight": 1.0,
                        "direction": 1,
                    }
                )

        if mid:
            m_entry = members_by_id.setdefault(
                mid,
                {
                    "subject_id": mid,
                    "member_id": mid,
                    "persona_id": pid or None,
                    "baseline": book.get("baseline"),
                    "loss_trigger": book.get("loss_trigger"),
                    "observations": [],
                },
            )
            m_entry["observations"].extend(obs_from_book)
            if book.get("baseline") and not m_entry.get("baseline"):
                m_entry["baseline"] = book.get("baseline")

        if pid:
            p_entry = personas_by_id.setdefault(
                pid,
                {
                    "subject_id": pid,
                    "persona_id": pid,
                    "baseline": book.get("persona_baseline"),
                    "loss_trigger": book.get("persona_loss_trigger"),
                    "observations": [],
                },
            )
            p_entry["observations"].extend(obs_from_book)

    # Incorporate institutional paper_fills keyed by persona_id or member_id
    for fill in paper_fills or ():
        if not isinstance(fill, Mapping):
            continue
        if _clean(fill.get("decision_id") or fill.get("human_decision_id")):
            # Agora decision lock fill already consumed by settlement
            continue
        pid = _clean(fill.get("persona_id"))
        mid = _clean(fill.get("member_id"))
        if pid:
            personas_by_id.setdefault(pid, {"subject_id": pid, "persona_id": pid, "observations": []})[
                "observations"
            ].append(dict(fill))
        if mid:
            members_by_id.setdefault(mid, {"subject_id": mid, "member_id": mid, "persona_id": pid or None, "observations": []})[
                "observations"
            ].append(dict(fill))

    return {
        "trader": list(traders_by_id.values()),
        "persona": list(personas_by_id.values()),
        "pool_member": list(members_by_id.values()),
    }


def run_weekly_outlier_attribution(
    *,
    window_end: str,
    window_start: Optional[str] = None,
    tenant_id: str = "default",
    config: Optional[OutlierAttributionConfig] = None,
    cost_model: Optional[CostModelConfig] = None,
    decision_locks: Sequence[Mapping[str, Any]] = (),
    settled_decisions: Sequence[ShadowDecisionSettlement | Mapping[str, Any]] = (),
    market_rows: Sequence[Mapping[str, Any]] = (),
    paper_fills: Sequence[Mapping[str, Any]] = (),
    journal_entries: Sequence[Mapping[str, Any]] = (),
    member_virtual_books: Sequence[Mapping[str, Any]] = (),
    persona_subjects: Sequence[Mapping[str, Any]] = (),
    pool_member_subjects: Sequence[Mapping[str, Any]] = (),
    trader_subjects: Sequence[Mapping[str, Any]] = (),
    regime_state: Optional[Mapping[str, Any]] = None,
    regime_resolver: Optional[Callable[[str, str], Optional[Mapping[str, Any]]]] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute one deterministic weekly settlement + outlier detection + decomposition run."""
    cfg = config or OutlierAttributionConfig()
    stamp = _clean(created_at) or _utc_now()
    window = build_window(window_end, window_start=window_start, window_days=cfg.window_days)

    all_settlements: List[ShadowDecisionSettlement | Mapping[str, Any]] = list(settled_decisions or ())
    seen_decision_ids = {
        _clean(s.decision_id if isinstance(s, ShadowDecisionSettlement) else s.get("decision_id"))
        for s in all_settlements
    }

    for lock in decision_locks or ():
        if not isinstance(lock, Mapping):
            continue
        did = _clean(lock.get("decision_id") or lock.get("human_decision_id") or lock.get("record_id"))
        if not did or did in seen_decision_ids:
            continue
        settlement = settle_decision_lock(
            lock,
            market_rows=market_rows,
            paper_fills=paper_fills,
            cost_model=cost_model,
            observation_window=cfg.observation_window,
            settled_at=stamp,
        )
        all_settlements.append(settlement)
        seen_decision_ids.add(did)

    assembled = build_subjects_from_inputs(
        settled_decisions=all_settlements,
        journal_entries=journal_entries,
        persona_inputs=persona_subjects,
        pool_member_inputs=pool_member_subjects,
        paper_fills=paper_fills,
        member_virtual_books=member_virtual_books,
    )
    if trader_subjects:
        by_id = {t["subject_id"]: t for t in assembled["trader"]}
        for t in trader_subjects:
            if isinstance(t, Mapping):
                tid = _clean(t.get("subject_id") or t.get("agora_user_id") or t.get("user_id"))
                if tid:
                    by_id[tid] = dict(t)
        assembled["trader"] = list(by_id.values())

    records: List[Dict[str, Any]] = []
    for kind in SUBJECT_KINDS:
        records.extend(
            detect_cohort_outliers(
                assembled[kind],
                subject_kind=kind,
                window=window,
                config=cfg,
                tenant_id=tenant_id,
                regime_state=regime_state,
                regime_resolver=regime_resolver,
                created_at=stamp,
            )
        )

    records.extend(
        detect_disagreement_outliers(
            all_settlements,
            window=window,
            config=cfg,
            tenant_id=tenant_id,
            regime_state=regime_state,
            regime_resolver=regime_resolver,
            created_at=stamp,
        )
    )

    return {
        "window": window,
        "iso_week": window["iso_week"],
        "tenant_id": tenant_id,
        "records": records,
        "settlements": [
            s.to_dict() if isinstance(s, ShadowDecisionSettlement) else dict(s)
            for s in all_settlements
        ],
        "created_at": stamp,
    }


__all__ = [
    "DIRECTIONS",
    "OutlierAttributionConfig",
    "SUBJECT_KINDS",
    "build_subjects_from_inputs",
    "build_window",
    "compute_risk_adjusted_metrics",
    "detect_cohort_outliers",
    "detect_disagreement_outliers",
    "iso_week_for_date",
    "run_weekly_outlier_attribution",
]
