from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
SLA_TARGETS_PATH = ROOT / "services" / "bff" / "ha" / "sla_targets.json"

EXPECTED_TARGETS = {
    "dev": {
        "uptime_target_percent": 95.0,
        "p99_latency_ms": 1000,
        "sse_connections": 100,
        "rto_seconds": 300,
        "rpo_seconds": 60,
        "monthly_cost_ceiling_usd": 100,
    },
    "staging": {
        "uptime_target_percent": 99.0,
        "p99_latency_ms": 700,
        "sse_connections": 500,
        "rto_seconds": 120,
        "rpo_seconds": 30,
        "monthly_cost_ceiling_usd": 300,
    },
    "production": {
        "uptime_target_percent": 99.5,
        "p99_latency_ms": 500,
        "sse_connections": 1000,
        "rto_seconds": 60,
        "rpo_seconds": 10,
        "monthly_cost_ceiling_usd": 800,
    },
}

REQUIRED_FIELDS = {
    "uptime_target_percent": (int, float),
    "p99_latency_ms": (int,),
    "sse_connections": (int,),
    "rto_seconds": (int,),
    "rpo_seconds": (int,),
    "monthly_cost_ceiling_usd": (int,),
}


def _load_sla_targets() -> dict[str, Any]:
    return json.loads(SLA_TARGETS_PATH.read_text(encoding="utf-8"))


def _validate_sla_targets(payload: dict[str, Any]) -> None:
    errors: list[str] = []

    if payload.get("schema_version") != "bff_ha_sla_targets.v1":
        errors.append("schema_version must be bff_ha_sla_targets.v1")

    targets = payload.get("targets")
    if not isinstance(targets, dict):
        raise ValueError("targets must be an object")

    expected_envs = set(EXPECTED_TARGETS)
    actual_envs = set(targets)
    if actual_envs != expected_envs:
        errors.append(f"targets must contain exactly {sorted(expected_envs)}")

    for environment in ("dev", "staging", "production"):
        target = targets.get(environment)
        if not isinstance(target, dict):
            errors.append(f"{environment} must be an object")
            continue

        extra_fields = set(target) - set(REQUIRED_FIELDS)
        if extra_fields:
            errors.append(f"{environment} has unsupported fields: {sorted(extra_fields)}")

        for field, expected_types in REQUIRED_FIELDS.items():
            if field not in target:
                errors.append(f"{environment}.{field} is required")
                continue
            value = target[field]
            if isinstance(value, bool) or not isinstance(value, expected_types):
                errors.append(f"{environment}.{field} must be numeric")

        uptime = target.get("uptime_target_percent")
        if isinstance(uptime, (int, float)) and not isinstance(uptime, bool):
            if uptime <= 0 or uptime > 100:
                errors.append(f"{environment}.uptime_target_percent must be between 0 and 100")

        for field in (
            "p99_latency_ms",
            "sse_connections",
            "rto_seconds",
            "rpo_seconds",
            "monthly_cost_ceiling_usd",
        ):
            value = target.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value <= 0:
                errors.append(f"{environment}.{field} must be positive")

        rto = target.get("rto_seconds")
        rpo = target.get("rpo_seconds")
        if isinstance(rto, int) and isinstance(rpo, int) and not isinstance(rto, bool) and not isinstance(rpo, bool):
            if rpo > rto:
                errors.append(f"{environment}.rpo_seconds must not exceed rto_seconds")

    if errors:
        raise ValueError("; ".join(errors))


def test_sla_targets_match_part_d3_values() -> None:
    payload = _load_sla_targets()

    _validate_sla_targets(payload)

    assert payload["source"].endswith("#d3-sla-targets")
    assert payload["targets"] == EXPECTED_TARGETS


def test_sla_targets_reject_missing_required_field() -> None:
    payload = _load_sla_targets()
    broken_payload = copy.deepcopy(payload)
    del broken_payload["targets"]["staging"]["rpo_seconds"]

    with pytest.raises(ValueError, match=r"staging\.rpo_seconds is required"):
        _validate_sla_targets(broken_payload)


def test_sla_targets_reject_invalid_numeric_types() -> None:
    payload = _load_sla_targets()
    broken_payload = copy.deepcopy(payload)
    broken_payload["targets"]["production"]["monthly_cost_ceiling_usd"] = "800"

    with pytest.raises(ValueError, match=r"production\.monthly_cost_ceiling_usd must be numeric"):
        _validate_sla_targets(broken_payload)
