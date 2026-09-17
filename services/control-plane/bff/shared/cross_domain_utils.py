"""Cross-domain utility helpers with no single-domain owner."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi.params import Param as FastAPIParam

from ..auth.policy import _BFF_AUTH_STUB_ENV, bff_auth_mode, bff_error, bool_from_env
from ..models import ErrorCode


def _resolve_param(val: Any) -> Any:
    if isinstance(val, FastAPIParam):
        if val.default is ... or type(val.default).__name__ == "PydanticUndefined":
            return None
        return val.default
    return val


def _surface_degradation_reason(
    surface: Dict[str, Any],
    *,
    degraded_reason: str,
    unavailable_reason: str,
) -> Optional[str]:
    status = surface.get("status")
    if status == "ok":
        return None
    if status == "unavailable":
        return unavailable_reason
    if surface.get("message"):
        return str(surface["message"])
    if surface.get("note"):
        return str(surface["note"])
    return degraded_reason


def _ppl_alloc_009_paper_environment_guard() -> None:
    env_name = str(os.getenv("PANTHEON_ENV") or "").strip().lower()
    if (
        env_name != "dev"
        or bff_auth_mode() != "strict"
        or bool_from_env(_BFF_AUTH_STUB_ENV, default=False)
        or bool_from_env("PANTHEON_LIVE_BROKER_ENABLED", default=False)
        or bool_from_env("PANTHEON_CANARY_EXECUTION_ENABLED", default=False)
    ):
        raise bff_error(
            403,
            ErrorCode.PRECONDITION_FAILED,
            "Governed paper allocation simulation is unavailable",
            (
                "The paper-only authority requires strict dev auth with both "
                "live broker and canary execution disabled."
            ),
            precondition_failed="paper_simulation_environment",
            suggestion=(
                "Use the accepted strict dev BFF with "
                "PANTHEON_LIVE_BROKER_ENABLED=false and "
                "PANTHEON_CANARY_EXECUTION_ENABLED=false"
            ),
        )


def _management_as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _management_nested_value(record: Dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _management_first_float(record: Dict[str, Any], *paths: str) -> Optional[float]:
    for path in paths:
        value = _management_nested_value(record, path)
        number = _management_as_float(value)
        if number is not None:
            return number
    return None


def _management_telemetry_rollup(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        return {
            "runtime_count": 0,
            "total_pnl": None,
            "max_drawdown": None,
            "average_fill_rate": None,
            "total_trades": 0,
            "latest_collected_at": None,
        }

    pnl_values: List[float] = []
    drawdown_values: List[float] = []
    fill_rates: List[float] = []
    total_trades = 0
    latest_collected_at: Optional[str] = None

    for record in records:
        pnl = _management_first_float(record, "pnl", "summary.total_pnl", "summary.pnl")
        drawdown = _management_first_float(
            record,
            "drawdown",
            "max_drawdown",
            "summary.max_drawdown",
        )
        fill_rate = _management_first_float(record, "fill_rate", "summary.fill_rate")
        trades = _management_first_float(record, "total_trades", "summary.total_trades")
        collected_at = str(
            record.get("collected_at")
            or record.get("collectedAt")
            or record.get("updated_at")
            or record.get("updatedAt")
            or ""
        ).strip()
        if pnl is not None:
            pnl_values.append(pnl)
        if drawdown is not None:
            drawdown_values.append(drawdown)
        if fill_rate is not None:
            fill_rates.append(fill_rate)
        if trades is not None:
            total_trades += int(trades)
        if collected_at and (latest_collected_at is None or collected_at > latest_collected_at):
            latest_collected_at = collected_at

    return {
        "runtime_count": len(records),
        "total_pnl": round(sum(pnl_values), 6) if pnl_values else None,
        "max_drawdown": max(drawdown_values) if drawdown_values else None,
        "average_fill_rate": round(sum(fill_rates) / len(fill_rates), 6) if fill_rates else None,
        "total_trades": total_trades,
        "latest_collected_at": latest_collected_at,
    }


def _sort_records_latest_first(
    records: List[Dict[str, Any]],
    fields: tuple[str, ...],
) -> List[Dict[str, Any]]:
    return sorted(
        records,
        key=lambda item: next(
            (str(item.get(field) or "") for field in fields if item.get(field)),
            "",
        ),
        reverse=True,
    )


def _merge_registry_records(
    fixture_records: List[Dict[str, Any]],
    registry_records: List[Dict[str, Any]],
    id_keys: tuple[str, ...],
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for record in fixture_records + registry_records:
        record_id = ""
        for key in id_keys:
            value = record.get(key)
            if value not in (None, ""):
                record_id = str(value)
                break
        if record_id:
            merged[record_id] = dict(record)
    return list(merged.values())
