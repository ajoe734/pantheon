#!/usr/bin/env python3
"""Issue operator-authorized, bounded dev product credentials without printing them.

Run only inside the governed dev deploy lane. Existing dev verifier material is
read from environment, not command arguments. No owner signing key is rotated.
Output is a newly created mode-0600 shell env file; it must never be an artifact.
Governance's owner policy, not possession of a labelled JWT alone, enforces paper.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shlex
import tempfile
import time
from typing import Mapping


PAPER_SUBJECT = "pantheon-dev-paper-provisioner"
PAPER_SCOPE = "pantheon:dev-paper-approval"
TTL_SECONDS = 24 * 60 * 60
READERS = {
    "DEPLOYMENT_REGISTRY_SERVICE_TOKEN": ("pantheon-dev-deployment-registry-reader", "registry-reader"),
    "DEPLOYMENT_GOVERNANCE_SERVICE_TOKEN": ("pantheon-dev-deployment-approval-reader", "approval_reader"),
    "REGISTRY_GOVERNANCE_SERVICE_TOKEN": ("pantheon-dev-registry-approval-reader", "approval_reader"),
    "GOVERNANCE_REGISTRY_SERVICE_TOKEN": ("pantheon-dev-governance-registry-reader", "registry-reader"),
    "RUNTIME_MANAGER_GOVERNANCE_SERVICE_TOKEN": ("pantheon-dev-runtime-approval-reader", "approval_reader"),
    "RUNTIME_MANAGER_REGISTRY_SERVICE_TOKEN": ("pantheon-dev-runtime-registry-reader", "registry-reader"),
}
CONSUMER_FILES = {
    "deployment": ("DEPLOYMENT_REGISTRY_SERVICE_TOKEN", "DEPLOYMENT_GOVERNANCE_SERVICE_TOKEN"),
    "registry": ("REGISTRY_GOVERNANCE_SERVICE_TOKEN",),
    "governance": ("GOVERNANCE_REGISTRY_SERVICE_TOKEN",),
    "runtime-manager": ("RUNTIME_MANAGER_REGISTRY_SERVICE_TOKEN", "RUNTIME_MANAGER_GOVERNANCE_SERVICE_TOKEN"),
    "operator-bff": ("PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN",),
}
REFRESH_SECONDS = 60 * 60


def issue_environment(env: Mapping[str, str], *, now: int | None = None) -> dict[str, str]:
    if env.get("PANTHEON_ENV") != "dev" or env.get("PANTHEON_DEV_BFF_TENANT_ID") != "tenant-dev":
        raise ValueError("Product principal issuance requires dev and exact tenant-dev")
    if env.get("PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED") != "true":
        raise ValueError("Explicit dev paper principal issuance authorization required")
    secret = env.get("PANTHEON_DEV_BFF_JWT_SECRET", "").strip()
    issuer = env.get("PANTHEON_DEV_BFF_JWT_ISSUER", "").strip()
    audience = env.get("PANTHEON_DEV_BFF_JWT_AUDIENCE", "").strip()
    if len(secret) < 32 or not issuer or not audience:
        raise ValueError("Configured dev verifier secret, issuer and audience required")
    issued = int(time.time()) if now is None else now

    def token(subject: str, role: str, scope: str) -> str:
        claims = {
            "sub": subject, "service": subject, "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"], "roles": [role], "scope": scope,
            "iss": issuer, "aud": audience, "iat": issued, "nbf": issued,
            "exp": issued + TTL_SECONDS, "jti": secrets.token_hex(16),
        }
        encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=")
        encoded = encode(b'{"alg":"HS256","typ":"JWT"}') + b"." + encode(
            json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        )
        signature = hmac.new(secret.encode(), encoded, hashlib.sha256).digest()
        return (encoded + b"." + encode(signature)).decode("ascii")

    values = {
        variable: token(subject, role, "pantheon:dev-owner-read")
        for variable, (subject, role) in READERS.items()
    }
    values.update({
        "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN": token(PAPER_SUBJECT, "automated_gate", PAPER_SCOPE),
        "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID": PAPER_SUBJECT,
        "GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED": "true",
        "GOVERNANCE_REGISTRY_BASE_URL": "http://registry:8087",
        "PANTHEON_REGISTRY_JWT_ISSUER": issuer,
        "PANTHEON_REGISTRY_JWT_AUDIENCE": audience,
        "GOVERNANCE_DEV_PAPER_GRANT_FILE": "/run/pantheon-principals/grant",
    })
    for variables in CONSUMER_FILES.values():
        for variable in variables:
            values[variable + "_FILE"] = "/run/pantheon-principals/" + variable
    return values


def revoked_environment(env: Mapping[str, str]) -> dict[str, str]:
    if (env.get("PANTHEON_ENV") != "dev" or env.get("PANTHEON_DEV_BFF_TENANT_ID") != "tenant-dev"
            or env.get("PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED") != "false"):
        raise ValueError("Explicit tenant-dev withdrawal required")
    values = {"GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED": "false",
              "GOVERNANCE_DEV_PAPER_GRANT_FILE": "/run/pantheon-principals/grant"}
    for variables in CONSUMER_FILES.values():
        for variable in variables:
            values[variable] = ""
            values[variable + "_FILE"] = "/run/pantheon-principals/" + variable
    return values


def _atomic_private(directory: Path, name: str, value: str) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Unsafe principal directory")
    os.chmod(directory, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".rotating-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def revoke_files(root: Path, env: Mapping[str, str]) -> None:
    """After issuer stop, deny even captured JWTs at Governance before deleting files."""
    revoked_environment(env)
    _atomic_private(root / "governance", "grant", "revoked")
    for consumer, variables in CONSUMER_FILES.items():
        directory = root / consumer
        if directory.is_symlink():
            raise ValueError("Unsafe principal directory")
        for variable in variables:
            (directory / variable).unlink(missing_ok=True)


def refresh_files(root: Path, env: Mapping[str, str], *, now: int | None = None) -> None:
    """Trusted dev issuer writes each consumer's distinct, read-only mounted directory.

    Atomic replacement plus request-time reads rotate without restarting product
    owners. The issuer has no listening socket, shell API or untrusted payload.
    """
    values = issue_environment(env, now=now)
    for consumer, variables in CONSUMER_FILES.items():
        directory = root / consumer
        for variable in variables:
            _atomic_private(directory, variable, values[variable])
    _atomic_private(root / "governance", "grant", "enabled")


def healthy_files(root: Path, env: Mapping[str, str], *, now: int | None = None) -> bool:
    """Check signatures, fixed grants and renewal headroom without logging values."""
    issued = int(time.time()) if now is None else now
    # Validate current environment authorization, not only old files.
    issue_environment(env, now=issued)
    if (root / "governance" / "grant").read_text() != "enabled":
        return False
    secret = env["PANTHEON_DEV_BFF_JWT_SECRET"].strip().encode()
    decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    for consumer, variables in CONSUMER_FILES.items():
        for variable in variables:
            path = root / consumer / variable
            if path.is_symlink() or path.stat().st_mode & 0o077:
                return False
            encoded = path.read_text(encoding="utf-8")
            header, payload, signature = encoded.split(".")
            signed = (header + "." + payload).encode()
            if not hmac.compare_digest(decode(signature), hmac.new(secret, signed, hashlib.sha256).digest()):
                return False
            claims = json.loads(decode(payload))
            subject, role = READERS.get(variable, (PAPER_SUBJECT, "automated_gate"))
            scope = PAPER_SCOPE if subject == PAPER_SUBJECT else "pantheon:dev-owner-read"
            expected = {"sub": subject, "service": subject, "roles": [role], "scope": scope,
                        "tenant_id": "tenant-dev", "allowed_tenants": ["tenant-dev"],
                        "iss": env["PANTHEON_DEV_BFF_JWT_ISSUER"].strip(),
                        "aud": env["PANTHEON_DEV_BFF_JWT_AUDIENCE"].strip()}
            if any(claims.get(key) != value for key, value in expected.items()):
                return False
            if not (claims["nbf"] <= issued < claims["exp"] - REFRESH_SECONDS * 2
                    and 0 < claims["exp"] - claims["iat"] <= TTL_SECONDS):
                return False
    return True


def write_environment(path: Path, values: Mapping[str, str]) -> None:
    # Refuse existing files and symlinks. Never truncate a credential or follow a
    # caller-selected symlink; the deploy lane supplies its own fresh private dir.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        for name, value in sorted(values.items()):
            output.write(f"export {name}={shlex.quote(value)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output-env", type=Path)
    mode.add_argument("--serve", action="store_true")
    mode.add_argument("--healthcheck", action="store_true")
    mode.add_argument("--revoke", action="store_true")
    parser.add_argument("--credentials-root", type=Path, default=Path("/issued"))
    args = parser.parse_args()
    try:
        if args.revoke:
            revoke_files(args.credentials_root, os.environ)
            print("Dev paper grant withdrawn; scoped credential files removed; values withheld")
            return 0
        if args.healthcheck:
            return 0 if healthy_files(args.credentials_root, os.environ) else 1
        if args.serve:
            while True:
                refresh_files(args.credentials_root, os.environ)
                print("Dev paper principals refreshed; values withheld", flush=True)
                time.sleep(REFRESH_SECONDS)
        values = (revoked_environment(os.environ)
                  if os.environ.get("PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED") == "false"
                  else issue_environment(os.environ))
        write_environment(args.output_env, values)
    except (ValueError, OSError, KeyError, TypeError):
        # Do not echo paths, environment, key material or a token on failures.
        parser.exit(1, "Dev product principal issuance failed closed\n")
    print("Prepared bounded dev principal configuration; values withheld")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
