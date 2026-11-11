"""Taiwan point-in-time evaluation for ``alpha_template.v1`` (Dev Plan §3.5).

Computes 1-, 5-, and 20-day time-series Information Coefficient (Spearman rank
correlation) per symbol and pooled across the governed Taiwan evaluation
universe, plus hit rate, turnover, IC decay, and redundancy against every
``active`` template (correlation > 0.9 marks ``duplicate_of``).

Stores evaluation results as a completed research run (``ResearchOrchestratorStore``)
and links the run ID from each template's ``evaluation_ref`` in ``RegistryService``.
Uses ``pandas`` as specified in Dev Plan §3.2/§3.5 because ``alphalens-reloaded``
is not installed in the locked research image.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.registry.alpha_template import (  # noqa: E402
    ALPHA_TEMPLATE_METADATA_KEY,
    AlphaTemplateValidationError,
    ensure_builtin_alpha_templates,
    load_library_templates,
    record_alpha_template_evaluation,
    validate_alpha_template,
)
from services.registry.factor_rule import (  # noqa: E402
    FactorRuleValidationError,
    _eval_node_series,
    _is_number,
    _parse_timestamp_utc,
    get_feature_definition,
    resolve_point_in_time_inputs,
)
from services.registry.models import ArtifactType  # noqa: E402
from services.registry.split_api import RegistryService  # noqa: E402

EVALUATION_HORIZONS: tuple[int, ...] = (1, 5, 20)
HORIZON_KEYS: tuple[str, ...] = ("1d", "5d", "20d")
REDUNDANCY_CORRELATION_THRESHOLD: float = 0.9
DEFAULT_TAIWAN_SYMBOLS: tuple[str, ...] = ("2330", "2317", "2454", "2308", "2881")
DEFAULT_TAIWAN_BAR_COUNT: int = 160

_SYMBOL_PROFILES: dict[str, dict[str, float]] = {
    "2330": {"base_price": 880.0, "base_volume": 38_000_000.0, "drift": 0.0014, "vol": 0.016, "phase": 0.15},
    "2317": {"base_price": 175.0, "base_volume": 62_000_000.0, "drift": 0.0010, "vol": 0.019, "phase": 0.85},
    "2454": {"base_price": 1240.0, "base_volume": 14_000_000.0, "drift": 0.0012, "vol": 0.021, "phase": 1.65},
    "2308": {"base_price": 390.0, "base_volume": 19_000_000.0, "drift": 0.0009, "vol": 0.015, "phase": 2.40},
    "2881": {"base_price": 82.0, "base_volume": 29_000_000.0, "drift": 0.0007, "vol": 0.012, "phase": 3.10},
}


SYNTHETIC_SOURCE_KIND = "synthetic_fixture"
GOVERNED_FILE_SOURCE_KIND = "governed_normalized_file"
CALLER_SUPPLIED_SOURCE_KIND = "caller_supplied"
MIN_GOVERNED_BAR_COUNT = 85


class TaiwanEvaluationDataError(ValueError):
    """Governed Taiwan evaluation data is missing, unreadable, empty, or not governed."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trading_dates_utc(count: int, *, start_date: str = "2025-01-02") -> list[str]:
    cur = datetime.fromisoformat(f"{start_date}T00:00:00+00:00")
    out: list[str] = []
    while len(out) < count:
        if cur.weekday() < 5:
            out.append(cur.isoformat().replace("+00:00", "Z"))
        cur += timedelta(days=1)
    return out


def build_taiwan_point_in_time_universe(
    symbols: Sequence[str] = DEFAULT_TAIWAN_SYMBOLS,
    *,
    bar_count: int = DEFAULT_TAIWAN_BAR_COUNT,
    start_date: str = "2025-01-02",
) -> dict[str, dict[str, Any]]:
    """Build a deterministic SYNTHETIC Taiwan-shaped daily dataset (test fixture only).

    The bars are generated from sin/cos/hash dynamics, NOT real market data. Entries
    are tagged ``source_kind=synthetic_fixture`` and are refused by
    ``run_alpha_template_evaluation`` / ``load_taiwan_evaluation_universe``.

    Every observation carries explicit ``event_time`` and ``available_time`` UTC
    timestamps on the governed ``TWSE_Normalized`` dataset (``open``, ``high``,
    ``low``, ``close``, ``volume``, ``vwap``).
    """
    if bar_count < 85:
        raise ValueError("bar_count must be at least 85 to cover 60-bar warm-up + 20-day forward horizon")
    dates = _trading_dates_utc(bar_count, start_date=start_date)
    universe: dict[str, dict[str, Any]] = {}

    for sym_idx, raw_sym in enumerate(symbols):
        symbol = str(raw_sym).strip()
        profile = _SYMBOL_PROFILES.get(
            symbol,
            {
                "base_price": 120.0 + 35.0 * sym_idx,
                "base_volume": 25_000_000.0,
                "drift": 0.0008,
                "vol": 0.017,
                "phase": 0.55 * (sym_idx + 1),
            },
        )
        price = float(profile["base_price"])
        base_vol = float(profile["base_volume"])
        drift = float(profile["drift"])
        vol = float(profile["vol"])
        phase = float(profile["phase"])

        bars: list[dict[str, Any]] = []
        features: dict[str, list[dict[str, Any]]] = {
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
            "vwap": [],
        }

        for t, ts in enumerate(dates):
            # Deterministic multi-frequency regime + mean-reversion + momentum dynamics
            cyc_fast = math.sin(0.37 * t + phase)
            cyc_med = math.cos(0.13 * t + 0.7 * phase)
            cyc_slow = math.sin(0.045 * t + 1.3 * phase)
            hash_jitter = (
                int(hashlib.sha256(f"{symbol}:{t}".encode("utf-8")).hexdigest()[:8], 16)
                / 0xFFFFFFFF
                - 0.5
            )
            ret = drift + vol * (0.48 * cyc_fast + 0.32 * cyc_med + 0.20 * cyc_slow + 0.25 * hash_jitter)
            prev_close = price
            close_px = max(5.0, round(prev_close * (1.0 + ret), 4))
            open_gap = 0.004 * math.cos(0.29 * t + phase) + 0.002 * hash_jitter
            open_px = max(5.0, round(prev_close * (1.0 + open_gap), 4))
            intraday_span = max(0.003, abs(ret) * 0.85 + 0.006 * (0.5 + 0.5 * abs(cyc_fast)))
            hi_base = max(open_px, close_px)
            lo_base = min(open_px, close_px)
            high_px = round(hi_base * (1.0 + 0.55 * intraday_span), 4)
            low_px = max(1.0, round(lo_base * (1.0 - 0.50 * intraday_span), 4))
            vwap_px = round((high_px + low_px + 2.0 * close_px + open_px) / 5.0, 4)
            vol_mult = 1.0 + 0.35 * math.sin(0.23 * t + phase) + 0.25 * abs(ret) / max(vol, 1e-4) + 0.12 * hash_jitter
            volume_val = round(max(100_000.0, base_vol * max(0.25, vol_mult)), 2)
            price = close_px

            bar = {
                "symbol": symbol,
                "date": ts,
                "event_time": ts,
                "available_time": ts,
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close_px,
                "volume": volume_val,
                "vwap": vwap_px,
            }
            bars.append(bar)
            for field_name in ("open", "high", "low", "close", "volume", "vwap"):
                features[field_name].append(
                    {
                        "value": float(bar[field_name]),
                        "event_time": ts,
                        "available_time": ts,
                    }
                )

        universe[symbol] = {
            "symbol": symbol,
            "dataset": "TWSE_Normalized",
            "source_kind": SYNTHETIC_SOURCE_KIND,
            "provenance": {"source_kind": SYNTHETIC_SOURCE_KIND, "governed": False},
            "decision_times": list(dates),
            "bars": bars,
            "features": features,
        }
    return universe


def _attach_zero_lag_cache(entry: dict[str, Any]) -> dict[str, Any]:
    """Pre-verify zero-lag alignment and resolve point-in-time series once per symbol."""
    decision_times = entry["decision_times"]
    features = entry["features"]
    n = len(decision_times)
    zero_lag_fields: dict[str, tuple[float, ...]] = {}
    if n > 0:
        final_dt = decision_times[-1]
        aligned_inputs: list[dict[str, Any]] = []
        for field_name, seq in features.items():
            if not isinstance(seq, Sequence) or len(seq) != n:
                continue
            if all(
                isinstance(item, Mapping)
                and item.get("event_time") == decision_times[idx]
                and item.get("available_time") == decision_times[idx]
                for idx, item in enumerate(seq)
            ):
                aligned_inputs.append(
                    {
                        "name": field_name,
                        "dataset": "TWSE_Normalized",
                        "field": field_name,
                        "availability_lag_days": 0,
                    }
                )
        if aligned_inputs:
            try:
                zero_lag_fields = resolve_point_in_time_inputs(
                    {"inputs": aligned_inputs},
                    None,
                    features=features,
                    decision_time=final_dt,
                )
            except FactorRuleValidationError:
                zero_lag_fields = {}
    entry["_zero_lag_fields"] = zero_lag_fields
    return entry


def _normalize_symbol_panel_entry(symbol: str, raw_entry: Any) -> dict[str, Any]:
    """Normalize caller-supplied symbol data into ``{symbol, decision_times, features, closes}``."""
    out = _normalize_symbol_panel_entry_core(symbol, raw_entry)
    if isinstance(raw_entry, Mapping):
        for key in ("source_kind", "provenance"):
            if key in raw_entry and key not in out:
                out[key] = raw_entry[key]
    return out


def _normalize_symbol_panel_entry_core(symbol: str, raw_entry: Any) -> dict[str, Any]:
    if isinstance(raw_entry, Mapping) and "_zero_lag_fields" in raw_entry and "decision_times" in raw_entry:
        return dict(raw_entry)

    if isinstance(raw_entry, Mapping) and "features" in raw_entry and "decision_times" in raw_entry:
        decision_times = [str(ts) for ts in raw_entry["decision_times"]]
        features = dict(raw_entry["features"])
        closes_raw = features.get("close", ())
        closes = [
            float(item["value"]) if isinstance(item, Mapping) else float(item[1] if isinstance(item, (tuple, list)) else item)
            for item in closes_raw
        ]
        return _attach_zero_lag_cache(
            {
                "symbol": symbol,
                "dataset": str(raw_entry.get("dataset") or "TWSE_Normalized"),
                "decision_times": decision_times,
                "features": features,
                "closes": closes,
            }
        )

    if isinstance(raw_entry, Sequence) and not isinstance(raw_entry, (str, bytes)):
        # Sequence of bar mappings
        bars = list(raw_entry)
        if not bars or not isinstance(bars[0], Mapping):
            raise ValueError(f"symbol {symbol!r} bar sequence must contain bar mappings")
        decision_times: list[str] = []
        features: dict[str, list[dict[str, Any]]] = {}
        closes: list[float] = []
        base_dt = datetime(2025, 1, 2, tzinfo=timezone.utc)
        for idx, bar in enumerate(bars):
            ev = str(
                bar.get("event_time")
                or bar.get("date")
                or bar.get("as_of")
                or (base_dt + timedelta(days=idx)).isoformat().replace("+00:00", "Z")
            )
            av = str(bar.get("available_time") or ev)
            decision_times.append(ev)
            closes.append(float(bar["close"]))
            for k, v in bar.items():
                if k in {"symbol", "date", "event_time", "available_time", "as_of"}:
                    continue
                if _is_number(v):
                    features.setdefault(k, []).append(
                        {
                            "value": float(v),
                            "event_time": ev,
                            "available_time": av,
                        }
                    )
                elif isinstance(v, Mapping) and _is_number(v.get("value")):
                    features.setdefault(k, []).append(
                        {
                            "value": float(v["value"]),
                            "event_time": str(v.get("event_time") or ev),
                            "available_time": str(v.get("available_time") or av),
                        }
                    )
        return _attach_zero_lag_cache(
            {
                "symbol": symbol,
                "dataset": "TWSE_Normalized",
                "decision_times": decision_times,
                "features": features,
                "closes": closes,
            }
        )

    if isinstance(raw_entry, Mapping):
        # Mapping of field names -> sequences of numbers or observation dicts
        raw_map = dict(raw_entry)
        explicit_dts = raw_map.pop("decision_times", None)
        close_seq = raw_map.get("close")
        if close_seq is None:
            raise ValueError(f"symbol {symbol!r} feature mapping must include 'close'")
        n = len(close_seq)
        if explicit_dts is not None:
            decision_times = [str(ts) for ts in explicit_dts]
        else:
            decision_times = []
            base_dt = datetime(2025, 1, 2, tzinfo=timezone.utc)
            for idx, item in enumerate(close_seq):
                if isinstance(item, Mapping) and (item.get("event_time") or item.get("date") or item.get("available_time")):
                    decision_times.append(
                        str(item.get("event_time") or item.get("date") or item.get("available_time"))
                    )
                elif isinstance(item, (tuple, list)) and len(item) == 2:
                    decision_times.append(str(item[0]))
                else:
                    decision_times.append((base_dt + timedelta(days=idx)).isoformat().replace("+00:00", "Z"))

        features = {}
        for k, seq in raw_map.items():
            if not isinstance(seq, Sequence) or isinstance(seq, (str, bytes)):
                continue
            norm_seq: list[Any] = []
            for idx, item in enumerate(seq):
                default_ts = decision_times[min(idx, len(decision_times) - 1)]
                if _is_number(item):
                    norm_seq.append(
                        {
                            "value": float(item),
                            "event_time": default_ts,
                            "available_time": default_ts,
                        }
                    )
                elif isinstance(item, Mapping):
                    norm_seq.append(
                        {
                            "value": float(item["value"]),
                            "event_time": str(item.get("event_time") or item.get("date") or default_ts),
                            "available_time": str(
                                item.get("available_time")
                                or item.get("event_time")
                                or item.get("date")
                                or default_ts
                            ),
                        }
                    )
                elif isinstance(item, (tuple, list)) and len(item) == 2:
                    norm_seq.append(
                        {
                            "value": float(item[1]),
                            "event_time": str(item[0]),
                            "available_time": str(item[0]),
                        }
                    )
                else:
                    raise ValueError(f"unsupported observation in {symbol}.{k}[{idx}]")
            features[k] = norm_seq

        closes = [
            float(item["value"]) if isinstance(item, Mapping) else float(item)
            for item in features["close"]
        ]
        return _attach_zero_lag_cache(
            {
                "symbol": symbol,
                "dataset": "TWSE_Normalized",
                "decision_times": decision_times,
                "features": features,
                "closes": closes,
            }
        )

    raise ValueError(f"unsupported panel entry for symbol {symbol!r}")


def _load_governed_file_universe(
    data_dir: Path, symbols: Sequence[str]
) -> dict[str, dict[str, Any]]:
    if not data_dir.is_dir():
        raise TaiwanEvaluationDataError(
            f"governed Taiwan evaluation data directory is missing or not a directory: {data_dir}"
        )
    loaded: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for sym in symbols:
        sym = str(sym)
        candidate = data_dir / f"{sym}.json"
        if not candidate.is_file():
            missing.append(sym)
            continue
        try:
            raw = candidate.read_bytes()
            doc = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise TaiwanEvaluationDataError(
                f"governed Taiwan data for {sym} is unreadable: {candidate}: {exc}"
            ) from exc
        if isinstance(doc, Mapping) and (
            doc.get("source_kind") == SYNTHETIC_SOURCE_KIND
            or (doc.get("provenance") or {}).get("source_kind") == SYNTHETIC_SOURCE_KIND
        ):
            raise TaiwanEvaluationDataError(f"{candidate} is a synthetic fixture, not governed data")
        bars = doc.get("bars") if isinstance(doc, Mapping) else doc
        if not isinstance(bars, Sequence) or isinstance(bars, (str, bytes)) or len(bars) < MIN_GOVERNED_BAR_COUNT:
            raise TaiwanEvaluationDataError(
                f"governed Taiwan data for {sym} must contain at least {MIN_GOVERNED_BAR_COUNT} bars: {candidate}"
            )
        try:
            entry = _normalize_symbol_panel_entry(sym, bars)
        except (ValueError, KeyError, TypeError) as exc:
            raise TaiwanEvaluationDataError(f"governed Taiwan data for {sym} is invalid: {exc}") from exc
        decision_times = entry["decision_times"]
        entry["source_kind"] = GOVERNED_FILE_SOURCE_KIND
        entry["provenance"] = {
            "source_kind": GOVERNED_FILE_SOURCE_KIND,
            "governed": True,
            "path": str(candidate),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bar_count": len(decision_times),
            "first_event_time": decision_times[0],
            "last_event_time": decision_times[-1],
        }
        loaded[sym] = entry
    if missing:
        raise TaiwanEvaluationDataError(
            f"governed Taiwan data missing for symbols {missing} in {data_dir}"
        )
    if not loaded:
        raise TaiwanEvaluationDataError(f"governed Taiwan data directory is empty: {data_dir}")
    return loaded


def load_taiwan_evaluation_universe(
    market_data: Mapping[str, Any] | None = None,
    *,
    symbols: Sequence[str] = DEFAULT_TAIWAN_SYMBOLS,
    normalized_data_dir: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load the governed Taiwan point-in-time evaluation universe.

    Fails closed (``TaiwanEvaluationDataError``) when no source is configured or
    the configured directory is missing, empty, unreadable, or incomplete. It never
    falls back to synthetic data; use ``build_taiwan_point_in_time_universe`` and pass
    the result explicitly as ``market_data`` for test fixtures.
    """
    if market_data is not None:
        if not market_data:
            raise TaiwanEvaluationDataError("supplied market_data is empty")
        out: dict[str, dict[str, Any]] = {}
        for sym, entry in market_data.items():
            norm = _normalize_symbol_panel_entry(str(sym), entry)
            norm.setdefault("source_kind", CALLER_SUPPLIED_SOURCE_KIND)
            out[str(sym)] = norm
        return out

    data_dir_raw = str(normalized_data_dir or os.getenv("PANTHEON_TAIWAN_EVAL_DATA_DIR", "")).strip()
    if not data_dir_raw:
        raise TaiwanEvaluationDataError(
            "no governed Taiwan evaluation data source configured "
            "(set PANTHEON_TAIWAN_EVAL_DATA_DIR or supply market_data)"
        )
    return _load_governed_file_universe(Path(data_dir_raw), symbols)


def _verify_governed_file_provenance(
    sym: str, entry: Mapping[str, Any], prov: Mapping[str, Any]
) -> dict[str, Any]:
    """Re-verify a governed-file claim against the file on disk; never trust the label."""
    path = str(prov.get("path") or "").strip()
    claimed_sha = str(prov.get("sha256") or "").strip()
    if (
        prov.get("source_kind") != GOVERNED_FILE_SOURCE_KIND
        or not path
        or not claimed_sha
    ):
        raise TaiwanEvaluationDataError(
            f"symbol {sym} claims governed_normalized_file without verifiable snapshot provenance"
        )
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise TaiwanEvaluationDataError(
            f"symbol {sym} governed file provenance unverifiable: {path}: {exc}"
        ) from exc
    if hashlib.sha256(raw).hexdigest() != claimed_sha:
        raise TaiwanEvaluationDataError(
            f"symbol {sym} governed file sha256 does not match provenance: {path}"
        )
    if int(prov.get("bar_count") or -1) != len(entry.get("decision_times") or ()):
        raise TaiwanEvaluationDataError(
            f"symbol {sym} governed provenance bar_count does not match supplied data"
        )
    return dict(prov)


def summarize_universe_provenance(universe: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Return run-level source/snapshot provenance; raise unless the data is governed."""
    if not universe:
        raise TaiwanEvaluationDataError("evaluation universe is empty")
    per_symbol: dict[str, Any] = {}
    for sym in sorted(universe):
        entry = universe[sym]
        kind = entry.get("source_kind")
        prov = entry.get("provenance") if isinstance(entry.get("provenance"), Mapping) else {}
        if kind == SYNTHETIC_SOURCE_KIND or prov.get("source_kind") == SYNTHETIC_SOURCE_KIND:
            raise TaiwanEvaluationDataError(
                f"symbol {sym} is synthetic fixture data; governed evaluation refuses it"
            )
        if kind == GOVERNED_FILE_SOURCE_KIND:
            per_symbol[sym] = _verify_governed_file_provenance(sym, entry, prov)
            continue
        snapshot = str(prov.get("snapshot_id") or prov.get("sha256") or "").strip()
        source = str(prov.get("source") or prov.get("source_ref") or "").strip()
        if not snapshot or not source:
            raise TaiwanEvaluationDataError(
                f"caller-supplied data for {sym} lacks provenance (source and snapshot_id required)"
            )
        per_symbol[sym] = {
            "source_kind": CALLER_SUPPLIED_SOURCE_KIND,
            "governed": True,
            "source": source,
            "snapshot_id": snapshot,
            "bar_count": len(entry.get("decision_times") or ()),
        }
    digest = hashlib.sha256(
        json.dumps(per_symbol, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return {
        "governed": True,
        "dataset": "TWSE_Normalized",
        "snapshot_id": f"sha256:{digest}",
        "symbols": per_symbol,
    }


def _resolve_fast_zero_lag_env(
    record: Mapping[str, Any],
    symbol_entry: Mapping[str, Any],
) -> dict[str, tuple[float, ...]] | None:
    """Return pre-resolved point-in-time env iff every declared input is zero-lag and aligned."""
    zero_lag_fields = symbol_entry.get("_zero_lag_fields") or {}
    if not zero_lag_fields:
        return None
    out: dict[str, tuple[float, ...]] = {}
    for inp in record["inputs"]:
        lag_days = int(inp.get("availability_lag_days", 0))
        cat_def = get_feature_definition(inp["dataset"], inp["field"])
        if cat_def is not None:
            lag_days = max(lag_days, cat_def.min_availability_lag_days)
        if lag_days > 0:
            return None
        series = (
            zero_lag_fields.get(inp["name"])
            or zero_lag_fields.get(f"{inp['dataset']}.{inp['field']}")
            or zero_lag_fields.get(inp["field"])
        )
        if series is None:
            return None
        out[inp["name"]] = series
    return out


def evaluate_template_point_in_time_symbol(
    template: Mapping[str, Any],
    symbol_data: Any,
    *,
    symbol: str = "2330",
    parameters: Mapping[str, Any] | None = None,
    decision_times: Sequence[str] | None = None,
    strict_stepwise: bool = False,
    _validated_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a ``time_series`` AlphaTemplate on a single symbol strictly point-in-time.

    At each bar index ``t`` with decision timestamp ``d_t``, only observations
    satisfying ``effective_available_time <= d_t`` are visible.
    """
    if template.get("scope") != "time_series":
        raise AlphaTemplateValidationError(
            f"template {template.get('template_id')!r} has scope {template.get('scope')!r}; "
            "per-symbol point-in-time evaluator refuses cross-sectional templates"
        )
    record = _validated_record if _validated_record is not None else validate_alpha_template(template)
    params = dict(record["parameters"])
    if parameters:
        params.update(parameters)

    norm = _normalize_symbol_panel_entry(symbol, symbol_data)
    eval_dts = list(decision_times) if decision_times is not None else list(norm["decision_times"])
    features = norm["features"]

    fast_pit_env = (
        _resolve_fast_zero_lag_env(record, norm)
        if (not strict_stepwise and decision_times is None)
        else None
    )
    if fast_pit_env is not None:
        series, _ = _eval_node_series(record["expression"], fast_pit_env, params)
        if len(series) < len(eval_dts):
            series = ([math.nan] * (len(eval_dts) - len(series))) + list(series)
        elif len(series) > len(eval_dts):
            series = list(series[-len(eval_dts) :])
    else:
        # Step-by-step point-in-time evaluation at each decision_time d_t
        series = []
        for dt in eval_dts:
            try:
                pit_env = resolve_point_in_time_inputs(
                    {"inputs": record["inputs"]},
                    None,
                    features=features,
                    decision_time=dt,
                )
                step_series, _ = _eval_node_series(record["expression"], pit_env, params)
                val = float(step_series[-1]) if step_series and math.isfinite(step_series[-1]) else math.nan
            except FactorRuleValidationError:
                val = math.nan
            series.append(val)

    # Also resolve visible closes at each decision_time for forward return pairing
    close_obs = features.get("close", ())
    closes_at_dts: list[float] = []
    if len(close_obs) == len(eval_dts) and decision_times is None:
        closes_at_dts = list(norm["closes"])
    else:
        for dt in eval_dts:
            dt_parsed = _parse_timestamp_utc(dt, is_decision_time=True)
            visible_closes = [
                float(item["value"])
                for item in close_obs
                if _parse_timestamp_utc(
                    item.get("available_time") or item.get("event_time"),
                    is_decision_time=False,
                )
                <= dt_parsed
            ]
            closes_at_dts.append(visible_closes[-1] if visible_closes else math.nan)

    return {
        "symbol": symbol,
        "decision_times": eval_dts,
        "factor_values": series,
        "closes": closes_at_dts,
    }


def evaluate_template_point_in_time_panel(
    template: Mapping[str, Any],
    universe: Mapping[str, Any],
    *,
    parameters: Mapping[str, Any] | None = None,
    strict_stepwise: bool = False,
) -> dict[str, dict[str, Any]]:
    """Evaluate a ``time_series`` AlphaTemplate point-in-time across a multi-symbol universe."""
    if template.get("scope") != "time_series":
        raise AlphaTemplateValidationError(
            f"template {template.get('template_id')!r} has scope {template.get('scope')!r}; "
            "per-symbol point-in-time evaluator refuses cross-sectional templates"
        )
    validated_record = validate_alpha_template(template)
    panel: dict[str, dict[str, Any]] = {}
    for symbol, sym_data in universe.items():
        panel[str(symbol)] = evaluate_template_point_in_time_symbol(
            validated_record,
            sym_data,
            symbol=str(symbol),
            parameters=parameters,
            strict_stepwise=strict_stepwise,
            _validated_record=validated_record,
        )
    return panel


def _spearman_ic_pandas(factors: Sequence[float], forward_returns: Sequence[float]) -> float:
    """Compute Spearman rank correlation using ``pandas.Series`` on finite pairs."""
    df = pd.DataFrame({"factor": list(factors), "ret": list(forward_returns)})
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    if len(df) < 3:
        return 0.0
    f_rank = df["factor"].rank(method="average")
    r_rank = df["ret"].rank(method="average")
    if float(f_rank.std(ddof=0)) <= 1e-12 or float(r_rank.std(ddof=0)) <= 1e-12:
        return 0.0
    corr = float(f_rank.corr(r_rank, method="pearson"))
    if not math.isfinite(corr):
        return 0.0
    return round(max(-1.0, min(1.0, corr)), 6)


def _compute_hit_rate_pandas(factors: Sequence[float], forward_returns: Sequence[float]) -> float:
    """Compute directional hit rate in ``[0, 1]`` using ``pandas``."""
    df = pd.DataFrame({"factor": list(factors), "ret": list(forward_returns)})
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    if len(df) == 0:
        return 0.0
    f_ser = df["factor"]
    r_ser = df["ret"]
    # If factor is strictly one-sided (e.g. volatility or price ratio > 0), center around median
    if float(f_ser.min()) >= 0.0 or float(f_ser.max()) <= 0.0:
        f_centered = f_ser - float(f_ser.median())
    else:
        f_centered = f_ser
    active = f_centered.abs() > 1e-12
    if int(active.sum()) == 0:
        return 0.5
    f_sign = np.sign(f_centered[active])
    r_sign = np.sign(r_ser[active])
    hits = (f_sign == r_sign).astype(float)
    return round(float(hits.mean()), 6)


def _compute_symbol_turnover_pandas(
    factors: Sequence[float],
) -> tuple[dict[str, float], float]:
    """Compute rank-weight turnover across horizons and 1-day rank autocorrelation."""
    ser = pd.Series(list(factors), dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    if len(ser) < 3 or float(ser.std(ddof=0)) <= 1e-12:
        return {f"{h}d": 0.0 for h in EVALUATION_HORIZONS}, 1.0
    # Rank-normalized signal weight in [-1, 1]
    weights = ser.rank(pct=True, method="average") * 2.0 - 1.0
    turnover_by_h: dict[str, float] = {}
    for h in EVALUATION_HORIZONS:
        if len(weights) <= h:
            turnover_by_h[f"{h}d"] = 0.0
            continue
        delta = (weights - weights.shift(h)).dropna().abs() * 0.5
        val = float(delta.mean()) if len(delta) > 0 else 0.0
        turnover_by_h[f"{h}d"] = round(max(0.0, min(1.0, val)), 6)

    lag1 = weights.shift(1)
    valid = pd.DataFrame({"w": weights, "l1": lag1}).dropna()
    if len(valid) >= 3 and float(valid["w"].std(ddof=0)) > 1e-12 and float(valid["l1"].std(ddof=0)) > 1e-12:
        ac1 = float(valid["w"].corr(valid["l1"], method="pearson"))
        ac1 = round(max(-1.0, min(1.0, ac1)), 6) if math.isfinite(ac1) else 0.0
    else:
        ac1 = 0.0
    return turnover_by_h, ac1


def _compute_forward_returns(closes: Sequence[float], horizon: int) -> list[float]:
    n = len(closes)
    out = [math.nan] * n
    for t in range(n - horizon):
        c_now = closes[t]
        c_fut = closes[t + horizon]
        if math.isfinite(c_now) and math.isfinite(c_fut) and abs(c_now) > 1e-12:
            out[t] = (c_fut - c_now) / c_now
    return out


def compute_template_evaluation_metrics(
    panel: Mapping[str, Mapping[str, Any]],
    *,
    horizons: Sequence[int] = EVALUATION_HORIZONS,
) -> dict[str, Any]:
    """Compute per-symbol and pooled 1d/5d/20d Spearman IC, hit rate, turnover, and IC decay."""
    per_symbol_ic: dict[str, dict[str, float]] = {}
    per_symbol: dict[str, dict[str, Any]] = {}
    pooled_factors_by_h: dict[int, list[float]] = {h: [] for h in horizons}
    pooled_returns_by_h: dict[int, list[float]] = {h: [] for h in horizons}
    pooled_centered_factors_by_h: dict[int, list[float]] = {h: [] for h in horizons}
    turnover_1d_list: list[float] = []
    turnover_by_h_lists: dict[str, list[float]] = {f"{h}d": [] for h in horizons}
    autocorr_list: list[float] = []
    total_valid_bars = 0

    for symbol in sorted(panel.keys()):
        sym_entry = panel[symbol]
        factors = [float(x) for x in sym_entry["factor_values"]]
        closes = [float(x) for x in sym_entry["closes"]]
        finite_f = [x for x in factors if math.isfinite(x)]
        total_valid_bars += len(finite_f)

        # Within-symbol rank-standardized factor for pooled cross-symbol comparability
        f_ser = pd.Series(factors, dtype="float64").replace([np.inf, -np.inf], np.nan)
        if int(f_ser.notna().sum()) >= 3 and float(f_ser.std(ddof=0)) > 1e-12:
            f_norm_list = (f_ser.rank(pct=True, method="average") * 2.0 - 1.0).tolist()
        else:
            f_norm_list = factors

        sym_ic: dict[str, float] = {}
        sym_hit: dict[str, float] = {}
        for h in horizons:
            h_key = f"{h}d"
            fwd_ret = _compute_forward_returns(closes, h)
            ic_val = _spearman_ic_pandas(factors, fwd_ret)
            hit_val = _compute_hit_rate_pandas(factors, fwd_ret)
            sym_ic[h_key] = ic_val
            sym_hit[h_key] = hit_val
            for f_raw, f_norm, r_val in zip(factors, f_norm_list, fwd_ret):
                if math.isfinite(f_raw) and math.isfinite(r_val):
                    pooled_factors_by_h[h].append(
                        float(f_norm) if math.isfinite(f_norm) else float(f_raw)
                    )
                    pooled_returns_by_h[h].append(float(r_val))
                    pooled_centered_factors_by_h[h].append(
                        float(f_norm) if math.isfinite(f_norm) else float(f_raw)
                    )

        sym_turnover_by_h, sym_ac1 = _compute_symbol_turnover_pandas(factors)
        turnover_1d_list.append(sym_turnover_by_h.get("1d", 0.0))
        for h_key, t_val in sym_turnover_by_h.items():
            if h_key in turnover_by_h_lists:
                turnover_by_h_lists[h_key].append(t_val)
        autocorr_list.append(sym_ac1)

        per_symbol_ic[symbol] = sym_ic
        per_symbol[symbol] = {
            "ic": sym_ic,
            "hit_rate": sym_hit["1d"],
            "hit_rate_by_horizon": sym_hit,
            "turnover": sym_turnover_by_h.get("1d", 0.0),
            "turnover_by_horizon": sym_turnover_by_h,
            "autocorrelation_1d": sym_ac1,
            "valid_bars": len(finite_f),
        }

    pooled_ic: dict[str, float] = {}
    mean_symbol_ic: dict[str, float] = {}
    hit_rate_by_horizon: dict[str, float] = {}
    for h in horizons:
        h_key = f"{h}d"
        pooled_ic[h_key] = _spearman_ic_pandas(
            pooled_factors_by_h[h], pooled_returns_by_h[h]
        )
        sym_vals = [per_symbol_ic[s][h_key] for s in sorted(per_symbol_ic)]
        mean_symbol_ic[h_key] = round(float(np.mean(sym_vals)) if sym_vals else 0.0, 6)
        hit_rate_by_horizon[h_key] = _compute_hit_rate_pandas(
            pooled_centered_factors_by_h[h], pooled_returns_by_h[h]
        )

    turnover_by_horizon = {
        h_key: round(float(np.mean(vals)) if vals else 0.0, 6)
        for h_key, vals in turnover_by_h_lists.items()
    }
    overall_turnover = turnover_by_horizon.get("1d", 0.0)
    overall_hit_rate = hit_rate_by_horizon.get("1d", 0.0)
    mean_ac1 = round(float(np.mean(autocorr_list)) if autocorr_list else 0.0, 6)

    ic_1d = pooled_ic.get("1d", 0.0)
    ic_5d = pooled_ic.get("5d", 0.0)
    ic_20d = pooled_ic.get("20d", 0.0)
    abs_1d = abs(ic_1d)
    abs_20d = abs(ic_20d)
    decay_ratio = round(ic_20d / ic_1d, 6) if abs_1d > 1e-6 else 0.0
    # OLS slope of IC across horizons (1, 5, 20)
    x_h = np.array([1.0, 5.0, 20.0], dtype="float64")
    y_ic = np.array([ic_1d, ic_5d, ic_20d], dtype="float64")
    slope_per_day = round(float(np.cov(x_h, y_ic, bias=True)[0, 1] / np.var(x_h)), 6)
    if abs_1d > abs_20d and abs_20d > 1e-6:
        rate = math.log(abs_1d / abs_20d) / 19.0
        half_life_days: float | None = round(math.log(2.0) / rate, 4) if rate > 1e-9 else None
    else:
        half_life_days = None

    decay_summary = {
        "1d": ic_1d,
        "5d": ic_5d,
        "20d": ic_20d,
        "ic_change_1d_to_5d": round(ic_5d - ic_1d, 6),
        "ic_change_1d_to_20d": round(ic_20d - ic_1d, 6),
        "abs_ic_decay_1d_to_20d": round(abs_1d - abs_20d, 6),
        "decay_ratio_20d_over_1d": decay_ratio,
        "slope_per_day": slope_per_day,
        "half_life_days": half_life_days,
    }

    return {
        "horizons": list(horizons),
        "per_symbol_ic": per_symbol_ic,
        "per_symbol": per_symbol,
        "pooled_ic": pooled_ic,
        "mean_symbol_ic": mean_symbol_ic,
        "ic_1d": ic_1d,
        "ic_5d": ic_5d,
        "ic_20d": ic_20d,
        "hit_rate": overall_hit_rate,
        "hit_rate_by_horizon": hit_rate_by_horizon,
        "turnover": overall_turnover,
        "turnover_by_horizon": turnover_by_horizon,
        "autocorrelation_1d": mean_ac1,
        "decay": decay_summary,
        "ic_decay": decay_summary,
        "valid_observations": total_valid_bars,
    }


def _panel_correlation(
    panel_a: Mapping[str, Mapping[str, Any]],
    panel_b: Mapping[str, Mapping[str, Any]],
) -> float:
    """Compute time-series correlation between two evaluated factor panels across symbols."""
    common_symbols = sorted(set(panel_a.keys()) & set(panel_b.keys()))
    if not common_symbols:
        return 0.0

    weighted_pearson_num = 0.0
    weighted_spearman_num = 0.0
    total_weight = 0

    for sym in common_symbols:
        fa = panel_a[sym]["factor_values"]
        fb = panel_b[sym]["factor_values"]
        n = min(len(fa), len(fb))
        if n < 3:
            continue
        df = pd.DataFrame({"a": list(fa[-n:]), "b": list(fb[-n:])})
        df = df.replace([np.inf, -np.inf], np.nan).dropna()
        m = len(df)
        if m < 3:
            continue
        std_a = float(df["a"].std(ddof=0))
        std_b = float(df["b"].std(ddof=0))
        if std_a <= 1e-12 or std_b <= 1e-12:
            continue
        p_corr = float(df["a"].corr(df["b"], method="pearson"))
        s_corr = float(
            df["a"].rank(method="average").corr(df["b"].rank(method="average"), method="pearson")
        )
        if math.isfinite(p_corr):
            weighted_pearson_num += m * p_corr
        if math.isfinite(s_corr):
            weighted_spearman_num += m * s_corr
        total_weight += m

    if total_weight == 0:
        return 0.0
    mean_pearson = weighted_pearson_num / total_weight
    mean_spearman = weighted_spearman_num / total_weight
    best = max(mean_pearson, mean_spearman)
    return round(max(-1.0, min(1.0, best)), 6)


def compute_active_template_redundancy(
    template_id: str,
    candidate_panel: Mapping[str, Mapping[str, Any]],
    active_panels: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *,
    threshold: float = REDUNDANCY_CORRELATION_THRESHOLD,
    candidate_status: str = "draft",
) -> dict[str, Any]:
    """Compare ``candidate_panel`` against every ``active`` template panel.

    If correlation with any active template (excluding ``template_id`` itself)
    exceeds ``threshold`` (default ``0.9``), marks ``duplicate_of`` with the
    highest-correlated active template ID.
    """
    correlations: dict[str, float] = {}
    best_active_id: str | None = None
    best_corr: float | None = None

    for active_id in sorted(active_panels.keys()):
        if active_id == template_id:
            continue
        corr = _panel_correlation(candidate_panel, active_panels[active_id])
        correlations[active_id] = corr
        if best_corr is None or corr > best_corr:
            best_corr = corr
            best_active_id = active_id

    duplicate_of: str | None = None
    if (
        candidate_status != "active"
        and best_active_id is not None
        and best_corr is not None
        and best_corr > threshold
    ):
        duplicate_of = best_active_id

    return {
        "active_correlations": correlations,
        "max_active_correlation": best_corr,
        "duplicate_of": duplicate_of,
        "redundancy_threshold": threshold,
    }


def evaluate_single_alpha_template(
    template: Mapping[str, Any],
    universe: Mapping[str, Any] | None = None,
    *,
    active_templates: Sequence[Mapping[str, Any]] = (),
    active_panels: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    parameters: Mapping[str, Any] | None = None,
    strict_stepwise: bool = False,
    redundancy_threshold: float = REDUNDANCY_CORRELATION_THRESHOLD,
) -> dict[str, Any]:
    """Evaluate a single ``time_series`` AlphaTemplate on Taiwan point-in-time data."""
    eval_universe = load_taiwan_evaluation_universe(universe)
    record = validate_alpha_template(template)
    template_id = record["template_id"]
    panel = evaluate_template_point_in_time_panel(
        record,
        eval_universe,
        parameters=parameters,
        strict_stepwise=strict_stepwise,
    )
    metrics = compute_template_evaluation_metrics(panel)

    resolved_active_panels: dict[str, Mapping[str, Mapping[str, Any]]] = (
        dict(active_panels) if active_panels is not None else {}
    )
    for act_tmpl in active_templates:
        if act_tmpl.get("scope") != "time_series":
            continue
        act_id = str(act_tmpl["template_id"])
        if act_id not in resolved_active_panels:
            resolved_active_panels[act_id] = evaluate_template_point_in_time_panel(
                act_tmpl,
                eval_universe,
                strict_stepwise=strict_stepwise,
            )

    redundancy = compute_active_template_redundancy(
        template_id,
        panel,
        resolved_active_panels,
        threshold=redundancy_threshold,
        candidate_status=str(record.get("status", "draft")),
    )

    return {
        "template_id": template_id,
        "name": record["name"],
        "family": record["family"],
        "scope": record["scope"],
        "status": "evaluated" if record["status"] == "draft" else record["status"],
        "duplicate_of": redundancy["duplicate_of"],
        "active_correlations": redundancy["active_correlations"],
        "max_active_correlation": redundancy["max_active_correlation"],
        **metrics,
    }


def evaluate_alpha_template_library(
    templates: Sequence[Mapping[str, Any]] | None = None,
    *,
    universe: Mapping[str, Any] | None = None,
    include_cross_sectional: bool = False,
    strict_stepwise: bool = False,
    redundancy_threshold: float = REDUNDANCY_CORRELATION_THRESHOLD,
) -> dict[str, Any]:
    """Evaluate a collection of AlphaTemplates (default: full checked-in seed library)."""
    eval_universe = load_taiwan_evaluation_universe(universe)
    raw_templates = list(templates) if templates is not None else load_library_templates()
    validated_templates = [validate_alpha_template(t) for t in raw_templates]

    # Step 1: Evaluate point-in-time panels for all time_series templates
    panels: dict[str, dict[str, dict[str, Any]]] = {}
    skipped_cross_sectional: list[str] = []

    for record in validated_templates:
        tid = record["template_id"]
        if record["scope"] != "time_series":
            if include_cross_sectional:
                raise AlphaTemplateValidationError(
                    f"template {tid!r} has scope {record['scope']!r}; per-symbol evaluator refuses cross-sectional templates"
                )
            skipped_cross_sectional.append(tid)
            continue
        panels[tid] = evaluate_template_point_in_time_panel(
            record,
            eval_universe,
            strict_stepwise=strict_stepwise,
        )

    # Step 2: Identify active time_series templates for redundancy check
    active_panels: dict[str, Mapping[str, Mapping[str, Any]]] = {
        record["template_id"]: panels[record["template_id"]]
        for record in validated_templates
        if record["scope"] == "time_series"
        and record.get("status") == "active"
        and record["template_id"] in panels
    }

    # Step 3: Compute metrics and active-template redundancy for each time_series template
    evaluations: dict[str, dict[str, Any]] = {}
    duplicates_detected: dict[str, str] = {}

    for record in validated_templates:
        tid = record["template_id"]
        if record["scope"] != "time_series":
            continue
        panel = panels[tid]
        metrics = compute_template_evaluation_metrics(panel)
        redundancy = compute_active_template_redundancy(
            tid,
            panel,
            active_panels,
            threshold=redundancy_threshold,
            candidate_status=str(record.get("status", "draft")),
        )
        dup_of = redundancy["duplicate_of"]
        if dup_of is not None:
            duplicates_detected[tid] = dup_of

        evaluations[tid] = {
            "template_id": tid,
            "name": record["name"],
            "family": record["family"],
            "scope": record["scope"],
            "status": "evaluated" if record["status"] == "draft" else record["status"],
            "duplicate_of": dup_of,
            "active_correlations": redundancy["active_correlations"],
            "max_active_correlation": redundancy["max_active_correlation"],
            **metrics,
        }

    return {
        "symbols": sorted(eval_universe.keys()),
        "horizons": list(EVALUATION_HORIZONS),
        "evaluated_count": len(evaluations),
        "skipped_cross_sectional_count": len(skipped_cross_sectional),
        "skipped_cross_sectional_ids": skipped_cross_sectional,
        "active_template_ids": sorted(active_panels.keys()),
        "duplicates_detected": duplicates_detected,
        "evaluations": evaluations,
    }


def run_alpha_template_evaluation(
    *,
    research_store: Any,
    registry_service: RegistryService | None = None,
    template_ids: Sequence[str] | None = None,
    templates: Sequence[Mapping[str, Any]] | None = None,
    universe: Mapping[str, Any] | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    strict_stepwise: bool = False,
    redundancy_threshold: float = REDUNDANCY_CORRELATION_THRESHOLD,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run Taiwan point-in-time evaluation, persist a research run, and link ``evaluation_ref``.

    1. Loads templates from ``registry_service`` (ensuring built-in templates are
       registered when ``registry_service`` is provided) or ``templates``.
    2. Evaluates all selected ``time_series`` templates (and all ``active``
       templates for redundancy comparison) on governed Taiwan point-in-time data.
    3. Stores the research task, completed research run (``rrun-...``), and
       evaluation artifact (``rart-...``) in ``research_store``.
    4. Updates each evaluated template in ``registry_service`` so
       ``template.evaluation_ref == run_id``, ``template.status == "evaluated"``
       (for draft templates), and ``template.duplicate_of`` is set whenever
       correlation with an ``active`` template exceeds ``0.9``.
    """
    if registry_service is not None and templates is None:
        ensure_builtin_alpha_templates(registry_service)
        entries = [
            v
            for v in registry_service.list_all_entries()
            if v.entry.artifact_type == ArtifactType.ALPHA_TEMPLATE
        ]
        all_registry_templates: list[dict[str, Any]] = []
        for view in entries:
            meta_tmpl = (view.entry.metadata or {}).get(ALPHA_TEMPLATE_METADATA_KEY)
            if isinstance(meta_tmpl, Mapping):
                all_registry_templates.append(copy.deepcopy(dict(meta_tmpl)))
        source_templates = all_registry_templates
    elif templates is not None:
        source_templates = [copy.deepcopy(dict(t)) for t in templates]
    else:
        source_templates = load_library_templates()

    if template_ids is not None:
        wanted = set(str(tid) for tid in template_ids)
        # Always include any active time_series templates so redundancy correlation is computed against all active templates
        selected_templates = [
            t
            for t in source_templates
            if t.get("template_id") in wanted or t.get("status") == "active"
        ]
    else:
        wanted = {str(t["template_id"]) for t in source_templates}
        selected_templates = source_templates

    eval_universe = load_taiwan_evaluation_universe(universe)
    universe_provenance = summarize_universe_provenance(eval_universe)

    batch_result = evaluate_alpha_template_library(
        selected_templates,
        universe=eval_universe,
        strict_stepwise=strict_stepwise,
        redundancy_threshold=redundancy_threshold,
    )

    now = _utc_now_iso()
    resolved_task_id = str(task_id or f"rtask-alpha-eval-{uuid4().hex[:10]}")
    resolved_run_id = str(run_id or f"rrun-alpha-eval-{uuid4().hex[:10]}")
    artifact_id = f"rart-alpha-eval-{uuid4().hex[:10]}"

    target_evaluations = {
        tid: ev
        for tid, ev in batch_result["evaluations"].items()
        if tid in wanted
    }
    for ev in target_evaluations.values():
        ev["evaluation_ref"] = resolved_run_id

    # Update Registry entries so template.evaluation_ref points to resolved_run_id
    updated_registry_ids: dict[str, str] = {}
    if registry_service is not None:
        for tid, ev in target_evaluations.items():
            view = record_alpha_template_evaluation(
                registry_service,
                tid,
                evaluation_ref=resolved_run_id,
                evaluation_summary=ev,
                duplicate_of=ev.get("duplicate_of"),
                status=ev.get("status"),
                actor=actor,
            )
            updated_registry_ids[tid] = view.entry.registry_id

    # Persist task, run, and artifact in ResearchOrchestratorStore
    if research_store is not None:
        task_record = {
            "id": resolved_task_id,
            "task_id": resolved_task_id,
            "title": "AlphaTemplate Taiwan Point-in-Time Evaluation",
            "task_type": "alpha_template_evaluation",
            "status": "completed",
            "data_provenance": universe_provenance,
            "snapshot_id": universe_provenance["snapshot_id"],
            "symbols": batch_result["symbols"],
            "horizons": batch_result["horizons"],
            "evaluated_count": len(target_evaluations),
            "created_at": now,
            "updated_at": now,
        }
        research_store.put_task(task_record)

        artifact_record = {
            "id": artifact_id,
            "artifact_id": artifact_id,
            "run_id": resolved_run_id,
            "task_id": resolved_task_id,
            "artifact_type": "alpha_template_evaluation_report",
            "schema_version": "alpha_template_evaluation.v1",
            "data_provenance": universe_provenance,
            "snapshot_id": universe_provenance["snapshot_id"],
            "symbols": batch_result["symbols"],
            "horizons": batch_result["horizons"],
            "evaluated_count": len(target_evaluations),
            "skipped_cross_sectional_count": batch_result["skipped_cross_sectional_count"],
            "active_template_ids": batch_result["active_template_ids"],
            "duplicates_detected": {
                k: v for k, v in batch_result["duplicates_detected"].items() if k in wanted
            },
            "evaluations": target_evaluations,
            "created_at": now,
        }
        research_store.put_artifact(artifact_record)

        run_record = {
            "id": resolved_run_id,
            "run_id": resolved_run_id,
            "task_id": resolved_task_id,
            "run_type": "alpha_template_evaluation",
            "status": "completed",
            "market": "TW",
            "dataset": "TWSE_Normalized",
            "data_provenance": universe_provenance,
            "snapshot_id": universe_provenance["snapshot_id"],
            "symbols": batch_result["symbols"],
            "horizons": batch_result["horizons"],
            "evaluated_count": len(target_evaluations),
            "skipped_cross_sectional_count": batch_result["skipped_cross_sectional_count"],
            "active_template_ids": batch_result["active_template_ids"],
            "duplicates_detected": {
                k: v for k, v in batch_result["duplicates_detected"].items() if k in wanted
            },
            "artifact_ids": [artifact_id],
            "registry_entries": updated_registry_ids,
            "evaluations": target_evaluations,
            "started_at": now,
            "completed_at": now,
            "updated_at": now,
        }
        research_store.put_run(run_record)
        research_store.append_event(
            {
                "event_id": f"revt-{uuid4().hex[:10]}",
                "run_id": resolved_run_id,
                "task_id": resolved_task_id,
                "event_type": "alpha_template_evaluation.completed",
                "timestamp": now,
                "payload": {
                    "evaluated_count": len(target_evaluations),
                    "duplicates_detected": run_record["duplicates_detected"],
                    "artifact_id": artifact_id,
                },
            }
        )
    else:
        run_record = {
            "id": resolved_run_id,
            "run_id": resolved_run_id,
            "task_id": resolved_task_id,
            "run_type": "alpha_template_evaluation",
            "status": "completed",
            "market": "TW",
            "dataset": "TWSE_Normalized",
            "data_provenance": universe_provenance,
            "snapshot_id": universe_provenance["snapshot_id"],
            "symbols": batch_result["symbols"],
            "horizons": batch_result["horizons"],
            "evaluated_count": len(target_evaluations),
            "skipped_cross_sectional_count": batch_result["skipped_cross_sectional_count"],
            "active_template_ids": batch_result["active_template_ids"],
            "duplicates_detected": {
                k: v for k, v in batch_result["duplicates_detected"].items() if k in wanted
            },
            "artifact_ids": [artifact_id],
            "registry_entries": updated_registry_ids,
            "evaluations": target_evaluations,
            "started_at": now,
            "completed_at": now,
            "updated_at": now,
        }

    return {
        "run_id": resolved_run_id,
        "task_id": resolved_task_id,
        "artifact_id": artifact_id,
        "status": "completed",
        "data_provenance": universe_provenance,
        "snapshot_id": universe_provenance["snapshot_id"],
        "symbols": batch_result["symbols"],
        "horizons": batch_result["horizons"],
        "evaluated_count": len(target_evaluations),
        "skipped_cross_sectional_count": batch_result["skipped_cross_sectional_count"],
        "skipped_cross_sectional_ids": batch_result["skipped_cross_sectional_ids"],
        "active_template_ids": batch_result["active_template_ids"],
        "duplicates_detected": run_record["duplicates_detected"],
        "registry_entries": updated_registry_ids,
        "evaluations": target_evaluations,
        "run": run_record,
    }
