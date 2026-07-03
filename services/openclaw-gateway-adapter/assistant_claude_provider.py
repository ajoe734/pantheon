"""Claude Code CLI provider for the OpenClaw gateway adapter.

Invokes ``claude -p`` inside the gateway container using the dedicated
service-user CLAUDE_CONFIG_DIR mount.  Output is normalized from stream-json
or plain text into a structured provider result.  Missing binary, missing auth
mount, and timeout all produce a degraded result rather than an exception so
the caller can apply a deterministic fallback.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from assistant_credential_mounts import (
    AssistantCredentialMounts,
    DEFAULT_CLAUDE_CONTAINER_CONFIG_DIR,
)
from assistant_provider_usage import provider_usage_snapshot

_DEFAULT_TIMEOUT = int(os.getenv("ASSISTANT_CLAUDE_PROVIDER_TIMEOUT", "60"))
_DEFAULT_REAUTH_CAPTURE_TIMEOUT_SECONDS = 20
_DEFAULT_REAUTH_MAX_WAIT_SECONDS = 900
_DEFAULT_REAUTH_POLL_INTERVAL_SECONDS = 5
_BINARY_NAME = "claude"
_BINARY_ENV = "PANTHEON_ASSISTANT_CLAUDE_BIN"
_PROVIDER_NAME = "claude"
_RUNTIME = "openclaw_gateway_cli_mount"

PopenFunc = Callable[..., subprocess.Popen[str]]
ClockFunc = Callable[[], datetime]


class ClaudeProviderError(RuntimeError):
    """Raised when Claude CLI provider auth cannot complete safely."""

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
            "provider": _PROVIDER_NAME,
            "runtime": _RUNTIME,
            "error_code": self.code,
            "message": str(self),
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class ClaudeProviderResult:
    status: str
    text: str
    raw_events: List[Dict[str, Any]] = field(default_factory=list)
    degraded_reason: Optional[str] = None
    exit_code: Optional[int] = None
    config_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        config_target = "claude_config" if self.config_dir else ""
        result: Dict[str, Any] = {
            "provider": _PROVIDER_NAME,
            "status": self.status,
            "text": self.text,
            "config_dir": config_target,
        }
        if self.degraded_reason is not None:
            result["degraded_reason"] = self.degraded_reason
        if self.exit_code is not None:
            result["exit_code"] = self.exit_code
        if self.raw_events:
            result["raw_events"] = self.raw_events
        return result


class AssistantClaudeProvider:
    """Runs Claude Code CLI for the gateway adapter."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        mounts: AssistantCredentialMounts | None = None,
        popen_func: PopenFunc | None = None,
        clock: ClockFunc | None = None,
    ) -> None:
        self._environ = dict(environ if environ is not None else os.environ)
        self._mounts = mounts or AssistantCredentialMounts(self._environ)
        self._popen = popen_func or subprocess.Popen
        self._clock = clock or _utc_now
        self._reauth_lock = threading.Lock()
        self._reauth_sessions: dict[str, dict[str, Any]] = {}

    def readiness(self, *, auth_probe: bool = False) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        binary = _resolve_binary(self._environ)
        mount_validation = self._mounts.validate_mounts().get(_PROVIDER_NAME)
        usage = provider_usage_snapshot(
            _PROVIDER_NAME,
            _PROVIDER_NAME,
            environ=self._environ,
        )

        base = {
            "provider": _PROVIDER_NAME,
            "provider_name": _PROVIDER_NAME,
            "runtime": _RUNTIME,
            "checked_at": checked_at,
            "binary_path": binary or self._configured_binary(),
            "version": "unknown",
            "auth": "not_checked",
            "auth_status": "not_checked",
            "credential_mount": _mount_metadata(mount_validation),
            "mount_mode": getattr(mount_validation, "mount_mode", "unknown"),
            "usage": usage,
            "quota": usage,
            "last_refresh_check_time": None,
            "ready": False,
            "status": "degraded",
        }

        if not binary:
            return {**base, "degraded_reason": "claude_binary_not_found"}

        try:
            version = subprocess.check_output([binary, "--version"], stderr=subprocess.STDOUT).decode().strip()
            base["version"] = version
        except Exception:
            return {**base, "degraded_reason": "claude_version_probe_failed"}

        if not mount_validation:
            return {**base, "degraded_reason": "claude_mount_not_configured"}
        if not mount_validation.ready:
            return {
                **base,
                "degraded_reason": f"claude_mount_{mount_validation.status}",
                "auth": "unavailable",
                "auth_status": "mount_unavailable",
            }

        if auth_probe:
            # Claude has no stable no-op auth probe, so use a tiny prompt.
            result = self.invoke("Reply with: ok", timeout=30)
            base["last_refresh_check_time"] = checked_at
            if result.status == "ok":
                base["auth"] = "account_session"
                base["auth_status"] = "ready"
                base["ready"] = True
                base["status"] = "ready"
                base["degraded_reason"] = None
            else:
                base["auth"] = "unavailable"
                base["auth_status"] = "failed"
                base["degraded_reason"] = _auth_probe_degraded_reason(result.degraded_reason)
        else:
            base["auth"] = "account_session"
            base["auth_status"] = "not_checked"
            base["ready"] = True
            base["status"] = "ready"
            base["degraded_reason"] = None

        return base

    def invoke(
        self,
        prompt: str,
        *,
        mode: str = "user",
        context_pack: Optional[Dict[str, Any]] = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> ClaudeProviderResult:
        return invoke_claude(
            prompt,
            mode=mode,
            context_pack=context_pack,
            timeout=timeout,
            mounts=self._mounts,
        )

    def _configured_binary(self) -> str:
        return self._environ.get(_BINARY_ENV, _BINARY_NAME).strip() or _BINARY_NAME

    def start_device_reauth(
        self,
        *,
        operator_id: str,
        trace_id: str | None = None,
        reason: str | None = None,
        capture_timeout_seconds: int | None = None,
        poll_interval_seconds: int | None = None,
        max_wait_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Start ``claude auth login`` and return only browser-safe fields."""
        clean_operator = str(operator_id or "").strip()
        if not clean_operator:
            raise ClaudeProviderError(
                "OPERATOR_REQUIRED",
                "Claude provider reauth requires X-Operator-Id.",
                status_code=401,
                retryable=False,
            )
        binary = self._require_binary()
        self._ensure_reauth_mount_ready()
        capture_timeout = _positive_int(
            capture_timeout_seconds,
            _DEFAULT_REAUTH_CAPTURE_TIMEOUT_SECONDS,
            minimum=1,
            maximum=120,
        )
        poll_interval = _positive_int(
            poll_interval_seconds,
            _DEFAULT_REAUTH_POLL_INTERVAL_SECONDS,
            minimum=1,
            maximum=60,
        )
        max_wait = _positive_int(
            max_wait_seconds,
            _DEFAULT_REAUTH_MAX_WAIT_SECONDS,
            minimum=30,
            maximum=3600,
        )
        session_id = f"claude_reauth_{uuid.uuid4().hex}"
        now = self._clock().isoformat().replace("+00:00", "Z")
        session: dict[str, Any] = {
            "reauth_session_id": session_id,
            "provider": _PROVIDER_NAME,
            "provider_name": _PROVIDER_NAME,
            "runtime": _RUNTIME,
            "status": "capturing",
            "started_at": now,
            "updated_at": now,
            "verification_uri": None,
            "verification_uri_complete": None,
            "user_code": None,
            "poll_interval_seconds": poll_interval,
            "max_wait_seconds": max_wait,
            "credential_exchange": _reauth_credential_exchange_metadata(),
            "readiness": None,
        }
        if reason:
            session["reason"] = str(reason)
        with self._reauth_lock:
            self._reauth_sessions[session_id] = session

        try:
            process = self._popen(
                [binary, "auth", "login"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._build_env(),
                cwd="/tmp",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            self._mark_reauth_failed(session_id, "CLAUDE_BINARY_NOT_FOUND", "Claude binary is not available.")
            raise ClaudeProviderError(
                "CLAUDE_BINARY_NOT_FOUND",
                "Claude binary is not available inside the gateway container.",
                status_code=503,
                retryable=False,
            ) from exc
        except OSError as exc:
            self._mark_reauth_failed(session_id, "CLAUDE_REAUTH_START_FAILED", type(exc).__name__)
            raise ClaudeProviderError(
                "CLAUDE_REAUTH_START_FAILED",
                "Claude auth login process could not be started.",
                status_code=503,
                retryable=True,
                details={"error_type": type(exc).__name__, "errno": getattr(exc, "errno", None)},
            ) from exc

        self._update_reauth_session(session_id, _process=process)

        captured = threading.Event()
        for stream_name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            if stream is None:
                continue
            threading.Thread(
                target=self._read_reauth_stream,
                args=(session_id, stream_name, stream, captured),
                daemon=True,
            ).start()

        deadline = time.monotonic() + capture_timeout
        while time.monotonic() < deadline:
            if captured.is_set():
                break
            if process.poll() is not None:
                break
            time.sleep(0.1)

        public = self.reauth_status(session_id)
        if not public.get("verification_uri") and not public.get("verification_uri_complete"):
            returncode = process.poll()
            if returncode is None:
                _terminate_process(process)
                code = "CLAUDE_REAUTH_LOGIN_URL_TIMEOUT"
                message = "Claude auth login did not emit a login URL before timeout."
            else:
                code = "CLAUDE_REAUTH_LOGIN_URL_UNAVAILABLE"
                message = "Claude auth login exited before emitting a login URL."
            self._mark_reauth_failed(session_id, code, message)
            raise ClaudeProviderError(code, message, status_code=503, retryable=True)

        self._update_reauth_session(
            session_id,
            status="pending",
            updated_at=self._clock().isoformat().replace("+00:00", "Z"),
        )
        threading.Thread(
            target=self._monitor_reauth_session,
            args=(session_id, process, poll_interval, max_wait),
            daemon=True,
        ).start()
        return self.reauth_status(session_id)

    def submit_reauth_code(
        self,
        session_id: str,
        *,
        code: str,
        operator_id: str,
    ) -> dict[str, Any]:
        """Submit the browser-returned Claude authorization code to the live CLI."""
        clean_operator = str(operator_id or "").strip()
        if not clean_operator:
            raise ClaudeProviderError(
                "OPERATOR_REQUIRED",
                "Claude provider reauth code submission requires X-Operator-Id.",
                status_code=401,
                retryable=False,
            )
        clean_session_id = str(session_id or "").strip()
        clean_code = _normalize_submitted_auth_code(code)
        if not clean_code:
            raise ClaudeProviderError(
                "CLAUDE_REAUTH_CODE_REQUIRED",
                "Claude provider reauth requires an authorization code.",
                status_code=422,
                retryable=False,
            )

        with self._reauth_lock:
            session = self._reauth_sessions.get(clean_session_id)
            if not session:
                raise ClaudeProviderError(
                    "CLAUDE_REAUTH_SESSION_NOT_FOUND",
                    "Claude provider reauth session was not found.",
                    status_code=404,
                    retryable=False,
                )
            status = str(session.get("status") or "").strip().lower()
            process = session.get("_process")

        if status in {"completed", "failed", "timeout", "cancelled", "expired"}:
            raise ClaudeProviderError(
                "CLAUDE_REAUTH_SESSION_NOT_ACTIVE",
                "Claude provider reauth session is not accepting authorization codes.",
                status_code=409,
                retryable=status not in {"completed"},
                details={"session_status": status or "unknown"},
            )
        if process is None:
            raise ClaudeProviderError(
                "CLAUDE_REAUTH_PROCESS_UNAVAILABLE",
                "Claude auth login process is not available for this reauth session.",
                status_code=409,
                retryable=False,
            )
        if process.poll() is not None:
            self._mark_reauth_failed(
                clean_session_id,
                "CLAUDE_REAUTH_PROCESS_EXITED",
                "Claude auth login exited before the authorization code was submitted.",
            )
            raise ClaudeProviderError(
                "CLAUDE_REAUTH_PROCESS_EXITED",
                "Claude auth login exited before the authorization code was submitted.",
                status_code=409,
                retryable=True,
            )

        stdin = getattr(process, "stdin", None)
        if stdin is None:
            raise ClaudeProviderError(
                "CLAUDE_REAUTH_STDIN_UNAVAILABLE",
                "Claude auth login process is not accepting authorization code input.",
                status_code=503,
                retryable=True,
            )
        try:
            stdin.write(f"{clean_code}\n")
            stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._mark_reauth_failed(
                clean_session_id,
                "CLAUDE_REAUTH_CODE_WRITE_FAILED",
                "Claude auth login process rejected the authorization code input.",
            )
            raise ClaudeProviderError(
                "CLAUDE_REAUTH_CODE_WRITE_FAILED",
                "Claude auth login process rejected the authorization code input.",
                status_code=409,
                retryable=True,
                details={"error_type": type(exc).__name__},
            ) from exc

        submitted_at = self._clock().isoformat().replace("+00:00", "Z")
        self._update_reauth_session(
            clean_session_id,
            status="code_submitted",
            updated_at=submitted_at,
            code_submitted_at=submitted_at,
            message="Claude authorization code submitted; waiting for readiness probe.",
        )
        return self.reauth_status(clean_session_id)

    def reauth_status(self, session_id: str) -> dict[str, Any]:
        clean_session_id = str(session_id or "").strip()
        with self._reauth_lock:
            session = dict(self._reauth_sessions.get(clean_session_id) or {})
        if not session:
            raise ClaudeProviderError(
                "CLAUDE_REAUTH_SESSION_NOT_FOUND",
                "Claude provider reauth session was not found.",
                status_code=404,
                retryable=False,
            )
        return _reauth_public_payload(session)

    def _read_reauth_stream(
        self,
        session_id: str,
        _stream_name: str,
        stream: Any,
        captured: threading.Event,
    ) -> None:
        try:
            for raw_line in iter(stream.readline, ""):
                line = _coerce_output(raw_line).strip()
                if not line:
                    continue
                fields = _extract_auth_fields(line)
                if fields:
                    fields["updated_at"] = self._clock().isoformat().replace("+00:00", "Z")
                    self._update_reauth_session(session_id, **fields)
                with self._reauth_lock:
                    session = self._reauth_sessions.get(session_id) or {}
                    if session.get("verification_uri") or session.get("verification_uri_complete"):
                        captured.set()
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    def _monitor_reauth_session(
        self,
        session_id: str,
        process: Any,
        poll_interval_seconds: int,
        max_wait_seconds: int,
    ) -> None:
        deadline = time.monotonic() + max_wait_seconds
        while time.monotonic() < deadline:
            readiness = self.readiness(auth_probe=True)
            if readiness.get("ready") is True:
                completed_at = self._clock().isoformat().replace("+00:00", "Z")
                self._update_reauth_session(
                    session_id,
                    status="completed",
                    updated_at=completed_at,
                    completed_at=completed_at,
                    readiness=readiness,
                    error_code=None,
                    message=None,
                )
                if process.poll() is None:
                    _terminate_process(process)
                return

            returncode = process.poll()
            if returncode is not None:
                readiness = self._readiness_after_process_exit()
                if readiness.get("ready") is True:
                    completed_at = self._clock().isoformat().replace("+00:00", "Z")
                    self._update_reauth_session(
                        session_id,
                        status="completed",
                        updated_at=completed_at,
                        completed_at=completed_at,
                        readiness=readiness,
                        error_code=None,
                        message=None,
                        returncode=returncode,
                    )
                    return
                with self._reauth_lock:
                    session = dict(self._reauth_sessions.get(session_id) or {})
                if returncode == 0 and session.get("code_submitted_at"):
                    self._update_reauth_session(
                        session_id,
                        status="failed",
                        updated_at=self._clock().isoformat().replace("+00:00", "Z"),
                        readiness=readiness,
                        error_code="CLAUDE_REAUTH_READY_PROBE_DEGRADED",
                        warning_code=None,
                        message=(
                            "Claude auth login accepted the authorization code, "
                            "but readiness probe is still degraded."
                        ),
                        returncode=returncode,
                    )
                    return
                self._update_reauth_session(
                    session_id,
                    status="failed",
                    updated_at=self._clock().isoformat().replace("+00:00", "Z"),
                    readiness=readiness,
                    error_code="CLAUDE_REAUTH_NOT_READY",
                    message="Claude auth login exited but readiness auth probe is not ready.",
                    returncode=returncode,
                )
                return
            time.sleep(max(1, poll_interval_seconds))

        if process.poll() is None:
            _terminate_process(process)
        self._update_reauth_session(
            session_id,
            status="timeout",
            updated_at=self._clock().isoformat().replace("+00:00", "Z"),
            error_code="CLAUDE_REAUTH_TIMEOUT",
            message="Claude auth login did not complete before the reauth timeout.",
        )

    def _readiness_after_process_exit(self) -> dict[str, Any]:
        readiness = self.readiness(auth_probe=True)
        if readiness.get("ready") is True:
            return readiness
        for _ in range(3):
            time.sleep(1)
            readiness = self.readiness(auth_probe=True)
            if readiness.get("ready") is True:
                return readiness
        return readiness

    def _update_reauth_session(self, session_id: str, **fields: Any) -> None:
        with self._reauth_lock:
            session = self._reauth_sessions.get(session_id)
            if session is None:
                return
            session.update({key: value for key, value in fields.items() if value is not None})

    def _mark_reauth_failed(self, session_id: str, error_code: str, message: str) -> None:
        self._update_reauth_session(
            session_id,
            status="failed",
            updated_at=self._clock().isoformat().replace("+00:00", "Z"),
            error_code=error_code,
            message=message,
        )

    def _require_binary(self) -> str:
        binary = _resolve_binary(self._environ)
        if not binary:
            raise ClaudeProviderError(
                "CLAUDE_BINARY_NOT_FOUND",
                "Claude binary is not available inside the gateway container.",
                status_code=503,
                retryable=False,
            )
        return binary

    def _ensure_reauth_mount_ready(self) -> None:
        mount_validation = self._mounts.validate_mounts().get(_PROVIDER_NAME)
        if not mount_validation or not mount_validation.ready:
            status = getattr(mount_validation, "status", "missing")
            raise ClaudeProviderError(
                "CLAUDE_REAUTH_MOUNT_UNAVAILABLE",
                "Claude reauth requires a ready service-user credential mount.",
                status_code=503,
                retryable=False,
                details={"mount_status": status},
            )
        if getattr(mount_validation, "mount_mode", "") != "rw":
            raise ClaudeProviderError(
                "CLAUDE_REAUTH_MOUNT_READ_ONLY",
                "Claude reauth requires a writable service-user credential mount.",
                status_code=409,
                retryable=False,
                details={"mount_mode": getattr(mount_validation, "mount_mode", "unknown")},
            )

    def _build_env(self) -> dict[str, str]:
        return _claude_cli_env(_resolve_config_dir(self._mounts), self._environ)


def _mount_metadata(validation: Any) -> dict[str, Any]:
    if validation is None:
        return {"ready": False, "status": "missing", "container_target": "claude_config"}
    return {
        "ready": bool(validation.ready),
        "status": validation.status,
        "host_source": validation.host_source,
        "container_target": validation.container_target,
        "mount_mode": validation.mount_mode,
        "owner_check": validation.owner_check,
    }


def _resolve_config_dir(mounts: AssistantCredentialMounts) -> str:
    """Return the container-side CLAUDE_CONFIG_DIR from the mount contract."""
    for contract in mounts._contracts():
        if contract.provider == "claude":
            return contract.container_path
    return DEFAULT_CLAUDE_CONTAINER_CONFIG_DIR


def _claude_cli_env(config_dir: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build an environment where Claude auth and probes share one credential home."""
    env = {**os.environ, **dict(environ or {})}
    env["CLAUDE_CONFIG_DIR"] = config_dir
    claude_home = os.path.dirname(config_dir.rstrip(os.sep)) or env.get("HOME", "")
    if claude_home:
        env["HOME"] = claude_home
    return env


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _auth_probe_degraded_reason(reason: Optional[str]) -> str:
    if not reason:
        return "claude_auth_unavailable"
    if reason == "auth_failure":
        return "claude_auth_failure"
    if reason.startswith("auth_"):
        return f"claude_{reason}"
    return f"claude_auth_probe_{reason}"


def _resolve_binary(environ: Mapping[str, str] | None = None) -> Optional[str]:
    """Return the configured container-side Claude binary path, if usable."""
    env = environ if environ is not None else os.environ
    configured = env.get(_BINARY_ENV, "").strip()
    if configured:
        if os.path.isabs(configured):
            return configured if os.path.isfile(configured) and os.access(configured, os.X_OK) else None
        return shutil.which(configured)
    return shutil.which(_BINARY_NAME)


def _normalize_output(raw: str) -> tuple[str, List[Dict[str, Any]]]:
    """Parse stream-json or plain text output into (text, events).

    ``claude -p`` emits newline-delimited JSON events when ``--output-format
    stream-json`` is supplied.  Each event has a ``type`` field; the final
    ``result`` event carries the assistant text.  If the output is not valid
    JSON, it is treated as plain text.
    """
    events: List[Dict[str, Any]] = []
    text_parts: List[str] = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)
            event_type = event.get("type", "")
            if event_type == "result":
                result_val = event.get("result", "")
                if isinstance(result_val, str):
                    text_parts.append(result_val)
            elif event_type == "assistant":
                content = event.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                elif isinstance(content, str):
                    text_parts.append(content)
        except (json.JSONDecodeError, ValueError):
            text_parts.append(line)

    return "\n".join(text_parts).strip(), events


def _positive_int(value: int | None, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _extract_auth_fields(line: str) -> dict[str, Any]:
    fields = _extract_auth_json(line)
    fields.update(_extract_auth_text(line))
    return {key: value for key, value in fields.items() if value}


def _extract_auth_json(line: str) -> dict[str, Any]:
    text = line.strip()
    if not text.startswith("{"):
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    return {
        "verification_uri": _first_mapping_value(
            payload,
            "verification_uri",
            "verificationUri",
            "verification_url",
            "verificationUrl",
            "login_url",
            "loginUrl",
            "url",
        ),
        "verification_uri_complete": _first_mapping_value(
            payload,
            "verification_uri_complete",
            "verificationUriComplete",
            "verification_url_complete",
            "verificationUrlComplete",
            "login_url_complete",
            "loginUrlComplete",
        ),
        "user_code": _first_mapping_value(payload, "user_code", "userCode", "code"),
        "expires_in": _first_mapping_value(payload, "expires_in", "expiresIn"),
    }


def _extract_auth_text(line: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    url_match = re.search(r"https?://[^\s)>\]\"']+", line)
    code_source = line
    if url_match:
        url = url_match.group(0).rstrip(".,;")
        key = "verification_uri_complete" if "code=" in url or "token=" in url else "verification_uri"
        fields[key] = url
        code_source = f"{line[:url_match.start()]} {line[url_match.end():]}"
    code_match = re.search(
        r"(?:login\s*code|verification\s*code|user\s*code|code)\s*[:=]\s*([A-Z0-9][A-Z0-9-]{3,})",
        code_source,
        re.IGNORECASE,
    )
    if code_match is None:
        code_match = re.search(
            r"enter\s+(?:the\s+)?(?:code\s+)?([A-Z0-9][A-Z0-9-]{3,})",
            code_source,
            re.IGNORECASE,
        )
    if code_match:
        fields["user_code"] = code_match.group(1).strip()
    return fields


def _first_mapping_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _reauth_public_payload(session: Mapping[str, Any]) -> dict[str, Any]:
    credential_exchange = session.get("credential_exchange") or _reauth_credential_exchange_metadata()
    payload = {
        "status": session.get("status"),
        "reauth_session_id": session.get("reauth_session_id"),
        "reauthSessionId": session.get("reauth_session_id"),
        "provider": session.get("provider") or _PROVIDER_NAME,
        "provider_name": session.get("provider_name") or _PROVIDER_NAME,
        "providerName": session.get("provider_name") or _PROVIDER_NAME,
        "runtime": session.get("runtime") or _RUNTIME,
        "started_at": session.get("started_at"),
        "startedAt": session.get("started_at"),
        "updated_at": session.get("updated_at"),
        "updatedAt": session.get("updated_at"),
        "code_submitted_at": session.get("code_submitted_at"),
        "codeSubmittedAt": session.get("code_submitted_at"),
        "completed_at": session.get("completed_at"),
        "completedAt": session.get("completed_at"),
        "verification_uri": session.get("verification_uri"),
        "verificationUri": session.get("verification_uri"),
        "verification_uri_complete": session.get("verification_uri_complete"),
        "verificationUriComplete": session.get("verification_uri_complete"),
        "user_code": session.get("user_code"),
        "userCode": session.get("user_code"),
        "expires_in": session.get("expires_in"),
        "expiresIn": session.get("expires_in"),
        "poll_interval_seconds": session.get("poll_interval_seconds"),
        "pollIntervalSeconds": session.get("poll_interval_seconds"),
        "max_wait_seconds": session.get("max_wait_seconds"),
        "maxWaitSeconds": session.get("max_wait_seconds"),
        "credential_exchange": credential_exchange,
        "credentialExchange": credential_exchange,
        "readiness": session.get("readiness"),
        "error_code": session.get("error_code"),
        "errorCode": session.get("error_code"),
        "warning_code": session.get("warning_code"),
        "warningCode": session.get("warning_code"),
        "message": session.get("message"),
        "returncode": session.get("returncode"),
    }
    if session.get("reason"):
        payload["reason"] = session.get("reason")
    return {key: value for key, value in payload.items() if value is not None}


def _reauth_credential_exchange_metadata() -> dict[str, Any]:
    return {
        "idp_exchange": "operator_browser",
        "idpExchange": "operator_browser",
        "bff_handles_credentials": False,
        "bffHandlesCredentials": False,
        "frontend_handles_credentials": False,
        "frontendHandlesCredentials": False,
        "provider_cli_writes_mount": True,
        "providerCliWritesMount": True,
        "requires_authorization_code": True,
        "requiresAuthorizationCode": True,
        "code_submit_to_bff": True,
        "codeSubmitToBff": True,
        "returned_fields": ["verification_uri", "verification_uri_complete", "user_code"],
    }


def _normalize_submitted_auth_code(code: Any) -> str:
    raw = str(code or "").strip()
    if not raw:
        return ""
    lines = [line.strip() for line in raw.replace("\r", "\n").split("\n") if line.strip()]
    if len(lines) != 1:
        return ""
    clean = lines[0]
    return clean if len(clean) <= 4096 else ""


def _terminate_process(process: Any) -> None:
    terminate = getattr(process, "terminate", None)
    wait = getattr(process, "wait", None)
    kill = getattr(process, "kill", None)
    try:
        if callable(terminate):
            terminate()
        if callable(wait):
            wait(timeout=3)
    except Exception:
        if callable(kill):
            try:
                kill()
            except Exception:
                return


def _coerce_output(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    return str(value)


def invoke_claude(
    prompt: str,
    *,
    mode: str = "user",
    context_pack: Optional[Dict[str, Any]] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    mounts: Optional[AssistantCredentialMounts] = None,
    tool_policy: Optional[Dict[str, Any]] = None,
) -> ClaudeProviderResult:
    """Invoke the Claude Code CLI and return a normalized result.

    Degraded results are returned (not raised) for:
    - binary not found
    - missing or invalid auth mount
    - process timeout
    - non-zero exit with parseable output
    - malformed output that cannot be normalized

    ``tool_policy`` is reserved for future brokered tool-use enforcement; it
    is not forwarded as free shell flags.  The caller is responsible for
    ensuring only allowlisted operations reach this function.
    """
    if mounts is None:
        mounts = AssistantCredentialMounts()

    binary = _resolve_binary()
    if not binary:
        return ClaudeProviderResult(
            status="degraded",
            text="",
            degraded_reason="binary_not_found",
            config_dir="",
        )

    validations = mounts.validate_mounts()
    claude_validation = validations.get("claude")
    if claude_validation is None or not claude_validation.ready:
        reason = "auth_mount_missing"
        if claude_validation is not None:
            reason = f"auth_mount_{claude_validation.status}"
        return ClaudeProviderResult(
            status="degraded",
            text="",
            degraded_reason=reason,
            config_dir="",
        )

    config_dir = _resolve_config_dir(mounts)

    env = _claude_cli_env(config_dir)

    cmd = [
        binary,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--permission-mode",
        "plan",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return ClaudeProviderResult(
            status="degraded",
            text="",
            degraded_reason="timeout",
            config_dir=config_dir,
        )

    raw = proc.stdout.decode("utf-8", errors="replace")
    stderr_raw = proc.stderr.decode("utf-8", errors="replace").strip()

    try:
        text, events = _normalize_output(raw)
    except Exception:
        return ClaudeProviderResult(
            status="degraded",
            text="",
            raw_events=[],
            degraded_reason="malformed_output",
            exit_code=proc.returncode,
            config_dir=config_dir,
        )

    if proc.returncode != 0 and not text:
        degraded_reason = "non_zero_exit"
        if stderr_raw and ("auth" in stderr_raw.lower() or "login" in stderr_raw.lower()):
            degraded_reason = "auth_failure"
        return ClaudeProviderResult(
            status="degraded",
            text=text,
            raw_events=events,
            degraded_reason=degraded_reason,
            exit_code=proc.returncode,
            config_dir=config_dir,
        )

    return ClaudeProviderResult(
        status="ok",
        text=text,
        raw_events=events,
        exit_code=proc.returncode,
        config_dir=config_dir,
    )
