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
    payload: Mapping[str, Any],
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
        data=json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"),
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
    attempts = 0

    while True:
        attempts += 1
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
        state = str(data.get("state") or "")
        provisioning_state = str(meta.get("provisioning_state") or "")
        runtime_id = str(meta.get("runtime_id") or "").strip()
        runtime_binding_id = str(meta.get("runtime_binding_id") or "").strip()

        if data.get("capitalMode") != "paper" or meta.get("live_capital_side_effects") is not False:
            raise BootstrapError("BFF returned a response outside the paper-only boundary")

        if (
            state == "paper_running"
            and provisioning_state == "succeeded"
            and runtime_id
            and runtime_binding_id
        ):
            return {
                "status": "ok",
                "attempts": attempts,
                "persona_id": data.get("id"),
                "state": state,
                "provisioning_state": provisioning_state,
                "provisioning_step": meta.get("provisioning_step"),
                "runtime_id": runtime_id,
                "runtime_binding_id": runtime_binding_id,
                "deployment_plan_id": meta.get("deployment_plan_id"),
                "capital_mode": "paper",
                "live_capital_side_effects": False,
            }

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
        if monotonic() >= deadline:
            raise BootstrapError(
                "timed out waiting for authoritative runtime binding and paper worker: "
                + json.dumps(
                    {
                        "attempts": attempts,
                        "state": state,
                        "provisioning_state": provisioning_state,
                        "provisioning_step": meta.get("provisioning_step"),
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
