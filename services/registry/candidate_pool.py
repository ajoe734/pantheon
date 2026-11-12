"""Strategy-owned Taiwan screens shared by paper and research.

The feed supplies ``candidate_pool_market.v1``: a complete governed Taiwan
symbol map, source references, dated daily bars (including traded_value and
suspended), and timestamped catalogue features. A provisioned symbol list is
never a substitute for that market. Missing inputs exclude, never impute.
Industry grouping remains unavailable until its PIT dataset is catalogued.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta
import hashlib
import json
import re
from statistics import median
from typing import Any, Mapping

from . import factor_rule as fr

CADENCES = ("daily", "weekly", "monthly", "quarterly")
MARKET_FEATURE_KEY = "candidate_pool_market"


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def canonical_symbol(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise fr.FactorRuleValidationError("screen symbol must be a string")
    match = re.fullmatch(r"([0-9]{4,6})\.(TW|TWSE|TWO|TPEX)", symbol.upper())
    if not match:
        raise fr.FactorRuleValidationError(f"screen requires a Taiwan equity symbol: {symbol!r}")
    return match[1] + (".TW" if match[2] in {"TW", "TWSE"} else ".TWO")


def _param(node: Any, parameters: Mapping[str, Any], label: str, *, minimum: float = 0,
           integer: bool = False) -> float | int:
    if not isinstance(node, Mapping) or set(node) != {"const_param"}:
        raise fr.FactorRuleValidationError(f"screen {label} must be a const_param reference")
    value = parameters.get(node["const_param"])
    if not fr._is_number(value) or value < minimum or (integer and not fr._is_int(value)):
        raise fr.FactorRuleValidationError(f"screen {label} has an invalid parameter")
    return int(value) if integer else float(value)


def screen_param_names(screen: Mapping[str, Any]) -> set[str]:
    def walk(node: Any) -> set[str]:
        if isinstance(node, Mapping):
            if "const_param" in node:
                return {node["const_param"]}
            return set().union(*(walk(v) for v in node.values()))
        if isinstance(node, list):
            return set().union(*(walk(v) for v in node))
        return set()
    return walk(screen)


def validate_screen(screen: Any, inputs: Mapping[str, Any], parameters: Mapping[str, Any]) -> dict:
    if not isinstance(screen, Mapping) or set(screen) != {"version", "min_size", "eligibility", "steps"}:
        raise fr.FactorRuleValidationError("screen requires version, min_size, eligibility and steps")
    if screen["version"] != "candidate_screen.v1":
        raise fr.FactorRuleValidationError("unsupported screen version")
    if parameters.get("market") != "TW":
        raise fr.FactorRuleValidationError("screen market must be TW")
    _param(screen["min_size"], parameters, "min_size", minimum=1, integer=True)
    eligibility = screen["eligibility"]
    if not isinstance(eligibility, Mapping) or set(eligibility) != {
        "min_history_bars", "liquidity_window", "min_median_traded_value",
    }:
        raise fr.FactorRuleValidationError("screen eligibility has invalid keys")
    for key in ("min_history_bars", "liquidity_window"):
        _param(eligibility[key], parameters, key, minimum=1, integer=True)
    _param(eligibility["min_median_traded_value"], parameters, "min_median_traded_value")
    steps = screen["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
        raise fr.FactorRuleValidationError("screen steps must contain 1..16 steps")
    result = copy.deepcopy(dict(screen))
    for index, step in enumerate(result["steps"]):
        if not isinstance(step, dict):
            raise fr.FactorRuleValidationError("screen step must be an object")
        expected = {"kind", "expression", "refresh"}
        if step.get("kind") == "rank":
            expected |= {"side", "count"}
            if step.get("side") not in {"top", "bottom"}:
                raise fr.FactorRuleValidationError("screen rank side must be top/bottom")
            _param(step["count"], parameters, "rank count", minimum=1, integer=True)
        elif step.get("kind") != "filter":
            raise fr.FactorRuleValidationError("screen step kind must be filter/rank")
        if set(step) - {"group_by"} != expected or step.get("refresh") not in CADENCES:
            raise fr.FactorRuleValidationError("screen step keys or refresh cadence are invalid")
        expr = fr.parse_factor_expression(step["expression"], allow_literal_constants=False)
        fr._validate_window_And_param_constraints(expr, parameters)
        step["expression"] = expr
        refs, _ = fr.collect_expression_refs_and_params(expr)
        if step.get("group_by") is not None:
            group = step["group_by"]
            if not isinstance(group, Mapping) or set(group) != {"industry_input"}:
                raise fr.FactorRuleValidationError("screen group_by requires industry_input")
            name = group["industry_input"]
            spec = inputs.get(name, {})
            if (spec.get("dataset"), spec.get("field")) != (
                "tw_industry_classification", "industry_code",
            ):
                raise fr.FactorRuleValidationError("screen grouping requires governed PIT industry classification")
            refs.add(name)
        if not refs or refs - inputs.keys():
            raise fr.FactorRuleValidationError("screen expressions must reference declared catalogue inputs")
        for name in sorted(refs):
            spec = inputs[name]
            definition = fr.get_feature_definition(spec["dataset"], spec["field"])
            if definition is None or definition.release_cadence is None:
                raise fr.FactorRuleValidationError(f"screen input {name} has no governed release cadence")
            if CADENCES.index(step["refresh"]) < CADENCES.index(definition.release_cadence):
                raise fr.FactorRuleValidationError(f"screen step {index} refresh outruns {name} release cadence")
    return result


def _day(value: Any) -> date:
    if value is None:
        raise fr.FactorRuleValidationError("screen requires decision_time")
    return fr._parse_timestamp_utc(value).astimezone(fr._TAIPEI_TZ).date()


def _cutoff(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=fr._TAIPEI_TZ) - timedelta(microseconds=1)


def _anchor(day: date, cadence: str) -> date:
    if cadence == "weekly":
        return day - timedelta(days=day.weekday())
    if cadence == "monthly":
        return day.replace(day=1)
    if cadence == "quarterly":
        return day.replace(month=((day.month - 1) // 3) * 3 + 1, day=1)
    return day


def _market(raw: Any) -> tuple[dict, dict[str, dict]]:
    if not isinstance(raw, Mapping) or raw.get("schema_version") != "candidate_pool_market.v1":
        raise fr.FactorRuleValidationError("screen requires candidate_pool_market.v1")
    source_refs = raw.get("source_refs")
    if (raw.get("scope") != "all_governed_tw" or not isinstance(source_refs, list)
            or not source_refs or not all(isinstance(s, str) and s.strip() for s in source_refs)):
        raise fr.FactorRuleValidationError("screen requires complete all_governed_tw market with source_refs")
    sessions = raw.get("trading_sessions")
    if (not isinstance(sessions, list) or not sessions
            or not all(isinstance(s, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) for s in sessions)
            or not raw.get("calendar_coverage_through")):
        raise fr.FactorRuleValidationError("screen requires a governed trading calendar and coverage date")
    try:
        for session in sessions:
            date.fromisoformat(session)
        date.fromisoformat(raw["calendar_coverage_through"])
    except (TypeError, ValueError) as exc:
        raise fr.FactorRuleValidationError("invalid screen trading calendar") from exc
    symbols = raw.get("symbols")
    if not isinstance(symbols, Mapping) or not symbols:
        raise fr.FactorRuleValidationError("screen market symbols are missing")
    normalized = {}
    for symbol, payload in symbols.items():
        key = canonical_symbol(symbol)
        if key in normalized or not isinstance(payload, Mapping):
            raise fr.FactorRuleValidationError("screen market has duplicate or invalid symbols")
        if payload.get("dataset") not in fr.PRICE_VOLUME_DATASETS - fr.INTRADAY_PRICE_VOLUME_DATASETS:
            raise fr.FactorRuleValidationError("screen boundary requires governed daily bars")
        normalized[key] = dict(payload)
    return dict(raw), normalized


def _visible_bars(payload: Mapping, cutoff: datetime) -> list[dict]:
    raw = payload.get("bars")
    if not isinstance(raw, list):
        raise fr.FactorRuleValidationError("missing_daily_bars")
    latest: dict[datetime, tuple[datetime, dict]] = {}
    for row in raw:
        if not isinstance(row, Mapping) or not row.get("event_time") or not row.get("available_time"):
            raise fr.FactorRuleValidationError("undated_daily_bars")
        event = fr._parse_timestamp_utc(row["event_time"])
        available = fr._parse_timestamp_utc(row["available_time"])
        if max(event, available) > cutoff:
            continue
        if event not in latest or available >= latest[event][0]:
            latest[event] = (available, dict(row))
    return [latest[event][1] for event in sorted(latest)]


def _features(payload: Mapping, cutoff: datetime) -> tuple[dict, list[float]]:
    bars = _visible_bars(payload, cutoff)
    features = dict(payload.get("features") or {})
    closes = []
    for field in fr.BUILT_IN_PRICE_VOLUME_FIELDS:
        observations = [
            {"event_time": row["event_time"], "available_time": row["available_time"], "value": row[field]}
            for row in bars if field in row
        ]
        if observations:
            features[field] = observations
            if field == "close":
                closes = [row["value"] for row in observations]
    return features, closes


def _dated_input_features(inputs: list, features: Mapping, cutoff: datetime) -> dict:
    # Screen inputs must be dated even when legacy per-symbol price evaluation
    # admits aligned arrays. No price/vwap/close fallback is used here.
    result = dict(features)
    for spec in inputs:
        rows = fr._lookup_raw_feature(features, name=spec["name"], dataset=spec["dataset"], field=spec["field"])
        if not isinstance(rows, list) or not rows or not all(
            isinstance(r, Mapping) and r.get("event_time") and r.get("available_time") for r in rows
        ):
            raise fr.FactorRuleValidationError(f"missing_dated_input:{spec['name']}")
        # Hide future values before numeric validation, including malformed
        # future rows and late revisions of prices used by a screen.
        result[spec["name"]] = fr._latest_visible_versions(
            rows, availability_lag_days=0, decision_time=cutoff,
        )
    return result


def _step_value(step: Mapping, logic: Mapping, payload: Mapping, parameters: Mapping,
                cutoff: datetime) -> tuple[float, float | None]:
    refs, _ = fr.collect_expression_refs_and_params(step["expression"])
    group = (step.get("group_by") or {}).get("industry_input")
    needed = refs | ({group} if group else set())
    inputs = [i for i in logic["inputs"] if i["name"] in needed]
    features, closes = _features(payload, cutoff)
    features = _dated_input_features(inputs, features, cutoff)
    env = fr.resolve_point_in_time_inputs({"inputs": inputs}, closes, features=features, decision_time=cutoff)
    score = fr.evaluate_factor_expression(step["expression"], env, parameters)
    industry = env[group][-1] if group else None
    return score, industry


def build_candidate_pool_snapshot(artifact: Mapping, market: Mapping, decision_time: Any) -> dict:
    """Compute a deterministic, daily snapshot without mutable cadence caches.

    Each stage evaluates its prefix at its own calendar anchor. Later stages
    therefore cannot accidentally refresh a monthly parent daily. Daily safety
    eligibility still removes suspended/illiquid/stale names between refreshes.
    """
    logic = artifact["strategy_logic"]
    parameters = artifact["parameters"]
    validated = fr.validate_factor_rule_logic(logic, parameters, artifact.get("mutation_surface"))
    screen = validated["screen"]
    raw, symbols = _market(market)
    day = _day(decision_time)
    if date.fromisoformat(raw["calendar_coverage_through"]) < day:
        raise fr.FactorRuleValidationError("screen trading calendar does not cover decision day")
    eligibility = screen["eligibility"]
    history = _param(eligibility["min_history_bars"], parameters, "history", integer=True)
    window = _param(eligibility["liquidity_window"], parameters, "liquidity", integer=True)
    min_value = _param(eligibility["min_median_traded_value"], parameters, "liquidity value")
    memo = {}

    def boundary(on: date) -> tuple[set[str], dict[str, str]]:
        all_bars, reasons = {}, {}
        for symbol, payload in symbols.items():
            try:
                all_bars[symbol] = _visible_bars(payload, _cutoff(on))
            except fr.FactorRuleValidationError as exc:
                reasons[symbol] = str(exc)
        latest_day = max((date.fromisoformat(s) for s in raw["trading_sessions"]
                          if date.fromisoformat(s) < on), default=None)
        for symbol, bars in all_bars.items():
            if not bars or _day(bars[-1]["event_time"]) != latest_day:
                reasons[symbol] = "missing_prior_session"
            elif not isinstance(bars[-1].get("suspended"), bool):
                reasons[symbol] = "missing_suspension_status"
            elif bars[-1]["suspended"]:
                reasons[symbol] = "suspended"
            elif len(bars) < max(history, window):
                reasons[symbol] = "insufficient_history"
            else:
                values = [row.get("traded_value") for row in bars[-window:]]
                if not all(fr._is_number(row.get("close")) and row["close"] > 0 for row in bars):
                    reasons[symbol] = "invalid_price_history"
                elif not all(fr._is_number(v) and v >= 0 for v in values):
                    reasons[symbol] = "missing_liquidity"
                elif median(values) < min_value:
                    reasons[symbol] = "illiquid"
        return set(symbols) - reasons.keys(), reasons

    def stage(index: int, on: date) -> tuple[set[str], dict[str, str], list[dict]]:
        if index < 0:
            members, excluded = boundary(on)
            return members, excluded, []
        step = screen["steps"][index]
        anchor = _anchor(on, step["refresh"])
        key = (index, anchor)
        if key in memo:
            return copy.deepcopy(memo[key])
        members, excluded, records = stage(index - 1, anchor)
        groups: dict[float | None, list[tuple[str, float]]] = {}
        for symbol in sorted(members):
            try:
                score, group = _step_value(step, validated, symbols[symbol], parameters, _cutoff(anchor))
                groups.setdefault(group, []).append((symbol, score))
            except fr.FactorRuleValidationError as exc:
                excluded[symbol] = f"step_{index}:missing_input:{exc}"
        kept = set()
        for values in groups.values():
            if step["kind"] == "filter":
                selected = {symbol for symbol, score in values if score > 0}
            else:
                count = _param(step["count"], parameters, "count", integer=True)
                ordered = sorted(values, key=lambda pair: (
                    -pair[1] if step["side"] == "top" else pair[1], pair[0],
                ))
                selected = {symbol for symbol, _ in ordered[:count]}
            kept |= selected
            for symbol, _ in values:
                if symbol not in selected:
                    excluded[symbol] = f"step_{index}:{step['kind']}"
        records = [*records, {"step": index, "refresh": step["refresh"],
                              "as_of": anchor.isoformat(), "members": sorted(kept),
                              "scores": {s: score for values in groups.values() for s, score in values}}]
        memo[key] = (kept, excluded, records)
        return copy.deepcopy(memo[key])

    members, excluded, records = stage(len(screen["steps"]) - 1, day)
    safe, daily_excluded = boundary(day)
    members &= safe
    excluded.update(daily_excluded)
    # Preserve an explanation for every governed symbol, including late listings
    # absent at a slower stage's anchor.
    for symbol in set(symbols) - members:
        excluded.setdefault(symbol, "not_in_refresh_snapshot")
    limited = not (raw.get("includes_delisted") is True and raw.get("includes_suspended") is True)
    snapshot = {
        "schema_version": "candidate_pool_snapshot.v1", "as_of": day.isoformat(),
        "cutoff": _cutoff(day).isoformat(), "members": sorted(members),
        "excluded": [{"symbol": s, "reason": excluded[s]} for s in sorted(excluded)],
        "filters_version": digest({"screen": screen, "parameters": parameters}),
        "source_refs": sorted(raw["source_refs"]), "steps": records,
        "survivorship_limited": limited,
        "below_minimum": len(members) < _param(screen["min_size"], parameters, "min_size", integer=True),
    }
    return {**snapshot, "digest": digest(snapshot)}


def _member_view(artifact: Mapping, symbols: Mapping, snapshot: Mapping, symbol: str,
                 decision_time: Any, current_direction: str | None,
                 holding_bars: int | None) -> dict:
    """The shared screen/entry/exit entry point. Explicit holdings prevent phantom
    positions inferred from pre-membership signals. Feed owners supply the actual
    prior position; absent state starts flat (the research adapter replays it).
    """
    key = canonical_symbol(symbol)
    hold = {"action": "HOLD", "direction": "FLAT", "signal": 0.0, "momentum": 0.0, "strength": 0.0}
    if snapshot["below_minimum"]:
        view = {**hold, "hold_reason": "candidate_pool_below_minimum"}
    elif key not in snapshot["members"]:
        if current_direction in {"LONG", "SHORT"}:
            short = current_direction == "SHORT"
            view = {**hold, "action": "BUY" if short else "SELL",
                    "direction": "LONG" if short else "SHORT", "strength": 1.0,
                    "exit_reason": "left_candidate_pool", "target_direction": "FLAT"}
        else:
            view = {**hold, "hold_reason": "outside_candidate_pool"}
    else:
        member = copy.deepcopy(dict(artifact))
        logic = member["strategy_logic"]
        logic.pop("screen")
        refs = set()
        for node in [logic["signal"], logic["strength"], *logic["entry"].values(), *logic["exit"].values()]:
            if isinstance(node, Mapping):
                refs.update(fr.collect_expression_refs_and_params(node)[0])
        logic["inputs"] = [i for i in logic["inputs"] if i["name"] in refs]
        declared = {i["name"] for i in logic["inputs"]}
        for field in sorted((refs & fr.BUILT_IN_PRICE_VOLUME_FIELDS) - declared):
            logic["inputs"].append({"name": field, "dataset": symbols[key]["dataset"],
                                     "field": field, "availability_lag_days": 0})
        cutoff = fr._parse_timestamp_utc(decision_time, is_decision_time=True)
        features, closes = _features(symbols[key], cutoff)
        try:
            features = _dated_input_features(logic["inputs"], features, cutoff)
            view = fr.evaluate_factor_rule_view(
                member, closes, features=features, decision_time=cutoff,
                current_direction=current_direction or "FLAT", holding_bars=holding_bars or 0,
                include_position_state=True,
            )
            if view["action"] != "HOLD" and view["target_direction"] == "FLAT":
                if current_direction not in {"LONG", "SHORT"}:
                    view = {**hold, "target_direction": "FLAT", "holding_bars": 0,
                            "hold_reason": "exit_without_position"}
                else:
                    view["exit_reason"] = "rule_exit"
        except fr.FactorRuleValidationError as exc:
            view = {**hold, "hold_reason": f"missing_entry_input:{exc}"}
    return {**view, "candidate_pool_snapshot": snapshot, "candidate_pool_digest": snapshot["digest"]}


def evaluate_screened_views(artifact: Mapping, market: Mapping, decision_time: Any,
                            *, positions: Mapping[str, Mapping] | None = None) -> dict[str, dict]:
    """Evaluate the whole governed market with one shared screen snapshot."""
    snapshot = build_candidate_pool_snapshot(artifact, market, decision_time)
    _, symbols = _market(market)
    positions = {canonical_symbol(s): p for s, p in (positions or {}).items()}
    keys = set(symbols) | positions.keys()
    return {s: _member_view(
        artifact, symbols, snapshot, s, decision_time,
        positions.get(s, {}).get("current_direction"), positions.get(s, {}).get("holding_bars"),
    ) for s in sorted(keys)}


def evaluate_screened_view(artifact: Mapping, market: Mapping, symbol: str, decision_time: Any,
                           *, current_direction: str | None = None,
                           holding_bars: int | None = None) -> dict:
    snapshot = build_candidate_pool_snapshot(artifact, market, decision_time)
    _, symbols = _market(market)
    return _member_view(artifact, symbols, snapshot, symbol, decision_time,
                        current_direction, holding_bars)
