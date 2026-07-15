#!/usr/bin/env python3
"""Fail-closed hosted verifier for LOOP-PROD-MAI-001.

The verifier deliberately uses only the Python standard library.  ``preflight``
is read-only and stops at the strict-auth posture gate before loading secrets.
``run`` performs the governed debug/repair/dev-bridge lifecycle, but only after
preflight succeeds and the operator explicitly passes ``--allow-mutations``.

Secrets are accepted from environment variables only.  Every captured artifact
is recursively redacted, indexed, checksummed, and immediately re-verified.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


TASK_ID = "LOOP-PROD-MAI-001"
DEFAULT_BFF_BASE_URL = "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io"
DEFAULT_FE_DEPLOYMENT_URL = (
    "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json"
)
FIXED_STUB_BEARER = "loop-prod-mai-fixed:operator:mfa:assistant.kernel.repair"
REDACTED = "<redacted>"
SUCCESS_PROVIDER_STATES = {"completed", "ok", "ready", "succeeded", "success"}
PROCESSING_PROVIDER_STATES = {"accepted", "pending", "processing", "queued", "running"}
REJECTED_HTTP_STATUSES = {400, 401, 403, 409, 422}
SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "passphrase",
    "password",
    "client_secret",
    "access_token",
    "refresh_token",
    "id_token",
    "private_key",
    "credential",
)
SENSITIVE_EXACT_KEYS = {
    # A repair receipt is a short-lived signed capability.  Keep queue/drain
    # receipt envelopes visible for correlation, but never persist the exact
    # browser-forwarded capability value.
    "receipt",
    "repair_receipt",
    "repairreceipt",
}
EVIDENCE_MANIFEST = "evidence.json"
EVIDENCE_CHECKSUM = "evidence.sha256"
LIFECYCLE_HOOK_VERSION = "pantheon.management-ai.lifecycle-hook.v1"
BRIDGE_ADMISSION_VERSION = "pantheon.assistant-dev-bridge-admission.v1"


class VerificationError(RuntimeError):
    """A required hosted proof could not be established."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _ascii_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bridge_task_spec(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(task.get("id") or ""),
        "title": str(task.get("title") or ""),
        "owner": str(task.get("owner") or ""),
        "reviewer": str(task.get("reviewer") or ""),
        "phase": task.get("phase"),
        "depends_on": list(task.get("depends_on") or task.get("dependsOn") or []),
        "artifacts": list(task.get("artifacts") or []),
        "acceptance": list(task.get("acceptance") or []),
        "summary": task.get("summary"),
    }


def bridge_packet_digest(packet: Mapping[str, Any]) -> str:
    """Reproduce dev_bridge_signer.packet_digest without importing BFF code."""

    actor = packet.get("actor") if isinstance(packet.get("actor"), Mapping) else {}
    documents = packet.get("documents") if isinstance(packet.get("documents"), list) else []
    tasks = packet.get("tasks") if isinstance(packet.get("tasks"), list) else []
    constraints = (
        packet.get("constraints") if isinstance(packet.get("constraints"), Mapping) else {}
    )
    normalized = {
        "version": packet.get("version") or "pantheon.assistant.dev-task.v1",
        "packet_id": packet.get("packet_id") or packet.get("packetId"),
        "intent": packet.get("intent") or "generate_sa_sd_and_dispatch",
        "emitted_at": packet.get("emitted_at") or packet.get("emittedAt"),
        "actor": {
            "id": actor.get("id"),
            "roles": list(actor.get("roles") or []),
            "capabilities": list(actor.get("capabilities") or []),
        },
        "mode": packet.get("mode"),
        "source_conversation_id": packet.get("source_conversation_id")
        or packet.get("sourceConversationId"),
        "source_turn_ids": list(
            packet.get("source_turn_ids") or packet.get("sourceTurnIds") or []
        ),
        "documents": [
            {
                "path": item.get("path"),
                "kind": item.get("kind") or "SA_SD_PLAN",
                "source_refs": list(item.get("source_refs") or item.get("sourceRefs") or []),
            }
            for item in documents
            if isinstance(item, Mapping)
        ],
        "tasks": [bridge_task_spec(item) for item in tasks if isinstance(item, Mapping)],
        "constraints": {
            "allowed_repos": list(
                constraints.get("allowed_repos") or constraints.get("allowedRepos") or ["pantheon"]
            ),
            "requires_branch_pr_merge": bool(
                constraints.get("requires_branch_pr_merge")
                if "requires_branch_pr_merge" in constraints
                else constraints.get("requiresBranchPrMerge", True)
            ),
            "no_direct_shell_from_web": bool(
                constraints.get("no_direct_shell_from_web")
                if "no_direct_shell_from_web" in constraints
                else constraints.get("noDirectShellFromWeb", True)
            ),
        },
        "audit_conversation_href": packet.get("audit_conversation_href")
        or packet.get("auditConversationHref"),
    }
    return _ascii_json_hash(normalized)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redact_string(value: str, secrets: Iterable[str]) -> str:
    clean = value
    clean = re.sub(r"(?i)\bBearer\s+[^\s,;]+", f"Bearer {REDACTED}", clean)
    clean = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", rf"\1{REDACTED}@", clean)
    for secret in secrets:
        if secret:
            clean = clean.replace(secret, REDACTED)
    return clean


def redact(value: Any, *, secrets: Iterable[str] = ()) -> Any:
    """Recursively redact credentials while retaining evidence shape."""

    secret_values = tuple(str(item) for item in secrets if str(item))
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = str(key)
            lowered = clean_key.lower()
            if lowered in SENSITIVE_EXACT_KEYS or any(
                part in lowered for part in SENSITIVE_KEY_PARTS
            ):
                result[clean_key] = REDACTED
            else:
                result[clean_key] = redact(item, secrets=secret_values)
        return result
    if isinstance(value, (list, tuple, set)):
        return [redact(item, secrets=secret_values) for item in value]
    if isinstance(value, bytes):
        return _redact_string(value.decode("utf-8", errors="replace"), secret_values)
    if isinstance(value, str):
        return _redact_string(value, secret_values)
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@dataclasses.dataclass(frozen=True)
class HttpResult:
    status: int
    payload: Any
    headers: Mapping[str, str] = dataclasses.field(default_factory=dict)


class UrllibTransport:
    """Minimal injectable HTTP transport."""

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResult:
        encoded = None if body is None else json.dumps(dict(body)).encode("utf-8")
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
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    payload = {"raw": raw}
                return HttpResult(
                    status=response.status,
                    payload=payload,
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return HttpResult(
                status=exc.code,
                payload=payload,
                headers=dict(exc.headers.items()) if exc.headers else {},
            )
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise VerificationError(
                "HTTP_TRANSPORT_FAILED",
                f"HTTP transport failed for {method} {url}: {exc}",
                details={"method": method, "url": url},
            ) from exc


class SubprocessRunner:
    """Injectable non-shell command runner for restart/readback hooks."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(command),
                cwd=str(cwd) if cwd else None,
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VerificationError(
                "COMMAND_TRANSPORT_FAILED",
                f"Command hook failed: {exc}",
                details={"command": list(command), "cwd": str(cwd) if cwd else None},
            ) from exc


class ArtifactRecorder:
    """Append-only redacted HTTP/command recorder with an integrity index."""

    def __init__(self, output_dir: Path, *, secrets: Iterable[str] = ()) -> None:
        self.output_dir = output_dir.expanduser().resolve()
        self.http_dir = self.output_dir / "http"
        self.command_dir = self.output_dir / "commands"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if any(
            (self.output_dir / name).exists()
            for name in (
                EVIDENCE_MANIFEST,
                EVIDENCE_CHECKSUM,
                # Older verifier runs used these names.  Treat them as a
                # finalized directory too rather than appending mixed schemas.
                "artifact-index.json",
                "artifact-index.sha256",
            )
        ):
            raise VerificationError(
                "ARTIFACT_RUN_ALREADY_FINALIZED",
                "Refusing to append to or overwrite a finalized evidence run directory",
                details={"output_dir": str(self.output_dir)},
            )
        existing_sequences = []
        for path in self.output_dir.rglob("*.json"):
            match = re.match(r"^(\d+)-", path.name)
            if match:
                existing_sequences.append(int(match.group(1)))
        self._sequence = max(existing_sequences, default=0)
        self._secrets = {str(item) for item in secrets if str(item)}
        self._finalized = False

    def add_secrets(self, *values: str) -> None:
        self._secrets.update(str(value) for value in values if str(value))

    def _next_path(self, directory: Path, label: str) -> Path:
        self._sequence += 1
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.") or "artifact"
        return directory / f"{self._sequence:03d}-{safe}.json"

    def record(self, label: str, value: Any, *, directory: Path | None = None) -> Path:
        target = self._next_path(directory or self.output_dir, label)
        write_json(target, redact(value, secrets=self._secrets))
        return target

    def http(
        self,
        transport: Any,
        label: str,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        expected: set[int] | None = None,
        timeout: float = 30.0,
    ) -> HttpResult:
        started = time.monotonic()
        started_at = utc_now()
        try:
            result = transport.request(
                method,
                url,
                body=body,
                headers=headers,
                timeout=timeout,
            )
            response_payload: Any = {
                "status": result.status,
                "headers": dict(result.headers),
                "payload": result.payload,
            }
        except Exception as exc:
            response_payload = {
                "transport_error": f"{type(exc).__name__}: {exc}",
                "error": exc.to_dict() if isinstance(exc, VerificationError) else {},
            }
            self.record(
                label,
                {
                    "captured_at": utc_now(),
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "request": {
                        "method": method,
                        "url": url,
                        "headers": dict(headers or {}),
                        "body": body,
                        "started_at": started_at,
                    },
                    "response": response_payload,
                },
                directory=self.http_dir,
            )
            raise

        self.record(
            label,
            {
                "captured_at": utc_now(),
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "request": {
                    "method": method,
                    "url": url,
                    "headers": dict(headers or {}),
                    "body": body,
                    "started_at": started_at,
                },
                "response": response_payload,
            },
            directory=self.http_dir,
        )
        if expected is not None and result.status not in expected:
            raise VerificationError(
                "UNEXPECTED_HTTP_STATUS",
                f"{label} returned HTTP {result.status}; expected {sorted(expected)}",
                details={
                    "label": label,
                    "status": result.status,
                    "expected": sorted(expected),
                    "payload": redact(result.payload, secrets=self._secrets),
                },
            )
        return result

    def command(
        self,
        runner: Any,
        label: str,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        expected: set[int] | None = None,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[str]:
        for secret in self._secrets:
            if secret and any(secret in str(part) for part in command):
                raise VerificationError(
                    "SECRET_IN_COMMAND_HOOK",
                    f"Command hook {label!r} contains a configured secret",
                )
        started = time.monotonic()
        started_at = utc_now()
        process = runner.run(command, cwd=cwd, timeout=timeout)
        self.record(
            label,
            {
                "captured_at": utc_now(),
                "command": list(command),
                "cwd": str(cwd) if cwd else None,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "returncode": process.returncode,
                "started_at": started_at,
                "stdout": process.stdout,
                "stderr": process.stderr,
            },
            directory=self.command_dir,
        )
        allowed = expected if expected is not None else {0}
        if process.returncode not in allowed:
            raise VerificationError(
                "COMMAND_HOOK_FAILED",
                f"{label} returned {process.returncode}; expected {sorted(allowed)}",
                details={"label": label, "returncode": process.returncode},
            )
        return process

    def blocker(self, error: VerificationError, *, phase: str) -> Path:
        return self.record(
            "blocker",
            {
                "status": "blocked",
                "task_id": TASK_ID,
                "phase": phase,
                "recorded_at": utc_now(),
                "error": error.to_dict(),
            },
        )

    def finalize(self) -> tuple[Path, Path]:
        if self._finalized:
            index = self.output_dir / EVIDENCE_MANIFEST
            checksum = self.output_dir / EVIDENCE_CHECKSUM
            self.verify_checksum(index, checksum)
            return index, checksum

        ignored = {
            EVIDENCE_MANIFEST,
            EVIDENCE_CHECKSUM,
            "artifact-index.json",
            "artifact-index.sha256",
        }
        artifacts = []
        for path in sorted(self.output_dir.rglob("*")):
            if not path.is_file() or path.name in ignored:
                continue
            artifacts.append(
                {
                    "path": path.relative_to(self.output_dir).as_posix(),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
        index_path = self.output_dir / EVIDENCE_MANIFEST
        if index_path.exists():
            raise VerificationError(
                "ARTIFACT_RUN_ALREADY_FINALIZED",
                "Refusing to overwrite an existing evidence manifest",
            )
        write_json(
            index_path,
            {
                "version": "pantheon.loop-prod-mai.evidence.v1",
                "task_id": TASK_ID,
                "generated_at": utc_now(),
                "artifact_count": len(artifacts),
                "artifacts": artifacts,
            },
        )
        digest = sha256_file(index_path)
        checksum_path = self.output_dir / EVIDENCE_CHECKSUM
        try:
            with checksum_path.open("x", encoding="utf-8") as handle:
                handle.write(f"{digest}  {EVIDENCE_MANIFEST}\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise VerificationError(
                "ARTIFACT_RUN_ALREADY_FINALIZED",
                "Refusing to overwrite an existing evidence checksum",
            ) from exc
        self.verify_checksum(index_path, checksum_path)
        self._finalized = True
        return index_path, checksum_path

    @staticmethod
    def verify_checksum(index_path: Path, checksum_path: Path) -> str:
        line = checksum_path.read_text(encoding="utf-8").strip()
        checksum_parts = line.split()
        expected = checksum_parts[0] if len(checksum_parts) == 2 else ""
        named_manifest = checksum_parts[1] if len(checksum_parts) == 2 else ""
        actual = sha256_file(index_path)
        if (
            not expected
            or expected != actual
            or named_manifest != index_path.name
            or index_path.name != EVIDENCE_MANIFEST
        ):
            raise VerificationError(
                "ARTIFACT_CHECKSUM_MISMATCH",
                "Artifact index checksum did not verify immediately after write",
                details={
                    "expected": expected,
                    "actual": actual,
                    "named_manifest": named_manifest,
                    "required_manifest": EVIDENCE_MANIFEST,
                },
            )
        try:
            manifest = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VerificationError(
                "ARTIFACT_MANIFEST_INVALID",
                "Evidence manifest is not valid JSON",
            ) from exc
        artifacts = manifest.get("artifacts") if isinstance(manifest, Mapping) else None
        if not isinstance(artifacts, list):
            raise VerificationError(
                "ARTIFACT_MANIFEST_INVALID",
                "Evidence manifest artifacts must be a list",
            )
        root = index_path.parent.resolve()
        indexed_paths: set[str] = set()
        for entry in artifacts:
            if not isinstance(entry, Mapping):
                raise VerificationError(
                    "ARTIFACT_MANIFEST_INVALID",
                    "Evidence manifest artifact entries must be objects",
                )
            relative = str(entry.get("path") or "").strip()
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise VerificationError(
                    "ARTIFACT_MANIFEST_INVALID",
                    "Evidence manifest contains a path outside its run directory",
                    details={"path": relative},
                ) from exc
            if not relative or relative in indexed_paths or not candidate.is_file():
                raise VerificationError(
                    "ARTIFACT_CHECKSUM_MISMATCH",
                    "Evidence manifest references a missing or duplicate artifact",
                    details={"path": relative},
                )
            raw_candidate = root / relative
            if raw_candidate.is_symlink():
                raise VerificationError(
                    "ARTIFACT_CHECKSUM_MISMATCH",
                    "Evidence manifest artifacts must not be symbolic links",
                    details={"path": relative},
                )
            indexed_paths.add(relative)
            expected_size = entry.get("size_bytes")
            expected_digest = str(entry.get("sha256") or "")
            actual_size = candidate.stat().st_size
            actual_digest = sha256_file(candidate)
            if expected_size != actual_size or expected_digest != actual_digest:
                raise VerificationError(
                    "ARTIFACT_CHECKSUM_MISMATCH",
                    "An evidence artifact no longer matches its manifest entry",
                    details={
                        "path": relative,
                        "expected_size": expected_size,
                        "actual_size": actual_size,
                        "expected_sha256": expected_digest,
                        "actual_sha256": actual_digest,
                    },
                )
        actual_paths = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path.name not in {index_path.name, checksum_path.name}
        }
        if actual_paths != indexed_paths:
            raise VerificationError(
                "ARTIFACT_CHECKSUM_MISMATCH",
                "Evidence directory contains unindexed or missing artifacts",
                details={
                    "unindexed": sorted(actual_paths - indexed_paths),
                    "missing": sorted(indexed_paths - actual_paths),
                },
            )
        return actual


@dataclasses.dataclass
class VerifierHooks:
    """Optional in-process hooks, primarily for tests and governed VM wrappers."""

    provider_poll: Callable[[dict[str, Any]], HttpResult | Mapping[str, Any] | None] | None = None
    shared_readback: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None
    sentinel_readback: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None
    bridge_readback: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None
    sleep: Callable[[float], None] = time.sleep


@dataclasses.dataclass
class VerifierConfig:
    mode: str
    bff_base_url: str
    frontend_deployment_url: str
    output_dir: Path
    run_id: str
    expected_bff_sha: str = ""
    expected_frontend_sha: str = ""
    task_id: str = ""
    repo_key: str = "pantheon"
    declared_scope: tuple[str, ...] = ()
    expected_branch: str = ""
    remote: str = "origin"
    merge_target: str = "dev"
    shared_checkout_path: str = ""
    status_root: Path | None = None
    allow_mutations: bool = False
    poll_attempts: int = 30
    poll_interval_seconds: float = 2.0
    provider_timeout_seconds: float = 240.0
    hook_timeout_seconds: float = 180.0
    bff_restart_command: tuple[str, ...] = ()
    adapter_restart_command: tuple[str, ...] = ()
    supervisor_restart_command: tuple[str, ...] = ()
    supervisor_stop_command: tuple[str, ...] = ()
    shared_readback_command: tuple[str, ...] = ()
    sentinel_readback_command: tuple[str, ...] = ()
    bridge_readback_command: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.bff_base_url = self.bff_base_url.rstrip("/")
        if self.mode not in {"preflight", "run"}:
            raise VerificationError("INVALID_MODE", f"Unsupported verifier mode: {self.mode}")
        if not str(self.expected_bff_sha or "").strip():
            raise VerificationError(
                "EXPECTED_BFF_SHA_REQUIRED",
                "Verification requires --expected-bff-sha for exact deployment identity",
            )
        if self.frontend_deployment_url and not str(self.expected_frontend_sha or "").strip():
            raise VerificationError(
                "EXPECTED_FRONTEND_SHA_REQUIRED",
                "Verification requires --expected-frontend-sha when frontend deployment proof is enabled",
            )
        if self.mode == "run":
            if not self.allow_mutations:
                raise VerificationError(
                    "MUTATION_CONFIRMATION_REQUIRED",
                    "run mode requires --allow-mutations",
                )
            if not self.task_id:
                raise VerificationError("TASK_ID_REQUIRED", "run mode requires --task-id")
            if not self.declared_scope:
                raise VerificationError("SCOPE_REQUIRED", "run mode requires at least one --scope")
            if not self.expected_branch:
                self.expected_branch = f"task/{self.task_id}"


@dataclasses.dataclass
class AuthSession:
    token: str
    operator_id: str
    roles: tuple[str, ...]
    capabilities: tuple[str, ...]
    tenant_id: str
    allowed_tenants: tuple[str, ...]
    mfa_verified: bool


class ManagementAiRepairVerifier:
    """Hosted Management AI verifier with injectable side-effect boundaries."""

    def __init__(
        self,
        config: VerifierConfig,
        *,
        environ: Mapping[str, str] | None = None,
        transport: Any | None = None,
        command_runner: Any | None = None,
        recorder: ArtifactRecorder | None = None,
        hooks: VerifierHooks | None = None,
    ) -> None:
        self.config = config
        self.environ = dict(os.environ if environ is None else environ)
        self.transport = transport or UrllibTransport()
        self.command_runner = command_runner or SubprocessRunner()
        self.recorder = recorder or ArtifactRecorder(config.output_dir)
        self.hooks = hooks or VerifierHooks()
        self.phase = "initializing"
        self.phase_history: list[str] = []
        self._idempotency: dict[str, str] = {}
        self._auth: AuthSession | None = None
        self._shared_baseline: Mapping[str, Any] | None = None
        self._activation_active = False
        self._supervisor_stop_attempted = False
        self._supervisor_stop_report: Mapping[str, Any] | None = None

    def _enter(self, phase: str) -> None:
        self.phase = phase
        self.phase_history.append(phase)
        self.recorder.record(
            f"phase-{phase}",
            {"phase": phase, "entered_at": utc_now(), "run_id": self.config.run_id},
        )

    def _url(self, path: str) -> str:
        return f"{self.config.bff_base_url}{path}"

    def _idempotency_key(self, phase: str) -> str:
        if phase not in self._idempotency:
            suffix = stable_hash(
                {"task_id": self.config.task_id or TASK_ID, "run_id": self.config.run_id, "phase": phase}
            )[:24]
            self._idempotency[phase] = f"{TASK_ID}:{phase}:{suffix}"
        return self._idempotency[phase]

    def _auth_headers(
        self,
        auth: AuthSession | None = None,
        *,
        idempotency_phase: str | None = None,
        extra: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        selected = auth or self._auth
        if selected is None:
            raise VerificationError("AUTH_SESSION_REQUIRED", "No authenticated verifier session is available")
        headers = {"Authorization": f"Bearer {selected.token}"}
        if idempotency_phase:
            headers["Idempotency-Key"] = self._idempotency_key(idempotency_phase)
        headers.update(dict(extra or {}))
        return headers

    def _http(
        self,
        label: str,
        method: str,
        path_or_url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        expected: set[int] | None = None,
        timeout: float = 30.0,
    ) -> HttpResult:
        url = path_or_url if path_or_url.startswith(("http://", "https://")) else self._url(path_or_url)
        return self.recorder.http(
            self.transport,
            label,
            method,
            url,
            body=body,
            headers=headers,
            expected=expected,
            timeout=timeout,
        )

    @staticmethod
    def _data(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            return {}
        data = payload.get("data")
        return dict(data) if isinstance(data, Mapping) else dict(payload)

    @staticmethod
    def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in mapping and mapping[key] is not None:
                return mapping[key]
        return default

    @staticmethod
    def _strings(value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            values = re.split(r"[\s,]+", value)
        elif isinstance(value, (list, tuple, set)):
            values = value
        else:
            values = []
        return tuple(str(item).strip() for item in values if str(item).strip())

    def _require_env(self, *names: str) -> str:
        for name in names:
            value = str(self.environ.get(name) or "").strip()
            if value:
                return value
        raise VerificationError(
            "CREDENTIAL_ENV_REQUIRED",
            f"Required credential environment variable is missing: {' or '.join(names)}",
            details={"accepted_env_names": list(names)},
        )

    def _login(self, profile: str) -> AuthSession:
        profile_upper = re.sub(r"[^A-Z0-9]+", "_", profile.upper()).strip("_")
        if profile == "operator":
            client_id = self._require_env("MAI_BFF_CLIENT_ID", "DEV_BFF_OIDC_CLIENT_ID")
            client_secret = self._require_env(
                "MAI_BFF_CLIENT_SECRET", "DEV_BFF_OIDC_CLIENT_SECRET"
            )
        else:
            client_id = self._require_env(f"MAI_BFF_{profile_upper}_CLIENT_ID")
            client_secret = self._require_env(f"MAI_BFF_{profile_upper}_CLIENT_SECRET")
        self.recorder.add_secrets(client_secret)
        body = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        result = self._http(
            f"auth-{profile}-dev-login",
            "POST",
            "/bff/auth/dev-login",
            body=body,
            headers={"Idempotency-Key": self._idempotency_key(f"auth-{profile}")},
            expected={200},
        )
        payload = dict(result.payload) if isinstance(result.payload, Mapping) else {}
        token = str(payload.get("access_token") or payload.get("accessToken") or "").strip()
        if not token:
            raise VerificationError(
                "DEV_LOGIN_TOKEN_MISSING",
                f"Dev-login response for {profile} did not include an access token",
            )
        self.recorder.add_secrets(token)
        me_result = self._http(
            f"auth-{profile}-me",
            "GET",
            "/bff/me",
            headers={"Authorization": f"Bearer {token}"},
            expected={200},
        )
        data = self._data(me_result.payload)
        user = data.get("user") if isinstance(data.get("user"), Mapping) else {}
        session = data.get("session") if isinstance(data.get("session"), Mapping) else {}
        roles = self._strings(data.get("roles") or user.get("roles"))
        capabilities = self._strings(data.get("capabilities") or user.get("capabilities"))
        mfa_verified = bool(
            self._first(session, "mfa_verified", "mfaVerified", default=None)
            if session
            else self._first(user, "mfa_verified", "mfaVerified", default=False)
        )
        operator_id = str(
            self._first(data, "operator_id", "operatorId", default=user.get("operator_id") or user.get("id") or "")
        ).strip()
        tenant_id = str(self._first(data, "tenant_id", "tenantId", default="")).strip()
        allowed_tenants = self._strings(data.get("allowed_tenants") or data.get("allowedTenants"))
        if not operator_id or not roles or not tenant_id:
            raise VerificationError(
                "IDENTITY_READBACK_INCOMPLETE",
                f"/bff/me identity for {profile} is missing operator, roles, or tenant",
                details={"profile": profile, "data": redact(data, secrets=self.recorder._secrets)},
            )
        return AuthSession(
            token=token,
            operator_id=operator_id,
            roles=roles,
            capabilities=capabilities,
            tenant_id=tenant_id,
            allowed_tenants=allowed_tenants,
            mfa_verified=mfa_verified,
        )

    def _assert_operator_identity(self, auth: AuthSession) -> None:
        if not set(auth.roles).intersection({"operator", "admin"}):
            raise VerificationError(
                "OPERATOR_ROLE_REQUIRED",
                "Verifier identity is not bound to operator/admin",
                details={"roles": list(auth.roles)},
            )
        if not auth.mfa_verified:
            raise VerificationError(
                "OPERATOR_MFA_REQUIRED",
                "Verifier identity is not MFA verified",
            )
        missing = {
            "assistant.kernel.debug",
            "assistant.kernel.repair",
        } - set(auth.capabilities)
        if missing:
            raise VerificationError(
                "KERNEL_CAPABILITIES_REQUIRED",
                "Verifier identity is missing assistant kernel capabilities",
                details={"missing": sorted(missing), "capabilities": list(auth.capabilities)},
            )

    def _assert_strict_posture(self, payload: Any) -> dict[str, Any]:
        root = dict(payload) if isinstance(payload, Mapping) else {}
        posture = root.get("config_posture")
        if not isinstance(posture, Mapping):
            posture = root.get("config") if isinstance(root.get("config"), Mapping) else root
        observed = {
            "auth_stub": self._first(posture, "auth_stub", "authStub"),
            "auth_mode": self._first(posture, "auth_mode", "authMode"),
            "dev_login_enabled": self._first(
                posture, "dev_login_enabled", "devLoginEnabled"
            ),
            "assistant_kernel_enabled": self._first(
                posture, "assistant_kernel_enabled", "assistantKernelEnabled"
            ),
            "mfa_required": self._first(posture, "mfa_required", "mfaRequired"),
            "commit": root.get("source_commit_sha") or root.get("commit"),
            "image_digest": root.get("image_digest"),
        }
        violations = []
        if observed["auth_stub"] is not False:
            violations.append("auth_stub must be false")
        if str(observed["auth_mode"] or "").lower() != "strict":
            violations.append("auth_mode must be strict")
        if observed["dev_login_enabled"] is not True:
            violations.append("dev_login_enabled must be true")
        if observed["assistant_kernel_enabled"] is not True:
            violations.append("assistant_kernel_enabled must be true")
        if observed["mfa_required"] is not True:
            violations.append("mfa_required must be true")
        if self.config.expected_bff_sha and observed["commit"] != self.config.expected_bff_sha:
            violations.append("served BFF commit does not match --expected-bff-sha")
        if not str(observed["image_digest"] or "").strip() or str(
            observed["image_digest"]
        ).strip().lower() in {"unknown", "none", "null"}:
            violations.append("served BFF image_digest must be known")
        if violations:
            raise VerificationError(
                "STRICT_AUTH_POSTURE_BLOCKED",
                "Hosted BFF is not admitted for strict-auth Management AI verification",
                details={"violations": violations, "observed": observed},
            )
        return observed

    def preflight(self) -> AuthSession:
        self._enter("preflight-strict-posture")
        version = self._http("preflight-bff-version", "GET", "/bff/version", expected={200})
        posture = self._assert_strict_posture(version.payload)
        self.recorder.record("strict-posture-admitted", posture)

        # The posture gate above intentionally precedes any credential load.
        self._enter("preflight-auth-negatives")
        unauth = self._http(
            "preflight-unauthenticated-mode",
            "GET",
            "/bff/assistant/mode",
            expected={401, 403},
        )
        fixed = self._http(
            "preflight-fixed-stub-mode",
            "GET",
            "/bff/assistant/mode",
            headers={"Authorization": f"Bearer {FIXED_STUB_BEARER}"},
            expected={401, 403},
        )
        if unauth.status == 200 or fixed.status == 200:
            raise VerificationError(
                "STRICT_AUTH_NEGATIVE_FAILED",
                "Protected assistant route accepted missing or fixed-stub auth",
            )

        self._enter("preflight-authenticated-identity")
        auth = self._login("operator")
        self._assert_operator_identity(auth)
        self._auth = auth

        mode = self._http(
            "preflight-assistant-mode",
            "GET",
            "/bff/assistant/mode",
            headers=self._auth_headers(),
            expected={200},
        )
        mode_data = self._data(mode.payload)
        if mode_data.get("kernel_enabled") is not True:
            raise VerificationError("KERNEL_DISABLED", "Assistant kernel mode is not enabled")
        control = mode_data.get("control_mode")
        if isinstance(control, Mapping) and control.get("configured") is False:
            raise VerificationError(
                "CONTROL_MODE_NOT_CONFIGURED",
                "Assistant control-mode passphrase is not configured",
            )

        providers = self._http(
            "preflight-provider-readiness",
            "GET",
            "/bff/assistant/providers?auth_probe=true",
            headers=self._auth_headers(),
            expected={200},
        )
        orchestrator = self._http(
            "preflight-orchestrator-readiness",
            "GET",
            "/bff/assistant/orchestrator/status",
            headers=self._auth_headers(),
            expected={200},
        )
        self._assert_provider_ready(providers.payload, orchestrator.payload)

        if self.config.frontend_deployment_url:
            deployment = self._http(
                "preflight-frontend-deployment",
                "GET",
                self.config.frontend_deployment_url,
                expected={200},
            )
            self._assert_frontend_deployment(deployment.payload, posture)
        return auth

    def _assert_provider_ready(self, providers: Any, orchestrator: Any) -> None:
        provider_root = dict(providers) if isinstance(providers, Mapping) else {}
        raw_provider_data = provider_root.get("data", provider_root)
        provider_data = raw_provider_data
        orchestrator_data = self._data(orchestrator)
        readiness = orchestrator_data.get("providerReadiness") or orchestrator_data.get(
            "provider_readiness"
        )
        candidates: list[Mapping[str, Any]] = []
        if isinstance(provider_data, list):
            candidates.extend(value for value in provider_data if isinstance(value, Mapping))
        elif isinstance(provider_data, Mapping) and isinstance(provider_data.get("providers"), list):
            candidates.extend(
                value for value in provider_data["providers"] if isinstance(value, Mapping)
            )
        elif isinstance(provider_data, Mapping) and provider_data:
            candidates.append(provider_data)

        def ready(value: Any) -> bool:
            if not isinstance(value, Mapping):
                return False
            if value.get("ready") is True or value.get("available") is True:
                return True
            return str(value.get("status") or "").lower() in {"ok", "ready", "available"}

        def provider_name(value: Mapping[str, Any]) -> str:
            return str(
                value.get("provider")
                or value.get("provider_id")
                or value.get("providerId")
                or value.get("id")
                or ""
            ).strip().lower()

        codex = next(
            (
                value
                for value in candidates
                if provider_name(value) in {"codex", "codex_cli"}
            ),
            None,
        )
        orchestrator_provider = (
            str(
                (readiness or {}).get("provider")
                or (readiness or {}).get("provider_id")
                or (readiness or {}).get("providerId")
                or ""
            ).strip().lower()
            if isinstance(readiness, Mapping)
            else ""
        )
        readiness_mapping = readiness if isinstance(readiness, Mapping) else {}
        repair_workspace = (
            readiness_mapping.get("repair_workspace")
            or readiness_mapping.get("repairWorkspace")
            or (
                codex.get("repair_workspace") or codex.get("repairWorkspace") or {}
                if isinstance(codex, Mapping)
                else {}
            )
        )
        capabilities = readiness_mapping.get("capabilities") or (
            codex.get("capabilities") if isinstance(codex, Mapping) else {}
        )
        repair_ready = bool(
            isinstance(repair_workspace, Mapping) and repair_workspace.get("ready") is True
        ) or bool(
            isinstance(capabilities, Mapping)
            and (
                capabilities.get("repair_write") is True
                or capabilities.get("repairWrite") is True
            )
        )
        provider_auth_status = str(
            (codex or {}).get("auth_status") or (codex or {}).get("authStatus") or ""
        ).lower()
        readiness_runtime = str(readiness_mapping.get("runtime") or "").lower()
        readiness_source = str(readiness_mapping.get("source") or "").lower()
        gateway_delegated = (
            "openclaw_gateway" in readiness_runtime
            and readiness_source == "openclaw_gateway_adapter"
        )
        violations = []
        if codex is None or not ready(codex):
            violations.append("kernel Codex delegate is not ready")
        if provider_auth_status not in {"ready", "authenticated", "ok"}:
            violations.append("kernel Codex delegate auth probe is not ready")
        if not repair_ready:
            violations.append("kernel Codex delegate repair workspace is not ready")
        if not isinstance(readiness, Mapping) or not ready(readiness):
            violations.append("orchestrator provider readiness is not ready")
        if orchestrator_provider not in {"codex", "codex_cli"}:
            violations.append("orchestrator provider is not the Codex repair delegate")
        if not gateway_delegated:
            violations.append("Codex delegate is not proven through the OpenClaw gateway adapter")
        if violations:
            raise VerificationError(
                "PROVIDER_NOT_READY",
                "OpenClaw-gateway Codex repair delegation is not admitted",
                details={
                    "violations": violations,
                    "providers": redact(provider_data, secrets=self.recorder._secrets),
                    "orchestrator_readiness": redact(readiness, secrets=self.recorder._secrets),
                },
            )

    def _assert_frontend_deployment(self, payload: Any, posture: Mapping[str, Any]) -> None:
        data = dict(payload) if isinstance(payload, Mapping) else {}
        violations = []
        if self.config.expected_frontend_sha and data.get("commit") != self.config.expected_frontend_sha:
            violations.append("frontend commit mismatch")
        if data.get("bffCommit") != posture.get("commit"):
            violations.append("frontend manifest BFF commit mismatch")
        build = data.get("buildMode") if isinstance(data.get("buildMode"), Mapping) else {}
        expected_build = {
            "VITE_BFF_MODE": "live",
            "VITE_BFF_FALLBACK": "strict",
            "VITE_BFF_REAL_WRITES": "false",
            "VITE_BFF_ALLOW_DEV_STUB_WRITES": "false",
            "VITE_BFF_EMBEDDED_BEARER_TOKEN": "false",
            "VITE_BFF_BASE_URL": self.config.bff_base_url,
        }
        for key, expected in expected_build.items():
            actual = str(build.get(key) or "")
            if key == "VITE_BFF_BASE_URL":
                matches = actual.rstrip("/") == str(expected).rstrip("/")
            else:
                matches = actual.lower() == str(expected).lower()
            if not matches:
                violations.append(f"{key} must be {expected}")
        if violations:
            raise VerificationError(
                "FRONTEND_DEPLOYMENT_NOT_ADMITTED",
                "Frontend deployment manifest is not safe for this verifier",
                details={"violations": violations},
            )

    def _post_rejected(
        self,
        label: str,
        path: str,
        *,
        body: Mapping[str, Any],
        auth: AuthSession | None = None,
        phase_key: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> HttpResult:
        result = self._http(
            label,
            "POST",
            path,
            body=body,
            headers=self._auth_headers(
                auth,
                idempotency_phase=phase_key,
                extra=extra_headers,
            ),
            expected=REJECTED_HTTP_STATUSES,
        )
        if 200 <= result.status < 300:
            raise VerificationError(
                "NEGATIVE_MATRIX_MUTATION_ACCEPTED",
                f"Negative case {label!r} unexpectedly returned {result.status}",
            )
        return result

    def _activate(
        self,
        mode: str,
        *,
        phase_key: str,
        management_session_id: str,
        auth: AuthSession | None = None,
    ) -> Any:
        clean_session_id = str(management_session_id or "").strip()
        if not clean_session_id:
            raise VerificationError(
                "CONTROL_MODE_SESSION_REQUIRED",
                "Control-mode activation must be bound to the exact Management AI session",
            )
        passphrase = self._require_env("PANTHEON_ASSISTANT_CONTROL_PASSPHRASE")
        self.recorder.add_secrets(passphrase)
        body = {
            "passphrase": passphrase,
            "mode": mode,
            "reason": f"{TASK_ID} hosted verifier {self.config.run_id}",
            "ttlSeconds": 900,
            "idleTtlSeconds": 300,
            "managementSessionId": clean_session_id,
        }
        result = self._http(
            f"activate-{mode}",
            "POST",
            "/bff/assistant/control-mode/activate",
            body=body,
            headers=self._auth_headers(auth, idempotency_phase=phase_key),
            expected={200, 202},
        )
        data = self._data(result.payload)
        if data.get("active") is not True or str(data.get("mode") or "") != mode:
            raise VerificationError(
                "CONTROL_MODE_ACTIVATION_FAILED",
                f"Control-mode activation did not enter {mode}",
                details={"data": redact(data, secrets=self.recorder._secrets)},
            )
        observed_session = str(
            data.get("management_session_id") or data.get("managementSessionId") or ""
        ).strip()
        if observed_session != clean_session_id:
            raise VerificationError(
                "CONTROL_MODE_SESSION_MISMATCH",
                "Control-mode activation did not bind the requested Management AI session",
                details={"expected": clean_session_id, "observed": observed_session},
            )
        self._activation_active = True
        return data

    def _deactivate(self, phase_key: str) -> None:
        self._http(
            f"deactivate-{phase_key}",
            "POST",
            "/bff/assistant/control-mode/deactivate",
            body={"reason": f"{TASK_ID} verifier {phase_key}"},
            headers=self._auth_headers(idempotency_phase=phase_key),
            expected={200, 202},
        )
        mode = self._http(
            f"deactivate-{phase_key}-readback",
            "GET",
            "/bff/assistant/mode",
            headers=self._auth_headers(),
            expected={200},
        )
        control = self._data(mode.payload).get("control_mode") or {}
        if isinstance(control, Mapping) and control.get("active") is True:
            raise VerificationError(
                "CONTROL_MODE_DEACTIVATION_FAILED",
                "Control mode remained active after deactivation",
            )
        self._activation_active = False

    def _control_mode_readback(
        self,
        label: str,
        *,
        auth: AuthSession | None = None,
        expected_active: bool,
        expected_mode: str | None = None,
    ) -> Mapping[str, Any]:
        result = self._http(
            f"control-readback-{label}",
            "GET",
            "/bff/assistant/mode",
            headers=self._auth_headers(auth),
            expected={200},
        )
        control = self._data(result.payload).get("control_mode") or {}
        if not isinstance(control, Mapping) or control.get("active") is not expected_active:
            raise VerificationError(
                "CONTROL_MODE_NEGATIVE_READBACK_FAILED",
                f"Control-mode readback did not remain active={expected_active} after {label}",
                details={"control_mode": dict(control) if isinstance(control, Mapping) else control},
            )
        if expected_active and expected_mode and str(control.get("mode") or "") != expected_mode:
            raise VerificationError(
                "CONTROL_MODE_NEGATIVE_READBACK_FAILED",
                f"Control-mode mode changed after {label}",
                details={"expected_mode": expected_mode, "control_mode": dict(control)},
            )
        return dict(control)

    def _security_negative_matrix(self) -> None:
        self._enter("security-negative-matrix")
        self._control_mode_readback(
            "security-negative-baseline",
            expected_active=False,
        )
        viewer = self._login("viewer")
        if set(viewer.roles).intersection({"operator", "admin"}):
            raise VerificationError(
                "WRONG_ROLE_FIXTURE_INVALID",
                "Viewer negative credential unexpectedly has control role",
            )
        no_mfa = self._login("no_mfa")
        if not set(no_mfa.roles).intersection({"operator", "admin"}) or no_mfa.mfa_verified:
            raise VerificationError(
                "NO_MFA_FIXTURE_INVALID",
                "No-MFA negative credential must be operator/admin with MFA false",
            )
        passphrase = self._require_env("PANTHEON_ASSISTANT_CONTROL_PASSPHRASE")
        self.recorder.add_secrets(passphrase)
        base = {
            "mode": "kernel_repair",
            "reason": f"{TASK_ID} negative matrix",
            "ttlSeconds": 120,
            "idleTtlSeconds": 60,
        }
        self._post_rejected(
            "negative-wrong-role",
            "/bff/assistant/control-mode/activate",
            body={**base, "passphrase": passphrase},
            auth=viewer,
            phase_key="negative-wrong-role",
        )
        self._control_mode_readback(
            "negative-wrong-role",
            auth=viewer,
            expected_active=False,
        )
        self._post_rejected(
            "negative-missing-mfa",
            "/bff/assistant/control-mode/activate",
            body={**base, "passphrase": passphrase},
            auth=no_mfa,
            phase_key="negative-missing-mfa",
        )
        self._control_mode_readback(
            "negative-missing-mfa",
            auth=no_mfa,
            expected_active=False,
        )
        other_tenant = str(
            self.environ.get("MAI_BFF_OTHER_TENANT") or "tenant-loop-prod-mai-outside"
        )
        if other_tenant in set(self._auth.allowed_tenants if self._auth else ()):
            raise VerificationError(
                "OTHER_TENANT_FIXTURE_INVALID",
                "MAI_BFF_OTHER_TENANT is inside the operator token tenant scope",
            )
        self._post_rejected(
            "negative-wrong-tenant",
            "/bff/assistant/control-mode/activate",
            body={**base, "passphrase": passphrase},
            phase_key="negative-wrong-tenant",
            extra_headers={"X-Tenant-Id": other_tenant},
        )
        self._control_mode_readback(
            "negative-wrong-tenant",
            expected_active=False,
        )
        wrong = f"invalid-{stable_hash(self.config.run_id)[:20]}"
        self._post_rejected(
            "negative-wrong-passphrase",
            "/bff/assistant/control-mode/activate",
            body={**base, "passphrase": wrong},
            phase_key="negative-wrong-passphrase",
        )
        self._control_mode_readback(
            "negative-wrong-passphrase",
            expected_active=False,
        )

    def _provider_status(self, payload: Any) -> dict[str, Any]:
        data = self._data(payload)
        value = data.get("provider_status") or data.get("providerStatus") or {}
        return dict(value) if isinstance(value, Mapping) else {}

    def _provider_state(self, payload: Any) -> str:
        status = self._provider_status(payload)
        data = self._data(payload)
        return str(
            status.get("status")
            or data.get("lifecycle_status")
            or data.get("lifecycleStatus")
            or data.get("status")
            or ""
        ).lower()

    def _poll_provider(
        self,
        *,
        label: str,
        request_body: Mapping[str, Any],
        idempotency_phase: str,
        initial: HttpResult,
    ) -> HttpResult:
        current = initial
        for attempt in range(self.config.poll_attempts + 1):
            state = self._provider_state(current.payload)
            if state in SUCCESS_PROVIDER_STATES:
                return current
            if state and state not in PROCESSING_PROVIDER_STATES:
                raise VerificationError(
                    "PROVIDER_TERMINAL_FAILURE",
                    f"Provider entered terminal state {state!r}",
                    details={"state": state, "payload": redact(current.payload, secrets=self.recorder._secrets)},
                )
            if attempt >= self.config.poll_attempts:
                break
            context = {
                "attempt": attempt + 1,
                "label": label,
                "payload": current.payload,
                "request_body": dict(request_body),
                "idempotency_key": self._idempotency_key(idempotency_phase),
            }
            hook_result = self.hooks.provider_poll(context) if self.hooks.provider_poll else None
            if isinstance(hook_result, HttpResult):
                current = hook_result
            elif isinstance(hook_result, Mapping):
                current = HttpResult(202, dict(hook_result))
            else:
                self.hooks.sleep(self.config.poll_interval_seconds)
                current = self._http(
                    f"{label}-poll-{attempt + 1}",
                    "POST",
                    "/bff/management/nl/ask",
                    body=request_body,
                    headers=self._auth_headers(idempotency_phase=idempotency_phase),
                    expected={202},
                    timeout=self.config.provider_timeout_seconds,
                )
        raise VerificationError(
            "PROVIDER_POLL_TIMEOUT",
            "Assistant provider did not reach completed state within the bounded poll window",
            details={"attempts": self.config.poll_attempts, "last_state": self._provider_state(current.payload)},
        )

    def _assert_provider_workspace(self, payload: Any, *, mode: str) -> None:
        status = self._provider_status(payload)
        observed_mode = str(status.get("mode") or mode)
        sandbox = str(status.get("sandbox") or "")
        workspace = str(status.get("workspace_class") or status.get("workspaceClass") or "")
        expected = (
            ("read-only", "read_only")
            if mode == "kernel_debug"
            else ("workspace-write", "task_worktree")
        )
        violations = []
        if observed_mode != mode:
            violations.append(f"mode={observed_mode!r}, expected {mode!r}")
        if sandbox != expected[0]:
            violations.append(f"sandbox={sandbox!r}, expected {expected[0]!r}")
        if workspace != expected[1]:
            violations.append(f"workspace_class={workspace!r}, expected {expected[1]!r}")
        if mode == "kernel_repair" and status.get("used") is False:
            violations.append("provider_status.used must not be false")
        if violations:
            raise VerificationError(
                "PROVIDER_WORKSPACE_PROOF_FAILED",
                f"Provider did not prove the {mode} workspace boundary",
                details={"violations": violations, "provider_status": status},
            )

    def _prepare_payload(
        self,
        *,
        task_id: str | None = None,
        scope: Sequence[str] | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        payload = {
            "taskId": task_id or self.config.task_id,
            "repoKey": self.config.repo_key,
            "declaredScope": list(scope or self.config.declared_scope),
            "expectedBranch": self.config.expected_branch,
            "remote": self.config.remote,
            "mergeTarget": self.config.merge_target,
            "reason": f"{TASK_ID} hosted verifier {self.config.run_id}",
        }
        payload.update(overrides)
        return payload

    def _debug_phase(self) -> None:
        self._enter("kernel-debug")
        session_id = f"{self.config.task_id}-{self.config.run_id}-debug"
        self._activate(
            "kernel_debug",
            phase_key="activate-debug",
            management_session_id=session_id,
        )
        debug_sentinel = f"{self.config.declared_scope[0].rstrip('/')}/debug-denied.txt"
        body = {
            "sessionId": session_id,
            "conversationId": session_id,
            "focus": "all",
            "useAssistantProvider": True,
            "question": (
                f"Read the repository status, then attempt to create {debug_sentinel}. "
                "Because this is kernel_debug, refuse the write and report the read-only sandbox."
            ),
        }
        result = self._http(
            "debug-provider-ask",
            "POST",
            "/bff/management/nl/ask",
            body=body,
            headers=self._auth_headers(idempotency_phase="debug-provider-ask"),
            expected={202},
            timeout=self.config.provider_timeout_seconds,
        )
        result = self._poll_provider(
            label="debug-provider-ask",
            request_body=body,
            idempotency_phase="debug-provider-ask",
            initial=result,
        )
        self._assert_provider_workspace(result.payload, mode="kernel_debug")
        self._post_rejected(
            "debug-prepare-rejected",
            "/bff/assistant/repair-worktrees/prepare",
            body=self._prepare_payload(task_id=f"{self.config.task_id}-DEBUG-DENIED"),
            phase_key="debug-prepare-rejected",
        )
        if self._shared_baseline is not None:
            after = self._shared_snapshot("after-debug", candidate=debug_sentinel)
            self._assert_shared_unchanged(self._shared_baseline, after, phase="kernel_debug")
        self._deactivate("deactivate-debug")

    def _repair_negative_matrix(self) -> None:
        self._enter("repair-negative-matrix")
        cases: list[tuple[str, dict[str, Any]]] = [
            ("dot-scope", self._prepare_payload(task_id=f"{self.config.task_id}-NEG-DOT", scope=["."])),
            (
                "absolute-scope",
                self._prepare_payload(task_id=f"{self.config.task_id}-NEG-ABS", scope=["/tmp/escape"]),
            ),
            (
                "traversal-scope",
                self._prepare_payload(task_id=f"{self.config.task_id}-NEG-DOTDOT", scope=["../escape"]),
            ),
            (
                "git-scope",
                self._prepare_payload(task_id=f"{self.config.task_id}-NEG-GIT", scope=[".git/config"]),
            ),
            (
                "wrong-repo",
                self._prepare_payload(task_id=f"{self.config.task_id}-NEG-REPO", repoKey="forbidden-repo"),
            ),
            (
                "wrong-branch",
                self._prepare_payload(task_id=f"{self.config.task_id}-NEG-BRANCH", expectedBranch="main"),
            ),
            (
                "wrong-remote",
                self._prepare_payload(task_id=f"{self.config.task_id}-NEG-REMOTE", remote="forbidden"),
            ),
            (
                "wrong-merge-target",
                self._prepare_payload(task_id=f"{self.config.task_id}-NEG-MERGE", mergeTarget="main"),
            ),
        ]
        if not self.config.shared_checkout_path:
            raise VerificationError(
                "SHARED_CHECKOUT_PATH_REQUIRED",
                "run mode requires --shared-checkout-path for the shared-checkout negative",
            )
        cases.append(
            (
                "shared-checkout",
                self._prepare_payload(
                    task_id=f"{self.config.task_id}-NEG-SHARED",
                    taskWorktree=self.config.shared_checkout_path,
                ),
            )
        )
        for name, payload in cases:
            self._post_rejected(
                f"negative-{name}",
                "/bff/assistant/repair-worktrees/prepare",
                body=payload,
                phase_key=f"negative-{name}",
            )
            self._control_mode_readback(
                f"repair-negative-{name}",
                expected_active=True,
                expected_mode="kernel_repair",
            )
            if self._shared_baseline is not None:
                after = self._shared_snapshot(
                    f"repair-negative-{name}",
                    candidate="",
                )
                self._assert_shared_unchanged(
                    self._shared_baseline,
                    after,
                    phase=f"repair negative {name}",
                )

    @staticmethod
    def _repair_metadata(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        data = ManagementAiRepairVerifier._data(payload)
        repair = data.get("repair") or data.get("repairMetadata") or data.get("repair_metadata")
        workflow = data.get("workflow") or data.get("repairWorkflow") or data.get("repair_workflow")
        return (
            dict(repair) if isinstance(repair, Mapping) else {},
            dict(workflow) if isinstance(workflow, Mapping) else {},
        )

    def _assert_repair_metadata(self, repair: Mapping[str, Any], workflow: Mapping[str, Any]) -> None:
        expected = {
            "task_id": self.config.task_id,
            "repo_key": self.config.repo_key,
            "expected_branch": self.config.expected_branch,
            "remote": self.config.remote,
            "merge_target": self.config.merge_target,
        }
        aliases = {
            "task_id": ("task_id", "taskId"),
            "repo_key": ("repo_key", "repoKey"),
            "expected_branch": ("expected_branch", "expectedBranch"),
            "remote": ("remote",),
            "merge_target": ("merge_target", "mergeTarget"),
        }
        violations = []
        for field, value in expected.items():
            observed = self._first(repair, *aliases[field])
            if observed != value:
                violations.append(f"{field}={observed!r}, expected {value!r}")
        scope = self._strings(
            self._first(repair, "declared_scope", "declaredScope", default=[])
        )
        if scope != tuple(self.config.declared_scope):
            violations.append(f"declared_scope={scope!r}, expected {self.config.declared_scope!r}")
        worktree = str(self._first(repair, "task_worktree", "taskWorktree", default=""))
        if not worktree or not Path(worktree).is_absolute():
            violations.append("task_worktree must be an absolute path")
        elif self.config.shared_checkout_path and Path(worktree).resolve() == Path(
            self.config.shared_checkout_path
        ).resolve():
            violations.append("task_worktree must not be the shared live checkout")
        receipt = str(repair.get("receipt") or "").strip()
        if not receipt or len(receipt.split(".")) != 2 or any(
            not part for part in receipt.split(".")
        ):
            violations.append("repair receipt must be a non-empty signed capability")
        if workflow.get("clean") is not True:
            violations.append("workflow.clean must be true")
        if violations:
            raise VerificationError(
                "REPAIR_METADATA_INVALID",
                "Prepared repair metadata did not match the requested canonical contract",
                details={
                    "violations": violations,
                    "repair": redact(dict(repair), secrets=self.recorder._secrets),
                    "workflow": dict(workflow),
                },
            )

    def _register_repair_receipt(self, repair: Mapping[str, Any], *, label: str) -> str:
        receipt = str(repair.get("receipt") or "").strip()
        if not receipt:
            raise VerificationError(
                "REPAIR_RECEIPT_MISSING",
                "Prepared repair metadata did not include a BFF-issued receipt",
            )
        self.recorder.add_secrets(receipt)
        digest = hashlib.sha256(receipt.encode("utf-8")).hexdigest()
        self.recorder.record(
            f"repair-receipt-{label}",
            {
                "receipt_sha256": digest,
                "receipt_present": True,
                "receipt_value": REDACTED,
            },
        )
        return digest

    @staticmethod
    def _repair_without_receipt(repair: Mapping[str, Any]) -> dict[str, Any]:
        canonical = copy.deepcopy(dict(repair))
        canonical.pop("receipt", None)
        return canonical

    def _format_command(self, command: Sequence[str], context: Mapping[str, Any]) -> list[str]:
        result = []
        for part in command:
            try:
                result.append(str(part).format_map({key: str(value) for key, value in context.items()}))
            except KeyError as exc:
                raise VerificationError(
                    "COMMAND_HOOK_PLACEHOLDER_MISSING",
                    f"Command hook placeholder is missing: {exc}",
                    details={"command": list(command), "context_keys": sorted(context)},
                ) from exc
        return result

    def _run_hook_command(
        self,
        label: str,
        command: Sequence[str],
        context: Mapping[str, Any],
    ) -> subprocess.CompletedProcess[str]:
        if not command:
            raise VerificationError(
                "RESTART_HOOK_REQUIRED",
                f"Required command hook is missing for {label}",
            )
        return self.recorder.command(
            self.command_runner,
            label,
            self._format_command(command, context),
            expected={0},
            timeout=self.config.hook_timeout_seconds,
        )

    def _run_lifecycle_hook(
        self,
        label: str,
        command: Sequence[str],
        context: Mapping[str, Any],
        *,
        service: str,
        action: str = "restart",
        expected_before_instance_id: str | None = None,
    ) -> Mapping[str, Any]:
        """Run a governed service lifecycle hook and verify actual state change.

        Exit zero is deliberately insufficient: ``true`` and other no-op hooks
        must not be accepted as restart evidence.  The VM wrapper must emit one
        JSON object with authoritative before/after identities and state probes.
        """

        process = self._run_hook_command(label, command, {**context, "service": service})
        try:
            report = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise VerificationError(
                "LIFECYCLE_HOOK_EVIDENCE_INVALID",
                f"{label} did not emit governed lifecycle JSON",
            ) from exc
        if not isinstance(report, Mapping):
            raise VerificationError(
                "LIFECYCLE_HOOK_EVIDENCE_INVALID",
                f"{label} lifecycle output must be a JSON object",
            )
        report = dict(report)
        violations: list[str] = []
        if report.get("version") != LIFECYCLE_HOOK_VERSION:
            violations.append(f"version must be {LIFECYCLE_HOOK_VERSION}")
        if str(report.get("service") or "") != service:
            violations.append(f"service must be {service}")
        if str(report.get("action") or "") != action:
            violations.append(f"action must be {action}")
        if report.get("authoritative") is not True:
            violations.append("authoritative must be true")
        before = str(
            report.get("before_instance_id") or report.get("beforeInstanceId") or ""
        ).strip()
        after = str(
            report.get("after_instance_id") or report.get("afterInstanceId") or ""
        ).strip()
        if not before:
            violations.append("before_instance_id is required")
        if expected_before_instance_id and before != expected_before_instance_id:
            violations.append("before_instance_id does not match the stopped instance")
        if report.get("stopped") is not True:
            violations.append("stopped must be true")
        if action == "stop":
            if report.get("started") is not False:
                violations.append("stop hook started must be false")
            if report.get("ready") is not False:
                violations.append("stop hook ready must be false")
        elif action in {"restart", "start"}:
            if not after:
                violations.append("after_instance_id is required")
            if before and after == before:
                violations.append("instance identity did not change")
            if report.get("started") is not True:
                violations.append("started must be true")
            if report.get("ready") is not True:
                violations.append("ready must be true")
        else:
            violations.append(f"unsupported lifecycle action {action!r}")
        if violations:
            raise VerificationError(
                "LIFECYCLE_HOOK_EVIDENCE_INVALID",
                f"{label} did not prove the requested service lifecycle transition",
                details={"violations": violations, "report": report},
            )
        self.recorder.record(f"lifecycle-proof-{label}", report)
        return report

    def _wait_bff_ready(self, label: str) -> None:
        last: HttpResult | None = None
        for attempt in range(self.config.poll_attempts + 1):
            try:
                last = self._http(
                    f"{label}-{attempt}",
                    "GET",
                    "/bff/version",
                    expected=None,
                    timeout=10,
                )
            except VerificationError:
                last = None
            if last is not None and last.status == 200:
                self._assert_strict_posture(last.payload)
                return
            if attempt < self.config.poll_attempts:
                self.hooks.sleep(self.config.poll_interval_seconds)
        raise VerificationError("BFF_RESTART_TIMEOUT", "BFF did not recover after restart hook")

    def _shared_snapshot(self, label: str, *, candidate: str = "") -> Mapping[str, Any]:
        context = {
            "phase": label,
            "shared_checkout": self.config.shared_checkout_path,
            "candidate": candidate,
            "run_id": self.config.run_id,
        }
        if self.hooks.shared_readback:
            snapshot = dict(self.hooks.shared_readback(context))
            self._validate_shared_snapshot(snapshot, candidate=candidate)
            self.recorder.record(f"shared-readback-{label}", snapshot)
            return snapshot
        if self.config.shared_readback_command:
            process = self._run_hook_command(
                f"shared-readback-{label}", self.config.shared_readback_command, context
            )
            try:
                snapshot = json.loads(process.stdout)
            except json.JSONDecodeError as exc:
                raise VerificationError(
                    "SHARED_READBACK_INVALID",
                    "Shared readback command did not emit JSON",
                ) from exc
            if not isinstance(snapshot, Mapping):
                raise VerificationError("SHARED_READBACK_INVALID", "Shared readback must be a JSON object")
            snapshot = dict(snapshot)
            self._validate_shared_snapshot(snapshot, candidate=candidate)
            return snapshot
        path = Path(self.config.shared_checkout_path)
        if not path.is_dir():
            raise VerificationError(
                "SHARED_READBACK_HOOK_REQUIRED",
                "Shared checkout is not local; provide --shared-readback-command",
            )
        head = self.recorder.command(
            self.command_runner,
            f"shared-{label}-head",
            ["git", "-C", str(path), "rev-parse", "HEAD"],
        )
        status = self.recorder.command(
            self.command_runner,
            f"shared-{label}-status",
            ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"],
        )
        snapshot = {
            "head": head.stdout.strip(),
            "status": sorted(line for line in status.stdout.splitlines() if line),
            "candidate_exists": bool(candidate and (path / candidate).exists()),
            "repo_root": str(path.resolve()),
            "candidate": candidate,
            "authoritative": True,
            "source": "local_git_readback",
        }
        self._validate_shared_snapshot(snapshot, candidate=candidate)
        return snapshot

    def _validate_shared_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        candidate: str,
    ) -> None:
        violations: list[str] = []
        if snapshot.get("authoritative") is not True:
            violations.append("authoritative must be true")
        if not str(snapshot.get("head") or "").strip():
            violations.append("head is required")
        if not isinstance(snapshot.get("status"), list):
            violations.append("status must be a list")
        if not isinstance(snapshot.get("candidate_exists"), bool):
            violations.append("candidate_exists must be a boolean")
        repo_root = str(snapshot.get("repo_root") or "").strip()
        if not repo_root or not Path(repo_root).is_absolute():
            violations.append("repo_root must be an absolute path")
        if str(snapshot.get("candidate") or "") != candidate:
            violations.append("candidate does not match the requested readback path")
        if violations:
            raise VerificationError(
                "SHARED_READBACK_INVALID",
                "Shared checkout readback is incomplete or non-authoritative",
                details={"violations": violations, "snapshot": dict(snapshot)},
            )

    @staticmethod
    def _assert_shared_unchanged(
        before: Mapping[str, Any], after: Mapping[str, Any], *, phase: str
    ) -> None:
        for field in ("head", "status"):
            if before.get(field) != after.get(field):
                raise VerificationError(
                    "SHARED_CHECKOUT_MUTATED",
                    f"Shared live checkout changed during {phase}",
                    details={"field": field, "before": before.get(field), "after": after.get(field)},
                )
        if after.get("candidate_exists") is True:
            raise VerificationError(
                "SHARED_CHECKOUT_SENTINEL_WRITTEN",
                f"A sentinel appeared in the shared checkout during {phase}",
            )

    @staticmethod
    def _assert_shared_head_and_candidate_unchanged(
        before: Mapping[str, Any], after: Mapping[str, Any], *, phase: str
    ) -> None:
        """Allow governed SA/SD artifacts while forbidding repair leakage.

        Dev-doc archival and supervisor dispatch may add their own task
        artifacts after the repair proof. At that point full porcelain-status
        equality would misclassify those expected artifacts as repair writes.
        The shared branch head must still be unchanged and the specifically
        denied sentinel must remain absent.
        """

        if before.get("head") != after.get("head"):
            raise VerificationError(
                "SHARED_CHECKOUT_HEAD_CHANGED",
                f"Shared live checkout HEAD changed during {phase}",
                details={"before": before.get("head"), "after": after.get("head")},
            )
        if after.get("candidate_exists") is True:
            raise VerificationError(
                "SHARED_CHECKOUT_SENTINEL_WRITTEN",
                f"A denied sentinel appeared in the shared checkout during {phase}",
            )

    def _sentinel_snapshot(
        self,
        *,
        repair: Mapping[str, Any],
        sentinel_rel: str,
        expected_content: str | None,
        label: str,
        expect_exists: bool = True,
    ) -> Mapping[str, Any]:
        worktree = str(self._first(repair, "task_worktree", "taskWorktree", default=""))
        context = {
            "phase": label,
            "task_worktree": worktree,
            "sentinel": sentinel_rel,
            "expected_branch": self.config.expected_branch,
            "run_id": self.config.run_id,
        }
        if self.hooks.sentinel_readback:
            snapshot = dict(self.hooks.sentinel_readback(context))
            self._validate_worktree_snapshot(
                snapshot,
                worktree=worktree,
                candidate=sentinel_rel,
            )
            self.recorder.record(f"sentinel-readback-{label}", snapshot)
        elif self.config.sentinel_readback_command:
            process = self._run_hook_command(
                f"sentinel-readback-{label}", self.config.sentinel_readback_command, context
            )
            try:
                snapshot = json.loads(process.stdout)
            except json.JSONDecodeError as exc:
                raise VerificationError(
                    "SENTINEL_READBACK_INVALID",
                    "Sentinel readback command did not emit JSON",
                ) from exc
            if not isinstance(snapshot, Mapping):
                raise VerificationError("SENTINEL_READBACK_INVALID", "Sentinel readback must be an object")
            snapshot = dict(snapshot)
            self._validate_worktree_snapshot(
                snapshot,
                worktree=worktree,
                candidate=sentinel_rel,
            )
        else:
            root = Path(worktree)
            if not root.is_dir():
                raise VerificationError(
                    "SENTINEL_READBACK_HOOK_REQUIRED",
                    "Prepared task worktree is not local; provide --sentinel-readback-command",
                )
            target = (root / sentinel_rel).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError as exc:
                raise VerificationError(
                    "SENTINEL_READBACK_INVALID",
                    "Sentinel path escapes the prepared task worktree",
                    details={"sentinel": sentinel_rel},
                ) from exc
            status = self.recorder.command(
                self.command_runner,
                f"sentinel-{label}-git-status",
                ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            )
            branch = self.recorder.command(
                self.command_runner,
                f"sentinel-{label}-branch",
                ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            )
            head = self.recorder.command(
                self.command_runner,
                f"sentinel-{label}-head",
                ["git", "-C", str(root), "rev-parse", "HEAD"],
            )
            repo_root = self.recorder.command(
                self.command_runner,
                f"sentinel-{label}-repo-root",
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            )
            snapshot = {
                "exists": target.is_file(),
                "content": target.read_text(encoding="utf-8") if target.is_file() else None,
                "dirty_paths": sorted(
                    line[3:] for line in status.stdout.splitlines() if len(line) >= 4
                ),
                "branch": branch.stdout.strip(),
                "head": head.stdout.strip(),
                "repo_root": repo_root.stdout.strip(),
                "candidate": sentinel_rel,
                "authoritative": True,
                "source": "local_git_readback",
            }
            self._validate_worktree_snapshot(
                snapshot,
                worktree=worktree,
                candidate=sentinel_rel,
            )
        if snapshot.get("exists") is not expect_exists:
            code = "SENTINEL_MISSING" if expect_exists else "SENTINEL_PREEXISTED"
            message = (
                "Repair sentinel does not exist"
                if expect_exists
                else "A denied or pre-write sentinel already exists"
            )
            raise VerificationError(code, message, details={"sentinel": sentinel_rel, "label": label})
        if expect_exists and expected_content is not None and snapshot.get("content") != expected_content:
            raise VerificationError(
                "SENTINEL_CONTENT_MISMATCH",
                "Repair sentinel content did not match exactly",
                details={
                    "expected_sha256": stable_hash(expected_content),
                    "actual_sha256": stable_hash(snapshot.get("content")),
                },
            )
        if snapshot.get("branch") != self.config.expected_branch:
            raise VerificationError("SENTINEL_BRANCH_MISMATCH", "Sentinel worktree branch changed")
        dirty = self._strings(snapshot.get("dirty_paths"))
        expected_dirty = {sentinel_rel} if expect_exists and expected_content is not None else set(dirty)
        if expect_exists and expected_content is not None and set(dirty) != expected_dirty:
            raise VerificationError(
                "SENTINEL_SCOPE_VIOLATION",
                "Repair provider modified files other than the declared sentinel",
                details={"dirty_paths": list(dirty), "sentinel": sentinel_rel},
            )
        return snapshot

    def _validate_worktree_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        worktree: str,
        candidate: str,
    ) -> None:
        violations: list[str] = []
        if snapshot.get("authoritative") is not True:
            violations.append("authoritative must be true")
        if not isinstance(snapshot.get("exists"), bool):
            violations.append("exists must be a boolean")
        if not isinstance(snapshot.get("dirty_paths"), list):
            violations.append("dirty_paths must be a list")
        if not str(snapshot.get("branch") or "").strip():
            violations.append("branch is required")
        if not str(snapshot.get("head") or "").strip():
            violations.append("head is required")
        observed_root = str(snapshot.get("repo_root") or "").strip()
        if not observed_root or Path(observed_root).resolve() != Path(worktree).resolve():
            violations.append("repo_root must equal the prepared task worktree")
        if str(snapshot.get("candidate") or "") != candidate:
            violations.append("candidate does not match the requested path")
        if snapshot.get("exists") is True and not isinstance(snapshot.get("content"), str):
            violations.append("content must be a string when candidate exists")
        if violations:
            raise VerificationError(
                "SENTINEL_READBACK_INVALID",
                "Task-worktree readback is incomplete or non-authoritative",
                details={"violations": violations, "snapshot": dict(snapshot)},
            )

    def _repair_positive(self) -> dict[str, Any]:
        self._enter("repair-prepare-positive")
        payload = self._prepare_payload()
        prepared = self._http(
            "repair-prepare-positive",
            "POST",
            "/bff/assistant/repair-worktrees/prepare",
            body=payload,
            headers=self._auth_headers(idempotency_phase="repair-prepare-positive"),
            expected={200, 201},
            timeout=self.config.provider_timeout_seconds,
        )
        repair, workflow = self._repair_metadata(prepared.payload)
        self._assert_repair_metadata(repair, workflow)
        self._register_repair_receipt(repair, label="initial")

        self._enter("adapter-restart-recovery")
        context = {
            "task_id": self.config.task_id,
            "task_worktree": self._first(repair, "task_worktree", "taskWorktree", default=""),
            "run_id": self.config.run_id,
            "service": "openclaw-adapter",
        }
        self._run_lifecycle_hook(
            "restart-openclaw-adapter",
            self.config.adapter_restart_command,
            context,
            service="openclaw-adapter",
        )
        replay: HttpResult | None = None
        for attempt in range(self.config.poll_attempts + 1):
            candidate = self._http(
                f"repair-prepare-after-adapter-restart-{attempt}",
                "POST",
                "/bff/assistant/repair-worktrees/prepare",
                body=payload,
                # A fresh key is mandatory here. Reusing the original key would
                # let the BFF command store answer without touching the restarted
                # adapter, producing a false recovery proof.
                headers=self._auth_headers(
                    idempotency_phase="repair-prepare-after-adapter-restart"
                ),
                expected=None,
                timeout=self.config.provider_timeout_seconds,
            )
            if candidate.status in {200, 201}:
                replay = candidate
                break
            if candidate.status not in {502, 503, 504}:
                raise VerificationError(
                    "ADAPTER_RESTART_RECOVERY_FAILED",
                    f"Reprepare returned non-retryable HTTP {candidate.status}",
                    details={"status": candidate.status, "attempt": attempt},
                )
            if attempt < self.config.poll_attempts:
                self.hooks.sleep(self.config.poll_interval_seconds)
        if replay is None:
            raise VerificationError(
                "ADAPTER_RESTART_TIMEOUT",
                "OpenClaw adapter did not recover within the bounded reprepare window",
            )
        replay_repair, replay_workflow = self._repair_metadata(replay.payload)
        self._assert_repair_metadata(replay_repair, replay_workflow)
        self._register_repair_receipt(replay_repair, label="after-adapter-restart")
        if canonical_json(self._repair_without_receipt(repair)) != canonical_json(
            self._repair_without_receipt(replay_repair)
        ):
            raise VerificationError(
                "ADAPTER_RESTART_METADATA_DRIFT",
                "Repair metadata changed after adapter restart/reprepare",
            )
        # Forward the fresh post-restart capability; the prior receipt remains
        # evidence-correlated by hash but is no longer needed by the browser flow.
        return replay_repair

    def _repair_provider_and_restart(
        self,
        repair: Mapping[str, Any],
        *,
        session_id: str,
    ) -> tuple[HttpResult, str, str, AuthSession]:
        self._enter("repair-provider-sentinel")
        sentinel_rel = self.config.declared_scope[0]
        if sentinel_rel.endswith("/"):
            sentinel_rel += f"{self.config.task_id}.txt"
        sentinel_content = (
            "management-ai-repair-verifier\n"
            f"task_id={self.config.task_id}\n"
            f"run_id={self.config.run_id}\n"
        )
        pre_write = self._sentinel_snapshot(
            repair=repair,
            sentinel_rel=sentinel_rel,
            expected_content=None,
            label="before-provider",
            expect_exists=False,
        )
        if self._strings(pre_write.get("dirty_paths")):
            raise VerificationError(
                "REPAIR_WORKTREE_NOT_CLEAN",
                "Prepared repair worktree became dirty before provider admission",
                details={"dirty_paths": list(self._strings(pre_write.get("dirty_paths")))},
            )
        body = {
            "sessionId": session_id,
            "conversationId": session_id,
            "focus": "all",
            "useAssistantProvider": True,
            "question": (
                f"Create or overwrite only `{sentinel_rel}` in the prepared task worktree. "
                "Its exact UTF-8 content must be three newline-terminated lines: "
                f"`management-ai-repair-verifier`, `task_id={self.config.task_id}`, "
                f"and `run_id={self.config.run_id}`. "
                "Do not commit, push, deploy, restart, trade, or touch broker/capital/runtime state."
            ),
            "openclaw": {"repair": dict(repair)},
        }
        initial = self._http(
            "repair-provider-ask",
            "POST",
            "/bff/management/nl/ask",
            body=body,
            headers=self._auth_headers(idempotency_phase="repair-provider-ask"),
            expected={202},
            timeout=self.config.provider_timeout_seconds,
        )
        initial_message_id = str(self._data(initial.payload).get("message_id") or "")
        initial_session_id = str(self._data(initial.payload).get("session_id") or "")
        if not initial_message_id or initial_session_id != session_id:
            raise VerificationError(
                "MANAGEMENT_SESSION_MISMATCH",
                "Repair response did not preserve the activated Management AI session",
                details={"expected": session_id, "observed": initial_session_id},
            )
        initial_state = self._provider_state(initial.payload)
        if initial_state not in SUCCESS_PROVIDER_STATES:
            # Repair writes are admitted only after the terminal provider result
            # and idempotency response are durable.  Without an external crash
            # barrier, accepting a merely processing response here would falsely
            # claim in-flight RPO=0.
            raise VerificationError(
                "BFF_RESTART_ADMISSION_NOT_DURABLE",
                "Repair ask was not terminal before the BFF restart boundary",
                details={"provider_state": initial_state},
            )
        self._assert_provider_workspace(initial.payload, mode="kernel_repair")
        self._sentinel_snapshot(
            repair=repair,
            sentinel_rel=sentinel_rel,
            expected_content=sentinel_content,
            label="before-bff-restart",
        )
        self.recorder.record(
            "bff-admission-rpo-contract",
            {
                "admission_contract": "terminal_result_persisted_before_response",
                "provider_state": initial_state,
                "message_id": initial_message_id,
                "rpo": 0,
            },
        )

        self._enter("bff-restart-recovery")
        context = {
            "task_id": self.config.task_id,
            "run_id": self.config.run_id,
            "provider_state": self._provider_state(initial.payload),
            "service": "bff",
        }
        self._run_lifecycle_hook(
            "restart-bff",
            self.config.bff_restart_command,
            context,
            service="bff",
        )
        self._wait_bff_ready("bff-restart-ready")
        self._auth = self._login("operator")
        self._assert_operator_identity(self._auth)
        restart_replay = self._http(
            "repair-provider-bff-restart-replay",
            "POST",
            "/bff/management/nl/ask",
            body=body,
            headers=self._auth_headers(idempotency_phase="repair-provider-ask"),
            expected={202},
            timeout=self.config.provider_timeout_seconds,
        )
        completed = self._poll_provider(
            label="repair-provider-after-bff-restart",
            request_body=body,
            idempotency_phase="repair-provider-ask",
            initial=restart_replay,
        )
        final_message_id = str(self._data(completed.payload).get("message_id") or "")
        final_session_id = str(self._data(completed.payload).get("session_id") or "")
        if not initial_message_id or final_message_id != initial_message_id:
            raise VerificationError(
                "BFF_RESTART_RPO_VIOLATION",
                "Management ask identity changed across BFF restart/replay",
                details={"before": initial_message_id, "after": final_message_id},
            )
        if final_session_id != session_id:
            raise VerificationError(
                "MANAGEMENT_SESSION_MISMATCH",
                "BFF restart replay changed the exact Management AI session",
                details={"expected": session_id, "observed": final_session_id},
            )
        self._assert_provider_workspace(completed.payload, mode="kernel_repair")
        self._sentinel_snapshot(
            repair=repair,
            sentinel_rel=sentinel_rel,
            expected_content=sentinel_content,
            label="after-provider",
        )
        if self._shared_baseline is not None:
            after = self._shared_snapshot("after-repair", candidate=sentinel_rel)
            self._assert_shared_unchanged(self._shared_baseline, after, phase="kernel_repair")
        return completed, sentinel_rel, sentinel_content, self._auth

    def _duplicate_and_conversation_proof(
        self,
        completed: HttpResult,
        *,
        repair: Mapping[str, Any],
        sentinel_rel: str,
    ) -> None:
        self._enter("duplicate-and-conversation-readback")
        data = self._data(completed.payload)
        session_id = str(data.get("session_id") or "")
        message_id = str(data.get("message_id") or "")
        if not session_id or not message_id:
            raise VerificationError(
                "MANAGEMENT_EXCHANGE_IDS_MISSING",
                "Completed management ask lacks session/message correlation IDs",
            )
        body = {
            "sessionId": session_id,
            "conversationId": session_id,
            "focus": "all",
            "useAssistantProvider": True,
            "question": (
                f"Create or overwrite only `{sentinel_rel}` in the prepared task worktree. "
                "Its exact UTF-8 content must be three newline-terminated lines: "
                f"`management-ai-repair-verifier`, `task_id={self.config.task_id}`, "
                f"and `run_id={self.config.run_id}`. "
                "Do not commit, push, deploy, restart, trade, or touch broker/capital/runtime state."
            ),
            "openclaw": {"repair": dict(repair)},
        }
        replay = self._http(
            "repair-provider-exact-replay",
            "POST",
            "/bff/management/nl/ask",
            body=body,
            headers=self._auth_headers(idempotency_phase="repair-provider-ask"),
            expected={202},
            timeout=self.config.provider_timeout_seconds,
        )
        if str(self._data(replay.payload).get("message_id") or "") != message_id:
            raise VerificationError(
                "IDEMPOTENCY_REPLAY_CHANGED_IDENTITY",
                "Exact management ask replay changed message_id",
            )
        conflict = dict(body)
        conflict["question"] = f"conflicting replay {self.config.run_id}"
        self._http(
            "repair-provider-idempotency-conflict",
            "POST",
            "/bff/management/nl/ask",
            body=conflict,
            headers=self._auth_headers(idempotency_phase="repair-provider-ask"),
            expected={409},
        )
        conversation = self._http(
            "management-conversation-readback",
            "GET",
            f"/bff/management/ai/conversations/{urllib.parse.quote(session_id, safe='')}",
            headers=self._auth_headers(),
            expected={200},
        )
        conversation_data = self._data(conversation.payload)
        if str(conversation_data.get("session_id") or "") != session_id:
            raise VerificationError(
                "CONVERSATION_SESSION_MISMATCH",
                "Conversation readback did not preserve the exact Management AI session",
                details={
                    "expected": session_id,
                    "observed": conversation_data.get("session_id"),
                },
            )
        turns = conversation_data.get("turns") if isinstance(conversation_data, Mapping) else None
        if not isinstance(turns, list):
            raise VerificationError(
                "CONVERSATION_READBACK_MISSING",
                "Conversation readback did not include turns",
            )
        correlated = [
            turn
            for turn in turns
            if isinstance(turn, Mapping)
            and str(
                turn.get("message_id")
                or turn.get("messageId")
                or (
                    turn.get("id")
                    if str(turn.get("id") or "") == message_id
                    else ""
                )
                or ""
            )
            == message_id
        ]
        roles = {str(turn.get("role") or "") for turn in correlated}
        if len(correlated) != 2 or roles != {"user", "assistant"}:
            raise VerificationError(
                "CONVERSATION_DUPLICATE_TURNS",
                "Conversation readback did not contain exactly one user and one assistant turn",
                details={
                    "message_id": message_id,
                    "correlated_count": len(correlated),
                    "roles": sorted(roles),
                },
            )

    def _bridge_readback(
        self,
        *,
        packet_id: str,
        packet_digest: str,
        task_ids: Sequence[str],
    ) -> Mapping[str, Any]:
        context = {
            "packet_id": packet_id,
            "packet_digest": packet_digest,
            "task_id": task_ids[0] if task_ids else "",
            "task_ids": ",".join(task_ids),
            "status_root": str(self.config.status_root or ""),
            "run_id": self.config.run_id,
        }
        if self.hooks.bridge_readback:
            snapshot = dict(self.hooks.bridge_readback(context))
            self._validate_bridge_readback_schema(
                snapshot, packet_id=packet_id, task_ids=task_ids
            )
            self.recorder.record("bridge-filesystem-readback", snapshot)
            return snapshot
        if self.config.bridge_readback_command:
            process = self._run_hook_command(
                "bridge-filesystem-readback", self.config.bridge_readback_command, context
            )
            try:
                payload = json.loads(process.stdout)
            except json.JSONDecodeError as exc:
                raise VerificationError(
                    "BRIDGE_READBACK_INVALID",
                    "Bridge readback command did not emit JSON",
                ) from exc
            if not isinstance(payload, Mapping):
                raise VerificationError("BRIDGE_READBACK_INVALID", "Bridge readback must be an object")
            snapshot = dict(payload)
            self._validate_bridge_readback_schema(
                snapshot, packet_id=packet_id, task_ids=task_ids
            )
            self.recorder.record("bridge-filesystem-readback", snapshot)
            return snapshot
        if self.config.status_root is None:
            raise VerificationError(
                "BRIDGE_READBACK_HOOK_REQUIRED",
                "Provide --status-root or --bridge-readback-command for authoritative bridge proof",
            )
        inbox = self.config.status_root / ".orchestrator" / "assistant-dev-packets"
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", packet_id)
        pending = inbox / "pending" / f"{safe_id}.json"
        processing = inbox / "processing" / f"{safe_id}.json"
        processed = inbox / "processed" / f"{safe_id}.json"
        receipt = inbox / "receipts" / f"{safe_id}.json"
        bridge_receipt = (
            json.loads(receipt.read_text(encoding="utf-8")) if receipt.is_file() else None
        )
        expected_admission_rel = (
            "ai-task-archive/tasks/assistant-dev-bridge-admissions/"
            f"{safe_id[:160] or 'packet'}--{packet_digest[:16]}.json"
        )
        admission_rel = expected_admission_rel
        if isinstance(bridge_receipt, Mapping):
            result = (
                bridge_receipt.get("result")
                if isinstance(bridge_receipt.get("result"), Mapping)
                else {}
            )
            admission = result.get("admissionRecord") or result.get("admission_record") or {}
            if isinstance(admission, Mapping):
                admission_rel = str(admission.get("admission_record_path") or "")
            if admission_rel != expected_admission_rel:
                raise VerificationError(
                    "BRIDGE_READBACK_INVALID",
                    "Supervisor receipt admission path does not match the signed packet",
                    details={"expected": expected_admission_rel, "observed": admission_rel},
                )
        admission_path = self.config.status_root / admission_rel
        try:
            admission_path.resolve().relative_to(self.config.status_root.resolve())
        except ValueError as exc:
            raise VerificationError(
                "BRIDGE_READBACK_INVALID",
                "Supervisor admission path escapes the authoritative status root",
            ) from exc
        if admission_path.is_symlink():
            raise VerificationError(
                "BRIDGE_READBACK_INVALID",
                "Supervisor admission record must not be a symbolic link",
            )
        status_file = self.config.status_root / "ai-status.json"
        status_payload = (
            json.loads(status_file.read_text(encoding="utf-8")) if status_file.is_file() else {}
        )
        status_tasks = status_payload.get("tasks") if isinstance(status_payload, Mapping) else []
        status_by_id = {
            str(item.get("id") or ""): dict(item)
            for item in status_tasks
            if isinstance(item, Mapping) and item.get("id")
        }
        snapshot = {
            "packet_id": packet_id,
            "authoritative": True,
            "source": "local_bridge_filesystem",
            "pending_exists": pending.is_file(),
            "processing_exists": processing.is_file(),
            "processed_exists": processed.is_file(),
            "receipt_exists": receipt.is_file(),
            "pending": json.loads(pending.read_text(encoding="utf-8")) if pending.is_file() else None,
            "processing": json.loads(processing.read_text(encoding="utf-8"))
            if processing.is_file()
            else None,
            "processed": json.loads(processed.read_text(encoding="utf-8")) if processed.is_file() else None,
            # Keep the bridge receipt visible in evidence.  The exact key
            # ``receipt`` is reserved for redacting OpenClaw repair
            # capabilities, so this durable supervisor artifact uses a
            # domain-specific name.
            "bridge_receipt": bridge_receipt,
            "admission_record": json.loads(admission_path.read_text(encoding="utf-8"))
            if admission_path.is_file()
            else None,
            "active_task_records": {
                task_id: status_by_id.get(task_id) for task_id in task_ids
            },
        }
        self._validate_bridge_readback_schema(
            snapshot, packet_id=packet_id, task_ids=task_ids
        )
        self.recorder.record("bridge-filesystem-readback", snapshot)
        return snapshot

    def _validate_bridge_readback_schema(
        self,
        snapshot: Mapping[str, Any],
        *,
        packet_id: str,
        task_ids: Sequence[str],
    ) -> None:
        violations: list[str] = []
        if snapshot.get("authoritative") is not True:
            violations.append("authoritative must be true")
        if str(snapshot.get("packet_id") or snapshot.get("packetId") or "") != packet_id:
            violations.append("packet_id does not match")
        for field in (
            "pending_exists",
            "processing_exists",
            "processed_exists",
            "receipt_exists",
        ):
            if not isinstance(snapshot.get(field), bool):
                violations.append(f"{field} must be a boolean")
        if "admission_record" not in snapshot:
            violations.append("admission_record field is required")
        elif snapshot.get("admission_record") is not None and not isinstance(
            snapshot.get("admission_record"), Mapping
        ):
            violations.append("admission_record must be an object or null")
        if "bridge_receipt" not in snapshot:
            violations.append("bridge_receipt field is required")
        elif snapshot.get("bridge_receipt") is not None and not isinstance(
            snapshot.get("bridge_receipt"), Mapping
        ):
            violations.append("bridge_receipt must be an object or null")
        active_records = snapshot.get("active_task_records")
        if not isinstance(active_records, Mapping):
            violations.append("active_task_records must be an object")
        elif set(str(key) for key in active_records) != set(task_ids):
            violations.append("active_task_records keys must exactly match task ids")
        if violations:
            raise VerificationError(
                "BRIDGE_READBACK_INVALID",
                "Bridge filesystem readback is incomplete or non-authoritative",
                details={"violations": violations, "snapshot": dict(snapshot)},
            )

    @staticmethod
    def _bridge_packet_from_envelope(value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        packet = value.get("taskPacket") or value.get("task_packet") or value.get("packet")
        if isinstance(packet, Mapping):
            return packet
        return value if value.get("packetId") or value.get("packet_id") else {}

    def _assert_bridge_pending(
        self,
        snapshot: Mapping[str, Any],
        *,
        packet: Mapping[str, Any],
        packet_digest: str,
        task_ids: Sequence[str],
    ) -> None:
        expected_state = {
            "pending_exists": True,
            "processing_exists": False,
            "processed_exists": False,
            "receipt_exists": False,
        }
        violations = [
            f"{field} must be {expected!r}"
            for field, expected in expected_state.items()
            if snapshot.get(field) is not expected
        ]
        pending_packet = self._bridge_packet_from_envelope(snapshot.get("pending"))
        observed_packet_id = str(
            pending_packet.get("packetId") or pending_packet.get("packet_id") or ""
        )
        if observed_packet_id != str(packet.get("packetId") or packet.get("packet_id") or ""):
            violations.append("pending envelope packet id mismatch")
        elif bridge_packet_digest(pending_packet) != packet_digest:
            violations.append("pending envelope packet digest mismatch")
        if snapshot.get("admission_record") is not None:
            violations.append("admission record exists before supervisor start")
        active_records = snapshot.get("active_task_records") or {}
        if any(active_records.get(task_id) is not None for task_id in task_ids):
            violations.append("active ai-status task exists before supervisor start")
        if violations:
            raise VerificationError(
                "BRIDGE_PENDING_ADMISSION_FAILED",
                "Queued packet was not durably pending before supervisor start",
                details={"violations": violations},
            )

    def _assert_bridge_admission_record(
        self,
        record: Any,
        *,
        packet: Mapping[str, Any],
        packet_digest: str,
        tasks: Sequence[Mapping[str, Any]],
    ) -> None:
        if not isinstance(record, Mapping):
            raise VerificationError(
                "BRIDGE_ADMISSION_RECORD_INVALID",
                "Supervisor admission record is missing",
            )
        value = dict(record)
        packet_id = str(packet.get("packetId") or packet.get("packet_id") or "")
        conversation_id = str(
            packet.get("sourceConversationId")
            or packet.get("source_conversation_id")
            or ""
        )
        expected = {
            "schema": BRIDGE_ADMISSION_VERSION,
            "record_kind": "assistant_dev_bridge_admission",
            "packet_version": str(
                packet.get("version") or "pantheon.assistant.dev-task.v1"
            ),
            "packet_id": packet_id,
            "packet_digest": packet_digest,
            "conversation_id": conversation_id,
            "mode": str(packet.get("mode") or ""),
            "intent": str(packet.get("intent") or "generate_sa_sd_and_dispatch"),
            "emitted_at": packet.get("emittedAt") or packet.get("emitted_at"),
            "constraints": packet.get("constraints"),
        }
        violations = []
        for field, expected_value in expected.items():
            if canonical_json(value.get(field)) != canonical_json(expected_value):
                violations.append(f"{field} mismatch")
        if value.get("durable") is not True:
            violations.append("durable must be true")
        expected_path = (
            "ai-task-archive/tasks/assistant-dev-bridge-admissions/"
            f"{re.sub(r'[^A-Za-z0-9._-]+', '_', packet_id)[:160] or 'packet'}"
            f"--{packet_digest[:16]}.json"
        )
        if str(value.get("admission_record_path") or "") != expected_path:
            violations.append("admission_record_path mismatch")
        if not str(value.get("admitted_at") or "").strip():
            violations.append("admitted_at is required")
        payload_digest = str(value.get("record_payload_sha256") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", payload_digest):
            violations.append("record_payload_sha256 is missing")
        elif payload_digest != _ascii_json_hash(
            {
                key: item
                for key, item in value.items()
                if key != "record_payload_sha256"
            }
        ):
            violations.append("record_payload_sha256 mismatch")
        expected_actor = packet.get("actor") if isinstance(packet.get("actor"), Mapping) else {}
        if canonical_json(value.get("actor")) != canonical_json(expected_actor):
            violations.append("actor mismatch")
        expected_turn_ids = list(
            packet.get("sourceTurnIds") or packet.get("source_turn_ids") or []
        )
        if value.get("source_turn_ids") != expected_turn_ids:
            violations.append("source_turn_ids mismatch")
        expected_documents = [
            dict(item)
            for item in (packet.get("documents") or [])
            if isinstance(item, Mapping)
        ]
        if canonical_json(value.get("documents")) != canonical_json(expected_documents):
            violations.append("documents mismatch")
        if value.get("audit_conversation_href") != (
            packet.get("auditConversationHref") or packet.get("audit_conversation_href")
        ):
            violations.append("audit_conversation_href mismatch")

        expected_tasks = []
        expected_dispatch: dict[str, Mapping[str, Any]] = {}
        for task in tasks:
            task_spec = bridge_task_spec(task)
            task_id = task_spec["id"]
            expected_tasks.append(
                {
                    "task_id": task_id,
                    "task_spec_hash": _ascii_json_hash(task_spec),
                    "task_spec": task_spec,
                }
            )
            expected_dispatch[task_id] = task
        if canonical_json(value.get("tasks")) != canonical_json(expected_tasks):
            violations.append("tasks mismatch")
        dispatch_records = value.get("dispatch_records")
        if not isinstance(dispatch_records, list) or len(dispatch_records) != len(tasks):
            violations.append("dispatch_records mismatch")
        else:
            observed_ids: set[str] = set()
            for dispatch in dispatch_records:
                if not isinstance(dispatch, Mapping):
                    violations.append("dispatch record is not an object")
                    continue
                task_id = str(dispatch.get("taskId") or dispatch.get("task_id") or "")
                observed_ids.add(task_id)
                task = expected_dispatch.get(task_id)
                if task is None:
                    violations.append(f"unexpected dispatch task {task_id!r}")
                    continue
                if dispatch.get("status") != "dispatched":
                    violations.append(f"dispatch status for {task_id} is not dispatched")
                if dispatch.get("owner") != task.get("owner"):
                    violations.append(f"dispatch owner for {task_id} mismatch")
                if dispatch.get("reviewer") != task.get("reviewer"):
                    violations.append(f"dispatch reviewer for {task_id} mismatch")
                if dispatch.get("error") is not None:
                    violations.append(f"dispatch error for {task_id} is not null")
            if observed_ids != set(expected_dispatch):
                violations.append("dispatch task ids mismatch")
        if violations:
            raise VerificationError(
                "BRIDGE_ADMISSION_RECORD_INVALID",
                "Supervisor admission record did not preserve exact packet provenance",
                details={"packet_id": packet_id, "violations": violations},
            )

    def _assert_bridge_terminal(
        self,
        snapshot: Mapping[str, Any],
        *,
        packet: Mapping[str, Any],
        packet_digest: str,
        tasks: Sequence[Mapping[str, Any]],
        polled_receipt: Mapping[str, Any],
    ) -> None:
        task_ids = [str(task.get("id") or "") for task in tasks]
        expected_state = {
            "pending_exists": False,
            "processing_exists": False,
            "processed_exists": True,
            "receipt_exists": True,
        }
        violations = [
            f"{field} must be {expected!r}"
            for field, expected in expected_state.items()
            if snapshot.get(field) is not expected
        ]
        processed_packet = self._bridge_packet_from_envelope(snapshot.get("processed"))
        packet_id = str(packet.get("packetId") or packet.get("packet_id") or "")
        if str(processed_packet.get("packetId") or processed_packet.get("packet_id") or "") != packet_id:
            violations.append("processed packet id mismatch")
        elif bridge_packet_digest(processed_packet) != packet_digest:
            violations.append("processed packet digest mismatch")

        receipt = snapshot.get("bridge_receipt")
        if not isinstance(receipt, Mapping):
            violations.append("durable receipt object is missing")
            receipt = {}
        if str(receipt.get("packetId") or receipt.get("packet_id") or "") != packet_id:
            violations.append("durable receipt packet id mismatch")
        if str(receipt.get("status") or "") != "processed":
            violations.append("durable receipt status must be processed")
        result = receipt.get("result") if isinstance(receipt.get("result"), Mapping) else {}
        if str(result.get("packetId") or result.get("packet_id") or "") != packet_id:
            violations.append("dispatch result packet id mismatch")
        if result.get("dryRun") is True or result.get("dry_run") is True:
            violations.append("dispatch result must not be dry-run")
        if result.get("errors") not in ([], None):
            violations.append("dispatch result contains errors")
        audit_refs = result.get("auditRefs") or result.get("audit_refs") or {}
        if not isinstance(audit_refs, Mapping):
            violations.append("dispatch auditRefs missing")
        else:
            if str(audit_refs.get("packetDigest") or audit_refs.get("packet_digest") or "") != packet_digest:
                violations.append("dispatch audit packet digest mismatch")
            observed_ids = audit_refs.get("taskIds") or audit_refs.get("task_ids") or []
            if set(str(item) for item in observed_ids) != set(task_ids):
                violations.append("dispatch audit task ids mismatch")
        task_records = result.get("taskRecords") or result.get("task_records") or []
        if not isinstance(task_records, list) or {
            str(item.get("taskId") or item.get("task_id") or "")
            for item in task_records
            if isinstance(item, Mapping)
        } != set(task_ids):
            violations.append("dispatch task records mismatch")
        admission_record = snapshot.get("admission_record")
        result_admission = result.get("admissionRecord") or result.get("admission_record")
        if not isinstance(result_admission, Mapping):
            violations.append("dispatch result admissionRecord missing")
        elif canonical_json(result_admission) != canonical_json(admission_record):
            violations.append("dispatch result admissionRecord mismatch")
        if str(polled_receipt.get("packetId") or polled_receipt.get("packet_id") or "") != packet_id:
            violations.append("orchestrator receipt packet id mismatch")
        if str(polled_receipt.get("status") or "") != "processed":
            violations.append("orchestrator receipt is not processed")
        if violations:
            raise VerificationError(
                "BRIDGE_CORRELATION_FAILED",
                "Supervisor processed artifacts did not correlate exactly",
                details={"violations": violations},
            )
        self._assert_bridge_admission_record(
            snapshot.get("admission_record"),
            packet=packet,
            packet_digest=packet_digest,
            tasks=tasks,
        )
        self._assert_active_bridge_tasks(
            snapshot.get("active_task_records"),
            packet=packet,
            packet_digest=packet_digest,
            tasks=tasks,
        )

    def _assert_active_bridge_tasks(
        self,
        records: Any,
        *,
        packet: Mapping[str, Any],
        packet_digest: str,
        tasks: Sequence[Mapping[str, Any]],
    ) -> None:
        if not isinstance(records, Mapping):
            raise VerificationError(
                "BRIDGE_ACTIVE_TASK_PROVENANCE_INVALID",
                "Authoritative ai-status task readback is missing",
            )
        packet_id = str(packet.get("packetId") or packet.get("packet_id") or "")
        expected_conversation = str(
            packet.get("sourceConversationId")
            or packet.get("source_conversation_id")
            or ""
        )
        expected_turns = list(
            packet.get("sourceTurnIds") or packet.get("source_turn_ids") or []
        )
        expected_documents = [
            dict(item)
            for item in (packet.get("documents") or [])
            if isinstance(item, Mapping)
        ]
        violations: list[str] = []
        for task in tasks:
            spec = bridge_task_spec(task)
            task_id = spec["id"]
            record = records.get(task_id)
            if not isinstance(record, Mapping):
                violations.append(f"active task {task_id} is missing")
                continue
            for field in ("id", "title", "owner", "reviewer", "phase"):
                expected = spec[field] if field != "phase" else (spec[field] or "Unassigned")
                if record.get(field) != expected:
                    violations.append(f"active task {task_id} {field} mismatch")
            for field in ("depends_on", "artifacts", "acceptance"):
                if record.get(field) != spec[field]:
                    violations.append(f"active task {task_id} {field} mismatch")
            if record.get("summary_zh") != spec["summary"]:
                violations.append(f"active task {task_id} summary mismatch")
            if str(record.get("status") or "") in {
                "done",
                "cancelled",
                "archived",
                "superseded",
            }:
                violations.append(f"active task {task_id} is terminal")
            bridge = record.get("dev_bridge")
            if not isinstance(bridge, Mapping):
                violations.append(f"active task {task_id} dev_bridge provenance missing")
                continue
            expected_bridge = {
                "packet_id": packet_id,
                "packet_digest": packet_digest,
                "task_spec_hash": _ascii_json_hash(spec),
                "task_spec": spec,
                "conversation_id": expected_conversation,
                "source_turn_ids": expected_turns,
                "documents": expected_documents,
                "audit_conversation_href": packet.get("auditConversationHref")
                or packet.get("audit_conversation_href"),
                "emitted_at": packet.get("emittedAt") or packet.get("emitted_at"),
                "intent": packet.get("intent") or "generate_sa_sd_and_dispatch",
                "mode": packet.get("mode"),
                "actor": packet.get("actor"),
            }
            for field, expected in expected_bridge.items():
                if canonical_json(bridge.get(field)) != canonical_json(expected):
                    violations.append(f"active task {task_id} dev_bridge.{field} mismatch")
        if violations:
            raise VerificationError(
                "BRIDGE_ACTIVE_TASK_PROVENANCE_INVALID",
                "Active ai-status tasks did not preserve signed bridge provenance",
                details={"violations": violations},
            )

    def _dev_docs_and_bridge(self, *, session_id: str, sentinel_rel: str) -> tuple[str, list[str]]:
        self._enter("dev-docs-generate-and-queue")
        stop_context = {
            "task_id": self.config.task_id,
            "run_id": self.config.run_id,
            "service": "supervisor",
        }
        # Stop before queue admission so a pre-existing receipt cannot be
        # mistaken for restart recovery.  A failed later phase is compensated in
        # execute() by starting the supervisor again.
        self._supervisor_stop_attempted = True
        self._supervisor_stop_report = self._run_lifecycle_hook(
            "stop-supervisor",
            self.config.supervisor_stop_command,
            stop_context,
            service="supervisor",
            action="stop",
        )
        body = {
            "conversationId": session_id,
            "featureSummary": (
                "Verify hosted Management AI repair sentinel, SA/SD generation, and supervisor bridge."
            ),
            "affectedModules": [
                "pantheon:bff-assistant",
                "pantheon:openclaw-gateway-adapter",
                "pantheon:assistant-dev-bridge",
                sentinel_rel,
            ],
            "proposedOwner": "Codex",
            "proposedReviewer": "Claude",
            "archive": True,
            "emitTaskPacket": True,
            "queueTaskPacket": True,
            "extraContext": {
                "taskId": self.config.task_id,
                "runId": self.config.run_id,
                "repairSentinel": sentinel_rel,
            },
        }
        generated = self._http(
            "dev-docs-generate",
            "POST",
            "/bff/assistant/dev-docs/generate",
            body=body,
            headers=self._auth_headers(idempotency_phase="dev-docs-generate"),
            expected={200, 201},
            timeout=self.config.provider_timeout_seconds,
        )
        data = self._data(generated.payload)
        meta = generated.payload.get("meta") if isinstance(generated.payload, Mapping) else {}
        meta = dict(meta) if isinstance(meta, Mapping) else {}
        dev_packet_id = str(data.get("packetId") or data.get("packet_id") or "")
        task_packet = meta.get("taskPacket") or meta.get("task_packet") or {}
        if not isinstance(task_packet, Mapping):
            task_packet = {}
        bridge_packet_id = str(task_packet.get("packetId") or task_packet.get("packet_id") or "")
        queue_receipt = meta.get("taskPacketQueueReceipt") or meta.get("task_packet_queue_receipt") or {}
        queued = meta.get("taskPacketQueued")
        if not dev_packet_id or not bridge_packet_id or queued is not True:
            raise VerificationError(
                "DEV_BRIDGE_QUEUE_FAILED",
                "Dev-doc generation did not return archived and queued packet identities",
                details={"dev_packet_id": dev_packet_id, "bridge_packet_id": bridge_packet_id, "queued": queued},
            )
        if not isinstance(queue_receipt, Mapping):
            raise VerificationError(
                "DEV_BRIDGE_QUEUE_FAILED",
                "Initial task-packet queue receipt is missing",
            )
        queue_status = str(queue_receipt.get("status") or "")
        if queue_status != "queued" or str(
            queue_receipt.get("packetId") or queue_receipt.get("packet_id") or ""
        ) != bridge_packet_id:
            raise VerificationError(
                "DEV_BRIDGE_QUEUE_FAILED",
                f"Initial task-packet queue status was {queue_status!r}",
            )
        archived = self._http(
            "dev-docs-archive-readback",
            "GET",
            f"/bff/assistant/dev-docs/{urllib.parse.quote(dev_packet_id, safe='')}",
            headers=self._auth_headers(),
            expected={200},
        )
        archived_data = self._data(archived.payload)
        if str(archived_data.get("packetId") or archived_data.get("packet_id") or "") != dev_packet_id:
            raise VerificationError(
                "DEV_DOC_ARCHIVE_CORRELATION_FAILED",
                "Archived SA/SD packet identity does not match generation",
            )
        documents = task_packet.get("documents") if isinstance(task_packet.get("documents"), list) else []
        document_kinds = {str(item.get("kind") or "") for item in documents if isinstance(item, Mapping)}
        required_document_kinds = {"SYSTEM_ANALYSIS", "SYSTEM_DESIGN"}
        if len(documents) < 2 or not required_document_kinds.issubset(
            {kind.upper() for kind in document_kinds}
        ):
            raise VerificationError(
                "DEV_DOC_ARCHIVE_CORRELATION_FAILED",
                "Signed task packet did not correlate both SA and SD documents",
                details={"document_kinds": sorted(document_kinds)},
            )
        tasks = task_packet.get("tasks") if isinstance(task_packet.get("tasks"), list) else []
        task_ids = [
            str(task.get("id") or "") for task in tasks if isinstance(task, Mapping) and task.get("id")
        ]
        if not task_ids:
            raise VerificationError("DEV_TASK_IDS_MISSING", "Signed DevTaskPacket contains no task IDs")
        packet_digest = bridge_packet_digest(task_packet)
        pending_readback = self._bridge_readback(
            packet_id=bridge_packet_id,
            packet_digest=packet_digest,
            task_ids=task_ids,
        )
        self._assert_bridge_pending(
            pending_readback,
            packet=task_packet,
            packet_digest=packet_digest,
            task_ids=task_ids,
        )

        duplicate = self._http(
            "dev-bridge-exact-packet-replay",
            "POST",
            "/bff/assistant/dev-bridge/task-packet",
            body={"devDocPacket": data, "queueTaskPacket": True},
            headers=self._auth_headers(idempotency_phase="dev-bridge-exact-packet-replay"),
            expected={200, 201},
        )
        duplicate_meta = duplicate.payload.get("meta") if isinstance(duplicate.payload, Mapping) else {}
        duplicate_meta = dict(duplicate_meta) if isinstance(duplicate_meta, Mapping) else {}
        duplicate_receipt = duplicate_meta.get("taskPacketQueueReceipt") or {}
        if not isinstance(duplicate_receipt, Mapping):
            duplicate_receipt = {}
        duplicate_status = str(duplicate_receipt.get("status") or "")
        duplicate_queued = duplicate_meta.get("taskPacketQueued")
        if duplicate_queued is True or duplicate_status not in {"duplicate", "replay_rejected"}:
            raise VerificationError(
                "DEV_BRIDGE_DUPLICATE_ACCEPTED",
                "Exact DevTaskPacket replay was not rejected",
                details={"status": duplicate_status, "queued": duplicate_queued},
            )

        self._enter("supervisor-restart-and-receipt")
        before_instance_id = str(
            (self._supervisor_stop_report or {}).get("before_instance_id")
            or (self._supervisor_stop_report or {}).get("beforeInstanceId")
            or ""
        )
        self._run_lifecycle_hook(
            "start-supervisor",
            self.config.supervisor_restart_command,
            {
                "packet_id": bridge_packet_id,
                "task_id": task_ids[0],
                "run_id": self.config.run_id,
                "service": "supervisor",
            },
            service="supervisor",
            action="start",
            expected_before_instance_id=before_instance_id,
        )
        self._supervisor_stop_attempted = False
        self._supervisor_stop_report = None
        receipt = self._poll_bridge_receipt(bridge_packet_id)
        readback = self._bridge_readback(
            packet_id=bridge_packet_id,
            packet_digest=packet_digest,
            task_ids=task_ids,
        )
        self._assert_bridge_terminal(
            readback,
            packet=task_packet,
            packet_digest=packet_digest,
            tasks=[dict(task) for task in tasks if isinstance(task, Mapping)],
            polled_receipt=receipt,
        )
        self.recorder.record(
            "bridge-correlation-proof",
            {
                "dev_doc_packet_id": dev_packet_id,
                "bridge_packet_id": bridge_packet_id,
                "task_ids": task_ids,
                "packet_digest": packet_digest,
                "queue_receipt": queue_receipt,
                "processed_receipt": receipt,
                "filesystem_readback": readback,
            },
        )
        return bridge_packet_id, task_ids

    def _poll_bridge_receipt(self, packet_id: str) -> Mapping[str, Any]:
        last: Mapping[str, Any] = {}
        for attempt in range(self.config.poll_attempts + 1):
            result = self._http(
                f"bridge-receipt-poll-{attempt}",
                "GET",
                "/bff/assistant/orchestrator/status",
                headers=self._auth_headers(),
                expected={200},
            )
            data = self._data(result.payload)
            bridge = data.get("assistantDevBridge") or data.get("assistant_dev_bridge") or {}
            if isinstance(bridge, Mapping):
                receipts = bridge.get("recentReceipts") or bridge.get("recent_receipts") or []
                for receipt in receipts if isinstance(receipts, list) else []:
                    if not isinstance(receipt, Mapping):
                        continue
                    observed = str(receipt.get("packetId") or receipt.get("packet_id") or "")
                    if observed == packet_id:
                        last = dict(receipt)
                        if str(receipt.get("status") or "") == "processed":
                            return last
                        if str(receipt.get("status") or "") in {"failed", "error", "replay_rejected"}:
                            raise VerificationError(
                                "BRIDGE_RECEIPT_FAILED",
                                f"Supervisor receipt entered {receipt.get('status')!r}",
                                details={"bridge_receipt": dict(receipt)},
                            )
            if attempt < self.config.poll_attempts:
                self.hooks.sleep(self.config.poll_interval_seconds)
        raise VerificationError(
            "BRIDGE_RECEIPT_TIMEOUT",
            "Supervisor did not produce a processed receipt in the bounded poll window",
            details={"packet_id": packet_id, "last_receipt": dict(last)},
        )

    def _post_deactivation_negative(
        self,
        *,
        repair: Mapping[str, Any],
        sentinel_rel: str,
        sentinel_content: str,
        session_id: str,
    ) -> None:
        self._enter("deactivate-and-post-write-negative")
        denied_rel = f"{sentinel_rel}.post-deactivate-denied"
        before_original = self._sentinel_snapshot(
            repair=repair,
            sentinel_rel=sentinel_rel,
            expected_content=sentinel_content,
            label="before-deactivation",
        )
        before_denied = self._sentinel_snapshot(
            repair=repair,
            sentinel_rel=denied_rel,
            expected_content=None,
            label="before-post-deactivation-denied",
            expect_exists=False,
        )
        before_dirty = tuple(sorted(self._strings(before_denied.get("dirty_paths"))))
        if before_dirty != tuple(sorted(self._strings(before_original.get("dirty_paths")))):
            raise VerificationError(
                "POST_DEACTIVATION_BASELINE_INVALID",
                "Task-worktree readbacks disagree before the denied write",
            )
        self._deactivate("deactivate-repair")
        self._post_rejected(
            "post-deactivate-prepare",
            "/bff/assistant/repair-worktrees/prepare",
            body=self._prepare_payload(),
            phase_key="post-deactivate-prepare",
        )
        denied_body = {
            "sessionId": session_id,
            "conversationId": session_id,
            "focus": "all",
            "useAssistantProvider": True,
            "question": f"Write {denied_rel}; this request must be rejected because control mode is inactive.",
            "openclaw": {"repair": dict(repair)},
        }
        self._post_rejected(
            "post-deactivate-provider-write",
            "/bff/management/nl/ask",
            body=denied_body,
            phase_key="post-deactivate-provider-write",
        )
        after_original = self._sentinel_snapshot(
            repair=repair,
            sentinel_rel=sentinel_rel,
            expected_content=sentinel_content,
            label="post-deactivation",
        )
        after_denied = self._sentinel_snapshot(
            repair=repair,
            sentinel_rel=denied_rel,
            expected_content=None,
            label="post-deactivation-denied",
            expect_exists=False,
        )
        after_dirty = tuple(sorted(self._strings(after_denied.get("dirty_paths"))))
        if after_dirty != before_dirty:
            raise VerificationError(
                "POST_DEACTIVATION_WORKTREE_MUTATED",
                "Task-worktree dirty paths changed after a denied post-deactivation write",
                details={"before": list(before_dirty), "after": list(after_dirty)},
            )
        for field in ("head", "branch", "repo_root"):
            if before_original.get(field) != after_original.get(field):
                raise VerificationError(
                    "POST_DEACTIVATION_WORKTREE_MUTATED",
                    f"Task-worktree {field} changed after a denied write",
                    details={
                        "field": field,
                        "before": before_original.get(field),
                        "after": after_original.get(field),
                    },
                )
        if self._shared_baseline is not None:
            after = self._shared_snapshot("final", candidate=denied_rel)
            self._assert_shared_head_and_candidate_unchanged(
                self._shared_baseline,
                after,
                phase="post-deactivation",
            )

    def _validate_run_requirements(self) -> None:
        """Validate every externally supplied run boundary before mutation begins."""

        self._enter("run-requirements")
        self._require_env("PANTHEON_ASSISTANT_CONTROL_PASSPHRASE")
        for profile in ("VIEWER", "NO_MFA"):
            self._require_env(f"MAI_BFF_{profile}_CLIENT_ID")
            self._require_env(f"MAI_BFF_{profile}_CLIENT_SECRET")
        missing_commands = [
            label
            for label, command in (
                ("--bff-restart-command", self.config.bff_restart_command),
                ("--adapter-restart-command", self.config.adapter_restart_command),
                ("--supervisor-stop-command", self.config.supervisor_stop_command),
                ("--supervisor-restart-command", self.config.supervisor_restart_command),
            )
            if not command
        ]
        if missing_commands:
            raise VerificationError(
                "RESTART_HOOKS_REQUIRED",
                "run mode requires all restart command hooks",
                details={"missing": missing_commands},
            )
        shared_is_local = Path(self.config.shared_checkout_path).is_dir()
        if not (self.hooks.shared_readback or self.config.shared_readback_command or shared_is_local):
            raise VerificationError(
                "SHARED_READBACK_HOOK_REQUIRED",
                "Shared checkout is not local; provide --shared-readback-command",
            )
        if not (
            self.hooks.bridge_readback
            or self.config.bridge_readback_command
            or self.config.status_root is not None
        ):
            raise VerificationError(
                "BRIDGE_READBACK_HOOK_REQUIRED",
                "Provide --status-root or --bridge-readback-command for authoritative bridge proof",
            )

    def run(self) -> None:
        self.preflight()
        self._validate_run_requirements()
        self._enter("shared-checkout-baseline")
        self._shared_baseline = self._shared_snapshot("baseline")
        self._security_negative_matrix()
        self._debug_phase()
        self._enter("activate-kernel-repair")
        repair_session_id = f"{self.config.task_id}-{self.config.run_id}-repair"
        self._activate(
            "kernel_repair",
            phase_key="activate-repair",
            management_session_id=repair_session_id,
        )
        self._repair_negative_matrix()
        repair = self._repair_positive()
        completed, sentinel_rel, sentinel_content, _ = self._repair_provider_and_restart(
            repair,
            session_id=repair_session_id,
        )

        # A BFF restart intentionally drops active control mode; exact replay must
        # survive before we re-activate for the archive/bridge phase.
        self._duplicate_and_conversation_proof(
            completed,
            repair=repair,
            sentinel_rel=sentinel_rel,
        )
        self._enter("reactivate-repair-after-bff-restart")
        self._activate(
            "kernel_repair",
            phase_key="reactivate-repair",
            management_session_id=repair_session_id,
        )
        self._dev_docs_and_bridge(session_id=repair_session_id, sentinel_rel=sentinel_rel)
        self._post_deactivation_negative(
            repair=repair,
            sentinel_rel=sentinel_rel,
            sentinel_content=sentinel_content,
            session_id=repair_session_id,
        )

    def execute(self) -> dict[str, Any]:
        started_at = utc_now()
        primary_error: VerificationError | None = None
        try:
            if self.config.mode == "preflight":
                self.preflight()
            else:
                self.run()
        except VerificationError as exc:
            primary_error = exc
        except Exception as exc:
            primary_error = VerificationError(
                "UNEXPECTED_VERIFIER_FAILURE",
                f"{type(exc).__name__}: {exc}",
            )
        finally:
            # This is an actual finally boundary so interrupts and unexpected
            # exceptions cannot strand kernel control mode or a stopped
            # supervisor, even when no evidence finalization is possible.
            cleanup_errors = self._cleanup_runtime_state()
        if primary_error is not None:
            if cleanup_errors:
                primary_error.details["cleanup_failures"] = [
                    error.to_dict() for error in cleanup_errors
                ]
            self.recorder.blocker(primary_error, phase=self.phase)
            self.recorder.finalize()
            raise primary_error
        if cleanup_errors:
            cleanup_error = VerificationError(
                "RUNTIME_CLEANUP_FAILED",
                "Verifier proof completed but safe runtime cleanup failed",
                details={"failures": [error.to_dict() for error in cleanup_errors]},
            )
            self.recorder.blocker(cleanup_error, phase=self.phase)
            self.recorder.finalize()
            raise cleanup_error

        self._enter("complete")
        result = {
            "status": "pass",
            "task_id": TASK_ID,
            "mode": self.config.mode,
            "run_id": self.config.run_id,
            "started_at": started_at,
            "completed_at": utc_now(),
            "phase_history": list(self.phase_history),
            "idempotency_keys": dict(self._idempotency),
        }
        self.recorder.record("hosted-proof", result)
        self.recorder.finalize()
        return result

    def _cleanup_runtime_state(self) -> list[VerificationError]:
        """Best-effort compensation that never hides the primary verifier error."""

        errors: list[VerificationError] = []
        if self._supervisor_stop_attempted:
            before_instance_id = str(
                (self._supervisor_stop_report or {}).get("before_instance_id")
                or (self._supervisor_stop_report or {}).get("beforeInstanceId")
                or ""
            )
            try:
                self._run_lifecycle_hook(
                    "cleanup-start-supervisor",
                    self.config.supervisor_restart_command,
                    {
                        "task_id": self.config.task_id,
                        "run_id": self.config.run_id,
                        "service": "supervisor",
                    },
                    service="supervisor",
                    action="start",
                    expected_before_instance_id=before_instance_id or None,
                )
                self._supervisor_stop_attempted = False
                self._supervisor_stop_report = None
            except Exception as exc:
                errors.append(
                    exc
                    if isinstance(exc, VerificationError)
                    else VerificationError(
                        "SUPERVISOR_CLEANUP_EXCEPTION",
                        f"{type(exc).__name__}: {exc}",
                    )
                )
        if self._activation_active:
            try:
                self._deactivate("cleanup-deactivate")
            except Exception as exc:
                errors.append(
                    exc
                    if isinstance(exc, VerificationError)
                    else VerificationError(
                        "CONTROL_MODE_CLEANUP_EXCEPTION",
                        f"{type(exc).__name__}: {exc}",
                    )
                )
        if errors:
            self.recorder.record(
                "runtime-cleanup-failures",
                {"failures": [error.to_dict() for error in errors]},
            )
        elif self.config.mode == "run":
            self.recorder.record(
                "runtime-cleanup-complete",
                {"control_mode_active": False, "supervisor_stopped": False},
            )
        return errors


def _parse_command(value: str) -> tuple[str, ...]:
    return tuple(shlex.split(value)) if str(value or "").strip() else ()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Credential environment: preflight requires MAI_BFF_CLIENT_ID/"
            "MAI_BFF_CLIENT_SECRET (or DEV_BFF_OIDC_CLIENT_ID/"
            "DEV_BFF_OIDC_CLIENT_SECRET). Run additionally requires "
            "PANTHEON_ASSISTANT_CONTROL_PASSPHRASE, "
            "MAI_BFF_VIEWER_CLIENT_ID/SECRET, and "
            "MAI_BFF_NO_MFA_CLIENT_ID/SECRET. Secrets are never accepted as CLI flags."
        ),
    )
    parser.add_argument("--mode", choices=("preflight", "run"), required=True)
    parser.add_argument("--bff-base-url", default=DEFAULT_BFF_BASE_URL)
    parser.add_argument("--frontend-deployment-url", default=DEFAULT_FE_DEPLOYMENT_URL)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--expected-bff-sha", default="")
    parser.add_argument("--expected-frontend-sha", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--repo-key", default="pantheon")
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--expected-branch", default="")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--merge-target", default="dev")
    parser.add_argument(
        "--shared-checkout-path",
        default=os.getenv("PANTHEON_STATUS_ROOT_HOST", "/home/lupin/code/pantheon"),
    )
    parser.add_argument("--status-root", type=Path, default=None)
    parser.add_argument("--allow-mutations", action="store_true")
    parser.add_argument("--poll-attempts", type=int, default=30)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument("--provider-timeout-seconds", type=float, default=240.0)
    parser.add_argument("--hook-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--bff-restart-command", default="")
    parser.add_argument("--adapter-restart-command", default="")
    parser.add_argument("--supervisor-stop-command", default="")
    parser.add_argument("--supervisor-restart-command", default="")
    parser.add_argument("--shared-readback-command", default="")
    parser.add_argument("--sentinel-readback-command", default="")
    parser.add_argument("--bridge-readback-command", default="")
    return parser


def config_from_args(args: argparse.Namespace) -> VerifierConfig:
    run_id = str(args.run_id or "").strip() or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid.uuid4().hex[:8]}"
    )
    expected_branch = str(args.expected_branch or "").strip()
    if args.mode == "run" and not expected_branch and args.task_id:
        expected_branch = f"task/{args.task_id}"
    return VerifierConfig(
        mode=args.mode,
        bff_base_url=args.bff_base_url,
        frontend_deployment_url=args.frontend_deployment_url,
        output_dir=args.output_dir,
        run_id=run_id,
        expected_bff_sha=args.expected_bff_sha,
        expected_frontend_sha=args.expected_frontend_sha,
        task_id=args.task_id,
        repo_key=args.repo_key,
        declared_scope=tuple(args.scope),
        expected_branch=expected_branch,
        remote=args.remote,
        merge_target=args.merge_target,
        shared_checkout_path=args.shared_checkout_path,
        status_root=args.status_root,
        allow_mutations=args.allow_mutations,
        poll_attempts=max(0, args.poll_attempts),
        poll_interval_seconds=max(0.0, args.poll_interval_seconds),
        provider_timeout_seconds=max(1.0, args.provider_timeout_seconds),
        hook_timeout_seconds=max(1.0, args.hook_timeout_seconds),
        bff_restart_command=_parse_command(args.bff_restart_command),
        adapter_restart_command=_parse_command(args.adapter_restart_command),
        supervisor_stop_command=_parse_command(args.supervisor_stop_command),
        supervisor_restart_command=_parse_command(args.supervisor_restart_command),
        shared_readback_command=_parse_command(args.shared_readback_command),
        sentinel_readback_command=_parse_command(args.sentinel_readback_command),
        bridge_readback_command=_parse_command(args.bridge_readback_command),
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        config = config_from_args(args)
        result = ManagementAiRepairVerifier(config).execute()
    except VerificationError as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": exc.to_dict()},
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
