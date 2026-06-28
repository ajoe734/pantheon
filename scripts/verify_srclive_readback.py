#!/usr/bin/env python3
"""Verify SRCLIVE readback status against a live BFF.

The verifier is intentionally read-only. It checks the operator-facing BFF
persona-fleet projection and optionally the source-ingest health snapshot when
``--source-ingest-base`` is provided.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BFF_BASE = os.environ.get("BFF_BASE", "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io")
DEFAULT_TOKEN = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
DEFAULT_SOURCE_INGEST_BASE = os.environ.get("SOURCE_INGEST_BASE", "")

EXPECTED_PROVIDER_STATUS = {
    "persona-tw-equity": {
        "shioaji": "read_ok",
        "twse": "read_ok",
        "tpex": "read_ok",
        "mops": "read_ok",
        "finmind": "read_ok",
    },
    "persona-us-equity": {
        "ibkr": "read_ok",
        "sec_edgar": "read_ok",
        "finra": "read_ok",
        "fred": "read_ok",
        "polygon": "credential_unavailable",
        "alphavantage": "credential_unavailable",
    },
    "persona-crypto": {
        "coingecko": "read_ok",
    },
}

OPTIONAL_PROVIDER_STATUS = {
    # Newer BFF source maps may keep Stooq as source-ingest-only health proof
    # while exposing Yahoo as the public price provider key. Source-ingest
    # health below still requires us-stooq-daily-ohlcv to be ok.
    "persona-us-equity": {
        "stooq": "read_ok",
    },
}

EXPECTED_SOURCE_HEALTH_OK = {
    "tw-twse-tpex-official-market",
    "tw-mops-official-disclosures",
    "tw-finmind-datasets",
    "us-stooq-daily-ohlcv",
    "us-sec-edgar-filings",
    "us-finra-short-sale",
    "us-fred-macro",
    "crypto-coingecko-spot",
}


def _get_json(url: str, *, token: str | None = None, timeout: float = 20.0) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token.removeprefix('Bearer ').strip()}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {url} failed HTTP {exc.code}: {body}") from exc


def _persona_id(item: dict[str, Any]) -> str:
    return str(item.get("persona_id") or item.get("personaId") or item.get("id") or "")


def _provider_statuses(item: dict[str, Any]) -> dict[str, str]:
    status = item.get("dataSourceStatus") or item.get("data_source_status") or {}
    providers = status.get("provider_statuses") or status.get("providerStatuses") or {}
    return {str(key): str(value) for key, value in providers.items()}


def _source_health_source(item: dict[str, Any]) -> str:
    status = item.get("dataSourceStatus") or item.get("data_source_status") or {}
    return str(status.get("source_health_source") or status.get("sourceHealthSource") or "")


def verify_bff(base_url: str, token: str) -> dict[str, Any]:
    payload = _get_json(f"{base_url.rstrip('/')}/bff/management/persona-fleet", token=token)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("persona-fleet response missing items[]")
    by_id = {_persona_id(item): item for item in items if isinstance(item, dict)}

    failures: list[str] = []
    summary: dict[str, Any] = {}
    for persona_id, expected in EXPECTED_PROVIDER_STATUS.items():
        item = by_id.get(persona_id)
        if not item:
            failures.append(f"{persona_id}: missing persona")
            continue
        providers = _provider_statuses(item)
        summary[persona_id] = {
            "provider_statuses": providers,
            "source_health_source": _source_health_source(item),
        }
        if persona_id != "persona-crypto" and _source_health_source(item) != "source_ingest":
            failures.append(f"{persona_id}: source_health_source is not source_ingest")
        for provider, expected_status in expected.items():
            actual = providers.get(provider)
            if actual != expected_status:
                failures.append(f"{persona_id}.{provider}: expected {expected_status}, got {actual}")
        for provider, expected_status in OPTIONAL_PROVIDER_STATUS.get(persona_id, {}).items():
            actual = providers.get(provider)
            if actual is not None and actual != expected_status:
                failures.append(f"{persona_id}.{provider}: expected {expected_status}, got {actual}")

    if failures:
        raise RuntimeError("BFF readback failed:\n" + "\n".join(failures) + "\n" + json.dumps(summary, indent=2))
    return summary


def verify_source_ingest(base_url: str) -> dict[str, Any]:
    payload = _get_json(f"{base_url.rstrip('/')}/api/source-ingest/health-usage-snapshot")
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, list):
        raise RuntimeError("source-ingest health snapshot missing sources[]")
    by_id: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        health = source.get("health")
        if isinstance(health, dict):
            by_id[str(health.get("source_id") or "")] = health

    failures: list[str] = []
    summary: dict[str, Any] = {}
    for source_id in sorted(EXPECTED_SOURCE_HEALTH_OK):
        health = by_id.get(source_id)
        if not health:
            failures.append(f"{source_id}: missing health")
            continue
        status = str(health.get("status") or "")
        rows = int(health.get("row_count_last_run") or 0)
        summary[source_id] = {
            "status": status,
            "row_count_last_run": rows,
            "last_success_at": health.get("last_success_at"),
        }
        if status != "ok" or rows <= 0:
            failures.append(f"{source_id}: expected ok rows>0, got status={status} rows={rows}")

    if failures:
        raise RuntimeError("source-ingest health failed:\n" + "\n".join(failures) + "\n" + json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bff-base", default=DEFAULT_BFF_BASE)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--source-ingest-base", default=DEFAULT_SOURCE_INGEST_BASE)
    parser.add_argument("--json", action="store_true", help="Print JSON summary only.")
    args = parser.parse_args(argv)

    result = {"bff": verify_bff(args.bff_base, args.token)}
    if args.source_ingest_base:
        result["source_ingest"] = verify_source_ingest(args.source_ingest_base)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("SRCLIVE readback verification passed")
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
