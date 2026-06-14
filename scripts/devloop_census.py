#!/usr/bin/env python3
"""Repeatable census for Pantheon dev-loop right-half read surfaces.

The probe is read-only. It uses the dev BFF's stub bearer auth by default and
counts the surfaces that indicate execute/feedback/learn progress:

  * /api/v1/telemetry
  * /bff/v5/loop-runs
  * /bff/approvals
  * /api/v1/evolution-decisions
  * /bff/incidents
  * /api/v1/rollbacks

Empty ledgers are reported as a census result, not as a hard failure. Transport
errors, non-200 responses, and malformed JSON are hard failures because the
meter cannot be trusted when a surface cannot be read.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_URL = "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io"
DEFAULT_STUB_TOKEN = "op-dev:admin:mfa"


@dataclass(frozen=True)
class CensusSurface:
    name: str
    path: str
    label: str


SURFACES: tuple[CensusSurface, ...] = (
    CensusSurface("telemetry", "/api/v1/telemetry", "telemetry records"),
    CensusSurface("loop_runs", "/bff/v5/loop-runs", "loop runs"),
    CensusSurface("approvals", "/bff/approvals", "pending approvals"),
    CensusSurface("evolution", "/api/v1/evolution-decisions", "evolution decisions"),
    CensusSurface("incidents", "/bff/incidents", "incidents"),
    CensusSurface("rollbacks", "/api/v1/rollbacks", "rollbacks"),
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _first_list(payload: dict[str, Any]) -> list[Any]:
    for key in ("data", "items", "records", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def count_payload_items(payload: dict[str, Any]) -> int:
    page_info = payload.get("page_info")
    if isinstance(page_info, dict):
        total = page_info.get("total")
        if isinstance(total, int) and total >= 0:
            return total
    meta = payload.get("meta")
    if isinstance(meta, dict):
        total = meta.get("total")
        if isinstance(total, int) and total >= 0:
            return total
    count = payload.get("count")
    if isinstance(count, int) and count >= 0:
        return count
    return len(_first_list(payload))


def _surface_meta(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta")
    surfaces = meta.get("surfaces") if isinstance(meta, dict) else None
    if not isinstance(surfaces, dict):
        return {}
    for info in surfaces.values():
        if isinstance(info, dict):
            return {
                "status": info.get("status"),
                "source": info.get("source"),
                "note": info.get("note"),
            }
    return {}


def telemetry_trade_count(payload: dict[str, Any]) -> int:
    total = 0.0
    for item in _first_list(payload):
        if not isinstance(item, dict):
            continue
        candidates = [
            item.get("total_trades"),
            item.get("trade_count"),
            item.get("trades"),
        ]
        metrics = item.get("metrics")
        if isinstance(metrics, dict):
            candidates.extend(
                [
                    metrics.get("total_trades"),
                    metrics.get("trade_count"),
                    metrics.get("trades"),
                ]
            )
        for candidate in candidates:
            value = _as_number(candidate)
            if value is not None:
                total += max(value, 0.0)
                break
    return int(total)


def telemetry_non_empty_metric_count(payload: dict[str, Any]) -> int:
    keys = {
        "pnl",
        "drawdown",
        "sharpe_ratio",
        "total_trades",
        "fill_rate",
        "avg_slippage_bps",
        "trade_count",
        "trades",
    }
    count = 0
    for item in _first_list(payload):
        if not isinstance(item, dict):
            continue
        metrics: dict[str, Any] = {}
        raw_metrics = item.get("metrics")
        if isinstance(raw_metrics, dict):
            metrics.update(raw_metrics)
        metrics.update({key: item.get(key) for key in keys if key in item})
        for key in keys:
            value = metrics.get(key)
            if value is None:
                continue
            numeric = _as_number(value)
            if numeric is None or numeric != 0:
                count += 1
                break
    return count


def request_json(
    *,
    base_url: str,
    token: str,
    path: str,
    timeout: float,
    ctx: ssl.SSLContext,
) -> dict[str, Any]:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status_code": int(exc.code),
            "duration_ms": round((time.time() - started) * 1000, 2),
            "error": f"HTTP {exc.code}",
        }
    except Exception as exc:  # noqa: BLE001 - probe should report transport errors.
        return {
            "ok": False,
            "status_code": None,
            "duration_ms": round((time.time() - started) * 1000, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - malformed JSON is a probe failure.
        return {
            "ok": False,
            "status_code": status,
            "duration_ms": round((time.time() - started) * 1000, 2),
            "error": f"invalid JSON: {exc}",
        }
    if status != 200 or not isinstance(payload, dict):
        return {
            "ok": False,
            "status_code": status,
            "duration_ms": round((time.time() - started) * 1000, 2),
            "payload": payload,
            "error": "expected HTTP 200 JSON object",
        }
    return {
        "ok": True,
        "status_code": status,
        "duration_ms": round((time.time() - started) * 1000, 2),
        "payload": payload,
    }


def _surface_material_signal(surface: CensusSurface, payload: dict[str, Any], count: int) -> tuple[bool, str]:
    if surface.name != "telemetry":
        return count > 0, "count>0" if count > 0 else "empty"

    meta = _surface_meta(payload)
    source = str(meta.get("source") or "")
    trades = telemetry_trade_count(payload)
    non_empty_metrics = telemetry_non_empty_metric_count(payload)
    if trades > 0:
        return True, "trades>0"
    if source and source not in {"missing", "telemetry_summary_fallback"} and count > 0:
        return True, f"source={source}"
    if non_empty_metrics > 0:
        return True, "non-empty telemetry metrics"
    if source == "telemetry_summary_fallback" and count > 0:
        return False, "synthesized summary fallback"
    return False, "empty"


def build_census(
    *,
    base_url: str,
    token: str,
    timeout: float,
    insecure: bool,
) -> dict[str, Any]:
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    surface_results: dict[str, dict[str, Any]] = {}
    hard_failures: list[dict[str, Any]] = []
    material_signals = 0
    trades = 0

    for surface in SURFACES:
        response = request_json(
            base_url=base_url,
            token=token,
            path=surface.path,
            timeout=timeout,
            ctx=ctx,
        )
        result: dict[str, Any] = {
            "path": surface.path,
            "label": surface.label,
            "ok": bool(response.get("ok")),
            "status_code": response.get("status_code"),
            "duration_ms": response.get("duration_ms"),
        }
        payload = response.get("payload")
        if response.get("ok") and isinstance(payload, dict):
            count = count_payload_items(payload)
            meta = _surface_meta(payload)
            material, material_reason = _surface_material_signal(surface, payload, count)
            result.update(
                {
                    "count": count,
                    "material": material,
                    "material_reason": material_reason,
                    "surface_status": meta.get("status"),
                    "source": meta.get("source"),
                    "note": meta.get("note"),
                }
            )
            if surface.name == "telemetry":
                trades = telemetry_trade_count(payload)
                result["trade_count"] = trades
                result["non_empty_metric_count"] = telemetry_non_empty_metric_count(payload)
            if material:
                material_signals += 1
        else:
            result.update(
                {
                    "count": None,
                    "material": False,
                    "material_reason": "unreadable",
                    "error": response.get("error"),
                }
            )
            hard_failures.append(
                {
                    "surface": surface.name,
                    "path": surface.path,
                    "status_code": response.get("status_code"),
                    "error": response.get("error"),
                }
            )
        surface_results[surface.name] = result

    return {
        "generated_at": utc_now(),
        "base_url": base_url.rstrip("/"),
        "right_half_started": material_signals > 0,
        "material_signal_count": material_signals,
        "hard_failure_count": len(hard_failures),
        "hard_failures": hard_failures,
        "totals": {
            "telemetry_records": surface_results["telemetry"].get("count"),
            "trades": trades,
            "loop_runs": surface_results["loop_runs"].get("count"),
            "approvals": surface_results["approvals"].get("count"),
            "evolution": surface_results["evolution"].get("count"),
            "incidents": surface_results["incidents"].get("count"),
            "rollbacks": surface_results["rollbacks"].get("count"),
        },
        "surfaces": surface_results,
    }


def format_text(result: dict[str, Any]) -> str:
    lines = [
        "Pantheon dev-loop right-half census",
        f"generated_at: {result['generated_at']}",
        f"base_url: {result['base_url']}",
        f"right_half_started: {'yes' if result['right_half_started'] else 'no'}",
        f"material_signal_count: {result['material_signal_count']}",
        f"hard_failure_count: {result['hard_failure_count']}",
        "",
        "surface            status  count  material  source                         note",
        "-----------------  ------  -----  --------  -----------------------------  ----------------------------",
    ]
    for name in [surface.name for surface in SURFACES]:
        item = result["surfaces"][name]
        count = item.get("count")
        count_text = "-" if count is None else str(count)
        material = "yes" if item.get("material") else "no"
        source = str(item.get("source") or "-")
        note = str(item.get("material_reason") or item.get("error") or "-")
        if name == "telemetry":
            note = f"{note}; trades={item.get('trade_count', 0)}"
        lines.append(
            f"{name:<17}  {str(item.get('status_code') or '-'):>6}  "
            f"{count_text:>5}  {material:<8}  {source:<29}  {note[:28]}"
        )
    if result["hard_failures"]:
        lines.append("")
        lines.append("hard_failures:")
        for failure in result["hard_failures"]:
            lines.append(
                f"  {failure['surface']}: {failure.get('status_code') or '-'} {failure.get('error')}"
            )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("BFF_BASE") or os.getenv("PANTHEON_BFF_BASE_URL") or DEFAULT_BASE_URL,
        help="Pantheon BFF base URL. Defaults to BFF_BASE/PANTHEON_BFF_BASE_URL or the dev BFF.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("BFF_TOKEN") or os.getenv("PANTHEON_BFF_TOKEN") or DEFAULT_STUB_TOKEN,
        help="Bearer token value without the 'Bearer ' prefix. Defaults to the dev stub token.",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="Per-request timeout in seconds.")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the JSON census result.",
    )
    parser.add_argument(
        "--insecure",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("BFF_INSECURE", "1") == "1",
        help="Disable TLS hostname/certificate verification for dev sslip.io probes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    token = str(args.token or "").removeprefix("Bearer ").strip()
    if not token:
        print("ERROR: token cannot be empty", file=sys.stderr)
        return 2

    result = build_census(
        base_url=args.base_url,
        token=token,
        timeout=args.timeout,
        insecure=args.insecure,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) if args.format == "json" else format_text(result)
    print(rendered)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return 1 if result["hard_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
