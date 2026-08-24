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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


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


class GitHubApiError(LeaseError):
    def __init__(self, status: int, message: str, body: str = "") -> None:
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status
        self.body = body


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: Mapping[str, str]
    payload: Any


@dataclass(frozen=True)
class RemoteContent:
    state: dict[str, Any]
    content_sha: str
    server_now: datetime


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

    def _request(
        self,
        method: str,
        route: str,
        *,
        body: Mapping[str, Any] | None = None,
        allowed: Sequence[int] = (200,),
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
            with urllib.request.urlopen(request, timeout=30) as response:
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
        except urllib.error.URLError as exc:
            raise LeaseError(f"GitHub API request failed: {method} {route}: {exc}") from exc
        if result.status not in allowed:
            message = "request failed"
            if isinstance(result.payload, dict) and result.payload.get("message"):
                message = str(result.payload["message"])
            raise GitHubApiError(result.status, message, json.dumps(result.payload))
        return result

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
        return RemoteContent(state=state, content_sha=content_sha, server_now=now)

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
        self.ensure_branch()

        deadline = time.monotonic() + wait_seconds
        last_busy: dict[str, Any] | None = None
        while True:
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
                    if time.monotonic() >= deadline:
                        raise LeaseBusy(
                            "dev environment is leased by "
                            f"{state['owner']} ({state['mode']}) until {state['expiresAt']}"
                        )
                    time.sleep(min(poll_seconds, remaining or poll_seconds))
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
                if time.monotonic() >= deadline:
                    raise LeaseBusy(
                        "dev environment lease CAS remained contended"
                        + (f"; last owner={last_busy.get('owner')}" if last_busy else "")
                    )
                time.sleep(min(poll_seconds, random.uniform(0.25, 1.0)))
                continue
            if expected_sha:
                # Local-only evidence binds a bounded initial visibility retry
                # to the exact stale blob replaced by this successful CAS.
                candidate["previousContentSha"] = expected_sha.lower()
            return candidate, content_sha, written_at

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

    def verify(
        self,
        local_state: Mapping[str, Any],
        *,
        max_heartbeat_age_seconds: int,
        initial_visibility_wait_seconds: float = 0.0,
        initial_visibility_poll_seconds: float = 0.5,
    ) -> dict[str, Any]:
        if initial_visibility_wait_seconds < 0 or initial_visibility_wait_seconds > 30:
            raise LeaseError(
                "initial-visibility-wait-seconds must be between 0 and 30"
            )
        if initial_visibility_poll_seconds <= 0 or initial_visibility_poll_seconds > 5:
            raise LeaseError(
                "initial-visibility-poll-seconds must be greater than 0 and at most 5"
            )
        deadline = time.monotonic() + initial_visibility_wait_seconds
        while True:
            try:
                state, remote = self._matching_remote(
                    local_state,
                    require_active=True,
                    allow_exact_stale_predecessor=(
                        initial_visibility_wait_seconds > 0
                    ),
                )
                break
            except InitialLeaseVisibilityPending as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LeaseLost(
                        "initial lease visibility remained on the exact stale "
                        "predecessor until the bounded timeout"
                    ) from exc
                time.sleep(min(initial_visibility_poll_seconds, remaining))
        heartbeat = parse_utc_iso(state["heartbeatAt"], "heartbeatAt")
        age = (remote.server_now - heartbeat).total_seconds()
        if max_heartbeat_age_seconds <= 0:
            raise LeaseError("max-heartbeat-age-seconds must be positive")
        if age > max_heartbeat_age_seconds:
            raise LeaseLost(
                f"dev environment lease heartbeat is stale: {age:.0f}s > "
                f"{max_heartbeat_age_seconds}s"
            )
        result = public_state(state, content_sha=remote.content_sha)
        result.update(
            {
                "status": "verified",
                "verifiedAt": utc_iso(remote.server_now),
                "heartbeatAgeSeconds": max(0, int(age)),
            }
        )
        return result

    def heartbeat(
        self, local_state: Mapping[str, Any], *, ttl_seconds: int
    ) -> tuple[dict[str, Any], str, datetime]:
        if ttl_seconds < 30 or ttl_seconds > 3600:
            raise LeaseError("ttl-seconds must be between 30 and 3600")
        state, remote = self._matching_remote(local_state, require_active=True)
        now = remote.server_now
        renewed = dict(state)
        renewed["heartbeatAt"] = utc_iso(now)
        renewed["expiresAt"] = utc_iso(now + timedelta(seconds=ttl_seconds))
        content_sha, written_at = self.client.put_content(
            self.repository,
            self.branch,
            self.path,
            renewed,
            expected_sha=remote.content_sha,
            message=f"lease: heartbeat {self.resource} for {state['owner']}",
        )
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

    verify = subparsers.add_parser("verify", help="verify current ownership and freshness")
    common_parser(verify)
    verify.add_argument("--state-file", required=True)
    verify.add_argument("--max-heartbeat-age-seconds", type=int, default=120)
    verify.add_argument("--initial-visibility-wait-seconds", type=float, default=0.0)
    verify.add_argument("--initial-visibility-poll-seconds", type=float, default=0.5)

    heartbeat = subparsers.add_parser(
        "heartbeat-loop", help="renew the lease until signalled; fail closed on ownership loss"
    )
    common_parser(heartbeat)
    heartbeat.add_argument("--state-file", required=True)
    heartbeat.add_argument("--ttl-seconds", type=int, default=300)
    heartbeat.add_argument("--interval-seconds", type=float, default=60.0)
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


def heartbeat_loop(args: argparse.Namespace, manager: LeaseManager) -> int:
    if args.interval_seconds <= 0 or args.interval_seconds >= args.ttl_seconds:
        raise LeaseError("interval-seconds must be positive and less than ttl-seconds")
    if args.parent_pid < 0:
        raise LeaseError("parent-pid must be non-negative")
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
    while not stop:
        slept = 0.0
        while not stop and slept < args.interval_seconds:
            interval = min(0.5, args.interval_seconds - slept)
            time.sleep(interval)
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
        except (LeaseLost, LeaseConflict) as exc:
            failure = {
                "schemaVersion": SCHEMA_VERSION,
                "status": "lost",
                "resource": manager.resource,
                "repository": manager.repository,
                "branch": manager.branch,
                "path": manager.path,
                "detectedAt": utc_iso(datetime.now(timezone.utc)),
                "error": str(exc),
            }
            if args.failure_json_out:
                atomic_write_json(args.failure_json_out, failure, 0o644)
            print(json.dumps(failure, sort_keys=True), file=sys.stderr)
            if args.parent_pid:
                try:
                    os.kill(args.parent_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            return 75
        except Exception as exc:
            now = datetime.now(timezone.utc)
            local_expired = True
            try:
                local = read_json_file(args.state_file, "lease state file")
                expires_at = parse_utc_iso(local.get("expiresAt"), "lease state expiresAt")
                if expires_at > now:
                    local_expired = False
            except Exception:
                pass
            if local_expired:
                failure = {
                    "schemaVersion": SCHEMA_VERSION,
                    "status": "lost",
                    "resource": manager.resource,
                    "repository": manager.repository,
                    "branch": manager.branch,
                    "path": manager.path,
                    "detectedAt": utc_iso(now),
                    "error": f"transient heartbeat error and local lease expired: {exc}",
                }
                if args.failure_json_out:
                    atomic_write_json(args.failure_json_out, failure, 0o644)
                print(json.dumps(failure, sort_keys=True), file=sys.stderr)
                if args.parent_pid:
                    try:
                        os.kill(args.parent_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                return 75
            print(
                json.dumps(
                    {
                        "schemaVersion": SCHEMA_VERSION,
                        "status": "warning",
                        "resource": manager.resource,
                        "detectedAt": utc_iso(now),
                        "warning": f"transient heartbeat failure (retrying): {exc}",
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            slept = 0.0
            retry_interval = min(5.0, args.interval_seconds)
            while not stop and slept < retry_interval:
                step = min(0.5, retry_interval - slept)
                time.sleep(step)
                slept += step
            continue
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
    if args.command == "acquire":
        state, content_sha, written_at = manager.acquire(
            mode=args.mode,
            owner=args.owner,
            ttl_seconds=args.ttl_seconds,
            wait_seconds=args.wait_seconds,
            poll_seconds=args.poll_seconds,
            expected_backend_sha=args.expected_backend_sha,
            run_url=args.run_url,
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
