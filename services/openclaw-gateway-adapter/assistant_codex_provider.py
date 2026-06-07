"""Codex CLI provider for the OpenClaw gateway adapter.

The provider is intentionally small: it builds the exact non-interactive
``codex exec`` command, enforces read-only mode by default, and leaves prompt
and transcript redaction to ``AssistantProviderRuntime``.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assistant_credential_mounts import AssistantCredentialMounts, DEFAULT_CODEX_CONTAINER_HOME
from assistant_repair_workflow import AssistantRepairWorkflow, AssistantRepairWorkflowError

try:
    from assistant.redaction import RedactionError, redact_assistant_payload
except ModuleNotFoundError:  # pragma: no cover - exercised in service import contexts
    import sys

    _BFF_DIR = Path(__file__).resolve().parents[1] / "control-plane" / "bff"
    if str(_BFF_DIR) not in sys.path:
        sys.path.insert(0, str(_BFF_DIR))
    from assistant.redaction import RedactionError, redact_assistant_payload


CODEX_PROVIDER = "codex"
CODEX_PROVIDER_ID = "codex_cli"
PROVIDER_RUNTIME = "openclaw_gateway_cli_mount"
DEFAULT_CODEX_BIN = "codex"
DEFAULT_CODEX_WORKSPACE = "/srv/pantheon-assistant/workspaces/read-only"
DEFAULT_REPAIR_WORKTREE_ROOT = "/srv/pantheon-assistant/worktrees"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_AUDIT_PATH = "/tmp/openclaw-gateway-adapter/assistant_provider_audit.jsonl"
MAX_CODEX_IMAGES = 8
MAX_CODEX_IMAGE_TOTAL_BYTES = 16 * 1024 * 1024
_CODEX_IMAGE_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_AUTH_FAILURE_RE = re.compile(
    r"(not\s+logged\s+in|login\s+required|sign\s+in|authentication|unauthorized|oauth|expired|token)",
    re.IGNORECASE,
)
_SANDBOX_NAMESPACE_FAILURE_RE = re.compile(
    r"(bwrap:\s*No permissions to create a new namespace|"
    r"fs sandbox helper failed|"
    r"kernel\.unprivileged_userns_clone)",
    re.IGNORECASE,
)


RunFunc = Callable[..., subprocess.CompletedProcess[str]]
WhichFunc = Callable[[str], str | None]
ClockFunc = Callable[[], datetime]


class CodexProviderError(RuntimeError):
    """Raised when the Codex CLI provider cannot complete safely."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 500,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.details = dict(details or {})

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "provider_error",
            "provider": CODEX_PROVIDER_ID,
            "runtime": PROVIDER_RUNTIME,
            "error_code": self.code,
            "message": str(self),
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class _CommandContext:
    mode: str
    workspace: str
    sandbox: str
    workspace_class: str
    repair_workflow: Mapping[str, Any] | None = None


class AssistantProviderAuditLog:
    """Append-only redacted audit log for provider invocations."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        redaction_enabled: bool = True,
        clock: ClockFunc | None = None,
    ) -> None:
        self.path = Path(path or os.getenv("PANTHEON_ASSISTANT_PROVIDER_AUDIT_PATH", DEFAULT_AUDIT_PATH))
        self.redaction_enabled = redaction_enabled
        self._clock = clock or _utc_now

    def record(self, event: Mapping[str, Any]) -> None:
        payload = {
            "ts": self._clock().isoformat().replace("+00:00", "Z"),
            **dict(event),
        }
        safe_payload = self._redact_for_audit(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe_payload, sort_keys=True) + "\n")

    def _redact_for_audit(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            result = redact_assistant_payload(
                payload,
                mode=str(payload.get("mode") or "user"),
                stage="provider_audit",
                enabled=self.redaction_enabled,
                allow_kernel_override=False,
            )
            if isinstance(result.value, Mapping):
                return result.value
            return {
                "event_type": "assistant.redaction.failed",
                "provider": CODEX_PROVIDER_ID,
                "runtime": PROVIDER_RUNTIME,
                "redaction_failed": True,
                "payload": "[REDACTION_FAILED_PAYLOAD_SUPPRESSED]",
            }
        except (RedactionError, Exception) as exc:  # noqa: BLE001 - audit must not leak raw payload
            return {
                "ts": payload.get("ts"),
                "event_type": "assistant.redaction.failed",
                "provider": CODEX_PROVIDER_ID,
                "runtime": PROVIDER_RUNTIME,
                "redaction_failed": True,
                "failure_reason": type(exc).__name__,
                "payload": "[REDACTION_FAILED_PAYLOAD_SUPPRESSED]",
            }


class AssistantCodexProvider:
    """Runs ``codex exec`` for the gateway adapter."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        mounts: AssistantCredentialMounts | None = None,
        run_func: RunFunc | None = None,
        which_func: WhichFunc | None = None,
        audit_log: AssistantProviderAuditLog | None = None,
        repair_workflow: AssistantRepairWorkflow | None = None,
        clock: ClockFunc | None = None,
    ) -> None:
        self._environ = dict(environ if environ is not None else os.environ)
        self._mounts = mounts or AssistantCredentialMounts(self._environ)
        self._run = run_func or subprocess.run
        self._which = which_func or shutil.which
        self._audit = audit_log or AssistantProviderAuditLog(
            redaction_enabled=_truthy(self._environ.get("PANTHEON_ASSISTANT_REDACTION_ENABLED", "true")),
        )
        self._repair_workflow = repair_workflow or AssistantRepairWorkflow(self._environ)
        self._clock = clock or _utc_now

    def readiness(self, *, auth_probe: bool = False) -> dict[str, Any]:
        checked_at = self._clock().isoformat().replace("+00:00", "Z")
        binary = self._resolve_binary()
        mount_validation = self._mounts.validate_mounts().get(CODEX_PROVIDER)
        repair_workspace = _repair_workspace_metadata(
            self._environ.get("PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT", DEFAULT_REPAIR_WORKTREE_ROOT)
        )
        base = {
            "provider": CODEX_PROVIDER_ID,
            "provider_name": CODEX_PROVIDER,
            "runtime": PROVIDER_RUNTIME,
            "checked_at": checked_at,
            "binary_path": binary or self._configured_binary(),
            "version": "unknown",
            "auth": "not_checked",
            "auth_status": "not_checked",
            "credential_mount": _mount_metadata(mount_validation),
            "mount_mode": getattr(mount_validation, "mount_mode", "unknown"),
            "repair_workspace": repair_workspace,
            "repairWorkspace": repair_workspace,
            "capabilities": {
                "read": True,
                "repairWrite": bool(repair_workspace.get("ready")),
                "repair_write": bool(repair_workspace.get("ready")),
            },
            "last_refresh_check_time": None,
            "ready": False,
            "status": "degraded",
        }
        if not binary:
            return {**base, "degraded_reason": "codex_binary_not_found"}

        version = self._probe_version(binary)
        base["version"] = version.get("version", "unknown")
        if not version.get("ready"):
            return {**base, "degraded_reason": version.get("reason", "codex_version_probe_failed")}

        if not mount_validation:
            return {**base, "degraded_reason": "codex_mount_not_configured"}
        if not mount_validation.ready:
            return {
                **base,
                "degraded_reason": f"codex_mount_{mount_validation.status}",
                "auth": "unavailable",
                "auth_status": "mount_unavailable",
            }

        if auth_probe:
            auth = self._probe_auth(binary)
            base["last_refresh_check_time"] = checked_at
            base["auth"] = auth["auth"]
            base["auth_status"] = auth["auth_status"]
            if not auth["ready"]:
                return {**base, "degraded_reason": auth["reason"]}
        else:
            base["auth"] = "account_session"
            base["auth_status"] = "not_checked"

        return {
            **base,
            "ready": True,
            "status": "ready",
            "degraded_reason": None,
        }

    def invoke(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "user")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise CodexProviderError(
                "CODEX_PROMPT_REQUIRED",
                "Codex provider requires a non-empty prompt.",
                status_code=400,
            )
        metadata = _ensure_mapping(payload.get("metadata"), field="metadata")
        operator_id = str(metadata.get("operator_id") or metadata.get("operatorId") or "").strip()
        if not operator_id:
            raise CodexProviderError(
                "OPERATOR_REQUIRED",
                "Codex provider requires operator_id metadata from X-Operator-Id before invocation.",
                status_code=401,
                retryable=False,
            )
        context = self._command_context(mode, metadata)
        self._ensure_workspace_available(context)
        binary = self._require_binary()
        image_paths, image_tmpdir, image_bytes = self._materialize_request_images(payload)
        cmd = self._build_command(binary=binary, context=context, image_paths=image_paths)
        env = self._build_env()
        timeout = self._timeout_seconds()
        started = time.monotonic()
        audit_context = _audit_context(metadata)

        self._audit.record(
            {
                "event_type": "assistant.provider.started",
                "provider": CODEX_PROVIDER_ID,
                "runtime": PROVIDER_RUNTIME,
                **audit_context,
                "mode": mode,
                "sandbox": context.sandbox,
                "workspace_class": context.workspace_class,
                **({"repair_workflow": context.repair_workflow} if context.repair_workflow else {}),
                "prompt_bytes": len(prompt.encode("utf-8")),
                "image_count": len(image_paths),
                "image_bytes": image_bytes,
                "timeout_seconds": timeout,
            }
        )
        try:
            completed = self._run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=context.workspace,
                check=False,
                input=prompt,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = _duration_ms(started)
            self._audit.record(
                {
                    "event_type": "assistant.provider.timeout",
                    "provider": CODEX_PROVIDER_ID,
                    "runtime": PROVIDER_RUNTIME,
                    **audit_context,
                    "mode": mode,
                    "sandbox": context.sandbox,
                    "workspace_class": context.workspace_class,
                    **({"repair_workflow": context.repair_workflow} if context.repair_workflow else {}),
                    "duration_ms": duration_ms,
                    "timeout_seconds": timeout,
                    "partial_stdout": _coerce_output(exc.output),
                    "partial_stderr": _coerce_output(exc.stderr),
                }
            )
            raise CodexProviderError(
                "CODEX_TIMEOUT",
                f"Codex provider timed out after {timeout}s.",
                status_code=504,
                retryable=True,
                details={"duration_ms": duration_ms, "timeout_seconds": timeout},
            ) from exc
        except FileNotFoundError as exc:
            raise CodexProviderError(
                "CODEX_BINARY_NOT_FOUND",
                "Codex binary is not available inside the gateway container.",
                status_code=503,
                retryable=False,
            ) from exc
        except OSError as exc:
            duration_ms = _duration_ms(started)
            self._audit.record(
                {
                    "event_type": "assistant.provider.start_failed",
                    "provider": CODEX_PROVIDER_ID,
                    "runtime": PROVIDER_RUNTIME,
                    **audit_context,
                    "mode": mode,
                    "sandbox": context.sandbox,
                    "workspace_class": context.workspace_class,
                    **({"repair_workflow": context.repair_workflow} if context.repair_workflow else {}),
                    "duration_ms": duration_ms,
                    "error_code": "CODEX_PROCESS_START_FAILED",
                    "error_type": type(exc).__name__,
                    "errno": getattr(exc, "errno", None),
                }
            )
            raise CodexProviderError(
                "CODEX_PROCESS_START_FAILED",
                "Codex provider process could not be started.",
                status_code=503,
                retryable=True,
                details={
                    "duration_ms": duration_ms,
                    "error_type": type(exc).__name__,
                    "errno": getattr(exc, "errno", None),
                },
            ) from exc
        finally:
            if image_tmpdir is not None:
                shutil.rmtree(image_tmpdir, ignore_errors=True)

        duration_ms = _duration_ms(started)
        if completed.returncode != 0:
            code, status_code, retryable = _classify_failure(completed.stdout, completed.stderr)
            self._audit.record(
                {
                    "event_type": "assistant.provider.failed",
                    "provider": CODEX_PROVIDER_ID,
                    "runtime": PROVIDER_RUNTIME,
                    **audit_context,
                    "mode": mode,
                    "sandbox": context.sandbox,
                    "workspace_class": context.workspace_class,
                    **({"repair_workflow": context.repair_workflow} if context.repair_workflow else {}),
                    "duration_ms": duration_ms,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "error_code": code,
                }
            )
            raise CodexProviderError(
                code,
                _failure_message(code),
                status_code=status_code,
                retryable=retryable,
                details={"returncode": completed.returncode, "duration_ms": duration_ms},
            )

        sandbox_error = _sandbox_namespace_failure(completed.stdout, completed.stderr)
        if sandbox_error:
            code = "CODEX_SANDBOX_UNAVAILABLE"
            self._audit.record(
                {
                    "event_type": "assistant.provider.failed",
                    "provider": CODEX_PROVIDER_ID,
                    "runtime": PROVIDER_RUNTIME,
                    **audit_context,
                    "mode": mode,
                    "sandbox": context.sandbox,
                    "workspace_class": context.workspace_class,
                    **({"repair_workflow": context.repair_workflow} if context.repair_workflow else {}),
                    "duration_ms": duration_ms,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "error_code": code,
                }
            )
            raise CodexProviderError(
                code,
                _failure_message(code),
                status_code=503,
                retryable=False,
                details={"returncode": completed.returncode, "duration_ms": duration_ms},
            )

        self._audit.record(
            {
                "event_type": "assistant.provider.completed",
                "provider": CODEX_PROVIDER_ID,
                "runtime": PROVIDER_RUNTIME,
                **audit_context,
                "mode": mode,
                "sandbox": context.sandbox,
                "workspace_class": context.workspace_class,
                **({"repair_workflow": context.repair_workflow} if context.repair_workflow else {}),
                "duration_ms": duration_ms,
                "returncode": completed.returncode,
                "output_summary": _codex_output_summary(completed.stdout),
            }
        )
        return {
            "provider": CODEX_PROVIDER_ID,
            "runtime": PROVIDER_RUNTIME,
            "status": "completed",
            "mode": mode,
            "sandbox": context.sandbox,
            "workspace_class": context.workspace_class,
            **({"repair_workflow": context.repair_workflow} if context.repair_workflow else {}),
            "returncode": completed.returncode,
            "duration_ms": duration_ms,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "json_events": _parse_json_lines(completed.stdout),
        }

    def _materialize_request_images(
        self, payload: Mapping[str, Any]
    ) -> tuple[list[str], str | None, int]:
        """Decode forwarded image attachments to temp files for ``codex exec -i``.

        Images arrive as ``data:<mime>;base64,<...>`` URLs inside the request
        ``attachments`` (or ``messages[].content``) the BFF forwards. The codex
        CLI reads ``-i`` files itself, so we stage decoded bytes in a private
        out-of-tree temp dir (never the read-only workspace or a worker
        worktree). Returns (paths, tmpdir_or_None, total_bytes). Any decode/IO
        problem degrades to text-only rather than failing the ask.
        """
        parts = self._collect_image_parts(payload)
        if not parts:
            return [], None, 0
        try:
            tmpdir = tempfile.mkdtemp(prefix="codex-img-")
            os.chmod(tmpdir, 0o700)
        except OSError:
            return [], None, 0
        paths: list[str] = []
        total = 0
        try:
            for index, part in enumerate(parts):
                if len(paths) >= MAX_CODEX_IMAGES:
                    break
                image_url = part.get("image_url")
                url = str(image_url.get("url") or "") if isinstance(image_url, Mapping) else ""
                if not url.startswith("data:") or "," not in url:
                    continue
                header, _, b64 = url.partition(",")
                mime = header[5:].split(";")[0].strip().lower()
                try:
                    data = base64.b64decode(b64, validate=False)
                except (ValueError, TypeError):
                    continue
                if not data:
                    continue
                if total + len(data) > MAX_CODEX_IMAGE_TOTAL_BYTES:
                    break
                total += len(data)
                ext = _CODEX_IMAGE_MIME_EXT.get(mime) or self._image_ext_fallback(part.get("filename"))
                path = os.path.join(tmpdir, f"image-{index}{ext}")
                with open(path, "wb") as handle:
                    handle.write(data)
                paths.append(path)
        except OSError:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return [], None, 0
        if not paths:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return [], None, 0
        return paths, tmpdir, total

    @staticmethod
    def _collect_image_parts(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        parts: list[Mapping[str, Any]] = []
        attachments = payload.get("attachments")
        if isinstance(attachments, list):
            for item in attachments:
                if isinstance(item, Mapping) and item.get("type") == "image_url":
                    parts.append(item)
        if parts:
            return parts
        messages = payload.get("messages")
        if isinstance(messages, list):
            for message in messages:
                content = message.get("content") if isinstance(message, Mapping) else None
                if isinstance(content, list):
                    for chunk in content:
                        if isinstance(chunk, Mapping) and chunk.get("type") == "image_url":
                            parts.append(chunk)
        return parts

    @staticmethod
    def _image_ext_fallback(filename: Any) -> str:
        if isinstance(filename, str) and "." in filename:
            ext = "." + filename.rsplit(".", 1)[1].strip().lower()
            if 2 <= len(ext) <= 6 and ext[1:].isalnum():
                return ext
        return ".img"

    def _build_command(
        self,
        *,
        binary: str,
        context: _CommandContext,
        image_paths: list[str] | tuple[str, ...] = (),
    ) -> list[str]:
        cmd = [
            binary,
            "exec",
            "-C",
            context.workspace,
            "--skip-git-repo-check",
            "-s",
            context.sandbox,
            "-c",
            'ask_for_approval="never"',
            "--json",
        ]
        for path in image_paths:
            cmd.extend(["-i", path])
        # Trailing "-" tells codex exec to read the prompt from stdin.
        cmd.append("-")
        return cmd

    def _command_context(self, mode: str, metadata: Mapping[str, Any]) -> _CommandContext:
        if mode == "kernel_repair":
            return self._repair_context(metadata)
        return _CommandContext(
            mode=mode,
            workspace=_norm_path(self._environ.get("PANTHEON_ASSISTANT_CODEX_WORKSPACE", DEFAULT_CODEX_WORKSPACE)),
            sandbox="read-only",
            workspace_class="read_only",
        )

    def _repair_context(self, metadata: Mapping[str, Any]) -> _CommandContext:
        task_id = str(metadata.get("task_id") or metadata.get("taskId") or "").strip()
        worktree = str(metadata.get("task_worktree") or metadata.get("taskWorktree") or "").strip()
        if not task_id or not worktree:
            raise CodexProviderError(
                "CODEX_REPAIR_METADATA_REQUIRED",
                "kernel_repair mode requires task_id and task_worktree metadata.",
                status_code=400,
                retryable=False,
            )
        root = Path(_norm_path(self._environ.get("PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT", DEFAULT_REPAIR_WORKTREE_ROOT)))
        worktree_path = Path(_norm_path(worktree))
        try:
            worktree_path.relative_to(root)
        except ValueError as exc:
            raise CodexProviderError(
                "CODEX_REPAIR_WORKTREE_OUTSIDE_ROOT",
                "kernel_repair task_worktree must be inside PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT.",
                status_code=400,
                retryable=False,
            ) from exc
        try:
            workflow = self._repair_workflow.validate(metadata, require_clean=True)
        except AssistantRepairWorkflowError as exc:
            raise CodexProviderError(
                exc.code,
                str(exc),
                status_code=exc.status_code,
                retryable=False,
                details=exc.details,
            ) from exc
        return _CommandContext(
            mode="kernel_repair",
            workspace=worktree_path.as_posix(),
            sandbox="workspace-write",
            workspace_class="task_worktree",
            repair_workflow=workflow.to_dict(),
        )

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self._environ)
        env["CODEX_HOME"] = self._environ.get("PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME", DEFAULT_CODEX_CONTAINER_HOME)
        return env

    def _require_binary(self) -> str:
        binary = self._resolve_binary()
        if not binary:
            raise CodexProviderError(
                "CODEX_BINARY_NOT_FOUND",
                "Codex binary is not available inside the gateway container.",
                status_code=503,
                retryable=False,
            )
        return binary

    def _resolve_binary(self) -> str | None:
        configured = self._configured_binary()
        if os.path.isabs(configured):
            return configured if Path(configured).exists() else self._which(Path(configured).name)
        return self._which(configured)

    def _configured_binary(self) -> str:
        return self._environ.get("PANTHEON_ASSISTANT_CODEX_BIN", DEFAULT_CODEX_BIN).strip() or DEFAULT_CODEX_BIN

    def _probe_version(self, binary: str) -> dict[str, Any]:
        try:
            completed = self._run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=min(10, self._timeout_seconds()),
                env=self._build_env(),
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ready": False, "reason": f"codex_version_probe_failed:{type(exc).__name__}"}
        version = (completed.stdout or completed.stderr or "").strip() or "unknown"
        if completed.returncode != 0:
            return {"ready": False, "version": version, "reason": "codex_version_probe_failed"}
        return {"ready": True, "version": version}

    def _probe_auth(self, binary: str) -> dict[str, Any]:
        context = _CommandContext(
            mode="kernel_observe",
            workspace=_norm_path(self._environ.get("PANTHEON_ASSISTANT_CODEX_WORKSPACE", DEFAULT_CODEX_WORKSPACE)),
            sandbox="read-only",
            workspace_class="read_only",
        )
        if not Path(context.workspace).is_dir():
            return {
                "ready": False,
                "auth": "unknown",
                "auth_status": "workspace_unavailable",
                "reason": "codex_workspace_unavailable",
            }
        try:
            completed = self._run(
                self._build_command(binary=binary, context=context),
                capture_output=True,
                text=True,
                timeout=min(30, self._timeout_seconds()),
                env=self._build_env(),
                cwd=context.workspace,
                check=False,
                input="Reply with exactly: ok",
            )
        except subprocess.TimeoutExpired:
            return {"ready": False, "auth": "unknown", "auth_status": "timeout", "reason": "codex_auth_probe_timeout"}
        except Exception as exc:  # noqa: BLE001
            return {
                "ready": False,
                "auth": "unknown",
                "auth_status": "failed",
                "reason": f"codex_auth_probe_failed:{type(exc).__name__}",
            }
        if completed.returncode == 0:
            return {"ready": True, "auth": "account_session", "auth_status": "ready", "reason": None}
        code, _, _ = _classify_failure(completed.stdout, completed.stderr)
        reason = "codex_auth_unavailable" if code == "CODEX_AUTH_UNAVAILABLE" else "codex_auth_probe_failed"
        return {"ready": False, "auth": "unavailable", "auth_status": "failed", "reason": reason}

    def _timeout_seconds(self) -> int:
        raw = self._environ.get("PANTHEON_ASSISTANT_COMMAND_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        try:
            parsed = int(raw)
        except ValueError:
            return DEFAULT_TIMEOUT_SECONDS
        return max(1, parsed)

    def _ensure_workspace_available(self, context: _CommandContext) -> None:
        if not Path(context.workspace).is_dir():
            raise CodexProviderError(
                "CODEX_WORKSPACE_UNAVAILABLE",
                "Codex provider workspace is not available inside the gateway container.",
                status_code=503,
                retryable=False,
                details={"workspace_class": context.workspace_class},
            )


def _classify_failure(stdout: str, stderr: str) -> tuple[str, int, bool]:
    combined = f"{stdout}\n{stderr}"
    if _SANDBOX_NAMESPACE_FAILURE_RE.search(combined):
        return "CODEX_SANDBOX_UNAVAILABLE", 503, False
    if _AUTH_FAILURE_RE.search(combined):
        return "CODEX_AUTH_UNAVAILABLE", 503, False
    return "CODEX_EXEC_FAILED", 502, True


def _failure_message(code: str) -> str:
    if code == "CODEX_AUTH_UNAVAILABLE":
        return "Codex service-user account session is unavailable or expired."
    if code == "CODEX_SANDBOX_UNAVAILABLE":
        return "Codex workspace-write sandbox is unavailable in the OpenClaw adapter container."
    return "Codex provider invocation failed."


def _sandbox_namespace_failure(stdout: str, stderr: str) -> bool:
    return bool(_SANDBOX_NAMESPACE_FAILURE_RE.search(f"{stdout}\n{stderr}"))


def _repair_workspace_metadata(root_value: str) -> dict[str, Any]:
    root = Path(_norm_path(root_value or DEFAULT_REPAIR_WORKTREE_ROOT))
    exists = _path_exists(root)
    is_dir = _path_is_dir(root)
    base: dict[str, Any] = {
        "root": root.as_posix(),
        "exists": exists,
        "isDir": is_dir,
        "is_dir": is_dir,
        "writable": os.access(root, os.W_OK) if exists else False,
        "ready": False,
        "status": "missing",
        "worktreeCount": 0,
        "worktree_count": 0,
        "recentWorktrees": [],
        "recent_worktrees": [],
    }
    if not exists:
        return base
    if not is_dir:
        return {**base, "status": "not_directory"}
    if not os.access(root, os.W_OK):
        return {**base, "status": "not_writable"}

    worktrees = _recent_worktree_dirs(root)
    recent = [
        {
            "name": item.name,
            "path": item.as_posix(),
            "lastModifiedAt": _mtime_z(item),
            "last_modified_at": _mtime_z(item),
            "gitRepo": (item / ".git").exists(),
            "git_repo": (item / ".git").exists(),
        }
        for item in worktrees[:8]
    ]
    return {
        **base,
        "ready": True,
        "status": "ready",
        "worktreeCount": len(worktrees),
        "worktree_count": len(worktrees),
        "recentWorktrees": recent,
        "recent_worktrees": recent,
    }


def _recent_worktree_dirs(root: Path) -> list[Path]:
    try:
        dirs = [item for item in root.iterdir() if _path_is_dir(item)]
    except OSError:
        return []
    return sorted(dirs, key=lambda item: _safe_mtime(item), reverse=True)


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _path_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _mtime_z(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        return None


def _mount_metadata(validation: Any) -> dict[str, Any]:
    if validation is None:
        return {"ready": False, "status": "missing", "container_target": "codex_home"}
    return {
        "ready": bool(validation.ready),
        "status": validation.status,
        "host_source": validation.host_source,
        "container_target": validation.container_target,
        "mount_mode": validation.mount_mode,
        "owner_check": validation.owner_check,
    }


def _parse_json_lines(stdout: str) -> list[Any]:
    events: list[Any] = []
    for line in stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            events.append(json.loads(text))
        except json.JSONDecodeError:
            return []
    return events


def _ensure_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    raise CodexProviderError(
        "CODEX_REQUEST_SCHEMA_ERROR",
        f"Codex provider expected {field} to be an object.",
        status_code=400,
        retryable=False,
    )


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _norm_path(value: str) -> str:
    return Path(value).expanduser().resolve().as_posix()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _coerce_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _audit_context(metadata: Mapping[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key in (
        "trace_id",
        "provider_run_id",
        "session_id",
        "message_id",
        "operator_id",
        "tenant_id",
        "audit_id",
        "route",
    ):
        value = metadata.get(key)
        if value not in (None, ""):
            context[key] = str(value)
    return context


def _codex_output_summary(stdout: str) -> dict[str, Any]:
    events = _parse_json_lines(stdout)
    event_types: list[str] = []
    usage: Mapping[str, Any] | None = None
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("type") or "").strip()
        if event_type:
            event_types.append(event_type)
        if event_type == "turn.completed" and isinstance(event.get("usage"), Mapping):
            usage = event.get("usage")
    return {
        "json_event_count": len(events),
        "json_event_types": event_types,
        "usage": dict(usage or {}),
    }
