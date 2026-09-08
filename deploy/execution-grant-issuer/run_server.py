#!/usr/bin/env python3
"""Entrypoint to configure, launch, and manage the Execution Grant Issuer service.

OPS-EXECUTION-MFA-ISSUER-001.
Source of record: ISSUER-SA-SD-20260908.md.

Usage:
  # Generate a fresh Ed25519 key pair and print config snippet:
  python3 run_server.py --generate-key-pair /etc/pantheon/execution-grant-issuer/ed25519-private.pem

  # Run the service with configuration:
  python3 run_server.py --config /etc/pantheon/execution-grant-issuer/config.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

# Resolve repository and .orchestrator directories
DEPLOY_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEPLOY_DIR.parents[1]
ORCHESTRATOR_DIR = REPO_ROOT / ".orchestrator"
for d in (REPO_ROOT, ORCHESTRATOR_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from execution_grant_issuer.challenge_store import ChallengeStore
from execution_grant_issuer.secure_io import UnsafeCredentialFileError, read_private_file_strict
from execution_grant_issuer.service import ExecutionGrantIssuerService, create_issuer_server
from execution_grant_issuer.signer import Ed25519GrantSigner
from execution_grant_issuer.token_verifier import IdentityPlatformTokenVerifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("execution_grant_issuer")


def generate_key_pair(output_path: Path, key_id: str = "pantheon-mfa-issuer-dev-20260908") -> None:
    """Generate a new Ed25519 key pair and print public key trust configuration."""
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    priv_key = Ed25519PrivateKey.generate()
    pem_bytes = priv_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(str(output_path), flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"Private key destination already exists or is a symlink: {output_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to create exclusive private key file {output_path}: {exc}") from exc

    try:
        with open(fd, "wb") as f:
            f.write(pem_bytes)
    except Exception:
        raise

    signer = Ed25519GrantSigner(priv_key, key_id=key_id)
    pub_b64 = signer.public_key_base64url
    fp = signer.public_key_fingerprint

    print(f"Generated Ed25519 private key: {output_path} (mode 0600)")
    print(f"Key ID: {key_id}")
    print(f"Public Key (base64url): {pub_b64}")
    print(f"SHA-256 Fingerprint: {fp}")
    print("\nAdd the following public trust snippet to Pantheon's .orchestrator/config.json:")
    config_snippet = {
        "execution_authorization": {
            "mfa_issuer_public_keys": {
                key_id: pub_b64
            }
        }
    }
    print(json.dumps(config_snippet, indent=2))


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def run_service(config: dict[str, Any]) -> None:
    svc_cfg = config.get("service", {})
    id_cfg = config.get("identity_platform", {})
    sign_cfg = config.get("signing", {})
    pol_cfg = config.get("policy", {})

    host = svc_cfg.get("host", "127.0.0.1")
    port = int(svc_cfg.get("port", 8090))
    audit_log = svc_cfg.get("audit_log_path")

    tls_cfg = svc_cfg.get("tls", {})
    tls_enabled = tls_cfg.get("enabled", False)
    ssl_cert_file = tls_cfg.get("cert_file")
    ssl_key_file = tls_cfg.get("key_file")

    if host not in ("127.0.0.1", "localhost", "::1") and not tls_enabled:
        raise ValueError(
            f"Plaintext HTTP on non-loopback host {host!r} is strictly prohibited. "
            "Configure 'service.tls.enabled': true with cert_file and key_file, or bind host to 127.0.0.1."
        )

    if tls_enabled:
        if not ssl_cert_file or not Path(ssl_cert_file).is_file():
            raise FileNotFoundError(f"TLS certificate file not found: {ssl_cert_file}")
        if not ssl_key_file or not Path(ssl_key_file).is_file():
            raise FileNotFoundError(f"TLS private key file not found: {ssl_key_file}")

    # Initialize Token Verifier. Verification (signature, issuer, audience,
    # expiry, and revoked/disabled-account denial when check_revocation is
    # enabled) is performed by the pinned firebase-admin SDK using
    # Application Default Credentials on this isolated issuer host -- no
    # downloadable service-account key file is read or required.
    project_id = id_cfg.get("project_id", "pantheon-dev-20260902")
    allowed_uids = id_cfg.get("allowed_operator_uids", [])
    max_auth_age = int(id_cfg.get("max_auth_age_seconds", 3600))
    allowed_factors = id_cfg.get("allowed_second_factors")
    check_revocation = bool(id_cfg.get("check_revocation", True))

    verifier = IdentityPlatformTokenVerifier(
        project_id=project_id,
        allowed_operator_uids=allowed_uids,
        max_auth_age_seconds=max_auth_age,
        allowed_second_factors=allowed_factors,
        check_revocation=check_revocation,
    )

    # Initialize Signer
    key_id = sign_cfg.get("key_id", "pantheon-mfa-issuer-dev-20260908")
    priv_file = sign_cfg.get("private_key_file")
    if not priv_file:
        raise ValueError("signing.private_key_file must be specified in config")
    try:
        priv_bytes = read_private_file_strict(
            Path(priv_file).expanduser(), description="Signer private key file"
        )
    except UnsafeCredentialFileError as exc:
        raise ValueError(str(exc)) from exc

    signer = Ed25519GrantSigner(priv_bytes, key_id=key_id)

    # Initialize Challenge Store
    challenge_ttl = int(pol_cfg.get("challenge_ttl_seconds", 180))
    challenge_store = ChallengeStore(default_ttl_seconds=challenge_ttl)

    # Policy controls
    allowed_tasks = pol_cfg.get("allowed_tasks", ["DEV502-TRACE-001"])
    allowed_envs = pol_cfg.get("allowed_environments", ["pantheon-dev"])
    freshness = int(pol_cfg.get("grant_freshness_seconds", 120))
    run_ttl = int(pol_cfg.get("default_run_ttl_seconds", 1800))

    service = ExecutionGrantIssuerService(
        verifier=verifier,
        signer=signer,
        challenge_store=challenge_store,
        allowed_tasks=allowed_tasks,
        allowed_environments=allowed_envs,
        grant_freshness_seconds=freshness,
        run_ttl_seconds=run_ttl,
        audit_log_path=audit_log,
    )

    server = create_issuer_server(
        service,
        host=host,
        port=port,
        ssl_cert_file=ssl_cert_file if tls_enabled else None,
        ssl_key_file=ssl_key_file if tls_enabled else None,
    )
    logger.info("Pantheon Execution Grant Issuer starting on %s:%d", host, port)
    logger.info("Identity Platform Project: %s", project_id)
    logger.info("Signer Key ID: %s (fingerprint: %s)", key_id, signer.public_key_fingerprint)
    logger.info("Allowed Tasks: %s", sorted(allowed_tasks))

    def _shutdown_handler(signum, frame):
        logger.info("Received termination signal %d; shutting down...", signum)
        threading.Thread(target=server.shutdown).start()

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    try:
        server.serve_forever()
    finally:
        server.server_close()
        logger.info("Execution Grant Issuer stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Pantheon Execution Grant Issuer service.")
    parser.add_argument("--config", help="Path to JSON configuration file")
    parser.add_argument("--generate-key-pair", help="Generate Ed25519 key pair to destination path")
    parser.add_argument("--key-id", default="pantheon-mfa-issuer-dev-20260908", help="Key ID for generated key")

    args = parser.parse_args()

    if args.generate_key_pair:
        generate_key_pair(Path(args.generate_key_pair), key_id=args.key_id)
        return

    config_path = (
        Path(args.config)
        if args.config
        else Path(os.environ.get("ISSUER_CONFIG_PATH", "/etc/pantheon/execution-grant-issuer/config.json"))
    )

    if not config_path.is_file():
        parser.error(
            f"Configuration file not found at {config_path}. "
            "Specify --config <path> or generate a key with --generate-key-pair."
        )

    config = load_config(config_path)
    run_service(config)


if __name__ == "__main__":
    main()
