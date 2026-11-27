"""Deterministic trader habit profile (AGORA-TRADER-HABIT-PROFILE-20261127, SD §6).

Pure functions only. The profile is the *fact* layer of the trader's habits: it is derived from
the trader's own decision locks (decisions, rejections, deferrals, interventions), decision
journal entries and servant-conversation widget context. No language model computes any
feature (11/16 rule): every feature is a fixed rule over the inputs, carries its sample size,
and is shown only from ``HABIT_MIN_SAMPLE`` samples (SD §6 proposal: 10 decisions).

The servant's *interpretation* of these facts is private memory and is not produced or stored
here (SD §6, memory rule): this module only owns facts.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

HABIT_PROFILE_SCHEMA = "AgoraTraderHabitProfile.v1"
HABIT_DERIVATION_METHOD = "deterministic_rules.v1"
HABIT_MIN_SAMPLE = 10  # SD §6: a feature is shown only from 10 samples (proposal)
HABIT_PROFILE_HISTORY_VERSIONS = 12  # SD §6: latest row plus 12 superseded versions (proposal)
HABIT_PROFILE_MAX_BYTES = 8192  # SD §6 budget is about 4 KB; hard cap before refs are trimmed
HABIT_SOURCE_REFS_PER_FEATURE = 5
ALL_STRATEGIES = "*"

FEATURE_KEYS = (
    "holding_period",
    "entry_timing",
    "rule_discipline",
    "position_size",
    "rejected_candidates",
    "data_order",
    "intervention",
)
FEATURE_LABELS = {
    "holding_period": "持有期間",
    "entry_timing": "進場時機（相對訊號）",
    "rule_discipline": "對規則的加減碼／出場紀律",
    "position_size": "部位大小",
    "rejected_candidates": "被剔除的候選",
    "data_order": "看資料的順序",
    "intervention": "介入頻率與方向",
}

_ENTRY_ACTIONS = ("enter", "add")
_EXIT_ACTIONS = ("exit",)
_EXECUTED_VERDICTS = ("approve", "modify")
_RULE_EVENT_KINDS = ("entry", "add", "reduce", "exit")
_SIZE_KEYS = ("size", "quantity", "position_size", "shares", "qty")
_LIQUIDITY_KEYS = ("avg_daily_turnover", "avg_daily_value", "turnover", "avg_volume", "volume", "liquidity")
_PRICE_KEYS = ("price", "close", "last_price", "reference_price")
_SECTOR_KEYS = ("sector", "industry")
_DATA_FAMILIES = (
    ("chips", ("chip", "broker", "branch", "institution", "籌碼", "分點", "法人")),
    ("price", ("price", "bar", "kline", "chart", "candle", "quote", "價")),
    ("news", ("news", "disclosure", "announce", "新聞", "公告")),
    ("fundamental", ("fundamental", "financial", "revenue", "eps", "基本面", "財報")),
)


def profile_key(tenant_id: str, user_id: str, strategy_id: Optional[str] = None) -> str:
    """Stable owner key: one profile row per trader and strategy (``*`` = all strategies)."""
    digest = hashlib.sha256(
        f"{tenant_id}\0{user_id}\0{strategy_id or ALL_STRATEGIES}\0habit".encode("utf-8")
    ).hexdigest()[:24]
    return f"habit-{digest}"


def _parse_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_number(source: Any, keys: Sequence[str]) -> Optional[float]:
    if not isinstance(source, Mapping):
        return None
    for key in keys:
        number = _number(source.get(key))
        if number is not None:
            return number
    return None


def _first_text(source: Any, keys: Sequence[str]) -> Optional[str]:
    if not isinstance(source, Mapping):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _round(value: float) -> float:
    return round(float(value), 4)


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile of an already sorted, non-empty sequence."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def _distribution(values: Iterable[float]) -> Optional[Dict[str, float]]:
    ordered = sorted(values)
    if not ordered:
        return None
    return {
        "median": _round(_quantile(ordered, 0.5)),
        "p25": _round(_quantile(ordered, 0.25)),
        "p75": _round(_quantile(ordered, 0.75)),
    }


def _lock_id(lock: Mapping[str, Any]) -> str:
    return str(
        lock.get("human_decision_id") or lock.get("decision_event_id") or lock.get("id") or ""
    )


def _lock_time(lock: Mapping[str, Any]) -> Optional[datetime]:
    return _parse_time(lock.get("decision_time") or lock.get("created_at"))


def _symbol(lock: Mapping[str, Any]) -> str:
    snapshot = lock.get("decision_time_snapshot")
    snap = snapshot if isinstance(snapshot, Mapping) else {}
    return str(lock.get("target_instrument") or snap.get("symbol") or "").strip().upper()


def _snapshot(lock: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = lock.get("decision_time_snapshot")
    return snapshot if isinstance(snapshot, Mapping) else {}


def _modifications(lock: Mapping[str, Any]) -> Mapping[str, Any]:
    mods = lock.get("raw_modifications")
    return mods if isinstance(mods, Mapping) else {}


def _verdict(lock: Mapping[str, Any]) -> str:
    return str(lock.get("trading_room_verdict") or "").strip()


def _executed(lock: Mapping[str, Any]) -> bool:
    return _verdict(lock) in _EXECUTED_VERDICTS


def _headline(metric: str, value: Any, unit: str) -> Dict[str, Any]:
    return {"metric": metric, "value": value, "unit": unit}


def _feature(
    key: str,
    *,
    value: Optional[Dict[str, Any]],
    headline: Optional[Dict[str, Any]],
    sample_size: int,
    inputs: Sequence[str],
    source_refs: Sequence[str],
    unavailable_reason: Optional[str] = None,
) -> Dict[str, Any]:
    shown = sample_size >= HABIT_MIN_SAMPLE and value is not None
    reason = None
    if not shown:
        reason = unavailable_reason or (
            "insufficient_sample" if sample_size < HABIT_MIN_SAMPLE else "no_comparable_inputs"
        )
    return {
        "key": key,
        "label": FEATURE_LABELS[key],
        "sample_size": int(sample_size),
        "min_sample": HABIT_MIN_SAMPLE,
        "shown": shown,
        "unavailable_reason": reason,
        "value": value if shown else None,
        "headline": headline if shown else None,
        "inputs": list(inputs),
        "source_refs": list(source_refs)[-HABIT_SOURCE_REFS_PER_FEATURE:],
    }


# ---------------------------------------------------------------------------- features


def _holding_period(locks: Sequence[Mapping[str, Any]], journal: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    events: List[Tuple[datetime, str, str, str]] = []  # (time, symbol, kind, ref)
    for lock in locks:
        when, symbol = _lock_time(lock), _symbol(lock)
        action = str(lock.get("decision") or "")
        if when is None or not symbol or not _executed(lock):
            continue
        if action in _ENTRY_ACTIONS:
            events.append((when, symbol, "open", _lock_id(lock)))
        elif action in _EXIT_ACTIONS:
            events.append((when, symbol, "close", _lock_id(lock)))
    for entry in journal:
        when = _parse_time(entry.get("occurred_at") or entry.get("created_at") or entry.get("createdAt"))
        symbol = str(entry.get("symbol") or entry.get("target_instrument") or "").strip().upper()
        action = str(entry.get("action") or "").strip().lower()
        if when is None or not symbol:
            continue
        ref = str(entry.get("id") or entry.get("entry_id") or "")
        if action in ("enter", "buy", "add"):
            events.append((when, symbol, "open", ref))
        elif action in ("exit", "sell"):
            events.append((when, symbol, "close", ref))
    events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))
    opened: Dict[str, datetime] = {}
    days: List[float] = []
    refs: List[str] = []
    for when, symbol, kind, ref in events:
        if kind == "open":
            opened.setdefault(symbol, when)
        elif symbol in opened:
            days.append(max(0.0, (when - opened.pop(symbol)).total_seconds() / 86400.0))
            refs.append(ref)
    dist = _distribution(days)
    return _feature(
        "holding_period",
        value={**dist, "unit": "days"} if dist else None,
        headline=_headline("median_days", dist["median"], "days") if dist else None,
        sample_size=len(days),
        inputs=["decision_locks", "decision_journal"],
        source_refs=refs,
    )


def _entry_timing(locks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    minutes: List[float] = []
    refs: List[str] = []
    for lock in locks:
        if not _executed(lock) or str(lock.get("decision") or "") not in _ENTRY_ACTIONS:
            continue
        decided = _lock_time(lock)
        triggered = _parse_time(_snapshot(lock).get("triggered_at"))
        if decided is None or triggered is None:
            continue
        minutes.append(max(0.0, (decided - triggered).total_seconds() / 60.0))
        refs.append(_lock_id(lock))
    dist = _distribution(minutes)
    return _feature(
        "entry_timing",
        value={**dist, "unit": "minutes_after_signal"} if dist else None,
        headline=_headline("median_minutes_after_signal", dist["median"], "minutes") if dist else None,
        sample_size=len(minutes),
        inputs=["decision_locks"],
        source_refs=refs,
    )


def _rule_discipline(locks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_kind: Dict[str, List[bool]] = {}
    refs: List[str] = []
    for lock in locks:
        kind = str(lock.get("event_kind") or "")
        base = (lock.get("arms") or {}).get("base_strategy") if isinstance(lock.get("arms"), Mapping) else None
        if kind not in _RULE_EVENT_KINDS or not isinstance(base, Mapping) or not base.get("available"):
            continue
        followed = _verdict(lock) == "approve" and not _modifications(lock)
        by_kind.setdefault(kind, []).append(followed)
        refs.append(_lock_id(lock))
    total = sum(len(v) for v in by_kind.values())
    followed_total = sum(sum(v) for v in by_kind.values())
    value = None
    if total:
        value = {
            "follow_rate": _round(followed_total / total),
            "by_event_kind": {
                kind: {"follow_rate": _round(sum(v) / len(v)), "n": len(v)}
                for kind, v in sorted(by_kind.items())
            },
        }
    return _feature(
        "rule_discipline",
        value=value,
        headline=_headline("follow_rate_percent", _round(100.0 * value["follow_rate"]), "percent") if value else None,
        sample_size=total,
        inputs=["decision_locks"],
        source_refs=refs,
    )


def _size_of(lock: Mapping[str, Any]) -> Optional[float]:
    size = _first_number(_modifications(lock), _SIZE_KEYS)
    if size is None:
        size = _number(_snapshot(lock).get("suggested_size"))
    return size


def _position_size(locks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    sizes: List[float] = []
    refs: List[str] = []
    for lock in locks:
        if not _executed(lock) or str(lock.get("decision") or "") not in _ENTRY_ACTIONS:
            continue
        size = _size_of(lock)
        if size is None or size <= 0:
            continue
        sizes.append(size)
        refs.append(_lock_id(lock))
    dist = _distribution(sizes)
    return _feature(
        "position_size",
        value={**dist, "unit": "shares"} if dist else None,
        headline=_headline("median_size", dist["median"], "shares") if dist else None,
        sample_size=len(sizes),
        inputs=["decision_locks"],
        source_refs=refs,
    )


def _rejected_candidates(locks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rejections = [lock for lock in locks if _verdict(lock) == "reject"]
    accepted = [lock for lock in locks if _executed(lock)]
    value: Optional[Dict[str, Any]] = None
    low_share: Optional[float] = None
    if rejections:
        value = {"rejection_rate": _round(len(rejections) / len(locks))}
        liquidity_all = [x for x in (_first_number(_snapshot(l), _LIQUIDITY_KEYS) for l in locks) if x is not None]
        if liquidity_all:
            cutoff = _quantile(sorted(liquidity_all), 0.5)
            rejected_liq = [x for x in (_first_number(_snapshot(l), _LIQUIDITY_KEYS) for l in rejections) if x is not None]
            if rejected_liq:
                low_share = _round(sum(1 for x in rejected_liq if x < cutoff) / len(rejected_liq))
                value["low_liquidity_share"] = low_share
                value["liquidity_sample"] = len(rejected_liq)
        sectors = [s for s in (_first_text(_snapshot(l), _SECTOR_KEYS) for l in rejections) if s]
        if sectors:
            counts: Dict[str, int] = {}
            for sector in sectors:
                counts[sector] = counts.get(sector, 0) + 1
            top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            value["top_sector"] = top[0]
            value["top_sector_share"] = _round(top[1] / len(sectors))
            value["sector_sample"] = len(sectors)
        rejected_price = [x for x in (_first_number(_snapshot(l), _PRICE_KEYS) for l in rejections) if x is not None]
        accepted_price = [x for x in (_first_number(_snapshot(l), _PRICE_KEYS) for l in accepted) if x is not None]
        if rejected_price:
            value["median_price_rejected"] = _round(_quantile(sorted(rejected_price), 0.5))
        if accepted_price:
            value["median_price_accepted"] = _round(_quantile(sorted(accepted_price), 0.5))
    return _feature(
        "rejected_candidates",
        value=value,
        headline=_headline("rejection_rate_percent", _round(100.0 * value["rejection_rate"]), "percent") if value else None,
        sample_size=len(rejections),
        inputs=["decision_locks"],
        source_refs=[_lock_id(l) for l in rejections],
    )


def data_family_for(widget_or_page: str) -> Optional[str]:
    text = (widget_or_page or "").lower()
    for family, needles in _DATA_FAMILIES:
        if any(needle in text for needle in needles):
            return family
    return None


def _data_order(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    first_by_session: Dict[str, Tuple[str, str]] = {}
    for event in sorted(events, key=lambda e: (str(e.get("at") or ""), str(e.get("session_id") or ""))):
        session = str(event.get("session_id") or "")
        family = str(event.get("family") or "")
        if session and family and session not in first_by_session:
            first_by_session[session] = (family, str(event.get("ref") or session))
    value = None
    if first_by_session:
        counts: Dict[str, int] = {}
        for family, _ref in first_by_session.values():
            counts[family] = counts.get(family, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        value = {
            "first_family": top[0],
            "first_family_share": _round(top[1] / len(first_by_session)),
            "families": dict(sorted(counts.items())),
        }
    return _feature(
        "data_order",
        value=value,
        headline=_headline("first_family", value["first_family"], "text") if value else None,
        sample_size=len(first_by_session),
        inputs=["servant_session_widget_context"],
        source_refs=[ref for _f, ref in first_by_session.values()],
    )


def _intervention(locks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    interventions = [l for l in locks if str(l.get("evidence_kind") or "") == "intervention"]
    up = down = 0
    for lock in interventions:
        modified = _first_number(_modifications(lock), _SIZE_KEYS)
        suggested = _number(_snapshot(lock).get("suggested_size"))
        if modified is None or suggested is None or modified == suggested:
            continue
        if modified > suggested:
            up += 1
        else:
            down += 1
    value = None
    if locks:
        value = {"intervention_rate": _round(len(interventions) / len(locks)), "intervention_count": len(interventions)}
        if up + down:
            value["size_up_share"] = _round(up / (up + down))
            value["size_down_share"] = _round(down / (up + down))
            value["size_direction_sample"] = up + down
    return _feature(
        "intervention",
        value=value,
        headline=_headline("intervention_rate_percent", _round(100.0 * value["intervention_rate"]), "percent") if value else None,
        sample_size=len(locks),
        inputs=["decision_locks"],
        source_refs=[_lock_id(l) for l in interventions],
    )


# ---------------------------------------------------------------------------- profile


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def input_digest(
    locks: Sequence[Mapping[str, Any]],
    journal: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> str:
    material = {
        "locks": sorted((_lock_id(l), str(l.get("checksum") or "")) for l in locks),
        "journal": sorted(
            (str(e.get("id") or e.get("entry_id") or ""), str(e.get("updated_at") or e.get("updatedAt") or ""))
            for e in journal
        ),
        "events": sorted((str(e.get("session_id") or ""), str(e.get("at") or ""), str(e.get("family") or "")) for e in events),
    }
    return "sha256:" + hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def derive_habit_profile(
    *,
    tenant_id: str,
    user_id: str,
    strategy_id: Optional[str],
    locks: Sequence[Mapping[str, Any]],
    journal_entries: Sequence[Mapping[str, Any]] = (),
    interaction_events: Sequence[Mapping[str, Any]] = (),
    computed_at: str,
) -> Dict[str, Any]:
    """Derive the habit profile of one trader (and strategy) from the trader's own records.

    ``locks`` must already be scoped to the trader; foreign rows are dropped here as a second
    guard. The result is a pure function of the inputs: the same inputs give the same features
    and the same ``input_digest`` (``computed_at`` is metadata and is not an input).
    """
    scoped = [
        dict(lock)
        for lock in locks
        if str(lock.get("tenant_id")) == str(tenant_id)
        and str(lock.get("user_id")) == str(user_id)
        and (strategy_id in (None, "", ALL_STRATEGIES) or str(lock.get("strategy_id") or "") == str(strategy_id))
    ]
    scoped.sort(key=lambda l: (str(l.get("decision_time") or ""), _lock_id(l)))
    journal = [dict(e) for e in journal_entries if isinstance(e, Mapping)]
    events = [dict(e) for e in interaction_events if isinstance(e, Mapping)]
    features = [
        _holding_period(scoped, journal),
        _entry_timing(scoped),
        _rule_discipline(scoped),
        _position_size(scoped),
        _rejected_candidates(scoped),
        _data_order(events),
        _intervention(scoped),
    ]
    profile = {
        "schema": HABIT_PROFILE_SCHEMA,
        "profile_key": profile_key(tenant_id, user_id, strategy_id),
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "strategy_id": strategy_id or ALL_STRATEGIES,
        "computed_at": computed_at,
        "input_digest": input_digest(scoped, journal, events),
        "decision_count": len(scoped),
        "journal_count": len(journal),
        "min_sample": HABIT_MIN_SAMPLE,
        "derivation": {"method": HABIT_DERIVATION_METHOD, "language_model": "none"},
        "features": features,
    }
    return _fit_budget(profile)


def _fit_budget(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Keep one row near 4 KB: trim source refs first if a profile exceeds the hard cap."""
    for refs in (HABIT_SOURCE_REFS_PER_FEATURE, 2, 0):
        if len(_canonical(profile).encode("utf-8")) <= HABIT_PROFILE_MAX_BYTES:
            break
        for feature in profile["features"]:
            feature["source_refs"] = feature["source_refs"][-refs:] if refs else []
    return profile


def feature_by_key(profile: Mapping[str, Any], key: str) -> Optional[Mapping[str, Any]]:
    for feature in profile.get("features") or []:
        if isinstance(feature, Mapping) and feature.get("key") == key:
            return feature
    return None


__all__ = [
    "ALL_STRATEGIES",
    "FEATURE_KEYS",
    "FEATURE_LABELS",
    "HABIT_DERIVATION_METHOD",
    "HABIT_MIN_SAMPLE",
    "HABIT_PROFILE_HISTORY_VERSIONS",
    "HABIT_PROFILE_MAX_BYTES",
    "HABIT_PROFILE_SCHEMA",
    "data_family_for",
    "derive_habit_profile",
    "feature_by_key",
    "input_digest",
    "profile_key",
]
