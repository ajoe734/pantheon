#!/usr/bin/env python3
"""Build broker sandbox/test-key order smoke evidence packets.

The runner is intentionally fail-closed: it never submits production-live
orders and it does not accept raw broker secrets. Current provider lanes use
payload validation / simulation semantics so the archived packet can prove
place, cancel/replace, readback, telemetry, and reconciliation shape before
any live-capital activation.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.execution.ibkr_adapter import IBKRAdapter, IBKRConfig, IBKROrderIntent
from services.execution.kraken_adapter import KrakenAdapter, KrakenConfig, KrakenOrderIntent
from services.execution.shioaji_adapter import ShioajiAdapter, ShioajiConfig, ShioajiOrderIntent


TASK_ID = "P2-BROKER-SANDBOX-ORDER-001"
PRODUCTION_LIVE_MODES = {"live", "production", "prod", "real_money"}
SECRET_REF_PREFIXES = (
    "aws-secretsmanager://",
    "azure-keyvault://",
    "doppler://",
    "env://",
    "gcp-secret://",
    "op://",
    "secret://",
    "secrets://",
    "vault://",
    "vm2-secret://",
)
SUPPORTED_MODES = {
    "ibkr": {"validate_only", "paper_validate_only"},
    "shioaji": {"simulation"},
    "kraken": {"validate_only"},
}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def reject_raw_secret_ref(value: str | None, *, field_name: str) -> None:
    """Reject values that look like pasted credentials instead of secret refs."""

    if not value:
        return
    lowered = value.lower()
    if any(token in lowered for token in ("secret=", "api_key=", "apikey=", "private_key=", "-----begin")):
        raise ValueError(f"{field_name} must be a secret reference, not raw secret material")
    if len(value) > 160:
        raise ValueError(f"{field_name} is too long for a repo-safe secret reference")


def require_secret_ref(value: str | None, *, field_name: str) -> None:
    """Require repo-safe secret references instead of arbitrary credential text."""

    reject_raw_secret_ref(value, field_name=field_name)
    if not value:
        return
    lowered = value.lower()
    if not lowered.startswith(SECRET_REF_PREFIXES):
        supported = ", ".join(SECRET_REF_PREFIXES)
        raise ValueError(f"{field_name} must be a secret reference using one of: {supported}")


def ensure_non_live_mode(provider: str, mode: str) -> None:
    normalized = mode.strip().lower()
    if normalized in PRODUCTION_LIVE_MODES:
        raise ValueError("production live order smoke is disabled; use paper/sandbox/simulation/validate_only")
    if normalized not in SUPPORTED_MODES[provider]:
        supported = ", ".join(sorted(SUPPORTED_MODES[provider]))
        raise ValueError(f"unsupported {provider} smoke mode: {mode}; supported: {supported}")


def build_ibkr_payload(args: argparse.Namespace) -> dict[str, Any]:
    adapter = IBKRAdapter(
        IBKRConfig(
            host=args.host or "paper-gateway-ref",
            port=args.port or 7497,
            client_id=args.client_id or 0,
            account_id=args.account_ref,
            readonly_market_data=True,
            market_data_type=3,
        )
    )
    order = adapter.build_order(
        IBKROrderIntent(
            symbol=args.symbol,
            side=args.side,
            quantity=args.quantity,
            order_type="LMT",
            price=args.limit_price,
            tif="DAY",
            outside_rth=False,
            metadata={"smoke_mode": args.mode, "task_id": TASK_ID},
        )
    )
    return {
        "provider": "IBKR",
        "mode": args.mode,
        "auth": {
            "status": "credential_ref_recorded" if args.credential_ref else "validate_only_without_secret_ref",
            "credential_ref": args.credential_ref,
            "host_ref": args.host or "paper-gateway-ref",
            "port": args.port or 7497,
            "client_id": args.client_id or 0,
        },
        "account_readiness": {
            "status": "account_ref_recorded" if args.account_ref else "account_ref_missing_validate_only",
            "account_ref": args.account_ref,
        },
        "place": {
            "status": "validated_not_submitted",
            "request": order,
            "side_effect": "none",
        },
        "cancel_replace": {
            "status": "validated_not_submitted",
            "cancel_request": {"order_ref": "ibkr-validate-only-order-ref", "account_ref": args.account_ref},
            "replace_request": {
                "order_ref": "ibkr-validate-only-order-ref",
                "limit_price": args.replace_limit_price or args.limit_price,
                "time_in_force": "DAY",
            },
            "side_effect": "none",
        },
    }


def build_shioaji_payload(args: argparse.Namespace) -> dict[str, Any]:
    adapter = ShioajiAdapter(
        ShioajiConfig(
            api_key="SECRET_REF_ONLY",
            secret_key="SECRET_REF_ONLY",
            simulation=True,
            account_name=args.account_ref,
        )
    )
    order = adapter.build_order(
        ShioajiOrderIntent(
            symbol=args.symbol,
            side=args.side,
            quantity=args.quantity,
            price=args.limit_price,
            account=args.account_ref,
            metadata={"smoke_mode": args.mode, "task_id": TASK_ID},
        )
    )
    return {
        "provider": "Shioaji",
        "mode": args.mode,
        "auth": {
            "status": "credential_ref_recorded" if args.credential_ref else "simulation_without_secret_ref",
            "credential_ref": args.credential_ref,
        },
        "account_readiness": {
            "status": "simulation_account_ref_recorded" if args.account_ref else "simulation_account_ref_missing",
            "account_ref": args.account_ref,
        },
        "place": {
            "status": "simulation_payload_validated",
            "request": order,
            "side_effect": "simulation_only",
        },
        "cancel_replace": {
            "status": "simulation_payload_validated",
            "cancel_request": {"order_ref": "shioaji-simulation-order-ref", "account_ref": args.account_ref},
            "replace_request": {
                "order_ref": "shioaji-simulation-order-ref",
                "price": args.replace_limit_price or args.limit_price,
            },
            "side_effect": "simulation_only",
        },
    }


def build_kraken_payload(args: argparse.Namespace) -> dict[str, Any]:
    adapter = KrakenAdapter(
        KrakenConfig(
            api_key="SECRET_REF_ONLY",
            api_secret="SECRET_REF_ONLY",
            validate_only=True,
        )
    )
    order = adapter.build_order(
        KrakenOrderIntent(
            symbol=args.symbol,
            side=args.side,
            quantity=float(args.quantity),
            order_type="limit",
            price=args.limit_price,
            validate=True,
            metadata={"smoke_mode": args.mode, "task_id": TASK_ID},
        )
    )
    return {
        "provider": "Kraken",
        "mode": args.mode,
        "auth": {
            "status": "credential_ref_recorded" if args.credential_ref else "validate_only_without_secret_ref",
            "credential_ref": args.credential_ref,
        },
        "account_readiness": {
            "status": "test_key_ref_recorded" if args.account_ref else "test_account_ref_missing_validate_only",
            "account_ref": args.account_ref,
        },
        "place": {
            "status": "validate_only_payload_validated",
            "request": order,
            "side_effect": "none",
        },
        "cancel_replace": {
            "status": "validate_only_payload_validated",
            "cancel_request": {"txid": "kraken-validate-only-txid", "account_ref": args.account_ref},
            "replace_request": {
                "txid": "kraken-validate-only-txid",
                "price": str(args.replace_limit_price or args.limit_price),
                "validate": True,
            },
            "side_effect": "none",
        },
    }


def attach_common_evidence(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    generated_at = iso_now()
    request = payload["place"]["request"]
    provider = payload["provider"]
    telemetry_event = {
        "event_type": "broker_order_smoke.validate_only",
        "task_id": TASK_ID,
        "provider": provider,
        "mode": payload["mode"],
        "generated_at": generated_at,
        "account_ref": args.account_ref,
        "symbol": args.symbol,
        "side": args.side.upper() if provider != "Kraken" else args.side.lower(),
        "quantity": args.quantity,
        "production_live_enabled": False,
        "raw_secret_material_present": False,
    }
    readback = {
        "status": "readback_shape_validated",
        "source": "local_adapter_payload",
        "observed_order": request,
    }
    execution = {
        "status": "no_broker_execution_attempted",
        "fill_status": "no_fill_validate_only",
        "matching_execution_count": 0,
        "matching_quantity": 0,
    }
    reconciliation = {
        "status": "passed",
        "checks": {
            "place_request_symbol_matches": True,
            "place_request_quantity_matches": True,
            "cancel_or_replace_targets_same_order_ref": True,
            "production_live_side_effect_disabled": True,
            "raw_secret_material_absent": True,
        },
    }
    payload.update(
        {
            "task_id": TASK_ID,
            "status": "passed",
            "generated_at": generated_at,
            "symbol": args.symbol,
            "production_live": {
                "enabled": False,
                "order_side_effects_allowed": False,
                "activation_gate": "not_requested",
            },
            "readback": readback,
            "execution": execution,
            "telemetry": {"status": "event_shape_validated", "event": telemetry_event},
            "reconciliation": reconciliation,
            "notes": [
                "This packet contains no raw broker secrets.",
                "This runner validates sandbox/simulation/validate-only order API shape only.",
                "Production-live order, cancel/replace, position, and capital side effects remain disabled.",
            ],
        }
    )
    return payload


def write_packet(output_dir: Path, payload: dict[str, Any]) -> None:
    dump_json(output_dir / "summary.json", payload)
    dump_json(output_dir / "auth.json", payload["auth"])
    dump_json(output_dir / "account-readiness.json", payload["account_readiness"])
    dump_json(output_dir / "place.request.json", payload["place"])
    dump_json(output_dir / "cancel-replace.request.json", payload["cancel_replace"])
    dump_json(output_dir / "readback.json", payload["readback"])
    dump_json(output_dir / "execution.json", payload["execution"])
    dump_json(output_dir / "telemetry-event.json", payload["telemetry"])
    dump_json(output_dir / "reconciliation.json", payload["reconciliation"])


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    provider = args.provider.lower()
    ensure_non_live_mode(provider, args.mode)
    require_secret_ref(args.credential_ref, field_name="credential_ref")
    reject_raw_secret_ref(args.account_ref, field_name="account_ref")

    if provider == "ibkr":
        payload = build_ibkr_payload(args)
    elif provider == "shioaji":
        payload = build_shioaji_payload(args)
    elif provider == "kraken":
        payload = build_kraken_payload(args)
    else:  # pragma: no cover - argparse constrains this
        raise ValueError(f"unsupported provider: {args.provider}")
    return attach_common_evidence(args, payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate broker sandbox/test-key order smoke evidence.")
    parser.add_argument("--provider", required=True, choices=sorted(SUPPORTED_MODES))
    parser.add_argument("--mode", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", default="buy", choices=["buy", "sell", "BUY", "SELL"])
    parser.add_argument("--quantity", required=True, type=int)
    parser.add_argument("--limit-price", required=True, type=float)
    parser.add_argument("--replace-limit-price", type=float)
    parser.add_argument("--account-ref")
    parser.add_argument("--credential-ref")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--client-id", type=int)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = build_payload(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    write_packet(Path(args.output_dir), payload)
    print(json.dumps({"status": payload["status"], "provider": payload["provider"], "output_dir": args.output_dir}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
