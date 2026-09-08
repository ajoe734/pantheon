"""Unit and integration tests for scripts/request_execution_grant.py.

OPS-EXECUTION-MFA-ISSUER-001.
Tests:
- Rejection of ID token on command line (sys.argv security check)
- Task eligibility validation (S5 pause, scope limit, policy verification)
- Protected token loading (file and stdin)
- Local grant verification with execution_authorization
- End-to-end mock flow: prepare -> challenge -> issue -> local verify
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

# Ensure orchestrator and repo root are in path
ROOT_DIR = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT_DIR / ".orchestrator"
for d in (ROOT_DIR, ORCHESTRATOR_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import execution_authorization as ea
import scripts.request_execution_grant as cli
from execution_grant_issuer.challenge_store import ChallengeStore
from execution_grant_issuer.service import ExecutionGrantIssuerService, create_issuer_server
from execution_grant_issuer.signer import Ed25519GrantSigner
from execution_grant_issuer.token_verifier import IdentityPlatformTokenVerifier


class TestRequestExecutionGrantCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rsa_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.rsa_public_key = cls.rsa_private_key.public_key()
        cls.ed25519_private_key = ed25519.Ed25519PrivateKey.generate()

    def setUp(self) -> None:
        self.now = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
        self.project_id = "pantheon-dev-20260902"
        self.operator_uid = "operator-chloe-primary"
        self.key_id = "test-kid-cli"
        self.signer_key_id = "test-signer-cli"

        self.signer = Ed25519GrantSigner(self.ed25519_private_key, key_id=self.signer_key_id)
        self.verifier = IdentityPlatformTokenVerifier(
            project_id=self.project_id,
            allowed_operator_uids=[self.operator_uid],
            trusted_public_keys={self.key_id: self.rsa_public_key},
        )

        self.task_id = "DEV502-TRACE-001"
        self.spec = {
            "id": self.task_id,
            "title": "Obtain authenticated candidate failure evidence",
            "owner": "Antigravity",
            "reviewer": "Codex",
            "target_repo": "pantheon",
            "phase": "step-3a",
            "summary": "Urgent TRACE evidence",
            "depends_on": ["DEV502-FAILPATH-001"],
            "execution_resources": ["pantheon-dev"],
            "artifacts": ["docs/deployment/evidence/DEV502-TRACE-001/"],
            "acceptance": ["Trace captured"],
        }
        self.policy = ea.derive_execution_policy(
            task_id=self.task_id,
            work_class="hosted",
            repository="pantheon",
            environment="pantheon-dev",
            resources=["pantheon-dev"],
            action_scope="execute",
            artifacts=["docs/deployment/evidence/DEV502-TRACE-001/"],
            task_spec=self.spec,
        )
        self.canonical_task = {
            "id": self.task_id,
            "generation": 3,
            "phase": "step-3a",
            "status": "todo",
            "execution_authorization": {
                "state": "pending_authorization",
                "policy": self.policy,
            },
        }

    def _mint_token(self) -> str:
        current_ts = int(datetime.now(timezone.utc).timestamp())
        claims = {
            "iss": f"https://securetoken.google.com/{self.project_id}",
            "aud": self.project_id,
            "sub": self.operator_uid,
            "user_id": self.operator_uid,
            "email": "operator-chloe@pantheon.trade",
            "email_verified": True,
            "auth_time": current_ts,
            "iat": current_ts,
            "exp": current_ts + 3600,
            "firebase": {
                "sign_in_provider": "password",
                "sign_in_second_factor": "phone",
            },
        }
        return jwt.encode(claims, self.rsa_private_key, algorithm="RS256", headers={"kid": self.key_id})

    def test_token_in_argv_rejected_with_security_error(self) -> None:
        """Verify that passing --token in sys.argv is strictly rejected."""
        cmd = [sys.executable, str(ROOT_DIR / "scripts" / "request_execution_grant.py"), "--token", "fake.jwt.token"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("process listings (ps)", proc.stderr)

        cmd_eq = [sys.executable, str(ROOT_DIR / "scripts" / "request_execution_grant.py"), "--token=fake.jwt.token"]
        proc_eq = subprocess.run(cmd_eq, capture_output=True, text=True)
        self.assertEqual(proc_eq.returncode, 2)
        self.assertIn("process listings (ps)", proc_eq.stderr)

    def test_task_eligibility_validation(self) -> None:
        """Verify task validation passes for DEV502-TRACE-001 and rejects invalid/paused tasks."""
        policy, gen = cli.validate_task_eligibility(self.canonical_task)
        self.assertEqual(gen, 3)
        self.assertTrue(policy["requires_execution_authorization"])

        # Reject S5 task ID
        s5_task = deepcopy(self.canonical_task)
        s5_task["id"] = "DEV502-S5-DEPLOY"
        with self.assertRaises(ValueError) as cm:
            cli.validate_task_eligibility(s5_task)
        self.assertIn("Step 5 / S5", str(cm.exception))

        # Reject S5 phase
        s5_phase_task = deepcopy(self.canonical_task)
        s5_phase_task["phase"] = "step-5"
        with self.assertRaises(ValueError) as cm:
            cli.validate_task_eligibility(s5_phase_task)
        self.assertIn("remains paused", str(cm.exception))

        # Reject unexpected task ID unless overridden
        other_task = deepcopy(self.canonical_task)
        other_task["id"] = "OTHER-001"
        with self.assertRaises(ValueError) as cm:
            cli.validate_task_eligibility(other_task)
        self.assertIn("limited to DEV502-TRACE-001", str(cm.exception))

        # Allow unexpected task ID with allow_any_task flag
        p, _ = cli.validate_task_eligibility(other_task, allow_any_task=True)
        self.assertIsNotNone(p)

    def test_token_loading_mechanisms(self) -> None:
        """Verify token loading from file and stdin, and empty token rejection."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("my-sample-token-content")
            f_path = f.name

        try:
            token = cli.load_token(f_path, token_stdin=False)
            self.assertEqual(token, "my-sample-token-content")
        finally:
            os.unlink(f_path)

        # Stdin loading
        with patch("sys.stdin", io.StringIO("token-from-stdin\n")):
            token_stdin = cli.load_token(None, token_stdin=True)
            self.assertEqual(token_stdin, "token-from-stdin")

        # Missing token raises ValueError
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                cli.load_token(None, token_stdin=False)

    def test_local_grant_verification(self) -> None:
        """Verify local grant verification logic against trusted issuer keys."""
        trusted_keys = {self.signer_key_id: self.signer.public_key_base64url}
        signed_grant = self.signer.sign_grant(
            task_id=self.task_id,
            generation=3,
            policy=self.policy,
            actor_uid=self.operator_uid,
            now=self.now,
        )

        canonical_task_row = {
            **deepcopy(self.spec),
            "id": self.task_id,
            "generation": 3,
            "summary_zh": self.spec["summary"],
            "target_repo": "pantheon",
            "execution_resources": ["pantheon-dev"],
            "artifacts": ["docs/deployment/evidence/DEV502-TRACE-001/"],
            "dev_bridge": {
                "work_class": "hosted",
                "operator_authorization_required": True,
                "task_spec": deepcopy(self.spec),
                "task_spec_hash": self.policy["task_spec_hash"],
            },
        }

        with patch("scripts.request_execution_grant.datetime") as mock_dt:
            mock_dt.now.return_value = self.now + timedelta(seconds=5)
            fp = cli.verify_grant_locally(signed_grant, canonical_task_row, self.policy, trusted_keys)
            self.assertEqual(fp, self.signer.public_key_fingerprint)

        # Untrusted key fails verification
        wrong_keys = {"other-key": self.signer.public_key_base64url}
        with self.assertRaises(ea.ExecutionAuthorizationError):
            cli.verify_grant_locally(signed_grant, canonical_task_row, self.policy, wrong_keys)

    def test_end_to_end_prepare_and_request_flow(self) -> None:
        """Run full HTTP server and execute prepare and request flow."""
        service = ExecutionGrantIssuerService(
            verifier=self.verifier,
            signer=self.signer,
            challenge_store=ChallengeStore(),
            allowed_tasks=[self.task_id],
            allowed_environments=["pantheon-dev"],
        )
        server = create_issuer_server(service, host="127.0.0.1", port=0)
        port = server.server_port
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        issuer_url = f"http://127.0.0.1:{port}"
        token = self._mint_token()

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tf:
            tf.write(token)
            token_file = tf.name

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as gf:
            grant_file = gf.name

        config_data = {
            "execution_authorization": {
                "mfa_issuer_public_keys": {
                    self.signer_key_id: self.signer.public_key_base64url
                }
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as cf:
            json.dump(config_data, cf)
            config_file = cf.name

        try:
            # Mock fetch_canonical_task to return self.canonical_task
            with patch("scripts.request_execution_grant.fetch_canonical_task", return_value=self.canonical_task):
                # 1. Prepare
                prep_args = MagicMock(
                    task=self.task_id,
                    allowed_task=self.task_id,
                    allow_any_task=False,
                    out=None,
                )
                with patch("sys.stdout", new=io.StringIO()) as fake_out:
                    cli.cmd_prepare(prep_args)
                    prep_json = json.loads(fake_out.getvalue())
                    self.assertEqual(prep_json["task_id"], self.task_id)

                # 2. Request with local verification and output to file
                req_args = MagicMock(
                    task=self.task_id,
                    allowed_task=self.task_id,
                    allow_any_task=False,
                    issuer_url=issuer_url,
                    token_file=token_file,
                    token_stdin=False,
                    config_file=config_file,
                    grant_out=grant_file,
                    submit=False,
                )

                canonical_task_row = {
                    **deepcopy(self.spec),
                    "id": self.task_id,
                    "generation": 3,
                    "summary_zh": self.spec["summary"],
                    "target_repo": "pantheon",
                    "execution_resources": ["pantheon-dev"],
                    "artifacts": ["docs/deployment/evidence/DEV502-TRACE-001/"],
                    "dev_bridge": {
                        "work_class": "hosted",
                        "operator_authorization_required": True,
                        "task_spec": deepcopy(self.spec),
                        "task_spec_hash": self.policy["task_spec_hash"],
                    },
                    "execution_authorization": {
                        "state": "pending_authorization",
                        "policy": self.policy,
                    },
                }

                with patch("scripts.request_execution_grant.fetch_canonical_task", return_value=canonical_task_row):
                    cli.cmd_request(req_args)

                saved_grant = json.loads(Path(grant_file).read_text(encoding="utf-8"))
                self.assertEqual(saved_grant["task_id"], self.task_id)
                self.assertEqual(saved_grant["audience"], self.task_id)
                self.assertEqual(saved_grant["signature"]["key_id"], self.signer_key_id)
        finally:
            server.shutdown()
            server.server_close()
            for p in (token_file, grant_file, config_file):
                if os.path.exists(p):
                    os.unlink(p)


if __name__ == "__main__":
    unittest.main()
