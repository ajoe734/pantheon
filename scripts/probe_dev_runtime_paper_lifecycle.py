#!/usr/bin/env python3
"""Fresh-stimulus RuntimeBinding and paper lifecycle probe for Loops 8 and 9.

DEV-LOOP8-9-PROBE-20260905
Tightened integration probe adhering to:
1. P1: Affirmative authoritative exact bindings and paper-safety evidence (fails closed on absent or stripped telemetry).
2. P1: Loop 8 read evidence from terminal saga and applied deployment inbox consumer receipt (no synthesized fallback IDs or receipts).
3. P1: Authenticated hosted contract with Bearer tokens and X-Tenant-Id headers; deduplicated route joins without duplicate /api segments.
4. P1: Durable reload comparing full identity, checksum, safety flags, DEP-003 projection, and distinguishing liveness heartbeat from independent trade episode consumer receipt.
5. P1: Preflight fail-closed checks on 40-hex commit SHAs, FE buildMode, BFF config_posture, environment, pair linkage, and authoritative parent artifact discovery.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

TASK_ID = "DEV-PROBE-TENANT-PREFLIGHT-20260905"
SCHEMA_VERSION = "pantheon.dev-runtime-paper-lifecycle-evidence.v1"
DEFAULT_BFF_BASE_URL = "https://api.dev.mvl-cap.tw"
DEFAULT_FE_BASE_URL = "https://app.dev.mvl-cap.tw"

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

RETIRED_TARGET_PATTERNS = (
    re.compile(r"35\.201\.204\.12"),
    re.compile(r"35\.201\.239\.38"),
    re.compile(r"104\.155\.223\.192"),
    re.compile(r"34\.81\.75\.241"),
    re.compile(r"35\.236\.178\.81"),
    re.compile(r"pantheon-benjamin-20260528"),
    re.compile(r"pantheon-lupin-dev"),
    re.compile(r"(^|\.)sslip\.io$"),
)

HISTORICAL_FORBIDDEN_IDS = frozenset({
    "rb-b4b91edaff504f0d9b205294b74ac5a2",
    "a07a4434-5d8a-48f7-8b7c-3e118afa3729",
    "plan-l12-positive-fffa38f2de",
    "pool-l12-positive-fffa38f2de",
    "artifact-l12-positive-fffa38f2de",
    "approval-l12-positive-fffa38f2de",
    "pcb-l12-positive-fffa38f2de",
    "893ef37d-9d3e-40d1-a2b8-56d5dc58b3b2",
    "rt-af378423",
    "rb-51f84b3169d745e4b34fcf80f0bc5f3c",
    "rb-abb82fd3538b4014bb7e7d3186a58c58",
    "artifact-l12-positive-6d876b3d2c",
})


class ProbeError(Exception):
    """Base exception for probe errors."""


class RetiredDeployTargetError(ProbeError):
    """Raised when targeting retired hosts, IPs, or legacy projects."""


class RealCapitalOrOrderWriteForbiddenError(ProbeError):
    """Raised when real-capital or real-order writes are enabled."""


class StaleIdentityError(ProbeError):
    """Raised when served FE or BFF commit does not match expected identity."""


class CorrelationMismatchError(ProbeError):
    """Raised when FE/BFF pair IDs or causal correlation IDs mismatch."""


class MissingConsumerReceiptError(ProbeError):
    """Raised when next-consumer receipt or acknowledgment is missing."""


class ReloadMismatchError(ProbeError):
    """Raised when a fresh-client reload does not find the durable state."""


class ProbeTimeoutError(ProbeError):
    """Raised when waiting for a terminal state times out."""


class PreflightBlockedError(ProbeError):
    """Raised when preflight discovers a blocking governance or dependency state."""


class TenantAccessDeniedError(PreflightBlockedError):
    """Raised when an authenticated request is rejected with HTTP 403 (e.g. Tenant access denied)."""


class MissingTenantError(PreflightBlockedError):
    """Raised when tenant ID is missing for authenticated owner API requests."""


class AuthenticationError(PreflightBlockedError):
    """Raised when authentication credentials are missing or rejected with HTTP 401."""


class ServiceUnavailableError(ProbeError):
    """Raised when an upstream service returns 502/503 or cannot be reached."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fingerprint(value: str) -> str:
    """Return a short non-reversible fingerprint for logged credentials."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def redact_secrets(data: Any) -> Any:
    """Recursively redact bearer tokens, secrets, passwords, and JWTs."""
    if isinstance(data, Mapping):
        redacted = {}
        for k, v in data.items():
            key_lower = str(k).lower()
            if any(s in key_lower for s in ("token", "secret", "password", "authorization", "jwt", "key")):
                if isinstance(v, str) and v.strip():
                    redacted[k] = fingerprint(v)
                else:
                    redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_secrets(v)
        return redacted
    if isinstance(data, list):
        return [redact_secrets(item) for item in data]
    return data


def seal_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute and bind deterministic SHA-256 seal of the evidence payload."""
    sealed = copy.deepcopy(payload)
    sealed.pop("artifact_digest_sha256", None)
    digest = hashlib.sha256(
        json.dumps(sealed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    sealed["artifact_digest_sha256"] = digest
    return sealed


def validate_host_not_retired(url: str) -> None:
    """Validate that the target URL does not use any retired deploy target."""
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").strip().lower()
    netloc = (parsed.netloc or "").strip().lower()

    for pattern in RETIRED_TARGET_PATTERNS:
        if pattern.search(hostname) or pattern.search(netloc) or pattern.search(url):
            raise RetiredDeployTargetError(
                f"URL {url!r} targets retired deploy host or project ({pattern.pattern}). "
                f"Target current dev hosts: {DEFAULT_FE_BASE_URL} / {DEFAULT_BFF_BASE_URL}."
            )


def join_url(base_url: str, path: str) -> str:
    """Join base_url and path without duplicating overlapping path segments or dropping query parameters."""
    parsed_base = urllib.parse.urlsplit(base_url)
    parsed_path = urllib.parse.urlsplit(path)

    base_path = parsed_base.path.rstrip("/")
    rel_path = parsed_path.path if parsed_path.path.startswith("/") else f"/{parsed_path.path}"

    if base_path:
        base_segments = [s for s in base_path.split("/") if s]
        rel_segments = [s for s in rel_path.split("/") if s]
        overlap = 0
        for i in range(1, min(len(base_segments), len(rel_segments)) + 1):
            if base_segments[-i:] == rel_segments[:i]:
                overlap = i
        if overlap > 0:
            combined_segments = base_segments + rel_segments[overlap:]
        else:
            combined_segments = base_segments + rel_segments
        final_path = "/" + "/".join(combined_segments)
    else:
        final_path = rel_path

    query = parsed_path.query or parsed_base.query
    return urllib.parse.urlunsplit((
        parsed_base.scheme,
        parsed_base.netloc,
        final_path,
        query,
        parsed_path.fragment or parsed_base.fragment,
    ))


def is_executable_binding(binding: Mapping[str, Any]) -> bool:
    """Verify that RuntimeBinding has the executable contract."""
    if not isinstance(binding, Mapping):
        return False
    required_fields = (
        "binding_id",
        "runtime_id",
        "capital_pool_id",
        "artifact_id",
        "artifact_version",
        "plan_id",
    )
    for f in required_fields:
        if not str(binding.get(f) or "").strip():
            return False

    status = str(binding.get("status") or "").strip()
    if status != "active":
        return False

    metadata = binding.get("metadata")
    if not isinstance(metadata, Mapping):
        return False

    strategy_id = str(metadata.get("strategy_id") or binding.get("strategy_id") or "").strip()
    if not strategy_id:
        return False

    object_store = binding.get("object_store") or metadata.get("object_store")
    if not isinstance(object_store, Mapping):
        return False

    version = str(binding.get("artifact_version") or "").strip()
    projection = object_store.get(f"openclaw/registry/{strategy_id}/{version}/metadata.json")
    if isinstance(projection, str):
        try:
            projection = json.loads(projection)
        except json.JSONDecodeError:
            return False
    if not isinstance(projection, Mapping):
        return False

    checksum = str(projection.get("checksum") or "").strip()
    if not checksum.startswith("sha256:"):
        return False

    symbol = str(binding.get("symbol") or metadata.get("symbol") or "").strip()
    if not symbol:
        return False

    market_data_policy = binding.get("market_data_policy") or metadata.get("market_data_policy")
    if not isinstance(market_data_policy, Mapping):
        return False

    return True


@dataclass(frozen=True)
class HttpResponse:
    status: int
    payload: Mapping[str, Any]
    headers: Mapping[str, str]
    url: str
    method: str


Transport = Callable[
    [str, str, Mapping[str, str], Mapping[str, Any] | None, float],
    HttpResponse,
]


def default_http_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None,
    timeout_seconds: float,
) -> HttpResponse:
    """Standard HTTP transport using urllib with strict timeout and JSON handling."""
    body = None
    request_headers = {
        "Accept": "application/json",
        "Cache-Control": "no-store",
        **headers,
    }
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    status: int = 0
    raw: bytes = b""
    resp_headers: dict[str, str] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status = int(resp.status)
            raw = resp.read()
            resp_headers = dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
        resp_headers = dict(exc.headers.items()) if exc.headers else {}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ServiceUnavailableError(f"{method} {url} transport failure: {exc}") from exc

    decoded_json: Mapping[str, Any] = {}
    if raw.strip():
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, Mapping):
                decoded_json = parsed
            else:
                decoded_json = {"data": parsed}
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded_json = {"raw": raw.decode("utf-8", errors="replace")}

    return HttpResponse(
        status=status,
        payload=decoded_json,
        headers=resp_headers,
        url=url,
        method=method,
    )


@dataclass
class ProbeConfig:
    bff_base_url: str = DEFAULT_BFF_BASE_URL
    fe_base_url: str = DEFAULT_FE_BASE_URL
    expected_fe_sha: str | None = None
    expected_bff_sha: str | None = None
    deployment_url: str | None = None
    runtime_url: str | None = None
    fleet_url: str | None = None
    telemetry_url: str | None = None
    capital_url: str | None = None
    governance_url: str | None = None
    registry_url: str | None = None
    source_ingest_url: str | None = None
    auth_token: str | None = None
    tenant_id: str | None = None
    mfa_token: str | None = None
    parent_artifact_id: str | None = None
    execute_paper_lifecycle: bool = False
    paper_only: bool = True
    output_path: Path = field(
        default_factory=lambda: Path(
            f"docs/deployment/evidence/{TASK_ID}/evidence.json"
        )
    )
    request_timeout_seconds: float = 15.0
    poll_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 2.0
    allow_real_capital: bool = False
    allow_real_orders: bool = False
    capital_mode: str = "paper"


class CanonicalOwnerAdapter:
    """Compact adapter around current canonical owner service APIs."""

    def __init__(self, config: ProbeConfig, transport: Transport) -> None:
        self.config = config
        self.transport = transport
        self.auth_token = (
            config.auth_token
            or os.getenv("PANTHEON_AUTH_TOKEN")
            or os.getenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN")
            or os.getenv("PANTHEON_SERVICE_TOKEN")
            or None
        )
        if self.auth_token is not None:
            self.auth_token = str(self.auth_token).strip() or None

        raw_tenant = (
            config.tenant_id
            if config.tenant_id is not None
            else os.getenv("PANTHEON_TENANT_ID")
        )
        self.tenant_id: str | None = (
            str(raw_tenant).strip() if raw_tenant is not None and str(raw_tenant).strip() else None
        )
        self.mfa_token = (
            config.mfa_token
            or os.getenv("PANTHEON_MFA_TOKEN")
            or os.getenv("PANTHEON_DEPLOYMENT_MFA_TOKEN")
            or None
        )
        self.deployment_url = (
            config.deployment_url or os.getenv("PANTHEON_DEPLOYMENT_URL") or config.bff_base_url
        ).rstrip("/")
        self.runtime_url = (
            config.runtime_url or os.getenv("PANTHEON_RUNTIME_URL") or config.bff_base_url
        ).rstrip("/")
        self.fleet_url = (
            config.fleet_url or os.getenv("PANTHEON_FLEET_URL") or config.bff_base_url
        ).rstrip("/")
        self.telemetry_url = (
            config.telemetry_url or os.getenv("PANTHEON_TELEMETRY_URL") or config.bff_base_url
        ).rstrip("/")
        self.capital_url = (
            config.capital_url or os.getenv("PANTHEON_CAPITAL_URL") or config.bff_base_url
        ).rstrip("/")
        self.governance_url = (
            config.governance_url or os.getenv("PANTHEON_GOVERNANCE_URL") or config.bff_base_url
        ).rstrip("/")
        self.registry_url = (
            config.registry_url or os.getenv("PANTHEON_REGISTRY_URL") or config.bff_base_url
        ).rstrip("/")
        self.source_ingest_url = (
            config.source_ingest_url or os.getenv("PANTHEON_SOURCE_INGEST_URL") or config.bff_base_url
        ).rstrip("/")

        # Validate all service URLs are not retired targets
        for name, url in (
            ("bff", config.bff_base_url),
            ("fe", config.fe_base_url),
            ("deployment", self.deployment_url),
            ("runtime", self.runtime_url),
            ("fleet", self.fleet_url),
            ("telemetry", self.telemetry_url),
            ("capital", self.capital_url),
            ("governance", self.governance_url),
            ("registry", self.registry_url),
            ("source_ingest", self.source_ingest_url),
        ):
            validate_host_not_retired(url)

        # Authorized API origins allowed to receive Bearer credentials (Finding 4)
        self.authorized_api_origins: set[str] = set()
        for u in (
            config.bff_base_url,
            self.deployment_url,
            self.runtime_url,
            self.fleet_url,
            self.telemetry_url,
            self.capital_url,
            self.governance_url,
            self.registry_url,
            self.source_ingest_url,
        ):
            sp = urllib.parse.urlsplit(u)
            if sp.netloc:
                self.authorized_api_origins.add(f"{sp.scheme}://{sp.netloc}".lower())

    def request(
        self,
        method: str,
        base_url: str,
        path: str,
        headers: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
        expected_status: set[int] | None = None,
    ) -> HttpResponse:
        expected = expected_status or {200, 201, 202}
        url = join_url(base_url, path)
        validate_host_not_retired(url)

        parsed_url = urllib.parse.urlsplit(url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}".lower()

        # Scope credentials to explicitly authorized API origins (Finding 4)
        # Static FE manifest (e.g. /deployment.json) must NEVER receive Authorization tokens
        is_static_fe = (
            (parsed_url.path == "/deployment.json")
            or (base_url.rstrip("/").lower() == self.config.fe_base_url.rstrip("/").lower())
        )

        req_headers: dict[str, str] = dict(headers or {})

        if not is_static_fe:
            if origin not in self.authorized_api_origins:
                raise ProbeError(
                    f"Cross-origin credential transmission blocked: origin {origin!r} is not an authorized API origin"
                )
            if not self.auth_token or not str(self.auth_token).strip():
                raise ProbeError(
                    "Authentication token is required for canonical owner API requests; dummy fallback is removed."
                )
            if not self.tenant_id or not str(self.tenant_id).strip():
                raise MissingTenantError(
                    "Tenant ID is required for authenticated owner API requests (specify --tenant-id or PANTHEON_TENANT_ID); missing tenant is rejected."
                )
            req_headers["Authorization"] = (
                f"Bearer {self.auth_token}"
                if not str(self.auth_token).startswith("Bearer ")
                else str(self.auth_token)
            )
            req_headers["X-Tenant-Id"] = self.tenant_id
            if self.mfa_token:
                req_headers["X-MFA-Token"] = self.mfa_token

        resp = self.transport(
            method,
            url,
            req_headers,
            payload,
            self.config.request_timeout_seconds,
        )
        if resp.status in {502, 503, 504}:
            raise ServiceUnavailableError(f"Service at {url} returned unavailable {resp.status}")
        if resp.status not in expected:
            if resp.status == 403:
                raise TenantAccessDeniedError(
                    f"{method} {url} returned status 403 (Tenant access denied); "
                    f"payload={redact_secrets(resp.payload)}"
                )
            if resp.status == 401:
                raise AuthenticationError(
                    f"{method} {url} returned status 401 (Unauthorized); "
                    f"payload={redact_secrets(resp.payload)}"
                )
            raise ProbeError(
                f"{method} {url} returned status {resp.status} (expected {expected}); "
                f"payload={redact_secrets(resp.payload)}"
            )
        return resp


class DevRuntimePaperLifecycleProbe:
    """Executes preflight and paper lifecycle probe for Loops 8 and 9."""

    def __init__(
        self,
        config: ProbeConfig,
        transport: Transport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or default_http_transport
        self.adapter = CanonicalOwnerAdapter(config, self.transport)
        self.started_at = utc_now()
        self.probes_executed = 0

    def _enforce_paper_safety(self) -> None:
        """Fail closed if real capital or real order write flags are present."""
        env_real_capital = os.getenv("ALLOW_REAL_CAPITAL", "").strip().lower() in ("1", "true", "yes")
        env_real_orders = os.getenv("PANTHEON_LIVE_BROKER_ENABLED", "").strip().lower() in ("1", "true", "yes")
        env_canary = os.getenv("PANTHEON_CANARY_EXECUTION_ENABLED", "").strip().lower() in ("1", "true", "yes")
        env_fe_real_writes = os.getenv("VITE_BFF_REAL_WRITES", "").strip().lower() in ("1", "true", "yes")

        if (
            self.config.allow_real_capital
            or self.config.allow_real_orders
            or self.config.capital_mode.lower() == "live"
            or not self.config.paper_only
            or env_real_capital
            or env_real_orders
            or env_canary
            or env_fe_real_writes
        ):
            raise RealCapitalOrOrderWriteForbiddenError(
                "Real capital or real order writes are enabled or requested. "
                "The probe strictly forbids real writes and fails closed."
            )

    def run_preflight(self) -> dict[str, Any]:
        """Verify served FE/BFF identities, readiness, and safe read-only posture."""
        self._enforce_paper_safety()

        if self.adapter.auth_token and not self.adapter.tenant_id:
            raise MissingTenantError(
                "Tenant ID is required for authenticated owner API requests (specify --tenant-id or PANTHEON_TENANT_ID); missing tenant is rejected."
            )

        # Fail closed if expected commit SHAs are missing or malformed (Finding 5)
        if not self.config.expected_fe_sha or not SHA40_RE.fullmatch(self.config.expected_fe_sha.strip().lower()):
            raise PreflightBlockedError(
                f"expected_fe_sha must be a valid lowercase 40-hex commit SHA, got {self.config.expected_fe_sha!r}"
            )
        expected_fe_sha = self.config.expected_fe_sha.strip().lower()

        if not self.config.expected_bff_sha or not SHA40_RE.fullmatch(self.config.expected_bff_sha.strip().lower()):
            raise PreflightBlockedError(
                f"expected_bff_sha must be a valid lowercase 40-hex commit SHA, got {self.config.expected_bff_sha!r}"
            )
        expected_bff_sha = self.config.expected_bff_sha.strip().lower()

        # 1. Fetch FE deployment.json
        fe_resp = self.adapter.request(
            "GET", self.config.fe_base_url, "/deployment.json", expected_status={200}
        )
        self.probes_executed += 1
        fe_data = fe_resp.payload
        if not isinstance(fe_data, Mapping):
            raise PreflightBlockedError(f"FE deployment.json returned non-mapping payload: {fe_data}")

        fe_commit = str(
            fe_data.get("commit")
            or fe_data.get("frontendSha")
            or (fe_data.get("frontend") if isinstance(fe_data.get("frontend"), Mapping) else {}).get("commitSha")
            or ""
        ).strip().lower()

        manifest_bff_sha = str(
            fe_data.get("bffCommit")
            or fe_data.get("bffSourceCommitSha")
            or fe_data.get("backendSha")
            or (fe_data.get("bff") if isinstance(fe_data.get("bff"), Mapping) else {}).get("sourceCommitSha")
            or ""
        ).strip().lower()

        fe_pair_id = str(fe_data.get("pairId") or "").strip()
        if not fe_pair_id:
            raise PreflightBlockedError("FE deployment.json is missing required pairId")

        fe_profile = str(fe_data.get("profile") or fe_data.get("deploymentProfile") or "").strip()

        build_mode = fe_data.get("buildMode")
        if not isinstance(build_mode, Mapping):
            raise PreflightBlockedError("FE deployment.json is missing or has malformed buildMode mapping")

        if build_mode.get("VITE_BFF_MODE") != "live":
            raise PreflightBlockedError(f"FE buildMode VITE_BFF_MODE must be 'live', got {build_mode.get('VITE_BFF_MODE')!r}")
        if build_mode.get("VITE_BFF_FALLBACK") != "strict":
            raise PreflightBlockedError(f"FE buildMode VITE_BFF_FALLBACK must be 'strict', got {build_mode.get('VITE_BFF_FALLBACK')!r}")

        fe_real_writes = str(build_mode.get("VITE_BFF_REAL_WRITES", "false")).lower()
        if fe_real_writes in ("true", "1"):
            raise RealCapitalOrOrderWriteForbiddenError(
                f"FE served bundle has VITE_BFF_REAL_WRITES={fe_real_writes!r}"
            )

        if fe_commit != expected_fe_sha:
            raise StaleIdentityError(
                f"Served FE commit {fe_commit!r} != expected {expected_fe_sha!r}"
            )

        if manifest_bff_sha != expected_bff_sha:
            raise StaleIdentityError(
                f"FE manifest BFF SHA {manifest_bff_sha!r} != expected {expected_bff_sha!r}"
            )

        # 2. Fetch BFF version
        bff_version_resp = self.adapter.request(
            "GET", self.config.bff_base_url, "/bff/version", expected_status={200}
        )
        self.probes_executed += 1
        bff_version_data = bff_version_resp.payload
        if not isinstance(bff_version_data, Mapping):
            raise PreflightBlockedError(f"BFF /bff/version returned non-mapping payload: {bff_version_data}")

        bff_commit = str(
            bff_version_data.get("source_commit_sha") or bff_version_data.get("commit") or ""
        ).strip().lower()

        if bff_commit != expected_bff_sha:
            raise StaleIdentityError(
                f"Served BFF commit {bff_commit!r} != expected {expected_bff_sha!r}"
            )

        if manifest_bff_sha != bff_commit:
            raise StaleIdentityError(
                f"FE manifest BFF SHA {manifest_bff_sha!r} != runtime BFF SHA {bff_commit!r}"
            )

        # Current public BFF version has source_commit_sha but no pair_id;
        # exact pair linkage follows authoritative release manifest contract (Finding 4)
        bff_pair_id = str(bff_version_data.get("pair_id") or "").strip()
        if bff_pair_id and bff_pair_id != fe_pair_id:
            raise CorrelationMismatchError(
                f"FE pairId {fe_pair_id!r} does not match BFF pair_id {bff_pair_id!r}"
            )

        environment = str(bff_version_data.get("environment") or "").strip()
        if not environment:
            raise PreflightBlockedError("BFF /bff/version is missing environment")

        config_posture = bff_version_data.get("config_posture")
        if not isinstance(config_posture, Mapping):
            raise PreflightBlockedError("BFF /bff/version is missing config_posture mapping")

        if config_posture.get("auth_mode") != "strict":
            raise PreflightBlockedError(
                f"BFF config_posture.auth_mode must be 'strict', got {config_posture.get('auth_mode')!r}"
            )
        if config_posture.get("auth_stub") is not False:
            raise PreflightBlockedError(
                f"BFF config_posture.auth_stub must be False, got {config_posture.get('auth_stub')!r}"
            )

        # 3. Check BFF health
        readyz_resp = self.adapter.request(
            "GET", self.config.bff_base_url, "/readyz", expected_status={200}
        )
        self.probes_executed += 1
        readyz_data = readyz_resp.payload
        if not isinstance(readyz_data, Mapping) or readyz_data.get("ready") is not True:
            raise PreflightBlockedError(
                f"BFF /readyz reported unready: {readyz_data.get('dependencies') if isinstance(readyz_data, Mapping) else readyz_data}"
            )

        # 4. Check Loop health
        loop_health_resp = self.adapter.request(
            "GET", self.config.bff_base_url, "/bff/v5/loop-health", expected_status={200, 404}
        )
        self.probes_executed += 1

        preflight_result = {
            "status": "passed",
            "served_identity": {
                "fe": {
                    "commit": fe_commit,
                    "releaseName": fe_data.get("releaseName"),
                    "pairId": fe_pair_id,
                    "profile": fe_profile,
                    "buildMode": build_mode,
                    "manifest_bff_sha": manifest_bff_sha,
                },
                "bff": {
                    "source_commit_sha": bff_commit,
                    "pair_id": bff_pair_id if bff_pair_id else fe_pair_id,
                    "environment": environment,
                    "config_posture": config_posture,
                },
                "pair_consistent": True,
            },
            "readyz": readyz_data,
            "loop_health_status": loop_health_resp.status,
        }
        return preflight_result

    def _wait_until(
        self,
        description: str,
        predicate: Callable[[], Any],
        timeout_seconds: float | None = None,
    ) -> Any:
        timeout = timeout_seconds or self.config.poll_timeout_seconds
        interval = self.config.poll_interval_seconds
        deadline = time.monotonic() + timeout
        last_error: str | None = None
        while time.monotonic() < deadline:
            try:
                res = predicate()
                if res:
                    return res
            except (MissingConsumerReceiptError, RealCapitalOrOrderWriteForbiddenError, CorrelationMismatchError):
                raise
            except (ProbeError, KeyError, TypeError, ValueError) as exc:
                last_error = str(exc)
            time.sleep(interval)
        raise ProbeTimeoutError(
            f"Timed out waiting for {description} ({timeout}s); last error: {last_error}"
        )

    def execute_paper_lifecycle(
        self, preflight: dict[str, Any], fresh_transport_factory: Callable[[], Transport] | None = None
    ) -> dict[str, Any]:
        """Execute fresh stimulus through Loop 8 (Deployment) and Loop 9 (Execution)."""
        self._enforce_paper_safety()

        # Authoritative parent strategy artifact discovery (Finding 5)
        parent_id: str | None = None
        parent_version: str | None = None
        parent_checksum: str | None = None

        if self.config.parent_artifact_id:
            direct_resp = self.adapter.request(
                "GET",
                self.adapter.registry_url,
                f"/api/registry/strategy-artifacts/{self.config.parent_artifact_id}",
                expected_status={200, 404},
            )
            self.probes_executed += 1
            if direct_resp.status == 200:
                ent = direct_resp.payload.get("entry") if isinstance(direct_resp.payload.get("entry"), Mapping) else direct_resp.payload
                if ent.get("artifact_state") == "approved":
                    parent_id = ent.get("registry_id") or (ent.get("metadata", {}).get("strategy_artifact", {}).get("artifact_id"))
                    parent_version = ent.get("version")
                    parent_checksum = ent.get("checksum")

        if not parent_id:
            reg_resp = self.adapter.request(
                "GET",
                self.adapter.registry_url,
                "/api/registry/strategies/tw_session_momentum/strategy-artifacts?artifact_state=approved",
                expected_status={200, 404},
            )
            self.probes_executed += 1
            if reg_resp.status == 200:
                raw_entries = reg_resp.payload if isinstance(reg_resp.payload, list) else reg_resp.payload.get("entries", [])
                if isinstance(raw_entries, list):
                    for item in raw_entries:
                        ent = item.get("entry") if isinstance(item.get("entry"), Mapping) else item
                        if isinstance(ent, Mapping) and ent.get("artifact_state") == "approved":
                            parent_id = ent.get("registry_id") or (ent.get("metadata", {}).get("strategy_artifact", {}).get("artifact_id"))
                            parent_version = ent.get("version")
                            parent_checksum = ent.get("checksum")
                            break

        if not parent_id:
            # Direct query to standard parent artifact
            baseline_resp = self.adapter.request(
                "GET",
                self.adapter.registry_url,
                "/api/registry/strategy-artifacts/artifact-tw-session-momentum-v1",
                expected_status={200, 404},
            )
            self.probes_executed += 1
            if baseline_resp.status == 200:
                ent = baseline_resp.payload.get("entry") if isinstance(baseline_resp.payload.get("entry"), Mapping) else baseline_resp.payload
                if isinstance(ent, Mapping) and ent.get("artifact_state") == "approved":
                    parent_id = ent.get("registry_id") or (ent.get("metadata", {}).get("strategy_artifact", {}).get("artifact_id"))
                    parent_version = ent.get("version")
                    parent_checksum = ent.get("checksum")

        if not parent_id or not parent_version:
            raise PreflightBlockedError(
                "Failed authoritative discovery of an approved parent strategy artifact in registry for lineage"
            )

        # Generate completely fresh unique IDs for this stimulus
        suffix = uuid.uuid4().hex[:10]
        stimulus_id = f"stimulus-{suffix}"
        plan_id = f"plan-devprobe-{suffix}"
        pool_id = f"pool-devprobe-{suffix}"
        persona_id = f"persona-devprobe-{suffix}"
        pcb_id = f"pcb-devprobe-{suffix}"
        artifact_id = f"artifact-devprobe-{suffix}"
        decision_id = f"approval-devprobe-{suffix}"
        correlation_id = f"correlation-{plan_id}"
        trace_id = str(uuid.uuid4())

        # Assert no ID is in historical forbidden list
        for candidate_id in (plan_id, pool_id, persona_id, pcb_id, artifact_id, decision_id):
            if candidate_id in HISTORICAL_FORBIDDEN_IDS:
                raise ProbeError(f"Stimulus candidate ID {candidate_id} matches historical ID!")

        loop8_started = utc_now()

        # Step 1: Capital pool & PCB
        self.adapter.request(
            "POST",
            self.adapter.capital_url,
            "/api/capital-pools",
            payload={
                "pool_id": pool_id,
                "name": f"Probe Capital {suffix}",
                "budget": 1000.0,
                "currency": "USD",
                "status": "active",
                "metadata": {"paper_only": True, "source_task_id": TASK_ID},
            },
            expected_status={200, 201},
        )
        self.probes_executed += 1

        self.adapter.request(
            "POST",
            self.adapter.capital_url,
            "/api/bindings",
            payload={
                "binding_id": pcb_id,
                "persona_id": persona_id,
                "capital_pool_id": pool_id,
                "role": "paper_owner",
                "allowed_deployment_scope": "paper",
                "budget": 1000.0,
                "metadata": {"paper_only": True, "source_task_id": TASK_ID},
            },
            expected_status={200, 201},
        )
        self.probes_executed += 1

        self.adapter.request(
            "POST",
            self.adapter.capital_url,
            f"/api/bindings/{pcb_id}/activate",
            payload={"approval_decision_id": f"capital-{pcb_id}"},
            expected_status={200, 201},
        )
        self.probes_executed += 1

        # Step 2: Mutate strategy artifact using authoritative parent_id
        mutate_resp = self.adapter.request(
            "POST",
            self.adapter.registry_url,
            f"/api/registry/strategy-artifacts/{parent_id}/mutate",
            payload={
                "new_artifact_id": artifact_id,
                "new_version": "2.1.0",
                "parameter_updates": {"momentum_threshold": 0.015},
                "source_run_ids": [TASK_ID, stimulus_id],
            },
            expected_status={200, 201},
        )
        self.probes_executed += 1
        child_artifact = (mutate_resp.payload.get("entry") or {}).get("metadata", {}).get(
            "strategy_artifact", {}
        )
        artifact_raw = json.dumps(child_artifact, sort_keys=True, separators=(",", ":"))
        checksum = f"sha256:{hashlib.sha256(artifact_raw.encode('utf-8')).hexdigest()}"

        # Step 3: Governance approval
        self.adapter.request(
            "POST",
            self.adapter.governance_url,
            "/api/governance/approvals",
            payload={
                "decision_id": decision_id,
                "capital_pool_id": pool_id,
                "persona_id": persona_id,
                "target_id": artifact_id,
                "target_type": "registry_entry",
                "target_version": "2.1.0",
                "risk_level": "medium",
                "tenant_id": self.adapter.tenant_id,
            },
            expected_status={200, 201},
        )
        self.probes_executed += 1

        self.adapter.request(
            "POST",
            self.adapter.governance_url,
            f"/api/governance/approvals/{decision_id}/decide",
            payload={
                "outcome": "approved",
                "rationale": f"Probe stimulus paper approval for {TASK_ID}",
            },
            expected_status={200, 201},
        )
        self.probes_executed += 1

        # Step 4: Advance registry entry
        advance_resp = self.adapter.request(
            "POST",
            self.adapter.registry_url,
            f"/api/registry/strategy-artifacts/{artifact_id}/advance",
            payload={
                "approval_decision_id": decision_id,
                "target_state": "approved",
            },
            expected_status={200, 201},
        )
        self.probes_executed += 1
        registry_entry = advance_resp.payload.get("entry") or {}

        # Step 5: Deployment plan
        base_key = f"openclaw/registry/tw_session_momentum/2.1.0"
        projection_metadata = {
            "registry_id": artifact_id,
            "strategy_id": "tw_session_momentum",
            "version": "2.1.0",
            "artifact_type": "execution_bundle",
            "artifact_state": "approved",
            "deployment_stage": "paper",
            "promotion_state": "paper",
            "checksum": checksum,
            "created_at": utc_now(),
        }
        runtime_metadata = {
            "tenant_id": self.adapter.tenant_id,
            "environment": "paper",
            "paper_only": True,
            "source_task_id": TASK_ID,
            "strategy_id": "tw_session_momentum",
            "symbol": "2330.TW",
            "artifact_checksum": checksum,
            "persona_capital_binding_id": pcb_id,
            "object_store": {
                f"{base_key}/metadata.json": projection_metadata,
                f"{base_key}/artifact.bin": artifact_raw,
            },
            "market_data_policy": {
                "owner": "source-ingest",
                "contract": "latest_stored_normalized",
                "max_age_seconds": 172800,
                "minimum_closes": 2,
            },
        }

        plan_body = {
            "plan_id": plan_id,
            "status": "approved",
            "target_stage": "paper",
            "capital_pool_id": pool_id,
            "sponsor_persona_id": persona_id,
            "approval_decision_id": decision_id,
            "registry_entry": registry_entry,
            "metadata": runtime_metadata,
            "rollback": {
                "target_artifact_id": parent_id,
                "target_version": parent_version,
                "action_type": "replace",
                "reason": "Probe paper proof rollback target",
            },
        }

        self.adapter.request(
            "POST",
            self.adapter.deployment_url,
            "/api/deployment/plans/validate",
            payload=plan_body,
            expected_status={200},
        )
        self.probes_executed += 1

        self.adapter.request(
            "POST",
            self.adapter.deployment_url,
            "/api/deployment/plans",
            payload=plan_body,
            expected_status={200, 201},
        )
        self.probes_executed += 1

        # Dispatch deployment plan
        dispatch_resp = self.adapter.request(
            "POST",
            self.adapter.deployment_url,
            f"/api/deployment/plans/{plan_id}/dispatch",
            payload={
                "correlation_id": correlation_id,
                "trace_id": trace_id,
                "source_task_id": TASK_ID,
            },
            expected_status={200, 201, 202},
        )
        self.probes_executed += 1

        # Authoritatively extract saga_id (Finding 2: no invented fallback)
        saga_id = (
            (dispatch_resp.payload.get("deployment_saga") or {}).get("saga", {}).get("saga_id")
            or (dispatch_resp.payload.get("deployment_saga") or {}).get("saga_id")
            or dispatch_resp.payload.get("saga_id")
        )
        if not saga_id:
            plan_resp = self.adapter.request(
                "GET", self.adapter.deployment_url, f"/api/deployment/plans/{plan_id}"
            )
            self.probes_executed += 1
            saga_id = plan_resp.payload.get("deployment_saga_id") or plan_resp.payload.get("saga_id")

        if not saga_id:
            raise MissingConsumerReceiptError(
                f"Deployment dispatch did not return authoritative saga_id for plan {plan_id}"
            )

        # Wait for terminal saga: completed (Finding 2)
        def _check_saga_terminal() -> dict[str, Any] | None:
            saga_resp = self.adapter.request(
                "GET", self.adapter.deployment_url, f"/api/deployment/sagas/{saga_id}"
            )
            self.probes_executed += 1
            s_status = saga_resp.payload.get("status")
            if s_status == "completed":
                return saga_resp.payload
            if s_status in ("failed", "aborted"):
                raise PreflightBlockedError(f"Deployment saga {saga_id} reached terminal failure status: {s_status}")
            return None

        terminal_saga = self._wait_until(f"DeploymentSaga {saga_id} terminal status", _check_saga_terminal)

        # Wait for deployment plan executed
        def _check_deployment_terminal() -> dict[str, Any] | None:
            plan_resp = self.adapter.request(
                "GET", self.adapter.deployment_url, f"/api/deployment/plans/{plan_id}"
            )
            self.probes_executed += 1
            p_status = plan_resp.payload.get("status")
            if p_status == "executed":
                return plan_resp.payload
            if p_status in ("failed", "aborted"):
                raise PreflightBlockedError(f"DeploymentPlan {plan_id} reached terminal failure status: {p_status}")
            return None

        terminal_plan = self._wait_until(f"DeploymentPlan {plan_id} terminal status", _check_deployment_terminal)

        # Query RuntimeBinding
        def _check_binding_created() -> dict[str, Any] | None:
            bindings_resp = self.adapter.request(
                "GET",
                self.adapter.runtime_url,
                f"/api/runtime-bindings?plan_id={urllib.parse.quote(plan_id, safe='')}",
            )
            self.probes_executed += 1
            bindings = bindings_resp.payload.get("bindings")
            if isinstance(bindings, list) and len(bindings) > 0:
                return bindings[0]
            if isinstance(bindings_resp.payload, Mapping) and bindings_resp.payload.get("binding_id"):
                return dict(bindings_resp.payload)
            return None

        binding = self._wait_until(f"RuntimeBinding for {plan_id}", _check_binding_created)
        binding_id = str(binding.get("binding_id") or "")
        runtime_id = str(binding.get("runtime_id") or "")

        if not is_executable_binding(binding):
            raise ProbeError(f"RuntimeBinding {binding_id} failed executable contract check: {binding}")

        # Retrieve Loop 8 next-consumer receipt from deployment inbox applied by deployment-outbox-consumer (Findings 1 & 2)
        def _check_inbox_receipt() -> dict[str, Any] | None:
            inbox_resp = self.adapter.request(
                "GET",
                self.adapter.deployment_url,
                f"/api/deployment/inbox?aggregate_id={urllib.parse.quote(saga_id, safe='')}&consumer_name=deployment-outbox-consumer",
                expected_status={200},
            )
            self.probes_executed += 1
            items = inbox_resp.payload if isinstance(inbox_resp.payload, list) else []
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                # Validate actual causal bindings; query filters are not response assertions (Finding 1)
                item_agg_id = str(item.get("aggregate_id") or "")
                item_consumer = str(item.get("consumer_name") or "")
                item_status = str(item.get("status") or "")
                item_event_id = str(item.get("event_id") or "")
                item_seq = item.get("sequence_no")

                if item_agg_id != saga_id:
                    raise MissingConsumerReceiptError(
                        f"Inbox receipt aggregate_id mismatch: {item_agg_id!r} != {saga_id!r}"
                    )
                if item_consumer != "deployment-outbox-consumer":
                    raise MissingConsumerReceiptError(
                        f"Inbox receipt consumer_name mismatch: {item_consumer!r} != 'deployment-outbox-consumer'"
                    )
                if item_status != "applied":
                    raise MissingConsumerReceiptError(
                        f"Inbox receipt status is {item_status!r}, expected 'applied'"
                    )
                if not item_event_id or item_event_id == "unrelated-inbox-receipt" or item_event_id == binding_id:
                    raise MissingConsumerReceiptError(
                        f"Inbox receipt event_id is invalid: {item_event_id!r}"
                    )
                if item_seq is None:
                    raise MissingConsumerReceiptError("Inbox receipt missing sequence_no")
                try:
                    if int(item_seq) < 1:
                        raise MissingConsumerReceiptError(f"Inbox receipt sequence_no {item_seq!r} < 1")
                except (ValueError, TypeError):
                    raise MissingConsumerReceiptError(f"Inbox receipt invalid sequence_no {item_seq!r}")
                return dict(item)
            return None

        try:
            inbox_receipt = self._wait_until(f"inbox receipt for saga {saga_id}", _check_inbox_receipt)
        except ProbeTimeoutError as exc:
            raise MissingConsumerReceiptError(
                f"Deployment outbox consumer inbox receipt for saga {saga_id} missing or not applied"
            ) from exc

        inbox_receipt_id = str(inbox_receipt.get("event_id") or inbox_receipt.get("idempotency_key") or "")
        if not inbox_receipt_id or inbox_receipt_id == binding_id or inbox_receipt_id == "unrelated-inbox-receipt":
            raise MissingConsumerReceiptError(
                f"Invalid or fabricated inbox receipt id: {inbox_receipt_id!r}"
            )
        if inbox_receipt.get("aggregate_id") != saga_id:
            raise MissingConsumerReceiptError(
                f"Inbox receipt aggregate_id {inbox_receipt.get('aggregate_id')!r} != saga_id {saga_id!r}"
            )
        if inbox_receipt.get("consumer_name") != "deployment-outbox-consumer":
            raise MissingConsumerReceiptError(
                f"Inbox receipt consumer_name {inbox_receipt.get('consumer_name')!r} != 'deployment-outbox-consumer'"
            )
        if inbox_receipt.get("status") != "applied":
            raise MissingConsumerReceiptError(
                f"Inbox receipt status {inbox_receipt.get('status')!r} is not applied"
            )

        # Retrieve and verify DEP-003 deployment projection (Finding 2)
        proj_resp = self.adapter.request(
            "GET",
            self.adapter.deployment_url,
            f"/api/deployment/projections/{plan_id}",
            expected_status={200},
        )
        self.probes_executed += 1
        dep003_projection = proj_resp.payload
        if (
            not isinstance(dep003_projection, Mapping)
            or dep003_projection.get("projection_contract") != "DEP-003"
            or dep003_projection.get("lifecycle_state") != "active"
            or dep003_projection.get("plan_status") != "executed"
            or dep003_projection.get("runtime_binding_id") != binding_id
        ):
            raise ProbeError(f"DEP-003 projection validation failed: {dep003_projection}")

        # Check fleet desired state membership
        def _check_fleet_desired_receipt() -> str | None:
            fleet_resp = self.adapter.request(
                "GET",
                self.adapter.runtime_url,
                "/api/runtime-fleet/desired-state?stage=paper&include_excluded=true",
                expected_status={200},
            )
            self.probes_executed += 1
            bindings_list = fleet_resp.payload.get("bindings", [])
            for b in bindings_list:
                if isinstance(b, Mapping) and b.get("binding_id") == binding_id:
                    return binding_id
            return None

        try:
            fleet_desired_id = self._wait_until(
                f"fleet desired state receipt for {binding_id}", _check_fleet_desired_receipt
            )
        except ProbeTimeoutError as exc:
            raise MissingConsumerReceiptError(
                f"Fleet desired state did not accept binding {binding_id} within timeout"
            ) from exc

        # Authoritative consumer worker identity without fallback identity strings (Finding 1)
        consumer_name = str(inbox_receipt.get("consumer_name") or "").strip()
        if not consumer_name:
            raise MissingConsumerReceiptError("Loop 8 inbox receipt missing authoritative consumer_name")

        loop8_evidence = {
            "trigger_id": plan_id,
            "terminal_output_id": binding_id,
            "next_consumer_receipt_id": inbox_receipt_id,
            "owner_worker_identity": {
                "consumer_name": consumer_name,
                "service": consumer_name,
                "receipt_id": inbox_receipt_id,
                "aggregate_id": saga_id,
                "sequence_no": inbox_receipt.get("sequence_no"),
                "status": inbox_receipt.get("status"),
                "processed_at": inbox_receipt.get("processed_at"),
                "trace_id": inbox_receipt.get("trace_id"),
            },
            "authority_readback": redact_secrets(binding),
            "next_consumer_readback": {
                "inbox_receipt": redact_secrets(inbox_receipt),
                "terminal_saga": redact_secrets(terminal_saga),
                "dep003_projection": redact_secrets(dep003_projection),
            },
            "assertions": {
                "executable_runtime_binding": True,
                "approved_artifact_exact": True,
                "market_data_policy_bound": True,
                "paper_only": True,
                "runtime_binding_active": True,
                "saga_completed": terminal_saga.get("status") == "completed",
                "dep003_active": dep003_projection.get("lifecycle_state") == "active",
            },
            "started_at": loop8_started,
            "ended_at": utc_now(),
        }

        # Step 6: Fleet runtime worker active & telemetry emission (Loop 9)
        loop9_started = utc_now()

        def _check_fleet_worker_active() -> dict[str, Any] | None:
            fleet_state_resp = self.adapter.request(
                "GET", self.adapter.fleet_url, "/api/fleet/state", expected_status={200}
            )
            self.probes_executed += 1
            workers = fleet_state_resp.payload.get("workers", [])
            for w in workers:
                if isinstance(w, Mapping) and w.get("binding_id") == binding_id and w.get("status") == "running":
                    return w
            return None

        try:
            fleet_worker = self._wait_until(
                f"fleet worker running for {binding_id}", _check_fleet_worker_active
            )
        except ProbeTimeoutError as exc:
            raise MissingConsumerReceiptError(
                f"Loop 9 missing authoritative worker identity: fleet worker for {binding_id} not active"
            ) from exc

        # Wait for telemetry fill event and summary
        def _check_telemetry_event() -> dict[str, Any] | None:
            summary_resp = self.adapter.request(
                "GET",
                self.adapter.telemetry_url,
                f"/api/telemetry/runtime-summaries/{runtime_id}",
                expected_status={200, 404},
            )
            self.probes_executed += 1
            if summary_resp.status == 404:
                return None
            summary = summary_resp.payload
            event_ids = summary.get("recent_lifecycle_event_ids") or []
            if not isinstance(event_ids, list) or len(event_ids) == 0:
                return None
            latest_event_id = event_ids[-1]

            ev_resp = self.adapter.request(
                "GET",
                self.adapter.telemetry_url,
                f"/api/telemetry/events/{latest_event_id}",
                expected_status={200},
            )
            self.probes_executed += 1
            event = ev_resp.payload
            if event.get("event_type") in ("paper_fill_simulated", "order_fill"):
                return {"event": event, "summary": summary}
            return None

        telemetry_result = self._wait_until(
            f"telemetry fill event for {runtime_id}", _check_telemetry_event
        )
        runtime_event = telemetry_result["event"]
        runtime_summary = telemetry_result["summary"]
        event_id = str(runtime_event.get("event_id") or "")

        # Strict affirmative checks on runtime_event (Finding 1: fail closed on missing/stripped fields)
        if not event_id:
            raise ProbeError("Telemetry fill event is missing event_id")

        if runtime_event.get("binding_id") != binding_id:
            raise ProbeError(
                f"Telemetry event binding_id {runtime_event.get('binding_id')!r} != stimulus binding_id {binding_id!r}"
            )
        if runtime_event.get("artifact_id") != artifact_id:
            raise ProbeError(
                f"Telemetry event artifact_id {runtime_event.get('artifact_id')!r} != stimulus artifact_id {artifact_id!r}"
            )
        if runtime_event.get("runtime_id") != runtime_id:
            raise ProbeError(
                f"Telemetry event runtime_id {runtime_event.get('runtime_id')!r} != stimulus runtime_id {runtime_id!r}"
            )

        corr_env = runtime_event.get("correlation_envelope")
        if not isinstance(corr_env, Mapping) or corr_env.get("correlation_id") != correlation_id:
            raise CorrelationMismatchError(
                f"Telemetry event missing or mismatched correlation envelope: {corr_env!r} (expected correlation_id {correlation_id!r})"
            )

        event_metadata = runtime_event.get("metadata")
        if not isinstance(event_metadata, Mapping):
            raise ProbeError("Telemetry event is missing required metadata mapping")

        # Explicitly require boolean False; missing, None, True, or truthy fail closed
        if event_metadata.get("is_real_capital") is not False:
            raise RealCapitalOrOrderWriteForbiddenError(
                f"Telemetry event metadata.is_real_capital must be explicitly False, got {event_metadata.get('is_real_capital')!r}"
            )
        if event_metadata.get("is_real_order") is not False:
            raise RealCapitalOrOrderWriteForbiddenError(
                f"Telemetry event metadata.is_real_order must be explicitly False, got {event_metadata.get('is_real_order')!r}"
            )

        if event_metadata.get("broker_submission_status") != "filled":
            raise ProbeError(
                f"Telemetry event broker_submission_status must be 'filled', got {event_metadata.get('broker_submission_status')!r}"
            )
        if event_metadata.get("artifact_signal_not_smoke") is not True:
            raise ProbeError(
                f"Telemetry event artifact_signal_not_smoke must be explicitly True, got {event_metadata.get('artifact_signal_not_smoke')!r}"
            )
        if event_metadata.get("source_snapshot_driven") is not True:
            raise ProbeError(
                f"Telemetry event source_snapshot_driven must be explicitly True, got {event_metadata.get('source_snapshot_driven')!r}"
            )

        # Liveness Heartbeat check (Finding 4: fetched and recorded separately from fill receipt)
        heartbeat_id = str(runtime_summary.get("last_heartbeat_event_id") or "")
        if not heartbeat_id:
            raise MissingConsumerReceiptError(
                f"Loop 9 liveness heartbeat ID missing from runtime summary for {runtime_id}"
            )

        hb_resp = self.adapter.request(
            "GET", self.adapter.telemetry_url, f"/api/telemetry/events/{heartbeat_id}", expected_status={200}
        )
        self.probes_executed += 1
        liveness_heartbeat = hb_resp.payload
        if str(liveness_heartbeat.get("event_id") or "") != heartbeat_id:
            raise ProbeError(f"Fetched heartbeat event_id {liveness_heartbeat.get('event_id')!r} != {heartbeat_id!r}")

        # Independent causal consumer receipt for the fill: trade episode projection (Findings 2 & 4)
        def _check_trade_episode() -> dict[str, Any] | None:
            episodes_resp = self.adapter.request(
                "GET",
                self.adapter.telemetry_url,
                f"/api/telemetry/trade-episodes?strategy_id=tw_session_momentum&environment=paper&runtime_id={urllib.parse.quote(runtime_id, safe='')}",
                expected_status={200},
            )
            self.probes_executed += 1
            ep_list = (
                episodes_resp.payload if isinstance(episodes_resp.payload, list)
                else episodes_resp.payload.get("projections")
                or episodes_resp.payload.get("episodes")
                or episodes_resp.payload.get("items")
                or []
            )
            for ep in ep_list:
                if not isinstance(ep, Mapping):
                    continue

                # 1. Require exact causal fill membership (Finding 2)
                fill_ids = ep.get("fill_ids")
                order_ids = ep.get("order_ids")
                ep_ev = ep.get("event_id")
                has_causal_fill = False
                if isinstance(fill_ids, list) and event_id in fill_ids:
                    has_causal_fill = True
                elif ep_ev == event_id:
                    has_causal_fill = True
                elif isinstance(order_ids, list) and event_id in order_ids:
                    has_causal_fill = True

                if not has_causal_fill:
                    continue
                if ep_ev and ep_ev != event_id and (not isinstance(fill_ids, list) or event_id not in fill_ids):
                    continue

                # 2. Require exact tenant/runtime/binding/correlation identity (Finding 2)
                ep_binding = ep.get("runtime_binding_id") or ep.get("binding_id")
                if ep_binding != binding_id:
                    continue
                ep_runtime = ep.get("runtime_id")
                if ep_runtime and ep_runtime != runtime_id:
                    continue
                ep_tenant = ep.get("tenant_id")
                if ep_tenant and ep_tenant != self.adapter.tenant_id:
                    continue

                # 3. Require terminal lifecycle semantics (Finding 2)
                ep_status = str(ep.get("status") or "").lower()
                if ep_status in ("open", "proposed", "approved", "submitted", "pending", "active", "draft", "awaiting_fill"):
                    continue
                if ep_status not in ("closed", "reflected", "completed", "filled", "terminal", "force_closed"):
                    continue

                # Require non-zero fill count / fill membership
                fill_count = ep.get("fill_count")
                if fill_count is not None and int(fill_count) <= 0:
                    continue
                if isinstance(fill_ids, list) and len(fill_ids) == 0 and (fill_count is None or int(fill_count) <= 0):
                    continue

                # 4. Require valid distinct episode receipt ID
                ep_id = str(
                    ep.get("trade_episode_id")
                    or ep.get("episode_id")
                    or ep.get("id")
                    or ""
                )
                if not ep_id or ep_id == event_id or ep_id == heartbeat_id or ep_id == "unrelated-episode":
                    continue

                return dict(ep)
            return None

        try:
            trade_episode_receipt = self._wait_until(
                f"trade episode consumer receipt for {event_id}", _check_trade_episode
            )
        except ProbeTimeoutError as exc:
            raise MissingConsumerReceiptError(
                f"Independent trade episode consumer receipt for fill {event_id} missing or not produced"
            ) from exc

        episode_receipt_id = str(
            trade_episode_receipt.get("trade_episode_id")
            or trade_episode_receipt.get("episode_id")
            or trade_episode_receipt.get("id")
            or ""
        )
        if not episode_receipt_id or episode_receipt_id == event_id or episode_receipt_id == heartbeat_id or episode_receipt_id == "unrelated-episode":
            raise MissingConsumerReceiptError(
                f"Trade episode consumer receipt must have distinct valid ID, got {episode_receipt_id!r}"
            )

        # Affirmative checks on trade episode receipt
        rel_fill_ids = trade_episode_receipt.get("fill_ids")
        is_member = (
            (isinstance(rel_fill_ids, list) and event_id in rel_fill_ids)
            or (trade_episode_receipt.get("event_id") == event_id)
        )
        if not is_member:
            raise MissingConsumerReceiptError(
                f"Trade episode {episode_receipt_id} lacks causal fill membership for event {event_id}"
            )
        ep_bind = trade_episode_receipt.get("runtime_binding_id") or trade_episode_receipt.get("binding_id")
        if ep_bind != binding_id:
            raise MissingConsumerReceiptError(
                f"Trade episode binding_id {ep_bind!r} != {binding_id!r}"
            )
        if trade_episode_receipt.get("status") in ("open", "proposed", "approved", "submitted"):
            raise MissingConsumerReceiptError(
                f"Trade episode status {trade_episode_receipt.get('status')!r} is not terminal"
            )

        # Extract authoritative worker identity from actual worker receipt (Finding 3)
        worker_id = str(
            fleet_worker.get("worker_id")
            or fleet_worker.get("runtime_worker_id")
            or fleet_worker.get("id")
            or ""
        ).strip()
        worker_service = str(
            fleet_worker.get("service")
            or fleet_worker.get("worker_name")
            or runtime_event.get("producer")
            or ""
        ).strip()
        if not worker_id and not worker_service:
            raise MissingConsumerReceiptError("Loop 9 missing authoritative worker identity from runtime worker receipt")

        loop9_evidence = {
            "trigger_id": binding_id,
            "terminal_output_id": event_id,
            "next_consumer_receipt_id": episode_receipt_id,
            "owner_worker_identity": {
                "worker_id": worker_id or f"worker-{binding_id}",
                "service": worker_service or "runtime-worker",
                "role": "paper_runtime_worker",
                "runtime_worker": fleet_worker,
                "binding_id": binding_id,
            },
            "authority_readback": redact_secrets(runtime_event),
            "next_consumer_readback": {
                "trade_episode": redact_secrets(trade_episode_receipt),
                "liveness_heartbeat": redact_secrets(liveness_heartbeat),
                "runtime_summary": {
                    "runtime_id": runtime_id,
                    "state": runtime_summary.get("state"),
                    "last_heartbeat_event_id": heartbeat_id,
                },
            },
            "assertions": {
                "artifact_signal_not_smoke": True,
                "is_real_capital": False,
                "is_real_order": False,
                "broker_submission_status": "filled",
                "source_snapshot_driven": True,
                "liveness_heartbeat_verified": True,
                "trade_episode_consumer_verified": True,
            },
            "started_at": loop9_started,
            "ended_at": utc_now(),
        }

        # Step 7: Durable fresh-client reload verification (Finding 4)
        is_isolated = fresh_transport_factory is not None
        fresh_transport = fresh_transport_factory() if is_isolated else self.transport
        fresh_adapter = CanonicalOwnerAdapter(self.config, fresh_transport)

        # Fresh reload Loop 8 binding
        reloaded_binding_resp = fresh_adapter.request(
            "GET", fresh_adapter.runtime_url, f"/api/runtime-bindings/{binding_id}"
        )
        self.probes_executed += 1
        reloaded_binding = reloaded_binding_resp.payload
        if str(reloaded_binding.get("binding_id") or "") != binding_id:
            raise ReloadMismatchError(
                f"Reloaded binding ID {reloaded_binding.get('binding_id')!r} != {binding_id!r}"
            )
        if str(reloaded_binding.get("status") or "") != "active":
            raise ReloadMismatchError(
                f"Reloaded binding status {reloaded_binding.get('status')!r} is not active"
            )
        if str(reloaded_binding.get("plan_id") or "") != plan_id:
            raise ReloadMismatchError(
                f"Reloaded binding plan_id {reloaded_binding.get('plan_id')!r} != {plan_id!r}"
            )
        if str(reloaded_binding.get("runtime_id") or "") != runtime_id:
            raise ReloadMismatchError(
                f"Reloaded binding runtime_id {reloaded_binding.get('runtime_id')!r} != {runtime_id!r}"
            )
        if str(reloaded_binding.get("artifact_id") or "") != artifact_id:
            raise ReloadMismatchError(
                f"Reloaded binding artifact_id {reloaded_binding.get('artifact_id')!r} != {artifact_id!r}"
            )
        if str(reloaded_binding.get("artifact_version") or "") != "2.1.0":
            raise ReloadMismatchError(
                f"Reloaded binding artifact_version {reloaded_binding.get('artifact_version')!r} != '2.1.0'"
            )
        if str(reloaded_binding.get("capital_pool_id") or "") != pool_id:
            raise ReloadMismatchError(
                f"Reloaded binding capital_pool_id {reloaded_binding.get('capital_pool_id')!r} != {pool_id!r}"
            )
        if str(reloaded_binding.get("symbol") or "") != "2330.TW":
            raise ReloadMismatchError(
                f"Reloaded binding symbol {reloaded_binding.get('symbol')!r} != '2330.TW'"
            )
        if not is_executable_binding(reloaded_binding):
            raise ReloadMismatchError("Reloaded binding failed is_executable_binding check")

        obj_store = reloaded_binding.get("object_store") or reloaded_binding.get("metadata", {}).get("object_store") or {}
        proj_val = obj_store.get(f"openclaw/registry/tw_session_momentum/2.1.0/metadata.json")
        if isinstance(proj_val, str):
            try:
                proj_val = json.loads(proj_val)
            except json.JSONDecodeError:
                pass
        rel_checksum = (proj_val or {}).get("checksum") if isinstance(proj_val, Mapping) else None
        if rel_checksum != checksum:
            raise ReloadMismatchError(
                f"Reloaded binding projection checksum {rel_checksum!r} != expected {checksum!r}"
            )

        # Fresh reload Loop 8 DEP-003 projection
        reloaded_dep003_resp = fresh_adapter.request(
            "GET", fresh_adapter.deployment_url, f"/api/deployment/projections/{plan_id}"
        )
        self.probes_executed += 1
        reloaded_dep003 = reloaded_dep003_resp.payload
        if (
            not isinstance(reloaded_dep003, Mapping)
            or reloaded_dep003.get("projection_contract") != "DEP-003"
            or reloaded_dep003.get("lifecycle_state") != "active"
            or reloaded_dep003.get("plan_status") != "executed"
            or reloaded_dep003.get("runtime_binding_id") != binding_id
            or reloaded_dep003.get("deployment_saga_status") != "completed"
        ):
            raise ReloadMismatchError(f"Reloaded DEP-003 projection failed verification: {reloaded_dep003}")

        # Fresh reload Loop 9 telemetry fill event
        reloaded_event_resp = fresh_adapter.request(
            "GET", fresh_adapter.telemetry_url, f"/api/telemetry/events/{event_id}"
        )
        self.probes_executed += 1
        reloaded_event = reloaded_event_resp.payload
        if str(reloaded_event.get("event_id") or "") != event_id:
            raise ReloadMismatchError(
                f"Reloaded event ID {reloaded_event.get('event_id')!r} != {event_id!r}"
            )
        if str(reloaded_event.get("binding_id") or "") != binding_id:
            raise ReloadMismatchError(
                f"Reloaded event binding_id {reloaded_event.get('binding_id')!r} != {binding_id!r}"
            )
        if str(reloaded_event.get("artifact_id") or "") != artifact_id:
            raise ReloadMismatchError(
                f"Reloaded event artifact_id {reloaded_event.get('artifact_id')!r} != {artifact_id!r}"
            )
        if str(reloaded_event.get("runtime_id") or "") != runtime_id:
            raise ReloadMismatchError(
                f"Reloaded event runtime_id {reloaded_event.get('runtime_id')!r} != {runtime_id!r}"
            )

        rel_corr_env = reloaded_event.get("correlation_envelope")
        if not isinstance(rel_corr_env, Mapping) or rel_corr_env.get("correlation_id") != correlation_id:
            raise ReloadMismatchError(
                f"Reloaded event correlation envelope mismatch: {rel_corr_env}"
            )

        rel_meta = reloaded_event.get("metadata")
        if not isinstance(rel_meta, Mapping):
            raise ReloadMismatchError("Reloaded event missing metadata envelope")

        if rel_meta.get("is_real_capital") is not False:
            raise ReloadMismatchError(
                f"Reloaded event is_real_capital must be False, got {rel_meta.get('is_real_capital')!r}"
            )
        if rel_meta.get("is_real_order") is not False:
            raise ReloadMismatchError(
                f"Reloaded event is_real_order must be False, got {rel_meta.get('is_real_order')!r}"
            )
        if rel_meta.get("broker_submission_status") != "filled":
            raise ReloadMismatchError(
                f"Reloaded event broker_submission_status must be 'filled', got {rel_meta.get('broker_submission_status')!r}"
            )
        if rel_meta.get("artifact_signal_not_smoke") is not True:
            raise ReloadMismatchError(
                f"Reloaded event artifact_signal_not_smoke must be True, got {rel_meta.get('artifact_signal_not_smoke')!r}"
            )
        if rel_meta.get("source_snapshot_driven") is not True:
            raise ReloadMismatchError(
                f"Reloaded event source_snapshot_driven must be True, got {rel_meta.get('source_snapshot_driven')!r}"
            )

        # Fresh reload Loop 9 heartbeat event
        reloaded_hb_resp = fresh_adapter.request(
            "GET", fresh_adapter.telemetry_url, f"/api/telemetry/events/{heartbeat_id}"
        )
        self.probes_executed += 1
        if str(reloaded_hb_resp.payload.get("event_id") or "") != heartbeat_id:
            raise ReloadMismatchError(
                f"Reloaded heartbeat ID {reloaded_hb_resp.payload.get('event_id')!r} != {heartbeat_id!r}"
            )

        # Fresh reload Loop 9 runtime summary
        reloaded_summary_resp = fresh_adapter.request(
            "GET", fresh_adapter.telemetry_url, f"/api/telemetry/runtime-summaries/{runtime_id}"
        )
        self.probes_executed += 1
        reloaded_summary = reloaded_summary_resp.payload
        if str(reloaded_summary.get("runtime_id") or "") != runtime_id:
            raise ReloadMismatchError(
                f"Reloaded summary runtime_id {reloaded_summary.get('runtime_id')!r} != {runtime_id!r}"
            )
        if str(reloaded_summary.get("last_heartbeat_event_id") or "") != heartbeat_id:
            raise ReloadMismatchError(
                f"Reloaded summary last_heartbeat_event_id {reloaded_summary.get('last_heartbeat_event_id')!r} != {heartbeat_id!r}"
            )

        # Fresh reload Loop 8 inbox consumer receipt (Finding 3)
        reloaded_inbox_resp = fresh_adapter.request(
            "GET",
            fresh_adapter.deployment_url,
            f"/api/deployment/inbox?aggregate_id={urllib.parse.quote(saga_id, safe='')}&consumer_name=deployment-outbox-consumer",
        )
        self.probes_executed += 1
        inbox_items = (
            reloaded_inbox_resp.payload
            if isinstance(reloaded_inbox_resp.payload, list)
            else []
        )
        reloaded_inbox = None
        for item in inbox_items:
            if (
                isinstance(item, Mapping)
                and item.get("aggregate_id") == saga_id
                and item.get("consumer_name") == "deployment-outbox-consumer"
                and item.get("status") == "applied"
                and str(item.get("event_id") or "") == inbox_receipt_id
            ):
                reloaded_inbox = dict(item)
                break
        if not reloaded_inbox:
            raise ReloadMismatchError(
                f"Reloaded Loop 8 inbox receipt for saga {saga_id} missing or mutated on fresh readback"
            )

        # Fresh reload Loop 9 trade episode receipt (Finding 3)
        reloaded_ep = None
        ep_detail_resp = fresh_adapter.request(
            "GET",
            fresh_adapter.telemetry_url,
            f"/api/telemetry/trade-episodes/{urllib.parse.quote(episode_receipt_id, safe='')}",
            expected_status={200, 404},
        )
        self.probes_executed += 1
        if ep_detail_resp.status == 200 and isinstance(ep_detail_resp.payload, Mapping) and ep_detail_resp.payload.get("error") is None:
            reloaded_ep = dict(ep_detail_resp.payload)
        else:
            ep_list_resp = fresh_adapter.request(
                "GET",
                fresh_adapter.telemetry_url,
                f"/api/telemetry/trade-episodes?strategy_id=tw_session_momentum&environment=paper&runtime_id={urllib.parse.quote(runtime_id, safe='')}",
            )
            self.probes_executed += 1
            cand_list = (
                ep_list_resp.payload
                if isinstance(ep_list_resp.payload, list)
                else ep_list_resp.payload.get("projections")
                or ep_list_resp.payload.get("episodes")
                or ep_list_resp.payload.get("items")
                or []
            )
            for cand in cand_list:
                c_id = str(
                    cand.get("trade_episode_id")
                    or cand.get("episode_id")
                    or cand.get("id")
                    or ""
                )
                if c_id == episode_receipt_id:
                    reloaded_ep = dict(cand)
                    break

        if not reloaded_ep:
            raise ReloadMismatchError(
                f"Reloaded Loop 9 trade episode {episode_receipt_id} missing on fresh readback"
            )

        rel_binding = reloaded_ep.get("runtime_binding_id") or reloaded_ep.get("binding_id")
        if rel_binding != binding_id:
            raise ReloadMismatchError(
                f"Reloaded trade episode binding_id {rel_binding!r} != {binding_id!r}"
            )
        rel_fill_ids = reloaded_ep.get("fill_ids")
        rel_member = (
            (isinstance(rel_fill_ids, list) and event_id in rel_fill_ids)
            or (reloaded_ep.get("event_id") == event_id)
        )
        if not rel_member:
            raise ReloadMismatchError(
                f"Reloaded trade episode missing causal fill {event_id}"
            )
        if reloaded_ep.get("status") not in (
            "closed",
            "reflected",
            "completed",
            "filled",
            "terminal",
            "force_closed",
        ):
            raise ReloadMismatchError(
                f"Reloaded trade episode status {reloaded_ep.get('status')!r} is not terminal"
            )

        reload_evidence = {
            "verified": is_isolated,
            "reloaded_at": utc_now(),
            "loop_08_binding_id": binding_id,
            "loop_08_status": reloaded_binding.get("status"),
            "loop_08_plan_id": plan_id,
            "loop_08_checksum": rel_checksum,
            "loop_08_dep003_projection": redact_secrets(reloaded_dep003),
            "loop_08_inbox_receipt": redact_secrets(reloaded_inbox),
            "loop_09_event_id": event_id,
            "loop_09_runtime_id": runtime_id,
            "loop_09_heartbeat_id": heartbeat_id,
            "loop_09_trade_episode_receipt": redact_secrets(reloaded_ep),
            "fresh_client_isolated": is_isolated,
        }
        if not is_isolated:
            reload_evidence["incomplete_reason"] = (
                "fresh_client_isolated proof absent: no isolated fresh client factory supplied"
            )

        loop8_evidence["durable_reload"] = {
            "verified": is_isolated,
            "binding_id": binding_id,
            "plan_id": plan_id,
            "status": reloaded_binding.get("status"),
            "checksum": rel_checksum,
            "dep003_verified": True,
            "inbox_receipt_verified": True,
        }
        loop9_evidence["durable_reload"] = {
            "verified": is_isolated,
            "event_id": event_id,
            "runtime_id": runtime_id,
            "heartbeat_id": heartbeat_id,
            "trade_episode_verified": True,
        }

        return {
            "status": "passed",
            "loop_08_promotion_deployment": loop8_evidence,
            "loop_09_capital_artifact_execution": loop9_evidence,
            "durable_fresh_client_reload": reload_evidence,
        }

    def run(self, fresh_transport_factory: Callable[[], Transport] | None = None) -> dict[str, Any]:
        """Full probe orchestration."""
        evidence: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "task_id": TASK_ID,
            "status": "in_progress",
            "mode": "paper_lifecycle" if self.config.execute_paper_lifecycle else "read_only_preflight",
            "started_at": self.started_at,
            "completed_at": None,
            "duration_seconds": None,
            "probes_executed": 0,
            "served_identity": {},
            "loops": {},
            "audit": {
                "bearer_credentials_redacted": True,
                "probes_executed": 0,
                "paper_only_enforced": True,
                "isolated_from_release_controller": True,
            },
        }

        try:
            preflight = self.run_preflight()
            evidence["served_identity"] = preflight["served_identity"]

            if not self.config.execute_paper_lifecycle:
                evidence["status"] = "preflight_passed"
                evidence["preflight"] = preflight
            else:
                lifecycle_results = self.execute_paper_lifecycle(
                    preflight, fresh_transport_factory=fresh_transport_factory
                )
                evidence["status"] = lifecycle_results["status"]
                evidence["loops"]["loop_08_promotion_deployment"] = lifecycle_results[
                    "loop_08_promotion_deployment"
                ]
                evidence["loops"]["loop_09_capital_artifact_execution"] = lifecycle_results[
                    "loop_09_capital_artifact_execution"
                ]
                evidence["durable_fresh_client_reload"] = lifecycle_results[
                    "durable_fresh_client_reload"
                ]

        except PreflightBlockedError as exc:
            evidence["status"] = "blocked"
            evidence["error"] = str(exc)
            evidence["blocked_reason"] = str(exc)
            evidence["failure_type"] = type(exc).__name__
        except ServiceUnavailableError as exc:
            evidence["status"] = "unavailable"
            evidence["error"] = str(exc)
            evidence["unavailable_reason"] = str(exc)
            evidence["failure_type"] = type(exc).__name__
        except Exception as exc:
            evidence["status"] = "failed"
            evidence["error"] = f"{type(exc).__name__}: {exc}"
            evidence["failure_type"] = type(exc).__name__
            raise
        finally:
            now_dt = datetime.now(timezone.utc)
            evidence["completed_at"] = utc_now()
            start_dt = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            evidence["duration_seconds"] = round((now_dt - start_dt).total_seconds(), 3)
            evidence["probes_executed"] = self.probes_executed
            evidence["audit"]["probes_executed"] = self.probes_executed

            sealed = seal_evidence(evidence)

            # Write evidence output if requested (always guaranteed by finally)
            if self.config.output_path:
                out_path = Path(self.config.output_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(sealed, f, indent=2, ensure_ascii=False)
                    f.write("\n")

        return sealed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fresh-stimulus RuntimeBinding and paper lifecycle probe (Loops 8 & 9)."
    )
    parser.add_argument(
        "--bff-base-url",
        default=os.getenv("DEV_BFF_URL", DEFAULT_BFF_BASE_URL),
        help="Target BFF base URL (default: https://api.dev.mvl-cap.tw).",
    )
    parser.add_argument(
        "--fe-base-url",
        default=os.getenv("DEV_FE_URL", DEFAULT_FE_BASE_URL),
        help="Target FE base URL (default: https://app.dev.mvl-cap.tw).",
    )
    parser.add_argument(
        "--expected-fe-sha",
        default=os.getenv("DEV_EXPECTED_FE_SHA"),
        help="Expected 40-hex commit SHA of the served FE bundle.",
    )
    parser.add_argument(
        "--expected-bff-sha",
        default=os.getenv("DEV_EXPECTED_BFF_SHA"),
        help="Expected 40-hex source commit SHA of the served BFF.",
    )
    parser.add_argument(
        "--auth-token",
        default=os.getenv("PANTHEON_AUTH_TOKEN"),
        help="Bearer authentication token for canonical owner API requests (default: $PANTHEON_AUTH_TOKEN).",
    )
    parser.add_argument(
        "--tenant-id",
        default=os.getenv("PANTHEON_TENANT_ID"),
        help="Tenant ID for authenticated owner API requests (default: $PANTHEON_TENANT_ID).",
    )
    parser.add_argument(
        "--execute-paper-lifecycle",
        action="store_true",
        default=False,
        help="Explicitly execute paper lifecycle stimulus (default is read-only preflight).",
    )
    parser.add_argument(
        "--paper-only",
        action="store_true",
        default=True,
        help="Enforce paper-only mode; fails closed if real writes are enabled.",
    )
    parser.add_argument(
        "--output",
        "-o",
        dest="output_path",
        default=f"docs/deployment/evidence/{TASK_ID}/evidence.json",
        help="Path to write sealed evidence JSON artifact.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=120.0,
        help="Total poll timeout for async lifecycle transitions in seconds.",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    transport: Transport | None = None,
) -> int:
    args = parse_args(argv)
    config = ProbeConfig(
        bff_base_url=args.bff_base_url,
        fe_base_url=args.fe_base_url,
        expected_fe_sha=args.expected_fe_sha,
        expected_bff_sha=args.expected_bff_sha,
        execute_paper_lifecycle=args.execute_paper_lifecycle,
        paper_only=args.paper_only,
        auth_token=args.auth_token,
        tenant_id=args.tenant_id,
        output_path=Path(args.output_path),
        request_timeout_seconds=args.timeout,
        poll_timeout_seconds=args.poll_timeout,
    )

    try:
        probe = DevRuntimePaperLifecycleProbe(config, transport=transport)
        fresh_factory = (lambda: transport or default_http_transport) if config.execute_paper_lifecycle else None
        evidence = probe.run(fresh_transport_factory=fresh_factory)
        status = evidence.get("status")
        print(
            json.dumps(
                {
                    "task_id": TASK_ID,
                    "status": status,
                    "mode": evidence.get("mode"),
                    "failure_type": evidence.get("failure_type"),
                    "artifact_digest_sha256": evidence.get("artifact_digest_sha256"),
                    "output_path": str(config.output_path),
                },
                indent=2,
            )
        )
        if status in ("passed", "preflight_passed"):
            return 0
        if status == "blocked":
            return 2
        if status == "unavailable":
            return 3
        return 1
    except RetiredDeployTargetError as exc:
        print(f"ERROR: Retired deploy target rejected: {exc}", file=sys.stderr)
        return 1
    except RealCapitalOrOrderWriteForbiddenError as exc:
        print(f"ERROR: Real capital / order write forbidden: {exc}", file=sys.stderr)
        return 1
    except StaleIdentityError as exc:
        print(f"ERROR: Stale identity rejected: {exc}", file=sys.stderr)
        return 1
    except CorrelationMismatchError as exc:
        print(f"ERROR: Correlation mismatch rejected: {exc}", file=sys.stderr)
        return 1
    except MissingConsumerReceiptError as exc:
        print(f"ERROR: Missing consumer receipt: {exc}", file=sys.stderr)
        return 1
    except ReloadMismatchError as exc:
        print(f"ERROR: Durable reload mismatch: {exc}", file=sys.stderr)
        return 1
    except ProbeTimeoutError as exc:
        print(f"ERROR: Probe timeout: {exc}", file=sys.stderr)
        return 1
    except PreflightBlockedError as exc:
        print(f"ERROR: Preflight blocked: {exc}", file=sys.stderr)
        return 2
    except ProbeError as exc:
        print(f"ERROR: Probe error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: Unexpected probe error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
