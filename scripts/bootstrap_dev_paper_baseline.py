#!/usr/bin/env python3
"""Idempotently establish the governed paper baseline required by dev probes.

Run this inside the dev operator-bff container. Credentials are read from the
container environment and are never included in output. The script uses the
same public BFF contract as an operator, then waits for authoritative runtime
binding and paper-worker readback before succeeding.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_NAME = "Pantheon Dev Paper Baseline 3"
DEFAULT_IDEMPOTENCY_KEY = "dev-paper-bootstrap-20260720-operator-a-v3"

# The BFF only resolves a Persona's reconcile lifecycle to a terminal state
# (paper_running, or provisioning_failed with a named provisioning_failure_reason)
# once PANTHEON_PERSONA_PROVISIONING_TIMEOUT_SECONDS has elapsed since
# provisioning_readback_started_at (services/control-plane/bff/personas/service.py,
# the ``is_timeout`` computation). Before that, an in-flight saga legitimately
# reports lifecycle_state="provisioning" on every poll. A client-side poll
# deadline shorter than that server-side timeout can never observe a terminal
# answer: it always aborts with a content-free "timed out waiting" error,
# regardless of whether provisioning is actually broken or just still running.
# This process runs inside the same operator-bff container (docker exec) as
# the BFF it polls, so it reads the identical env var with the identical
# fallback default to stay in lock-step with the server's own timeout.
DEFAULT_SERVER_PROVISIONING_TIMEOUT_SECONDS = 600.0
SERVER_TIMEOUT_SAFETY_MARGIN_SECONDS = 30.0

# Mirrors services/control-plane/bff/personas/service.py
# _market_persona_required_data_sources(market="US") for PANTHEON_ENV=dev.
# The Persona create response is the canonical source for this (it is used
# whenever present); this is only a fallback for a response shape that omits
# requiredDataSources, so the governed source provisioning prerequisite below
# still has a requirement to reconcile against.
DEV_US_REQUIRED_DATA_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "dataset": "us_price_daily",
        "market": "US",
        "cadence": "daily",
        "source_class": "live_pull",
        "connector_candidates": ["dev-paper-us-equity-simulation"],
        "policy_gates": [
            "require_connector_approved",
            "require_schedule_active",
            "require_source_health_ok",
        ],
    },
)


class BootstrapError(RuntimeError):
    """Raised when the dev paper baseline cannot safely converge."""


def _bool_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def assert_dev_paper_boundary() -> None:
    environment = os.getenv("PANTHEON_ENV", "").strip().lower()
    auth_mode = os.getenv("PANTHEON_BFF_AUTH_MODE", "").strip().lower()
    if environment != "dev":
        raise BootstrapError("dev paper bootstrap requires PANTHEON_ENV=dev")
    # Development functional closure runs with the permissive auth stub.  The
    # bootstrap still obtains its token through the public dev-login contract,
    # so it must accept both supported dev auth modes instead of turning an
    # authentication posture into a paper-lifecycle blocker.
    if auth_mode not in {"strict", "permissive"}:
        raise BootstrapError(
            "dev paper bootstrap requires a supported BFF auth mode"
        )
    if _bool_env("PANTHEON_LIVE_BROKER_ENABLED"):
        raise BootstrapError("dev paper bootstrap refuses to run with live broker enabled")
    if _bool_env("PANTHEON_CANARY_EXECUTION_ENABLED"):
        raise BootstrapError(
            "dev paper bootstrap refuses to run with canary execution enabled"
        )


def _login_credential_pair() -> tuple[str, str, str]:
    profiles = (
        (
            "PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID",
            "PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET",
            "operator_a",
        ),
        (
            "PANTHEON_BFF_OIDC_CLIENT_ID",
            "PANTHEON_BFF_OIDC_CLIENT_SECRET",
            "operator",
        ),
        (
            "PANTHEON_BFF_DEV_LOGIN_CLIENT_ID",
            "PANTHEON_BFF_DEV_LOGIN_CLIENT_SECRET",
            "operator",
        ),
    )
    for client_id_name, client_secret_name, identity in profiles:
        client_id = os.getenv(client_id_name, "").strip()
        client_secret = os.getenv(client_secret_name, "").strip()
        if client_id or client_secret:
            if not (client_id and client_secret):
                raise BootstrapError(
                    f"required credential pair {client_id_name}/{client_secret_name} is incomplete"
                )
            return client_id, client_secret, identity
    raise BootstrapError("no dev-login operator credential pair is configured")


def _post_json(
    url: str,
    payload: Mapping[str, Any] | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 30,
) -> tuple[int, dict[str, Any]]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **dict(headers or {}),
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(dict(payload or {}), separators=(",", ":")).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = json.loads(raw.decode("utf-8")) if raw else {}
        return int(exc.code), body if isinstance(body, dict) else {}
    with response:
        raw = response.read()
        body = json.loads(raw.decode("utf-8")) if raw else {}
        return int(response.status), body if isinstance(body, dict) else {}


def _get_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 30,
) -> tuple[int, dict[str, Any]]:
    request_headers = {
        "Accept": "application/json",
        **dict(headers or {}),
    }
    request = urllib.request.Request(
        url,
        headers=request_headers,
        method="GET",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = json.loads(raw.decode("utf-8")) if raw else {}
        return int(exc.code), body if isinstance(body, dict) else {}
    with response:
        raw = response.read()
        body = json.loads(raw.decode("utf-8")) if raw else {}
        return int(response.status), body if isinstance(body, dict) else {}


def ensure_dev_market_snapshot_ready(
    *,
    source_ingest_url: str,
    symbol: str = "SPY",
    timeout_seconds: float = 60.0,
    poll_seconds: float = 2.0,
    request_timeout_seconds: float = 10.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait until an admissible, fresh market snapshot is available for symbol.

    On a fresh host that has never ingested the dev synthetic connector, this
    waits for the snapshot to appear and be admissible before paper baseline
    creation proceeds.
    The wait is strictly bounded. If timeout expires, it raises BootstrapError
    explicitly naming the missing market snapshot and reason rather than a
    generic readback failure.
    """
    deadline = monotonic() + timeout_seconds
    snapshot_url = (
        f"{source_ingest_url.rstrip('/')}/api/source-ingest/snapshots/latest"
        f"?symbol={urllib.parse.quote(symbol, safe='')}"
    )
    last_reason = "market_snapshot_not_found"
    last_detail = f"snapshot for {symbol} was not found"

    while True:
        status, body = _get_json(snapshot_url, timeout_seconds=request_timeout_seconds)
        if status == 200 and isinstance(body, dict):
            closes = body.get("closes")
            if closes and isinstance(closes, Sequence) and not isinstance(closes, (str, bytes)) and len(closes) >= 2:
                ev_str = str(body.get("event_time") or "")
                is_fresh = True
                if ev_str:
                    try:
                        ev_dt = datetime.fromisoformat(ev_str.replace("Z", "+00:00"))
                        now_dt = datetime.now(timezone.utc)
                        age_sec = (now_dt - ev_dt).total_seconds()
                        if age_sec > 86400:
                            is_fresh = False
                            last_reason = "market_input_stale"
                            last_detail = f"snapshot event_time {ev_str} is stale ({age_sec:.1f}s > 86400s)"
                        elif age_sec < 0:
                            is_fresh = False
                            last_reason = "market_input_invalid"
                            last_detail = f"snapshot event_time {ev_str} is in the future"
                    except Exception as exc:
                        is_fresh = False
                        last_reason = "market_input_invalid"
                        last_detail = f"invalid event_time {ev_str}: {exc}"
                if is_fresh:
                    return body
            else:
                last_reason = "market_input_insufficient"
                count = len(closes) if isinstance(closes, Sequence) and not isinstance(closes, (str, bytes)) else 0
                last_detail = f"snapshot has {count} closes, requires >= 2"
        elif status == 404:
            last_reason = "market_snapshot_not_found"
            last_detail = f"HTTP 404: snapshot for symbol {symbol!r} not found in source-ingest"
            try:
                _post_json(
                    f"{source_ingest_url.rstrip('/')}/api/source-ingest/run-scheduled",
                    {"max_concurrency": 1},
                    timeout_seconds=request_timeout_seconds,
                )
            except Exception:
                pass
        else:
            last_reason = f"http_{status}"
            last_detail = f"source-ingest responded with HTTP {status}: {body}"

        if monotonic() >= deadline:
            raise BootstrapError(
                f"timed out waiting for admissible market snapshot for symbol {symbol!r}: "
                f"{last_reason} ({last_detail})"
            )

        sleep(poll_seconds)


def source_ingest_controller_token(environ: Mapping[str, str] | None = None) -> str:
    """Read the source-ingest controller's own bearer token.

    Mirrors services/source_ingestion/controller_auth.load_controller_token's
    explicit-value-then-file precedence, read-only: this process never
    creates the token file, it only reads the token the source-ingest
    service itself already created at startup (runtime.py,
    ``load_controller_token(..., create=True)``).
    """

    env = environ if environ is not None else os.environ
    explicit = str(env.get("SOURCE_INGEST_CONTROLLER_TOKEN") or "").strip()
    if explicit:
        return explicit
    token_file = str(env.get("SOURCE_INGEST_CONTROLLER_TOKEN_FILE") or "").strip()
    if not token_file:
        return ""
    try:
        return Path(token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_source_provisioning(
    *,
    source_ingest_url: str,
    persona_id: str,
    required_data_sources: Sequence[Mapping[str, Any]],
    controller_token: str,
    request_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Provision the Persona's declared data-source connector/schedule now.

    The BFF's async provisioning reconciler
    (PANTHEON_PERSONA_PROVISIONING_RECONCILE_SECONDS) only evaluates
    lifecycle readbacks -- it never provisions source connectors. The
    source-ingest controller's own scheduler tick instead reads a static
    desired-state file or URL (SOURCE_INGEST_DESIRED_STATE_PATH /
    SOURCE_INGEST_DESIRED_STATE_URL) that has no knowledge of a Persona
    created after that file was written. On a fresh host neither path ever
    registers the dev synthetic connector (dev-paper-us-equity-simulation),
    so its snapshot can never appear -- see
    DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001. This calls source-ingest's
    own authoritative persona-source-provisioning/reconcile endpoint
    directly (the same governed API the desired-state controller itself
    uses) so a first deploy converges without waiting on that external tick.
    """

    if not required_data_sources:
        return {"status": "skipped", "reason": "no_required_data_sources"}
    if not controller_token:
        raise BootstrapError(
            "governed source provisioning prerequisite requires a "
            "source-ingest controller token (SOURCE_INGEST_CONTROLLER_TOKEN "
            "or SOURCE_INGEST_CONTROLLER_TOKEN_FILE) but none is configured"
        )
    persona_payload = {
        "persona_id": persona_id,
        "lifecycle_state": "provisioning",
        "required_data_sources": list(required_data_sources),
    }
    status, body = _post_json(
        f"{source_ingest_url.rstrip('/')}/api/source-ingest/persona-source-provisioning/reconcile",
        {"persona": persona_payload, "dry_run": False},
        headers={"Authorization": f"Bearer {controller_token}"},
        timeout_seconds=request_timeout_seconds,
    )
    if status != 200:
        raise BootstrapError(
            "governed source provisioning prerequisite failed: "
            + json.dumps({"http_status": status, "body": body}, sort_keys=True)
        )
    summary = body.get("summary") if isinstance(body.get("summary"), Mapping) else {}
    if summary.get("unsupported") or summary.get("conflicts"):
        raise BootstrapError(
            "governed source provisioning prerequisite reported an "
            "unsupported or conflicting requirement: "
            + json.dumps(summary, sort_keys=True)
        )
    return body


def _login(base_url: str, *, request_timeout_seconds: float) -> str:
    client_id, client_secret, expected_identity = _login_credential_pair()
    status, body = _post_json(
        f"{base_url.rstrip('/')}/bff/auth/dev-login",
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout_seconds=request_timeout_seconds,
    )
    token = str(body.get("access_token") or "").strip()
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    if status != 200 or not token or meta.get("identity") != expected_identity:
        raise BootstrapError(
            "strict dev-login failed "
            f"(HTTP {status}, identity={meta.get('identity')!r}, expected={expected_identity!r})"
        )
    return token


def _failure_summary(status: int, body: Mapping[str, Any]) -> str:
    error = body.get("error") if isinstance(body.get("error"), Mapping) else {}
    details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
    fields = {
        "http_status": status,
        "code": error.get("code"),
        "message": error.get("message"),
        "precondition_failed": details.get("precondition_failed"),
        "reason": details.get("reason"),
        "suggestion": details.get("suggestion"),
    }
    return json.dumps(fields, sort_keys=True)


def server_authoritative_timeout_seconds(environ: Mapping[str, str] | None = None) -> float:
    """Read the BFF's own provisioning timeout from the shared container env.

    Falls back to the BFF's hardcoded default (600s) on an unset or malformed
    value, exactly mirroring ``os.getenv(..., "600")`` in
    ``services/control-plane/bff/personas/service.py`` so both processes
    agree without needing a compose-file change.
    """

    source = environ if environ is not None else os.environ
    raw = source.get("PANTHEON_PERSONA_PROVISIONING_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_SERVER_PROVISIONING_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SERVER_PROVISIONING_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_SERVER_PROVISIONING_TIMEOUT_SECONDS


def effective_poll_timeout_seconds(
    requested_timeout_seconds: float,
    *,
    environ: Mapping[str, str] | None = None,
) -> float:
    """Never poll for less than the server needs to reach a terminal state.

    A caller-requested timeout shorter than the server's own authoritative
    timeout would abort before the BFF can ever report success or a named
    failure reason, so the effective deadline is widened (never shortened)
    to cover it plus a fixed safety margin for the terminal reconcile poll
    itself.
    """

    return max(
        requested_timeout_seconds,
        server_authoritative_timeout_seconds(environ) + SERVER_TIMEOUT_SAFETY_MARGIN_SECONDS,
    )


def _timeout_detail(
    *,
    attempts: int,
    persona_id: str,
    state: str,
    provisioning_state: str,
    requested_timeout_seconds: float,
    effective_timeout_seconds: float,
    last_reconcile_meta: Mapping[str, Any] | None,
) -> str:
    detail: dict[str, Any] = {
        "attempts": attempts,
        "persona_id": persona_id,
        "state": state,
        "provisioning_state": provisioning_state,
        "requested_timeout_seconds": requested_timeout_seconds,
        "effective_timeout_seconds": effective_timeout_seconds,
    }
    if last_reconcile_meta:
        detail["last_reconcile_provisioning_failure_reason"] = last_reconcile_meta.get(
            "provisioning_failure_reason"
        )
        detail["last_reconcile_lifecycle_state"] = last_reconcile_meta.get("lifecycle_state")
        detail["last_reconcile_status"] = last_reconcile_meta.get("status")
        detail["last_reconcile_degraded_dependencies"] = last_reconcile_meta.get(
            "degraded_dependencies"
        )
    return json.dumps(detail, sort_keys=True)


def ensure_paper_baseline(
    *,
    base_url: str,
    name: str,
    idempotency_key: str,
    timeout_seconds: float,
    poll_seconds: float,
    request_timeout_seconds: float,
    source_ingest_url: str | None = None,
    market_symbol: str = "SPY",
    market_input_timeout_seconds: float = 60.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    assert_dev_paper_boundary()
    env = environ if environ is not None else os.environ
    effective_source_url = (
        source_ingest_url
        or env.get("SOURCE_MANAGEMENT_API_URL")
        or env.get("PANTHEON_SOURCE_INGEST_URL")
    )
    token = _login(base_url, request_timeout_seconds=request_timeout_seconds)
    payload = {
        "name": name,
        "archetype": "momentum",
        "risk": "low",
        "mandate": "Paper-only lifecycle verification in dev",
        "market": "US",
        "strategy_family": "dev_paper_baseline",
    }
    attempts = 1
    last_reconcile_meta: dict[str, Any] = {}

    status, body = _post_json(
        f"{base_url.rstrip('/')}/bff/management/personas/create-paper-bundle",
        payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": idempotency_key,
        },
        timeout_seconds=request_timeout_seconds,
    )
    if status != 201:
        raise BootstrapError(
            "governed dev paper provisioning failed: " + _failure_summary(status, body)
        )

    data = body.get("data") if isinstance(body.get("data"), Mapping) else {}
    meta = body.get("meta") if isinstance(body.get("meta"), Mapping) else {}
    persona_id = str(data.get("id") or "").strip()
    if not persona_id:
        raise BootstrapError("BFF response missing Persona ID")

    if data.get("capitalMode") != "paper" or meta.get("live_capital_side_effects") is not False:
        raise BootstrapError("BFF returned a response outside the paper-only boundary")

    state = str(data.get("state") or "").strip()
    provisioning_state = str(meta.get("provisioning_state") or "").strip()
    runtime_id = str(meta.get("runtime_id") or "").strip()
    runtime_binding_id = str(meta.get("runtime_binding_id") or "").strip()

    if (
        state in {"provisioning_failed", "failed"}
        or provisioning_state in {"failed", "compensated"}
    ):
        raise BootstrapError(
            "dev paper provisioning reached an unexpected non-success state: "
            + json.dumps(
                {
                    "state": state,
                    "provisioning_state": provisioning_state,
                    "provisioning_step": meta.get("provisioning_step"),
                },
                sort_keys=True,
            )
        )

    # Both the never-provisioned first-run path and an idempotent successful
    # replay must run this gate before returning "ok": the Persona (and its
    # required_data_sources declaration) already exists by this point, so
    # provisioning the connector here can never deadlock on a producer that
    # does not exist yet -- see DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001.
    # Skipping this on the early paper_running/succeeded replay return would
    # bypass freshness validation on that replay instead of merely skipping
    # redundant provisioning work.
    if effective_source_url:
        required_data_sources = (
            data.get("requiredDataSources")
            or data.get("required_data_sources")
            or (list(DEV_US_REQUIRED_DATA_SOURCES) if str(data.get("market") or "US").strip().upper() == "US" else [])
        )
        ensure_source_provisioning(
            source_ingest_url=effective_source_url,
            persona_id=persona_id,
            required_data_sources=required_data_sources,
            controller_token=source_ingest_controller_token(env),
            request_timeout_seconds=request_timeout_seconds,
        )
        ensure_dev_market_snapshot_ready(
            source_ingest_url=effective_source_url,
            symbol=market_symbol,
            timeout_seconds=market_input_timeout_seconds,
            poll_seconds=poll_seconds,
            request_timeout_seconds=request_timeout_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )

    if (
        state == "paper_running"
        and provisioning_state == "succeeded"
        and runtime_id
        and runtime_binding_id
    ):
        return {
            "status": "ok",
            "attempts": attempts,
            "persona_id": persona_id,
            "state": state,
            "provisioning_state": provisioning_state,
            "provisioning_step": meta.get("provisioning_step"),
            "runtime_id": runtime_id,
            "runtime_binding_id": runtime_binding_id,
            "deployment_plan_id": meta.get("deployment_plan_id"),
            "capital_mode": "paper",
            "live_capital_side_effects": False,
        }

    effective_timeout_seconds = effective_poll_timeout_seconds(timeout_seconds, environ=environ)
    deadline = monotonic() + effective_timeout_seconds

    if provisioning_state not in {"reserved", "provisioning"}:
        raise BootstrapError(
            "dev paper provisioning reached an unexpected non-success state: "
            + json.dumps(
                {
                    "state": state,
                    "provisioning_state": provisioning_state,
                    "provisioning_step": meta.get("provisioning_step"),
                },
                sort_keys=True,
            )
        )

    reconcile_url = f"{base_url.rstrip('/')}/bff/personas/{persona_id}/provisioning/reconcile"
    reconcile_headers = {"Authorization": f"Bearer {token}"}

    while True:
        if monotonic() >= deadline:
            raise BootstrapError(
                "timed out waiting for authoritative runtime binding and paper worker: "
                + _timeout_detail(
                    attempts=attempts,
                    persona_id=persona_id,
                    state=state,
                    provisioning_state=provisioning_state,
                    requested_timeout_seconds=timeout_seconds,
                    effective_timeout_seconds=effective_timeout_seconds,
                    last_reconcile_meta=last_reconcile_meta,
                )
            )

        attempts += 1
        r_status, r_body = _post_json(
            reconcile_url,
            headers=reconcile_headers,
            timeout_seconds=request_timeout_seconds,
        )
        if r_status != 200:
            raise BootstrapError(
                "persona provisioning reconcile failed: " + _failure_summary(r_status, r_body)
            )

        r_data = r_body.get("data") if isinstance(r_body.get("data"), Mapping) else {}
        r_meta = r_body.get("meta") if isinstance(r_body.get("meta"), Mapping) else {}

        r_persona_id = str(r_data.get("id") or r_data.get("persona_id") or "").strip()
        if not r_persona_id or r_persona_id != persona_id:
            raise BootstrapError(
                f"reconcile returned mismatched Persona ID {r_persona_id!r}, expected {persona_id!r}"
            )

        if r_data.get("capitalMode") != "paper":
            raise BootstrapError("reconcile response outside paper-only boundary")

        r_lifecycle = str(r_meta.get("lifecycle_state") or r_data.get("state") or "").strip()
        r_status_label = str(r_meta.get("status") or "").strip().lower()
        degraded_deps = r_meta.get("degraded_dependencies") or []
        last_reconcile_meta = dict(r_meta)

        if r_lifecycle in {"provisioning_failed", "failed"}:
            raise BootstrapError(
                "dev paper provisioning terminal failure during reconcile: "
                + json.dumps(
                    {
                        "persona_id": persona_id,
                        "lifecycle_state": r_lifecycle,
                        "meta": r_meta,
                    },
                    sort_keys=True,
                )
            )

        if r_status_label == "degraded" or degraded_deps:
            raise BootstrapError(
                "dev paper provisioning degraded during reconcile: "
                + json.dumps(
                    {
                        "persona_id": persona_id,
                        "lifecycle_state": r_lifecycle,
                        "status": r_status_label,
                        "degraded_dependencies": degraded_deps,
                    },
                    sort_keys=True,
                )
            )

        if r_lifecycle == "paper_running":
            s_status, s_body = _post_json(
                f"{base_url.rstrip('/')}/bff/management/personas/create-paper-bundle",
                payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": idempotency_key,
                },
                timeout_seconds=request_timeout_seconds,
            )
            if s_status != 201:
                raise BootstrapError(
                    "governed dev paper provisioning authoritative readback failed: "
                    + _failure_summary(s_status, s_body)
                )

            s_data = s_body.get("data") if isinstance(s_body.get("data"), Mapping) else {}
            s_meta = s_body.get("meta") if isinstance(s_body.get("meta"), Mapping) else {}
            s_persona_id = str(s_data.get("id") or "").strip()
            if s_persona_id != persona_id:
                raise BootstrapError(
                    f"authoritative create readback returned mismatched Persona ID {s_persona_id!r}, expected {persona_id!r}"
                )

            if s_data.get("capitalMode") != "paper" or s_meta.get("live_capital_side_effects") is not False:
                raise BootstrapError("BFF returned a response outside the paper-only boundary")

            s_state = str(s_data.get("state") or "")
            s_prov_state = str(s_meta.get("provisioning_state") or "")
            s_runtime_id = str(s_meta.get("runtime_id") or "").strip()
            s_runtime_binding_id = str(s_meta.get("runtime_binding_id") or "").strip()

            if (
                s_state == "paper_running"
                and s_prov_state == "succeeded"
                and s_runtime_id
                and s_runtime_binding_id
            ):
                return {
                    "status": "ok",
                    "attempts": attempts,
                    "persona_id": persona_id,
                    "state": s_state,
                    "provisioning_state": s_prov_state,
                    "provisioning_step": s_meta.get("provisioning_step"),
                    "runtime_id": s_runtime_id,
                    "runtime_binding_id": s_runtime_binding_id,
                    "deployment_plan_id": s_meta.get("deployment_plan_id"),
                    "capital_mode": "paper",
                    "live_capital_side_effects": False,
                }

            if s_prov_state not in {"reserved", "provisioning"}:
                raise BootstrapError(
                    "dev paper provisioning reached an unexpected non-success state: "
                    + json.dumps(
                        {
                            "state": s_state,
                            "provisioning_state": s_prov_state,
                            "provisioning_step": s_meta.get("provisioning_step"),
                        },
                        sort_keys=True,
                    )
                )

        state = r_lifecycle
        provisioning_state = "provisioning"

        if monotonic() >= deadline:
            raise BootstrapError(
                "timed out waiting for authoritative runtime binding and paper worker: "
                + _timeout_detail(
                    attempts=attempts,
                    persona_id=persona_id,
                    state=state,
                    provisioning_state=provisioning_state,
                    requested_timeout_seconds=timeout_seconds,
                    effective_timeout_seconds=effective_timeout_seconds,
                    last_reconcile_meta=last_reconcile_meta,
                )
            )

        sleep(poll_seconds)


def run_self_tests() -> int:
    """Run regression self-tests covering market snapshot readiness paths.

    Covers:
    1. Steady-state path: snapshot is already fresh and admissible -> returns immediately.
    2. Never-ingested first-run path: 404 initially, triggers run-scheduled, succeeds when fresh snapshot appears.
    3. Missing snapshot timeout: bounds wait and names missing snapshot symbol and reason code.
    4. Insufficient closes: < 2 closes is rejected and named.
    5. Stale snapshot: event_time > 86400s is rejected and named.
    6. Future snapshot: event_time in future is rejected and named.
    7. Integration with ensure_paper_baseline under effective_source_url.
    """
    from unittest.mock import patch

    this_module = sys.modules[__name__]
    tests_run = 0
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    valid_snapshot = {
        "schema_version": 1,
        "snapshot_id": "snap-test-001",
        "symbol": "SPY",
        "event_time": now_iso,
        "observed_at": now_iso,
        "closes": [500.0, 501.5],
        "lineage": {"source": "simulation"},
        "source_ref": "source-ingest://snapshots/snap-test-001",
    }

    # Test 1: Steady-state path (already fresh)
    with patch.object(this_module, "_get_json", return_value=(200, valid_snapshot)):
        res = ensure_dev_market_snapshot_ready(
            source_ingest_url="http://mock-source:8097",
            symbol="SPY",
            timeout_seconds=5.0,
            poll_seconds=0.01,
        )
        assert res["snapshot_id"] == "snap-test-001"
        assert res["symbol"] == "SPY"
        assert len(res["closes"]) == 2
        tests_run += 1

    # Test 2: Never-ingested first-run path (404 initially, then appears)
    responses_first_run = [
        (404, {"detail": {"code": "market_snapshot_not_found", "symbol": "SPY"}}),
        (200, valid_snapshot),
    ]
    post_calls = []

    def fake_post_json(url, payload=None, **kwargs):
        post_calls.append((url, payload))
        return 200, {"status": "ok"}

    with patch.object(this_module, "_get_json", side_effect=responses_first_run), \
         patch.object(this_module, "_post_json", side_effect=fake_post_json):
        res = ensure_dev_market_snapshot_ready(
            source_ingest_url="http://mock-source:8097",
            symbol="SPY",
            timeout_seconds=5.0,
            poll_seconds=0.01,
        )
        assert res["snapshot_id"] == "snap-test-001"
        assert len(post_calls) >= 1
        assert "run-scheduled" in post_calls[0][0]
        tests_run += 1

    # Test 3: Missing snapshot timeout names symbol and reason
    mock_clock = [0.0]

    def fake_mono():
        mock_clock[0] += 10.0
        return mock_clock[0]

    with patch.object(this_module, "_get_json", return_value=(404, {})), \
         patch.object(this_module, "_post_json", return_value=(200, {})):
        try:
            ensure_dev_market_snapshot_ready(
                source_ingest_url="http://mock-source:8097",
                symbol="SPY",
                timeout_seconds=5.0,
                poll_seconds=0.01,
                monotonic=fake_mono,
                sleep=lambda _: None,
            )
            raise AssertionError("Expected BootstrapError on missing snapshot timeout")
        except BootstrapError as exc:
            assert "symbol 'SPY'" in str(exc), f"Expected symbol in error: {exc}"
            assert "market_snapshot_not_found" in str(exc), f"Expected reason in error: {exc}"
            tests_run += 1

    # Test 4: Insufficient closes rejected (< 2 closes)
    one_close_snapshot = dict(valid_snapshot, closes=[500.0])
    mock_clock = [0.0]
    with patch.object(this_module, "_get_json", return_value=(200, one_close_snapshot)):
        try:
            ensure_dev_market_snapshot_ready(
                source_ingest_url="http://mock-source:8097",
                symbol="SPY",
                timeout_seconds=5.0,
                poll_seconds=0.01,
                monotonic=fake_mono,
                sleep=lambda _: None,
            )
            raise AssertionError("Expected BootstrapError on insufficient closes")
        except BootstrapError as exc:
            assert "symbol 'SPY'" in str(exc)
            assert "market_input_insufficient" in str(exc)
            tests_run += 1

    # Test 5: Stale snapshot rejected (event_time > 86400s)
    stale_iso = "2020-01-01T00:00:00Z"
    stale_snapshot = dict(valid_snapshot, event_time=stale_iso)
    mock_clock = [0.0]
    with patch.object(this_module, "_get_json", return_value=(200, stale_snapshot)):
        try:
            ensure_dev_market_snapshot_ready(
                source_ingest_url="http://mock-source:8097",
                symbol="SPY",
                timeout_seconds=5.0,
                poll_seconds=0.01,
                monotonic=fake_mono,
                sleep=lambda _: None,
            )
            raise AssertionError("Expected BootstrapError on stale snapshot")
        except BootstrapError as exc:
            assert "symbol 'SPY'" in str(exc)
            assert "market_input_stale" in str(exc)
            tests_run += 1

    # Test 6: Future snapshot rejected
    future_iso = "2099-01-01T00:00:00Z"
    future_snapshot = dict(valid_snapshot, event_time=future_iso)
    mock_clock = [0.0]
    with patch.object(this_module, "_get_json", return_value=(200, future_snapshot)):
        try:
            ensure_dev_market_snapshot_ready(
                source_ingest_url="http://mock-source:8097",
                symbol="SPY",
                timeout_seconds=5.0,
                poll_seconds=0.01,
                monotonic=fake_mono,
                sleep=lambda _: None,
            )
            raise AssertionError("Expected BootstrapError on future snapshot")
        except BootstrapError as exc:
            assert "symbol 'SPY'" in str(exc)
            assert "market_input_invalid" in str(exc)
            tests_run += 1

    # Test 7: Integration in ensure_paper_baseline with effective_source_url.
    # This is an idempotent successful replay (the create response already
    # reports paper_running/succeeded), so it also proves the governed source
    # provisioning prerequisite and the freshness gate both still run on that
    # early-return path (DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001 AC4).
    dev_env = {
        "PANTHEON_ENV": "dev",
        "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID": "op-a",
        "PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET": "op-sec",
        "SOURCE_MANAGEMENT_API_URL": "http://source-ingest:8097",
        "SOURCE_INGEST_CONTROLLER_TOKEN": "controller-token-test",
    }
    bff_responses = [
        (200, {"access_token": "token-1", "meta": {"identity": "operator_a"}}),
        (201, {
            "data": {"id": "p-1", "state": "paper_running", "capitalMode": "paper"},
            "meta": {
                "provisioning_state": "succeeded",
                "provisioning_step": "done",
                "runtime_id": "rt-1",
                "runtime_binding_id": "rb-1",
                "live_capital_side_effects": False,
            },
        }),
        (200, {"summary": {"mutated": 1, "satisfied": 0, "unsupported": 0, "conflicts": 0}}),
    ]
    with patch.dict(os.environ, dev_env, clear=True), \
         patch.object(this_module, "_get_json", return_value=(200, valid_snapshot)), \
         patch.object(this_module, "_post_json", side_effect=bff_responses):
        res = ensure_paper_baseline(
            base_url="http://127.0.0.1:8001",
            name=DEFAULT_NAME,
            idempotency_key=DEFAULT_IDEMPOTENCY_KEY,
            timeout_seconds=30.0,
            poll_seconds=0.1,
            request_timeout_seconds=5.0,
        )
        assert res["status"] == "ok"
        assert res["persona_id"] == "p-1"
        tests_run += 1

    print(json.dumps({
        "status": "passed",
        "tests_run": tests_run,
        "suite": "bootstrap_dev_paper_baseline_self_test",
    }, indent=2))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--idempotency-key", default=DEFAULT_IDEMPOTENCY_KEY)
    parser.add_argument("--timeout-seconds", type=float, default=420)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--request-timeout-seconds", type=float, default=180)
    parser.add_argument(
        "--source-ingest-url",
        default=os.getenv("SOURCE_MANAGEMENT_API_URL", os.getenv("PANTHEON_SOURCE_INGEST_URL", "")),
        help="Base URL for source ingestion service to verify market snapshot readiness",
    )
    parser.add_argument(
        "--market-symbol",
        default="SPY",
        help="Market symbol required for the dev paper baseline",
    )
    parser.add_argument(
        "--market-input-timeout-seconds",
        type=float,
        default=60.0,
        help="Maximum wait time for admissible market snapshot before paper baseline creation",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-tests verifying first-run and steady-state bootstrap paths",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return run_self_tests()
    try:
        result = ensure_paper_baseline(
            base_url=args.base_url,
            name=args.name,
            idempotency_key=args.idempotency_key,
            timeout_seconds=max(1, args.timeout_seconds),
            poll_seconds=max(0.1, args.poll_seconds),
            request_timeout_seconds=max(1, args.request_timeout_seconds),
            source_ingest_url=args.source_ingest_url or None,
            market_symbol=args.market_symbol,
            market_input_timeout_seconds=max(1, args.market_input_timeout_seconds),
        )
    except (BootstrapError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

