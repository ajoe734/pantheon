#!/usr/bin/env python3
"""Prepare and rehearse the EP5-001 canary-ready entry path.

This script is prerequisite-only tooling. It packages three things:

1. a runnable operator checklist
2. a concrete canary DeploymentPlan artifact with policy-default scale guards
3. a rollback drill harness that can archive dry-run or real request/response
   artifacts without claiming EP5 proof by itself
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE_DIR = ROOT / "services" / "control-plane" / "governance"
if str(GOVERNANCE_DIR) not in sys.path:
    sys.path.insert(0, str(GOVERNANCE_DIR))
EXECUTION_DIR = ROOT / "services" / "execution"
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))
RESEARCH_ADAPTERS_DIR = ROOT / "services" / "research" / "adapters"
if str(RESEARCH_ADAPTERS_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ADAPTERS_DIR))

from deployment_plan import DeploymentScale, RollbackRef, StagePlanner  # noqa: E402
from ibkr_adapter import IBKRAdapter, IBKRConfig, IBKROrderIntent  # noqa: E402
from kraken_adapter import KrakenAdapter, KrakenConfig, KrakenOrderIntent  # noqa: E402
from shioaji_adapter import ShioajiAdapter, ShioajiConfig, ShioajiOrderIntent  # noqa: E402
from taiwan_market_client import TaiwanMarketClient  # noqa: E402


class ReadinessError(RuntimeError):
    """Raised when the readiness bundle cannot be produced truthfully."""


@dataclass
class ChecklistItem:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": "pass" if self.passed else "fail",
            "detail": self.detail,
        }


@dataclass(frozen=True)
class GovernedProviderSpec:
    key: str
    env_key: str
    expected_value: str
    secret_name_keys: tuple[str, ...]
    role: str


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


GOVERNED_PROVIDER_SPECS = (
    GovernedProviderSpec(
        key="ibkr",
        env_key="EXECUTION_BROKER_PROVIDER",
        expected_value="IBKR",
        secret_name_keys=("BROKER_API_KEY_SECRET_NAME", "BROKER_API_SECRET_SECRET_NAME"),
        role="US broker execution",
    ),
    GovernedProviderSpec(
        key="shioaji",
        env_key="TW_EXECUTION_PROVIDER",
        expected_value="Shioaji",
        secret_name_keys=("SHIOAJI_API_KEY_SECRET_NAME", "SHIOAJI_SECRET_KEY_SECRET_NAME"),
        role="Taiwan broker execution",
    ),
    GovernedProviderSpec(
        key="kraken",
        env_key="CRYPTO_EXECUTION_PROVIDER",
        expected_value="Kraken",
        secret_name_keys=("KRAKEN_API_KEY_SECRET_NAME", "KRAKEN_API_SECRET_SECRET_NAME"),
        role="Crypto venue execution",
    ),
    GovernedProviderSpec(
        key="tej",
        env_key="TW_RESEARCH_PROVIDER",
        expected_value="TEJ",
        secret_name_keys=("TEJ_API_KEY_SECRET_NAME",),
        role="Taiwan research-grade vendor",
    ),
)


def sanitize_headers(headers: dict[str, str] | None) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if key.lower() == "authorization":
            sanitized[key] = "Bearer ***redacted***"
        else:
            sanitized[key] = value
    return sanitized


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_env_map(env_file: str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file:
        values.update(parse_env_file(Path(env_file)))
    for key, value in os.environ.items():
        values[key] = value
    return values


def make_output_dir(path_str: str | None, prefix: str) -> Path:
    if path_str:
        path = Path(path_str)
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(tempfile.mkdtemp(prefix=prefix))


def bool_from_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def require(env_map: dict[str, str], key: str) -> str:
    value = str(env_map.get(key, "")).strip()
    if not value:
        raise ReadinessError(f"Missing required value: {key}")
    return value


def optional(env_map: dict[str, str], key: str, default: str = "") -> str:
    return str(env_map.get(key, default)).strip()


def parse_float(env_map: dict[str, str], key: str, *, default: float | None = None) -> float:
    raw = optional(env_map, key)
    if not raw:
        if default is None:
            raise ReadinessError(f"Missing numeric value: {key}")
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ReadinessError(f"{key} must be numeric, got {raw!r}") from exc


def normalize_token(value: str | None) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "").replace("/", "")


def evaluate_provider_matrix(env_map: dict[str, str]) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []
    missing_refs: list[str] = []
    mismatched_refs: list[str] = []
    missing_secret_names: list[str] = []

    for spec in GOVERNED_PROVIDER_SPECS:
        configured = optional(env_map, spec.env_key)
        if not configured:
            missing_refs.append(spec.env_key)
        elif normalize_token(configured) != normalize_token(spec.expected_value):
            mismatched_refs.append(f"{spec.env_key}={configured}")
        for secret_key in spec.secret_name_keys:
            if not optional(env_map, secret_key):
                missing_secret_names.append(secret_key)

        items.append(
            ChecklistItem(
                f"{spec.key}_provider_declared",
                normalize_token(configured) == normalize_token(spec.expected_value),
                f"{spec.role}: {spec.env_key}={configured or '<missing>'}",
            )
        )

    matrix_passed = not missing_refs and not mismatched_refs
    matrix_detail_parts = []
    if missing_refs:
        matrix_detail_parts.append("missing refs: " + ", ".join(missing_refs))
    if mismatched_refs:
        matrix_detail_parts.append("mismatched refs: " + ", ".join(mismatched_refs))
    if not matrix_detail_parts:
        matrix_detail_parts.append("governed provider refs declared for IBKR, Shioaji, Kraken, and TEJ")
    items.append(
        ChecklistItem(
            "governed_provider_matrix_declared",
            matrix_passed,
            "; ".join(matrix_detail_parts),
        )
    )
    items.append(
        ChecklistItem(
            "governed_provider_secret_refs_present",
            not missing_secret_names,
            "missing: " + ", ".join(missing_secret_names)
            if missing_secret_names
            else "tracked secret-name refs exist for IBKR, Shioaji, Kraken, and TEJ",
        )
    )
    return items


def request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
) -> tuple[int, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed


def write_http_artifact(
    output_dir: Path,
    stem: str,
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    allowed_statuses: set[int] | None = None,
    timeout: int = 15,
) -> tuple[int, Any]:
    dump_json(
        output_dir / f"{stem}.request.json",
        {"method": method, "url": url, "headers": sanitize_headers(headers), "body": body},
    )
    status, payload = request_json(method, url, body=body, headers=headers, timeout=timeout)
    dump_json(output_dir / f"{stem}.response.json", {"status": status, "payload": payload})
    if allowed_statuses is not None and status not in allowed_statuses:
        raise ReadinessError(
            f"{stem} failed with HTTP {status}: {json.dumps(payload, ensure_ascii=False)}"
        )
    return status, payload


def infer_local_url(env_map: dict[str, str], env_key: str, port_key: str) -> str | None:
    explicit = optional(env_map, env_key)
    if explicit:
        return explicit
    port = optional(env_map, port_key)
    if not port:
        return None
    return f"http://127.0.0.1:{port}"


def evaluate_checklist(
    env_map: dict[str, str],
    *,
    allow_empty_secrets: bool,
    check_health: bool,
) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []

    execution_mode = optional(env_map, "PANTHEON_EXECUTION_MODE")
    items.append(
        ChecklistItem(
            "execution_mode",
            execution_mode == "canary",
            f"PANTHEON_EXECUTION_MODE={execution_mode or '<missing>'}",
        )
    )

    broker_mode = optional(env_map, "BROKER_ADAPTER_MODE")
    exchange_mode = optional(env_map, "EXCHANGE_ADAPTER_MODE")
    items.append(
        ChecklistItem(
            "real_broker_and_exchange_modes",
            broker_mode == "real" and exchange_mode == "real",
            f"BROKER_ADAPTER_MODE={broker_mode or '<missing>'}, EXCHANGE_ADAPTER_MODE={exchange_mode or '<missing>'}",
        )
    )

    secrets_optional = optional(env_map, "PANTHEON_SECRETS_OPTIONAL")
    items.append(
        ChecklistItem(
            "secrets_boundary_enforced",
            secrets_optional.lower() == "false",
            f"PANTHEON_SECRETS_OPTIONAL={secrets_optional or '<missing>'}",
        )
    )
    items.extend(evaluate_provider_matrix(env_map))

    boundary_refs = [
        "CANARY_BROKER_ACCOUNT_REF",
        "CANARY_VENUE_REF",
        "CANARY_POOL_ID",
        "CANARY_APPROVAL_DECISION_ID",
        "CANARY_PERSONA_CAPITAL_BINDING_ID",
        "CANARY_HUMAN_GATE_PACKET_REF",
        "CANARY_BROKER_SANDBOX_SMOKE_REF",
        "CANARY_RISK_OWNER_APPROVAL_REF",
        "CANARY_OPERATOR_APPROVAL_REF",
        "CANARY_REGISTRY_ID",
        "CANARY_REGISTRY_VERSION",
        "CANARY_FALLBACK_ARTIFACT_ID",
        "CANARY_FALLBACK_ARTIFACT_VERSION",
    ]
    missing_refs = [key for key in boundary_refs if not optional(env_map, key)]
    items.append(
        ChecklistItem(
            "operator_owned_refs_present",
            not missing_refs,
            "missing: " + ", ".join(missing_refs) if missing_refs else "all required ids and refs are present",
        )
    )

    capital_scale = parse_float(env_map, "CANARY_CAPITAL_SCALE_PCT", default=0.0)
    gross_scale = parse_float(env_map, "CANARY_GROSS_SCALE_PCT", default=0.0)
    scale_ok = 0 < capital_scale <= 5 and 0 < gross_scale <= 25
    items.append(
        ChecklistItem(
            "canary_scale_within_policy",
            scale_ok,
            f"capital_scale_pct={capital_scale}, gross_scale_pct={gross_scale}",
        )
    )

    rollback_action = optional(env_map, "CANARY_ROLLBACK_ACTION_TYPE", "pause_then_replace")
    rollback_ok = rollback_action in {"replace", "pause_then_replace", "liquidate_then_replace"}
    items.append(
        ChecklistItem(
            "rollback_action_type_valid",
            rollback_ok,
            f"CANARY_ROLLBACK_ACTION_TYPE={rollback_action or '<missing>'}",
        )
    )

    secret_keys = [
        "BROKER_API_KEY",
        "BROKER_API_SECRET",
        "EXCHANGE_API_KEY",
        "EXCHANGE_API_SECRET",
    ]
    if allow_empty_secrets:
        secret_name_keys = [
            "BROKER_API_KEY_SECRET_NAME",
            "BROKER_API_SECRET_SECRET_NAME",
            "EXCHANGE_API_KEY_SECRET_NAME",
            "EXCHANGE_API_SECRET_SECRET_NAME",
        ]
        missing_secret_names = [key for key in secret_name_keys if not optional(env_map, key)]
        items.append(
            ChecklistItem(
                "vm2_secret_name_refs_present",
                not missing_secret_names,
                "missing: " + ", ".join(missing_secret_names)
                if missing_secret_names
                else "tracked example uses secret-name refs instead of raw values",
            )
        )
    else:
        missing_secrets = [key for key in secret_keys if not optional(env_map, key)]
        items.append(
            ChecklistItem(
                "vm2_secret_material_present",
                not missing_secrets,
                "missing: " + ", ".join(missing_secrets) if missing_secrets else "all VM-2 secret values are populated",
            )
        )

    runtime_url = optional(env_map, "PANTHEON_RUNTIME_MANAGER_URL")
    runtime_token = optional(env_map, "PANTHEON_RUNTIME_MANAGER_TOKEN")
    items.append(
        ChecklistItem(
            "runtime_manager_access_configured",
            bool(runtime_url and runtime_token),
            f"url={runtime_url or '<missing>'}, token={'set' if runtime_token else '<missing>'}",
        )
    )

    if check_health:
        health_targets = [
            ("runtime_manager_health", optional(env_map, "PANTHEON_RUNTIME_MANAGER_URL")),
            ("telemetry_health", optional(env_map, "PANTHEON_TELEMETRY_URL")),
            ("broker_adapter_health", infer_local_url(env_map, "PANTHEON_BROKER_ADAPTER_URL", "BROKER_ADAPTER_PORT")),
            ("exchange_adapter_health", infer_local_url(env_map, "PANTHEON_EXCHANGE_ADAPTER_URL", "EXCHANGE_ADAPTER_PORT")),
        ]
        headers = {"Authorization": f"Bearer {runtime_token}"} if runtime_token else {}
        for name, base_url in health_targets:
            if not base_url:
                items.append(ChecklistItem(name, False, "URL unavailable"))
                continue
            url = f"{base_url.rstrip('/')}/__health__"
            try:
                status, payload = request_json("GET", url, headers=headers if "runtime_manager" in name else None, timeout=5)
                passed = status == 200 and isinstance(payload, dict) and payload.get("status") == "ok"
                detail = f"HTTP {status}"
            except Exception as exc:  # noqa: BLE001
                passed = False
                detail = f"{type(exc).__name__}: {exc}"
            items.append(ChecklistItem(name, passed, detail))

    return items


def command_run_operator_checklist(args: argparse.Namespace) -> int:
    env_map = load_env_map(args.env_file)
    output_dir = make_output_dir(args.output_dir, "pantheon-ep5-checklist-")
    items = evaluate_checklist(
        env_map,
        allow_empty_secrets=args.allow_empty_secrets,
        check_health=args.check_health,
    )
    passed = all(item.passed for item in items)
    payload = {
        "task_id": "EP5-001",
        "generated_at": iso_now(),
        "mode": "operator_checklist",
        "env_file": args.env_file,
        "status": "pass" if passed else "fail",
        "allow_empty_secrets": args.allow_empty_secrets,
        "check_health": args.check_health,
        "items": [item.to_dict() for item in items],
    }
    dump_json(output_dir / "operator-checklist.json", payload)
    print(json.dumps({"status": payload["status"], "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0 if passed else 1


def build_datasource_smoke_payloads(env_map: dict[str, str]) -> dict[str, Any]:
    ibkr_provider = optional(env_map, "EXECUTION_BROKER_PROVIDER")
    shioaji_provider = optional(env_map, "TW_EXECUTION_PROVIDER")
    kraken_provider = optional(env_map, "CRYPTO_EXECUTION_PROVIDER")
    tej_provider = optional(env_map, "TW_RESEARCH_PROVIDER")

    ibkr_adapter = IBKRAdapter(
        IBKRConfig(
            host="127.0.0.1",
            port=7497,
            client_id=7,
            account_id=optional(env_map, "CANARY_BROKER_ACCOUNT_REF") or None,
        )
    )
    ibkr_symbol = optional(env_map, "IBKR_SMOKE_SYMBOL", "AAPL.US")
    ibkr_order = ibkr_adapter.build_order(IBKROrderIntent(symbol=ibkr_symbol, side="buy", quantity=1))
    ibkr_market_data = ibkr_adapter.build_market_data_request(ibkr_symbol, snapshot=True)
    ibkr_quote = ibkr_adapter.normalize_quote(
        {
            "ts": optional(env_map, "IBKR_SMOKE_TS", "2026-04-24T17:00:00Z"),
            "last": optional(env_map, "IBKR_SMOKE_LAST", "183.42"),
            "close": optional(env_map, "IBKR_SMOKE_CLOSE", "183.10"),
            "bid": optional(env_map, "IBKR_SMOKE_BID", "183.40"),
            "ask": optional(env_map, "IBKR_SMOKE_ASK", "183.45"),
            "volume": optional(env_map, "IBKR_SMOKE_VOLUME", "10234"),
        },
        ibkr_symbol,
    ).to_dict()

    shioaji_adapter = ShioajiAdapter(
        ShioajiConfig(
            api_key=optional(env_map, "SHIOAJI_API_KEY", "placeholder"),
            secret_key=optional(env_map, "SHIOAJI_SECRET_KEY", "placeholder"),
            account_name=optional(env_map, "SHIOAJI_ACCOUNT_NAME") or None,
            simulation=bool_from_env(optional(env_map, "SHIOAJI_SIMULATION", "true")),
        )
    )
    shioaji_symbol = optional(env_map, "SHIOAJI_SMOKE_SYMBOL", "2330.TWSE")
    shioaji_order = shioaji_adapter.build_order(
        ShioajiOrderIntent(symbol=shioaji_symbol, side="buy", quantity=1, price=950.0)
    )
    shioaji_quote_subscription = shioaji_adapter.build_quote_subscription(shioaji_symbol)
    shioaji_quote = shioaji_adapter.normalize_quote(
        {
            "ts": optional(env_map, "SHIOAJI_SMOKE_TS", "2026-04-24T01:30:00Z"),
            "close": optional(env_map, "SHIOAJI_SMOKE_CLOSE", "950"),
            "bid_price": optional(env_map, "SHIOAJI_SMOKE_BID", "949.5"),
            "ask_price": optional(env_map, "SHIOAJI_SMOKE_ASK", "950.5"),
            "bid_volume": optional(env_map, "SHIOAJI_SMOKE_BID_VOLUME", "25"),
            "ask_volume": optional(env_map, "SHIOAJI_SMOKE_ASK_VOLUME", "30"),
            "reference": optional(env_map, "SHIOAJI_SMOKE_REFERENCE", "945"),
            "volume": optional(env_map, "SHIOAJI_SMOKE_DAY_VOLUME", "1200"),
        },
        shioaji_symbol,
    ).to_dict()

    kraken_adapter = KrakenAdapter(
        KrakenConfig(
            api_key=optional(env_map, "KRAKEN_API_KEY", "placeholder"),
            api_secret=optional(env_map, "KRAKEN_API_SECRET", "placeholder"),
            account_type=optional(env_map, "KRAKEN_ACCOUNT_TYPE", "cash"),
            validate_only=bool_from_env(optional(env_map, "KRAKEN_VALIDATE_ONLY", "true")),
        )
    )
    kraken_symbol = optional(env_map, "KRAKEN_SMOKE_SYMBOL", "BTC/USD.KRAKEN")
    kraken_order = kraken_adapter.build_order(
        KrakenOrderIntent(symbol=kraken_symbol, side="buy", quantity=0.01)
    )
    kraken_market_data = kraken_adapter.build_market_data_request(kraken_symbol, interval=1)
    kraken_quote = kraken_adapter.normalize_quote(
        {
            "ts": optional(env_map, "KRAKEN_SMOKE_TS", "2026-04-24T17:00:00Z"),
            "close": optional(env_map, "KRAKEN_SMOKE_CLOSE", "64320.4"),
            "bid": optional(env_map, "KRAKEN_SMOKE_BID", "64320.1"),
            "ask": optional(env_map, "KRAKEN_SMOKE_ASK", "64321.0"),
            "volume": optional(env_map, "KRAKEN_SMOKE_VOLUME", "128.55"),
        },
        kraken_symbol,
    ).to_dict()

    taiwan_client = TaiwanMarketClient(rate_limit_delay=0.0)
    tej_dataset_code = optional(env_map, "TEJ_DATASET_CODE", "TWN/APRCD1")
    tej_symbol = optional(env_map, "TEJ_SMOKE_SYMBOL", "2330")
    tej_as_of = optional(env_map, "TEJ_SMOKE_AS_OF_DATE", "2026-04-24")
    tej_record = taiwan_client.normalize_tej_dataset(
        {
            "coid": tej_symbol,
            "mdate": tej_as_of,
            "close": float(optional(env_map, "TEJ_SMOKE_CLOSE", "950.0")),
            "pe_ratio": float(optional(env_map, "TEJ_SMOKE_PE_RATIO", "18.2")),
            "foreign_holding_pct": float(optional(env_map, "TEJ_SMOKE_FOREIGN_HOLDING_PCT", "72.4")),
        },
        dataset_code=tej_dataset_code,
        api_endpoint="tej://dataset-smoke",
    ).to_dict()

    return {
        "generated_at": iso_now(),
        "providers": {
            "ibkr": {
                "configured_provider": ibkr_provider,
                "expected_provider": "IBKR",
                "provider_ok": normalize_token(ibkr_provider) == normalize_token("IBKR"),
                "secret_name_refs": {
                    "api_key": optional(env_map, "BROKER_API_KEY_SECRET_NAME"),
                    "api_secret": optional(env_map, "BROKER_API_SECRET_SECRET_NAME"),
                },
                "order_payload": ibkr_order,
                "market_data_request": ibkr_market_data,
                "normalized_quote": ibkr_quote,
            },
            "shioaji": {
                "configured_provider": shioaji_provider,
                "expected_provider": "Shioaji",
                "provider_ok": normalize_token(shioaji_provider) == normalize_token("Shioaji"),
                "secret_name_refs": {
                    "api_key": optional(env_map, "SHIOAJI_API_KEY_SECRET_NAME"),
                    "secret_key": optional(env_map, "SHIOAJI_SECRET_KEY_SECRET_NAME"),
                },
                "order_payload": shioaji_order,
                "quote_subscription": shioaji_quote_subscription,
                "normalized_quote": shioaji_quote,
            },
            "kraken": {
                "configured_provider": kraken_provider,
                "expected_provider": "Kraken",
                "provider_ok": normalize_token(kraken_provider) == normalize_token("Kraken"),
                "secret_name_refs": {
                    "api_key": optional(env_map, "KRAKEN_API_KEY_SECRET_NAME"),
                    "api_secret": optional(env_map, "KRAKEN_API_SECRET_SECRET_NAME"),
                },
                "order_payload": kraken_order,
                "market_data_request": kraken_market_data,
                "normalized_quote": kraken_quote,
            },
            "tej": {
                "configured_provider": tej_provider,
                "expected_provider": "TEJ",
                "provider_ok": normalize_token(tej_provider) == normalize_token("TEJ"),
                "secret_name_refs": {
                    "api_key": optional(env_map, "TEJ_API_KEY_SECRET_NAME"),
                },
                "dataset_code": tej_dataset_code,
                "normalized_dataset": tej_record,
            },
        },
    }


def command_run_datasource_smoke(args: argparse.Namespace) -> int:
    env_map = load_env_map(args.env_file)
    output_dir = make_output_dir(args.output_dir, "pantheon-ep5-datasource-smoke-")
    checklist_items = evaluate_provider_matrix(env_map)
    smoke_payloads = build_datasource_smoke_payloads(env_map)
    smoke_payloads["provider_checklist"] = [item.to_dict() for item in checklist_items]
    smoke_payloads["task_id"] = "APP-003-DATASOURCE-OPS-001"
    smoke_payloads["mode"] = "datasource_smoke"
    smoke_payloads["env_file"] = args.env_file
    passed = all(item.passed for item in checklist_items) and all(
        bool(payload.get("provider_ok")) for payload in smoke_payloads["providers"].values()
    )
    dump_json(output_dir / "datasource-smoke.json", smoke_payloads)
    dump_json(
        output_dir / "summary.json",
        {
            "task_id": "APP-003-DATASOURCE-OPS-001",
            "generated_at": iso_now(),
            "mode": "datasource_smoke",
            "status": "pass" if passed else "fail",
            "providers": sorted(smoke_payloads["providers"].keys()),
            "output_artifact": "datasource-smoke.json",
        },
    )
    print(json.dumps({"status": "pass" if passed else "fail", "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0 if passed else 1


def build_registry_entry(env_map: dict[str, str]) -> dict[str, Any]:
    return {
        "registry_id": require(env_map, "CANARY_REGISTRY_ID"),
        "version": require(env_map, "CANARY_REGISTRY_VERSION"),
        "artifact_type": optional(env_map, "CANARY_ARTIFACT_TYPE", "model_artifact"),
        "strategy_id": require(env_map, "CANARY_STRATEGY_ID"),
        "artifact_state": "approved",
        "checksum": require(env_map, "CANARY_ARTIFACT_CHECKSUM"),
        "deployment_summary": {"current_stage": optional(env_map, "CANARY_CURRENT_STAGE", "paper")},
        "approved_at": iso_now(),
        "created_at": iso_now(),
    }


def build_canary_plan(env_map: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    planner = StagePlanner()
    rollback = RollbackRef(
        target_artifact_id=require(env_map, "CANARY_FALLBACK_ARTIFACT_ID"),
        target_version=require(env_map, "CANARY_FALLBACK_ARTIFACT_VERSION"),
        action_type=optional(env_map, "CANARY_ROLLBACK_ACTION_TYPE", "pause_then_replace"),
        reason="EP5-001 rollback readiness target",
        verified_at=iso_now(),
    )
    scale = DeploymentScale(
        capital_scale_pct=parse_float(env_map, "CANARY_CAPITAL_SCALE_PCT"),
        gross_scale_pct=parse_float(env_map, "CANARY_GROSS_SCALE_PCT"),
        ramp_schedule=[
            item.strip()
            for item in optional(env_map, "CANARY_RAMP_SCHEDULE").split(";")
            if item.strip()
        ],
    )
    registry_entry = build_registry_entry(env_map)
    plan = planner.create_plan(
        plan_id=require(env_map, "CANARY_PLAN_ID"),
        approval_decision_id=require(env_map, "CANARY_APPROVAL_DECISION_ID"),
        registry_entry=registry_entry,
        capital_pool_id=require(env_map, "CANARY_POOL_ID"),
        current_stage=optional(env_map, "CANARY_CURRENT_STAGE", "paper"),
        target_stage=optional(env_map, "CANARY_TARGET_STAGE", "canary"),
        sponsor_persona_id=require(env_map, "CANARY_PERSONA_ID"),
        runtime_config_ref=require(env_map, "CANARY_RUNTIME_CONFIG_REF"),
        scale=scale,
        rollback=rollback,
        created_by=optional(env_map, "CANARY_OPERATOR_ID", "operator-oncall"),
        pre_checks=[
            "broker_venue_boundary_confirmed",
            "capital_gate_within_policy",
            "rollback_target_verified",
            "runtime_manager_endpoint_reachable",
        ],
        post_checks=[
            "operator_checklist_archived",
            "rollback_drill_archived",
        ],
        metadata={
            "source_task_id": "EP5-001",
            "proof_boundary": "prerequisite_only",
            "entry_bundle": "docs/deployment/ep5-canary-ready",
            "promotion_gate": {
                "promotion_gate_decision_id": require(env_map, "CANARY_APPROVAL_DECISION_ID"),
                "human_gate_packet_ref": require(env_map, "CANARY_HUMAN_GATE_PACKET_REF"),
                "broker_sandbox_smoke_ref": require(env_map, "CANARY_BROKER_SANDBOX_SMOKE_REF"),
                "risk_owner_approval_ref": require(env_map, "CANARY_RISK_OWNER_APPROVAL_REF"),
                "operator_approval_ref": require(env_map, "CANARY_OPERATOR_APPROVAL_REF"),
                "capital_scale_pct": scale.capital_scale_pct,
                "gross_scale_pct": scale.gross_scale_pct,
                "persona_capital_binding_id": require(env_map, "CANARY_PERSONA_CAPITAL_BINDING_ID"),
                "allowed_deployment_scope": optional(env_map, "CANARY_ALLOWED_DEPLOYMENT_SCOPE", "canary"),
            },
        },
    )
    projection = planner.build_execution_projection(plan, registry_entry)
    return plan.to_dict(), {
        "metadata_key": projection.metadata_key,
        "artifact_key": projection.artifact_key,
        "metadata": projection.metadata,
    }


def command_emit_canary_plan(args: argparse.Namespace) -> int:
    env_map = load_env_map(args.env_file)
    output_dir = make_output_dir(args.output_dir, "pantheon-ep5-plan-")
    plan, projection = build_canary_plan(env_map)
    dump_json(output_dir / "canary-deployment-plan.json", plan)
    dump_json(output_dir / "canary-execution-projection.json", projection)
    dump_json(
        output_dir / "summary.json",
        {
            "task_id": "EP5-001",
            "generated_at": iso_now(),
            "status": "prepared",
            "target_stage": plan["target_stage"],
            "capital_scale_pct": plan["scale"]["capital_scale_pct"],
            "gross_scale_pct": plan["scale"]["gross_scale_pct"],
            "rollback_action_type": plan["rollback"]["action_type"],
            "proof_boundary": "entry path only; not EP5 proof",
        },
    )
    print(json.dumps({"status": "prepared", "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


def build_human_gate_packet(
    *,
    checklist_path: Path,
    datasource_summary_path: Path,
    plan_path: Path,
    drill_summary_path: Path,
    broker_smoke_summary_path: Path | None,
    dual_vm_evidence_dir: Path | None,
    event_trace_status: str,
    event_trace_note: str,
) -> dict[str, Any]:
    checklist = load_json(checklist_path)
    datasource_summary = load_json(datasource_summary_path)
    plan = load_json(plan_path)
    drill_summary = load_json(drill_summary_path)
    broker_smoke_summary = load_json(broker_smoke_summary_path) if broker_smoke_summary_path else None

    checklist_ok = checklist.get("status") == "pass"
    datasource_ok = datasource_summary.get("status") == "pass"
    plan_ok = (
        plan.get("target_stage") == "canary"
        and float((plan.get("scale") or {}).get("capital_scale_pct") or 0.0) <= 5.0
        and float((plan.get("scale") or {}).get("gross_scale_pct") or 0.0) <= 25.0
        and str((plan.get("rollback") or {}).get("action_type") or "") == "pause_then_replace"
    )
    drill_ok = drill_summary.get("status") == "executed"
    broker_smoke_ok = True
    broker_smoke_detail = "No broker smoke summary was provided; retaining backward-compatible packet emission."
    if broker_smoke_summary is not None:
        no_real_capital = broker_smoke_summary.get("no_real_capital") or {}
        production_live = broker_smoke_summary.get("production_live") or {}
        live_gate = broker_smoke_summary.get("live_gate") or {}
        live_disabled = (
            live_gate.get("status") == "rejected"
            and (live_gate.get("response") or {}).get("error_code") == "SHIOAJI_LIVE_DISABLED"
        )
        if not live_gate:
            live_disabled = not bool(production_live.get("enabled"))
        broker_smoke_ok = (
            broker_smoke_summary.get("status") in {"pass", "passed"}
            and normalize_token(broker_smoke_summary.get("provider")) == normalize_token("Shioaji")
            and (broker_smoke_summary.get("reconciliation") or {}).get("status") == "passed"
            and no_real_capital.get("real_capital_used") is False
            and no_real_capital.get("production_live_order_submitted") is False
            and live_disabled
        )
        broker_smoke_detail = (
            "Shioaji broker sandbox smoke summary consumed with passed reconciliation and fail-closed live boundary."
            if broker_smoke_ok
            else "Shioaji broker sandbox smoke summary is missing pass status, reconciliation, no-real-capital, or live-disabled evidence."
        )
    packet_ready = checklist_ok and datasource_ok and plan_ok and drill_ok and broker_smoke_ok
    acceptance_checks = [
        {
            "name": "repo_authoritative_operator_packet",
            "status": "pass" if checklist_ok and datasource_ok and plan_ok and drill_ok else "fail",
            "detail": "Checklist, datasource smoke, canary plan, and rollback drill evidence are all present and internally consistent.",
        },
        {
            "name": "event_trace_gap_dispositioned",
            "status": "pass" if event_trace_status in {"closed", "packetized"} else "fail",
            "detail": event_trace_note,
        },
        {
            "name": "human_gate_bundle_complete",
            "status": "pass" if packet_ready else "fail",
            "detail": "Human gate input bundle is replay-clean at the artifact level; EP5 proof remains deferred.",
        },
    ]
    if broker_smoke_summary_path is not None:
        acceptance_checks.append(
            {
                "name": "broker_sandbox_smoke_consumed",
                "status": "pass" if broker_smoke_ok else "fail",
                "detail": broker_smoke_detail,
            }
        )

    return {
        "task_id": "APP-003-OPENCLAW-CLOSEOUT-001",
        "generated_at": iso_now(),
        "mode": "human_gate_packet",
        "status": "ready_for_review" if packet_ready else "incomplete",
        "proof_boundary": "EP5-001 prerequisite_only; not EP5-002 proof",
        "openclaw_runtime_boundary": {
            "contract": "OPENCLAW_RUNTIME_CONTRACT.md",
            "summary": "OpenClaw remains the governed control-plane/runtime substrate, not the execution kernel.",
            "execution_kernel_owner": "Pantheon runtime-manager and execution plane",
        },
        "bundle": {
            "operator_checklist": {
                "path": str(checklist_path),
                "status": checklist.get("status"),
                "check_health": checklist.get("check_health"),
            },
            "datasource_smoke": {
                "path": str(datasource_summary_path),
                "status": datasource_summary.get("status"),
                "providers": datasource_summary.get("providers"),
            },
            "canary_plan": {
                "path": str(plan_path),
                "target_stage": plan.get("target_stage"),
                "capital_scale_pct": (plan.get("scale") or {}).get("capital_scale_pct"),
                "gross_scale_pct": (plan.get("scale") or {}).get("gross_scale_pct"),
                "rollback_action_type": (plan.get("rollback") or {}).get("action_type"),
            },
            "rollback_drill": {
                "path": str(drill_summary_path),
                "status": drill_summary.get("status"),
                "replacement_binding_id": drill_summary.get("replacement_binding_id"),
                "kill_switch_safe_mode": drill_summary.get("kill_switch_safe_mode"),
            },
            "broker_sandbox_smoke": {
                "path": str(broker_smoke_summary_path) if broker_smoke_summary_path else None,
                "status": broker_smoke_summary.get("status") if broker_smoke_summary else "not_provided",
                "provider": broker_smoke_summary.get("provider") if broker_smoke_summary else None,
                "order_ids": broker_smoke_summary.get("order_ids") if broker_smoke_summary else None,
                "reconciliation_status": (broker_smoke_summary.get("reconciliation") or {}).get("status")
                if broker_smoke_summary
                else None,
                "proof_boundary": broker_smoke_summary.get("proof_boundary") if broker_smoke_summary else None,
                "live_gate_status": (broker_smoke_summary.get("live_gate") or {}).get("status")
                if broker_smoke_summary
                else None,
                "run_mode": broker_smoke_summary.get("run_mode") if broker_smoke_summary else None,
            },
            "dual_vm_evidence_dir": str(dual_vm_evidence_dir) if dual_vm_evidence_dir else None,
        },
        "event_trace_read_model": {
            "status": event_trace_status,
            "note": event_trace_note,
            "acceptance_rule": "Must be either closed with replayable trace evidence or explicitly packetized before human gate.",
        },
        "acceptance_checks": acceptance_checks,
    }


def command_emit_human_gate_packet(args: argparse.Namespace) -> int:
    output_dir = make_output_dir(args.output_dir, "pantheon-ep5-human-gate-")
    dual_vm_evidence_dir = Path(args.dual_vm_evidence_dir) if args.dual_vm_evidence_dir else None
    packet = build_human_gate_packet(
        checklist_path=Path(args.checklist_json),
        datasource_summary_path=Path(args.datasource_summary_json),
        plan_path=Path(args.plan_json),
        drill_summary_path=Path(args.drill_summary_json),
        broker_smoke_summary_path=Path(args.broker_smoke_summary_json)
        if getattr(args, "broker_smoke_summary_json", None)
        else None,
        dual_vm_evidence_dir=dual_vm_evidence_dir,
        event_trace_status=args.event_trace_status,
        event_trace_note=args.event_trace_note,
    )
    dump_json(output_dir / "human-gate-packet.json", packet)
    dump_json(
        output_dir / "summary.json",
        {
            "task_id": "APP-003-OPENCLAW-CLOSEOUT-001",
            "generated_at": packet["generated_at"],
            "mode": "human_gate_packet",
            "status": packet["status"],
            "event_trace_status": packet["event_trace_read_model"]["status"],
            "proof_boundary": packet["proof_boundary"],
        },
    )
    print(json.dumps({"status": packet["status"], "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0 if packet["status"] == "ready_for_review" else 1


def build_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def command_run_rollback_drill(args: argparse.Namespace) -> int:
    env_map = load_env_map(args.env_file)
    output_dir = make_output_dir(args.output_dir, "pantheon-ep5-drill-")
    runtime_manager_url = require(env_map, "PANTHEON_RUNTIME_MANAGER_URL").rstrip("/")
    runtime_manager_token = require(env_map, "PANTHEON_RUNTIME_MANAGER_TOKEN")
    telemetry_url = optional(env_map, "PANTHEON_TELEMETRY_URL").rstrip("/")
    pool_id = require(env_map, "CANARY_POOL_ID")
    actor_id = optional(env_map, "CANARY_OPERATOR_ID", "operator-oncall")
    binding_id = args.binding_id or optional(env_map, "CANARY_ACTIVE_BINDING_ID")
    if not binding_id:
        raise ReadinessError("run-rollback-drill requires --binding-id or CANARY_ACTIVE_BINDING_ID")

    headers = build_auth_headers(runtime_manager_token)
    kill_switch_payload = {
        "reason": optional(env_map, "CANARY_KILL_SWITCH_REASON", "canary_underperformance"),
        "capital_pool_id": pool_id,
        "actor_id": actor_id,
        "binding_id": binding_id,
    }
    rollback_payload = {
        "current_binding_id": binding_id,
        "action_type": optional(env_map, "CANARY_ROLLBACK_ACTION_TYPE", "pause_then_replace"),
        "replacement_plan_id": optional(env_map, "CANARY_FALLBACK_PLAN_ID", f"{require(env_map, 'CANARY_PLAN_ID')}-rollback"),
        "replacement_plan_status": "approved",
        "replacement_deployment_mode": optional(env_map, "CANARY_FALLBACK_TARGET_STAGE", "paper"),
        "replacement_artifact_id": require(env_map, "CANARY_FALLBACK_ARTIFACT_ID"),
        "replacement_artifact_version": require(env_map, "CANARY_FALLBACK_ARTIFACT_VERSION"),
        "replacement_persona_capital_binding_id": require(env_map, "CANARY_PERSONA_CAPITAL_BINDING_ID"),
        "replacement_persona_capital_binding_status": "active",
        "replacement_allowed_deployment_scope": optional(env_map, "CANARY_FALLBACK_ALLOWED_SCOPE", "paper"),
        "replacement_runtime_id": require(env_map, "CANARY_FALLBACK_RUNTIME_ID"),
        "loader_checks_passed": True,
        "opened_by_artifact_id": require(env_map, "CANARY_REGISTRY_ID"),
    }

    dump_json(output_dir / "kill-switch.request.json", kill_switch_payload)
    dump_json(output_dir / "rollback.request.json", rollback_payload)

    summary: dict[str, Any] = {
        "task_id": "EP5-001",
        "generated_at": iso_now(),
        "mode": "rollback_drill",
        "dry_run": bool(args.dry_run),
        "runtime_manager_url": runtime_manager_url,
        "telemetry_url": telemetry_url or None,
        "binding_id": binding_id,
        "capital_pool_id": pool_id,
        "notes": [
            "This harness prepares or rehearses the rollback drill entry path only.",
            "A successful run is not by itself an EP5 proof packet.",
        ],
    }

    if args.dry_run:
        if telemetry_url:
            rollback_event = {
                "event_id": str(uuid.uuid4()),
                "event_type": "rollback_completed",
                "created_at": iso_now(),
                "execution_mode": "live",
                "environment": optional(env_map, "CANARY_FALLBACK_TARGET_STAGE", "paper"),
                "deployment_stage": optional(env_map, "CANARY_FALLBACK_TARGET_STAGE", "paper"),
                "binding_id": "replacement-binding-placeholder",
                "runtime_id": rollback_payload["replacement_runtime_id"],
                "capital_pool_id": pool_id,
                "artifact_id": rollback_payload["replacement_artifact_id"],
                "artifact_version": rollback_payload["replacement_artifact_version"],
                "plan_id": rollback_payload["replacement_plan_id"],
                "persona_capital_binding_id": rollback_payload["replacement_persona_capital_binding_id"],
                "target": {
                    "registry_id": rollback_payload["replacement_artifact_id"],
                    "strategy_id": require(env_map, "CANARY_STRATEGY_ID"),
                    "artifact_version": rollback_payload["replacement_artifact_version"],
                    "artifact_type": require(env_map, "CANARY_ARTIFACT_TYPE"),
                    "promotion_state": rollback_payload["replacement_deployment_mode"],
                },
                "rollback_parent": binding_id,
                "rollback_action_type": rollback_payload["action_type"],
                "metrics": {"action": "rollback_completed"},
                "metadata": {"source_task_id": "EP5-001", "drill_mode": "dry_run"},
            }
            dump_json(output_dir / "telemetry-rollback.request.json", rollback_event)
        summary["status"] = "prepared"
        summary["message"] = "Dry-run payloads archived. No remote side effects were executed."
        dump_json(output_dir / "summary.json", summary)
        print(json.dumps({"status": "prepared", "output_dir": str(output_dir)}, ensure_ascii=False))
        return 0

    _, binding_payload = write_http_artifact(
        output_dir,
        "binding-detail",
        "GET",
        f"{runtime_manager_url}/api/runtime-bindings/{urllib.parse.quote(binding_id)}",
        headers=headers,
        allowed_statuses={200},
    )
    deployment_mode = str(binding_payload.get("deployment_mode") or "")
    if deployment_mode != "canary":
        raise ReadinessError(
            f"Rollback drill expects an active canary binding; binding {binding_id} reported deployment_mode={deployment_mode!r}"
        )

    _, kill_switch_response = write_http_artifact(
        output_dir,
        "kill-switch",
        "POST",
        f"{runtime_manager_url}/api/kill-switch/dispatch",
        body=kill_switch_payload,
        headers=headers,
        allowed_statuses={200},
    )
    _, safe_mode_response = write_http_artifact(
        output_dir,
        "safe-mode",
        "GET",
        f"{runtime_manager_url}/api/kill-switch/{urllib.parse.quote(pool_id)}/safe-mode",
        headers=headers,
        allowed_statuses={200},
    )
    _, audit_response = write_http_artifact(
        output_dir,
        "kill-switch-audit-log",
        "GET",
        f"{runtime_manager_url}/api/kill-switch/audit-log",
        headers=headers,
        allowed_statuses={200},
    )
    _, rollback_response = write_http_artifact(
        output_dir,
        "rollback",
        "POST",
        f"{runtime_manager_url}/api/rollback",
        body=rollback_payload,
        headers=headers,
        allowed_statuses={201},
    )

    new_binding = rollback_response.get("new_binding") or {}
    new_binding_id = str(new_binding.get("binding_id") or "")
    if not new_binding_id:
        raise ReadinessError("rollback response did not include new_binding.binding_id")

    if telemetry_url:
        rollback_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "rollback_completed",
            "created_at": iso_now(),
            "execution_mode": "live",
            "environment": rollback_payload["replacement_deployment_mode"],
            "deployment_stage": rollback_payload["replacement_deployment_mode"],
            "binding_id": new_binding_id,
            "runtime_id": rollback_payload["replacement_runtime_id"],
            "capital_pool_id": pool_id,
            "artifact_id": rollback_payload["replacement_artifact_id"],
            "artifact_version": rollback_payload["replacement_artifact_version"],
            "plan_id": rollback_payload["replacement_plan_id"],
            "persona_capital_binding_id": rollback_payload["replacement_persona_capital_binding_id"],
            "target": {
                "registry_id": rollback_payload["replacement_artifact_id"],
                "strategy_id": require(env_map, "CANARY_STRATEGY_ID"),
                "artifact_version": rollback_payload["replacement_artifact_version"],
                "artifact_type": require(env_map, "CANARY_ARTIFACT_TYPE"),
                "promotion_state": rollback_payload["replacement_deployment_mode"],
            },
            "rollback_parent": binding_id,
            "rollback_action_type": rollback_payload["action_type"],
            "metrics": {"action": "rollback_completed"},
            "metadata": {"source_task_id": "EP5-001", "drill_mode": "execute"},
        }
        write_http_artifact(
            output_dir,
            "telemetry-rollback",
            "POST",
            f"{telemetry_url}/api/telemetry/ingest",
            body=rollback_event,
            allowed_statuses={200, 202},
        )

    summary.update(
        {
            "status": "executed",
            "kill_switch_safe_mode": safe_mode_response.get("safe_mode_state"),
            "kill_switch_action_type": (kill_switch_response.get("command") or {}).get("action_type"),
            "audit_entry_count": len((audit_response.get("entries") or [])),
            "rollback_action_type": rollback_response.get("action_type"),
            "old_binding_status": (rollback_response.get("old_binding") or {}).get("status"),
            "new_binding_status": (rollback_response.get("new_binding") or {}).get("status"),
            "replacement_binding_id": new_binding_id,
        }
    )
    dump_json(output_dir / "summary.json", summary)
    print(json.dumps({"status": "executed", "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and rehearse the EP5 canary-ready entry path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_check = subparsers.add_parser("run-operator-checklist")
    p_check.add_argument("--env-file", default=None)
    p_check.add_argument("--output-dir", default=None)
    p_check.add_argument("--allow-empty-secrets", action="store_true")
    p_check.add_argument("--check-health", action="store_true")
    p_check.set_defaults(func=command_run_operator_checklist)

    p_plan = subparsers.add_parser("emit-canary-plan")
    p_plan.add_argument("--env-file", default=None)
    p_plan.add_argument("--output-dir", default=None)
    p_plan.set_defaults(func=command_emit_canary_plan)

    p_smoke = subparsers.add_parser("run-datasource-smoke")
    p_smoke.add_argument("--env-file", default=None)
    p_smoke.add_argument("--output-dir", default=None)
    p_smoke.set_defaults(func=command_run_datasource_smoke)

    p_packet = subparsers.add_parser("emit-human-gate-packet")
    p_packet.add_argument("--checklist-json", required=True)
    p_packet.add_argument("--datasource-summary-json", required=True)
    p_packet.add_argument("--plan-json", required=True)
    p_packet.add_argument("--drill-summary-json", required=True)
    p_packet.add_argument("--broker-smoke-summary-json", default=None)
    p_packet.add_argument("--dual-vm-evidence-dir", default=None)
    p_packet.add_argument("--event-trace-status", choices=("closed", "packetized"), required=True)
    p_packet.add_argument("--event-trace-note", required=True)
    p_packet.add_argument("--output-dir", default=None)
    p_packet.set_defaults(func=command_emit_human_gate_packet)

    p_drill = subparsers.add_parser("run-rollback-drill")
    p_drill.add_argument("--env-file", default=None)
    p_drill.add_argument("--binding-id", default=None)
    p_drill.add_argument("--output-dir", default=None)
    p_drill.add_argument("--dry-run", action="store_true")
    p_drill.set_defaults(func=command_run_rollback_drill)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    try:
        return int(args.func(args))
    except ReadinessError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
