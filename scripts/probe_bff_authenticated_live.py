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
import threading
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
    required_values: tuple[tuple[tuple[str, ...], Any], ...] = ()
    extract_paths: tuple[tuple[str, ...], ...] = ()
    extra_headers: tuple[tuple[str, str], ...] = ()
    expect_error_envelope: bool = False
    allowed_error_codes: tuple[str, ...] = ()
    min_turn_count: int = 0
    absent_item_values: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = ()


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
            ("data", "controlMode"),
            ("data", "uiActions"),
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


RBAC_ROLE_CASES: tuple[dict[str, Any], ...] = (
    {"label": "anonymous", "roles": None, "can_read": False, "can_write": False},
    {"label": "viewer", "roles": ["viewer"], "can_read": True, "can_write": False},
    {"label": "operator", "roles": ["operator"], "can_read": True, "can_write": True},
    {"label": "reviewer", "roles": ["reviewer"], "can_read": True, "can_write": True},
    {"label": "approver", "roles": ["approver"], "can_read": True, "can_write": True},
    {"label": "admin", "roles": ["admin"], "can_read": True, "can_write": True},
    {"label": "empty", "roles": [], "can_read": False, "can_write": False},
    {"label": "unknown", "roles": ["auditor"], "can_read": False, "can_write": False},
)

RBAC_READ_PATHS: tuple[str, ...] = (
    "/bff/strategies",
    "/bff/ranking-formulas",
    "/bff/agora/signals",
)

RBAC_WRITE_PATHS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("strategy", "/bff/strategies", {"name": ""}),
    ("ranking-formula", "/bff/ranking-formulas", {"name": ""}),
    ("agora-note", "/bff/agora/notes", {"title": "", "body": "live dry-run RBAC matrix"}),
    ("intervention-claim", "/bff/v5/interventions/int-live-rbac-matrix/claim", {"reason": ""}),
)
RBAC_WRITE_READBACK_RESOURCES = {"strategy", "ranking-formula", "agora-note"}

DENIED_ERROR_CODES = (
    "AUTH_REQUIRED",
    "FORBIDDEN",
    "INSUFFICIENT_ROLE",
    "PERMISSION_DENIED",
)
DRY_RUN_META_VALUES = (
    (("meta", "dryRun"), True),
    (("meta", "durable"), False),
    (("meta", "liveCapitalSideEffects"), False),
)
DRY_RUN_META_EXTRACT_PATHS = tuple(path for path, _expected in DRY_RUN_META_VALUES)
NOT_FOUND_ERROR_CODES = ("RESOURCE_NOT_FOUND", "OBJECT_NOT_FOUND", "NOT_FOUND")
APPROVAL_RACE_ACCEPTED_STATUSES = {200, 201, 202}
APPROVAL_RACE_SAFE_ERROR_CODES = (
    "RESOURCE_NOT_FOUND",
    "OBJECT_NOT_FOUND",
    "NOT_FOUND",
    "STATE_CONFLICT",
    "VERSION_CONFLICT",
    "CONFLICT",
    "VALIDATION_FAILED",
    "PRECONDITION_NOT_MET",
    "APPROVAL_ALREADY_DECIDED",
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def role_list(value: str) -> list[str]:
    return [role.strip() for role in value.split(",") if role.strip()]


def mint_hs256_jwt(
    *,
    subject: str,
    roles: list[str],
    issuer: str,
    audience: str,
    ttl_seconds: int,
    secret: str,
) -> str:
    from services.runtime_auth_inbound import encode_jwt_hs256

    now = int(time.time())
    payload = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
        "roles": roles,
        "amr": ["pwd", "mfa"],
        "mfa_verified": True,
    }
    return encode_jwt_hs256(payload, secret=secret)


def make_token(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    token = os.getenv("PANTHEON_BFF_SMOKE_BEARER_TOKEN", "").strip()
    if token:
        return token.removeprefix("Bearer ").strip(), {
            "kind": "provided_bearer",
            "sha256_12": sha256_12(token),
        }

    secret = (
        os.getenv("PANTHEON_BFF_SMOKE_JWT_SECRET", "").strip()
        or os.getenv("PANTHEON_BFF_JWT_SECRET", "").strip()
    )
    if not secret:
        raise SystemExit(
            "Set PANTHEON_BFF_SMOKE_BEARER_TOKEN or PANTHEON_BFF_SMOKE_JWT_SECRET/PANTHEON_BFF_JWT_SECRET"
        )

    roles = role_list(args.roles)
    encoded = mint_hs256_jwt(
        subject=args.subject,
        roles=roles,
        issuer=args.issuer,
        audience=args.audience,
        ttl_seconds=args.ttl_seconds,
        secret=secret,
    )
    return encoded, {
        "kind": "minted_hs256_jwt",
        "subject": args.subject,
        "issuer": args.issuer,
        "audience": args.audience,
        "roles": roles,
        "ttl_seconds": args.ttl_seconds,
        "secret_sha256_12": sha256_12(secret),
    }


def has_path(obj: Any, path: tuple[str, ...]) -> bool:
    return path_value(obj, path, default=None) is not None


_MISSING = object()


def path_value(obj: Any, path: tuple[str, ...], *, default: Any = _MISSING) -> Any:
    cur = obj
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def error_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    detail = data.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return detail["error"]
    if isinstance(data.get("error"), dict):
        return data["error"]
    return {}


def error_code_from(data: Any) -> str:
    return str(error_payload(data).get("code") or "")


def is_error_envelope(data: Any) -> bool:
    return bool(error_code_from(data))


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


def _extracted_value(result: dict[str, Any], path: tuple[str, ...]) -> Any:
    extracted = result.get("extracted")
    if not isinstance(extracted, dict):
        return None
    return extracted.get(".".join(path))


def dry_run_meta_side_effect_check(result: dict[str, Any], *, kind: str) -> dict[str, Any]:
    dry_run = _extracted_value(result, ("meta", "dryRun"))
    durable = _extracted_value(result, ("meta", "durable"))
    side_effects = _extracted_value(result, ("meta", "liveCapitalSideEffects"))
    ok = (
        result.get("ok") is True
        and dry_run is True
        and durable is False
        and side_effects is False
    )
    return {
        "kind": kind,
        "ok": ok,
        "dryRun": dry_run,
        "durable": durable,
        "liveCapitalSideEffects": side_effects,
    }


def readback_side_effect_check(
    result: dict[str, Any],
    *,
    target_family: str,
    target_id: str,
) -> dict[str, Any]:
    error_code = str(result.get("error_code") or "")
    ok = (
        result.get("ok") is True
        and result.get("error_envelope") is True
        and error_code in NOT_FOUND_ERROR_CODES
    )
    return {
        "kind": "readback_not_persisted",
        "ok": ok,
        "target_family": target_family,
        "target_id_sha256_12": sha256_12(str(target_id)),
        "status": result.get("status"),
        "error_envelope": result.get("error_envelope") is True,
        "error_code": error_code or None,
    }


def list_absence_side_effect_check(
    result: dict[str, Any],
    *,
    target_family: str,
    target_id: str,
) -> dict[str, Any]:
    checks = result.get("absent_item_checks") if isinstance(result.get("absent_item_checks"), list) else []
    ok_checks = [check for check in checks if isinstance(check, dict) and check.get("ok") is True]
    items_checked = sum(int(check.get("items_checked") or 0) for check in ok_checks)
    ok = (
        result.get("ok") is True
        and int(result.get("status") or 0) == 200
        and bool(checks)
        and len(ok_checks) == len(checks)
    )
    return {
        "kind": "list_readback_not_persisted",
        "ok": ok,
        "target_family": target_family,
        "target_id_sha256_12": sha256_12(str(target_id)),
        "status": result.get("status"),
        "absent_checks": len(ok_checks),
        "items_checked": items_checked,
    }


def invalid_dry_run_side_effect_check(result: dict[str, Any]) -> dict[str, Any]:
    error_code = str(result.get("error_code") or "")
    ok = (
        result.get("ok") is True
        and result.get("error_envelope") is True
        and error_code == "VALIDATION_FAILED"
    )
    return {
        "kind": "validation_rejected_before_persistence",
        "ok": ok,
        "error_code": error_code or None,
    }


def rbac_write_side_effect_check(
    result: dict[str, Any],
    *,
    can_write: bool,
    marker: str,
    readback_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if can_write:
        check = dry_run_meta_side_effect_check(result, kind="rbac_dry_run_write_meta")
        if readback_check is not None:
            check["readback_not_persisted"] = readback_check
            check["ok"] = check["ok"] is True and readback_check.get("ok") is True
    else:
        error_code = str(result.get("error_code") or "")
        check = {
            "kind": "authorization_rejected_before_persistence",
            "ok": (
                result.get("ok") is True
                and result.get("error_envelope") is True
                and error_code in DENIED_ERROR_CODES
            ),
            "error_code": error_code or None,
        }
    check["target_marker_sha256_12"] = sha256_12(marker)
    return check


def probe_path_from_href(href: str) -> str:
    parsed = urllib.parse.urlparse(str(href or ""))
    if parsed.scheme and parsed.netloc:
        path = parsed.path or "/"
    else:
        path = str(href or "")
    query = parsed.query
    if query and "?" not in path:
        return f"{path}?{query}"
    return path


def rbac_token_from_env(label: str) -> str:
    env_name = f"PANTHEON_BFF_RBAC_{label.upper().replace('-', '_')}_TOKEN"
    return os.getenv(env_name, "").removeprefix("Bearer ").strip()


def rbac_tokens_from_json() -> dict[str, str]:
    raw = os.getenv("PANTHEON_BFF_RBAC_TOKENS_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"PANTHEON_BFF_RBAC_TOKENS_JSON is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("PANTHEON_BFF_RBAC_TOKENS_JSON must be an object keyed by role label")
    tokens: dict[str, str] = {}
    for label, value in parsed.items():
        token = ""
        if isinstance(value, str):
            token = value
        elif isinstance(value, dict):
            token = str(value.get("token") or value.get("bearer") or "")
        token = token.removeprefix("Bearer ").strip()
        if token:
            tokens[str(label)] = token
    return tokens


def rbac_provided_labels() -> list[str]:
    return [str(case["label"]) for case in RBAC_ROLE_CASES if case["roles"] is not None]


def duplicate_rbac_bearer_label_groups(tokens_by_label: dict[str, str]) -> list[list[str]]:
    labels_by_token: dict[str, list[str]] = {}
    for label in rbac_provided_labels():
        token = tokens_by_label.get(label, "")
        if token:
            labels_by_token.setdefault(token, []).append(label)
    return [labels for labels in labels_by_token.values() if len(labels) > 1]


def make_rbac_tokens(args: argparse.Namespace) -> tuple[dict[str, str | None], dict[str, Any]]:
    provided = rbac_tokens_from_json()
    for case in RBAC_ROLE_CASES:
        label = str(case["label"])
        env_token = rbac_token_from_env(label)
        if env_token:
            provided[label] = env_token

    secret = (
        os.getenv("PANTHEON_BFF_SMOKE_JWT_SECRET", "").strip()
        or os.getenv("PANTHEON_BFF_JWT_SECRET", "").strip()
    )
    tokens: dict[str, str | None] = {"anonymous": None}
    source: dict[str, Any] = {
        "kind": "rbac_matrix",
        "cases": {},
        "require_provided_tokens": args.require_provided_rbac_tokens,
    }
    missing: list[str] = []
    for case in RBAC_ROLE_CASES:
        label = str(case["label"])
        roles = case["roles"]
        if roles is None:
            source["cases"][label] = {"kind": "anonymous"}
            continue
        token = provided.get(label)
        if token:
            tokens[label] = token
            source["cases"][label] = {
                "kind": "provided_bearer",
                "sha256_12": sha256_12(token),
            }
            continue
        if secret and not args.require_provided_rbac_tokens:
            minted = mint_hs256_jwt(
                subject=f"{args.subject}-{label}",
                roles=list(roles),
                issuer=args.issuer,
                audience=args.audience,
                ttl_seconds=args.ttl_seconds,
                secret=secret,
            )
            tokens[label] = minted
            source["cases"][label] = {
                "kind": "minted_hs256_jwt",
                "roles": list(roles),
                "sha256_12": sha256_12(minted),
            }
            continue
        missing.append(label)
    if missing:
        raise SystemExit(
            "Missing RBAC bearer tokens for labels "
            f"{', '.join(missing)}. Set PANTHEON_BFF_RBAC_TOKENS_JSON, "
            "PANTHEON_BFF_RBAC_<LABEL>_TOKEN, or a JWT secret for dev/staging minting."
        )
    if args.require_provided_rbac_tokens:
        provided_values = {
            label: str(tokens.get(label) or "")
            for label in rbac_provided_labels()
            if tokens.get(label)
        }
        duplicate_groups = duplicate_rbac_bearer_label_groups(provided_values)
        source["provided_bearer_count"] = len(provided_values)
        source["distinct_provided_bearer_count"] = len(set(provided_values.values()))
        source["distinct_provided_bearers"] = not duplicate_groups and len(provided_values) == len(rbac_provided_labels())
        source["duplicate_bearer_label_groups"] = duplicate_groups
        if duplicate_groups:
            duplicate_text = "; ".join("/".join(group) for group in duplicate_groups)
            raise SystemExit(
                "PANTHEON_BFF_RBAC_TOKENS_JSON must provide distinct bearer tokens per RBAC label: "
                f"{duplicate_text}"
            )
    if secret and not args.require_provided_rbac_tokens:
        source["secret_sha256_12"] = sha256_12(secret)
    return tokens, source


def marker_payload(path: str, base: dict[str, Any], marker: str) -> dict[str, Any]:
    payload = dict(base)
    if path == "/bff/strategies":
        payload["name"] = marker
    elif path == "/bff/ranking-formulas":
        payload["name"] = marker
    elif path == "/bff/agora/notes":
        payload["title"] = marker
        payload["id"] = marker
    elif path.endswith("/claim"):
        payload["reason"] = marker
    return payload


def rbac_write_readback_probe(resource: str, created_id: str) -> Probe | None:
    quoted_id = urllib.parse.quote(str(created_id), safe="")
    if resource == "strategy":
        return Probe(
            "GET",
            f"/bff/strategies/{quoted_id}",
            "rbac-write-strategy-readback-not-persisted",
            expect_status={404},
            expect_error_envelope=True,
            allowed_error_codes=NOT_FOUND_ERROR_CODES,
        )
    if resource == "ranking-formula":
        return Probe(
            "GET",
            f"/bff/ranking-formulas/{quoted_id}",
            "rbac-write-ranking-formula-readback-not-persisted",
            expect_status={404},
            expect_error_envelope=True,
            allowed_error_codes=NOT_FOUND_ERROR_CODES,
        )
    if resource == "agora-note":
        return Probe(
            "GET",
            "/bff/agora/notes",
            "rbac-write-agora-note-readback-not-persisted",
            expect_status={200},
            absent_item_values=(
                (("items",), ("id",), str(created_id)),
                (("items",), ("note_id",), str(created_id)),
            ),
        )
    return None


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
    headers.update(dict(probe.extra_headers))
    body_bytes = None
    idempotency_key = ""
    if probe.body is not None:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(probe.body).encode("utf-8")
    if probe.method in {"POST", "PUT", "PATCH", "DELETE"}:
        idempotency_key = f"{idempotency_prefix}-{probe.family}"
        headers["Idempotency-Key"] = idempotency_key
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
    for path, expected in probe.required_values:
        actual = path_value(parsed, path)
        if actual != expected:
            missing_paths.append(f"{'.'.join(path)}={expected!r}")
    absent_item_checks: list[dict[str, Any]] = []
    for list_path, item_path, forbidden_value in probe.absent_item_values:
        list_value = path_value(parsed, list_path, default=None)
        target_hash = sha256_12(str(forbidden_value))
        check: dict[str, Any] = {
            "list_path": ".".join(list_path),
            "item_path": ".".join(item_path),
            "target_sha256_12": target_hash,
        }
        if isinstance(list_value, list):
            found = any(
                isinstance(item, dict)
                and path_value(item, item_path, default=None) == forbidden_value
                for item in list_value
            )
            check.update({"ok": not found, "found": found, "items_checked": len(list_value)})
            if found:
                missing_paths.append(f"absent_item:{'.'.join(list_path)}:{'.'.join(item_path)}:{target_hash}")
        else:
            check.update({"ok": False, "found": None, "items_checked": 0})
            missing_paths.append(f"absent_item_list:{'.'.join(list_path)}")
        absent_item_checks.append(check)
    if probe.min_turn_count:
        turns = None
        if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
            turns = parsed["data"].get("turns")
        if not isinstance(turns, list) or len(turns) < probe.min_turn_count:
            missing_paths.append(f"data.turns>={probe.min_turn_count}")
    error_code = error_code_from(parsed)
    if probe.expect_error_envelope and not error_code:
        missing_paths.append("error.code")
    if probe.allowed_error_codes and error_code not in set(probe.allowed_error_codes):
        missing_paths.append(f"error.code in {sorted(probe.allowed_error_codes)}")
    ok = status in probe.expect_status and not missing_paths
    extracted = {}
    for path in probe.extract_paths:
        value = path_value(parsed, path)
        if value is not _MISSING:
            extracted[".".join(path)] = value
    result = {
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
        "error_code": error_code or None,
        "error_envelope": is_error_envelope(parsed),
    }
    if idempotency_key:
        result["request_idempotency_key_sha256_12"] = sha256_12(idempotency_key)
    if extracted:
        result["extracted"] = extracted
    if absent_item_checks:
        result["absent_item_checks"] = absent_item_checks
    if probe.family == "management-ai-multiturn" and isinstance(parsed, dict):
        data = parsed.get("data")
        if isinstance(data, dict):
            conversation = data.get("conversation")
            if isinstance(conversation, dict) and conversation.get("href"):
                result["conversation_href"] = str(conversation["href"])
    return result


def approval_race_tokens(args: argparse.Namespace, primary_token: str) -> tuple[str, str, dict[str, Any]]:
    token_a = os.getenv("PANTHEON_BFF_APPROVAL_RACE_TOKEN_A", "").removeprefix("Bearer ").strip()
    token_b = os.getenv("PANTHEON_BFF_APPROVAL_RACE_TOKEN_B", "").removeprefix("Bearer ").strip()
    if token_a and token_b:
        return token_a, token_b, {
            "kind": "provided_bearer_pair",
            "token_a_sha256_12": sha256_12(token_a),
            "token_b_sha256_12": sha256_12(token_b),
        }

    secret = (
        os.getenv("PANTHEON_BFF_SMOKE_JWT_SECRET", "").strip()
        or os.getenv("PANTHEON_BFF_JWT_SECRET", "").strip()
    )
    if secret and not args.require_provided_approval_race_tokens:
        minted_a = mint_hs256_jwt(
            subject=f"{args.subject}-approval-race-a",
            roles=["approver"],
            issuer=args.issuer,
            audience=args.audience,
            ttl_seconds=args.ttl_seconds,
            secret=secret,
        )
        minted_b = mint_hs256_jwt(
            subject=f"{args.subject}-approval-race-b",
            roles=["admin"],
            issuer=args.issuer,
            audience=args.audience,
            ttl_seconds=args.ttl_seconds,
            secret=secret,
        )
        return minted_a, minted_b, {
            "kind": "minted_hs256_jwt_pair",
            "subjects": [f"{args.subject}-approval-race-a", f"{args.subject}-approval-race-b"],
            "roles": [["approver"], ["admin"]],
            "secret_sha256_12": sha256_12(secret),
        }

    if args.allow_single_token_approval_race:
        return primary_token, primary_token, {
            "kind": "single_bearer_fallback",
            "sha256_12": sha256_12(primary_token),
            "warning": "same bearer token used for both race requests; this is not a true multi-operator proof",
        }

    raise SystemExit(
        "Set PANTHEON_BFF_APPROVAL_RACE_TOKEN_A and PANTHEON_BFF_APPROVAL_RACE_TOKEN_B, "
        "or set PANTHEON_BFF_SMOKE_JWT_SECRET/PANTHEON_BFF_JWT_SECRET for dev/staging minting. "
        "Use --allow-single-token-approval-race only for transport debugging, not final evidence."
    )


def build_approval_race_results(
    *,
    args: argparse.Namespace,
    base_url: str,
    token: str,
    timeout: float,
    idempotency_prefix: str,
) -> dict[str, Any]:
    target_id = args.approval_race_id.strip()
    if not target_id:
        raise SystemExit("--include-approval-race requires --approval-race-id for an expendable staging approval")

    token_a, token_b, token_source = approval_race_tokens(args, token)
    path = f"/bff/approvals/{urllib.parse.quote(target_id, safe='')}/decide"
    barrier = threading.Barrier(2)
    results: list[dict[str, Any] | None] = [None, None]

    def run_one(index: int, bearer: str, actor_label: str) -> None:
        barrier.wait(timeout=timeout)
        result = request_json(
            base_url=base_url,
            probe=Probe(
                "POST",
                path,
                f"approval-race-{actor_label}",
                body={
                    "decision": args.approval_race_decision,
                    "memo": f"approval race probe {actor_label}",
                },
                expect_status=set(range(200, 600)),
                required_paths=(),
            ),
            token=bearer,
            timeout=timeout,
            idempotency_prefix=f"{idempotency_prefix}-race-{actor_label}",
        )
        result["actor_label"] = actor_label
        result["request_bearer_sha256_12"] = sha256_12(bearer)
        results[index] = result

    threads = [
        threading.Thread(target=run_one, args=(0, token_a, "a"), daemon=True),
        threading.Thread(target=run_one, args=(1, token_b, "b"), daemon=True),
    ]
    started = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout + 2.0)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("approval race probe threads did not finish before timeout")

    race_results = [result for result in results if result is not None]
    target_hash = sha256_12(target_id)
    for result in race_results:
        result["target_id_sha256_12"] = target_hash
    accepted = [
        result
        for result in race_results
        if int(result.get("status") or 0) in APPROVAL_RACE_ACCEPTED_STATUSES
    ]
    safe_errors = [
        result
        for result in race_results
        if int(result.get("status") or 0) >= 400
        and result.get("error_envelope")
        and str(result.get("error_code") or "") in APPROVAL_RACE_SAFE_ERROR_CODES
    ]
    transport_failures = [result for result in race_results if int(result.get("status") or 0) == 0]
    duplicate_winners = len(accepted) > 1
    bounded = (
        len(race_results) == 2
        and not transport_failures
        and not duplicate_winners
        and len(accepted) == 1
        and len(safe_errors) == 1
    )
    return {
        "family": "approval-race",
        "method": "POST",
        "path": path,
        "status": "/".join(str(result.get("status") or 0) for result in race_results),
        "target_id": target_id,
        "target_id_sha256_12": target_hash,
        "duration_ms": round((time.time() - started) * 1000),
        "ok": bounded,
        "bounded": bounded,
        "accepted_count": len(accepted),
        "safe_error_count": len(safe_errors),
        "duplicate_winners": duplicate_winners,
        "token_source": token_source,
        "expectation": "one accepted decision plus one safe BffErrorEnvelope loser; two safe errors are not a live race proof",
        "results": race_results,
    }


def build_two_man_race_results(
    *,
    args: argparse.Namespace,
    base_url: str,
    token: str,
    timeout: float,
    idempotency_prefix: str,
) -> dict[str, Any]:
    target_id = args.two_man_race_id.strip()
    if not target_id:
        raise SystemExit("--include-two-man-race requires --two-man-race-id for an expendable staging intervention")

    token_a, token_b, token_source = approval_race_tokens(args, token)
    path = f"/bff/v5/interventions/{urllib.parse.quote(target_id, safe='')}/two-man-sign"
    target_hash = sha256_12(target_id)
    barrier = threading.Barrier(2)
    results: list[dict[str, Any] | None] = [None, None]
    stamp = int(time.time())

    def run_one(index: int, bearer: str, actor_label: str) -> None:
        barrier.wait(timeout=timeout)
        signature_id = f"tms-live-{target_id}-{actor_label}-{stamp}"
        result = request_json(
            base_url=base_url,
            probe=Probe(
                "POST",
                path,
                "two-man-race",
                body={
                    "twoManSignatureId": signature_id,
                    "command": "RemediateSentinelIntervention",
                    "target": {"type": "SentinelIntervention", "id": target_id},
                    "signerOperatorIds": ["live-two-man-primary", "live-two-man-secondary"],
                    "reason": f"two-man race probe {actor_label}",
                },
                expect_status=set(range(200, 600)),
                required_paths=(),
                extract_paths=(
                    ("data", "command_id"),
                    ("data", "commandId"),
                    ("meta", "idempotency", "replayed"),
                ),
            ),
            token=bearer,
            timeout=timeout,
            idempotency_prefix=f"{idempotency_prefix}-two-man-race",
        )
        result["actor_label"] = actor_label
        result["target_id_sha256_12"] = target_hash
        result["request_signature_id_sha256_12"] = sha256_12(signature_id)
        result["request_bearer_sha256_12"] = sha256_12(bearer)
        results[index] = result

    threads = [
        threading.Thread(target=run_one, args=(0, token_a, "a"), daemon=True),
        threading.Thread(target=run_one, args=(1, token_b, "b"), daemon=True),
    ]
    started = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout + 2.0)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("two-man race probe threads did not finish before timeout")

    race_results = [result for result in results if result is not None]
    accepted = [
        result
        for result in race_results
        if int(result.get("status") or 0) in APPROVAL_RACE_ACCEPTED_STATUSES
    ]
    replayed = [
        result
        for result in race_results
        if _extracted_value(result, ("meta", "idempotency", "replayed")) is True
    ]
    command_ids = []
    for result in accepted:
        command_id = (
            _extracted_value(result, ("data", "command_id"))
            or _extracted_value(result, ("data", "commandId"))
        )
        if command_id:
            command_ids.append(str(command_id))
    distinct_command_ids = len(set(command_ids)) == len(command_ids) == len(accepted) == 2
    transport_failures = [result for result in race_results if int(result.get("status") or 0) == 0]
    operator_scoped = (
        len(race_results) == 2
        and not transport_failures
        and len(accepted) == 2
        and not replayed
        and distinct_command_ids
    )
    return {
        "family": "two-man-race",
        "method": "POST",
        "path": path,
        "status": "/".join(str(result.get("status") or 0) for result in race_results),
        "target_id": target_id,
        "target_id_sha256_12": target_hash,
        "duration_ms": round((time.time() - started) * 1000),
        "ok": operator_scoped,
        "operator_scoped": operator_scoped,
        "accepted_count": len(accepted),
        "replayed_count": len(replayed),
        "distinct_command_ids": distinct_command_ids,
        "command_id_count": len(set(command_ids)),
        "token_source": token_source,
        "expectation": "two distinct operators share one idempotency key and both create independent two-man signatures without replay",
        "results": race_results,
    }


def build_rbac_matrix_results(
    *,
    args: argparse.Namespace,
    base_url: str,
    timeout: float,
    idempotency_prefix: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tokens, source = make_rbac_tokens(args)
    results: list[dict[str, Any]] = []
    for case in RBAC_ROLE_CASES:
        label = str(case["label"])
        token = tokens.get(label)
        can_read = bool(case["can_read"])
        can_write = bool(case["can_write"])
        deny_status = {401, 403} if label == "anonymous" else {403}

        case_info = source.get("cases", {}).get(label, {})
        auth_case_kind = str(case_info.get("kind") or "") if isinstance(case_info, dict) else ""
        token_hash = sha256_12(token) if token else None

        for path in RBAC_READ_PATHS:
            resource = path.strip("/").replace("/", "-")
            probe = Probe(
                "GET",
                path,
                f"rbac-read-{label}-{resource}",
                expect_status={200} if can_read else deny_status,
                expect_error_envelope=not can_read,
                allowed_error_codes=() if can_read else DENIED_ERROR_CODES,
            )
            result = request_json(
                base_url=base_url,
                probe=probe,
                token=token,
                timeout=timeout,
                idempotency_prefix=idempotency_prefix,
            )
            result["rbac_label"] = label
            result["rbac_operation"] = "read"
            result["rbac_resource"] = resource
            result["auth_case_kind"] = auth_case_kind
            if token_hash:
                result["request_bearer_sha256_12"] = token_hash
            results.append(result)

        for name, path, base_body in RBAC_WRITE_PATHS:
            marker = f"live-rbac-{label}-{name}-{int(time.time())}"
            marker_hash = sha256_12(marker)
            probe = Probe(
                "POST",
                path,
                f"rbac-write-{label}-{name}",
                body=marker_payload(path, base_body, marker),
                expect_status={200} if can_write else deny_status,
                required_paths=(("data", "id"),) if can_write and name in RBAC_WRITE_READBACK_RESOURCES else (),
                required_values=DRY_RUN_META_VALUES if can_write else (),
                extract_paths=DRY_RUN_META_EXTRACT_PATHS
                + ((("data", "id"),) if name in RBAC_WRITE_READBACK_RESOURCES else ()),
                extra_headers=(("X-Dry-Run", "1"),),
                expect_error_envelope=not can_write,
                allowed_error_codes=() if can_write else DENIED_ERROR_CODES,
            )
            result = request_json(
                base_url=base_url,
                probe=probe,
                token=token,
                timeout=timeout,
                idempotency_prefix=idempotency_prefix,
            )
            result["rbac_label"] = label
            result["rbac_operation"] = "write"
            result["rbac_resource"] = name
            result["auth_case_kind"] = auth_case_kind
            if token_hash:
                result["request_bearer_sha256_12"] = token_hash
            result["request_marker_sha256_12"] = marker_hash
            readback_check: dict[str, Any] | None = None
            created_id = _extracted_value(result, ("data", "id"))
            if can_write and created_id:
                readback_probe = rbac_write_readback_probe(name, str(created_id))
                if readback_probe is not None:
                    readback = request_json(
                        base_url=base_url,
                        probe=readback_probe,
                        token=token,
                        timeout=timeout,
                        idempotency_prefix=idempotency_prefix,
                    )
                    if name == "agora-note":
                        readback_check = list_absence_side_effect_check(
                            readback,
                            target_family=f"rbac-write-{name}",
                            target_id=str(created_id),
                        )
                    else:
                        readback_check = readback_side_effect_check(
                            readback,
                            target_family=f"rbac-write-{name}",
                            target_id=str(created_id),
                        )
            result["side_effect_check"] = rbac_write_side_effect_check(
                result,
                can_write=can_write,
                marker=marker,
                readback_check=readback_check,
            )
            result.pop("extracted", None)
            results.append(result)
    return results, source


def build_dry_run_results(
    *,
    base_url: str,
    token: str,
    timeout: float,
    idempotency_prefix: str,
) -> list[dict[str, Any]]:
    stamp = int(time.time())
    success_specs = (
        (
            "dry-run-strategy-create",
            "/bff/strategies",
            {"name": f"live-dry-run-strategy-{stamp}"},
            "/bff/strategies/{id}",
        ),
        (
            "dry-run-ranking-formula-create",
            "/bff/ranking-formulas",
            {"name": f"live-dry-run-ranking-formula-{stamp}"},
            "/bff/ranking-formulas/{id}",
        ),
        (
            "dry-run-v5-intervention-claim",
            "/bff/v5/interventions/int-live-dry-run/claim",
            {"reason": f"live-dry-run-claim-{stamp}"},
            "",
        ),
    )
    invalid_specs = (
        ("dry-run-invalid-strategy", "/bff/strategies", {}),
        ("dry-run-invalid-ranking-formula", "/bff/ranking-formulas", {}),
    )
    results: list[dict[str, Any]] = []

    for family, path, body, detail_template in success_specs:
        result = request_json(
            base_url=base_url,
            probe=Probe(
                "POST",
                path,
                family,
                body=body,
                expect_status={200},
                required_values=DRY_RUN_META_VALUES,
                extract_paths=DRY_RUN_META_EXTRACT_PATHS + (("data", "id"),),
                extra_headers=(("X-Dry-Run", "1"),),
            ),
            token=token,
            timeout=timeout,
            idempotency_prefix=idempotency_prefix,
        )
        result["side_effect_check"] = dry_run_meta_side_effect_check(
            result,
            kind="dry_run_preview_meta" if detail_template else "dry_run_command_meta",
        )
        results.append(result)
        created_id = (result.get("extracted") or {}).get("data.id")
        if result.get("ok") and detail_template and created_id:
            readback = request_json(
                base_url=base_url,
                probe=Probe(
                    "GET",
                    detail_template.format(id=urllib.parse.quote(str(created_id), safe="")),
                    f"{family}-readback-not-persisted",
                    expect_status={404},
                    expect_error_envelope=True,
                    allowed_error_codes=NOT_FOUND_ERROR_CODES,
                ),
                token=token,
                timeout=timeout,
                idempotency_prefix=idempotency_prefix,
            )
            readback["side_effect_check"] = readback_side_effect_check(
                readback,
                target_family=family,
                target_id=str(created_id),
            )
            results.append(readback)

    for family, path, body in invalid_specs:
        result = request_json(
            base_url=base_url,
            probe=Probe(
                "POST",
                path,
                family,
                body=body,
                expect_status={422},
                extra_headers=(("X-Dry-Run", "1"),),
                expect_error_envelope=True,
                allowed_error_codes=("VALIDATION_FAILED",),
            ),
            token=token,
            timeout=timeout,
            idempotency_prefix=idempotency_prefix,
        )
        result["side_effect_check"] = invalid_dry_run_side_effect_check(result)
        results.append(result)
    return results


def apply_strict_live_evidence(args: argparse.Namespace) -> None:
    if not args.strict_live_evidence:
        return

    args.include_rbac_matrix = True
    args.include_dry_run = True
    args.include_approval_race = True
    args.include_two_man_race = True
    args.require_provided_rbac_tokens = True
    args.require_provided_approval_race_tokens = True

    if not os.getenv("PANTHEON_BFF_SMOKE_BEARER_TOKEN", "").strip():
        raise SystemExit(
            "--strict-live-evidence requires PANTHEON_BFF_SMOKE_BEARER_TOKEN; "
            "dev/staging minted JWTs are not accepted as final live bearer evidence"
        )
    if not args.approval_race_id.strip():
        raise SystemExit("--strict-live-evidence requires --approval-race-id for an expendable staging approval")
    if not args.two_man_race_id.strip():
        raise SystemExit("--strict-live-evidence requires --two-man-race-id for an expendable staging intervention")
    race_token_a = os.getenv("PANTHEON_BFF_APPROVAL_RACE_TOKEN_A", "").removeprefix("Bearer ").strip()
    race_token_b = os.getenv("PANTHEON_BFF_APPROVAL_RACE_TOKEN_B", "").removeprefix("Bearer ").strip()
    if not (race_token_a and race_token_b):
        raise SystemExit(
            "--strict-live-evidence requires PANTHEON_BFF_APPROVAL_RACE_TOKEN_A and "
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B for two distinct operators"
        )
    if race_token_a == race_token_b:
        raise SystemExit(
            "--strict-live-evidence requires PANTHEON_BFF_APPROVAL_RACE_TOKEN_A and "
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B to be distinct bearer tokens"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--include-writes", action="store_true")
    parser.add_argument("--include-rbac-matrix", action="store_true")
    parser.add_argument("--include-dry-run", action="store_true")
    parser.add_argument("--include-approval-race", action="store_true")
    parser.add_argument("--include-two-man-race", action="store_true")
    parser.add_argument("--strict-live-evidence", action="store_true")
    parser.add_argument("--approval-race-id", default="")
    parser.add_argument("--two-man-race-id", default="")
    parser.add_argument("--approval-race-decision", default="approve")
    parser.add_argument("--require-provided-rbac-tokens", action="store_true")
    parser.add_argument("--require-provided-approval-race-tokens", action="store_true")
    parser.add_argument("--allow-single-token-approval-race", action="store_true")
    parser.add_argument("--subject", default="op-live-smoke")
    parser.add_argument("--roles", default="operator,admin,reviewer,approver")
    parser.add_argument("--issuer", default="pantheon-dev")
    parser.add_argument("--audience", default="bff-operators")
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    apply_strict_live_evidence(args)
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
    followup_results = []
    for result in route_results:
        if result.get("family") != "management-ai-multiturn" or not result.get("ok"):
            continue
        conversation_href = result.get("conversation_href")
        if not conversation_href:
            continue
        followup_results.append(
            request_json(
                base_url=args.base_url,
                probe=Probe(
                    "GET",
                    probe_path_from_href(str(conversation_href)),
                    "management-ai-conversation-readback",
                    required_paths=(("data", "sessionId"), ("data", "turns")),
                    min_turn_count=2,
                ),
                token=token,
                timeout=args.timeout,
                idempotency_prefix=idempotency_prefix,
            )
        )
    route_results.extend(followup_results)

    rbac_results: list[dict[str, Any]] = []
    rbac_auth_source: dict[str, Any] | None = None
    if args.include_rbac_matrix:
        rbac_results, rbac_auth_source = build_rbac_matrix_results(
            args=args,
            base_url=args.base_url,
            timeout=args.timeout,
            idempotency_prefix=idempotency_prefix,
        )

    dry_run_results: list[dict[str, Any]] = []
    if args.include_dry_run:
        dry_run_results = build_dry_run_results(
            base_url=args.base_url,
            token=token,
            timeout=args.timeout,
            idempotency_prefix=idempotency_prefix,
        )

    approval_race_result: dict[str, Any] | None = None
    if args.include_approval_race:
        approval_race_result = build_approval_race_results(
            args=args,
            base_url=args.base_url,
            token=token,
            timeout=args.timeout,
            idempotency_prefix=idempotency_prefix,
        )

    two_man_race_result: dict[str, Any] | None = None
    if args.include_two_man_race:
        two_man_race_result = build_two_man_race_results(
            args=args,
            base_url=args.base_url,
            token=token,
            timeout=args.timeout,
            idempotency_prefix=idempotency_prefix,
        )

    all_results = [
        health,
        openapi,
        *route_results,
        *rbac_results,
        *dry_run_results,
        *([approval_race_result] if approval_race_result else []),
        *([two_man_race_result] if two_man_race_result else []),
    ]
    failed = [item for item in all_results if not item.get("ok")]
    evidence = {
        "task_id": "BFF-LUV-AUTHED-LIVE-001",
        "generated_at": ts,
        "target_url": args.base_url.rstrip("/"),
        "auth_source": auth_source,
        "rbac_auth_source": rbac_auth_source,
        "include_writes": args.include_writes,
        "include_rbac_matrix": args.include_rbac_matrix,
        "include_dry_run": args.include_dry_run,
        "include_approval_race": args.include_approval_race,
        "include_two_man_race": args.include_two_man_race,
        "strict_live_evidence": args.strict_live_evidence,
        "commands": [
            "PANTHEON_BFF_SMOKE_BEARER_TOKEN=<redacted> PANTHEON_BFF_RBAC_TOKENS_JSON=<redacted> PANTHEON_BFF_APPROVAL_RACE_TOKEN_A=<redacted> PANTHEON_BFF_APPROVAL_RACE_TOKEN_B=<redacted> scripts/probe_bff_authenticated_live.py --strict-live-evidence --include-writes --approval-race-id=<expendable-staging-approval-id> --two-man-race-id=<expendable-staging-intervention-id>",
        ],
        "summary": {
            "total": len(all_results),
            "passed": len(all_results) - len(failed),
            "failed": len(failed),
            "read_probes": len(READ_PROBES),
            "write_probes": len(WRITE_PROBES) if args.include_writes else 0,
            "rbac_matrix_probes": len(rbac_results),
            "rbac_write_probes": len([item for item in rbac_results if str(item.get("family") or "").startswith("rbac-write-")]),
            "rbac_write_side_effect_proofs": len([
                item
                for item in rbac_results
                if str(item.get("family") or "").startswith("rbac-write-")
                and isinstance(item.get("side_effect_check"), dict)
                and item["side_effect_check"].get("ok") is True
            ]),
            "dry_run_probes": len(dry_run_results),
            "approval_race_probes": 1 if approval_race_result else 0,
            "approval_race_bounded": bool(approval_race_result and approval_race_result.get("bounded")),
            "two_man_race_probes": 1 if two_man_race_result else 0,
            "two_man_race_operator_scoped": bool(two_man_race_result and two_man_race_result.get("operator_scoped")),
            "VITE_BFF_MODE_live_allowed": len(failed) == 0,
            "VITE_BFF_REAL_WRITES_true_allowed": args.include_writes and len(failed) == 0,
            "live_capital_side_effects": False,
        },
        "health": health,
        "openapi": openapi,
        "routes": route_results,
        "rbac_matrix": rbac_results,
        "dry_run": dry_run_results,
        "approval_race": approval_race_result,
        "two_man_race": two_man_race_result,
        "failed_routes": [
            {
                "family": item.get("family"),
                "method": item.get("method"),
                "path": item.get("path"),
                "status": item.get("status"),
                "missing_required_paths": item.get("missing_required_paths"),
                "error_code": item.get("error_code"),
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
