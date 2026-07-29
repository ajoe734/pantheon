#!/usr/bin/env python3
"""Assign Pantheon roles to an existing GCP Identity Platform user.

The script uses Application Default Credentials and never accepts or prints a
user password, ID token, refresh token, API key, or service-account key.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.request
from typing import Any


ALLOWED_ROLES = {
    "admin",
    "approver",
    "operator",
    "reviewer",
    "risk_owner",
    "viewer",
}


def _access_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("Application Default Credentials returned no access token")
    return token


def _post(project_id: str, action: str, payload: dict[str, Any], token: str) -> dict[str, Any]:
    url = (
        "https://identitytoolkit.googleapis.com/v1/projects/"
        f"{project_id}/accounts:{action}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Identity Platform {action} failed with HTTP {exc.code}: {detail}"
        ) from exc


def _pantheon_claim_roles(roles: list[str]) -> list[str]:
    normalized = sorted({role.strip().lower() for role in roles if role.strip()})
    unsupported = sorted(set(normalized) - ALLOWED_ROLES)
    if unsupported:
        raise ValueError(f"unsupported Pantheon roles: {', '.join(unsupported)}")
    return [f"pantheon-{role.replace('_', '-')}" for role in normalized]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-id",
        default="pantheon-lupin-dev-20260719",
    )
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--role",
        action="append",
        required=True,
        help="Repeat for multiple roles (viewer, operator, reviewer, approver, risk_owner, admin).",
    )
    parser.add_argument("--tenant-id", default="tenant-dev")
    args = parser.parse_args()

    token = _access_token()
    lookup = _post(
        args.project_id,
        "lookup",
        {"email": [args.email]},
        token,
    )
    users = lookup.get("users")
    if not isinstance(users, list) or len(users) != 1:
        raise RuntimeError("expected exactly one Identity Platform user for that email")
    user = users[0]
    local_id = str(user.get("localId") or "").strip()
    if not local_id:
        raise RuntimeError("Identity Platform lookup returned no localId")

    existing: dict[str, Any] = {}
    raw_attributes = user.get("customAttributes")
    if isinstance(raw_attributes, str) and raw_attributes.strip():
        parsed = json.loads(raw_attributes)
        if isinstance(parsed, dict):
            existing = parsed
    existing.update(
        {
            "roles": _pantheon_claim_roles(args.role),
            "tenant_id": args.tenant_id,
        }
    )
    _post(
        args.project_id,
        "update",
        {
            "customAttributes": json.dumps(existing, separators=(",", ":"), sort_keys=True),
            "localId": local_id,
        },
        token,
    )
    print(
        json.dumps(
            {
                "email": args.email,
                "project_id": args.project_id,
                "roles": existing["roles"],
                "tenant_id": args.tenant_id,
                "updated": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
