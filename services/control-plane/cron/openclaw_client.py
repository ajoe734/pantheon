from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from typing import Any, Callable

try:
    from .models import OpenClawRuntimePin, WorkflowDefinition, utc_now
except ImportError:  # pragma: no cover - exercised when loaded as a bare top-level
    # module (some test harness configurations add this directory itself to
    # sys.path rather than its parent, so there is no enclosing package for a
    # relative import to resolve against).
    from models import OpenClawRuntimePin, WorkflowDefinition, utc_now


Transport = Callable[[dict[str, Any]], dict[str, Any]]

_DEFAULT_CRON_POLL_TIMEOUT = 30.0
_DEFAULT_CRON_POLL_INTERVAL = 1.0
_TERMINAL_RUN_STATUSES = frozenset({"ok", "failed", "cancelled", "canceled", "error", "timed_out"})


def _clean(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


class _CliGatewayTransport:
    """Dispatch cron workflow envelopes via the installed `openclaw` CLI.

    This transport is used as the default when `OPENCLAW_GATEWAY_URL` and
    `OPENCLAW_GATEWAY_TOKEN` are set but no explicit transport was supplied.
    It calls `openclaw gateway call <method> ...` directly — no docker exec
    required — making it suitable for compose environments where the binary
    is installed inside the adapter/cron image.
    """

    def __init__(
        self,
        gateway_url: str,
        token: str,
        *,
        openclaw_bin: str = "openclaw",
        poll_timeout_seconds: float = _DEFAULT_CRON_POLL_TIMEOUT,
        poll_interval_seconds: float = _DEFAULT_CRON_POLL_INTERVAL,
        _run_func=None,
        _which_func=None,
    ) -> None:
        self._url = gateway_url
        self._token = token
        self._bin = openclaw_bin
        self._poll_timeout = poll_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._run = _run_func or subprocess.run
        self._which = _which_func or shutil.which

    def _resolve_bin(self) -> str:
        explicit = os.getenv("OPENCLAW_BIN", "").strip()
        if explicit:
            return explicit
        found = self._which(self._bin)
        if not found:
            raise RuntimeError(
                "openclaw binary not found. Ensure it is installed in the image "
                "(OPENCLAW_BIN env override is also supported)."
            )
        return found

    def _call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        bin_path = self._resolve_bin()
        cmd = [
            bin_path,
            "gateway",
            "call",
            method,
            "--url", self._url,
            "--token", self._token,
            "--json",
        ]
        if params is not None:
            cmd.extend(["--params", json.dumps(params, separators=(",", ":"), sort_keys=True)])
        env = {**os.environ, "NO_COLOR": "1", "OPENCLAW_HIDE_BANNER": "1", "OPENCLAW_SUPPRESS_NOTES": "1"}
        budget = 30.0 if timeout_seconds is None else max(0.001, float(timeout_seconds))
        result = self._run(cmd, capture_output=True, text=True, env=env, timeout=budget)
        if result.returncode != 0:
            raise RuntimeError(
                f"openclaw gateway call {method} failed (exit {result.returncode}): "
                f"{(result.stderr or '').strip()[:300]}"
            )
        return json.loads(result.stdout.strip() or "{}")

    def _wait_for_terminal_run(
        self, job_id: str, run_id: str, deadline: float | None = None
    ) -> dict[str, Any]:
        """Poll `cron.runs` until the EXACT dispatched run reaches a terminal
        status, or the bounded deadline elapses.

        `deadline` is an absolute `time.monotonic()` value shared with the
        caller's earlier `cron.add`/`cron.run` calls (see `__call__`) so the
        whole dispatch — add, run, and poll — spends at most one total
        budget, never `poll_timeout_seconds` freshly on top of whatever
        `cron.add`/`cron.run` already spent. When called directly (as the
        tests below do), `deadline` defaults to a fresh
        `now + poll_timeout_seconds` window, preserving the previous
        poll-only-budget behavior for standalone callers.

        Correlation is by exact `(job_id, run_id)` match only. There is no
        `entries[0]` "most recent run" fallback here: a job can have multiple
        interleaved runs, and returning the wrong one would silently report an
        unrelated run's outcome as if it were this dispatch's result (a
        crossed-run false-positive/false-negative). If the target run has not
        yet appeared in the polled window, that is treated as "not found yet"
        (continue polling) — not as failure and not as success — and the
        caller only ever sees a real terminal status for `run_id` itself, or
        the timeout below (unknown outcome).

        The pinned Gateway's `cron.runs` supports an exact `{id, runId}`
        lookup ahead of pagination (see upstream `server-methods/cron.ts` /
        `cron/run-log.ts`), so the request always asks for our exact run
        rather than an arbitrary `limit`-bounded page that a sufficiently
        busy job could push our target run behind (a fixed `limit` falsely
        times out once enough newer runs exist). `entries` is still filtered
        client-side for the exact `run_id`, both as defense-in-depth against
        a Gateway build that ignores the `runId` filter and returns the
        unfiltered page, and so existing test doubles that stub the RPC
        without honoring `runId` remain correct.

        The RPC call and the inter-poll sleep share ONE total deadline: each
        `_call` is given only the time remaining, and a result that only
        arrives after the deadline has already passed is treated as unknown
        (timeout), never accepted as a late success — a slow/hung RPC must
        not silently extend the caller's bounded budget.
        """
        if deadline is None:
            deadline = time.monotonic() + self._poll_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            runs = self._call(
                "cron.runs", {"id": job_id, "runId": run_id}, timeout_seconds=remaining
            )
            if time.monotonic() >= deadline:
                # The RPC itself consumed the whole remaining budget (or ran
                # past it) — reject a late result rather than trusting it.
                break
            entries = runs.get("entries") or []
            target = next((e for e in entries if e.get("runId") == run_id), None)
            if target and target.get("status") in _TERMINAL_RUN_STATUSES:
                return target
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self._poll_interval, remaining))
        raise RuntimeError(
            f"Timed out waiting for openclaw cron job {job_id} run {run_id} to reach a terminal state."
        )

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        import re
        from datetime import datetime, timedelta, timezone

        # ONE deadline governs the entire dispatch — `cron.add`, `cron.run`,
        # and the terminal-run poll — instead of each phase getting its own
        # fresh budget on top of whatever the previous phase already spent
        # (previously: up to 30s for `cron.add` + 30s for `cron.run` + a
        # freshly-started `poll_timeout_seconds` for polling, regardless of
        # how little of the caller's actual budget remained).
        deadline = time.monotonic() + self._poll_timeout

        def _remaining(phase: str, *, prior_error: str | None = None) -> float:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"openclaw cron dispatch exhausted its bounded deadline before {phase}"
                    + (f"; {prior_error}" if prior_error else "")
                )
            return remaining

        workflow_id = request["workflow"]["workflow_id"]
        slug = re.sub(r"[^a-z0-9]+", "-", workflow_id.lower()).strip("-")
        suffix = request["request_id"].split("-", 1)[-1][:12]
        job_name = f"pantheon-{slug}-{suffix}"

        prepared_at = request["prepared_at"]
        prepared = datetime.fromisoformat(prepared_at.replace("Z", "+00:00"))
        schedule_at = (
            (prepared + timedelta(minutes=10))
            .astimezone(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        event_text = json.dumps(
            {
                "kind": "pantheon.workflow.dispatch",
                "prepared_at": prepared_at,
                "request_id": request["request_id"],
                "workflow_id": workflow_id,
                "upstream_entrypoint": request["workflow"]["upstream_entrypoint"],
                "policy_id": request["governance"]["policy_id"],
            },
            separators=(",", ":"),
            sort_keys=True,
        )

        add_response = self._call(
            "cron.add",
            {
                "name": job_name,
                "enabled": True,
                "deleteAfterRun": True,
                "schedule": {"kind": "at", "at": schedule_at},
                "sessionTarget": "main",
                "wakeMode": "next-heartbeat",
                "payload": {"kind": "systemEvent", "text": event_text},
                "delivery": {"mode": "none"},
            },
            timeout_seconds=_remaining("cron.add"),
        )
        job_id = add_response.get("id")
        if not job_id:
            raise RuntimeError(f"openclaw cron.add did not return a job id: {add_response}")

        run_response = self._call(
            "cron.run",
            {"id": job_id, "mode": "force"},
            timeout_seconds=_remaining(
                "cron.run", prior_error=f"job {job_id} was accepted but not dispatched, not resubmitting"
            ),
        )
        run_id = run_response.get("runId")
        if not run_id:
            # No run id means we cannot correlate a later poll to this exact
            # dispatch. Fail fast here rather than falling back to "most
            # recent run" (which could be a different run of the same job) or
            # blindly resubmitting cron.run (which could trigger a duplicate
            # execution of an already-accepted, possibly side-effecting turn).
            raise RuntimeError(
                f"openclaw cron.run for job {job_id} did not return a run id; "
                "completion is unknown, not resubmitting."
            )
        latest_run = self._wait_for_terminal_run(job_id, run_id, deadline)

        if latest_run.get("status") != "ok":
            raise RuntimeError(
                f"openclaw cron job {job_id} finished with status {latest_run.get('status')!r}: "
                f"{latest_run}"
            )

        return {
            "status": "accepted",
            "mode": "cli_gateway_rpc",
            "workflow_id": workflow_id,
            "request_id": request["request_id"],
            "runtime_pin": request["runtime"],
            "job": {"id": job_id, "name": job_name, "run_id": run_id},
            "run": latest_run,
        }


def _build_default_cli_transport() -> Transport | None:
    """Return a CLI-based transport when gateway env vars are present; None otherwise."""
    url = (os.environ.get("OPENCLAW_BASE_URL") or os.environ.get("OPENCLAW_GATEWAY_URL", "")).strip()
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip()
    if url and token:
        bin_path = os.getenv("OPENCLAW_BIN", "").strip() or shutil.which("openclaw")
        if bin_path:
            return _CliGatewayTransport(gateway_url=url, token=token)
    return None


class OpenClawCronClient:
    """Prepare versioned cron workflow dispatch envelopes for the upstream runtime."""

    def __init__(
        self,
        runtime_pin: OpenClawRuntimePin | None = None,
        base_url: str | None = None,
        transport: Transport | None = None,
    ):
        self.runtime_pin = runtime_pin or OpenClawRuntimePin(
            release_tag=os.environ.get("OPENCLAW_RELEASE_TAG", ""),
            commit_sha=os.environ.get("OPENCLAW_COMMIT_SHA", ""),
            image_ref=os.environ.get("OPENCLAW_IMAGE_REF", ""),
        )
        self.base_url = (
            base_url
            or os.environ.get("OPENCLAW_BASE_URL")
            or os.environ.get("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789")
        )
        # If no explicit transport is provided, auto-detect from env.
        # This ensures `--dispatch` actually reaches the gateway when the
        # CLI binary and token are available, instead of returning local_only.
        self.transport = transport if transport is not None else _build_default_cli_transport()
        self._adapter_boundary = {
            "integration_boundary": "pantheon-openclaw-gateway-adapter",
            "workspace_ref": _clean(os.environ.get("PANTHEON_WORKSPACE_REF")),
            "auth_profile_ref": _clean(os.environ.get("PANTHEON_AUTH_PROFILE_REF")),
            "persona_id": _clean(os.environ.get("PANTHEON_PERSONA_ID")),
            "session_id": _clean(os.environ.get("PANTHEON_SESSION_ID")),
            "trace_id": _clean(os.environ.get("PANTHEON_TRACE_ID")),
            "request_id": _clean(os.environ.get("PANTHEON_REQUEST_ID")),
            "credential_sharing": "disallowed",
            "filesystem_scope": "persona_workspace_only",
        }

    def prepare_dispatch(self, workflow: WorkflowDefinition, payload: dict[str, Any]) -> dict[str, Any]:
        missing = [key for key in workflow.required_payload_keys if key not in payload]
        if missing:
            raise ValueError(f"{workflow.workflow_id} missing required payload keys: {missing}")

        return {
            "request_id": f"{workflow.workflow_id}-{uuid.uuid4()}",
            "prepared_at": utc_now(),
            "runtime": {
                **self.runtime_pin.to_dict(),
                "base_url": self.base_url,
            },
            "pantheon_adapter": {
                key: value
                for key, value in self._adapter_boundary.items()
                if value is not None
            },
            "workflow": {
                "workflow_id": workflow.workflow_id,
                "upstream_entrypoint": workflow.upstream_entrypoint,
                "schedule": workflow.schedule,
                "description": workflow.description,
            },
            "governance": {
                "policy_id": workflow.policy_id,
                "execution_context": workflow.execution_context,
                "allowed_tool_classes": list(workflow.allowed_tool_classes),
                "approval_required": workflow.approval_required,
                "artifact_type": workflow.artifact_type,
                "initial_lifecycle_state": workflow.initial_lifecycle_state,
                "uses_promotion_gate": workflow.uses_promotion_gate,
            },
            "payload": payload,
        }

    def dispatch_prepared(self, workflow_id: str, request: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        if dry_run or self.transport is None:
            return {
                "status": "prepared",
                "mode": "dry_run" if dry_run else "local_only",
                "workflow_id": workflow_id,
                "request_id": request["request_id"],
            }

        response = self.transport(request)
        if not isinstance(response, dict):
            raise ValueError("OpenClaw transport must return a dictionary response")
        return response
