"""Current deployed runtime proof for Pantheon loops 8 through 12.

This suite is deliberately opt-in.  It talks only to deployed HTTP owners and
to one isolated Docker Compose project supplied by the caller; it never uses a
TestClient, an in-process store, or a loop-inventory verifier as runtime proof.

Required environment variables when ``PANTHEON_L12_DEPLOYED_E2E=1``:

* ``PANTHEON_L12_<SERVICE>_URL`` for BFF, capital, deployment, evolution,
  fleet, governance, incidents, reconciliation, registry, runtime, telemetry;
* ``PANTHEON_L12_EXPECTED_SHA`` (the exact 40-character image revision);
* ``PANTHEON_L12_COMPOSE_PROJECT`` and ``PANTHEON_L12_COMPOSE_FILES``;
* ``PANTHEON_L12_EVIDENCE_OUTPUT`` (a path outside the checked-in evidence
  directory; the governed owner copies the atomic result after validation).

The Compose files value is an ``os.pathsep``-separated list.  The task harness
must set paper-only controls, a one- or two-second worker interval, BFF health
probe interval, and must disable all live broker/capital execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest


TASK_ID = "L12-CURRENT-E2E-RUNTIME-20260814"
TENANT_ID = "default"
EVOLUTION_TENANT_ID = "pantheon-default"
PARENT_ARTIFACT_ID = "artifact-tw-session-momentum-v1"

pytestmark = pytest.mark.skipif(
    os.getenv("PANTHEON_L12_DEPLOYED_E2E", "").strip().lower()
    not in {"1", "true", "yes"},
    reason="set PANTHEON_L12_DEPLOYED_E2E=1 for the isolated deployed proof",
)


class DeployedProofError(AssertionError):
    """The deployed runtime proof failed or was not safely configured."""


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise DeployedProofError(f"{name} is required for the deployed proof")
    return value


def _optional_bearer(name: str) -> dict[str, str]:
    token = os.getenv(name, "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


@dataclass(frozen=True)
class Settings:
    urls: dict[str, str]
    expected_sha: str
    compose_project: str
    compose_files: tuple[str, ...]
    evidence_output: Path
    capital_headers: dict[str, str]

    @classmethod
    def from_env(cls) -> "Settings":
        services = (
            "bff",
            "capital",
            "deployment",
            "evolution",
            "fleet",
            "governance",
            "incidents",
            "reconciliation",
            "registry",
            "runtime",
            "telemetry",
        )
        urls = {
            service: _require_env(f"PANTHEON_L12_{service.upper()}_URL").rstrip("/")
            for service in services
        }
        for service, url in urls.items():
            if urllib.parse.urlparse(url).scheme not in {"http", "https"}:
                raise DeployedProofError(
                    f"{service} URL must be an HTTP boundary, got {url!r}"
                )

        expected_sha = _require_env("PANTHEON_L12_EXPECTED_SHA")
        if len(expected_sha) != 40 or any(ch not in "0123456789abcdef" for ch in expected_sha):
            raise DeployedProofError("PANTHEON_L12_EXPECTED_SHA must be a lowercase 40-character SHA")

        raw_files = _require_env("PANTHEON_L12_COMPOSE_FILES")
        compose_files = tuple(part for part in raw_files.split(os.pathsep) if part)
        if not compose_files or any(not Path(path).is_file() for path in compose_files):
            raise DeployedProofError("every PANTHEON_L12_COMPOSE_FILES entry must exist")

        output = Path(_require_env("PANTHEON_L12_EVIDENCE_OUTPUT")).resolve()
        if "docs/deployment/evidence" in output.as_posix():
            raise DeployedProofError(
                "the test writes a temporary atomic result; checked-in evidence is owner-managed"
            )
        return cls(
            urls=urls,
            expected_sha=expected_sha,
            compose_project=_require_env("PANTHEON_L12_COMPOSE_PROJECT"),
            compose_files=compose_files,
            evidence_output=output,
            capital_headers=_optional_bearer("PANTHEON_L12_CAPITAL_TOKEN"),
        )

    def compose_command(self, *args: str) -> list[str]:
        command = ["docker", "compose", "-p", self.compose_project]
        for path in self.compose_files:
            command.extend(("-f", path))
        command.extend(args)
        return command


class HttpOwner:
    """Small urllib client that preserves exact HTTP status/payload evidence."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def request(
        self,
        service: str,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        expected: set[int] = frozenset({200}),
        timeout: float = 20.0,
    ) -> Any:
        url = f"{self.settings.urls[service]}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        encoded = None if body is None else _canonical_json(dict(body)).encode("utf-8")
        final_headers = {"Accept": "application/json", **dict(headers or {})}
        if encoded is not None:
            final_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=final_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except (urllib.error.URLError, OSError) as exc:
            raise DeployedProofError(
                f"{method} {service}{path} transport failed: {exc}"
            ) from exc
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        if status not in expected:
            raise DeployedProofError(
                f"{method} {service}{path} returned {status}, expected "
                f"{sorted(expected)}: {_canonical_json(payload)}"
            )
        return payload


@dataclass
class Evidence:
    settings: Settings
    started_at: str = field(default_factory=_utc_now)
    cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    identities: dict[str, dict[str, Any]] = field(default_factory=dict)
    status: str = "running"
    error: str | None = None

    def add_case(
        self,
        name: str,
        *,
        loop: int,
        trigger_id: str,
        owner_worker: Mapping[str, Any],
        terminal_output_id: str,
        authority_readback: Mapping[str, Any],
        next_consumer_readback: Mapping[str, Any],
        started_at: str,
        compose_services: list[str],
        assertions: Mapping[str, Any],
    ) -> None:
        self.cases[name] = {
            "assertions": dict(assertions),
            "authority_readback": dict(authority_readback),
            "compose_services": compose_services,
            "ended_at": _utc_now(),
            "git_sha": self.settings.expected_sha,
            "loop": loop,
            "next_consumer_readback": dict(next_consumer_readback),
            "owner_worker_identity": dict(owner_worker),
            "started_at": started_at,
            "terminal_output_id": terminal_output_id,
            "trigger_id": trigger_id,
        }

    def write(self) -> None:
        payload = {
            "cases": self.cases,
            "compose_project": self.settings.compose_project,
            "ended_at": _utc_now(),
            "environment": "isolated_compose_paper",
            "error": self.error,
            "expected_sha": self.settings.expected_sha,
            "identities": self.identities,
            "live_broker_enabled": False,
            "live_capital_enabled": False,
            "started_at": self.started_at,
            "status": self.status,
            "task_id": TASK_ID,
        }
        self.settings.evidence_output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=self.settings.evidence_output.parent,
            prefix=f".{self.settings.evidence_output.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.settings.evidence_output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _wait_until(
    description: str,
    predicate: Callable[[], Any],
    *,
    timeout: float = 180.0,
    interval: float = 2.0,
) -> Any:
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (DeployedProofError, KeyError, TypeError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(interval)
    raise DeployedProofError(
        f"timed out waiting for {description}; last_error={last_error!r}"
    )


def _run(command: list[str], *, expected: set[int] = frozenset({0})) -> str:
    process = subprocess.run(command, check=False, capture_output=True, text=True)
    if process.returncode not in expected:
        raise DeployedProofError(
            f"command returned {process.returncode}: {command!r}: "
            f"{process.stderr or process.stdout}"
        )
    return process.stdout.strip()


class RuntimeChain:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = HttpOwner(settings)
        self.evidence = Evidence(settings)
        self.suffix = uuid.uuid4().hex[:10]
        self.results: dict[str, Any] = {}
        self.runtime_headers = {
            "Authorization": "Bearer l12-current-e2e:operator:mfa"
        }
        self.deployment_headers = {
            "Authorization": "Bearer l12-current-e2e:operator,service",
            "X-Tenant-Id": TENANT_ID,
        }
        self.telemetry_headers = {
            "Authorization": "Bearer telemetry-service-internal",
            "X-Tenant-Id": TENANT_ID,
        }
        self.reconciliation_headers = {
            "Authorization": "Bearer pantheon-local-reconciliation-drift-service",
            "X-Tenant-Id": TENANT_ID,
        }
        self.evolution_headers = {
            "Authorization": "Bearer pantheon-local-evolution-service",
            "X-Tenant-Id": EVOLUTION_TENANT_ID,
        }
        self.bff_headers = {
            "Authorization": f"Bearer l12-current-e2e:operator,reviewer,admin:{TENANT_ID}"
        }

    def _compose_container_id(self, service: str) -> str:
        return _run(self.settings.compose_command("ps", "-q", service))

    def _identity(self, service: str) -> dict[str, Any]:
        container_id = self._compose_container_id(service)
        if not container_id:
            raise DeployedProofError(f"Compose service {service!r} is not running")
        inspected = json.loads(_run(["docker", "inspect", container_id]))[0]
        labels = inspected.get("Config", {}).get("Labels") or {}
        revision = labels.get("org.opencontainers.image.revision") or ""
        source_head = _run(["git", "rev-parse", "HEAD"])
        identity = {
            "container_id": container_id,
            "container_name": str(inspected.get("Name") or "").lstrip("/"),
            "image_id": inspected.get("Image"),
            "image_name": inspected.get("Config", {}).get("Image"),
            "oci_revision": revision,
            "source_head": source_head,
            "revision_evidence": "oci_label" if revision else "compose_source_head",
            "service": service,
            "state": inspected.get("State", {}).get("Status"),
        }
        if source_head != self.settings.expected_sha:
            raise DeployedProofError(
                f"source HEAD mismatch: expected {self.settings.expected_sha}, "
                f"got {source_head}"
            )
        if revision and revision != self.settings.expected_sha:
            raise DeployedProofError(
                f"{service} OCI revision mismatch: expected "
                f"{self.settings.expected_sha}, got {revision or '<missing>'}"
            )
        self.evidence.identities[service] = identity
        return identity

    def _container_json(self, service: str, path: str) -> dict[str, Any]:
        raw = _run(["docker", "exec", self._compose_container_id(service), "cat", path])
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise DeployedProofError(f"{service}:{path} did not contain a JSON object")
        return payload

    def _redis_queue_depth(self, binding_id: str) -> int:
        raw = _run(
            [
                "docker",
                "exec",
                self._compose_container_id("signal-store"),
                "redis-cli",
                "LLEN",
                f"pantheon:signals:pending:{binding_id}",
            ]
        )
        return int(raw)

    def _create_capital(self, label: str) -> dict[str, str]:
        pool_id = f"pool-l12-{label}-{self.suffix}"
        persona_id = f"persona-l12-{label}-{self.suffix}"
        binding_id = f"pcb-l12-{label}-{self.suffix}"
        headers = {"X-Tenant-Id": TENANT_ID, **self.settings.capital_headers}
        self.http.request(
            "capital",
            "POST",
            "/api/capital-pools",
            body={
                "actor_id": f"{TASK_ID.lower()}-capital-admin",
                "actor_role": "capital.admin",
                "idempotency_key": f"{TASK_ID}:{pool_id}",
                "request_hash": f"request-{pool_id}",
                "pool_id": pool_id,
                "name": f"L12 current runtime {label}",
                "owner_id": TASK_ID,
                "owner_type": "operator",
                "status": "active",
                "currency": "USD",
                "budget": 1000.0,
                "single_runtime_enforced": True,
                "metadata": {"paper_only": True, "source_task_id": TASK_ID},
            },
            headers=headers,
            expected={201},
        )
        created = self.http.request(
            "capital",
            "POST",
            "/api/bindings",
            body={
                "actor_id": f"{TASK_ID.lower()}-persona-admin",
                "actor_role": "persona.admin",
                "idempotency_key": f"{TASK_ID}:{binding_id}",
                "request_hash": f"request-{binding_id}",
                "binding_id": binding_id,
                "persona_id": persona_id,
                "capital_pool_id": pool_id,
                "role": "paper_owner",
                "allowed_deployment_scope": "paper",
                "budget": 1000.0,
                "created_by": TASK_ID,
                "metadata": {"paper_only": True, "source_task_id": TASK_ID},
            },
            headers=headers,
            expected={201},
        )
        if created.get("status") != "pending":
            raise DeployedProofError("PersonaCapitalBinding did not begin pending")
        active = self.http.request(
            "capital",
            "POST",
            f"/api/bindings/{binding_id}/activate",
            body={
                "actor_id": f"{TASK_ID.lower()}-persona-admin",
                "actor_role": "persona.admin",
                "approval_decision_id": f"capital-paper-{binding_id}",
            },
            headers=headers,
        )
        if active.get("status") != "active" or active.get("allowed_deployment_scope") != "paper":
            raise DeployedProofError("PersonaCapitalBinding is not active paper-only authority")
        return {"pool_id": pool_id, "persona_id": persona_id, "pcb_id": binding_id}

    def _approve_artifact(
        self,
        *,
        artifact_id: str,
        version: str,
        capital: Mapping[str, str],
        label: str,
    ) -> dict[str, Any]:
        decision_id = f"approval-l12-{label}-{self.suffix}"
        self.http.request(
            "governance",
            "POST",
            "/api/governance/approvals",
            body={
                "capital_pool_id": capital["pool_id"],
                "decision_id": decision_id,
                "persona_id": capital["persona_id"],
                "risk_level": "medium",
                "target_id": artifact_id,
                "target_type": "registry_entry",
                "target_version": version,
                "tenant_id": TENANT_ID,
                "owner_user_id": TASK_ID,
            },
            expected={201},
        )
        self.http.request(
            "governance",
            "POST",
            f"/api/governance/approvals/{decision_id}/review",
            body={
                "actor_id": f"{TASK_ID.lower()}-reviewer",
                "actor_role": "governance_reviewer",
            },
        )
        decided = self.http.request(
            "governance",
            "POST",
            f"/api/governance/approvals/{decision_id}/decide",
            body={
                "actor_id": f"{TASK_ID.lower()}-risk-owner",
                "actor_role": "risk_owner",
                "outcome": "approved",
                "rationale": f"paper-only deployed proof for {TASK_ID}",
            },
        )
        if decided.get("decision") != "approved":
            raise DeployedProofError("Governance did not return an approved decision")
        return decided

    def _deploy_artifact(
        self,
        label: str,
        *,
        capital: Mapping[str, str],
        include_projection_checksum: bool,
        parameter: float,
        version_minor: int,
    ) -> dict[str, Any]:
        artifact_id = f"artifact-l12-{label}-{self.suffix}"
        version = f"2.{version_minor}.0"
        mutated = self.http.request(
            "registry",
            "POST",
            f"/api/registry/strategy-artifacts/{PARENT_ARTIFACT_ID}/mutate",
            body={
                "new_artifact_id": artifact_id,
                "new_version": version,
                "parameter_updates": {"momentum_threshold": parameter},
                "source_run_ids": [TASK_ID, f"{TASK_ID}-{label}"],
            },
        )
        child = mutated["entry"]["metadata"]["strategy_artifact"]
        approval = self._approve_artifact(
            artifact_id=artifact_id,
            version=version,
            capital=capital,
            label=label,
        )
        approved = self.http.request(
            "registry",
            "POST",
            f"/api/registry/strategy-artifacts/{artifact_id}/advance",
            body={
                "approval_decision_id": approval["decision_id"],
                "approver": f"{TASK_ID.lower()}-risk-owner",
                "target_state": "approved",
            },
        )
        registry_entry = approved["entry"]
        artifact_payload = _canonical_json(child)
        checksum = f"sha256:{hashlib.sha256(artifact_payload.encode('utf-8')).hexdigest()}"
        strategy_id = child["strategy_id"]
        market_symbol = child["parameters"]["symbols"][0]
        base_key = f"openclaw/registry/{strategy_id}/{version}"
        projection_metadata: dict[str, Any] = {
            "registry_id": artifact_id,
            "strategy_id": strategy_id,
            "version": version,
            "artifact_type": "execution_bundle",
            "artifact_state": "approved",
            "deployment_stage": "paper",
            "promotion_state": "paper",
            "lineage": child["lineage"],
            "created_at": _utc_now(),
        }
        if include_projection_checksum:
            projection_metadata["checksum"] = checksum
        runtime_metadata: dict[str, Any] = {
            "tenant_id": TENANT_ID,
            "environment": "paper",
            "paper_only": True,
            "source_task_id": TASK_ID,
            "strategy_id": strategy_id,
            "persona_capital_binding_id": capital["pcb_id"],
            "object_store": {
                f"{base_key}/metadata.json": projection_metadata,
                f"{base_key}/artifact.bin": artifact_payload,
            },
            "market_input": {
                "symbol": market_symbol,
                "closes": [100.0, 110.0],
                "source_ref": f"source-ingest://normalized/price/{market_symbol}",
                "observed_at": _utc_now(),
            },
        }
        if include_projection_checksum:
            runtime_metadata["artifact_checksum"] = checksum

        plan_id = f"plan-l12-{label}-{self.suffix}"
        plan_body = {
            "approval_decision": approval,
            "approval_decision_id": approval["decision_id"],
            "capital_pool_id": capital["pool_id"],
            "created_by": TASK_ID,
            "current_stage": "none",
            "metadata": runtime_metadata,
            "plan_id": plan_id,
            "registry_entry": registry_entry,
            "sponsor_persona_id": capital["persona_id"],
            "status": "approved",
            "target_stage": "paper",
            "rollback": {
                "target_artifact_id": PARENT_ARTIFACT_ID,
                "target_version": "1.0.0",
                "action_type": "replace",
                "reason": f"{TASK_ID} paper proof rollback parent",
            },
        }
        validation = self.http.request(
            "deployment",
            "POST",
            "/api/deployment/plans/validate",
            body=plan_body,
            headers=self.deployment_headers,
        )
        if validation.get("ok") is not True:
            raise DeployedProofError(f"DeploymentPlan validation failed: {validation!r}")
        plan = self.http.request(
            "deployment",
            "POST",
            "/api/deployment/plans",
            body=plan_body,
            headers=self.deployment_headers,
            expected={201},
        )
        dispatched = self.http.request(
            "deployment",
            "POST",
            f"/api/deployment/plans/{plan_id}/dispatch",
            body={
                "actor_id": TASK_ID,
                "correlation_id": f"correlation-{plan_id}",
                "idempotency_key": f"{TASK_ID}:{plan_id}",
                "registry_entry": registry_entry,
                "source_task_id": TASK_ID,
                "trace_id": str(uuid.uuid4()),
                "workflow_id": "pantheon.l12.current-runtime-e2e",
                "metadata": {"tenant_id": TENANT_ID},
            },
            headers=self.deployment_headers,
        )
        saga_id = dispatched["deployment_saga"]["saga"]["saga_id"]

        def terminal() -> dict[str, Any] | None:
            read_plan = self.http.request(
                "deployment",
                "GET",
                f"/api/deployment/plans/{plan_id}",
                headers=self.deployment_headers,
            )
            saga = self.http.request(
                "deployment",
                "GET",
                f"/api/deployment/sagas/{saga_id}",
                headers=self.deployment_headers,
            )
            if read_plan.get("status") == "executed" and saga.get("status") == "completed":
                return {"plan": read_plan, "saga": saga}
            if read_plan.get("status") in {"aborted", "failed"} or saga.get(
                "status"
            ) in {"aborted", "failed"}:
                raise DeployedProofError(
                    f"{label} deployment terminated without RuntimeBinding: "
                    f"plan={read_plan.get('status')!r}, saga={saga.get('status')!r}, "
                    f"error={saga.get('error') or saga.get('failure_reason')!r}"
                )
            return None

        terminal_state = _wait_until(
            f"{label} DeploymentPlan terminal RuntimeBinding",
            terminal,
            timeout=240,
        )
        listed = self.http.request(
            "runtime",
            "GET",
            "/api/runtime-bindings",
            query={"plan_id": plan_id},
            headers=self.runtime_headers,
        )
        bindings = listed.get("bindings") if isinstance(listed, dict) else listed
        if not isinstance(bindings, list) or len(bindings) != 1:
            raise DeployedProofError(
                f"expected one RuntimeBinding for {plan_id}, got {bindings!r}"
            )
        binding = self.http.request(
            "runtime",
            "GET",
            f"/api/runtime-bindings/{bindings[0]['binding_id']}",
            headers=self.runtime_headers,
        )
        expected = {
            "artifact_id": artifact_id,
            "artifact_version": version,
            "capital_pool_id": capital["pool_id"],
            "persona_capital_binding_id": capital["pcb_id"],
            "plan_id": plan_id,
            "status": "active",
            "strategy_id": strategy_id,
        }
        actual = {
            **binding,
            "strategy_id": (binding.get("metadata") or {}).get("strategy_id"),
        }
        mismatches = {
            key: {"expected": value, "actual": actual.get(key)}
            for key, value in expected.items()
            if actual.get(key) != value
        }
        if mismatches:
            raise DeployedProofError(f"RuntimeBinding identity mismatch: {mismatches!r}")
        return {
            "approval": approval,
            "artifact": child,
            "binding": binding,
            "checksum": checksum,
            "plan": plan,
            "registry_entry": registry_entry,
            "saga": terminal_state["saga"],
        }

    def _fleet_worker(self, binding_id: str) -> dict[str, Any] | None:
        fleet = self.http.request("fleet", "GET", "/api/fleet/state")
        workers = fleet.get("workers") or {}
        if isinstance(workers, list):
            worker = next(
                (item for item in workers if item.get("binding_id") == binding_id),
                None,
            )
        else:
            worker = workers.get(binding_id) if isinstance(workers, dict) else None
        if isinstance(worker, dict) and worker.get("status") == "running":
            return {"fleet": fleet, "worker": worker}
        return None

    def _runtime_event(self, runtime_id: str) -> dict[str, Any] | None:
        summary = self.http.request(
            "telemetry",
            "GET",
            f"/api/telemetry/runtime-summaries/{runtime_id}",
            headers=self.telemetry_headers,
            expected={200, 404},
        )
        if "error" in summary:
            return None
        event_ids = list(summary.get("recent_lifecycle_event_ids") or [])
        for event_id in reversed(event_ids):
            event = self.http.request(
                "telemetry",
                "GET",
                f"/api/telemetry/events/{event_id}",
                headers=self.telemetry_headers,
                expected={200, 404},
            )
            if event.get("event_type") == "paper_fill_simulated" and event.get("metrics"):
                return {"event": event, "summary": summary}
        return None

    def _bff_health(self) -> dict[str, Any]:
        return self.http.request(
            "bff",
            "GET",
            "/bff/v5/downstream-health",
            headers=self.bff_headers,
        )

    def run(self) -> dict[str, Any]:
        try:
            for service in (
                "broker",
                "operator-bff",
                "runtime-manager",
                "deployment",
                "deployment-outbox-consumer",
                "paper-fleet-reconciler",
                "paper-signal-producer",
                "telemetry",
                "reconciliation-drift-svc",
                "incidents",
                "evolution",
            ):
                self._identity(service)

            positive_capital = self._create_capital("positive")
            negative_capital = self._create_capital("missing-checksum")

            loop8_started = _utc_now()
            positive = self._deploy_artifact(
                "positive",
                capital=positive_capital,
                include_projection_checksum=True,
                parameter=0.01,
                version_minor=1,
            )
            negative = self._deploy_artifact(
                "missing-checksum",
                capital=negative_capital,
                include_projection_checksum=False,
                parameter=0.02,
                version_minor=2,
            )
            positive_binding = positive["binding"]
            negative_binding = negative["binding"]
            self.evidence.add_case(
                "loop_08_promotion_deployment",
                loop=8,
                trigger_id=positive["plan"]["plan_id"],
                owner_worker=self.evidence.identities["deployment-outbox-consumer"],
                terminal_output_id=positive_binding["binding_id"],
                authority_readback=positive_binding,
                next_consumer_readback={
                    "fleet_desired_binding_id": positive_binding["binding_id"],
                    "saga_id": positive["saga"]["saga_id"],
                    "saga_status": positive["saga"]["status"],
                },
                started_at=loop8_started,
                compose_services=["deployment", "deployment-outbox-consumer", "runtime-manager"],
                assertions={
                    "approved_artifact_exact": True,
                    "paper_only": True,
                    "runtime_binding_active": True,
                },
            )

            loop9_started = _utc_now()
            fleet_worker = _wait_until(
                "paper runtime worker for approved RuntimeBinding",
                lambda: self._fleet_worker(positive_binding["binding_id"]),
                timeout=180,
            )
            runtime_event = _wait_until(
                "paper fill telemetry from approved artifact",
                lambda: self._runtime_event(positive_binding["runtime_id"]),
                timeout=240,
            )
            event = runtime_event["event"]
            summary = runtime_event["summary"]
            if event.get("artifact_id") != positive["artifact"]["artifact_id"]:
                raise DeployedProofError("paper fill telemetry did not preserve artifact identity")
            if event.get("metadata", {}).get("is_real_capital") is not False:
                raise DeployedProofError("paper fill telemetry did not prove is_real_capital=false")
            if event.get("metadata", {}).get("is_real_order") is not False:
                raise DeployedProofError("paper fill telemetry did not prove is_real_order=false")
            if event.get("metadata", {}).get("broker_submission_status") != "filled":
                raise DeployedProofError("paper fill telemetry did not prove a filled paper broker handoff")
            self.evidence.add_case(
                "loop_09_capital_artifact_execution",
                loop=9,
                trigger_id=positive_binding["binding_id"],
                owner_worker={
                    **self.evidence.identities["paper-signal-producer"],
                    "runtime_worker": fleet_worker["worker"],
                },
                terminal_output_id=event["event_id"],
                authority_readback=event,
                next_consumer_readback={
                    "last_heartbeat_event_id": summary.get("last_heartbeat_event_id"),
                    "runtime_id": summary.get("runtime_id"),
                    "state": summary.get("state"),
                },
                started_at=loop9_started,
                compose_services=[
                    "paper-signal-producer",
                    "paper-fleet-reconciler",
                    "broker",
                    "telemetry",
                ],
                assertions={
                    "approved_checksum": positive["checksum"],
                    "artifact_signal_not_smoke": True,
                    "is_real_capital": False,
                    "is_real_order": False,
                },
            )

            def negative_health() -> dict[str, Any] | None:
                health = self._container_json(
                    "paper-signal-producer",
                    "/tmp/paper-signal-producer-health.json",
                )
                degraded = health.get("degraded_bindings") or {}
                if negative_binding["binding_id"] in degraded:
                    return health
                return None

            health = _wait_until(
                "missing-checksum binding degraded producer health",
                negative_health,
                timeout=120,
            )
            negative_queue_depth = self._redis_queue_depth(negative_binding["binding_id"])
            if negative_queue_depth != 0:
                raise DeployedProofError(
                    f"missing-checksum binding enqueued {negative_queue_depth} signal(s)"
                )
            self.evidence.add_case(
                "negative_missing_artifact_checksum",
                loop=9,
                trigger_id=negative_binding["binding_id"],
                owner_worker=self.evidence.identities["paper-signal-producer"],
                terminal_output_id=negative_binding["binding_id"],
                authority_readback={
                    "binding_id": negative_binding["binding_id"],
                    "degraded_reason": health["degraded_bindings"][negative_binding["binding_id"]],
                    "producer_status": health.get("status"),
                },
                next_consumer_readback={
                    "queue_key": f"pantheon:signals:pending:{negative_binding['binding_id']}",
                    "queue_depth": negative_queue_depth,
                },
                started_at=loop9_started,
                compose_services=["paper-signal-producer", "signal-store"],
                assertions={"degraded": True, "signals_enqueued_for_binding": 0},
            )

            loop10_started = _utc_now()
            numeric_metric = next(
                (
                    (name, value)
                    for name, value in event.get("metrics", {}).items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                ),
                None,
            )
            if numeric_metric is None:
                raise DeployedProofError("paper fill event has no numeric reconciliation metric")
            metric_name, observed = numeric_metric
            baseline = float(observed) + max(abs(float(observed)), 1.0) * 10.0
            consumed = self.http.request(
                "reconciliation",
                "POST",
                "/api/reconciliation-drift/telemetry-events/consume",
                body={
                    "tenant_id": TENANT_ID,
                    "worker_id": f"{TASK_ID.lower()}-deployed-consumer",
                    "events": [event],
                    "baseline_metrics": {metric_name: baseline},
                    "thresholds": {
                        metric_name: {
                            "warning_relative_delta": 0.01,
                            "critical_relative_delta": 0.02,
                        }
                    },
                },
                headers=self.reconciliation_headers,
                expected={201},
            )
            if consumed.get("drift_report_count") != 1:
                raise DeployedProofError(f"runtime event produced no DriftReport: {consumed!r}")
            drift = consumed["drift_reports"][0]
            drift_readback = self.http.request(
                "reconciliation",
                "GET",
                f"/api/reconciliation-drift/drift-reports/{drift['drift_report_id']}",
                headers=self.reconciliation_headers,
            )
            incident_cases = consumed.get("incident_cases") or []
            if incident_cases:
                incident_id = incident_cases[0].get("incident_id") or incident_cases[0].get("id")
            else:
                created_incident = self.http.request(
                    "incidents",
                    "POST",
                    "/api/incidents/consume-drift-report",
                    body={"drift_report": drift_readback},
                    expected={200, 201},
                )
                incident_id = created_incident["incident_id"]
            incident = self.http.request(
                "incidents", "GET", f"/api/incidents/{incident_id}"
            )
            if event["event_id"] not in incident.get("telemetry_event_ids", []):
                raise DeployedProofError("IncidentCase lost the real runtime telemetry event id")
            self.evidence.add_case(
                "loop_10_telemetry_reconciliation_incident",
                loop=10,
                trigger_id=event["event_id"],
                owner_worker=self.evidence.identities["reconciliation-drift-svc"],
                terminal_output_id=drift["drift_report_id"],
                authority_readback=drift_readback,
                next_consumer_readback=incident,
                started_at=loop10_started,
                compose_services=["telemetry", "reconciliation-drift-svc", "incidents"],
                assertions={
                    "incident_id": incident_id,
                    "real_runtime_event_preserved": True,
                    "severity": drift.get("severity"),
                },
            )

            loop11_started = _utc_now()
            threshold = self.http.request(
                "evolution",
                "POST",
                "/api/evolution/threshold-evaluate",
                body={
                    "snapshot": {
                        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
                        "signal_type": "governance_incident",
                        "metric_name": "severity1_incident_count",
                        "comparator": "gte",
                        "observed_value": 1,
                        "threshold_value": 1,
                        "window": "active-incident",
                    },
                    "context": {"has_active_runtime": True},
                },
                headers=self.evolution_headers,
            )
            sweep = self.http.request(
                "evolution",
                "POST",
                "/api/evolution/daily-sweep",
                body={
                    "incident_ids": [incident_id],
                    "sweep_id": f"sweep-{TASK_ID.lower()}-{self.suffix}",
                },
                headers=self.evolution_headers,
            )
            created = next(
                (item for item in sweep.get("items", []) if item.get("status") in {"created", "existing"}),
                None,
            )
            if created is None or not created.get("decision_id"):
                raise DeployedProofError(f"daily sweep produced no EvolutionDecision: {sweep!r}")
            decision = self.http.request(
                "evolution",
                "GET",
                f"/api/evolution/proposals/{created['decision_id']}",
                headers=self.evolution_headers,
            )
            if decision.get("linked_incident_id") != incident_id:
                raise DeployedProofError("EvolutionDecision lost IncidentCase lineage")
            self.evidence.add_case(
                "loop_11_evolution_decision",
                loop=11,
                trigger_id=incident_id,
                owner_worker=self.evidence.identities["evolution"],
                terminal_output_id=decision["decision_id"],
                authority_readback=decision,
                next_consumer_readback={
                    "proposed_action": threshold.get("proposed_action"),
                    "proposal_only": decision.get("metadata", {}).get("proposal_only"),
                    "runtime_binding_mutation_allowed": decision.get("metadata", {}).get(
                        "runtime_binding_mutation_allowed"
                    ),
                },
                started_at=loop11_started,
                compose_services=["incidents", "evolution"],
                assertions={
                    "incident_lineage_exact": True,
                    "proposal_only": True,
                    "runtime_mutation": False,
                },
            )

            loop12_started = _utc_now()

            def both_typed_targets_healthy() -> dict[str, Any] | None:
                payload = self._bff_health()
                targets = payload.get("data", {}).get("targets", {})
                runtime = targets.get("runtime-manager") or {}
                worker = targets.get("paper-fleet-reconciler") or {}
                if runtime.get("ok") is True and worker.get("ok") is True:
                    return payload
                return None

            healthy = _wait_until(
                "BFF typed Runtime Manager and fleet health",
                both_typed_targets_healthy,
                timeout=120,
            )
            healthy_targets = healthy["data"]["targets"]
            self.evidence.add_case(
                "loop_12_bff_typed_health",
                loop=12,
                trigger_id=f"bff-health-{self.suffix}",
                owner_worker=self.evidence.identities["operator-bff"],
                terminal_output_id="paper-fleet-reconciler",
                authority_readback=healthy_targets["paper-fleet-reconciler"],
                next_consumer_readback=healthy_targets["runtime-manager"],
                started_at=loop12_started,
                compose_services=["operator-bff", "paper-fleet-reconciler", "runtime-manager"],
                assertions={
                    "api_ready": True,
                    "typed_worker_ready": True,
                    "uses_generic_health_guess": False,
                },
            )

            worker_container = self._compose_container_id("paper-fleet-reconciler")
            _run(self.settings.compose_command("stop", "paper-fleet-reconciler"))
            try:
                def typed_failure() -> dict[str, Any] | None:
                    payload = self._bff_health()
                    targets = payload.get("data", {}).get("targets", {})
                    runtime = targets.get("runtime-manager") or {}
                    worker = targets.get("paper-fleet-reconciler") or {}
                    if runtime.get("ok") is True and worker.get("ok") is False:
                        return payload
                    return None

                failed = _wait_until(
                    "BFF worker failure while Runtime Manager API stays ready",
                    typed_failure,
                    timeout=120,
                )
                targets = failed["data"]["targets"]
                self.evidence.add_case(
                    "negative_typed_worker_failure",
                    loop=12,
                    trigger_id=worker_container,
                    owner_worker=self.evidence.identities["operator-bff"],
                    terminal_output_id="paper-fleet-reconciler:unhealthy",
                    authority_readback=targets["paper-fleet-reconciler"],
                    next_consumer_readback=targets["runtime-manager"],
                    started_at=loop12_started,
                    compose_services=["operator-bff", "paper-fleet-reconciler", "runtime-manager"],
                    assertions={
                        "api_ready_during_worker_failure": True,
                        "heartbeat_only_not_accepted": True,
                        "worker_failure_visible": True,
                    },
                )
            finally:
                _run(self.settings.compose_command("start", "paper-fleet-reconciler"))
                _wait_until(
                    "paper fleet reconciler recovery",
                    lambda: (
                        payload
                        if (
                            (payload := self.http.request(
                                "fleet",
                                "GET",
                                "/readyz",
                                expected={200, 503},
                            )).get("ready")
                            is True
                        )
                        else None
                    ),
                    timeout=120,
                )

            required_cases = {
                "loop_08_promotion_deployment",
                "loop_09_capital_artifact_execution",
                "loop_10_telemetry_reconciliation_incident",
                "loop_11_evolution_decision",
                "loop_12_bff_typed_health",
                "negative_missing_artifact_checksum",
                "negative_typed_worker_failure",
            }
            if set(self.evidence.cases) != required_cases:
                raise DeployedProofError(
                    f"evidence cases mismatch: {sorted(self.evidence.cases)}"
                )
            self.evidence.status = "passed"
            self.results = {
                "cases": self.evidence.cases,
                "positive": positive,
                "negative": negative,
            }
            return self.results
        except Exception as exc:
            self.evidence.status = "failed"
            self.evidence.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.evidence.write()


@pytest.fixture(scope="module")
def deployed_runtime_chain() -> dict[str, Any]:
    return RuntimeChain(Settings.from_env()).run()


def test_loop_08_approved_artifact_creates_exact_runtime_binding(
    deployed_runtime_chain: dict[str, Any],
) -> None:
    case = deployed_runtime_chain["cases"]["loop_08_promotion_deployment"]
    assert case["assertions"]["approved_artifact_exact"] is True
    assert case["authority_readback"]["status"] == "active"


def test_loop_09_current_artifact_drives_paper_runtime_execution(
    deployed_runtime_chain: dict[str, Any],
) -> None:
    case = deployed_runtime_chain["cases"]["loop_09_capital_artifact_execution"]
    assert case["assertions"]["artifact_signal_not_smoke"] is True
    assert case["authority_readback"]["event_type"] == "paper_fill_simulated"


def test_loop_10_runtime_event_creates_drift_and_incident(
    deployed_runtime_chain: dict[str, Any],
) -> None:
    case = deployed_runtime_chain["cases"]["loop_10_telemetry_reconciliation_incident"]
    assert case["assertions"]["real_runtime_event_preserved"] is True
    assert case["terminal_output_id"].startswith("drift-")


def test_loop_11_incident_creates_evolution_decision(
    deployed_runtime_chain: dict[str, Any],
) -> None:
    case = deployed_runtime_chain["cases"]["loop_11_evolution_decision"]
    assert case["assertions"]["proposal_only"] is True
    assert case["authority_readback"]["linked_incident_id"] == case["trigger_id"]


def test_loop_12_bff_reads_typed_worker_and_api_health(
    deployed_runtime_chain: dict[str, Any],
) -> None:
    case = deployed_runtime_chain["cases"]["loop_12_bff_typed_health"]
    assert case["authority_readback"]["ok"] is True
    assert case["next_consumer_readback"]["ok"] is True


def test_missing_artifact_checksum_degrades_without_signal(
    deployed_runtime_chain: dict[str, Any],
) -> None:
    case = deployed_runtime_chain["cases"]["negative_missing_artifact_checksum"]
    assert case["assertions"]["signals_enqueued_for_binding"] == 0
    assert case["next_consumer_readback"]["queue_depth"] == 0


def test_typed_worker_failure_does_not_mask_api_readiness(
    deployed_runtime_chain: dict[str, Any],
) -> None:
    case = deployed_runtime_chain["cases"]["negative_typed_worker_failure"]
    assert case["authority_readback"]["ok"] is False
    assert case["next_consumer_readback"]["ok"] is True
