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
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_NAME = "Pantheon Dev Paper Baseline 3"
DEFAULT_IDEMPOTENCY_KEY = "dev-paper-bootstrap-20260720-operator-a-v3"


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


def ensure_paper_baseline(
    *,
    base_url: str,
    name: str,
    idempotency_key: str,
    timeout_seconds: float,
    poll_seconds: float,
    request_timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    assert_dev_paper_boundary()
    token = _login(base_url, request_timeout_seconds=request_timeout_seconds)
    payload = {
        "name": name,
        "archetype": "momentum",
        "risk": "low",
        "mandate": "Paper-only lifecycle verification in dev",
        "market": "US",
        "strategy_family": "dev_paper_baseline",
    }
    deadline = monotonic() + timeout_seconds
    attempts = 1

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
                + json.dumps(
                    {
                        "attempts": attempts,
                        "persona_id": persona_id,
                        "state": state,
                        "provisioning_state": provisioning_state,
                    },
                    sort_keys=True,
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
                + json.dumps(
                    {
                        "attempts": attempts,
                        "persona_id": persona_id,
                        "state": state,
                        "provisioning_state": provisioning_state,
                    },
                    sort_keys=True,
                )
            )

        sleep(poll_seconds)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--idempotency-key", default=DEFAULT_IDEMPOTENCY_KEY)
    parser.add_argument("--timeout-seconds", type=float, default=420)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--request-timeout-seconds", type=float, default=180)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = ensure_paper_baseline(
            base_url=args.base_url,
            name=args.name,
            idempotency_key=args.idempotency_key,
            timeout_seconds=max(1, args.timeout_seconds),
            poll_seconds=max(0.1, args.poll_seconds),
            request_timeout_seconds=max(1, args.request_timeout_seconds),
        )
    except (BootstrapError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
