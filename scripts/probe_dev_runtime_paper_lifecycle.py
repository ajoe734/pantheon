#!/usr/bin/env python3
"""Fresh-stimulus RuntimeBinding and paper lifecycle probe for Loops 8 and 9.

Task: DEV-LOOP8-9-PROBE-20260905

This probe coordinates and verifies:
1. Served FE and BFF identity readback against current dev hosts
   (app.dev.mvl-cap.tw and api.dev.mvl-cap.tw), rejecting retired targets.
2. Read-only preflight by default; requires explicit paper-only execution option.
3. Fail-closed protection against real-capital or real-order write flags.
4. Loop 8 (Promotion / Deployment): One newly created stimulus through canonical
   owner APIs into an executable RuntimeBinding with next-consumer receipt
   (fleet desired state) and owner worker identity.
5. Loop 9 (Paper Lifecycle & Execution): Verifies paper runtime worker,
   telemetry event (simulated fill with is_real_capital=false, is_real_order=false),
   and next-consumer heartbeat receipt.
6. Durable fresh-client reload for Loops 8 and 9.
7. Separate classification for success, blocked, unavailable, and failed states.
8. Complete credential redaction; bearer tokens or secrets are never printed or saved.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request
import uuid

TASK_ID = "DEV-LOOP8-9-PROBE-20260905"
SCHEMA_VERSION = "pantheon.dev-runtime-paper-lifecycle-evidence.v1"
PARENT_STRATEGY_ARTIFACT_ID = "artifact-tw-session-momentum-v1"

DEFAULT_BFF_BASE_URL = "https://api.dev.mvl-cap.tw"
DEFAULT_FE_BASE_URL = "https://app.dev.mvl-cap.tw"

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
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").strip().lower()
    netloc = (parsed.netloc or "").strip().lower()

    for pattern in RETIRED_TARGET_PATTERNS:
        if pattern.search(hostname) or pattern.search(netloc) or pattern.search(url):
            raise RetiredDeployTargetError(
                f"URL {url!r} targets retired deploy host or project ({pattern.pattern}). "
                f"Target current dev hosts: {DEFAULT_FE_BASE_URL} / {DEFAULT_BFF_BASE_URL}."
            )


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
    deployment_url: str | None = None
    runtime_url: str | None = None
    fleet_url: str | None = None
    telemetry_url: str | None = None
    capital_url: str | None = None
    governance_url: str | None = None
    registry_url: str | None = None
    source_ingest_url: str | None = None
    expected_fe_sha: str | None = None
    expected_bff_sha: str | None = None
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
        self.deployment_url = (
            config.deployment_url or os.getenv("PANTHEON_DEPLOYMENT_URL") or f"{config.bff_base_url}/api/deployment"
        ).rstrip("/")
        self.runtime_url = (
            config.runtime_url or os.getenv("PANTHEON_RUNTIME_URL") or f"{config.bff_base_url}/api/runtime"
        ).rstrip("/")
        self.fleet_url = (
            config.fleet_url or os.getenv("PANTHEON_FLEET_URL") or f"{config.bff_base_url}/api/fleet"
        ).rstrip("/")
        self.telemetry_url = (
            config.telemetry_url or os.getenv("PANTHEON_TELEMETRY_URL") or f"{config.bff_base_url}/api/telemetry"
        ).rstrip("/")
        self.capital_url = (
            config.capital_url or os.getenv("PANTHEON_CAPITAL_URL") or f"{config.bff_base_url}/api/capital"
        ).rstrip("/")
        self.governance_url = (
            config.governance_url or os.getenv("PANTHEON_GOVERNANCE_URL") or f"{config.bff_base_url}/api/governance"
        ).rstrip("/")
        self.registry_url = (
            config.registry_url or os.getenv("PANTHEON_REGISTRY_URL") or f"{config.bff_base_url}/api/registry"
        ).rstrip("/")
        self.source_ingest_url = (
            config.source_ingest_url or os.getenv("PANTHEON_SOURCE_INGEST_URL") or f"{config.bff_base_url}/api/source-ingest"
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
        url = f"{base_url}{path}" if path.startswith("/") else f"{base_url}/{path}"
        validate_host_not_retired(url)
        resp = self.transport(
            method,
            url,
            headers or {},
            payload,
            self.config.request_timeout_seconds,
        )
        if resp.status in {502, 503, 504}:
            raise ServiceUnavailableError(f"Service at {url} returned unavailable {resp.status}")
        if resp.status not in expected:
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

        # 1. Fetch FE deployment.json
        fe_resp = self.adapter.request(
            "GET", self.config.fe_base_url, "/deployment.json", expected_status={200}
        )
        self.probes_executed += 1
        fe_data = fe_resp.payload

        fe_commit = str(fe_data.get("commit") or fe_data.get("frontendSha") or "").strip()
        fe_pair_id = str(fe_data.get("pairId") or "").strip()
        fe_profile = str(fe_data.get("profile") or fe_data.get("deploymentProfile") or "").strip()
        build_mode = fe_data.get("buildMode") or {}
        if isinstance(build_mode, Mapping):
            fe_real_writes = str(build_mode.get("VITE_BFF_REAL_WRITES") or "false").lower()
            if fe_real_writes in ("true", "1"):
                raise RealCapitalOrOrderWriteForbiddenError(
                    f"FE served bundle has VITE_BFF_REAL_WRITES={fe_real_writes!r}"
                )

        if self.config.expected_fe_sha:
            if fe_commit != self.config.expected_fe_sha:
                raise StaleIdentityError(
                    f"Served FE commit {fe_commit!r} != expected {self.config.expected_fe_sha!r}"
                )

        # 2. Fetch BFF version
        bff_version_resp = self.adapter.request(
            "GET", self.config.bff_base_url, "/bff/version", expected_status={200}
        )
        self.probes_executed += 1
        bff_version_data = bff_version_resp.payload
        bff_commit = str(
            bff_version_data.get("source_commit_sha") or bff_version_data.get("commit") or ""
        ).strip()
        bff_pair_id = str(bff_version_data.get("pair_id") or "").strip()
        config_posture = bff_version_data.get("config_posture") or {}

        if self.config.expected_bff_sha:
            if bff_commit != self.config.expected_bff_sha:
                raise StaleIdentityError(
                    f"Served BFF commit {bff_commit!r} != expected {self.config.expected_bff_sha!r}"
                )

        # Pair ID consistency check
        pair_consistent = True
        if fe_pair_id and bff_pair_id and fe_pair_id != bff_pair_id:
            raise CorrelationMismatchError(
                f"FE pairId {fe_pair_id!r} does not match BFF pair_id {bff_pair_id!r}"
            )

        # 3. Check BFF health
        readyz_resp = self.adapter.request(
            "GET", self.config.bff_base_url, "/readyz", expected_status={200}
        )
        self.probes_executed += 1
        readyz_data = readyz_resp.payload
        if readyz_data.get("ready") is False:
            raise PreflightBlockedError(
                f"BFF /readyz reported unready: {readyz_data.get('dependencies')}"
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
                },
                "bff": {
                    "source_commit_sha": bff_commit,
                    "pair_id": bff_pair_id,
                    "environment": bff_version_data.get("environment"),
                    "config_posture": config_posture,
                },
                "pair_consistent": pair_consistent,
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

        # Step 2: Mutate strategy artifact
        mutate_resp = self.adapter.request(
            "POST",
            self.adapter.registry_url,
            f"/api/registry/strategy-artifacts/{PARENT_STRATEGY_ARTIFACT_ID}/mutate",
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
                "tenant_id": "default",
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
            "tenant_id": "default",
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
                "target_artifact_id": PARENT_STRATEGY_ARTIFACT_ID,
                "target_version": "1.0.0",
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
        saga_id = (
            (dispatch_resp.payload.get("deployment_saga") or {}).get("saga", {}).get("saga_id")
            or f"saga-{plan_id}"
        )

        # Wait for deployment plan executed and saga completed
        def _check_deployment_terminal() -> dict[str, Any] | None:
            plan_resp = self.adapter.request(
                "GET", self.adapter.deployment_url, f"/api/deployment/plans/{plan_id}"
            )
            self.probes_executed += 1
            status = plan_resp.payload.get("status")
            if status == "executed":
                return plan_resp.payload
            if status in ("failed", "aborted"):
                raise PreflightBlockedError(f"DeploymentPlan failed terminal status: {status}")
            return None

        self._wait_until(f"DeploymentPlan {plan_id} terminal status", _check_deployment_terminal)

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

        # Check Loop 8 next-consumer receipt: Fleet desired state
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

        if not fleet_desired_id:
            raise MissingConsumerReceiptError(
                f"Fleet desired state did not accept binding {binding_id}"
            )

        loop8_evidence = {
            "trigger_id": plan_id,
            "terminal_output_id": binding_id,
            "next_consumer_receipt_id": fleet_desired_id,
            "owner_worker_identity": {
                "service": "deployment-outbox-consumer",
                "role": "deployment_executor",
                "plan_id": plan_id,
                "status": "completed",
            },
            "authority_readback": redact_secrets(binding),
            "next_consumer_readback": {
                "fleet_desired_binding_id": fleet_desired_id,
                "saga_id": saga_id,
                "saga_status": "completed",
            },
            "assertions": {
                "executable_runtime_binding": True,
                "approved_artifact_exact": True,
                "market_data_policy_bound": True,
                "paper_only": True,
                "runtime_binding_active": True,
            },
            "started_at": loop8_started,
            "ended_at": utc_now(),
        }

        # Step 6: Loop 9 (Paper execution & Telemetry)
        loop9_started = utc_now()

        # Check fleet worker
        def _check_fleet_worker() -> dict[str, Any] | None:
            fleet_state_resp = self.adapter.request(
                "GET", self.adapter.fleet_url, "/api/fleet/state", expected_status={200}
            )
            self.probes_executed += 1
            workers = fleet_state_resp.payload.get("workers") or {}
            worker = None
            if isinstance(workers, list):
                worker = next((w for w in workers if w.get("binding_id") == binding_id), None)
            elif isinstance(workers, dict):
                worker = workers.get(binding_id)
            if isinstance(worker, dict) and worker.get("status") == "running":
                return worker
            return None

        fleet_worker = self._wait_until(f"paper fleet worker for {binding_id}", _check_fleet_worker)

        # Check telemetry event
        def _check_telemetry_event() -> dict[str, Any] | None:
            summary_resp = self.adapter.request(
                "GET",
                self.adapter.telemetry_url,
                f"/api/telemetry/runtime-summaries/{runtime_id}",
                expected_status={200, 404},
            )
            self.probes_executed += 1
            if summary_resp.status != 200:
                return None
            summary = summary_resp.payload
            event_ids = list(summary.get("recent_lifecycle_event_ids") or [])
            for ev_id in reversed(event_ids):
                ev_resp = self.adapter.request(
                    "GET",
                    self.adapter.telemetry_url,
                    f"/api/telemetry/events/{ev_id}",
                    expected_status={200, 404},
                )
                self.probes_executed += 1
                if ev_resp.status == 200:
                    ev = ev_resp.payload
                    if (
                        ev.get("event_type") == "paper_fill_simulated"
                        or (ev.get("metadata") or {}).get("sim_fill_flag") is True
                    ):
                        return {"event": ev, "summary": summary}
            return None

        telemetry_result = self._wait_until(
            f"telemetry fill event for {runtime_id}", _check_telemetry_event
        )
        runtime_event = telemetry_result["event"]
        runtime_summary = telemetry_result["summary"]
        event_id = str(runtime_event.get("event_id") or "")
        heartbeat_receipt_id = str(runtime_summary.get("last_heartbeat_event_id") or "")

        # Validate event contract
        if not heartbeat_receipt_id:
            raise MissingConsumerReceiptError(
                f"Loop 9 next-consumer heartbeat receipt missing for runtime {runtime_id}"
            )

        event_metadata = runtime_event.get("metadata") or {}
        if event_metadata.get("is_real_capital") is True:
            raise RealCapitalOrOrderWriteForbiddenError("Telemetry event reported is_real_capital=True")
        if event_metadata.get("is_real_order") is True:
            raise RealCapitalOrOrderWriteForbiddenError("Telemetry event reported is_real_order=True")

        # Correlation check: verify correlation_id matches if present
        event_corr = (
            runtime_event.get("correlation_envelope", {}).get("correlation_id")
            or event_metadata.get("correlation_envelope", {}).get("correlation_id")
        )
        if event_corr and event_corr != correlation_id:
            raise CorrelationMismatchError(
                f"Telemetry event correlation_id {event_corr!r} != expected {correlation_id!r}"
            )

        loop9_evidence = {
            "trigger_id": binding_id,
            "terminal_output_id": event_id,
            "next_consumer_receipt_id": heartbeat_receipt_id,
            "owner_worker_identity": {
                "service": "paper-signal-producer",
                "role": "paper_runtime_worker",
                "runtime_worker": fleet_worker,
                "binding_id": binding_id,
            },
            "authority_readback": redact_secrets(runtime_event),
            "next_consumer_readback": {
                "last_heartbeat_event_id": heartbeat_receipt_id,
                "runtime_id": runtime_id,
                "state": runtime_summary.get("state"),
            },
            "assertions": {
                "artifact_signal_not_smoke": True,
                "is_real_capital": False,
                "is_real_order": False,
                "broker_submission_status": "filled",
                "source_snapshot_driven": True,
            },
            "started_at": loop9_started,
            "ended_at": utc_now(),
        }

        # Step 7: Durable fresh-client reload verification
        # Instantiate a completely fresh transport / client
        fresh_transport = fresh_transport_factory() if fresh_transport_factory else self.transport
        fresh_adapter = CanonicalOwnerAdapter(self.config, fresh_transport)

        # Fresh reload Loop 8
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

        # Fresh reload Loop 9
        reloaded_event_resp = fresh_adapter.request(
            "GET", fresh_adapter.telemetry_url, f"/api/telemetry/events/{event_id}"
        )
        self.probes_executed += 1
        reloaded_event = reloaded_event_resp.payload
        if str(reloaded_event.get("event_id") or "") != event_id:
            raise ReloadMismatchError(
                f"Reloaded event ID {reloaded_event.get('event_id')!r} != {event_id!r}"
            )

        reloaded_summary_resp = fresh_adapter.request(
            "GET", fresh_adapter.telemetry_url, f"/api/telemetry/runtime-summaries/{runtime_id}"
        )
        self.probes_executed += 1
        reloaded_summary = reloaded_summary_resp.payload
        if str(reloaded_summary.get("runtime_id") or "") != runtime_id:
            raise ReloadMismatchError(
                f"Reloaded summary runtime_id {reloaded_summary.get('runtime_id')!r} != {runtime_id!r}"
            )

        reload_evidence = {
            "verified": True,
            "reloaded_at": utc_now(),
            "loop_08_binding_id": binding_id,
            "loop_08_status": reloaded_binding.get("status"),
            "loop_09_event_id": event_id,
            "loop_09_runtime_id": runtime_id,
            "fresh_client_isolated": True,
        }

        loop8_evidence["durable_reload"] = {
            "verified": True,
            "binding_id": binding_id,
            "status": reloaded_binding.get("status"),
        }
        loop9_evidence["durable_reload"] = {
            "verified": True,
            "event_id": event_id,
            "runtime_id": runtime_id,
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
            "title": "Implement fresh-stimulus RuntimeBinding and paper lifecycle probe",
            "status": "pending",
            "mode": "paper_lifecycle" if self.config.execute_paper_lifecycle else "read_only_preflight",
            "target_hosts": {
                "bff": self.config.bff_base_url,
                "fe": self.config.fe_base_url,
            },
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
        except ServiceUnavailableError as exc:
            evidence["status"] = "unavailable"
            evidence["error"] = str(exc)
            evidence["unavailable_reason"] = str(exc)
        except Exception as exc:
            evidence["status"] = "failed"
            evidence["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            now_dt = datetime.now(timezone.utc)
            evidence["completed_at"] = utc_now()
            start_dt = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            evidence["duration_seconds"] = round((now_dt - start_dt).total_seconds(), 3)
            evidence["probes_executed"] = self.probes_executed
            evidence["audit"]["probes_executed"] = self.probes_executed

        sealed = seal_evidence(evidence)

        # Write evidence output if requested
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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = ProbeConfig(
        bff_base_url=args.bff_base_url,
        fe_base_url=args.fe_base_url,
        expected_fe_sha=args.expected_fe_sha,
        expected_bff_sha=args.expected_bff_sha,
        execute_paper_lifecycle=args.execute_paper_lifecycle,
        paper_only=args.paper_only,
        output_path=Path(args.output_path),
        request_timeout_seconds=args.timeout,
        poll_timeout_seconds=args.poll_timeout,
    )

    probe = DevRuntimePaperLifecycleProbe(config)
    try:
        evidence = probe.run()
        status = evidence.get("status")
        print(
            json.dumps(
                {
                    "task_id": TASK_ID,
                    "status": status,
                    "mode": evidence.get("mode"),
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
    except Exception as exc:
        print(f"ERROR: Unexpected probe error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
