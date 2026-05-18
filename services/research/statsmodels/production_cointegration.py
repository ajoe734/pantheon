"""Production TWSE pair cointegration snapshot for statsmodels admission.

This module consumes the governed MGMT-QLIB-001 TWSE OHLCV materialization,
runs Engle-Granger cointegration checks over a fixed two-year rolling window,
and returns a draft ``signal_snapshot`` artifact. It performs no registry
write, broker action, deployment, or capital binding.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TASK_ID = "OSS-STAT-V2-001"
BACKEND_ID = "statsmodels_production_cointegration"
CODE_VERSION = "pantheon:services/research/statsmodels@OSS-STAT-V2-001"
ARTIFACT_VERSION = "2.0.0"
DEFAULT_ROLLING_WINDOW = 504
DEFAULT_TOP_N = 10
DEFAULT_DATASET_PATH = (
    REPO_ROOT / "services" / "research" / "qlib" / "examples" / "smoke_dataset.json"
)
DEFAULT_DATASET_MANIFEST_PATH = (
    REPO_ROOT / "support" / "evidence" / "MGMT-QLIB-001" / "dataset_manifest.json"
)
REGISTRY_ENTRY_SCHEMA_PATH = REPO_ROOT / "services" / "registry" / "registry_entry_schema.json"
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
DEFAULT_TWSE_LARGE_CAP_PAIRS: tuple[tuple[str, str], ...] = (
    ("TWSE_0004", "TWSE_0044"),
    ("TWSE_0004", "TWSE_0013"),
    ("TWSE_0013", "TWSE_0027"),
    ("TWSE_0013", "TWSE_0044"),
    ("TWSE_0033", "TWSE_0041"),
    ("TWSE_0039", "TWSE_0046"),
    ("TWSE_0004", "TWSE_0027"),
    ("TWSE_0010", "TWSE_0023"),
    ("TWSE_0013", "TWSE_0049"),
    ("TWSE_0039", "TWSE_0045"),
)


class ProductionCointegrationError(ValueError):
    """Raised when a production cointegration snapshot is incomplete."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_production(
    pair_universe: Sequence[Any] | str = "twse_large_cap_pairs",
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
    *,
    dataset: Mapping[str, Any] | None = None,
    dataset_path: str | Path = DEFAULT_DATASET_PATH,
    dataset_manifest: Mapping[str, Any] | None = None,
    dataset_manifest_path: str | Path = DEFAULT_DATASET_MANIFEST_PATH,
    created_at: str | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Run production Engle-Granger checks and return a signal_snapshot dict."""

    created = created_at or utc_now()
    window = _positive_int(rolling_window, "rolling_window")
    requested_top_n = _positive_int(top_n, "top_n")
    raw_dataset = _payload_or_file(dataset, dataset_path, "dataset")
    manifest = _payload_or_file(dataset_manifest, dataset_manifest_path, "dataset_manifest")
    _assert_manifest_dataset_binding(raw_dataset, manifest)

    manifest_start, manifest_end = _manifest_date_bounds(manifest)
    grouped = _group_records(raw_dataset, start=manifest_start, end=manifest_end)
    pairs = _normalize_pair_universe(pair_universe, grouped)
    limit = min(requested_top_n, len(pairs))

    pair_results = [
        _run_pair_cointegration(grouped, instrument_a, instrument_b, rolling_window=window)
        for instrument_a, instrument_b in pairs
    ]
    pair_results.sort(key=lambda row: (row["p_value"], row["pair_id"]))
    for index, row in enumerate(pair_results, start=1):
        row["rank"] = index

    top_pairs = pair_results[:limit]
    production_summary = _production_dataset_summary(raw_dataset, manifest, grouped)
    lineage = _build_lineage(raw_dataset, manifest)
    summary = _cointegration_summary(pair_results, top_pairs, limit)
    payload = {
        "schema_version": "1.0",
        "artifact_type": "signal_snapshot",
        "snapshot_id": f"signal-snapshot:{BACKEND_ID}:{ARTIFACT_VERSION}",
        "task_id": TASK_ID,
        "backend_id": BACKEND_ID,
        "code_version": CODE_VERSION,
        "created_at": created,
        "pair_universe": {
            "label": _universe_label(pair_universe),
            "pair_count": len(pair_results),
            "pairs": [f"{a}/{b}" for a, b in pairs],
        },
        "rolling_window": window,
        "rolling_window_unit": "daily_periods",
        "test": "engle_granger",
        "dataset": production_summary,
        "cointegration_summary": summary,
        "pairs": pair_results,
        "top_cointegrated_pairs": top_pairs,
        "lineage": lineage,
        "downstream_scope": _downstream_scope(),
        "safety_assertions": _safety_assertions(),
    }
    checksum = f"sha256:{_sha256_json(payload)}"
    registry_entry = _build_signal_snapshot_artifact(payload, checksum)
    signal_ref = _build_signal_snapshot_ref(registry_entry)

    snapshot = {
        **payload,
        "checksum": checksum,
        "registry_entry": registry_entry,
        "signal_snapshot_ref": signal_ref,
    }
    _validate_signal_artifact(registry_entry)
    if summary["cointegrated_pair_count"] < 3:
        raise ProductionCointegrationError(
            "production TWSE fixture must yield at least 3 cointegrated pairs with p_value < 0.05"
        )
    return snapshot


def _payload_or_file(
    payload: Mapping[str, Any] | None,
    path: str | Path,
    label: str,
) -> dict[str, Any]:
    if payload is not None:
        if not isinstance(payload, Mapping):
            raise ProductionCointegrationError(f"{label} must be a mapping")
        return copy.deepcopy(dict(payload))
    source = Path(path)
    if not source.exists():
        raise ProductionCointegrationError(f"{label} file not found: {source}")
    loaded = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise ProductionCointegrationError(f"{label} file must contain a JSON object")
    return dict(loaded)


def _assert_manifest_dataset_binding(dataset: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    dataset_id = _required_text(dataset.get("dataset_id"), "dataset.dataset_id")
    manifest_dataset_id = _required_text(manifest.get("dataset_id"), "manifest.dataset_id")
    if dataset_id != manifest_dataset_id:
        raise ProductionCointegrationError(
            f"dataset_id {dataset_id!r} does not match MGMT-QLIB-001 manifest {manifest_dataset_id!r}"
        )
    if _required_text(dataset.get("strategy_id"), "dataset.strategy_id") != _required_text(
        manifest.get("strategy_id"),
        "manifest.strategy_id",
    ):
        raise ProductionCointegrationError("dataset.strategy_id does not match MGMT-QLIB-001 manifest")
    if _required_text(dataset.get("source_strategy_spec_id"), "dataset.source_strategy_spec_id") != _required_text(
        manifest.get("source_strategy_spec_id"),
        "manifest.source_strategy_spec_id",
    ):
        raise ProductionCointegrationError(
            "dataset.source_strategy_spec_id does not match MGMT-QLIB-001 manifest"
        )
    if not set(_source_dataset_refs(dataset)).intersection(_source_dataset_refs(manifest)):
        raise ProductionCointegrationError("dataset source_dataset_refs must match MGMT-QLIB-001 manifest refs")
    if _mapping(manifest.get("activation_floor")).get("dataset_gate_satisfied") is not True:
        raise ProductionCointegrationError("MGMT-QLIB-001 activation_floor.dataset_gate_satisfied is not true")


def _manifest_date_bounds(manifest: Mapping[str, Any]) -> tuple[date, date]:
    governed = _mapping(manifest.get("governed_dataset"))
    start = _parse_date(governed.get("history_start") or governed.get("start_date"), "manifest.history_start")
    end = _parse_date(governed.get("history_end") or governed.get("end_date"), "manifest.history_end")
    if end < start:
        raise ProductionCointegrationError("manifest history_end must be on or after history_start")
    return start, end


def _group_records(
    dataset: Mapping[str, Any],
    *,
    start: date,
    end: date,
) -> dict[str, list[dict[str, Any]]]:
    records = dataset.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ProductionCointegrationError("dataset.records must be an array")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ProductionCointegrationError("each dataset record must be an object")
        instrument = _required_text(raw.get("instrument"), "record.instrument")
        record_date = _parse_date(_required_text(raw.get("date"), "record.date"), "record.date")
        if record_date < start or record_date > end:
            continue
        normalized = {
            "instrument": instrument,
            "date": record_date.isoformat(),
        }
        for field_name in REQUIRED_OHLCV_FIELDS:
            normalized[field_name] = _number(raw.get(field_name), f"{instrument}.{field_name}")
        grouped[instrument].append(normalized)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["date"])
    if not grouped:
        raise ProductionCointegrationError("no dataset records remain inside MGMT-QLIB-001 manifest date bounds")
    return dict(grouped)


def _normalize_pair_universe(
    pair_universe: Sequence[Any] | str,
    grouped: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[tuple[str, str]]:
    all_instruments = set(grouped)
    if isinstance(pair_universe, str):
        raw = pair_universe.strip()
        if raw.lower() in {"default", "twse_large_cap_pairs", "manifest_top_pairs"}:
            pairs = list(DEFAULT_TWSE_LARGE_CAP_PAIRS)
        else:
            tokens = [item.strip() for item in raw.split(",") if item.strip()]
            if not tokens:
                raise ProductionCointegrationError("pair_universe must not be empty")
            if all("/" in token for token in tokens):
                pairs = [_pair_from_pair_id(token) for token in tokens]
            else:
                pairs = list(itertools.combinations(tokens, 2))
    else:
        values = list(pair_universe)
        if not values:
            raise ProductionCointegrationError("pair_universe must not be empty")
        if all(isinstance(item, str) and "/" not in item for item in values):
            pairs = list(itertools.combinations([str(item) for item in values], 2))
        else:
            pairs = [_pair_from_value(item) for item in values]

    normalized: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for instrument_a, instrument_b in pairs:
        a = _required_text(instrument_a, "pair.instrument_a")
        b = _required_text(instrument_b, "pair.instrument_b")
        if a == b:
            raise ProductionCointegrationError(f"pair instruments must differ: {a}")
        pair = tuple(sorted((a, b)))
        missing = [item for item in pair if item not in all_instruments]
        if missing:
            raise ProductionCointegrationError(
                "pair_universe contains unknown instruments: " + ", ".join(missing)
            )
        if pair not in seen:
            normalized.append(pair)
            seen.add(pair)
    return normalized


def _pair_from_value(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        return _pair_from_pair_id(value)
    if isinstance(value, Mapping):
        return (
            str(value.get("instrument_a") or value.get("a") or "").strip(),
            str(value.get("instrument_b") or value.get("b") or "").strip(),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        return (str(value[0]).strip(), str(value[1]).strip())
    raise ProductionCointegrationError(f"invalid pair specification: {value!r}")


def _pair_from_pair_id(pair_id: str) -> tuple[str, str]:
    parts = [item.strip() for item in str(pair_id).split("/") if item.strip()]
    if len(parts) != 2:
        raise ProductionCointegrationError(f"pair id must look like INSTRUMENT_A/INSTRUMENT_B: {pair_id!r}")
    return (parts[0], parts[1])


def _run_pair_cointegration(
    grouped: Mapping[str, Sequence[Mapping[str, Any]]],
    instrument_a: str,
    instrument_b: str,
    *,
    rolling_window: int,
) -> dict[str, Any]:
    closes_a = {str(row["date"]): _number(row.get("close"), f"{instrument_a}.close") for row in grouped[instrument_a]}
    closes_b = {str(row["date"]): _number(row.get("close"), f"{instrument_b}.close") for row in grouped[instrument_b]}
    aligned_dates = sorted(set(closes_a).intersection(closes_b))
    if len(aligned_dates) < rolling_window:
        raise ProductionCointegrationError(
            f"{instrument_a}/{instrument_b} has {len(aligned_dates)} aligned observations; "
            f"rolling_window requires {rolling_window}"
        )
    window_dates = aligned_dates[-rolling_window:]
    prices_a = [closes_a[item] for item in window_dates]
    prices_b = [closes_b[item] for item in window_dates]
    result = _engle_granger_cointegration(prices_a, prices_b)
    spread = [_number(item, "spread") for item in result.get("spread", [])]
    if len(spread) != rolling_window:
        raise ProductionCointegrationError(f"{instrument_a}/{instrument_b} spread length mismatch")
    zscore = _zscore_last(spread)
    p_value = _number(result.get("p_value"), "p_value")
    half_life = _number(result.get("half_life"), "half_life")
    return {
        "pair_id": f"{instrument_a}/{instrument_b}",
        "instrument_a": instrument_a,
        "instrument_b": instrument_b,
        "p_value": round(p_value, 10),
        "half_life": round(half_life, 6) if math.isfinite(half_life) else "inf",
        "spread_zscore": round(zscore, 6),
        "window_start_date": window_dates[0],
        "window_end_date": window_dates[-1],
        "observations": rolling_window,
        "cointegrated": p_value < 0.05,
    }


def _production_dataset_summary(
    dataset: Mapping[str, Any],
    manifest: Mapping[str, Any],
    grouped: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    governed = _mapping(manifest.get("governed_dataset"))
    manifest_floor = _mapping(manifest.get("activation_floor"))
    period_counts = [len(rows) for rows in grouped.values()]
    all_dates = sorted({str(row.get("date")) for rows in grouped.values() for row in rows})
    return {
        "dataset_id": dataset.get("dataset_id"),
        "manifest_id": manifest.get("manifest_id"),
        "source_dataset_refs": _source_dataset_refs(dataset),
        "source_strategy_spec_id": dataset.get("source_strategy_spec_id"),
        "strategy_id": dataset.get("strategy_id"),
        "num_instruments": len(grouped),
        "history_start": all_dates[0] if all_dates else None,
        "history_end": all_dates[-1] if all_dates else None,
        "history_years": governed.get("history_years"),
        "min_periods_per_instrument": min(period_counts) if period_counts else 0,
        "data_frequency": dataset.get("data_frequency"),
        "ohlcv_fields": list(REQUIRED_OHLCV_FIELDS),
        "manifest_required_min_instruments": manifest_floor.get("required_min_instruments"),
        "manifest_required_min_history_years": manifest_floor.get("required_min_history_years"),
        "manifest_required_min_daily_periods": manifest_floor.get("required_min_daily_periods"),
        "production_scale_satisfied": manifest_floor.get("dataset_gate_satisfied") is True,
    }


def _build_lineage(dataset: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "parent_registry_ids": [_required_text(manifest.get("source_strategy_spec_id"), "source_strategy_spec_id")],
        "source_run_ids": [
            str(manifest.get("task_id") or "MGMT-QLIB-001"),
            TASK_ID,
        ],
        "source_dataset_refs": _source_dataset_refs(dataset),
        "source_strategy_spec_id": manifest.get("source_strategy_spec_id"),
        "dataset_manifest_id": manifest.get("manifest_id"),
    }


def _cointegration_summary(
    all_pairs: Sequence[Mapping[str, Any]],
    top_pairs: Sequence[Mapping[str, Any]],
    top_n: int,
) -> dict[str, Any]:
    cointegrated = [row for row in all_pairs if row.get("cointegrated") is True]
    p_values = [_number(row.get("p_value"), "p_value") for row in all_pairs]
    return {
        "pair_count": len(all_pairs),
        "top_n": top_n,
        "cointegrated_pair_count": len(cointegrated),
        "cointegration_threshold": 0.05,
        "best_pair_id": top_pairs[0]["pair_id"] if top_pairs else None,
        "best_p_value": top_pairs[0]["p_value"] if top_pairs else None,
        "median_p_value": round(_median(p_values), 10) if p_values else None,
        "max_abs_spread_zscore_top_n": round(
            max((abs(_number(row.get("spread_zscore"), "spread_zscore")) for row in top_pairs), default=0.0),
            6,
        ),
    }


def _build_signal_snapshot_artifact(payload: Mapping[str, Any], checksum: str) -> dict[str, Any]:
    strategy_id = _required_text(_mapping(payload.get("dataset")).get("strategy_id"), "dataset.strategy_id")
    registry_id = f"statsmodels-production-cointegration-{_slug(strategy_id)}-{ARTIFACT_VERSION}"
    return {
        "registry_id": registry_id,
        "artifact_type": "signal_snapshot",
        "strategy_id": strategy_id,
        "version": ARTIFACT_VERSION,
        "artifact_state": "draft",
        "lineage": copy.deepcopy(payload["lineage"]),
        "storage_ref": {
            "backend": "inline",
            "path": "$.signal_snapshot",
        },
        "checksum": checksum,
        "producer_run_id": payload.get("snapshot_id"),
        "evaluation_summary": {
            "evaluation_kind": "production_rolling_engle_granger",
            "pair_count": _mapping(payload.get("cointegration_summary")).get("pair_count"),
            "cointegrated_pair_count": _mapping(payload.get("cointegration_summary")).get(
                "cointegrated_pair_count"
            ),
            "best_pair_id": _mapping(payload.get("cointegration_summary")).get("best_pair_id"),
            "best_p_value": _mapping(payload.get("cointegration_summary")).get("best_p_value"),
            "dataset_manifest_id": _mapping(payload.get("dataset")).get("manifest_id"),
        },
        "deployment_summary": {
            "current_stage": "none",
        },
        "metadata": {
            "created_at": payload.get("created_at"),
            "framework": "statsmodels",
            "test": "engle_granger",
            "rolling_window": payload.get("rolling_window"),
            "artifact_payload_checksum": checksum,
            "registry_write_performed": False,
            "registry_write_authority": "registry_service_only",
            "signal_payload": copy.deepcopy(dict(payload)),
        },
    }


def _build_signal_snapshot_ref(registry_entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_name": "signal_snapshot",
        "artifact_type": registry_entry["artifact_type"],
        "artifact_ref": f"{registry_entry['registry_id']}@{registry_entry['version']}",
        "registry_id": registry_entry["registry_id"],
        "strategy_id": registry_entry["strategy_id"],
        "version": registry_entry["version"],
        "artifact_state": registry_entry["artifact_state"],
        "deployment_stage": _mapping(registry_entry.get("deployment_summary")).get("current_stage"),
        "storage_ref": copy.deepcopy(registry_entry["storage_ref"]),
        "checksum": registry_entry["checksum"],
        "source_run_id": registry_entry["producer_run_id"],
        "source_dataset_refs": copy.deepcopy(registry_entry["lineage"]["source_dataset_refs"]),
        "source_strategy_spec_id": registry_entry["lineage"]["source_strategy_spec_id"],
    }


def _validate_signal_artifact(entry: Mapping[str, Any]) -> None:
    missing = {
        "registry_id",
        "artifact_type",
        "strategy_id",
        "version",
        "artifact_state",
        "lineage",
        "storage_ref",
        "checksum",
    } - set(entry.keys())
    if missing:
        raise ProductionCointegrationError("signal_snapshot registry entry missing: " + ", ".join(sorted(missing)))
    if entry.get("artifact_type") != "signal_snapshot":
        raise ProductionCointegrationError("signal_snapshot registry entry artifact_type mismatch")
    if entry.get("artifact_state") != "draft":
        raise ProductionCointegrationError("signal_snapshot registry entry must remain draft")
    if not str(entry.get("checksum") or "").startswith("sha256:"):
        raise ProductionCointegrationError("signal_snapshot registry entry checksum must be sha256-prefixed")
    lineage = _mapping(entry.get("lineage"))
    if not (lineage.get("source_run_ids") and lineage.get("source_dataset_refs") and lineage.get("source_strategy_spec_id")):
        raise ProductionCointegrationError("signal_snapshot lineage must include run, dataset, and StrategySpec refs")
    _validate_registry_schema_when_available(entry)


def _validate_registry_schema_when_available(entry: Mapping[str, Any]) -> None:
    try:
        from jsonschema import Draft7Validator  # type: ignore
    except ModuleNotFoundError:
        return
    schema = json.loads(REGISTRY_ENTRY_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(dict(entry)), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ProductionCointegrationError(f"signal_snapshot registry schema failed at {location}: {first.message}")


def _engle_granger_cointegration(prices_a: Sequence[float], prices_b: Sequence[float]) -> dict[str, Any]:
    if len(prices_a) != len(prices_b):
        raise ProductionCointegrationError("cointegration series lengths must match")
    if len(prices_a) < 20:
        raise ProductionCointegrationError("at least 20 observations are required for cointegration")
    import numpy as np

    coint = _external_statsmodels_coint()
    y = np.array(prices_a, dtype=float)
    x = np.array(prices_b, dtype=float)
    _, p_value, _ = coint(y, x)

    n = len(y)
    x_mat = np.column_stack([np.ones(n), x])
    coeffs, _, _, _ = np.linalg.lstsq(x_mat, y, rcond=None)
    alpha, beta = float(coeffs[0]), float(coeffs[1])
    spread_arr = y - alpha - beta * x

    delta_s = spread_arr[1:] - spread_arr[:-1]
    lag_s = spread_arr[:-1]
    lag_mat = np.column_stack([np.ones(len(lag_s)), lag_s])
    ar_coeffs, _, _, _ = np.linalg.lstsq(lag_mat, delta_s, rcond=None)
    gamma = float(ar_coeffs[1])
    rho = 1.0 + gamma
    if 0.0 < rho < 1.0:
        half_life = -math.log(2.0) / math.log(rho)
    else:
        half_life = float("inf")
    return {
        "p_value": float(p_value),
        "spread": spread_arr.tolist(),
        "half_life": half_life,
    }


def _external_statsmodels_coint() -> Any:
    # ``services/research/statsmodels`` can shadow the upstream package during
    # repo-wide pytest collection. Remove that path only for the upstream import.
    research_dir = (REPO_ROOT / "services" / "research").resolve()
    shadowed = sys.modules.get("statsmodels")
    if shadowed is not None and _module_is_repo_statsmodels(shadowed):
        for name in list(sys.modules):
            if name == "statsmodels" or name.startswith("statsmodels."):
                sys.modules.pop(name, None)
    original_path = list(sys.path)
    try:
        sys.path[:] = [
            item
            for item in sys.path
            if _safe_resolve_path(item) != research_dir
        ]
        from statsmodels.tsa.stattools import coint
    except ModuleNotFoundError as exc:
        raise ProductionCointegrationError(
            "upstream statsmodels is required; install services/research/statsmodels/requirements.txt"
        ) from exc
    finally:
        sys.path[:] = original_path
    return coint


def _module_is_repo_statsmodels(module: Any) -> bool:
    module_path = _safe_resolve_path(str(getattr(module, "__file__", "") or ""))
    statsmodels_dir = (REPO_ROOT / "services" / "research" / "statsmodels").resolve()
    if module_path is None:
        return False
    return module_path == statsmodels_dir or statsmodels_dir in module_path.parents


def _safe_resolve_path(value: str) -> Path | None:
    if not value:
        return None
    try:
        return Path(value).resolve()
    except OSError:
        return None


def _source_dataset_refs(payload: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for source in (payload, _mapping(payload.get("governed_dataset")), _mapping(payload.get("qlib_preflight_governed_dataset"))):
        raw_values = source.get("source_dataset_refs")
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        if isinstance(raw_values, Sequence) and not isinstance(raw_values, (str, bytes)):
            for item in raw_values:
                text = str(item or "").strip()
                if text and text not in refs:
                    refs.append(text)
        single = str(source.get("source_dataset_ref") or "").strip()
        if single and single not in refs:
            refs.append(single)
    return refs


def _downstream_scope() -> dict[str, Any]:
    return {
        "signal_snapshot_only": True,
        "registry_write_authority": "registry_service_only",
        "registry_write_performed": False,
        "deployment_stage": "none",
        "training_performed_in_this_task": False,
        "broker_session_opened": False,
        "order_route": "none",
        "capital_binding": "none",
        "cpu_only": True,
        "gpu_required": False,
    }


def _safety_assertions() -> dict[str, bool]:
    return {
        "no_registry_write": True,
        "no_order_route": True,
        "no_broker_session": True,
        "no_capital_binding": True,
        "deployment_stage_remains_none": True,
        "artifact_state_request_limited_to_candidate": True,
        "research_only_not_direct_action": True,
        "cpu_only": True,
        "no_gpu": True,
    }


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProductionCointegrationError(f"{field_name} must be a positive integer")
    return value


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProductionCointegrationError(f"{field_name} must be numeric")
    return float(value)


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ProductionCointegrationError(f"{field_name} is required")
    return text


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _parse_date(value: Any, field_name: str) -> date:
    text = _required_text(value, field_name)
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ProductionCointegrationError(f"{field_name} must be YYYY-MM-DD") from exc


def _zscore_last(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    variance = sum((item - mean) ** 2 for item in values) / max(len(values) - 1, 1)
    stddev = math.sqrt(variance)
    return 0.0 if stddev == 0.0 else (values[-1] - mean) / stddev


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _sha256_json(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


def _universe_label(pair_universe: Sequence[Any] | str) -> str:
    if isinstance(pair_universe, str):
        return pair_universe
    return "explicit_pairs"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run OSS-STAT-V2-001 production cointegration snapshot.")
    parser.add_argument("--pair-universe", default="twse_large_cap_pairs")
    parser.add_argument("--rolling-window", type=int, default=DEFAULT_ROLLING_WINDOW)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST_PATH)
    parser.add_argument("--created-at")
    args = parser.parse_args(argv)
    snapshot = run_production(
        args.pair_universe,
        args.rolling_window,
        dataset_path=args.dataset,
        dataset_manifest_path=args.dataset_manifest,
        created_at=args.created_at,
    )
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
