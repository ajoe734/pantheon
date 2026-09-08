"""Tests for the real deploy/execution-grant-issuer/run_server.py entrypoint.

OPS-EXECUTION-MFA-ISSUER-001.
Exercises the actual ``run_service`` constructor path (not a hand-rolled
stand-in) to prove that revoked/disabled-account denial cannot be disabled
through configuration, and that the readiness-critical signer/verifier
wiring fails closed on bad input before a server is ever created.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEPLOY_DIR.parents[1]
ORCHESTRATOR_DIR = REPO_ROOT / ".orchestrator"
for d in (DEPLOY_DIR, REPO_ROOT, ORCHESTRATOR_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

import run_server


def _write_private_key(path: Path) -> None:
    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with open(fd, "wb") as f:
        f.write(pem)


class TestRunServiceRevocationCannotBeDisabled(unittest.TestCase):
    def _base_config(self, priv_key_path: Path) -> dict:
        return {
            "service": {"host": "127.0.0.1", "port": 0},
            "identity_platform": {
                "project_id": "pantheon-dev-20260902",
                "allowed_operator_uids": ["operator-1"],
            },
            "signing": {
                "key_id": "test-signer",
                "private_key_file": str(priv_key_path),
            },
            "policy": {
                "allowed_tasks": ["DEV502-TRACE-001"],
                "allowed_environments": ["pantheon-dev"],
            },
        }

    def test_run_service_rejects_check_revocation_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            priv_path = Path(td) / "key.pem"
            _write_private_key(priv_path)
            config = self._base_config(priv_path)
            config["identity_platform"]["check_revocation"] = False

            with self.assertRaises(ValueError) as cm:
                run_server.run_service(config)
            self.assertIn("cannot be set to a non-true value", str(cm.exception))

    def test_run_service_rejects_check_revocation_falsy_string(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            priv_path = Path(td) / "key.pem"
            _write_private_key(priv_path)
            config = self._base_config(priv_path)
            # A misconfigured string value must not be truthy-coerced away.
            config["identity_platform"]["check_revocation"] = "false"

            with self.assertRaises(ValueError) as cm:
                run_server.run_service(config)
            self.assertIn("cannot be set to a non-true value", str(cm.exception))

    def test_run_service_accepts_check_revocation_true_and_proceeds_past_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            priv_path = Path(td) / "key.pem"
            _write_private_key(priv_path)
            config = self._base_config(priv_path)
            config["identity_platform"]["check_revocation"] = True
            # No real server should be started by this test: an invalid host
            # forces run_service to fail closed on the very next check
            # (plaintext non-loopback binding without TLS) instead, proving
            # the revocation gate itself did not block a legitimate explicit
            # `true`.
            config["service"]["host"] = "10.0.0.5"

            with self.assertRaises(ValueError) as cm:
                run_server.run_service(config)
            self.assertIn("Plaintext HTTP", str(cm.exception))

    def test_run_service_omitted_check_revocation_defaults_to_mandatory_true(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            priv_path = Path(td) / "key.pem"
            _write_private_key(priv_path)
            config = self._base_config(priv_path)
            config["service"]["host"] = "10.0.0.5"

            with self.assertRaises(ValueError) as cm:
                run_server.run_service(config)
            self.assertIn("Plaintext HTTP", str(cm.exception))

    def test_verifier_constructor_has_no_check_revocation_parameter(self) -> None:
        from execution_grant_issuer.token_verifier import IdentityPlatformTokenVerifier

        with self.assertRaises(TypeError):
            IdentityPlatformTokenVerifier(
                project_id="pantheon-dev-20260902",
                allowed_operator_uids=["operator-1"],
                check_revocation=False,
            )

    def test_generate_key_pair_writes_private_key_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "sub" / "key.pem"
            run_server.generate_key_pair(out_path, key_id="test-key-id")
            self.assertTrue(out_path.is_file())
            self.assertEqual(stat.S_IMODE(out_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
