#!/usr/bin/env python3
"""Authenticated Pantheon BFF live smoke probe.

This probe is intentionally small and dependency-light so it can run from the
dev VM during BFF/Lovable cutover work. It never writes the bearer token or JWT
secret to its evidence file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_BASE_URL = "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io"


@dataclass(frozen=True)
class Probe:
    method: str
    path: str
    family: str
    body: dict[str, Any] | None = None
    expect_status: set[int] = field(default_factory=lambda: {200})
    required_paths: tuple[tuple[str, ...], ...] = ()


READ_PROBES: tuple[Probe, ...] = (
    Probe("GET", "/bff/me", "session", required_paths=(("data", "user"), ("data", "tenant"), ("data", "capabilities"))),
    Probe("GET", "/bff/strategies", "strategy"),
    Probe("GET", "/bff/personas", "persona"),
    Probe("GET", "/bff/capital-pools", "capital"),
    Probe("GET", "/bff/rebalances", "rebalance"),
    Probe("GET", "/bff/deployments", "deployment"),
    Probe("GET", "/bff/evolution-programs", "evolution"),
    Probe("GET", "/bff/jobs", "jobs"),
    Probe("GET", "/bff/approvals", "approval"),
    Probe("GET", "/bff/v5/interventions", "v5-intervention"),
    Probe("GET", "/bff/alerts", "alerts"),
    Probe("GET", "/bff/incidents", "incidents"),
    Probe("GET", "/bff/audit", "audit"),
    Probe("GET", "/bff/artifacts", "artifacts"),
    Probe("GET", "/bff/runtimes", "runtimes"),
    Probe("GET", "/bff/mcp-servers", "mcp-servers"),
    Probe("GET", "/bff/mcp-tools", "mcp-tools"),
    Probe("GET", "/bff/skills", "skills"),
    Probe("GET", "/bff/channels", "channels"),
    Probe("GET", "/bff/tools", "tools"),
    Probe("GET", "/bff/ranking-formulas", "ranking-formulas"),
    Probe("GET", "/bff/research-experiments", "research"),
    Probe("GET", "/bff/agora/signals", "agora-signals"),
    Probe("GET", "/bff/agora/inbox", "agora-inbox"),
    Probe("GET", "/bff/agora/journal", "agora-journal"),
    Probe("GET", "/bff/agora/postmortems", "agora-postmortems"),
    Probe("GET", "/bff/agora/ask/sessions", "agora-ask"),
    Probe(
        "GET",
        "/bff/assistant/control-mode",
        "assistant-control-mode",
        required_paths=(
            ("data", "state"),
            ("data", "active"),
            ("data", "requiresRole"),
            ("data", "requiresMfa"),
            ("data", "changePassphraseHref"),
        ),
    ),
    Probe(
        "POST",
        "/bff/management/nl/ask",
        "management-ai-multiturn",
        body={
            "question": "Give a concise management cockpit status for this smoke probe.",
            "focus": "all",
            "sessionId": None,
            "conversation": {
                "recentTurns": [
                    {"role": "user", "content": "Start a management AI smoke conversation."}
                ],
                "summary": "Authenticated live smoke probe for the Management AI contract.",
            },
            "ui": {
                "currentRoute": "/management/cockpit",
                "selectedEntity": None,
                "visiblePanels": ["ManagementAIPanel", "CockpitSummary"],
                "filters": {"probe": "authenticated-live"},
                "availableUiActions": [
                    {"kind": "navigate", "description": "Navigate to a route", "paramsSchema": "{ to: string }"},
                    {
                        "kind": "refreshCurrentView",
                        "description": "Refresh the visible management view",
                        "paramsSchema": "{}",
                    },
                ],
            },
        },
        expect_status={202},
        required_paths=(
            ("data", "answer"),
            ("data", "sessionId"),
            ("data", "traceId"),
            ("data", "providerStatus"),
            ("data", "actions"),
            ("data", "conversation", "href"),
            ("data", "session", "ttlSeconds"),
        ),
    ),
    Probe("GET", "/bff/v5/loop-runs", "v5-loop-runs"),
    Probe("GET", "/bff/v5/sentinel/findings", "v5-sentinel"),
    Probe("GET", "/bff/v5/execution/persona-health", "v5-persona-health"),
)


WRITE_PROBES: tuple[Probe, ...] = (
    Probe(
        "POST",
        "/bff/confirm-tokens",
        "confirm-token-create",
        body={"tokenId": "ct-live-smoke-20260510", "reason": "BFF authenticated live smoke"},
        expect_status={201},
        required_paths=(("data", "tokenId"), ("data", "status"), ("meta", "idempotency")),
    ),
    Probe(
        "GET",
        "/bff/confirm-tokens/ct-live-smoke-20260510",
        "confirm-token-read-created",
        expect_status={200},
        required_paths=(("data", "status"),),
    ),
    Probe(
        "POST",
        "/bff/confirm-tokens/ct-live-smoke-20260510/redeem",
        "confirm-token-redeem",
        body={"reason": "operator confirmed BFF smoke"},
        expect_status={202},
        required_paths=(("data",), ("meta", "idempotency")),
    ),
    Probe(
        "DELETE",
        "/bff/confirm-tokens/ct-live-smoke-20260510",
        "confirm-token-delete",
        body={"reason": "BFF smoke cleanup"},
        expect_status={202},
        required_paths=(("data",), ("meta", "idempotency")),
    ),
    Probe(
        "GET",
        "/bff/confirm-tokens/ct-live-smoke-20260510",
        "confirm-token-read-deleted",
        expect_status={200},
        required_paths=(("data", "status"),),
    ),
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def make_token(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    token = os.getenv("PANTHEON_BFF_SMOKE_BEARER_TOKEN", "").strip()
    if token:
        return token.removeprefix("Bearer ").strip(), {
            "kind": "provided_bearer",
            "sha256_12": hashlib.sha256(token.encode("utf-8")).hexdigest()[:12],
        }

    secret = os.getenv("PANTHEON_BFF_SMOKE_JWT_SECRET", "").strip()
    if not secret:
        raise SystemExit(
            "Set PANTHEON_BFF_SMOKE_BEARER_TOKEN or PANTHEON_BFF_SMOKE_JWT_SECRET"
        )

    from services.runtime_auth_inbound import encode_jwt_hs256

    now = int(time.time())
    payload = {
        "sub": args.subject,
        "iss": args.issuer,
        "aud": args.audience,
        "iat": now,
        "exp": now + args.ttl_seconds,
        "roles": args.roles.split(","),
        "amr": ["pwd", "mfa"],
        "mfa_verified": True,
    }
    encoded = encode_jwt_hs256(payload, secret=secret)
    return encoded, {
        "kind": "minted_hs256_jwt",
        "subject": args.subject,
        "issuer": args.issuer,
        "audience": args.audience,
        "roles": args.roles.split(","),
        "ttl_seconds": args.ttl_seconds,
        "secret_sha256_12": hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12],
    }


def has_path(obj: Any, path: tuple[str, ...]) -> bool:
    cur = obj
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def body_summary(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        summary: dict[str, Any] = {"type": "object", "keys": sorted(data.keys())[:20]}
        if isinstance(data.get("data"), list):
            summary["data_type"] = "list"
            summary["data_count"] = len(data["data"])
            if data["data"] and isinstance(data["data"][0], dict):
                summary["first_data_keys"] = sorted(data["data"][0].keys())[:20]
        elif isinstance(data.get("data"), dict):
            summary["data_type"] = "object"
            summary["data_keys"] = sorted(data["data"].keys())[:20]
        if isinstance(data.get("meta"), dict):
            summary["meta_keys"] = sorted(data["meta"].keys())[:20]
        if isinstance(data.get("user"), dict):
            summary["user_keys"] = sorted(data["user"].keys())[:20]
        return summary
    if isinstance(data, list):
        return {"type": "list", "count": len(data)}
    return {"type": type(data).__name__}


def request_json(
    *,
    base_url: str,
    probe: Probe,
    token: str | None,
    timeout: float,
    idempotency_prefix: str,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{probe.path}"
    headers = {
        "Accept": "application/json",
        "X-BFF-Api-Version": "2026-05-07",
        "X-Correlation-Id": f"cid-live-smoke-{int(time.time())}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-MFA-Token"] = "000000"
    body_bytes = None
    if probe.body is not None:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(probe.body).encode("utf-8")
    if probe.method in {"POST", "PUT", "PATCH", "DELETE"}:
        headers["Idempotency-Key"] = f"{idempotency_prefix}-{probe.family}"
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=probe.method)

    started = time.time()
    raw = b""
    status = 0
    response_headers: dict[str, str] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(resp.status)
            raw = resp.read()
            response_headers = dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
        response_headers = dict(exc.headers.items())
    except Exception as exc:  # noqa: BLE001 - evidence should record transport failure
        return {
            "family": probe.family,
            "method": probe.method,
            "path": probe.path,
            "status": 0,
            "ok": False,
            "duration_ms": round((time.time() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }

    text = raw.decode("utf-8", errors="replace")
    try:
        parsed: Any = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = None
    missing_paths = [
        ".".join(path)
        for path in probe.required_paths
        if not has_path(parsed, path)
    ]
    ok = status in probe.expect_status and not missing_paths
    return {
        "family": probe.family,
        "method": probe.method,
        "path": probe.path,
        "status": status,
        "ok": ok,
        "duration_ms": round((time.time() - started) * 1000),
        "expected_status": sorted(probe.expect_status),
        "missing_required_paths": missing_paths,
        "response_headers": {
            key: response_headers.get(key)
            for key in ("X-BFF-Api-Version", "X-Request-Id", "X-Correlation-Id")
            if response_headers.get(key)
        },
        "body_summary": body_summary(parsed),
        "body_prefix": text[:300] if not ok else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--include-writes", action="store_true")
    parser.add_argument("--subject", default="op-live-smoke")
    parser.add_argument("--roles", default="operator,admin,reviewer,approver")
    parser.add_argument("--issuer", default="pantheon-dev")
    parser.add_argument("--audience", default="bff-operators")
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token, auth_source = make_token(args)
    ts = utc_now()
    idempotency_prefix = f"bff-live-smoke-{int(time.time())}"
    probes = list(READ_PROBES)
    if args.include_writes:
        probes.extend(WRITE_PROBES)

    health = request_json(
        base_url=args.base_url,
        probe=Probe("GET", "/health", "health", expect_status={200}),
        token=None,
        timeout=args.timeout,
        idempotency_prefix=idempotency_prefix,
    )
    openapi = request_json(
        base_url=args.base_url,
        probe=Probe("GET", "/openapi.json", "openapi", expect_status={200}, required_paths=(("paths",),)),
        token=None,
        timeout=args.timeout,
        idempotency_prefix=idempotency_prefix,
    )
    route_results = [
        request_json(
            base_url=args.base_url,
            probe=probe,
            token=token,
            timeout=args.timeout,
            idempotency_prefix=idempotency_prefix,
        )
        for probe in probes
    ]

    all_results = [health, openapi, *route_results]
    failed = [item for item in all_results if not item.get("ok")]
    evidence = {
        "task_id": "BFF-LUV-AUTHED-LIVE-001",
        "generated_at": ts,
        "target_url": args.base_url.rstrip("/"),
        "auth_source": auth_source,
        "include_writes": args.include_writes,
        "commands": [
            "PANTHEON_BFF_SMOKE_JWT_SECRET=<redacted> scripts/probe_bff_authenticated_live.py --include-writes",
        ],
        "summary": {
            "total": len(all_results),
            "passed": len(all_results) - len(failed),
            "failed": len(failed),
            "read_probes": len(READ_PROBES),
            "write_probes": len(WRITE_PROBES) if args.include_writes else 0,
            "VITE_BFF_MODE_live_allowed": len(failed) == 0,
            "VITE_BFF_REAL_WRITES_true_allowed": args.include_writes and len(failed) == 0,
            "live_capital_side_effects": False,
        },
        "health": health,
        "openapi": openapi,
        "routes": route_results,
        "failed_routes": [
            {
                "family": item.get("family"),
                "method": item.get("method"),
                "path": item.get("path"),
                "status": item.get("status"),
                "missing_required_paths": item.get("missing_required_paths"),
                "error": item.get("error"),
                "body_prefix": item.get("body_prefix"),
            }
            for item in failed
        ],
    }

    output = args.output
    if not output:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        output = f"docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-live-smoke-{stamp}.json"
    out_path = REPO_ROOT / output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(evidence["summary"], sort_keys=True))
    print(str(out_path))
    if failed:
        for item in failed:
            print(
                f"FAIL {item.get('status')} {item.get('method')} {item.get('path')}: "
                f"{item.get('error') or item.get('missing_required_paths') or item.get('body_prefix')}",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
