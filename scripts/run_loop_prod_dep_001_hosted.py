#!/usr/bin/env python3
"""Capture hosted product evidence for LOOP-PROD-DEP-001.

The probe is intentionally paper-only.  It drives the deployed service
boundaries, records redacted request/response artifacts, and refuses to stop
the shared deployment consumer until unrelated pending outbox work has
converged.  It has two modes:

``predeploy``
    Read-only snapshot of the currently served compose identities and state.

``run``
    Exercise response-loss recovery, a real Runtime Manager timeout/restart,
    duplicate handling, DLQ/replay, compensation, kill-wins, and security
    negatives against the exact requested deployment SHA.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


TASK_ID = "LOOP-PROD-DEP-001"
COMPOSE_PROJECT = "pantheon"
RUNTIME_AUTH = {"Authorization": "Bearer loop-prod-dep-probe:operator:mfa"}
REDACTED_RUNTIME_AUTH = {"Authorization": "Bearer <redacted>"}
SERVICE_URLS = {
    "bff": "http://127.0.0.1:18001",
    "runtime": "http://127.0.0.1:18081",
    "governance": "http://127.0.0.1:18082",
    "registry": "http://127.0.0.1:18087",
    "capital": "http://127.0.0.1:18092",
    "deployment": "http://127.0.0.1:18095",
    "fleet": "http://127.0.0.1:18011",
    "broker": "http://127.0.0.1:18106",
}
SAFE_POOL_ID = "pool-rescue-0260531-6f175c5c"
SAFE_PERSONA_ID = "persona-20260531-6f175c5c"
SAFE_PCB_ID = "pcb-rescue-0260531-6f175c5c"
PARENT_ARTIFACT_ID = "artifact-tw-session-momentum-v1"
CONSUMER_NAME = "deployment-outbox-consumer"


class ProbeError(RuntimeError):
    """Hosted evidence could not be established safely."""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Recorder:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.http_dir = output_dir / "http"
        self.command_dir = output_dir / "commands"
        self.sequence = 0

    def _next_path(self, directory: Path, label: str) -> Path:
        self.sequence += 1
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)
        return directory / f"{self.sequence:03d}-{safe}.json"

    def http(
        self,
        label: str,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        recorded_headers: Mapping[str, str] | None = None,
        expected: set[int] | None = None,
        timeout: float = 15.0,
    ) -> tuple[int, Any]:
        started_at = utc_now()
        started = time.monotonic()
        status, payload = request_json(
            method,
            url,
            body=dict(body) if body is not None else None,
            headers=dict(headers or {}),
            timeout=timeout,
        )
        artifact = {
            "captured_at": utc_now(),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "label": label,
            "request": {
                "body": body,
                "headers": dict(recorded_headers or headers or {}),
                "method": method,
                "started_at": started_at,
                "url": url,
            },
            "response": {"payload": payload, "status": status},
        }
        write_json(self._next_path(self.http_dir, label), artifact)
        if expected is not None and status not in expected:
            raise ProbeError(
                f"{label} returned HTTP {status}, expected {sorted(expected)}: "
                f"{canonical_json(payload)}"
            )
        return status, payload

    def command(
        self,
        label: str,
        command: list[str],
        *,
        cwd: Path | None = None,
        expected_returncodes: set[int] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        started_at = utc_now()
        started = time.monotonic()
        proc = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            check=False,
            text=True,
            capture_output=True,
        )
        artifact = {
            "captured_at": utc_now(),
            "command": command,
            "cwd": str(cwd) if cwd else None,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "label": label,
            "returncode": proc.returncode,
            "started_at": started_at,
            "stderr": proc.stderr,
            "stdout": proc.stdout,
        }
        write_json(self._next_path(self.command_dir, label), artifact)
        allowed = expected_returncodes if expected_returncodes is not None else {0}
        if proc.returncode not in allowed:
            raise ProbeError(
                f"{label} returned {proc.returncode}: {proc.stderr or proc.stdout}"
            )
        return proc


def request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> tuple[int, Any]:
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    final_headers = {"Accept": "application/json", **(headers or {})}
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
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload
    except (TimeoutError, urllib.error.URLError) as exc:
        raise ProbeError(f"HTTP transport failed for {method} {url}: {exc}") from exc


def get_json(url: str, *, headers: Mapping[str, str] | None = None) -> Any:
    status, payload = request_json("GET", url, headers=dict(headers or {}))
    if status != 200:
        raise ProbeError(f"GET {url} returned HTTP {status}: {canonical_json(payload)}")
    return payload


def wait_until(
    description: str,
    predicate: Callable[[], Any],
    *,
    timeout: float,
    interval: float = 2.0,
) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (ProbeError, OSError, ValueError):
            last = None
        time.sleep(interval)
    raise ProbeError(f"Timed out waiting for {description}; last={last!r}")


def compose_command(compose_dir: Path, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        COMPOSE_PROJECT,
        "-f",
        str(compose_dir / "docker-compose.yml"),
        *args,
    ]


def inspect_hosted_identity(
    recorder: Recorder,
    compose_dir: Path,
    *,
    expected_sha: str | None,
    enforce: bool,
    label: str,
) -> dict[str, Any]:
    head = recorder.command(
        f"{label}-managed-worktree-head",
        ["git", "rev-parse", "HEAD"],
        cwd=compose_dir,
    ).stdout.strip()
    ps = recorder.command(
        f"{label}-compose-ps",
        compose_command(compose_dir, "ps", "--format", "json"),
        cwd=compose_dir,
    )
    services = [
        "operator-bff",
        "runtime-manager",
        "deployment",
        "deployment-outbox-consumer",
        "paper-fleet-reconciler",
        "broker",
    ]
    identities: dict[str, Any] = {}
    for service in services:
        container_id = recorder.command(
            f"{label}-{service}-container-id",
            compose_command(compose_dir, "ps", "-q", service),
            cwd=compose_dir,
        ).stdout.strip()
        if not container_id:
            raise ProbeError(f"Compose service {service!r} has no running container")
        raw = recorder.command(
            f"{label}-{service}-inspect",
            ["docker", "inspect", container_id],
        ).stdout
        inspected = json.loads(raw)[0]
        labels = inspected.get("Config", {}).get("Labels") or {}
        revision = labels.get("org.opencontainers.image.revision") or ""
        identities[service] = {
            "container_id": container_id,
            "container_name": inspected.get("Name", "").lstrip("/"),
            "image_id": inspected.get("Image"),
            "image_name": inspected.get("Config", {}).get("Image"),
            "oci_revision": revision,
            "state": inspected.get("State", {}).get("Status"),
        }
        if enforce and expected_sha and revision != expected_sha:
            raise ProbeError(
                f"{service} OCI revision mismatch: expected {expected_sha}, got {revision or '<missing>'}"
            )
    bff_version = get_json(f"{SERVICE_URLS['bff']}/bff/version")
    if enforce and expected_sha and bff_version.get("source_commit_sha") != expected_sha:
        raise ProbeError(
            "BFF source SHA mismatch: "
            f"expected {expected_sha}, got {bff_version.get('source_commit_sha')!r}"
        )
    if enforce and expected_sha and head != expected_sha:
        raise ProbeError(
            f"Managed deploy worktree mismatch: expected {expected_sha}, got {head}"
        )
    result = {
        "bff_version": bff_version,
        "compose_ps_stdout_sha256": hashlib.sha256(ps.stdout.encode()).hexdigest(),
        "containers": identities,
        "expected_sha": expected_sha,
        "managed_worktree_head": head,
        "observed_at": utc_now(),
    }
    write_json(recorder.output_dir / f"{label}-identity.json", result)
    return result


def health_snapshot(recorder: Recorder, label: str) -> dict[str, Any]:
    endpoints = {
        "bff_health": f"{SERVICE_URLS['bff']}/health",
        "bff_ready": f"{SERVICE_URLS['bff']}/readyz",
        "runtime_ready": f"{SERVICE_URLS['runtime']}/readyz",
        "deployment_ready": f"{SERVICE_URLS['deployment']}/readyz",
        "fleet_ready": f"{SERVICE_URLS['fleet']}/readyz",
        "broker_ready": f"{SERVICE_URLS['broker']}/readyz",
    }
    payloads: dict[str, Any] = {}
    for name, url in endpoints.items():
        headers = RUNTIME_AUTH if name == "runtime_ready" else None
        _, payload = recorder.http(
            f"{label}-{name}",
            "GET",
            url,
            headers=headers,
            recorded_headers=REDACTED_RUNTIME_AUTH if headers else None,
            expected={200},
        )
        payloads[name] = payload
    return payloads


def pending_outbox() -> list[dict[str, Any]]:
    payload = get_json(f"{SERVICE_URLS['deployment']}/api/deployment/outbox?status=pending")
    if not isinstance(payload, list):
        raise ProbeError("Deployment outbox response is not a list")
    return [item for item in payload if isinstance(item, dict)]


def wait_for_unrelated_outbox_to_converge(recorder: Recorder) -> list[dict[str, Any]]:
    def no_pending() -> Any:
        records = pending_outbox()
        return True if not records else None

    wait_until("existing shared deployment outbox to converge", no_pending, timeout=240)
    _, observed = recorder.http(
        "shared-outbox-preflight",
        "GET",
        f"{SERVICE_URLS['deployment']}/api/deployment/outbox?status=pending",
        expected={200},
    )
    if observed:
        raise ProbeError(
            "Refusing to stop the shared deployment consumer while pending outbox "
            f"records remain: {canonical_json(observed)}"
        )
    return observed


def mutate_artifact(
    recorder: Recorder,
    *,
    artifact_id: str,
    version: str,
    threshold: float,
    label: str,
) -> dict[str, Any]:
    _, payload = recorder.http(
        f"{label}-mutate-artifact",
        "POST",
        f"{SERVICE_URLS['registry']}/api/registry/strategy-artifacts/{PARENT_ARTIFACT_ID}/mutate",
        body={
            "new_artifact_id": artifact_id,
            "new_version": version,
            "parameter_updates": {"momentum_threshold": threshold},
            "source_run_ids": [TASK_ID, f"{TASK_ID}-{label}"],
        },
        expected={200},
    )
    return payload


def approve_artifact(
    recorder: Recorder,
    *,
    decision_id: str,
    artifact_id: str,
    version: str,
    label: str,
) -> dict[str, Any]:
    proposal = {
        "capital_pool_id": SAFE_POOL_ID,
        "decision_id": decision_id,
        "persona_id": SAFE_PERSONA_ID,
        "risk_level": "medium",
        "target_id": artifact_id,
        "target_type": "registry_entry",
        "target_version": version,
    }
    recorder.http(
        f"{label}-approval-propose",
        "POST",
        f"{SERVICE_URLS['governance']}/api/governance/approvals",
        body=proposal,
        expected={201},
    )
    recorder.http(
        f"{label}-approval-review",
        "POST",
        f"{SERVICE_URLS['governance']}/api/governance/approvals/{decision_id}/review",
        body={
            "actor_id": f"{TASK_ID.lower()}-reviewer",
            "actor_role": "governance_reviewer",
        },
        expected={200},
    )
    _, decided = recorder.http(
        f"{label}-approval-decide",
        "POST",
        f"{SERVICE_URLS['governance']}/api/governance/approvals/{decision_id}/decide",
        body={
            "actor_id": f"{TASK_ID.lower()}-risk-owner",
            "actor_role": "risk_owner",
            "outcome": "approved",
            "rationale": f"Paper-only hosted evidence for {TASK_ID}",
        },
        expected={200},
    )
    if decided.get("actor_id") != f"{TASK_ID.lower()}-risk-owner":
        raise ProbeError("Governance decision actor readback does not match the distinct decider")
    return decided


def create_plan(
    recorder: Recorder,
    *,
    plan_id: str,
    registry_entry: dict[str, Any],
    approval: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    body = {
        "approval_decision": approval,
        "approval_decision_id": approval["decision_id"],
        "capital_pool_id": SAFE_POOL_ID,
        "created_by": TASK_ID,
        "current_stage": "none",
        "metadata": {
            "evidence_mode": "hosted_product_level",
            "source_task_id": TASK_ID,
        },
        "plan_id": plan_id,
        "registry_entry": registry_entry,
        "sponsor_persona_id": SAFE_PERSONA_ID,
        "status": "approved",
        "target_stage": "paper",
        "rollback": {
            "target_artifact_id": PARENT_ARTIFACT_ID,
            "target_version": "1.0.0",
            "action_type": "replace",
            "reason": f"{TASK_ID} hosted evidence rollback parent",
        },
    }
    recorder.http(
        f"{label}-plan-validate",
        "POST",
        f"{SERVICE_URLS['deployment']}/api/deployment/plans/validate",
        body=body,
        expected={200},
    )
    _, created = recorder.http(
        f"{label}-plan-create",
        "POST",
        f"{SERVICE_URLS['deployment']}/api/deployment/plans",
        body=body,
        expected={201},
    )
    return created


def dispatch_plan(
    recorder: Recorder,
    *,
    plan_id: str,
    registry_entry: dict[str, Any],
    dispatch_body: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    payload = {**dispatch_body, "registry_entry": registry_entry}
    _, dispatched = recorder.http(
        label,
        "POST",
        f"{SERVICE_URLS['deployment']}/api/deployment/plans/{plan_id}/dispatch",
        body=payload,
        expected={200},
    )
    return dispatched


def dispatch_ids(dispatched: Mapping[str, Any]) -> tuple[str, str]:
    bootstrap = dispatched.get("deployment_saga")
    if not isinstance(bootstrap, Mapping):
        raise ProbeError("Dispatch response is missing deployment_saga")
    saga = bootstrap.get("saga")
    outbox = bootstrap.get("outbox_event")
    event = outbox.get("event") if isinstance(outbox, Mapping) else None
    saga_id = str(saga.get("saga_id") or "") if isinstance(saga, Mapping) else ""
    event_id = str(event.get("event_id") or "") if isinstance(event, Mapping) else ""
    if not saga_id or not event_id:
        raise ProbeError("Dispatch response is missing saga_id or outbox event_id")
    return saga_id, event_id


def direct_runtime_deploy(
    recorder: Recorder,
    *,
    plan: Mapping[str, Any],
    runtime_id: str,
    label: str,
) -> dict[str, Any]:
    body = {
        "allowed_deployment_scope": "paper",
        "approval_decision_id": plan["approval_decision_id"],
        "artifact_id": plan["artifact_id"],
        "artifact_version": plan["artifact_version"],
        "capital_pool_id": SAFE_POOL_ID,
        "loader_checks_passed": False,
        "metadata": {"source_task_id": TASK_ID},
        "persona_capital_binding_id": SAFE_PCB_ID,
        "persona_capital_binding_status": "active",
        "plan_id": plan["plan_id"],
        "plan_status": "approved",
        "runtime_id": runtime_id,
        "sponsor_persona_id": SAFE_PERSONA_ID,
        "strategy_id": plan["strategy_id"],
        "target_stage": "paper",
    }
    _, binding = recorder.http(
        label,
        "POST",
        f"{SERVICE_URLS['runtime']}/api/runtimes/deploy",
        body=body,
        headers=RUNTIME_AUTH,
        recorded_headers=REDACTED_RUNTIME_AUTH,
        expected={201},
    )
    return binding


def outbox_record(event_id: str) -> dict[str, Any] | None:
    records = get_json(f"{SERVICE_URLS['deployment']}/api/deployment/outbox")
    return next(
        (
            item
            for item in records
            if isinstance(item, dict)
            and (item.get("event") or {}).get("event_id") == event_id
        ),
        None,
    )


def plan_terminal_readback(plan_id: str, saga_id: str) -> dict[str, Any] | None:
    plan = get_json(f"{SERVICE_URLS['deployment']}/api/deployment/plans/{plan_id}")
    saga = get_json(f"{SERVICE_URLS['deployment']}/api/deployment/sagas/{saga_id}")
    if plan.get("status") == "executed" and saga.get("status") == "completed":
        return {"plan": plan, "saga": saga}
    return None


def compensation_terminal_readback(plan_id: str, saga_id: str) -> dict[str, Any] | None:
    plan = get_json(f"{SERVICE_URLS['deployment']}/api/deployment/plans/{plan_id}")
    saga = get_json(f"{SERVICE_URLS['deployment']}/api/deployment/sagas/{saga_id}")
    if (
        plan.get("status") == "aborted"
        and saga.get("status") == "aborted"
        and saga.get("current_step") == "compensated"
    ):
        return {"plan": plan, "saga": saga}
    return None


def capture_terminal_bundle(
    recorder: Recorder,
    *,
    plan_id: str,
    saga_id: str,
    binding_id: str,
    label: str,
) -> dict[str, Any]:
    paths = {
        "plan": f"{SERVICE_URLS['deployment']}/api/deployment/plans/{plan_id}",
        "saga": f"{SERVICE_URLS['deployment']}/api/deployment/sagas/{saga_id}",
        "projection": f"{SERVICE_URLS['deployment']}/api/deployment/projections/{plan_id}",
        "outbox": f"{SERVICE_URLS['deployment']}/api/deployment/outbox?aggregate_id={saga_id}",
        "inbox": (
            f"{SERVICE_URLS['deployment']}/api/deployment/inbox?"
            + urllib.parse.urlencode(
                {"aggregate_id": saga_id, "consumer_name": CONSUMER_NAME}
            )
        ),
        "binding": f"{SERVICE_URLS['runtime']}/api/runtime-bindings/{binding_id}",
        "fleet": f"{SERVICE_URLS['fleet']}/api/fleet/state",
    }
    bundle: dict[str, Any] = {}
    for name, url in paths.items():
        headers = RUNTIME_AUTH if name == "binding" else None
        _, payload = recorder.http(
            f"{label}-{name}",
            "GET",
            url,
            headers=headers,
            recorded_headers=REDACTED_RUNTIME_AUTH if headers else None,
            expected={200},
        )
        bundle[name] = payload
    return bundle


def artifact_index(output_dir: Path) -> dict[str, Any]:
    artifacts: dict[str, str] = {}
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "artifact-index.json":
            continue
        artifacts[str(path.relative_to(output_dir))] = sha256_file(path)
    result = {
        "algorithm": "sha256",
        "artifacts": artifacts,
        "generated_at": utc_now(),
        "task_id": TASK_ID,
    }
    write_json(output_dir / "artifact-index.json", result)
    return result


def predeploy(args: argparse.Namespace, recorder: Recorder) -> None:
    identity = inspect_hosted_identity(
        recorder,
        args.compose_dir,
        expected_sha=args.expected_sha or None,
        enforce=False,
        label="pre-deploy",
    )
    health = health_snapshot(recorder, "pre-deploy")
    _, outbox = recorder.http(
        "pre-deploy-outbox",
        "GET",
        f"{SERVICE_URLS['deployment']}/api/deployment/outbox",
        expected={200},
    )
    snapshot = {
        "captured_at": utc_now(),
        "health": health,
        "identity": identity,
        "outbox": outbox,
        "task_id": TASK_ID,
    }
    write_json(args.output_dir / "pre-deploy.json", snapshot)
    artifact_index(args.output_dir)


def run_probe(args: argparse.Namespace, recorder: Recorder) -> None:
    if not args.expected_sha:
        raise ProbeError("--expected-sha is required in run mode")
    if not (args.output_dir / "pre-deploy.json").exists():
        raise ProbeError("pre-deploy.json is required; run predeploy mode first")

    run_started_at = utc_now()
    identity = inspect_hosted_identity(
        recorder,
        args.compose_dir,
        expected_sha=args.expected_sha,
        enforce=True,
        label="capture-time",
    )
    health = health_snapshot(recorder, "capture-time")
    wait_for_unrelated_outbox_to_converge(recorder)

    # Canonical safe identities must still be exact before any mutation.
    safe_reads: dict[str, Any] = {}
    safe_paths = {
        "pool": f"{SERVICE_URLS['capital']}/api/capital-pools/{SAFE_POOL_ID}",
        "persona_binding": f"{SERVICE_URLS['capital']}/api/bindings/{SAFE_PCB_ID}",
        "admissibility": (
            f"{SERVICE_URLS['capital']}/api/bindings/admissibility?"
            + urllib.parse.urlencode(
                {
                    "persona_id": SAFE_PERSONA_ID,
                    "capital_pool_id": SAFE_POOL_ID,
                    "target_stage": "paper",
                }
            )
        ),
        "runtime_bindings": (
            f"{SERVICE_URLS['runtime']}/api/runtime-bindings?"
            + urllib.parse.urlencode({"pool_id": SAFE_POOL_ID})
        ),
        "paper_orders": (
            f"{SERVICE_URLS['broker']}/api/broker/paper/orders?"
            + urllib.parse.urlencode({"capital_pool_id": SAFE_POOL_ID})
        ),
    }
    for name, url in safe_paths.items():
        headers = RUNTIME_AUTH if name == "runtime_bindings" else None
        _, safe_reads[name] = recorder.http(
            f"safe-preflight-{name}",
            "GET",
            url,
            headers=headers,
            recorded_headers=REDACTED_RUNTIME_AUTH if headers else None,
            expected={200},
        )
    if safe_reads["pool"].get("status") != "active":
        raise ProbeError("Safe capital pool is not active")
    if safe_reads["pool"].get("single_runtime_enforced") is not True:
        raise ProbeError("Safe capital pool does not enforce one runtime")
    if safe_reads["persona_binding"].get("status") != "active":
        raise ProbeError("Safe PersonaCapitalBinding is not active")
    if safe_reads["persona_binding"].get("allowed_deployment_scope") != "paper":
        raise ProbeError("Safe PersonaCapitalBinding is not paper-only")
    if safe_reads["admissibility"].get("permitted") is not True:
        raise ProbeError("Safe paper capital identity is not admissible")
    if safe_reads["runtime_bindings"].get("count") not in {None, 0}:
        raise ProbeError("Safe capital pool already has a RuntimeBinding")
    if safe_reads["runtime_bindings"].get("bindings"):
        raise ProbeError("Safe capital pool already has a RuntimeBinding")

    suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    trace_id = str(uuid.uuid4())
    artifact_id = f"artifact-loop-prod-dep-{suffix}"
    version = f"1.14.{int(suffix[-6:])}"
    approval_id = f"approval-loop-prod-dep-{suffix}"
    plan_id = f"plan-loop-prod-dep-{suffix}"
    runtime_id = f"runtime-loop-prod-dep-{suffix}"
    dispatch_body = {
        "actor_id": TASK_ID,
        "correlation_id": f"correlation-{suffix}",
        "idempotency_key": f"{TASK_ID}:{suffix}:positive",
        "source_task_id": TASK_ID,
        "trace_id": trace_id,
        "workflow_id": "pantheon.loop-product-level.deployment",
    }

    recorder.command(
        "positive-stop-shared-consumer",
        compose_command(args.compose_dir, "stop", "deployment-outbox-consumer"),
        cwd=args.compose_dir,
    )
    try:
        mutated = mutate_artifact(
            recorder,
            artifact_id=artifact_id,
            version=version,
            threshold=0.01,
            label="positive",
        )
        approval = approve_artifact(
            recorder,
            decision_id=approval_id,
            artifact_id=artifact_id,
            version=version,
            label="positive",
        )
        _, approved_view = recorder.http(
            "positive-registry-advance",
            "POST",
            f"{SERVICE_URLS['registry']}/api/registry/strategy-artifacts/{artifact_id}/advance",
            body={
                "approval_decision_id": approval_id,
                "approver": f"{TASK_ID.lower()}-risk-owner",
                "target_state": "approved",
            },
            expected={200},
        )
        if mutated.get("entry", {}).get("artifact_state") != "candidate":
            raise ProbeError("Mutated artifact did not begin as a candidate")
        registry_entry = approved_view["entry"]
        plan = create_plan(
            recorder,
            plan_id=plan_id,
            registry_entry=registry_entry,
            approval=approval,
            label="positive",
        )
        dispatched = dispatch_plan(
            recorder,
            plan_id=plan_id,
            registry_entry=registry_entry,
            dispatch_body=dispatch_body,
            label="positive-dispatch",
        )
        saga_id, binding_event_id = dispatch_ids(dispatched)
        binding = direct_runtime_deploy(
            recorder,
            plan=plan,
            runtime_id=runtime_id,
            label="positive-downstream-success-before-receipt",
        )
        binding_id = str(binding.get("binding_id") or "")
        if not binding_id:
            raise ProbeError("Direct Runtime Manager deploy returned no binding_id")

        recorder.command(
            "positive-pause-runtime-manager-for-timeout",
            compose_command(args.compose_dir, "pause", "runtime-manager"),
            cwd=args.compose_dir,
        )
        runtime_paused = True
        try:
            recorder.command(
                "positive-start-consumer-during-runtime-timeout",
                ["docker", "start", identity["containers"]["deployment-outbox-consumer"]["container_id"]],
            )

            def timeout_recorded() -> Any:
                record = outbox_record(binding_event_id)
                if record and int(record.get("delivery_attempts") or 0) >= 1:
                    return record
                return None

            timed_out_record = wait_until(
                "outbox retry after frozen Runtime Manager transport",
                timeout_recorded,
                timeout=45,
                interval=2,
            )
            write_json(args.output_dir / "timeout-retry-readback.json", timed_out_record)
        finally:
            if runtime_paused:
                recorder.command(
                    "positive-unpause-runtime-manager",
                    compose_command(args.compose_dir, "unpause", "runtime-manager"),
                    cwd=args.compose_dir,
                )

        terminal = wait_until(
            "positive DeploymentPlan and saga terminal convergence",
            lambda: plan_terminal_readback(plan_id, saga_id),
            timeout=240,
            interval=3,
        )
        write_json(args.output_dir / "positive-terminal-convergence.json", terminal)
        positive_bundle = capture_terminal_bundle(
            recorder,
            plan_id=plan_id,
            saga_id=saga_id,
            binding_id=binding_id,
            label="positive-terminal",
        )

        duplicate = dispatch_plan(
            recorder,
            plan_id=plan_id,
            registry_entry=registry_entry,
            dispatch_body=dispatch_body,
            label="positive-duplicate-dispatch",
        )
        if duplicate.get("replayed") is not True:
            raise ProbeError("Duplicate dispatch did not return replayed=true")
        duplicate_saga_id, duplicate_event_id = dispatch_ids(duplicate)
        if (duplicate_saga_id, duplicate_event_id) != (saga_id, binding_event_id):
            raise ProbeError("Duplicate dispatch changed the canonical saga or first event identity")

        recorder.http(
            "positive-duplicate-inbox-consume",
            "POST",
            f"{SERVICE_URLS['deployment']}/api/deployment/outbox/{binding_event_id}/consume",
            body={"consumer_name": CONSUMER_NAME},
            expected={200},
        )

        recorder.command(
            "restart-runtime-manager-and-consumer",
            compose_command(
                args.compose_dir,
                "restart",
                "runtime-manager",
                "deployment-outbox-consumer",
            ),
            cwd=args.compose_dir,
        )
        wait_until(
            "Runtime Manager health after process restart",
            lambda: request_json(
                "GET", f"{SERVICE_URLS['runtime']}/readyz", headers=RUNTIME_AUTH
            )[0]
            == 200,
            timeout=90,
        )
        post_restart_bundle = capture_terminal_bundle(
            recorder,
            plan_id=plan_id,
            saga_id=saga_id,
            binding_id=binding_id,
            label="post-restart-terminal",
        )
        if post_restart_bundle["binding"].get("status") != "active":
            raise ProbeError("RuntimeBinding did not remain active across Runtime Manager restart")

        _, killed = recorder.http(
            "kill-wins-dispatch",
            "POST",
            f"{SERVICE_URLS['runtime']}/api/kill-switch/dispatch",
            body={
                "action_override": "pause",
                "actor_id": f"{TASK_ID.lower()}-operator",
                "binding_id": binding_id,
                "capital_pool_id": SAFE_POOL_ID,
                "idempotency_key": f"{TASK_ID}:{suffix}:kill",
                "reason": "operator_emergency_stop",
                "severity": 1,
            },
            headers=RUNTIME_AUTH,
            recorded_headers=REDACTED_RUNTIME_AUTH,
            expected={200},
        )
        if (killed.get("binding_action") or {}).get("binding", {}).get("status") != "paused":
            raise ProbeError("Kill switch did not return paused RuntimeBinding readback")
        wait_until(
            "paused RuntimeBinding after kill switch",
            lambda: (
                payload
                if (
                    (payload := get_json(
                        f"{SERVICE_URLS['runtime']}/api/runtime-bindings/{binding_id}",
                        headers=RUNTIME_AUTH,
                    )).get("status")
                    == "paused"
                )
                else None
            ),
            timeout=45,
        )
        kill_duplicate = dispatch_plan(
            recorder,
            plan_id=plan_id,
            registry_entry=registry_entry,
            dispatch_body=dispatch_body,
            label="kill-wins-duplicate-dispatch",
        )
        if kill_duplicate.get("replayed") is not True:
            raise ProbeError("Post-kill duplicate dispatch was not idempotent")
        _, paused_readback = recorder.http(
            "kill-wins-paused-readback",
            "GET",
            f"{SERVICE_URLS['runtime']}/api/runtime-bindings/{binding_id}",
            headers=RUNTIME_AUTH,
            recorded_headers=REDACTED_RUNTIME_AUTH,
            expected={200},
        )
        if paused_readback.get("status") != "paused":
            raise ProbeError("Duplicate dispatch overrode the kill-switch pause")

        # Security negatives.  Every call is required to leave the binding set
        # unchanged and the live broker disabled.
        recorder.http(
            "security-missing-auth",
            "GET",
            f"{SERVICE_URLS['runtime']}/api/runtime-bindings",
            expected={401},
        )
        recorder.http(
            "security-wrong-role",
            "GET",
            f"{SERVICE_URLS['runtime']}/api/runtime-bindings",
            headers={"Authorization": "Bearer evidence-viewer:viewer"},
            recorded_headers=REDACTED_RUNTIME_AUTH,
            expected={403},
        )
        recorder.http(
            "security-invalid-mfa",
            "GET",
            f"{SERVICE_URLS['runtime']}/api/runtime-bindings",
            headers={
                "Authorization": "Bearer evidence-operator:operator",
                "X-MFA-Token": "invalid",
            },
            recorded_headers={
                "Authorization": "Bearer <redacted>",
                "X-MFA-Token": "<redacted-invalid-proof>",
            },
            expected={400, 401},
        )
        live_body = {
            "allowed_deployment_scope": "live",
            "approval_decision_id": approval_id,
            "artifact_id": artifact_id,
            "artifact_version": version,
            "capital_pool_id": SAFE_POOL_ID,
            "loader_checks_passed": True,
            "persona_capital_binding_id": SAFE_PCB_ID,
            "persona_capital_binding_status": "active",
            "plan_id": plan_id,
            "plan_status": "executed",
            "promotion_gate": {
                "canary_observation_ref": "caller-authored",
                "operator_approval_ref": "caller-authored",
                "risk_owner_approval_ref": "caller-authored",
            },
            "runtime_id": f"live-{runtime_id}",
            "sponsor_persona_id": SAFE_PERSONA_ID,
            "strategy_id": plan["strategy_id"],
            "target_stage": "live",
        }
        recorder.http(
            "security-live-missing-governed-two-person-proof",
            "POST",
            f"{SERVICE_URLS['runtime']}/api/runtimes/deploy",
            body=live_body,
            headers={"Authorization": "Bearer evidence-operator:operator"},
            recorded_headers=REDACTED_RUNTIME_AUTH,
            expected={422},
        )
        recorder.http(
            "security-live-broker-disabled",
            "POST",
            f"{SERVICE_URLS['broker']}/api/broker/live/orders",
            body={},
            expected={403},
        )

        # Hosted DLQ/replay plus canonical compensation.  The plan embeds an
        # approved-looking copy for planner construction, while the Registry
        # authority deliberately remains candidate.  Runtime mutation must not
        # occur; compensation must abort the plan after the replayed event.
        recorder.command(
            "failure-stop-shared-consumer",
            compose_command(args.compose_dir, "stop", "deployment-outbox-consumer"),
            cwd=args.compose_dir,
        )
        failure_artifact_id = f"artifact-loop-prod-dep-failure-{suffix}"
        failure_version = f"1.15.{int(suffix[-6:])}"
        failure_approval_id = f"approval-loop-prod-dep-failure-{suffix}"
        failure_plan_id = f"plan-loop-prod-dep-failure-{suffix}"
        candidate_view = mutate_artifact(
            recorder,
            artifact_id=failure_artifact_id,
            version=failure_version,
            threshold=0.02,
            label="failure",
        )
        failure_approval = approve_artifact(
            recorder,
            decision_id=failure_approval_id,
            artifact_id=failure_artifact_id,
            version=failure_version,
            label="failure",
        )
        embedded = copy.deepcopy(candidate_view["entry"])
        embedded.update(
            {
                "approval_decision_id": failure_approval_id,
                "approved_at": failure_approval.get("decided_at"),
                "approver": f"{TASK_ID.lower()}-risk-owner",
                "artifact_state": "approved",
            }
        )
        create_plan(
            recorder,
            plan_id=failure_plan_id,
            registry_entry=embedded,
            approval=failure_approval,
            label="failure",
        )
        failure_dispatch_body = {
            "actor_id": TASK_ID,
            "correlation_id": f"failure-correlation-{suffix}",
            "idempotency_key": f"{TASK_ID}:{suffix}:failure",
            "source_task_id": TASK_ID,
            "trace_id": str(uuid.uuid4()),
            "workflow_id": "pantheon.loop-product-level.deployment-failure",
        }
        failure_dispatch = dispatch_plan(
            recorder,
            plan_id=failure_plan_id,
            registry_entry=embedded,
            dispatch_body=failure_dispatch_body,
            label="failure-dispatch",
        )
        failure_saga_id, failure_event_id = dispatch_ids(failure_dispatch)
        for attempt in (1, 2):
            recorder.http(
                f"failure-manual-delivery-attempt-{attempt}",
                "POST",
                f"{SERVICE_URLS['deployment']}/api/deployment/outbox/{failure_event_id}/failure",
                body={
                    "consumer_name": CONSUMER_NAME,
                    "max_attempts": 2,
                    "reason": "hosted transport timeout drill",
                    "retry_delay_seconds": 0,
                    "retryable": True,
                },
                expected={200},
            )
        dead_letter = outbox_record(failure_event_id)
        if not dead_letter or dead_letter.get("status") != "dead_lettered":
            raise ProbeError("Failure event did not enter the DLQ")
        write_json(args.output_dir / "dlq-readback.json", dead_letter)
        _, replayed = recorder.http(
            "failure-dlq-replay",
            "POST",
            f"{SERVICE_URLS['deployment']}/api/deployment/outbox/{failure_event_id}/replay",
            body={"reason": f"{TASK_ID} hosted replay proof"},
            expected={200},
        )
        if replayed.get("replayed") is not True:
            raise ProbeError("DLQ replay did not return replayed=true")
        recorder.command(
            "failure-start-shared-consumer",
            ["docker", "start", identity["containers"]["deployment-outbox-consumer"]["container_id"]],
        )
        compensated = wait_until(
            "unapproved artifact compensation",
            lambda: compensation_terminal_readback(failure_plan_id, failure_saga_id),
            timeout=180,
            interval=3,
        )
        write_json(args.output_dir / "compensation-terminal-readback.json", compensated)
        _, failure_projection = recorder.http(
            "failure-compensation-projection",
            "GET",
            f"{SERVICE_URLS['deployment']}/api/deployment/projections/{failure_plan_id}",
            expected={200},
        )
        _, failure_outbox = recorder.http(
            "failure-compensation-outbox",
            "GET",
            f"{SERVICE_URLS['deployment']}/api/deployment/outbox?aggregate_id={failure_saga_id}",
            expected={200},
        )
        _, failure_inbox = recorder.http(
            "failure-compensation-inbox",
            "GET",
            f"{SERVICE_URLS['deployment']}/api/deployment/inbox?"
            + urllib.parse.urlencode(
                {"aggregate_id": failure_saga_id, "consumer_name": CONSUMER_NAME}
            ),
            expected={200},
        )
        _, failure_bindings = recorder.http(
            "failure-no-runtime-binding",
            "GET",
            f"{SERVICE_URLS['runtime']}/api/runtime-bindings?"
            + urllib.parse.urlencode({"plan_id": failure_plan_id}),
            headers=RUNTIME_AUTH,
            recorded_headers=REDACTED_RUNTIME_AUTH,
            expected={200},
        )
        if failure_bindings.get("bindings"):
            raise ProbeError("Unapproved canonical artifact created a RuntimeBinding")

        _, post_paper_orders = recorder.http(
            "safe-postflight-paper-orders",
            "GET",
            f"{SERVICE_URLS['broker']}/api/broker/paper/orders?"
            + urllib.parse.urlencode({"capital_pool_id": SAFE_POOL_ID}),
            expected={200},
        )
        if post_paper_orders.get("orders"):
            raise ProbeError("Hosted deployment proof unexpectedly created paper orders")

        final_bindings = get_json(
            f"{SERVICE_URLS['runtime']}/api/runtime-bindings?"
            + urllib.parse.urlencode({"pool_id": SAFE_POOL_ID}),
            headers=RUNTIME_AUTH,
        )
        matching = [
            item
            for item in final_bindings.get("bindings", [])
            if item.get("binding_id") == binding_id
        ]
        if len(matching) != 1 or matching[0].get("status") != "paused":
            raise ProbeError("Final kill-wins RuntimeBinding state is not exactly one paused binding")

        summary = {
            "deployment": {
                "expected_sha": args.expected_sha,
                "identity": identity,
                "run_id": args.deploy_run_id,
                "run_url": args.deploy_run_url,
            },
            "health": health,
            "identities": {
                "approval_decision_id": approval_id,
                "artifact_id": artifact_id,
                "binding_id": binding_id,
                "capital_pool_id": SAFE_POOL_ID,
                "failure_artifact_id": failure_artifact_id,
                "failure_plan_id": failure_plan_id,
                "failure_saga_id": failure_saga_id,
                "persona_capital_binding_id": SAFE_PCB_ID,
                "plan_id": plan_id,
                "runtime_id": runtime_id,
                "saga_id": saga_id,
            },
            "observed_at": utc_now(),
            "positive_terminal": positive_bundle,
            "post_restart_terminal": post_restart_bundle,
            "results": {
                "compensation": "pass",
                "dlq_replay": "pass",
                "downstream_success_before_receipt": "pass",
                "duplicate_safety": "pass",
                "environment_boundary": "pass",
                "failure_degraded_behavior": "pass",
                "kill_wins": "pass",
                "live_broker_disabled": "pass",
                "no_live_capital": "pass",
                "request_receipt_downstream_correlation": "pass",
                "restart_recovery": "pass",
                "security_negatives": "pass",
                "timeout_retry": "pass",
                "two_person_governance": "pass",
            },
            "run_started_at": run_started_at,
            "task_id": TASK_ID,
            "terminal_failure_projection": failure_projection,
            "terminal_failure_outbox": failure_outbox,
            "terminal_failure_inbox": failure_inbox,
        }
        write_json(args.output_dir / "hosted-proof.json", summary)
        recorder.command(
            "deployment-consumer-logs",
            [
                "docker",
                "logs",
                "--tail",
                "400",
                identity["containers"]["deployment-outbox-consumer"]["container_id"],
            ],
            expected_returncodes={0},
        )
    finally:
        # A failed probe must never leave the supervised consumer stopped or the
        # Runtime Manager paused.  Unpause is allowed to return non-zero when it
        # was already running.
        recorder.command(
            "cleanup-unpause-runtime-manager",
            compose_command(args.compose_dir, "unpause", "runtime-manager"),
            cwd=args.compose_dir,
            expected_returncodes={0, 1},
        )
        recorder.command(
            "cleanup-start-shared-consumer",
            ["docker", "start", identity["containers"]["deployment-outbox-consumer"]["container_id"]],
            expected_returncodes={0},
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("predeploy", "run"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--compose-dir",
        type=Path,
        default=Path(
            "/home/lupin/pantheon-ci-deploy/managed-deploy-worktrees/dev-root"
        ),
    )
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--deploy-run-id", default="")
    parser.add_argument("--deploy-run-url", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.compose_dir = args.compose_dir.expanduser().resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(args.output_dir)
    metadata = {
        "compose_dir": str(args.compose_dir),
        "deploy_run_id": args.deploy_run_id,
        "deploy_run_url": args.deploy_run_url,
        "expected_sha": args.expected_sha,
        "mode": args.mode,
        "started_at": utc_now(),
        "task_id": TASK_ID,
    }
    write_json(args.output_dir / f"{args.mode}-metadata.json", metadata)
    try:
        if args.mode == "predeploy":
            predeploy(args, recorder)
        else:
            run_probe(args, recorder)
        artifact_index(args.output_dir)
    except Exception as exc:  # noqa: BLE001 - evidence must preserve exact failure
        write_json(
            args.output_dir / f"{args.mode}-failure.json",
            {
                "error": f"{type(exc).__name__}: {exc}",
                "failed_at": utc_now(),
                "task_id": TASK_ID,
            },
        )
        artifact_index(args.output_dir)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
