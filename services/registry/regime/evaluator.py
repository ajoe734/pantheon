"""Regime evaluator (design R1, R5, R8, R10, R12, R15; corrections C1-C3).

``evaluate`` is idempotent per market, session and model set version. The first call after the
session's governed data is complete reads the governed rows, computes the shared measurement
(``market_state``) and one schema-valid RegimeState per strategy, and stores them. Later calls
return what is stored. Only this module writes the two tables (``RegimeStore.evaluator_*``).

Inference is filtered/online only; no smoothed probability is stored. A missing primary axis
produces no record; a missing non-primary axis is recorded with its reason; nothing is filled in.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from ..models import utc_now_iso
from .profiles import STATE_TO_CLASS, RegimeProfile, profile_for
from .store import RegimeStore

MARKET = "TW"
SESSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TRANSITION_WINDOW = 5  # R8: primary axis switched within 5 sessions
MIXED_BELOW = 0.5  # R8: no primary state reaches 0.5
LOW_CONFIDENCE = 0.3  # schema rule
HORIZONS = ("short", "medium")
MODEL_AXES = ("volatility", "trend", "risk_appetite")
MODEL_FAMILY = {"markov_switching": "markov_switching", "jumpmodels-cjm": "custom"}
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "registry-core" / "decision-domain" / "regime_state.schema.json"
MACRO_MODEL = "rule_based@regime-features/macro_state"
# Medium volatility fits non-overlapping 5-session returns, so its newest row may trail the session.
MAX_STALE_DAYS = {"short": 0, "medium": 7}


class EvaluationRefused(Exception):
    """The evaluation cannot run now (data incomplete, no model); nothing is stored."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code
        self.detail = detail


def _schema_validator():
    from jsonschema import Draft7Validator

    return Draft7Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _sha(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def model_set_version(models: Mapping[tuple[str, str], Mapping[str, Any]]) -> str:
    return "set-" + _sha(*sorted(m["metadata"]["model_version"] for m in models.values()))[:16]


def select_model_sets(entries: Sequence[Mapping[str, Any]], market: str = MARKET) -> dict[str, dict[tuple[str, str], Mapping[str, Any]]]:
    """Latest registry model_artifact per (axis, horizon), separately for approved and candidate."""
    sets: dict[str, dict[tuple[str, str], Mapping[str, Any]]] = {"approved": {}, "candidate": {}}
    for e in entries:
        meta = e.get("metadata") or {}
        state = e.get("artifact_state")
        if state not in sets or not meta.get("regime_classifier") or meta.get("market") != market:
            continue
        if state == "approved" and meta.get("scope") != "paper":
            continue
        key = (str(meta.get("axis")), str(meta.get("horizon")))
        if key[0] not in MODEL_AXES or key[1] not in HORIZONS or not isinstance(meta.get("model"), dict):
            continue
        cur = sets[state].get(key)
        if cur is None or str(e.get("created_at") or "") >= str(cur.get("created_at") or ""):
            sets[state][key] = e
    return {k: v for k, v in sets.items() if v}


# -- inputs --------------------------------------------------------------------------------------

def input_digest(observations: Sequence[Mapping[str, Any]], session: str, now: str) -> str:
    """Hash of the latest visible version of every governed input observation up to the session."""
    from services.research.regime.features import REGIME_INPUT_DATASETS
    from services.source_ingestion.feature_catalogue import parse_time

    cutoff = parse_time(now)
    latest: dict[tuple, tuple] = {}
    for o in observations:
        if o.get("dataset") not in REGIME_INPUT_DATASETS or str(o.get("event_time"))[:10] > session:
            continue
        if parse_time(o["available_time"]) > cutoff:
            continue
        ident = (o["dataset"], o.get("field"), o.get("entity"), str(o["event_time"]))
        version = (str(o["available_time"]), str(o.get("ingest_time") or ""), repr(o.get("value")))
        if ident not in latest or version[:2] >= latest[ident][:2]:
            latest[ident] = version
    return _sha(*sorted(f"{k}={v}" for k, v in latest.items()))


def _iso_day(ts: Any) -> str:
    return ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]


def _axis_detail(axis: str, horizon: str, entry: Mapping[str, Any], observations, session: str, config) -> dict[str, Any]:
    from services.research.regime.features import BUILDERS
    from services.research.regime.models import model_from_dict

    meta = entry["metadata"]
    version = meta["model_version"]
    base = {"horizon": horizon, "model": version, "model_family": MODEL_FAMILY.get(meta.get("model_family"), "custom"),
            "model_artifact_id": meta.get("model_artifact_id"), "model_registry_id": entry.get("registry_id")}
    inputs = BUILDERS[axis](observations, decision_time=session, horizon=horizon, config=config)
    if not inputs.available:
        return {**base, "available": False, "reason": inputs.reason, "input_refs": list(inputs.input_refs)}
    try:
        sessions = model_from_dict(meta["model"]).infer(inputs.frame)
    except Exception as exc:  # noqa: BLE001 - an inference failure is a recorded reason, never a stand-in value
        return {**base, "available": False, "reason": f"inference_failed:{type(exc).__name__}:{exc}",
                "input_refs": list(inputs.input_refs)}
    last = sessions[-1]
    gap = (datetime.fromisoformat(session) - datetime.fromisoformat(last.session)).days
    if gap < 0 or gap > MAX_STALE_DAYS[horizon]:
        return {**base, "available": False, "reason": f"no_observation_for_session:last={last.session}",
                "input_refs": list(inputs.input_refs)}
    row = inputs.frame.iloc[-1]
    return {**base, "available": True, "state": last.state, "probabilities": last.probabilities,
            "transition_matrix": last.transition_matrix, "expected_durations": last.expected_durations,
            "sessions_in_state": last.sessions_in_state, "last_switch_session": last.last_switch_session,
            "as_of_session": last.session, "window_start": _iso_day(inputs.frame.index[0]),
            "feature_values": {str(k): float(v) for k, v in row.items()}, "input_refs": list(inputs.input_refs)}


def _macro_detail(observations, session: str, config) -> dict[str, Any]:
    from services.research.regime.features import build_macro_inputs

    sessions, inputs = build_macro_inputs(observations, decision_time=session, config=config)
    base = {"horizon": "monthly", "model": MACRO_MODEL, "model_family": "rule_based"}
    if not inputs.available:
        return {**base, "available": False, "reason": inputs.reason, "input_refs": list(inputs.input_refs)}
    usable = [s for s in sessions if s.month <= session[:7]]
    if not usable:
        return {**base, "available": False, "reason": "tw_macro_ndc.no_month_at_session", "input_refs": list(inputs.input_refs)}
    last, run, last_switch = usable[-1], 1, None
    for prev, cur in zip(usable[-2::-1], usable[::-1]):
        if prev.state != cur.state:
            last_switch = cur.month
            break
        run += 1
    return {**base, "available": True, "state": last.state, "as_of_session": last.month, "sessions_in_state": run,
            "last_switch_session": last_switch, "monitoring_score": last.monitoring_score,
            "leading_index": last.leading_index, "leading_index_change": last.leading_index_change,
            "input_refs": list(inputs.input_refs)}


def compute_market_state(observations, session: str, models: Mapping[tuple[str, str], Mapping[str, Any]], *,
                         model_status: str, revision: int, now: str, config=None, digest: str = "") -> dict[str, Any]:
    from services.research.regime.features import RegimeConfig

    config = config or RegimeConfig()
    axes: dict[str, Any] = {axis: {} for axis in MODEL_AXES}
    for axis in MODEL_AXES:
        for horizon in HORIZONS:
            entry = models.get((axis, horizon))
            axes[axis][horizon] = (
                _axis_detail(axis, horizon, entry, observations, session, config) if entry is not None
                else {"horizon": horizon, "available": False, "reason": f"no_{model_status}_model:{axis}:{horizon}"})
    axes["macro"] = _macro_detail(observations, session, config)
    details = [axes[a][h] for a in MODEL_AXES for h in HORIZONS] + [axes["macro"]]
    refs = sorted({r for d in details for r in d.get("input_refs", [])})
    return {"market": MARKET, "session": session, "model_set_version": model_set_version(models), "model_status": model_status,
            "revision": revision, "state": "current", "input_digest": digest, "evaluated_at": now, "axes": axes,
            "input_refs": refs, "models": {f"{a}/{h}": {"model_version": e["metadata"]["model_version"], "registry_id": e.get("registry_id")}
                                           for (a, h), e in sorted(models.items())}}


def session_complete(observations, session: str) -> Optional[str]:
    """None when the session's governed index close is present; else the refusal detail."""
    from services.research.regime.features import TAIEX_ENTITY, read_series

    close = read_series(observations, "tw_index_daily", "close", decision_time=session, entity=TAIEX_ENTITY)
    if close.empty or close.index[-1].date().isoformat() != session:
        return "tw_index_daily.close_not_visible_for_session"
    return None


# -- per-strategy records ------------------------------------------------------------------------

def _primary_detail(market_state: Mapping[str, Any], profile: RegimeProfile) -> Optional[dict[str, Any]]:
    detail = market_state["axes"][profile.primary_axis][profile.horizon]
    return detail if detail.get("available") else None


def derive_record(strategy: Mapping[str, Any], profile: RegimeProfile, market_state: Mapping[str, Any], *,
                  now: str) -> tuple[Optional[dict[str, Any]], str]:
    """Design R8. Returns ``(record, "")`` or ``(None, reason)`` when the primary axis is missing."""
    axes = market_state["axes"]
    primary = _primary_detail(market_state, profile)
    if primary is None:
        why = axes[profile.primary_axis][profile.horizon].get("reason", "unavailable")
        return None, f"primary_axis_unavailable:{profile.primary_axis}/{profile.horizon}:{why}"
    mapping = STATE_TO_CLASS[profile.primary_axis]
    scores = {mapping[s]: float(p) for s, p in primary["probabilities"].items() if s in mapping}
    if sum(scores.values()) > 1.0 + 1e-6 or any(not math.isfinite(v) for v in scores.values()):  # C3
        raise ValueError("regime_scores_invalid")
    confidence = float(primary["probabilities"][primary["state"]])
    regime_class = mapping[primary["state"]]
    if primary["last_switch_session"] is not None and primary["sessions_in_state"] <= TRANSITION_WINDOW:
        regime_class = "regime_transition"
    elif max(primary["probabilities"].values()) < MIXED_BELOW:
        regime_class = "mixed"
    if confidence <= LOW_CONFIDENCE and regime_class not in ("regime_transition", "mixed"):
        regime_class = "mixed"

    snapshot: dict[str, Any] = {}
    vol = axes["volatility"][profile.horizon]
    if vol.get("available"):
        snapshot["volatility_regime"] = {"low": "low", "high": "elevated"}[vol["state"]]
    trend = axes["trend"][profile.horizon]
    if trend.get("available"):
        p = trend["probabilities"]
        snapshot["trend_strength"] = max(-1.0, min(1.0, float(p.get("up", 0.0)) - float(p.get("down", 0.0))))
        snapshot["momentum_signal"] = ({"up": "positive", "down": "negative"}.get(trend["state"], "neutral")
                                       if max(p.values()) >= MIXED_BELOW else "neutral")
    macro = axes["macro"]
    snapshot["macro_environment"] = (
        {"expansion": "expansion", "slowdown": "slowdown", "contraction": "recession"}.get(macro["state"], "unknown")
        if macro.get("available") else "unknown")
    snapshot["profile"] = profile.to_dict()
    snapshot["axes"] = {}
    for axis in (*MODEL_AXES, "macro"):
        d = axes[axis] if axis == "macro" else axes[axis][profile.horizon]
        if not d.get("available"):
            snapshot["axes"][axis] = {"available": False, "reason": d.get("reason", "unavailable"), "model": d.get("model")}
            continue
        entry = {k: d[k] for k in ("state", "sessions_in_state", "last_switch_session", "as_of_session", "model") if k in d}
        entry["available"] = True
        if axis != "macro":
            entry["horizon"] = profile.horizon
            entry["probabilities"] = d["probabilities"]
            entry["expected_durations"] = d["expected_durations"]
            idx = list(d["probabilities"]).index(d["state"])
            entry["transition_probabilities"] = dict(zip(d["probabilities"], d["transition_matrix"][idx]))
            entry["feature_values"] = d["feature_values"]
        else:
            entry.update({k: d[k] for k in ("monitoring_score", "leading_index", "leading_index_change")})
        snapshot["axes"][axis] = entry

    refs = [{"ref_type": "dataset_version", "ref_id": r} for r in primary["input_refs"]]
    refs += [{"ref_type": "dataset_version", "ref_id": r} for r in market_state["input_refs"] if r not in primary["input_refs"]]
    refs.append({"ref_type": "market_calendar_session", "ref_id": f"{market_state['market']}:{market_state['session']}"})
    revision = market_state["revision"]
    rid = f"regime:{market_state['market']}:{strategy['strategy_id']}:{market_state['session']}:{market_state['model_set_version'][4:12]}:r{revision}"
    session = market_state["session"]
    record = {
        "regime_id": rid, "strategy_id": strategy["strategy_id"], "artifact_id": str(primary.get("model_artifact_id") or primary["model"]),
        "version": f"1.{revision - 1}.0", "regime_class": regime_class, "confidence": confidence, "regime_scores": scores,
        "evaluated_at": now, "window_start": f"{primary['window_start']}T00:00:00Z", "window_end": f"{session}T23:59:59Z",
        "input_refs": refs, "output_refs": [],
        "model_ref": {"model_family": primary["model_family"], "model_version": primary["model"],
                      **({"training_dataset_ref": f"registry:{primary['model_registry_id']}"} if primary.get("model_registry_id") else {})},
        "decision_reasoning": (f"primary axis {profile.primary_axis}/{profile.horizon} ({profile.source}) state {primary['state']} "
                               f"p={confidence:.3f}; {primary['sessions_in_state']} sessions in state; class {regime_class}"),
        "feature_snapshot": snapshot,
        "market_scope": {"asset_classes": ["equities"], "markets": [market_state["market"]]},
        "state": "proposed" if market_state["model_status"] == "candidate" else "validated",
        "metadata": {"market_state_id": f"{market_state['market']}:{session}:{market_state['model_set_version']}:r{revision}",
                     "model_set_version": market_state["model_set_version"], "lifecycle": [{"state": "proposed" if market_state["model_status"] == "candidate" else "validated", "at": now}]},
    }
    errors = sorted(_schema_validator().iter_errors(record), key=lambda e: list(e.path))
    if errors:
        raise ValueError("regime_record_schema_invalid:" + "; ".join(e.message for e in errors[:3]))
    return record, ""


# -- evaluate ------------------------------------------------------------------------------------

class RegimeEvaluator:
    def __init__(self, store: RegimeStore, observations_loader: Callable[[], Sequence[Mapping[str, Any]]], *,
                 clock: Callable[[], str] = utc_now_iso, config=None) -> None:
        self._store = store
        self._load = observations_loader
        self._clock = clock
        self._config = config

    def evaluate(self, *, session: str, models: Sequence[Mapping[str, Any]], strategies: Sequence[Mapping[str, Any]],
                 verify_inputs: bool = False, market: str = MARKET) -> dict[str, Any]:
        if market != MARKET:
            raise EvaluationRefused("unsupported_market", market)
        if not SESSION_RE.match(session):
            raise EvaluationRefused("invalid_session", session)
        sets = select_model_sets(models, market)
        if not sets:
            raise EvaluationRefused("no_regime_model", "no approved or candidate regime model artifact")
        now = self._clock()
        observations: Optional[Sequence[Mapping[str, Any]]] = None
        results = []
        for status, chosen in sets.items():
            version = model_set_version(chosen)
            existing = self._store.get_market_state(market, session, version)
            revision, digest, invalidated = 1, "", None
            if existing is not None and existing["state"] != "invalidated" and verify_inputs:
                observations = self._load() if observations is None else observations
                digest = input_digest(observations, session, now)
                if digest != existing["input_digest"]:
                    invalidated = self._store.evaluator_invalidate(market, session, version, existing["revision"], "revised_inputs")
                    existing["state"] = "invalidated"
            if existing is not None and existing["state"] == "invalidated":
                revision, existing = existing["revision"] + 1, None
            computed = existing is None
            if existing is None:
                observations = self._load() if observations is None else observations
                refusal = session_complete(observations, session)
                if refusal:
                    raise EvaluationRefused("session_data_incomplete", refusal)
                existing = compute_market_state(observations, session, chosen, model_status=status, revision=revision, now=now,
                                                config=self._config, digest=digest or input_digest(observations, session, now))
            rows, skipped = [], []
            for strategy in strategies:
                profile = profile_for(strategy.get("metadata"))
                record, reason = derive_record(strategy, profile, existing, now=now)
                if record is None:
                    skipped.append({"strategy_id": strategy["strategy_id"], "reason": reason})
                    continue
                rid = record["regime_id"]
                if self._store.get_regime(rid) is not None:
                    continue
                rows.append({"record": record, "market": market, "session": session, "revision": existing["revision"],
                             "model_set_version": version, "model_status": status, "market_state_state": "current",
                             "owner_tenant": strategy.get("owner_tenant"), "strategy_registry_id": strategy.get("registry_id")})
            stored = self._store.evaluator_commit(existing, rows) if (computed or rows) else {"market_state_id": None, "regime_ids": []}
            results.append({"model_status": status, "model_set_version": version, "revision": existing["revision"],
                            "computed": computed, "market_state_id": stored.get("market_state_id") or
                            f"{market}:{session}:{version}:r{existing['revision']}", "new_regime_ids": stored["regime_ids"],
                            "skipped": skipped, "invalidated": (invalidated or {}).get("invalidated", [])})
        return {"market": market, "session": session, "sets": results}
