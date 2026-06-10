#!/usr/bin/env python3
"""Build read-only market-data credential smoke evidence packets.

The runner only probes market-data/reference/readback paths. It never builds
or submits broker order payloads, and it redacts raw credential material from
all archived evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.execution.ibkr_adapter import IBKRAdapter, IBKRConfig
from services.execution.kraken_adapter import KrakenAdapter, KrakenConfig
from services.execution.shioaji_adapter import ShioajiAdapter, ShioajiConfig


TASK_ID = "P2-MARKETDATA-CREDENTIAL-SMOKE-001"
PROVIDERS = (
    "massive_polygon",
    "twse",
    "tpex",
    "mops",
    "finmind",
    "tej",
    "coingecko",
    "kraken",
    "ibkr",
    "shioaji",
)
ORDER_CAPABLE = {"ibkr", "shioaji", "kraken"}
SUCCESS_STATUSES = {"read_ok", "credential_unavailable", "public_reference_unavailable", "read_unavailable"}
RATE_LIMIT_HEADER_ALLOWLIST = {
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
    "retry-after",
    "x-api-limit",
    "x-api-remaining",
    "x-api-used",
    "x-cg-pro-api-credits-left",
    "x-cg-pro-api-credits-used",
    "x-cg-pro-api-limit",
    "x-cg-pro-api-reset",
    "x-kf-api-key-expire",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
}
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


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    display_name: str
    source_class: str
    market: str
    credential_envs: tuple[str, ...] = ()
    secret_ref_envs: tuple[str, ...] = ()
    public_reference: bool = False
    order_capable: bool = False


@dataclass(frozen=True)
class ProbeRequest:
    method: str
    url: str | None
    headers: dict[str, str]
    body_json: dict[str, Any] | None
    secret_values: list[str]


SPECS = {
    "massive_polygon": ProviderSpec(
        key="massive_polygon",
        display_name="Massive / Polygon",
        source_class="research_grade",
        market="US",
        credential_envs=("POLYGON_API_KEY", "MASSIVE_API_KEY", "US_MARKET_DATA_API_KEY"),
        secret_ref_envs=("US_MARKET_DATA_SECRET_REF", "POLYGON_API_KEY_SECRET_NAME", "MASSIVE_API_KEY_SECRET_NAME"),
    ),
    "twse": ProviderSpec(
        key="twse",
        display_name="TWSE OpenAPI",
        source_class="official_reference",
        market="TW",
        public_reference=True,
    ),
    "tpex": ProviderSpec(
        key="tpex",
        display_name="TPEx E-Data",
        source_class="official_reference",
        market="TW",
        public_reference=True,
    ),
    "mops": ProviderSpec(
        key="mops",
        display_name="MOPS",
        source_class="official_reference",
        market="TW",
        public_reference=True,
    ),
    "tej": ProviderSpec(
        key="tej",
        display_name="TEJ API",
        source_class="research_grade",
        market="TW",
        credential_envs=("TEJ_API_KEY",),
        secret_ref_envs=("TEJ_API_KEY_SECRET_NAME", "TEJ_SECRET_REF"),
    ),
    "finmind": ProviderSpec(
        key="finmind",
        display_name="FinMind",
        source_class="research_grade",
        market="TW",
        credential_envs=("FINMIND_API_TOKEN", "FINMIND_API_KEY"),
        secret_ref_envs=("FINMIND_API_TOKEN_SECRET_NAME", "FINMIND_SECRET_REF"),
    ),
    "coingecko": ProviderSpec(
        key="coingecko",
        display_name="CoinGecko",
        source_class="research_grade",
        market="CRYPTO",
        credential_envs=("COINGECKO_API_KEY",),
        secret_ref_envs=("COINGECKO_API_KEY_SECRET_NAME", "COINGECKO_SECRET_REF"),
        public_reference=True,
    ),
    "kraken": ProviderSpec(
        key="kraken",
        display_name="Kraken market data",
        source_class="broker_execution",
        market="CRYPTO",
        credential_envs=("KRAKEN_API_KEY",),
        secret_ref_envs=("KRAKEN_API_KEY_SECRET_NAME", "KRAKEN_SECRET_REF"),
        public_reference=True,
        order_capable=True,
    ),
    "ibkr": ProviderSpec(
        key="ibkr",
        display_name="IBKR market data",
        source_class="broker_execution",
        market="US",
        credential_envs=("BROKER_API_KEY", "IBKR_API_KEY"),
        secret_ref_envs=("BROKER_API_KEY_SECRET_NAME", "IBKR_SECRET_REF"),
        order_capable=True,
    ),
    "shioaji": ProviderSpec(
        key="shioaji",
        display_name="Shioaji quote",
        source_class="broker_execution",
        market="TW",
        credential_envs=("SHIOAJI_API_KEY",),
        secret_ref_envs=("SHIOAJI_API_KEY_SECRET_NAME", "SHIOAJI_SECRET_REF"),
        order_capable=True,
    ),
}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def load_env_map(env_file: str | None) -> dict[str, str]:
    env_map = dict(os.environ)
    if not env_file:
        return env_map
    path = Path(env_file)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_map[key.strip()] = value.strip().strip("'\"")
    return env_map


def optional(env_map: dict[str, str], key: str, default: str = "") -> str:
    value = env_map.get(key)
    return default if value in (None, "") else str(value)


def first_env(env_map: dict[str, str], keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    for key in keys:
        value = optional(env_map, key)
        if value:
            return key, value
    return None, None


def secret_fingerprint(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact_url(url: str, secret_values: list[str]) -> str:
    redacted = url
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def credential_evidence(env_map: dict[str, str], spec: ProviderSpec) -> dict[str, Any]:
    credential_key, credential_value = first_env(env_map, spec.credential_envs)
    ref_key, ref_value = first_env(env_map, spec.secret_ref_envs)
    ref_is_structured = bool(ref_value and ref_value.lower().startswith(SECRET_REF_PREFIXES))
    return {
        "credential_present": bool(credential_value),
        "credential_env": credential_key,
        "credential_fingerprint": secret_fingerprint(credential_value),
        "secret_ref_present": bool(ref_value),
        "secret_ref_env": ref_key,
        "secret_ref": ref_value if ref_is_structured else None,
        "secret_ref_name": ref_value if ref_value and not ref_is_structured else None,
        "raw_secret_material_present_in_artifact": False,
        "credential_not_required": spec.public_reference and not spec.credential_envs,
    }


def rate_limit_evidence(headers: Any | None = None, *, unavailable_reason: str | None = None) -> dict[str, Any]:
    observed_headers: dict[str, str] = {}
    if headers:
        for key, value in headers.items():
            lowered = str(key).lower()
            if lowered in RATE_LIMIT_HEADER_ALLOWLIST:
                observed_headers[lowered] = str(value)
    status = "observed" if observed_headers else "not_observed"
    return {
        "status": "unavailable" if unavailable_reason else status,
        "headers": observed_headers,
        "header_allowlist": sorted(RATE_LIMIT_HEADER_ALLOWLIST),
        "quota": {
            "status": "observed" if observed_headers else ("unavailable" if unavailable_reason else "not_observed"),
            "non_secret": True,
        },
        "reason": unavailable_reason if unavailable_reason else (None if observed_headers else "provider response did not include allowlisted rate-limit/quota headers"),
    }


def session_provenance_evidence(
    *,
    status: str,
    session_type: str,
    generated_at: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "session_type": session_type,
        "generated_at": generated_at,
        "reason": reason,
        "details": details or {},
        "raw_secret_material_present_in_artifact": False,
    }


def http_json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body_json: dict[str, Any] | None = None,
    timeout_seconds: int = 15,
    max_bytes: int = 512_000,
) -> dict[str, Any]:
    body = json.dumps(body_json).encode("utf-8") if body_json is not None else None
    request_headers = dict(headers or {})
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=body, headers=request_headers, method=method.upper())
    started_at = iso_now()
    with urlopen(request, timeout=timeout_seconds) as response:
        body = response.read(max_bytes + 1)
        truncated = len(body) > max_bytes
        if truncated:
            body = body[:max_bytes]
        text = body.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        return {
            "status": "read_ok",
            "started_at": started_at,
            "completed_at": iso_now(),
            "http_status": getattr(response, "status", None),
            "content_type": response.headers.get("content-type"),
            "bytes_read": len(body),
            "truncated": truncated,
            "json_type": type(parsed).__name__ if parsed is not None else None,
            "record_count_hint": record_count_hint(parsed),
            "rate_limit": rate_limit_evidence(response.headers),
            "session_provenance": session_provenance_evidence(
                status="observed",
                session_type=f"stateless_http_{method.lower()}",
                generated_at=started_at,
                details={
                    "completed_at": iso_now(),
                    "http_status": getattr(response, "status", None),
                    "response_content_type": response.headers.get("content-type"),
                },
            ),
        }


def http_json_read(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 15,
    max_bytes: int = 512_000,
) -> dict[str, Any]:
    return http_json_request(url, headers=headers, timeout_seconds=timeout_seconds, max_bytes=max_bytes)


def record_count_hint(parsed: Any) -> int | None:
    if isinstance(parsed, list):
        return len(parsed)
    if isinstance(parsed, dict):
        for key in ("results", "data", "items"):
            value = parsed.get(key)
            if isinstance(value, list):
                return len(value)
    return None


def unavailable_status(reason: str, *, public_reference: bool = False) -> dict[str, Any]:
    status = "public_reference_unavailable" if public_reference else "credential_unavailable"
    return {
        "status": status,
        "reason": reason,
        "readback": None,
        "rate_limit": rate_limit_evidence(unavailable_reason=reason),
        "session_provenance": session_provenance_evidence(
            status="unavailable",
            session_type="not_started",
            generated_at=iso_now(),
            reason=reason,
        ),
    }


def build_probe_request(provider: str, env_map: dict[str, str], credential: str | None) -> ProbeRequest:
    if provider == "massive_polygon":
        symbol = optional(env_map, "POLYGON_SMOKE_SYMBOL", "AAPL")
        date = optional(env_map, "POLYGON_SMOKE_DATE", "2024-01-02")
        base_url = optional(env_map, "POLYGON_BASE_URL", optional(env_map, "MASSIVE_BASE_URL", "https://api.polygon.io"))
        query = urlencode({"adjusted": "true", "sort": "asc", "limit": "1", "apiKey": credential or ""})
        return ProbeRequest("GET", f"{base_url.rstrip('/')}/v2/aggs/ticker/{symbol}/range/1/day/{date}/{date}?{query}", {}, None, [credential or ""])
    if provider == "twse":
        return ProbeRequest("GET", optional(env_map, "TWSE_SMOKE_URL", "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL"), {}, None, [])
    if provider == "tpex":
        return ProbeRequest("GET", optional(env_map, "TPEX_SMOKE_URL", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"), {}, None, [])
    if provider == "mops":
        base_url = optional(env_map, "MOPS_BASE_URL", "https://mops.twse.com.tw/mops/api")
        endpoint = optional(env_map, "MOPS_SMOKE_ENDPOINT", "home_page/t05sr01_1").strip("/")
        url = optional(env_map, "MOPS_SMOKE_URL", f"{base_url.rstrip('/')}/{endpoint}")
        return ProbeRequest(
            "POST",
            url,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://mops.twse.com.tw",
                "Referer": "https://mops.twse.com.tw/mops/",
                "User-Agent": "pantheon-marketdata-smoke/0.1",
            },
            {
                "count": int(optional(env_map, "MOPS_SMOKE_COUNT", "8")),
                "marketKind": optional(env_map, "MOPS_SMOKE_MARKET_KIND", ""),
            },
            [],
        )
    if provider == "tej":
        base_url = optional(env_map, "TEJ_BASE_URL", "https://api.tej.com.tw")
        dataset = optional(env_map, "TEJ_DATASET_CODE", "TRAIL/TAPRCD")
        query = urlencode(
            {
                "api_key": credential or "",
                "coid": optional(env_map, "TEJ_SMOKE_SYMBOL", "2330"),
                "opts.columns": optional(env_map, "TEJ_SMOKE_COLUMNS", "coid,mdate,close_d"),
                "opts.per_page": optional(env_map, "TEJ_SMOKE_PER_PAGE", "1"),
                "opts.sort": optional(env_map, "TEJ_SMOKE_SORT", "mdate.desc"),
            }
        )
        return ProbeRequest("GET", f"{base_url.rstrip('/')}/api/datatables/{dataset}.json?{query}", {}, None, [credential or ""])
    if provider == "finmind":
        base_url = optional(env_map, "FINMIND_BASE_URL", "https://api.finmindtrade.com/api/v4")
        dataset = optional(env_map, "FINMIND_SMOKE_DATASET", "TaiwanStockPrice")
        query = urlencode(
            {
                "dataset": dataset,
                "data_id": optional(env_map, "FINMIND_SMOKE_SYMBOL", "2330"),
                "start_date": optional(env_map, "FINMIND_SMOKE_START_DATE", "2026-06-01"),
                "end_date": optional(env_map, "FINMIND_SMOKE_END_DATE", "2026-06-08"),
                "token": credential or "",
            }
        )
        return ProbeRequest("GET", f"{base_url.rstrip('/')}/data?{query}", {}, None, [credential or ""])
    if provider == "coingecko":
        base_url = optional(env_map, "COINGECKO_BASE_URL", "https://api.coingecko.com/api/v3")
        headers = {"x-cg-demo-api-key": credential} if credential else {}
        return ProbeRequest("GET", f"{base_url.rstrip('/')}/simple/price?ids=bitcoin&vs_currencies=usd", headers, None, [credential or ""])
    if provider == "kraken":
        base_url = optional(env_map, "KRAKEN_BASE_URL", "https://api.kraken.com")
        pair = optional(env_map, "KRAKEN_PUBLIC_PAIR", "XBTUSD")
        return ProbeRequest("GET", f"{base_url.rstrip('/')}/0/public/Ticker?{urlencode({'pair': pair})}", {}, None, [])
    return ProbeRequest("GET", None, {}, None, [])


def build_read_intent(provider: str, env_map: dict[str, str]) -> dict[str, Any] | None:
    if provider == "ibkr":
        adapter = IBKRAdapter(
            IBKRConfig(
                host=optional(env_map, "IBKR_HOST", "127.0.0.1"),
                port=int(optional(env_map, "IBKR_PORT", "7497")),
                client_id=int(optional(env_map, "IBKR_CLIENT_ID", "7")),
                readonly_market_data=True,
                market_data_type=int(optional(env_map, "IBKR_MARKET_DATA_TYPE", "3")),
            )
        )
        return adapter.build_market_data_request(optional(env_map, "IBKR_SMOKE_SYMBOL", "AAPL.US"), snapshot=True)
    if provider == "shioaji":
        adapter = ShioajiAdapter(
            ShioajiConfig(
                api_key="SECRET_REF_ONLY",
                secret_key="SECRET_REF_ONLY",
                simulation=True,
                quote_bind=True,
            )
        )
        return adapter.build_quote_subscription(optional(env_map, "SHIOAJI_SMOKE_SYMBOL", "2330.TWSE"))
    if provider == "kraken":
        adapter = KrakenAdapter(KrakenConfig(api_key="SECRET_REF_ONLY", api_secret="SECRET_REF_ONLY", validate_only=True))
        return adapter.build_market_data_request(optional(env_map, "KRAKEN_SMOKE_SYMBOL", "BTC/USD.KRAKEN"), interval=1)
    return None


def run_provider(provider: str, env_map: dict[str, str], *, allow_network: bool) -> dict[str, Any]:
    spec = SPECS[provider]
    generated_at = iso_now()
    credential = credential_evidence(env_map, spec)
    credential_value = optional(env_map, credential.get("credential_env") or "")
    read_intent = build_read_intent(provider, env_map)
    packet: dict[str, Any] = {
        "task_id": TASK_ID,
        "provider_key": spec.key,
        "provider": spec.display_name,
        "market": spec.market,
        "source_class": spec.source_class,
        "generated_at": generated_at,
        "credential": credential,
        "read_only": True,
        "order_side_effects_allowed": False,
        "capital_side_effects_allowed": False,
        "order_capable_provider": spec.order_capable,
        "order_path": "disabled_for_marketdata_smoke" if spec.order_capable else "not_applicable",
        "read_intent": read_intent,
        "rate_limit": rate_limit_evidence(unavailable_reason="provider read has not started"),
        "session_provenance": session_provenance_evidence(
            status="unavailable",
            session_type="not_started",
            generated_at=generated_at,
            reason="provider read has not started",
        ),
    }

    if provider in {"ibkr", "shioaji"}:
        evidence_path_key = f"{provider.upper()}_QUOTE_READBACK_JSON"
        evidence_path = optional(env_map, evidence_path_key)
        if evidence_path:
            path = Path(evidence_path)
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            loaded_at = iso_now()
            captured_at = optional(env_map, f"{provider.upper()}_QUOTE_READBACK_CAPTURED_AT")
            if not captured_at and isinstance(payload, dict):
                captured_at_value = payload.get("captured_at") or payload.get("generated_at")
                captured_at = str(captured_at_value) if captured_at_value else ""
            packet["status"] = "read_ok"
            packet["readback"] = {
                "source": evidence_path_key,
                "json_type": type(payload).__name__,
                "record_count_hint": record_count_hint(payload),
            }
            packet["rate_limit"] = rate_limit_evidence(
                unavailable_reason="quote readback file does not expose provider rate-limit/quota headers"
            )
            packet["session_provenance"] = session_provenance_evidence(
                status="observed",
                session_type="quote_readback_file",
                generated_at=loaded_at,
                details={
                    "source_env": evidence_path_key,
                    "file_name": path.name,
                    "file_sha256": file_sha256(path),
                    "bytes_read": len(raw.encode("utf-8")),
                    "loaded_at": loaded_at,
                    "captured_at": captured_at or None,
                    "credential_session_evidence_status": (
                        "credential_present_or_secret_ref_present"
                        if credential["credential_present"] or credential["secret_ref_present"]
                        else "not_available_in_repo_local_readback"
                    ),
                },
            )
        else:
            packet.update(unavailable_status(f"{evidence_path_key} was not provided for read-only quote readback"))
        return packet

    probe = build_probe_request(provider, env_map, credential_value)
    packet["request"] = {
        "method": probe.method,
        "url": redact_url(probe.url or "", probe.secret_values) if probe.url else None,
        "headers_present": sorted(probe.headers.keys()),
        "body_present": probe.body_json is not None,
        "body_keys": sorted(probe.body_json.keys()) if probe.body_json else [],
    }
    if not probe.url:
        packet.update(unavailable_status("provider smoke URL is not configured", public_reference=spec.public_reference))
        return packet
    if spec.credential_envs and not credential["credential_present"] and not spec.public_reference:
        packet.update(unavailable_status("required credential env var is absent"))
        return packet
    if not allow_network:
        packet.update(
            {
                "status": "read_unavailable",
                "reason": "network probing disabled; rerun with --allow-network on VM-2 or another credentialed runtime",
                "readback": None,
                "rate_limit": rate_limit_evidence(
                    unavailable_reason="network probing disabled; provider headers were not observed"
                ),
                "session_provenance": session_provenance_evidence(
                    status="unavailable",
                    session_type="stateless_http_get",
                    generated_at=iso_now(),
                    reason="network probing disabled; no provider session was opened",
                    details={"request_url_redacted": packet["request"]["url"]},
                ),
            }
        )
        return packet

    try:
        readback = http_json_request(probe.url, method=probe.method, headers=probe.headers, body_json=probe.body_json)
        packet.update(readback)
        packet["request"]["url"] = redact_url(probe.url, probe.secret_values)
    except HTTPError as exc:
        packet.update(
            {
                "status": "read_unavailable",
                "reason": f"HTTPError {exc.code}",
                "readback": {"http_status": exc.code},
                "rate_limit": rate_limit_evidence(exc.headers),
                "session_provenance": session_provenance_evidence(
                    status="observed",
                    session_type="stateless_http_get",
                    generated_at=iso_now(),
                    reason=f"provider returned HTTPError {exc.code}",
                    details={"http_status": exc.code},
                ),
            }
        )
    except (OSError, URLError) as exc:
        packet.update(
            {
                "status": "read_unavailable",
                "reason": exc.__class__.__name__,
                "readback": None,
                "rate_limit": rate_limit_evidence(unavailable_reason=f"{exc.__class__.__name__}; provider headers were not observed"),
                "session_provenance": session_provenance_evidence(
                    status="unavailable",
                    session_type="stateless_http_get",
                    generated_at=iso_now(),
                    reason=exc.__class__.__name__,
                ),
            }
        )
    return packet


def build_summary(packets: list[dict[str, Any]]) -> dict[str, Any]:
    status_by_provider = {packet["provider_key"]: packet["status"] for packet in packets}
    accepted = all(status in SUCCESS_STATUSES for status in status_by_provider.values())
    return {
        "task_id": TASK_ID,
        "generated_at": iso_now(),
        "status": "pass" if accepted else "fail",
        "providers": status_by_provider,
        "read_only": True,
        "order_side_effects_allowed": False,
        "capital_side_effects_allowed": False,
        "raw_secret_material_present_in_artifacts": False,
        "acceptance_note": (
            "Each governed provider has read_ok evidence or explicit unavailable-credential/read-unavailable evidence; "
            "rate-limit/quota and session/provenance fields are present without raw secrets; "
            "order-capable provider order paths are disabled."
        ),
    }


def run_smoke(env_map: dict[str, str], output_dir: Path, *, allow_network: bool, providers: list[str]) -> dict[str, Any]:
    packets = [run_provider(provider, env_map, allow_network=allow_network) for provider in providers]
    for packet in packets:
        dump_json(output_dir / f"{packet['provider_key']}.json", packet)
    summary = build_summary(packets)
    dump_json(output_dir / "summary.json", summary)
    return summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run read-only market-data credential smoke evidence capture.")
    parser.add_argument("--env-file", help="Optional env file with VM-2 local credential variables or secret refs.")
    parser.add_argument("--output-dir", required=True, help="Directory where evidence JSON files will be written.")
    parser.add_argument("--allow-network", action="store_true", help="Actually call configured read-only HTTP endpoints.")
    parser.add_argument(
        "--provider",
        action="append",
        choices=PROVIDERS,
        help="Provider to run. May be repeated. Defaults to all governed providers.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    providers = args.provider or list(PROVIDERS)
    summary = run_smoke(
        load_env_map(args.env_file),
        Path(args.output_dir),
        allow_network=args.allow_network,
        providers=providers,
    )
    print(json.dumps({"status": summary["status"], "output_dir": args.output_dir}, ensure_ascii=False))
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
