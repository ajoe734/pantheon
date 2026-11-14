#!/usr/bin/env python3
"""Cross-repository lease for the shared Pantheon dev environment.

The lease is a single JSON document on a dedicated execute-plans branch.  All
mutations use the GitHub Contents API's blob ``sha`` precondition, so acquire,
stale takeover, heartbeat, and release are compare-and-swap operations across
repositories.  The GitHub response ``Date`` header is the lease clock; runner
wall clocks are never trusted for ownership decisions.

Short-lived commands read authentication from
``PANTHEON_ENVIRONMENT_LEASE_TOKEN``.  ``heartbeat-loop --token-stdin`` reads
it from a closed stdin pipe instead, keeping the long-lived process environment
and argv credential-free.  The token is never written to the remote lease,
local state, or JSON evidence.
"""

from __future__ import annotations

import argparse
import base64
import email.utils
import hashlib
import json
import os
import random
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
DEFAULT_REPOSITORY = "ajoe734/execute-plans"
DEFAULT_BRANCH = "environment-coordination"
DEFAULT_PATH = ".pantheon/environment-leases/pantheon-dev-environment.json"
DEFAULT_RESOURCE = "pantheon-dev-environment"
DEFAULT_API_URL = "https://api.github.com"
TOKEN_ENV = "PANTHEON_ENVIRONMENT_LEASE_TOKEN"
VALID_MODES = frozenset({"qualification", "deployment"})
SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
MAX_STDIN_TOKEN_CHARACTERS = 8192
TRANSIENT_HTTP_STATUSES = frozenset({500, 502, 503, 504})
HTTP_TIMEOUT_SECONDS = 5
READ_ATTEMPTS = 3
DEFAULT_MAX_HEARTBEAT_AGE_SECONDS = 120
HEARTBEAT_RETRY_SLEEP_SECONDS = 5.0
MIN_RENEWAL_BUDGET_SECONDS = 1.0
RATE_LIMITED_REASON = "rate_limited"
RATE_LIMITED_ROLLBACK_PENDING_REASON = "rate_limited_rollback_pending"
RATE_LIMITED_ROLLBACK_PENDING_EXIT_CODE = 79
DEFAULT_RATE_LIMIT_JITTER_SECONDS = 2.0
MAX_RATE_LIMIT_JITTER_SECONDS = 5.0
DEFAULT_RATE_LIMIT_FALLBACK_WAIT_SECONDS = 60.0
DEFAULT_COMPENSATION_RATE_LIMIT_WAIT_SECONDS = 3900.0
DEFAULT_MIN_CORE_QUOTA_REMAINING = 1500
MIN_CORE_QUOTA_ENV = "PANTHEON_DEV_LEASE_MIN_REMAINING_QUOTA"
RATE_LIMIT_WAIT_ENV = "PANTHEON_DEV_ENVIRONMENT_LEASE_RATE_LIMIT_WAIT_SECONDS"
PREDECESSOR_FAILURE_FILE_ENV = "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE"
ACQUISITION_IMMUTABLE_FIELDS = (
    "schemaVersion",
    "repository",
    "branch",
    "path",
    "leaseId",
    "owner",
    "mode",
    "resource",
    "acquiredAt",
    "expectedBackendSha",
    "runUrl",
)


class LeaseError(RuntimeError):
    """Base class for fail-closed lease errors."""


class LeaseBusy(LeaseError):
    """Raised when another unexpired lease owns the resource."""


class LeaseLost(LeaseError):
    """Raised when the caller no longer owns the remote lease."""


class InitialLeaseVisibilityPending(LeaseLost):
    """Raised only when GitHub still serves the exact stale predecessor blob."""


class LeaseConflict(LeaseError):
    """Raised for a GitHub compare-and-swap conflict."""


class RateLimitedRollbackPending(LeaseError):
    """Raised when a GitHub rate-limit reset falls beyond the rollback budget."""

    reason = RATE_LIMITED_ROLLBACK_PENDING_REASON


class GitHubApiError(LeaseError):
    def __init__(self, status: int, message: str, body: str = "") -> None:
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status
        self.body = body


class GitHubRateLimitError(GitHubApiError):
    """Raised for primary or secondary GitHub API rate-limit responses."""

    reason = RATE_LIMITED_REASON

    def __init__(
        self,
        status: int,
        message: str,
        body: str = "",
        *,
        rate_limit_kind: str = "primary",
        remaining: int | None = None,
        reset_epoch: int | None = None,
        reset_at: datetime | None = None,
        retry_after_seconds: float | None = None,
        server_now: datetime | None = None,
    ) -> None:
        super().__init__(status, message, body)
        self.rate_limit_kind = rate_limit_kind
        self.remaining = remaining
        if reset_at is None and reset_epoch is not None:
            reset_at = datetime.fromtimestamp(int(reset_epoch), tz=timezone.utc)
        if reset_epoch is None and reset_at is not None:
            reset_epoch = int(reset_at.timestamp())
        self.reset_epoch = reset_epoch
        self.reset_at = reset_at
        self.retry_after_seconds = retry_after_seconds
        self.server_now = server_now


class GitHubTransportError(LeaseError):
    """A request failed without an authoritative GitHub response."""


def is_transient_error(exc: Exception) -> bool:
    return isinstance(exc, GitHubTransportError) or (
        isinstance(exc, GitHubApiError)
        and not isinstance(exc, GitHubRateLimitError)
        and exc.status in TRANSIENT_HTTP_STATUSES
    )


def is_rate_limit_error(exc: Exception) -> bool:
    return isinstance(exc, GitHubRateLimitError)


def classify_github_http_error(
    status: int,
    message: str,
    body: str = "",
    headers: Mapping[str, str] | None = None,
) -> GitHubApiError:
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    server_now: datetime | None = None
    if "date" in hdrs:
        try:
            server_now = github_server_now(hdrs)
        except LeaseError:
            server_now = None
    remaining: int | None = None
    raw_remaining = hdrs.get("x-ratelimit-remaining", "").strip()
    if raw_remaining:
        try:
            remaining = int(raw_remaining)
        except ValueError:
            remaining = None
    reset_epoch: int | None = None
    reset_at: datetime | None = None
    raw_reset = hdrs.get("x-ratelimit-reset", "").strip()
    if raw_reset:
        try:
            reset_epoch = int(float(raw_reset))
            reset_at = datetime.fromtimestamp(reset_epoch, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            reset_epoch = None
            reset_at = None
    retry_after_seconds: float | None = None
    raw_retry_after = hdrs.get("retry-after", "").strip()
    if raw_retry_after:
        try:
            retry_after_seconds = max(0.0, float(raw_retry_after))
        except ValueError:
            try:
                parsed_retry = email.utils.parsedate_to_datetime(raw_retry_after)
                if parsed_retry is not None:
                    if parsed_retry.tzinfo is None:
                        parsed_retry = parsed_retry.replace(tzinfo=timezone.utc)
                    ref_now = server_now or datetime.now(timezone.utc)
                    retry_after_seconds = max(
                        0.0,
                        (parsed_retry.astimezone(timezone.utc) - ref_now).total_seconds(),
                    )
            except (TypeError, ValueError):
                retry_after_seconds = None
    if status in (403, 429):
        combined_lower = f"{message} {body}".lower()
        has_rate_limit_message = (
            "rate limit exceeded" in combined_lower
            or "secondary rate limit" in combined_lower
            or "exceeded a secondary rate limit" in combined_lower
        )
        is_rate_limited = status == 429 or (
            status == 403
            and (
                (remaining is not None and remaining <= 0)
                or retry_after_seconds is not None
                or has_rate_limit_message
            )
        )
        if is_rate_limited:
            kind = (
                "secondary"
                if (retry_after_seconds is not None or "secondary rate limit" in combined_lower)
                else "primary"
            )
            return GitHubRateLimitError(
                status,
                message,
                body,
                rate_limit_kind=kind,
                remaining=remaining,
                reset_epoch=reset_epoch,
                reset_at=reset_at,
                retry_after_seconds=retry_after_seconds,
                server_now=server_now,
            )
    return GitHubApiError(status, message, body)


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: Mapping[str, str]
    payload: Any
    request_started_monotonic: float | None = None
    completed_monotonic: float | None = None


@dataclass(frozen=True)
class RemoteContent:
    state: dict[str, Any]
    content_sha: str
    server_now: datetime
    request_started_monotonic: float | None = None
    completed_monotonic: float | None = None


@dataclass(frozen=True)
class VerifiedRenewalBudget:
    validity_deadline_utc: datetime
    deadline_monotonic: float
    server_now: datetime
    content_sha: str
    heartbeat_at: datetime
    expires_at: datetime


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_utc_iso(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LeaseError(f"{label} must be a non-empty UTC timestamp")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LeaseError(f"{label} is not a valid timestamp: {text}") from exc
    if parsed.tzinfo is None:
        raise LeaseError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def github_server_now(headers: Mapping[str, str]) -> datetime:
    raw = headers.get("date") or headers.get("Date")
    if not raw:
        raise LeaseError("GitHub API response is missing the authoritative Date header")
    parsed = email.utils.parsedate_to_datetime(raw)
    if parsed is None:
        raise LeaseError(f"GitHub API Date header is invalid: {raw}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LeaseError(f"{label} must be a non-empty string")
    return value.strip()


def validate_state(
    raw: Any,
    *,
    repository: str,
    branch: str,
    path: str,
    resource: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LeaseError("remote lease JSON must be an object")
    state = dict(raw)
    if state.get("schemaVersion") != SCHEMA_VERSION:
        raise LeaseError(
            f"unsupported remote lease schemaVersion: {state.get('schemaVersion')!r}"
        )
    for key, expected in (
        ("repository", repository),
        ("branch", branch),
        ("path", path),
        ("resource", resource),
    ):
        actual = _require_text(state.get(key), key)
        if actual != expected:
            raise LeaseError(f"remote lease {key} mismatch: expected {expected}, got {actual}")
    mode = _require_text(state.get("mode"), "mode")
    if mode not in VALID_MODES:
        raise LeaseError(f"remote lease mode is unsupported: {mode}")
    _require_text(state.get("owner"), "owner")
    lease_id = _require_text(state.get("leaseId"), "leaseId")
    try:
        uuid.UUID(lease_id)
    except ValueError as exc:
        raise LeaseError("remote lease leaseId must be a UUID") from exc
    acquired = parse_utc_iso(state.get("acquiredAt"), "acquiredAt")
    heartbeat = parse_utc_iso(state.get("heartbeatAt"), "heartbeatAt")
    expires = parse_utc_iso(state.get("expiresAt"), "expiresAt")
    if heartbeat < acquired:
        raise LeaseError("remote lease heartbeatAt precedes acquiredAt")
    if expires <= heartbeat:
        raise LeaseError("remote lease expiresAt must be after heartbeatAt")
    expected_backend_sha = state.get("expectedBackendSha")
    if expected_backend_sha not in (None, "") and not SHA40_RE.fullmatch(
        str(expected_backend_sha)
    ):
        raise LeaseError("remote lease expectedBackendSha must be a 40-character SHA")
    if state.get("runUrl") not in (None, ""):
        _require_text(state.get("runUrl"), "runUrl")
    return state


def public_state(state: Mapping[str, Any], *, content_sha: str) -> dict[str, Any]:
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "resource": state["resource"],
        "mode": state["mode"],
        "owner": state["owner"],
        "leaseId": state["leaseId"],
        "acquiredAt": state["acquiredAt"],
        "heartbeatAt": state["heartbeatAt"],
        "expiresAt": state["expiresAt"],
        "repository": state["repository"],
        "branch": state["branch"],
        "path": state["path"],
        "contentSha": content_sha,
    }
    for key in ("expectedBackendSha", "runUrl"):
        if state.get(key):
            result[key] = state[key]
    previous_content_sha = state.get("previousContentSha")
    if previous_content_sha:
        if not SHA40_RE.fullmatch(str(previous_content_sha)):
            raise LeaseError("previousContentSha must be a 40-character SHA")
        result["previousContentSha"] = str(previous_content_sha).lower()
    return result


def atomic_write_json(path: str | Path, payload: Mapping[str, Any], mode: int) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        os.chmod(target, mode)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_json_file(path: str | Path, label: str) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise LeaseError(f"{label} is missing: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LeaseError(f"{label} is unreadable: {target}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LeaseError(f"{label} must contain a JSON object: {target}")
    return payload


def read_token_from_stdin() -> str:
    raw = sys.stdin.read(MAX_STDIN_TOKEN_CHARACTERS + 1)
    if len(raw) > MAX_STDIN_TOKEN_CHARACTERS:
        raise LeaseError("stdin lease token exceeds the maximum supported length")
    token = raw
    if token.endswith("\n"):
        token = token[:-1]
    if token.endswith("\r"):
        token = token[:-1]
    if "\r" in token or "\n" in token:
        raise LeaseError("stdin lease token must contain exactly one line")
    return _require_text(token, "stdin lease token")


def proc_start_ticks(pid: int) -> int:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError as exc:
        raise LeaseLost(f"heartbeat process {pid} stat is unavailable: {exc}") from exc
    close = raw.rfind(")")
    if close < 0:
        raise LeaseLost(f"heartbeat process {pid} stat is malformed")
    fields = raw[close + 1 :].strip().split()
    if len(fields) <= 19:
        raise LeaseLost(f"heartbeat process {pid} stat lacks start ticks")
    try:
        return int(fields[19])
    except ValueError as exc:
        raise LeaseLost(f"heartbeat process {pid} start ticks are invalid") from exc


def proc_cmdline(pid: int) -> bytes:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError as exc:
        raise LeaseLost(f"heartbeat process {pid} cmdline is unavailable: {exc}") from exc
    if not raw:
        raise LeaseLost(f"heartbeat process {pid} cmdline is empty")
    return raw


def resolved_process_argument(pid: int, argument: str) -> str:
    value = Path(argument)
    if not value.is_absolute():
        try:
            cwd = Path(os.readlink(f"/proc/{pid}/cwd"))
        except OSError as exc:
            raise LeaseLost(f"heartbeat process {pid} cwd is unavailable: {exc}") from exc
        value = cwd / value
    return str(value.resolve())


def heartbeat_identity_payload(pid: int, *, expected_cli: str, state_file: str) -> dict[str, Any]:
    cmdline = proc_cmdline(pid)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "running",
        "pid": pid,
        "startTicks": proc_start_ticks(pid),
        "cmdlineSha256": hashlib.sha256(cmdline).hexdigest(),
        "expectedCli": str(Path(expected_cli).resolve()),
        "stateFile": str(Path(state_file).resolve()),
        "recordedAt": utc_iso(datetime.now(timezone.utc)),
    }


def verify_heartbeat_identity(
    identity: Mapping[str, Any],
    *,
    pid: int,
    expected_cli: str,
    state_file: str,
) -> dict[str, Any]:
    expected_cli_path = str(Path(expected_cli).resolve())
    expected_state_path = str(Path(state_file).resolve())
    expected = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "running",
        "pid": pid,
        "expectedCli": expected_cli_path,
        "stateFile": expected_state_path,
    }
    for key, value in expected.items():
        if identity.get(key) != value:
            raise LeaseLost(
                f"heartbeat identity {key} mismatch: expected {value!r}, "
                f"got {identity.get(key)!r}"
            )

    actual_start_ticks = proc_start_ticks(pid)
    if identity.get("startTicks") != actual_start_ticks:
        raise LeaseLost(
            "heartbeat identity startTicks mismatch: "
            f"expected {identity.get('startTicks')!r}, got {actual_start_ticks!r}"
        )

    cmdline = proc_cmdline(pid)
    actual_digest = hashlib.sha256(cmdline).hexdigest()
    if identity.get("cmdlineSha256") != actual_digest:
        raise LeaseLost("heartbeat identity cmdline digest changed")

    arguments = [
        os.fsdecode(part) for part in cmdline.rstrip(b"\0").split(b"\0") if part
    ]
    cli_indices = []
    for index, argument in enumerate(arguments):
        try:
            resolved = resolved_process_argument(pid, argument)
        except LeaseLost:
            raise
        except (OSError, RuntimeError):
            continue
        if resolved == expected_cli_path:
            cli_indices.append(index)
    if not cli_indices:
        raise LeaseLost("heartbeat cmdline does not contain the pinned adjacent CLI")
    cli_index = cli_indices[0]
    if "heartbeat-loop" not in arguments[cli_index + 1 :]:
        raise LeaseLost("heartbeat cmdline is not running heartbeat-loop")
    try:
        state_index = arguments.index("--state-file", cli_index + 1)
        actual_state_path = resolved_process_argument(pid, arguments[state_index + 1])
    except (ValueError, IndexError) as exc:
        raise LeaseLost("heartbeat cmdline does not contain a valid --state-file") from exc
    if actual_state_path != expected_state_path:
        raise LeaseLost(
            f"heartbeat cmdline state file mismatch: expected {expected_state_path}, "
            f"got {actual_state_path}"
        )

    return {
        **dict(identity),
        "status": "verified",
        "verifiedAt": utc_iso(datetime.now(timezone.utc)),
    }


class GitHubClient:
    def __init__(self, token: str, api_url: str = DEFAULT_API_URL) -> None:
        self.token = _require_text(token, TOKEN_ENV)
        self.api_url = api_url.rstrip("/")
        self._deadline_monotonic: float | None = None
        self._sleep_offset: float = 0.0
        self._logical_monotonic: float | None = None

    def now_monotonic(self) -> float:
        current = time.monotonic() + self._sleep_offset
        if self._logical_monotonic is None or current > self._logical_monotonic:
            self._logical_monotonic = current
        return self._logical_monotonic

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        raw_before = time.monotonic()
        time.sleep(seconds)
        raw_after = time.monotonic()
        actual_advance = max(0.0, raw_after - raw_before)
        if seconds > actual_advance:
            self._sleep_offset += seconds - actual_advance
        self.now_monotonic()

    def _request(
        self,
        method: str,
        route: str,
        *,
        body: Mapping[str, Any] | None = None,
        allowed: Sequence[int] = (200,),
        deadline_monotonic: float | None = None,
    ) -> ApiResponse:
        # Only replay reads here. A failed write may already have committed;
        # heartbeat recovery must re-read ownership and obtain a fresh CAS SHA.
        effective_deadline = (
            deadline_monotonic
            if deadline_monotonic is not None
            else self._deadline_monotonic
        )
        attempts = READ_ATTEMPTS if method == "GET" else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            attempt_started = self.now_monotonic()
            timeout: float | int = HTTP_TIMEOUT_SECONDS
            if effective_deadline is not None:
                remaining = effective_deadline - attempt_started
                if remaining <= 0:
                    if last_error is not None:
                        raise last_error
                    raise GitHubTransportError(
                        f"GitHub API request deadline exhausted: {method} {route}"
                    )
                if remaining < HTTP_TIMEOUT_SECONDS:
                    timeout = remaining
            try:
                if timeout == HTTP_TIMEOUT_SECONDS:
                    response = self._request_once(
                        method, route, body=body, allowed=allowed
                    )
                else:
                    response = self._request_once(
                        method,
                        route,
                        body=body,
                        allowed=allowed,
                        timeout=timeout,
                    )
                completed_monotonic = self.now_monotonic()
                if (
                    effective_deadline is not None
                    and completed_monotonic >= effective_deadline
                ):
                    raise GitHubTransportError(
                        f"GitHub API request deadline exhausted after response: {method} {route}"
                    )
                return ApiResponse(
                    response.status,
                    response.headers,
                    response.payload,
                    request_started_monotonic=attempt_started,
                    completed_monotonic=completed_monotonic,
                )
            except (GitHubApiError, GitHubTransportError) as exc:
                last_error = exc
                if not is_transient_error(exc) or attempt + 1 == attempts:
                    raise
                retry_delay: float | int = attempt + 1
                if effective_deadline is not None:
                    remaining = effective_deadline - self.now_monotonic()
                    if remaining <= 0:
                        raise
                    if remaining <= retry_delay:
                        if remaining <= MIN_RENEWAL_BUDGET_SECONDS:
                            raise
                        retry_delay = min(1.0, remaining / 2.0)
                self.sleep(retry_delay)
        raise AssertionError("unreachable")

    def _request_once(
        self,
        method: str,
        route: str,
        *,
        body: Mapping[str, Any] | None,
        allowed: Sequence[int],
        timeout: float | int = HTTP_TIMEOUT_SECONDS,
    ) -> ApiResponse:
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_url}{route}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "pantheon-dev-environment-lease/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                payload = json.loads(raw) if raw else None
                headers = {key.lower(): value for key, value in response.headers.items()}
                result = ApiResponse(response.status, headers, payload)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            headers = {key.lower(): value for key, value in exc.headers.items()}
            payload: Any
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw
            result = ApiResponse(exc.code, headers, payload)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise GitHubTransportError(
                f"GitHub API transport failed: {method} {route}"
            ) from exc
        if result.status not in allowed:
            message = "request failed"
            if isinstance(result.payload, dict) and result.payload.get("message"):
                message = str(result.payload["message"])
            raise classify_github_http_error(
                result.status,
                message,
                json.dumps(result.payload),
                result.headers,
            )
        return result

    def get_rate_limit(self) -> dict[str, Any]:
        response = self._request("GET", "/rate_limit", allowed=(200,))
        now = github_server_now(response.headers)
        payload = response.payload if isinstance(response.payload, Mapping) else {}
        resources = payload.get("resources") if isinstance(payload, Mapping) else None
        core = resources.get("core") if isinstance(resources, Mapping) else None
        if not isinstance(core, Mapping):
            core = payload.get("rate") if isinstance(payload, Mapping) else None
        remaining: int | None = None
        limit: int | None = None
        reset_epoch: int | None = None
        if isinstance(core, Mapping):
            if core.get("remaining") is not None:
                try:
                    remaining = int(core["remaining"])
                except (TypeError, ValueError):
                    remaining = None
            if core.get("limit") is not None:
                try:
                    limit = int(core["limit"])
                except (TypeError, ValueError):
                    limit = None
            if core.get("reset") is not None:
                try:
                    reset_epoch = int(float(core["reset"]))
                except (TypeError, ValueError):
                    reset_epoch = None
        if remaining is None and "x-ratelimit-remaining" in response.headers:
            try:
                remaining = int(response.headers["x-ratelimit-remaining"])
            except ValueError:
                remaining = None
        if limit is None and "x-ratelimit-limit" in response.headers:
            try:
                limit = int(response.headers["x-ratelimit-limit"])
            except ValueError:
                limit = None
        if reset_epoch is None and "x-ratelimit-reset" in response.headers:
            try:
                reset_epoch = int(float(response.headers["x-ratelimit-reset"]))
            except ValueError:
                reset_epoch = None
        if remaining is None or reset_epoch is None:
            raise LeaseError("GitHub rate_limit response is missing core remaining/reset")
        reset_at = datetime.fromtimestamp(reset_epoch, tz=timezone.utc)
        return {
            "limit": limit,
            "remaining": remaining,
            "resetEpoch": reset_epoch,
            "resetAt": reset_at,
            "serverNow": now,
        }

    @staticmethod
    def _content_route(repository: str, path: str, branch: str | None = None) -> str:
        encoded_path = urllib.parse.quote(path, safe="/")
        route = f"/repos/{repository}/contents/{encoded_path}"
        if branch is not None:
            route += f"?{urllib.parse.urlencode({'ref': branch})}"
        return route

    def get_ref(self, repository: str, branch: str) -> tuple[str | None, datetime]:
        encoded = urllib.parse.quote(f"heads/{branch}", safe="")
        response = self._request(
            "GET", f"/repos/{repository}/git/ref/{encoded}", allowed=(200, 404)
        )
        now = github_server_now(response.headers)
        if response.status == 404:
            return None, now
        try:
            return str(response.payload["object"]["sha"]), now
        except (KeyError, TypeError) as exc:
            raise LeaseError("GitHub ref response is missing object.sha") from exc

    def get_default_branch(self, repository: str) -> tuple[str, datetime]:
        response = self._request("GET", f"/repos/{repository}")
        try:
            branch = _require_text(response.payload["default_branch"], "default_branch")
        except (KeyError, TypeError) as exc:
            raise LeaseError("GitHub repository response is missing default_branch") from exc
        return branch, github_server_now(response.headers)

    def create_ref(self, repository: str, branch: str, sha: str) -> datetime:
        response = self._request(
            "POST",
            f"/repos/{repository}/git/refs",
            body={"ref": f"refs/heads/{branch}", "sha": sha},
            allowed=(201,),
        )
        return github_server_now(response.headers)

    def get_content(self, repository: str, branch: str, path: str) -> RemoteContent | None:
        response = self._request(
            "GET", self._content_route(repository, path, branch), allowed=(200, 404)
        )
        now = github_server_now(response.headers)
        if response.status == 404:
            return None
        payload = response.payload
        try:
            if payload.get("encoding") != "base64":
                raise LeaseError("GitHub Contents API response is not base64 encoded")
            raw = base64.b64decode(payload["content"], validate=False).decode("utf-8")
            state = json.loads(raw)
            content_sha = str(payload["sha"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LeaseError("GitHub lease content is malformed") from exc
        if not content_sha:
            raise LeaseError("GitHub lease content sha is missing")
        return RemoteContent(
            state=state,
            content_sha=content_sha,
            server_now=now,
            request_started_monotonic=response.request_started_monotonic,
            completed_monotonic=response.completed_monotonic,
        )

    def put_content(
        self,
        repository: str,
        branch: str,
        path: str,
        state: Mapping[str, Any],
        *,
        expected_sha: str | None,
        message: str,
    ) -> tuple[str, datetime]:
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(
                (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8")
            ).decode("ascii"),
            "branch": branch,
        }
        if expected_sha:
            body["sha"] = expected_sha
        try:
            response = self._request(
                "PUT",
                self._content_route(repository, path),
                body=body,
                allowed=(200, 201),
            )
        except GitHubApiError as exc:
            if exc.status in (409, 422):
                raise LeaseConflict(str(exc)) from exc
            raise
        try:
            content_sha = str(response.payload["content"]["sha"])
        except (KeyError, TypeError) as exc:
            raise LeaseError("GitHub update response is missing content.sha") from exc
        return content_sha, github_server_now(response.headers)

    def delete_content(
        self,
        repository: str,
        branch: str,
        path: str,
        *,
        expected_sha: str,
        message: str,
    ) -> datetime:
        try:
            response = self._request(
                "DELETE",
                self._content_route(repository, path),
                body={"message": message, "sha": expected_sha, "branch": branch},
                allowed=(200,),
            )
        except GitHubApiError as exc:
            if exc.status in (409, 422):
                raise LeaseConflict(str(exc)) from exc
            raise
        return github_server_now(response.headers)


class LeaseManager:
    def __init__(
        self,
        client: GitHubClient,
        *,
        repository: str,
        branch: str,
        path: str,
        resource: str,
    ) -> None:
        self.client = client
        self.repository = repository
        self.branch = branch
        self.path = path
        self.resource = resource
        self.max_heartbeat_age_seconds = DEFAULT_MAX_HEARTBEAT_AGE_SECONDS
        self.rate_limit_jitter_seconds: float = DEFAULT_RATE_LIMIT_JITTER_SECONDS
        self._sleep_offset: float = 0.0
        self._logical_monotonic: float | None = None
        self._active_deadline_monotonic: float | None = None
        self._recovery_anchor: VerifiedRenewalBudget | None = None

    def _bounded_jitter(self) -> float:
        base = max(0.0, min(float(self.rate_limit_jitter_seconds), MAX_RATE_LIMIT_JITTER_SECONDS))
        if base <= 0:
            return 0.0
        return base

    def _current_now_utc(self, fallback: datetime | None = None) -> datetime:
        if fallback is not None:
            return fallback
        client_now = getattr(self.client, "now", None)
        if isinstance(client_now, datetime):
            return client_now
        if callable(client_now):
            val = client_now()
            if isinstance(val, datetime):
                return val
        return datetime.now(timezone.utc)

    def _rate_limit_wait_seconds(self, exc: GitHubRateLimitError) -> tuple[float, datetime | None]:
        ref_now = self._current_now_utc(exc.server_now)
        wait_seconds = 0.0
        reset_at = exc.reset_at
        if exc.retry_after_seconds is not None:
            wait_seconds = max(wait_seconds, float(exc.retry_after_seconds))
            if reset_at is None:
                reset_at = ref_now + timedelta(seconds=float(exc.retry_after_seconds))
        if reset_at is not None:
            delta = (reset_at - ref_now).total_seconds()
            wait_seconds = max(wait_seconds, delta)
        if exc.retry_after_seconds is None and reset_at is None:
            wait_seconds = DEFAULT_RATE_LIMIT_FALLBACK_WAIT_SECONDS
            reset_at = ref_now + timedelta(seconds=wait_seconds)
        total_wait = max(0.0, wait_seconds) + self._bounded_jitter()
        return total_wait, reset_at

    def wait_for_core_quota(
        self,
        *,
        min_remaining: int = DEFAULT_MIN_CORE_QUOTA_REMAINING,
    ) -> dict[str, Any]:
        if min_remaining <= 0:
            return {
                "schemaVersion": SCHEMA_VERSION,
                "status": "quota_check_skipped",
                "minRemaining": min_remaining,
            }
        waited_seconds = 0.0
        attempts = 0
        while True:
            attempts += 1
            try:
                quota = self.client.get_rate_limit()
                remaining = int(quota["remaining"])
                limit = quota.get("limit")
                reset_at: datetime = quota["resetAt"]
                server_now: datetime = quota["serverNow"]
                observed_event = {
                    "schemaVersion": SCHEMA_VERSION,
                    "status": "quota_observed",
                    "resource": self.resource,
                    "remaining": remaining,
                    "limit": limit,
                    "minRemaining": min_remaining,
                    "resetAt": utc_iso(reset_at),
                    "observedAt": utc_iso(server_now),
                    "attempt": attempts,
                }
                print(json.dumps(observed_event, sort_keys=True), file=sys.stderr)
                if remaining >= min_remaining:
                    return {
                        "schemaVersion": SCHEMA_VERSION,
                        "status": "quota_ready",
                        "resource": self.resource,
                        "remaining": remaining,
                        "limit": limit,
                        "minRemaining": min_remaining,
                        "resetAt": utc_iso(reset_at),
                        "observedAt": utc_iso(server_now),
                        "waitedSeconds": round(waited_seconds, 3),
                        "attempts": attempts,
                    }
                wait_seconds = max(0.0, (reset_at - server_now).total_seconds()) + self._bounded_jitter()
                wait_seconds = max(1.0, wait_seconds)
                wait_event = {
                    "schemaVersion": SCHEMA_VERSION,
                    "status": "quota_waiting",
                    "resource": self.resource,
                    "reason": RATE_LIMITED_REASON,
                    "remaining": remaining,
                    "limit": limit,
                    "minRemaining": min_remaining,
                    "resetAt": utc_iso(reset_at),
                    "waitSeconds": round(wait_seconds, 3),
                    "observedAt": utc_iso(server_now),
                }
                print(json.dumps(wait_event, sort_keys=True), file=sys.stderr)
                self._sleep(wait_seconds)
                waited_seconds += wait_seconds
            except GitHubRateLimitError as exc:
                wait_seconds, reset_at_opt = self._rate_limit_wait_seconds(exc)
                wait_seconds = max(1.0, wait_seconds)
                wait_event = {
                    "schemaVersion": SCHEMA_VERSION,
                    "status": "quota_waiting",
                    "resource": self.resource,
                    "reason": RATE_LIMITED_REASON,
                    "remaining": exc.remaining if exc.remaining is not None else 0,
                    "minRemaining": min_remaining,
                    "resetAt": utc_iso(reset_at_opt) if reset_at_opt else None,
                    "waitSeconds": round(wait_seconds, 3),
                    "error": str(exc),
                }
                print(json.dumps(wait_event, sort_keys=True), file=sys.stderr)
                self._sleep(wait_seconds)
                waited_seconds += wait_seconds
            except Exception as exc:
                fallback_wait = min(DEFAULT_RATE_LIMIT_FALLBACK_WAIT_SECONDS, 15.0)
                warn_event = {
                    "schemaVersion": SCHEMA_VERSION,
                    "status": "quota_probe_warning",
                    "resource": self.resource,
                    "minRemaining": min_remaining,
                    "waitSeconds": round(fallback_wait, 3),
                    "warning": f"core quota check error (waiting before retry): {exc}",
                }
                print(json.dumps(warn_event, sort_keys=True), file=sys.stderr)
                self._sleep(fallback_wait)
                waited_seconds += fallback_wait
                if attempts >= 3 and not isinstance(exc, (GitHubRateLimitError, GitHubTransportError)):
                    return {
                        "schemaVersion": SCHEMA_VERSION,
                        "status": "quota_probe_degraded",
                        "resource": self.resource,
                        "minRemaining": min_remaining,
                        "waitedSeconds": round(waited_seconds, 3),
                        "attempts": attempts,
                        "warning": str(exc),
                    }

    def _now_monotonic(self) -> float:
        client_now = getattr(self.client, "now_monotonic", None)
        if callable(client_now):
            current = float(client_now())
        else:
            current = time.monotonic() + self._sleep_offset
        if self._logical_monotonic is None or current > self._logical_monotonic:
            self._logical_monotonic = current
        if hasattr(self.client, "_logical_monotonic"):
            setattr(self.client, "_logical_monotonic", self._logical_monotonic)
        return self._logical_monotonic

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        client_sleep = getattr(self.client, "sleep", None)
        if callable(client_sleep):
            client_sleep(seconds)
            self._now_monotonic()
            return
        raw_before = time.monotonic()
        time.sleep(seconds)
        raw_after = time.monotonic()
        actual_advance = max(0.0, raw_after - raw_before)
        if seconds > actual_advance:
            self._sleep_offset += seconds - actual_advance
        self._now_monotonic()

    @contextmanager
    def _client_deadline(self, deadline_monotonic: float | None) -> Iterator[None]:
        previous = getattr(self.client, "_deadline_monotonic", None)
        setattr(self.client, "_deadline_monotonic", deadline_monotonic)
        try:
            yield
        finally:
            setattr(self.client, "_deadline_monotonic", previous)

    def _validated(self, remote: RemoteContent) -> dict[str, Any]:
        return validate_state(
            remote.state,
            repository=self.repository,
            branch=self.branch,
            path=self.path,
            resource=self.resource,
        )

    def ensure_branch(self) -> None:
        sha, _ = self.client.get_ref(self.repository, self.branch)
        if not sha:
            raise LeaseError(
                f"coordination branch {self.repository}:{self.branch} is missing; "
                "run the bootstrap command once"
            )

    def bootstrap(self, base_branch: str) -> dict[str, Any]:
        existing, now = self.client.get_ref(self.repository, self.branch)
        if existing:
            return {
                "schemaVersion": SCHEMA_VERSION,
                "status": "exists",
                "repository": self.repository,
                "branch": self.branch,
                "headSha": existing,
                "verifiedAt": utc_iso(now),
            }
        if not base_branch:
            base_branch, _ = self.client.get_default_branch(self.repository)
        base_sha, _ = self.client.get_ref(self.repository, base_branch)
        if not base_sha:
            raise LeaseError(
                f"bootstrap base branch {self.repository}:{base_branch} is missing"
            )
        try:
            created_at = self.client.create_ref(self.repository, self.branch, base_sha)
            status = "created"
            head_sha = base_sha
        except GitHubApiError as exc:
            if exc.status not in (409, 422):
                raise
            head_sha, created_at = self.client.get_ref(self.repository, self.branch)
            if not head_sha:
                raise LeaseConflict("coordination branch bootstrap lost a CAS race") from exc
            status = "exists"
        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": status,
            "repository": self.repository,
            "branch": self.branch,
            "baseBranch": base_branch,
            "headSha": head_sha,
            "verifiedAt": utc_iso(created_at),
        }

    def _wait_for_predecessor_rate_limit_reset(
        self,
        failure_file: str,
        *,
        deadline_monotonic: float,
        owner: str,
    ) -> None:
        path = Path(failure_file)
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, Mapping) or payload.get("reason") != RATE_LIMITED_REASON:
            return
        raw_reset_at = payload.get("rateLimitResetAt")
        retry_after = payload.get("retryAfterSeconds")
        ref_now = self._current_now_utc()
        wait_seconds = 0.0
        reset_at: datetime | None = None
        if isinstance(raw_reset_at, str) and raw_reset_at.strip():
            try:
                reset_at = parse_utc_iso(raw_reset_at, "rateLimitResetAt")
                wait_seconds = max(wait_seconds, (reset_at - ref_now).total_seconds())
            except LeaseError:
                reset_at = None
        if retry_after is not None:
            try:
                wait_seconds = max(wait_seconds, float(retry_after))
            except (TypeError, ValueError):
                pass
        if wait_seconds <= 0:
            return
        total_wait = wait_seconds + self._bounded_jitter()
        remaining_budget = deadline_monotonic - self._now_monotonic()
        if total_wait > remaining_budget:
            reset_desc = utc_iso(reset_at) if reset_at is not None else f"{total_wait:.1f}s"
            raise RateLimitedRollbackPending(
                f"{RATE_LIMITED_ROLLBACK_PENDING_REASON}: predecessor heartbeat lost due to "
                f"rate_limited and reset ({reset_desc}, wait {total_wait:.1f}s) exceeds "
                f"remaining rollback lease budget ({ max(0.0, remaining_budget):.1f}s) for {owner}"
            )
        print(
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "status": "rate_limit_waiting",
                    "phase": "predecessor_failure_handoff",
                    "resource": self.resource,
                    "owner": owner,
                    "reason": RATE_LIMITED_REASON,
                    "rateLimitResetAt": utc_iso(reset_at) if reset_at else None,
                    "waitSeconds": round(total_wait, 3),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        self._sleep(total_wait)

    def _sleep_for_rate_limit_or_raise(
        self,
        exc: GitHubRateLimitError,
        *,
        deadline_monotonic: float,
        owner: str,
        phase: str,
    ) -> None:
        total_wait, reset_at = self._rate_limit_wait_seconds(exc)
        remaining_budget = deadline_monotonic - self._now_monotonic()
        if total_wait > remaining_budget:
            reset_desc = utc_iso(reset_at) if reset_at is not None else f"{total_wait:.1f}s"
            raise RateLimitedRollbackPending(
                f"{RATE_LIMITED_ROLLBACK_PENDING_REASON}: GitHub API rate limit reset "
                f"({reset_desc}, wait {total_wait:.1f}s) exceeds remaining lease budget "
                f"({max(0.0, remaining_budget):.1f}s) during {phase} for {owner}: {exc}"
            ) from exc
        print(
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "status": "rate_limit_waiting",
                    "phase": phase,
                    "resource": self.resource,
                    "owner": owner,
                    "reason": RATE_LIMITED_REASON,
                    "rateLimitKind": exc.rate_limit_kind,
                    "rateLimitResetAt": utc_iso(reset_at) if reset_at else None,
                    "retryAfterSeconds": exc.retry_after_seconds,
                    "waitSeconds": round(total_wait, 3),
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        self._sleep(max(0.5, total_wait))

    def acquire(
        self,
        *,
        mode: str,
        owner: str,
        ttl_seconds: int,
        wait_seconds: int,
        poll_seconds: float,
        expected_backend_sha: str = "",
        run_url: str = "",
        predecessor_failure_file: str = "",
        min_remaining_quota: int = 0,
    ) -> tuple[dict[str, Any], str, datetime]:
        if mode not in VALID_MODES:
            raise LeaseError(f"unsupported lease mode: {mode}")
        owner = _require_text(owner, "owner")
        if ttl_seconds < 30 or ttl_seconds > 3600:
            raise LeaseError("ttl-seconds must be between 30 and 3600")
        if wait_seconds < 0:
            raise LeaseError("wait-seconds must be non-negative")
        if poll_seconds <= 0 or poll_seconds > 60:
            raise LeaseError("poll-seconds must be greater than 0 and at most 60")
        if expected_backend_sha and not SHA40_RE.fullmatch(expected_backend_sha):
            raise LeaseError("expected-backend-sha must be a 40-character SHA")

        if min_remaining_quota > 0:
            self.wait_for_core_quota(min_remaining=min_remaining_quota)

        deadline = self._now_monotonic() + wait_seconds
        effective_failure_file = predecessor_failure_file or (
            os.environ.get(PREDECESSOR_FAILURE_FILE_ENV, "")
            if owner.endswith(":rollback")
            else ""
        )
        if effective_failure_file:
            self._wait_for_predecessor_rate_limit_reset(
                effective_failure_file,
                deadline_monotonic=deadline,
                owner=owner,
            )

        branch_verified = False
        last_busy: dict[str, Any] | None = None
        while True:
            try:
                if not branch_verified:
                    self.ensure_branch()
                    branch_verified = True
                remote = self.client.get_content(self.repository, self.branch, self.path)
                expected_sha: str | None = None
                if remote is None:
                    # A missing file is free only because ensure_branch distinguished
                    # it from a missing coordination branch.
                    # A second lightweight ref read supplies the authoritative clock
                    # for the 404-content case without trusting the runner clock.
                    head_sha, now = self.client.get_ref(self.repository, self.branch)
                    if not head_sha:
                        raise LeaseError(
                            f"coordination branch {self.repository}:{self.branch} "
                            "disappeared during acquisition"
                        )
                else:
                    state = self._validated(remote)
                    now = remote.server_now
                    expires = parse_utc_iso(state["expiresAt"], "expiresAt")
                    if expires > now:
                        last_busy = public_state(state, content_sha=remote.content_sha)
                        remaining = max(0.0, (expires - now).total_seconds())
                        if self._now_monotonic() >= deadline:
                            raise LeaseBusy(
                                "dev environment is leased by "
                                f"{state['owner']} ({state['mode']}) until {state['expiresAt']}"
                            )
                        self._sleep(min(poll_seconds, remaining or poll_seconds))
                        continue
                    expected_sha = remote.content_sha

                lease_id = str(uuid.uuid4())
                timestamp = utc_iso(now)
                candidate: dict[str, Any] = {
                    "schemaVersion": SCHEMA_VERSION,
                    "resource": self.resource,
                    "mode": mode,
                    "owner": owner,
                    "leaseId": lease_id,
                    "acquiredAt": timestamp,
                    "heartbeatAt": timestamp,
                    "expiresAt": utc_iso(now + timedelta(seconds=ttl_seconds)),
                    "repository": self.repository,
                    "branch": self.branch,
                    "path": self.path,
                }
                if expected_backend_sha:
                    candidate["expectedBackendSha"] = expected_backend_sha.lower()
                if run_url:
                    candidate["runUrl"] = run_url
                action = "take over stale" if expected_sha else "acquire"
                try:
                    content_sha, written_at = self.client.put_content(
                        self.repository,
                        self.branch,
                        self.path,
                        candidate,
                        expected_sha=expected_sha,
                        message=f"lease: {action} {self.resource} for {owner}",
                    )
                except LeaseConflict:
                    if self._now_monotonic() >= deadline:
                        raise LeaseBusy(
                            "dev environment lease CAS remained contended"
                            + (f"; last owner={last_busy.get('owner')}" if last_busy else "")
                        )
                    self._sleep(min(poll_seconds, random.uniform(0.25, 1.0)))
                    continue
                if expected_sha:
                    # Local-only evidence binds a bounded initial visibility retry
                    # to the exact stale blob replaced by this successful CAS.
                    candidate["previousContentSha"] = expected_sha.lower()
                return candidate, content_sha, written_at
            except GitHubRateLimitError as exc:
                self._sleep_for_rate_limit_or_raise(
                    exc,
                    deadline_monotonic=deadline,
                    owner=owner,
                    phase="acquire",
                )
                continue

    def _is_exact_stale_predecessor(
        self,
        local_state: Mapping[str, Any],
        state: Mapping[str, Any],
        remote: RemoteContent,
    ) -> bool:
        previous_content_sha = local_state.get("previousContentSha")
        if not isinstance(previous_content_sha, str) or not SHA40_RE.fullmatch(
            previous_content_sha
        ):
            return False
        if remote.content_sha.lower() != previous_content_sha.lower():
            return False
        local_acquired = parse_utc_iso(local_state.get("acquiredAt"), "acquiredAt")
        remote_acquired = parse_utc_iso(state.get("acquiredAt"), "acquiredAt")
        remote_expires = parse_utc_iso(state.get("expiresAt"), "expiresAt")
        return remote_acquired < local_acquired and remote_expires <= local_acquired

    def _matching_remote(
        self,
        local_state: Mapping[str, Any],
        *,
        require_active: bool,
        allow_exact_stale_predecessor: bool = False,
    ) -> tuple[dict[str, Any], RemoteContent]:
        remote = self.client.get_content(self.repository, self.branch, self.path)
        if remote is None:
            raise LeaseLost("remote dev environment lease is missing")
        for key in ACQUISITION_IMMUTABLE_FIELDS:
            if remote.state.get(key) != local_state.get(key):
                if allow_exact_stale_predecessor:
                    state = self._validated(remote)
                    if self._is_exact_stale_predecessor(local_state, state, remote):
                        raise InitialLeaseVisibilityPending(
                            "GitHub still serves the exact expired predecessor blob "
                            f"{remote.content_sha} after acquisition"
                        )
                raise LeaseLost(
                    f"remote dev environment lease immutable field {key} changed from "
                    f"{local_state.get(key)!r} to {remote.state.get(key)!r}"
                )
        state = self._validated(remote)
        if require_active and parse_utc_iso(state["expiresAt"], "expiresAt") <= remote.server_now:
            raise LeaseLost(f"dev environment lease expired at {state['expiresAt']}")
        return state, remote

    def _resolve_verify_rate_limit_wait_seconds(
        self,
        local_state: Mapping[str, Any],
        rate_limit_wait_seconds: float | None,
    ) -> float:
        if rate_limit_wait_seconds is not None:
            return float(rate_limit_wait_seconds)
        raw_env = os.environ.get(RATE_LIMIT_WAIT_ENV, "").strip()
        if raw_env:
            try:
                return float(raw_env)
            except ValueError as exc:
                raise LeaseError(f"{RATE_LIMIT_WAIT_ENV} must be numeric") from exc
        owner = str(local_state.get("owner") or "")
        if owner.endswith(":rollback"):
            return DEFAULT_COMPENSATION_RATE_LIMIT_WAIT_SECONDS
        return 0.0

    def verify(
        self,
        local_state: Mapping[str, Any],
        *,
        max_heartbeat_age_seconds: int,
        initial_visibility_wait_seconds: float = 0.0,
        initial_visibility_poll_seconds: float = 0.5,
        rate_limit_wait_seconds: float | None = None,
        ttl_seconds: int = 300,
        state_file: str = "",
    ) -> dict[str, Any]:
        if initial_visibility_wait_seconds < 0 or initial_visibility_wait_seconds > 30:
            raise LeaseError(
                "initial-visibility-wait-seconds must be between 0 and 30"
            )
        if initial_visibility_poll_seconds <= 0 or initial_visibility_poll_seconds > 5:
            raise LeaseError(
                "initial-visibility-poll-seconds must be greater than 0 and at most 5"
            )
        if max_heartbeat_age_seconds <= 0:
            raise LeaseError("max-heartbeat-age-seconds must be positive")
        effective_rate_limit_wait = self._resolve_verify_rate_limit_wait_seconds(
            local_state, rate_limit_wait_seconds
        )
        if effective_rate_limit_wait < 0:
            raise LeaseError("rate-limit-wait-seconds must be non-negative")
        start_monotonic = self._now_monotonic()
        visibility_deadline = start_monotonic + initial_visibility_wait_seconds
        rate_limit_deadline = start_monotonic + effective_rate_limit_seconds(
            effective_rate_limit_wait, initial_visibility_wait_seconds
        )
        current_local: dict[str, Any] = dict(local_state)
        waited_for_rate_limit = False

        while True:
            try:
                state, remote = self._matching_remote(
                    current_local,
                    require_active=True,
                    allow_exact_stale_predecessor=(
                        initial_visibility_wait_seconds > 0
                    ),
                )
                heartbeat = parse_utc_iso(state["heartbeatAt"], "heartbeatAt")
                age = (remote.server_now - heartbeat).total_seconds()
                if age > max_heartbeat_age_seconds:
                    raise LeaseLost(
                        f"dev environment lease heartbeat is stale: {age:.0f}s > "
                        f"{max_heartbeat_age_seconds}s"
                    )
                break
            except GitHubRateLimitError as exc:
                if effective_rate_limit_wait <= 0:
                    raise
                owner = str(current_local.get("owner") or "unknown")
                self._sleep_for_rate_limit_or_raise(
                    exc,
                    deadline_monotonic=rate_limit_deadline,
                    owner=owner,
                    phase="verify",
                )
                waited_for_rate_limit = True
                visibility_deadline = max(
                    visibility_deadline,
                    self._now_monotonic() + initial_visibility_wait_seconds,
                )
                continue
            except InitialLeaseVisibilityPending as exc:
                if waited_for_rate_limit and effective_rate_limit_wait > 0:
                    current_local = self._reacquire_after_rate_limit_wait(
                        current_local,
                        local_state_Sink=local_state,
                        rate_limit_deadline=rate_limit_deadline,
                        ttl_seconds=ttl_seconds,
                        state_file=state_file,
                    )
                    waited_for_rate_limit = False
                    visibility_deadline = (
                        self._now_monotonic() + initial_visibility_wait_seconds
                    )
                    continue
                remaining = visibility_deadline - self._now_monotonic()
                if remaining <= 0:
                    raise LeaseLost(
                        "initial lease visibility remained on the exact stale "
                        "predecessor until the bounded timeout"
                    ) from exc
                self._sleep(min(initial_visibility_poll_seconds, remaining))
            except LeaseLost:
                if waited_for_rate_limit and effective_rate_limit_wait > 0:
                    current_local = self._reacquire_after_rate_limit_wait(
                        current_local,
                        local_state_Sink=local_state,
                        rate_limit_deadline=rate_limit_deadline,
                        ttl_seconds=ttl_seconds,
                        state_file=state_file,
                    )
                    waited_for_rate_limit = False
                    visibility_deadline = (
                        self._now_monotonic() + initial_visibility_wait_seconds
                    )
                    continue
                raise

        result = public_state(state, content_sha=remote.content_sha)
        result.update(
            {
                "status": "verified",
                "verifiedAt": utc_iso(remote.server_now),
                "heartbeatAgeSeconds": max(0, int(age)),
            }
        )
        return result

    def _reacquire_after_rate_limit_wait(
        self,
        current_local: Mapping[str, Any],
        *,
        local_state_Sink: Mapping[str, Any],
        rate_limit_deadline: float,
        ttl_seconds: int,
        state_file: str,
    ) -> dict[str, Any]:
        remaining_wait = max(1, int(rate_limit_deadline - self._now_monotonic()))
        acquired = parse_utc_iso(current_local.get("acquiredAt"), "acquiredAt")
        expires = parse_utc_iso(current_local.get("expiresAt"), "expiresAt")
        inferred_ttl = max(30, min(3600, int((expires - acquired).total_seconds()) or ttl_seconds))
        new_state, new_sha, _ = self.acquire(
            mode=str(current_local["mode"]),
            owner=str(current_local["owner"]),
            ttl_seconds=inferred_ttl,
            wait_seconds=remaining_wait,
            poll_seconds=5.0,
            expected_backend_sha=str(current_local.get("expectedBackendSha") or ""),
            run_url=str(current_local.get("runUrl") or ""),
        )
        updated_local = public_state(new_state, content_sha=new_sha)
        if "previousContentSha" in new_state:
            updated_local["previousContentSha"] = new_state["previousContentSha"]
        if state_file:
            atomic_write_json(state_file, updated_local, 0o600)
        if isinstance(local_state_Sink, dict):
            local_state_Sink.clear()
            local_state_Sink.update(updated_local)
        return updated_local

    def _fallback_budget_from_local_state(
        self,
        local_state: Mapping[str, Any],
        *,
        effective_freshness_seconds: int,
        exc: GitHubRateLimitError,
    ) -> VerifiedRenewalBudget | None:
        if self._recovery_anchor is not None:
            return self._recovery_anchor
        if exc.server_now is None:
            return None
        try:
            heartbeat_at = parse_utc_iso(local_state.get("heartbeatAt"), "heartbeatAt")
            expires_at = parse_utc_iso(local_state.get("expiresAt"), "expiresAt")
            content_sha = str(local_state.get("contentSha") or "")
        except LeaseError:
            return None
        freshness_deadline = heartbeat_at + timedelta(seconds=effective_freshness_seconds)
        validity_deadline_utc = min(expires_at, freshness_deadline)
        remaining_validity = (validity_deadline_utc - exc.server_now).total_seconds()
        if remaining_validity <= 0:
            return None
        deadline_monotonic = self._now_monotonic() + remaining_validity
        if self._active_deadline_monotonic is not None:
            deadline_monotonic = min(deadline_monotonic, self._active_deadline_monotonic)
        return VerifiedRenewalBudget(
            validity_deadline_utc=validity_deadline_utc,
            deadline_monotonic=deadline_monotonic,
            server_now=exc.server_now,
            content_sha=content_sha,
            heartbeat_at=heartbeat_at,
            expires_at=expires_at,
        )

    def heartbeat(
        self,
        local_state: Mapping[str, Any],
        *,
        ttl_seconds: int,
        max_heartbeat_age_seconds: int | None = None,
    ) -> tuple[dict[str, Any], str, datetime]:
        if ttl_seconds < 30 or ttl_seconds > 3600:
            raise LeaseError("ttl-seconds must be between 30 and 3600")
        configured_freshness = (
            self.max_heartbeat_age_seconds
            if max_heartbeat_age_seconds is None
            else max_heartbeat_age_seconds
        )
        if configured_freshness <= 0:
            raise LeaseError("max-heartbeat-age-seconds must be positive")
        effective_freshness_seconds = min(
            int(configured_freshness), DEFAULT_MAX_HEARTBEAT_AGE_SECONDS
        )
        if (
            self._active_deadline_monotonic is not None
            and self._now_monotonic() >= self._active_deadline_monotonic
        ):
            raise LeaseLost(
                "dev environment lease validity budget exhausted before renewal read"
            )
        read_started_monotonic = self._now_monotonic()
        try:
            with self._client_deadline(self._active_deadline_monotonic):
                state, remote = self._matching_remote(local_state, require_active=True)
        except GitHubRateLimitError as exc:
            fallback_budget = self._fallback_budget_from_local_state(
                local_state,
                effective_freshness_seconds=effective_freshness_seconds,
                exc=exc,
            )
            if (
                fallback_budget is not None
                and self._now_monotonic() < fallback_budget.deadline_monotonic
            ):
                setattr(exc, "_verified_renewal_budget", fallback_budget)
            raise
        observed_monotonic = self._now_monotonic()
        if (
            self._active_deadline_monotonic is not None
            and observed_monotonic >= self._active_deadline_monotonic
        ):
            raise LeaseLost(
                "dev environment lease validity budget exhausted during renewal read"
            )
        request_started_monotonic = getattr(
            remote, "request_started_monotonic", None
        )
        anchor_monotonic = (
            min(read_started_monotonic, float(request_started_monotonic))
            if request_started_monotonic is not None
            else read_started_monotonic
        )
        now = remote.server_now
        heartbeat_at = parse_utc_iso(state["heartbeatAt"], "heartbeatAt")
        expires_at = parse_utc_iso(state["expiresAt"], "expiresAt")
        age = (now - heartbeat_at).total_seconds()
        freshness_deadline = heartbeat_at + timedelta(
            seconds=effective_freshness_seconds
        )
        if freshness_deadline <= now:
            raise LeaseLost(
                f"dev environment lease heartbeat is stale: {age:.0f}s >= "
                f"{effective_freshness_seconds}s"
            )
        validity_deadline_utc = min(expires_at, freshness_deadline)
        remaining_validity_seconds = (validity_deadline_utc - now).total_seconds()
        if remaining_validity_seconds <= 0:
            raise LeaseLost(
                f"dev environment lease validity budget exhausted at {utc_iso(now)}"
            )
        get_elapsed_seconds = max(0.0, observed_monotonic - anchor_monotonic)
        remaining_after_get_seconds = (
            remaining_validity_seconds - get_elapsed_seconds
        )
        candidate_deadline_monotonic = (
            anchor_monotonic + remaining_validity_seconds
        )
        prior = self._recovery_anchor
        if (
            prior is not None
            and remote.content_sha == prior.content_sha
            and validity_deadline_utc <= prior.validity_deadline_utc
        ):
            deadline_monotonic = min(
                prior.deadline_monotonic, candidate_deadline_monotonic
            )
        else:
            deadline_monotonic = candidate_deadline_monotonic
        if remaining_after_get_seconds <= 0 or deadline_monotonic <= observed_monotonic:
            self._active_deadline_monotonic = deadline_monotonic
            raise LeaseLost(
                "dev environment lease validity budget exhausted before renewal write"
            )
        budget = VerifiedRenewalBudget(
            validity_deadline_utc=validity_deadline_utc,
            deadline_monotonic=deadline_monotonic,
            server_now=now,
            content_sha=remote.content_sha,
            heartbeat_at=heartbeat_at,
            expires_at=expires_at,
        )
        self._recovery_anchor = budget
        self._active_deadline_monotonic = deadline_monotonic
        renewed = dict(state)
        renewed["heartbeatAt"] = utc_iso(now)
        renewed["expiresAt"] = utc_iso(now + timedelta(seconds=ttl_seconds))
        try:
            with self._client_deadline(deadline_monotonic):
                content_sha, written_at = self.client.put_content(
                    self.repository,
                    self.branch,
                    self.path,
                    renewed,
                    expected_sha=remote.content_sha,
                    message=f"lease: heartbeat {self.resource} for {state['owner']}",
                )
        except (GitHubApiError, GitHubTransportError) as exc:
            if is_transient_error(exc) or is_rate_limit_error(exc):
                if self._now_monotonic() >= deadline_monotonic:
                    if is_rate_limit_error(exc):
                        raise
                    raise LeaseLost(
                        "dev environment lease validity budget exhausted during renewal write"
                    ) from exc
                setattr(exc, "_verified_renewal_budget", budget)
            raise
        if self._now_monotonic() >= deadline_monotonic:
            raise LeaseLost(
                "dev environment lease validity budget exhausted after renewal write"
            )
        self._recovery_anchor = None
        self._active_deadline_monotonic = None
        return renewed, content_sha, written_at

    def release(self, local_state: Mapping[str, Any]) -> dict[str, Any]:
        for _ in range(3):
            state, remote = self._matching_remote(local_state, require_active=False)
            try:
                released_at = self.client.delete_content(
                    self.repository,
                    self.branch,
                    self.path,
                    expected_sha=remote.content_sha,
                    message=f"lease: release {self.resource} for {state['owner']}",
                )
            except LeaseConflict:
                continue
            result = public_state(state, content_sha=remote.content_sha)
            result.update({"status": "released", "releasedAt": utc_iso(released_at)})
            return result
        raise LeaseLost("dev environment lease release lost repeated CAS races")


def effective_rate_limit_seconds(
    rate_limit_wait_seconds: float, initial_visibility_wait_seconds: float
) -> float:
    return max(float(rate_limit_wait_seconds), float(initial_visibility_wait_seconds))


def common_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    subparser.add_argument("--branch", default=DEFAULT_BRANCH)
    subparser.add_argument("--path", default=DEFAULT_PATH)
    subparser.add_argument("--resource", default=DEFAULT_RESOURCE)
    subparser.add_argument("--api-url", default=DEFAULT_API_URL)
    subparser.add_argument("--json-out", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap", help="create the coordination branch once")
    common_parser(bootstrap)
    bootstrap.add_argument(
        "--base-branch",
        default="",
        help="branch to bootstrap from (default: repository default branch)",
    )

    wait_quota = subparsers.add_parser(
        "wait-for-quota",
        help="wait until GitHub core API quota remaining meets the minimum threshold",
    )
    common_parser(wait_quota)
    wait_quota.add_argument(
        "--min-remaining",
        type=int,
        default=None,
        help=(
            f"minimum remaining core quota before continuing "
            f"(default: ${MIN_CORE_QUOTA_ENV} or {DEFAULT_MIN_CORE_QUOTA_REMAINING})"
        ),
    )

    acquire = subparsers.add_parser("acquire", help="atomically acquire the environment lease")
    common_parser(acquire)
    acquire.add_argument("--mode", choices=sorted(VALID_MODES), required=True)
    acquire.add_argument("--owner", required=True)
    acquire.add_argument("--ttl-seconds", type=int, default=300)
    acquire.add_argument("--wait-seconds", type=int, default=7200)
    acquire.add_argument("--poll-seconds", type=float, default=5.0)
    acquire.add_argument("--state-file", required=True)
    acquire.add_argument("--expected-backend-sha", default="")
    acquire.add_argument("--run-url", default="")
    acquire.add_argument("--predecessor-failure-file", default="")
    acquire.add_argument("--min-remaining-quota", type=int, default=0)

    verify = subparsers.add_parser("verify", help="verify current ownership and freshness")
    common_parser(verify)
    verify.add_argument("--state-file", required=True)
    verify.add_argument("--max-heartbeat-age-seconds", type=int, default=120)
    verify.add_argument("--initial-visibility-wait-seconds", type=float, default=0.0)
    verify.add_argument("--initial-visibility-poll-seconds", type=float, default=0.5)
    verify.add_argument(
        "--rate-limit-wait-seconds",
        type=float,
        default=None,
        help="maximum seconds to wait for a GitHub rate-limit reset during verification",
    )
    verify.add_argument("--ttl-seconds", type=int, default=300)

    heartbeat = subparsers.add_parser(
        "heartbeat-loop", help="renew the lease until signalled; fail closed on ownership loss"
    )
    common_parser(heartbeat)
    heartbeat.add_argument("--state-file", required=True)
    heartbeat.add_argument("--ttl-seconds", type=int, default=300)
    heartbeat.add_argument("--interval-seconds", type=float, default=60.0)
    heartbeat.add_argument(
        "--max-heartbeat-age-seconds",
        type=int,
        default=DEFAULT_MAX_HEARTBEAT_AGE_SECONDS,
    )
    heartbeat.add_argument("--failure-json-out", default="")
    heartbeat.add_argument("--shutdown-json-out", default="")
    heartbeat.add_argument("--identity-json-out", default="")
    heartbeat.add_argument(
        "--token-stdin",
        action="store_true",
        help=(
            f"read the token from stdin; mutually exclusive with {TOKEN_ENV} "
            "so the heartbeat process environment and argv stay token-free"
        ),
    )
    heartbeat.add_argument("--parent-pid", type=int, default=0)

    identity = subparsers.add_parser(
        "verify-heartbeat-identity",
        help="verify heartbeat PID, start ticks, cmdline, pinned CLI, and state file",
    )
    identity.add_argument("--identity-file", required=True)
    identity.add_argument("--pid", type=int, required=True)
    identity.add_argument("--expected-cli", required=True)
    identity.add_argument("--state-file", required=True)
    identity.add_argument("--json-out", default="")

    release = subparsers.add_parser("release", help="release only the caller's exact lease")
    common_parser(release)
    release.add_argument("--state-file", required=True)
    return parser


def manager_from_args(args: argparse.Namespace) -> LeaseManager:
    token_from_environment = os.environ.pop(TOKEN_ENV, "")
    if getattr(args, "token_stdin", False):
        if token_from_environment:
            raise LeaseError(f"--token-stdin and {TOKEN_ENV} are mutually exclusive")
        token = read_token_from_stdin()
    else:
        token = token_from_environment
    client = GitHubClient(token, args.api_url)
    return LeaseManager(
        client,
        repository=args.repository,
        branch=args.branch,
        path=args.path,
        resource=args.resource,
    )


def emit(payload: Mapping[str, Any], json_out: str = "") -> None:
    if json_out:
        atomic_write_json(json_out, payload, 0o644)
    print(json.dumps(payload, indent=2, sort_keys=True))


def _retry_sleep_seconds(
    interval_seconds: float, remaining_seconds: float
) -> float | None:
    if remaining_seconds <= MIN_RENEWAL_BUDGET_SECONDS:
        return None
    if remaining_seconds > HEARTBEAT_RETRY_SLEEP_SECONDS + MIN_RENEWAL_BUDGET_SECONDS:
        return min(HEARTBEAT_RETRY_SLEEP_SECONDS, interval_seconds)
    return min(interval_seconds, 1.0, remaining_seconds / 2.0)


def heartbeat_loop(args: argparse.Namespace, manager: LeaseManager) -> int:
    if args.interval_seconds <= 0 or args.interval_seconds >= args.ttl_seconds:
        raise LeaseError("interval-seconds must be positive and less than ttl-seconds")
    if args.parent_pid < 0:
        raise LeaseError("parent-pid must be non-negative")
    max_heartbeat_age_seconds = getattr(
        args, "max_heartbeat_age_seconds", DEFAULT_MAX_HEARTBEAT_AGE_SECONDS
    )
    if max_heartbeat_age_seconds <= 0:
        raise LeaseError("max-heartbeat-age-seconds must be positive")
    manager.max_heartbeat_age_seconds = min(
        int(max_heartbeat_age_seconds), DEFAULT_MAX_HEARTBEAT_AGE_SECONDS
    )
    stop = False

    def handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    if args.identity_json_out:
        atomic_write_json(
            args.identity_json_out,
            heartbeat_identity_payload(
                os.getpid(),
                expected_cli=__file__,
                state_file=args.state_file,
            ),
            0o644,
        )
    sleep_seconds = args.interval_seconds
    while not stop:
        slept = 0.0
        while not stop and slept < sleep_seconds:
            interval = min(0.5, sleep_seconds - slept)
            manager._sleep(interval)
            slept += interval
        if stop:
            break
        try:
            local = read_json_file(args.state_file, "lease state file")
            state, content_sha, _ = manager.heartbeat(
                local, ttl_seconds=args.ttl_seconds
            )
            atomic_write_json(
                args.state_file, public_state(state, content_sha=content_sha), 0o600
            )
            sleep_seconds = args.interval_seconds
        except Exception as exc:
            # A transient PUT failure after a verified fresh authoritative
            # same-owner GET may recover while conservative validity remains
            # within min(remote expiresAt, remote heartbeatAt + freshness<=120s).
            # On rate_limited, heartbeat never certifies unverified freshness;
            # it may retry only when the rate-limit wait fits within the
            # remaining verified freshness budget, and otherwise reports lost
            # with reason="rate_limited" so compensation knows to wait for reset.
            budget = getattr(exc, "_verified_renewal_budget", None)
            if is_rate_limit_error(exc) and isinstance(budget, VerifiedRenewalBudget):
                assert isinstance(exc, GitHubRateLimitError)
                remaining = budget.deadline_monotonic - manager._now_monotonic()
                rate_limit_wait, reset_at_opt = manager._rate_limit_wait_seconds(exc)
                if rate_limit_wait > 0 and rate_limit_wait + MIN_RENEWAL_BUDGET_SECONDS < remaining:
                    manager._active_deadline_monotonic = budget.deadline_monotonic
                    sleep_seconds = rate_limit_wait
                    print(
                        json.dumps(
                            {
                                "schemaVersion": SCHEMA_VERSION,
                                "status": "warning",
                                "reason": RATE_LIMITED_REASON,
                                "resource": manager.resource,
                                "detectedAt": utc_iso(datetime.now(timezone.utc)),
                                "rateLimitResetAt": utc_iso(reset_at_opt) if reset_at_opt else None,
                                "waitSeconds": round(rate_limit_wait, 3),
                                "warning": f"rate_limited heartbeat failure within verified freshness (retrying): {exc}",
                            },
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                    )
                    continue
            elif is_transient_error(exc) and isinstance(budget, VerifiedRenewalBudget):
                remaining = budget.deadline_monotonic - manager._now_monotonic()
                retry_sleep = _retry_sleep_seconds(args.interval_seconds, remaining)
                if retry_sleep is not None:
                    manager._active_deadline_monotonic = budget.deadline_monotonic
                    sleep_seconds = retry_sleep
                    print(
                        json.dumps(
                            {
                                "schemaVersion": SCHEMA_VERSION,
                                "status": "warning",
                                "resource": manager.resource,
                                "detectedAt": utc_iso(datetime.now(timezone.utc)),
                                "warning": f"transient heartbeat failure (retrying): {exc}",
                            },
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                    )
                    continue
            failure: dict[str, Any] = {
                "schemaVersion": SCHEMA_VERSION,
                "status": "lost",
                "resource": manager.resource,
                "repository": manager.repository,
                "branch": manager.branch,
                "path": manager.path,
                "detectedAt": utc_iso(datetime.now(timezone.utc)),
                "error": str(exc),
            }
            if isinstance(exc, GitHubRateLimitError):
                _, reset_at_opt = manager._rate_limit_wait_seconds(exc)
                failure["reason"] = RATE_LIMITED_REASON
                failure["rateLimitKind"] = exc.rate_limit_kind
                if reset_at_opt is not None:
                    failure["rateLimitResetAt"] = utc_iso(reset_at_opt)
                if exc.retry_after_seconds is not None:
                    failure["retryAfterSeconds"] = exc.retry_after_seconds
            if args.failure_json_out:
                atomic_write_json(args.failure_json_out, failure, 0o644)
            print(json.dumps(failure, sort_keys=True), file=sys.stderr)
            if args.parent_pid:
                try:
                    os.kill(args.parent_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            return 75
    stopped = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "stopped",
        "resource": manager.resource,
        "repository": manager.repository,
        "branch": manager.branch,
        "path": manager.path,
        "heartbeatPid": os.getpid(),
        "recordedAt": utc_iso(datetime.now(timezone.utc)),
    }
    if args.shutdown_json_out:
        atomic_write_json(args.shutdown_json_out, stopped, 0o644)
    print(json.dumps(stopped, sort_keys=True))
    return 0


def _resolve_min_remaining_quota(cli_value: int | None) -> int:
    if cli_value is not None:
        return int(cli_value)
    raw = os.environ.get(MIN_CORE_QUOTA_ENV, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError as exc:
            raise LeaseError(f"{MIN_CORE_QUOTA_ENV} must be an integer") from exc
    return DEFAULT_MIN_CORE_QUOTA_REMAINING


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "verify-heartbeat-identity":
        identity = read_json_file(args.identity_file, "heartbeat identity file")
        emit(
            verify_heartbeat_identity(
                identity,
                pid=args.pid,
                expected_cli=args.expected_cli,
                state_file=args.state_file,
            ),
            args.json_out,
        )
        return 0
    manager = manager_from_args(args)
    if args.command == "bootstrap":
        emit(manager.bootstrap(args.base_branch), args.json_out)
        return 0
    if args.command == "wait-for-quota":
        min_remaining = _resolve_min_remaining_quota(args.min_remaining)
        emit(
            manager.wait_for_core_quota(min_remaining=min_remaining),
            args.json_out,
        )
        return 0
    if args.command == "acquire":
        state, content_sha, written_at = manager.acquire(
            mode=args.mode,
            owner=args.owner,
            ttl_seconds=args.ttl_seconds,
            wait_seconds=args.wait_seconds,
            poll_seconds=args.poll_seconds,
            expected_backend_sha=args.expected_backend_sha,
            run_url=args.run_url,
            predecessor_failure_file=args.predecessor_failure_file,
            min_remaining_quota=args.min_remaining_quota,
        )
        local = public_state(state, content_sha=content_sha)
        atomic_write_json(args.state_file, local, 0o600)
        result = dict(local)
        result.update({"status": "acquired", "writtenAt": utc_iso(written_at)})
        emit(result, args.json_out)
        return 0
    if args.command == "verify":
        local = read_json_file(args.state_file, "lease state file")
        emit(
            manager.verify(
                local,
                max_heartbeat_age_seconds=args.max_heartbeat_age_seconds,
                initial_visibility_wait_seconds=args.initial_visibility_wait_seconds,
                initial_visibility_poll_seconds=args.initial_visibility_poll_seconds,
                rate_limit_wait_seconds=args.rate_limit_wait_seconds,
                ttl_seconds=args.ttl_seconds,
                state_file=args.state_file,
            ),
            args.json_out,
        )
        return 0
    if args.command == "heartbeat-loop":
        return heartbeat_loop(args, manager)
    if args.command == "release":
        local = read_json_file(args.state_file, "lease state file")
        emit(manager.release(local), args.json_out)
        return 0
    raise LeaseError(f"unsupported command: {args.command}")


def main() -> None:
    try:
        raise SystemExit(run())
    except RateLimitedRollbackPending as exc:
        print(
            f"environment lease {RATE_LIMITED_ROLLBACK_PENDING_REASON}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(RATE_LIMITED_ROLLBACK_PENDING_EXIT_CODE) from exc
    except LeaseBusy as exc:
        print(f"environment lease busy: {exc}", file=sys.stderr)
        raise SystemExit(75) from exc
    except LeaseLost as exc:
        print(f"environment lease lost: {exc}", file=sys.stderr)
        raise SystemExit(75) from exc
    except LeaseError as exc:
        print(f"environment lease failed: {exc}", file=sys.stderr)
        raise SystemExit(78) from exc


if __name__ == "__main__":
    main()
