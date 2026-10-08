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
DEFAULT_NAME = "Pantheon Dev Paper Baseline 4"
DEFAULT_IDEMPOTENCY_KEY = "dev-paper-bootstrap-20261007-operator-a-tw-v4"
DEFAULT_MARKET_SYMBOL = "2330.TW"

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
    market_symbol: str = DEFAULT_MARKET_SYMBOL,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    assert_dev_paper_boundary()
    token = _login(base_url, request_timeout_seconds=request_timeout_seconds)
    payload = {
        "name": name,
        "archetype": "momentum",
        "risk": "low",
        "mandate": "Paper-only lifecycle verification in dev",
        "market": "TW",
        "symbols": [market_symbol],
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

def transition_legacy_persona_market_record(
    *,
    idempotency_key: str,
    tenant_id: str = "tenant-a",
    market: str = "US",
    new_version: str = "1.0.1",
    coordinator: Any = None,
) -> dict[str, Any]:
    """Execute a governed legacy persona market transition for an existing provisioning record.

    Coordinates an approved child revision (version 1.0.1) citing the immutable parent
    artifact via existing Registry and Governance owners under zero-capital bounds.
    """
    if coordinator is None:
        try:
            from services.control_plane.bff.persona_provisioning_coordinator import (
                PersonaProvisioningCoordinator,
            )
            from services.control_plane.bff.personas.service import (
                PERSONA_OWNER_SERVICE_ACTOR_ID,
                _PersonaOwnerHttpTransport,
                _persona_provisioning_store,
                _register_persona_cron_required,
            )

            store = _persona_provisioning_store()
            coordinator = PersonaProvisioningCoordinator(
                store=store,
                transport=_PersonaOwnerHttpTransport(tenant_id=tenant_id),
                schedule_registrar=_register_persona_cron_required,
                lease_owner=f"legacy-market-transition:{os.getpid()}",
                lease_seconds=180,
                actor_id=PERSONA_OWNER_SERVICE_ACTOR_ID,
                governance_actor_id=os.getenv(
                    "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID", "pantheon-dev-paper-provisioner"
                ),
            )
        except Exception as exc:
            raise BootstrapError(
                f"Cannot initialize coordinator for legacy persona market transition: {exc}"
            ) from exc

    store = getattr(coordinator, "store", None)
    if store is None or not hasattr(store, "get"):
        raise BootstrapError("Coordinator is missing an accessible provisioning store")
    record = store.get(tenant_id, idempotency_key)
    if record is None:
        raise BootstrapError(
            f"Persona provisioning record not found for tenant '{tenant_id}' and key '{idempotency_key}'"
        )

    transitioned = coordinator.transition_legacy_persona_market(
        record,
        market=market,
        new_version=new_version,
    )
    result = getattr(transitioned, "result", None) or {}
    return {
        "status": "ok",
        "tenant_id": getattr(transitioned, "tenant_id", tenant_id),
        "persona_id": getattr(transitioned, "persona_id", ""),
        "idempotency_key": getattr(transitioned, "idempotency_key", idempotency_key),
        "strategy_artifact_id": result.get("strategy_artifact_id"),
        "legacy_strategy_artifact_id": result.get("legacy_strategy_artifact_id"),
        "market": result.get("market"),
        "version": new_version,
    }


def run_self_tests() -> int:
    """Run regression self-tests covering legacy persona transition path."""
    tests_run = 0

    # Test 9: transition_legacy_persona_market_record wires coordinator call correctly
    class _MockStore:
        def __init__(self, rec):
            self.rec = rec
        def get(self, tenant, key):
            if tenant == "t1" and key == "k1":
                return self.rec
            return None

    class _MockRecord:
        def __init__(self):
            self.tenant_id = "t1"
            self.persona_id = "p1"
            self.idempotency_key = "k1"
            self.result = {
                "strategy_artifact_id": "art-rev1",
                "legacy_strategy_artifact_id": "art-parent",
                "market": "US",
            }

    mock_rec = _MockRecord()
    class _MockCoord:
        def __init__(self):
            self.store = _MockStore(mock_rec)
            self.called_with = None
        def transition_legacy_persona_market(self, record, *, market=None, new_version="1.0.1"):
            self.called_with = (record, market, new_version)
            return record

    mock_coord = _MockCoord()
    trans_res = transition_legacy_persona_market_record(
        idempotency_key="k1",
        tenant_id="t1",
        market="US",
        coordinator=mock_coord,
    )
    assert trans_res["status"] == "ok"
    assert trans_res["strategy_artifact_id"] == "art-rev1"
    assert trans_res["legacy_strategy_artifact_id"] == "art-parent"
    assert trans_res["market"] == "US"
    assert mock_coord.called_with[1] == "US"
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
        "--market-symbol",
        default=DEFAULT_MARKET_SYMBOL,
        help="Market symbol required for the dev paper baseline",
    )
    parser.add_argument(
        "--transition-legacy-persona",
        action="store_true",
        help="Execute governed child revision transition with explicit market for a legacy persona",
    )
    parser.add_argument(
        "--legacy-idempotency-key",
        default="",
        help="Idempotency key of the legacy persona provisioning record to transition",
    )
    parser.add_argument(
        "--legacy-tenant-id",
        default=os.getenv("PANTHEON_DEFAULT_TENANT_ID", "tenant-a"),
        help="Tenant ID of the legacy persona provisioning record to transition",
    )
    parser.add_argument(
        "--legacy-market",
        default="US",
        help="Market context for the legacy persona transition (default: US)",
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
    if args.transition_legacy_persona:
        if not args.legacy_idempotency_key:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": "--legacy-idempotency-key is required when --transition-legacy-persona is set",
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1
        try:
            result = transition_legacy_persona_market_record(
                idempotency_key=args.legacy_idempotency_key,
                tenant_id=args.legacy_tenant_id,
                market=args.legacy_market,
            )
        except (BootstrapError, OSError, ValueError) as exc:
            print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True), file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0

    try:
        result = ensure_paper_baseline(
            base_url=args.base_url,
            name=args.name,
            idempotency_key=args.idempotency_key,
            timeout_seconds=max(1, args.timeout_seconds),
            poll_seconds=max(0.1, args.poll_seconds),
            request_timeout_seconds=max(1, args.request_timeout_seconds),
            market_symbol=args.market_symbol,
        )
    except (BootstrapError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

