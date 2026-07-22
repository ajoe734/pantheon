#!/usr/bin/env python3
"""Mint short-lived, dev-only BFF proof JWTs without printing credentials."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path


TTL_SECONDS = 3600
# The public dev environment is named ``pantheon-dev``, while its canonical
# data-plane tenant is ``tenant-dev``.  Proof JWTs must follow the data-plane
# tenant so hosted reads and writes exercise the same rows as the dev runtime.
TENANT_ID = "tenant-dev"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def mint_jwt(secret: str, claims: dict[str, object]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    encoded_claims = _b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"


def build_bundle(
    *, secret: str, issuer: str, audience: str, run_id: str, now: int
) -> tuple[dict[str, str], dict[str, object]]:
    if not secret:
        raise ValueError("DEV_BFF_JWT_SECRET is required")
    tokens: dict[str, str] = {}
    identities: dict[str, dict[str, object]] = {}

    cases: dict[str, list[str]] = {
        "operator_a": ["operator"],
        "operator_b": ["operator"],
        "viewer": ["viewer"],
    }
    for label, roles in cases.items():
        subject = f"pantheon-dev-proof-{label.replace('_', '-')}-{run_id}"
        claims: dict[str, object] = {
            "sub": subject,
            "user_id": subject,
            "sid": f"pantheon-dev-proof-{secrets.token_urlsafe(12)}",
            "roles": roles,
            "app_metadata": {"tenant_id": TENANT_ID},
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "nbf": now - 30,
            "exp": now + TTL_SECONDS,
            "jti": secrets.token_urlsafe(18),
            "token_use": "pantheon-dev-hosted-proof",
            # Hosted proof tokens are minted only by the dev-scoped rotation
            # workflow after the human-authorized write window is established.
            # The strict dev BFF requires an authenticated MFA claim even for
            # /bff/me, so bind that proof to the short-lived token itself.
            "mfa_verified": True,
            "tenant_id": TENANT_ID,
            "allowed_tenants": [TENANT_ID],
        }
        tokens[label] = mint_jwt(secret, claims)
        identities[label] = {
            "sub": subject,
            "roles": roles,
            "tenant_id": TENANT_ID,
            "exp": now + TTL_SECONDS,
        }

    rbac = {"operator": tokens["operator_a"], "viewer": tokens["viewer"]}
    return tokens, {"identities": identities, "rbac": rbac}


def write_bundle(output_dir: Path, tokens: dict[str, str], metadata: dict[str, object]) -> None:
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    for label, token in tokens.items():
        path = output_dir / f"{label}.jwt"
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)
    rbac = metadata["rbac"]
    (output_dir / "rbac.json").write_text(
        json.dumps(rbac, separators=(",", ":")), encoding="utf-8"
    )
    (output_dir / "rbac.json").chmod(0o600)
    safe_metadata = {"identities": metadata["identities"]}
    (output_dir / "manifest.json").write_text(
        json.dumps(safe_metadata, separators=(",", ":")), encoding="utf-8"
    )
    (output_dir / "manifest.json").chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    bundle, metadata = build_bundle(
        secret=os.environ.get("DEV_BFF_JWT_SECRET", ""),
        issuer=os.environ.get("DEV_BFF_JWT_ISSUER", "pantheon-dev"),
        audience=os.environ.get("DEV_BFF_JWT_AUDIENCE", "bff-operators"),
        run_id=args.run_id,
        now=int(time.time()),
    )
    write_bundle(args.output_dir, bundle, metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
