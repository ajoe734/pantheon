"""Production TXO option-chain pricing snapshot for OSS-QUANTLIB-V2-001.

This module is offline and review-only. It prices a governed option chain with
Black-Scholes-Merton analytics, emits deterministic Greeks, and prepares a
draft ``pricing_snapshot`` registry projection. It performs no registry write,
opens no broker session, and routes no orders.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TASK_ID = "OSS-QUANTLIB-V2-001"
BACKEND_ID = "quantlib_production_option_chain"
CODE_VERSION = "pantheon:services/research/quantlib@OSS-QUANTLIB-V2-001"
ARTIFACT_VERSION = "2.0.0"
DEFAULT_STRATEGY_ID = "txo-option-chain-risk-analytics"
DEFAULT_STRATEGY_SPEC_ID = "quantlib-txo-option-chain-pricing-spec-v1"
DEFAULT_DATASET_REF = "dataset:txo-option-chain-fixture-2026-05"
DEFAULT_AS_OF = "2026-05-17"
DEFAULT_SPOT = 21000.0
DEFAULT_RATE = 0.015
DEFAULT_DIVIDEND_YIELD = 0.012
DEFAULT_PRICING_SNAPSHOT_PATH = (
    REPO_ROOT / "support" / "evidence" / TASK_ID / "pricing_snapshot.json"
)

CallPut = Literal["call", "put"]


class ProductionOptionChainError(ValueError):
    """Raised when an option-chain pricing request is incomplete or unsafe."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_txo_chain_definition() -> dict[str, Any]:
    """Return the deterministic TXO-like chain used by tests and evidence."""

    return {
        "underlying": "TXO",
        "as_of": DEFAULT_AS_OF,
        "source_dataset_refs": [DEFAULT_DATASET_REF],
        "source_strategy_spec_id": DEFAULT_STRATEGY_SPEC_ID,
        "strikes": [20400.0, 20700.0, 21000.0, 21300.0, 21600.0],
        "expiries": ["2026-06-17", "2026-07-15", "2026-08-19"],
        "call_put": ["call", "put"],
        "multiplier": 200,
    }


def default_vol_surface() -> dict[str, Any]:
    """Return a small deterministic expiry/strike volatility surface."""

    return {
        "default_vol": 0.235,
        "by_expiry": {
            "2026-06-17": 0.225,
            "2026-07-15": 0.232,
            "2026-08-19": 0.241,
        },
        "by_strike": {
            "20400": 0.245,
            "20700": 0.236,
            "21000": 0.228,
            "21300": 0.233,
            "21600": 0.242,
        },
        "points": [
            {"expiry": "2026-06-17", "strike": 20400.0, "vol": 0.238},
            {"expiry": "2026-06-17", "strike": 21000.0, "vol": 0.224},
            {"expiry": "2026-06-17", "strike": 21600.0, "vol": 0.236},
            {"expiry": "2026-07-15", "strike": 20400.0, "vol": 0.242},
            {"expiry": "2026-07-15", "strike": 21000.0, "vol": 0.231},
            {"expiry": "2026-07-15", "strike": 21600.0, "vol": 0.239},
            {"expiry": "2026-08-19", "strike": 20400.0, "vol": 0.249},
            {"expiry": "2026-08-19", "strike": 21000.0, "vol": 0.239},
            {"expiry": "2026-08-19", "strike": 21600.0, "vol": 0.247},
        ],
    }


def price_chain(
    spot: float,
    dividend_yield: float,
    rate: float,
    vol_surface: Mapping[str, Any] | float,
    chain_definition: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Price a cross-product or explicit option chain.

    Returns one row per option contract with at least ``strike``, ``expiry``,
    ``call_put``, ``price``, ``delta``, ``gamma``, ``vega``, and ``theta``.
    ``theta`` is returned per calendar day to match the existing governed
    QuantLib adapter convention.
    """

    normalized_spot = _positive_float(spot, "spot")
    normalized_rate = _finite_float(rate, "rate")
    normalized_yield = _finite_float(dividend_yield, "dividend_yield")
    if not isinstance(chain_definition, Mapping):
        raise ProductionOptionChainError("chain_definition must be a mapping")

    as_of = _parse_date(chain_definition.get("as_of", DEFAULT_AS_OF), "chain_definition.as_of")
    contracts = _expand_contracts(chain_definition)
    if not contracts:
        raise ProductionOptionChainError("chain_definition must produce at least one contract")

    rows: list[dict[str, Any]] = []
    underlying = str(chain_definition.get("underlying") or "TXO")
    for contract in contracts:
        strike = _positive_float(contract["strike"], "contract.strike")
        expiry = _parse_date(contract["expiry"], "contract.expiry")
        if expiry <= as_of:
            raise ProductionOptionChainError("contract.expiry must be after chain_definition.as_of")
        tenor_years = (expiry - as_of).days / 365.0
        call_put = _normalize_call_put(contract["call_put"])
        vol = _resolve_vol(vol_surface, expiry=expiry.isoformat(), strike=strike)
        metrics = _black_scholes_merton(
            normalized_spot,
            strike,
            normalized_rate,
            normalized_yield,
            vol,
            tenor_years,
            call_put,
        )
        row = {
            "underlying": underlying,
            "strike": _rounded(strike),
            "expiry": expiry.isoformat(),
            "call_put": call_put,
            "price": _rounded(metrics["price"]),
            "delta": _rounded(metrics["delta"]),
            "gamma": _rounded(metrics["gamma"]),
            "vega": _rounded(metrics["vega"]),
            "theta": _rounded(metrics["theta"]),
            "implied_vol": _rounded(vol),
            "tenor_years": _rounded(tenor_years),
            "pricing_model": "black_scholes_merton",
        }
        if "contract_id" in contract:
            row["contract_id"] = str(contract["contract_id"])
        rows.append(row)

    rows.sort(key=lambda item: (item["expiry"], item["strike"], item["call_put"]))
    return rows


def build_pricing_snapshot(
    spot: float = DEFAULT_SPOT,
    dividend_yield: float = DEFAULT_DIVIDEND_YIELD,
    rate: float = DEFAULT_RATE,
    vol_surface: Mapping[str, Any] | float | None = None,
    chain_definition: Mapping[str, Any] | None = None,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic ``pricing_snapshot`` artifact payload."""

    chain_def = copy.deepcopy(dict(chain_definition or default_txo_chain_definition()))
    surface = copy.deepcopy(vol_surface if vol_surface is not None else default_vol_surface())
    chain = price_chain(spot, dividend_yield, rate, surface, chain_def)
    as_of = _parse_date(chain_def.get("as_of", DEFAULT_AS_OF), "chain_definition.as_of").isoformat()
    generated_at = created_at or f"{as_of}T00:00:00Z"
    source_dataset_refs = _text_sequence(
        chain_def.get("source_dataset_refs"), default=[DEFAULT_DATASET_REF]
    )
    source_strategy_spec_id = str(
        chain_def.get("source_strategy_spec_id") or DEFAULT_STRATEGY_SPEC_ID
    )
    registry_id = _registry_id(chain_def)
    run_id = f"pricing-snapshot:{BACKEND_ID}:{ARTIFACT_VERSION}"

    core_payload = {
        "artifact_type": "pricing_snapshot",
        "snapshot_id": run_id,
        "task_id": TASK_ID,
        "backend_id": BACKEND_ID,
        "code_version": CODE_VERSION,
        "version": ARTIFACT_VERSION,
        "generated_at": generated_at,
        "underlying": str(chain_def.get("underlying") or "TXO"),
        "as_of": as_of,
        "inputs": {
            "spot": _rounded(spot),
            "dividend_yield": _rounded(dividend_yield),
            "rate": _rounded(rate),
            "vol_surface": surface,
            "chain_definition": chain_def,
        },
        "chain": chain,
        "chain_summary": _chain_summary(chain),
        "safety_assertions": {
            "no_registry_write": True,
            "no_order_route": True,
            "no_broker_session": True,
            "no_capital_binding": True,
            "deployment_stage_remains_none": True,
            "artifact_state_remains_draft": True,
            "research_only_not_direct_action": True,
            "cpu_only": True,
            "no_gpu": True,
        },
    }
    checksum = f"sha256:{_sha256_json(core_payload)}"
    registry_entry = _build_registry_entry(
        registry_id=registry_id,
        checksum=checksum,
        source_run_id=run_id,
        source_dataset_refs=source_dataset_refs,
        source_strategy_spec_id=source_strategy_spec_id,
        generated_at=generated_at,
        chain_summary=core_payload["chain_summary"],
    )
    snapshot = {
        **core_payload,
        "checksum": checksum,
        "registry_entry": registry_entry,
        "pricing_snapshot_ref": _build_pricing_snapshot_ref(registry_entry),
    }
    _assert_snapshot(snapshot)
    return snapshot


def emit_pricing_snapshot(
    output_path: str | Path = DEFAULT_PRICING_SNAPSHOT_PATH,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Write the default deterministic pricing snapshot and return it."""

    snapshot = build_pricing_snapshot(created_at=created_at)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return snapshot


def _expand_contracts(chain_definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    explicit_contracts = chain_definition.get("contracts")
    if explicit_contracts is not None:
        if not isinstance(explicit_contracts, Sequence) or isinstance(explicit_contracts, (str, bytes)):
            raise ProductionOptionChainError("chain_definition.contracts must be a sequence")
        return [_normalize_contract(contract) for contract in explicit_contracts]

    strikes = _numeric_sequence(chain_definition.get("strikes"), "chain_definition.strikes")
    expiries = _text_sequence(chain_definition.get("expiries"), field_name="chain_definition.expiries")
    call_put_values = [
        _normalize_call_put(item)
        for item in _text_sequence(chain_definition.get("call_put"), default=["call", "put"])
    ]
    contracts: list[dict[str, Any]] = []
    for expiry in expiries:
        for strike in strikes:
            for call_put in call_put_values:
                contracts.append(
                    {
                        "contract_id": f"{chain_definition.get('underlying', 'TXO')}-{expiry}-{int(strike)}-{call_put}",
                        "strike": strike,
                        "expiry": expiry,
                        "call_put": call_put,
                    }
                )
    return contracts


def _normalize_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionOptionChainError("each contract must be a mapping")
    return {
        **dict(value),
        "strike": value.get("strike"),
        "expiry": value.get("expiry"),
        "call_put": value.get("call_put"),
    }


def _resolve_vol(vol_surface: Mapping[str, Any] | float, *, expiry: str, strike: float) -> float:
    if isinstance(vol_surface, (int, float)):
        return _positive_float(vol_surface, "vol_surface")
    if not isinstance(vol_surface, Mapping):
        raise ProductionOptionChainError("vol_surface must be a number or mapping")

    strike_key = _strike_key(strike)
    points = vol_surface.get("points", [])
    if isinstance(points, Sequence) and not isinstance(points, (str, bytes)):
        for point in points:
            if not isinstance(point, Mapping):
                continue
            if str(point.get("expiry")) == expiry and _strike_key(point.get("strike")) == strike_key:
                return _positive_float(point.get("vol"), "vol_surface.points.vol")

    grid = vol_surface.get("grid", {})
    if isinstance(grid, Mapping):
        expiry_grid = grid.get(expiry, {})
        if isinstance(expiry_grid, Mapping) and strike_key in {_strike_key(key) for key in expiry_grid.keys()}:
            for key, value in expiry_grid.items():
                if _strike_key(key) == strike_key:
                    return _positive_float(value, "vol_surface.grid")

    by_expiry = vol_surface.get("by_expiry", {})
    by_strike = vol_surface.get("by_strike", {})
    if isinstance(by_expiry, Mapping) and expiry in by_expiry:
        expiry_vol = _positive_float(by_expiry[expiry], "vol_surface.by_expiry")
    else:
        expiry_vol = None
    strike_vol = None
    if isinstance(by_strike, Mapping):
        for key, value in by_strike.items():
            if _strike_key(key) == strike_key:
                strike_vol = _positive_float(value, "vol_surface.by_strike")
                break
    if expiry_vol is not None and strike_vol is not None:
        return _positive_float((expiry_vol + strike_vol) / 2.0, "vol_surface.blended")
    if expiry_vol is not None:
        return expiry_vol
    if strike_vol is not None:
        return strike_vol

    for key in ("default_vol", "flat_vol", "volatility"):
        if key in vol_surface:
            return _positive_float(vol_surface[key], f"vol_surface.{key}")
    raise ProductionOptionChainError("vol_surface must provide default_vol, flat_vol, volatility, or matching points")


def _black_scholes_merton(
    spot: float,
    strike: float,
    rate: float,
    dividend_yield: float,
    vol: float,
    tenor: float,
    call_put: CallPut,
) -> dict[str, float]:
    sqrt_t = math.sqrt(tenor)
    discount_rate = math.exp(-rate * tenor)
    discount_dividend = math.exp(-dividend_yield * tenor)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend_yield + 0.5 * vol * vol) * tenor
    ) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    pdf_d1 = _norm_pdf(d1)

    if call_put == "call":
        price = spot * discount_dividend * _norm_cdf(d1) - strike * discount_rate * _norm_cdf(d2)
        delta = discount_dividend * _norm_cdf(d1)
        theta_annual = (
            -(spot * discount_dividend * pdf_d1 * vol) / (2.0 * sqrt_t)
            - rate * strike * discount_rate * _norm_cdf(d2)
            + dividend_yield * spot * discount_dividend * _norm_cdf(d1)
        )
    else:
        price = strike * discount_rate * _norm_cdf(-d2) - spot * discount_dividend * _norm_cdf(-d1)
        delta = discount_dividend * (_norm_cdf(d1) - 1.0)
        theta_annual = (
            -(spot * discount_dividend * pdf_d1 * vol) / (2.0 * sqrt_t)
            + rate * strike * discount_rate * _norm_cdf(-d2)
            - dividend_yield * spot * discount_dividend * _norm_cdf(-d1)
        )

    gamma = discount_dividend * pdf_d1 / (spot * vol * sqrt_t)
    vega = spot * discount_dividend * pdf_d1 * sqrt_t
    return {
        "price": price,
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta_annual / 365.0,
    }


def _build_registry_entry(
    *,
    registry_id: str,
    checksum: str,
    source_run_id: str,
    source_dataset_refs: list[str],
    source_strategy_spec_id: str,
    generated_at: str,
    chain_summary: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "registry_id": registry_id,
        "artifact_type": "pricing_snapshot",
        "strategy_id": DEFAULT_STRATEGY_ID,
        "version": ARTIFACT_VERSION,
        "artifact_state": "draft",
        "lineage": {
            "parent_registry_ids": [],
            "source_run_ids": [source_run_id],
            "source_dataset_refs": source_dataset_refs,
            "source_strategy_spec_id": source_strategy_spec_id,
        },
        "storage_ref": {
            "backend": "inline",
            "path": "$.pricing_snapshot",
        },
        "checksum": checksum,
        "producer_run_id": source_run_id,
        "deployment_summary": {
            "current_stage": "none",
            "last_transition_at": generated_at,
        },
        "metadata": {
            "task_id": TASK_ID,
            "backend_id": BACKEND_ID,
            "code_version": CODE_VERSION,
            "chain_summary": copy.deepcopy(dict(chain_summary)),
            "cpu_only": True,
            "gpu_required": False,
        },
    }


def _build_pricing_snapshot_ref(registry_entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_name": "pricing_snapshot",
        "artifact_type": "pricing_snapshot",
        "artifact_ref": f"{registry_entry['registry_id']}@{registry_entry['version']}",
        "registry_id": registry_entry["registry_id"],
        "version": registry_entry["version"],
        "artifact_state": registry_entry["artifact_state"],
        "deployment_stage": registry_entry["deployment_summary"]["current_stage"],
        "storage_ref": copy.deepcopy(registry_entry["storage_ref"]),
        "checksum": registry_entry["checksum"],
        "source_run_id": registry_entry["producer_run_id"],
        "source_dataset_refs": copy.deepcopy(registry_entry["lineage"]["source_dataset_refs"]),
        "source_strategy_spec_id": registry_entry["lineage"]["source_strategy_spec_id"],
        "strategy_id": registry_entry["strategy_id"],
    }


def _chain_summary(chain: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    strikes = sorted({_rounded(row["strike"]) for row in chain})
    expiries = sorted({str(row["expiry"]) for row in chain})
    call_count = sum(1 for row in chain if row.get("call_put") == "call")
    put_count = sum(1 for row in chain if row.get("call_put") == "put")
    return {
        "contract_count": len(chain),
        "strike_count": len(strikes),
        "expiry_count": len(expiries),
        "call_count": call_count,
        "put_count": put_count,
        "min_strike": strikes[0] if strikes else None,
        "max_strike": strikes[-1] if strikes else None,
        "first_expiry": expiries[0] if expiries else None,
        "last_expiry": expiries[-1] if expiries else None,
    }


def _assert_snapshot(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("artifact_type") != "pricing_snapshot":
        raise ProductionOptionChainError("snapshot artifact_type must be pricing_snapshot")
    if not str(snapshot.get("checksum") or "").startswith("sha256:"):
        raise ProductionOptionChainError("snapshot checksum must be sha256-prefixed")
    chain_summary = _mapping(snapshot.get("chain_summary"))
    if chain_summary.get("strike_count", 0) < 5:
        raise ProductionOptionChainError("pricing_snapshot must include at least 5 strikes")
    if chain_summary.get("expiry_count", 0) < 3:
        raise ProductionOptionChainError("pricing_snapshot must include at least 3 expiries")
    if chain_summary.get("call_count", 0) == 0 or chain_summary.get("put_count", 0) == 0:
        raise ProductionOptionChainError("pricing_snapshot must include calls and puts")
    registry_entry = _mapping(snapshot.get("registry_entry"))
    if registry_entry.get("artifact_type") != "pricing_snapshot":
        raise ProductionOptionChainError("registry_entry.artifact_type must be pricing_snapshot")
    if registry_entry.get("checksum") != snapshot.get("checksum"):
        raise ProductionOptionChainError("registry_entry.checksum must match snapshot checksum")
    safety = _mapping(snapshot.get("safety_assertions"))
    for key in (
        "no_registry_write",
        "no_order_route",
        "no_broker_session",
        "no_capital_binding",
        "deployment_stage_remains_none",
        "research_only_not_direct_action",
    ):
        if safety.get(key) is not True:
            raise ProductionOptionChainError(f"safety_assertions.{key} must be true")


def _registry_id(chain_definition: Mapping[str, Any]) -> str:
    underlying = str(chain_definition.get("underlying") or "txo").lower()
    return f"quantlib-production-option-chain-{underlying}-{ARTIFACT_VERSION}"


def _positive_float(value: Any, field_name: str) -> float:
    numeric = _finite_float(value, field_name)
    if numeric <= 0.0:
        raise ProductionOptionChainError(f"{field_name} must be positive")
    return numeric


def _finite_float(value: Any, field_name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ProductionOptionChainError(f"{field_name} must be numeric") from exc
    if not math.isfinite(numeric):
        raise ProductionOptionChainError(f"{field_name} must be finite")
    return numeric


def _numeric_sequence(value: Any, field_name: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProductionOptionChainError(f"{field_name} must be a sequence")
    numbers = [_positive_float(item, field_name) for item in value]
    if not numbers:
        raise ProductionOptionChainError(f"{field_name} must not be empty")
    return numbers


def _text_sequence(
    value: Any,
    *,
    field_name: str = "value",
    default: Sequence[str] | None = None,
) -> list[str]:
    if value is None and default is not None:
        return [str(item) for item in default]
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = [str(item) for item in value]
    else:
        raise ProductionOptionChainError(f"{field_name} must be a string or sequence")
    values = [item.strip() for item in values if item.strip()]
    if not values:
        raise ProductionOptionChainError(f"{field_name} must not be empty")
    return values


def _normalize_call_put(value: Any) -> CallPut:
    normalized = str(value or "").strip().lower()
    if normalized not in {"call", "put"}:
        raise ProductionOptionChainError("call_put must be 'call' or 'put'")
    return normalized  # type: ignore[return-value]


def _parse_date(value: Any, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ProductionOptionChainError(f"{field_name} must be an ISO date") from exc


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _strike_key(value: Any) -> str:
    return str(int(round(float(value))))


def _rounded(value: Any) -> float:
    return round(float(value), 8)


def _sha256_json(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _norm_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit OSS-QUANTLIB-V2-001 pricing_snapshot.json.")
    parser.add_argument("--output", type=Path, default=DEFAULT_PRICING_SNAPSHOT_PATH)
    parser.add_argument("--created-at")
    args = parser.parse_args(argv)

    snapshot = emit_pricing_snapshot(args.output, created_at=args.created_at)
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BACKEND_ID",
    "TASK_ID",
    "ProductionOptionChainError",
    "build_pricing_snapshot",
    "default_txo_chain_definition",
    "default_vol_surface",
    "emit_pricing_snapshot",
    "price_chain",
    "utc_now",
]
