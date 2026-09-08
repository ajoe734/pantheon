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

        # Reject unexpected task ID
        other_task = deepcopy(self.canonical_task)
        other_task["id"] = "OTHER-001"
        with self.assertRaises(ValueError) as cm:
            cli.validate_task_eligibility(other_task)
        self.assertIn("limited to DEV502-TRACE-001", str(cm.exception))

        # Ensure bypasses like allow_any_task or allowed_task are NOT permitted
        with self.assertRaises(ValueError) as cm:
            cli.validate_task_eligibility(other_task, allow_any_task=True)
        self.assertIn("limited to DEV502-TRACE-001", str(cm.exception))

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

    def test_end_to_end_qualified_chain(self) -> None:
        """Run full HTTP server and execute real qualified show -> issuance -> verifier -> submit chain."""
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

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            token_file = td_path / "token.txt"
            token_file.write_text(token, encoding="utf-8")

            grant_file = td_path / "grant.json"
            config_file = td_path / "config.json"
            config_file.write_text(json.dumps({
                "execution_authorization": {
                    "mfa_issuer_public_keys": {
                        self.signer_key_id: self.signer.public_key_base64url
                    }
                }
            }), encoding="utf-8")

            # Create an isolated qualified command root with scripts/ai-status.sh
            cmd_root = td_path / "command_root"
            scripts_dir = cmd_root / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            ai_status_sh = scripts_dir / "ai-status.sh"
            submitted_file = td_path / "submitted_receipt.json"
            counter_file = td_path / "show_counter.txt"
            counter_file.write_text("0", encoding="utf-8")

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
            task_json_file = td_path / "task.json"
            task_json_file.write_text(json.dumps({"task": canonical_task_row}), encoding="utf-8")

            # Write ai-status.sh runner script
            ai_status_script = f"""#!/bin/sh
set -e
CMD="$1"
TASK="$2"
if [ "$CMD" = "show" ]; then
  COUNT=$(cat "{counter_file}")
  COUNT=$((COUNT + 1))
  echo "$COUNT" > "{counter_file}"
  cat "{task_json_file}"
  exit 0
elif [ "$CMD" = "execution-grant-submit" ]; then
  if [ "$AI_NAME" != "Human/Ops" ]; then
    echo "ERROR: AI_NAME must be Human/Ops" >&2
    exit 1
  fi
  echo "$EXECUTION_GRANT_JSON" > "{submitted_file}"
  exit 0
else
  echo "Unknown command: $CMD" >&2
  exit 1
fi
"""
            ai_status_sh.write_text(ai_status_script, encoding="utf-8")
            ai_status_sh.chmod(0o755)

            env_patch = {
                "PANTHEON_COMMAND_ROOT": str(cmd_root),
            }

            try:
                with patch.dict(os.environ, env_patch):
                    # 1. Prepare
                    prep_out_file = td_path / "prep.json"
                    prep_args = MagicMock(
                        task=self.task_id,
                        out=str(prep_out_file),
                    )
                    cli.cmd_prepare(prep_args)
                    prep_data = json.loads(prep_out_file.read_text(encoding="utf-8"))
                    self.assertEqual(prep_data["task_id"], self.task_id)
                    self.assertEqual(prep_data["generation"], 3)

                    # 2. Request with local verification and immediate submit
                    req_args = MagicMock(
                        task=self.task_id,
                        issuer_url=issuer_url,
                        token_file=str(token_file),
                        token_stdin=False,
                        config_file=str(config_file),
                        grant_out=str(grant_file),
                        submit=True,
                    )

                    with patch("sys.stdout", new=io.StringIO()) as fake_out:
                        cli.cmd_request(req_args)
                        stdout_str = fake_out.getvalue()

                    # Verify stdout does not contain raw bearer or secret credentials
                    self.assertNotIn("EXECUTION_GRANT_JSON=", stdout_str)
                    self.assertNotIn(token, stdout_str)

                    # Verify grant was saved to grant_file and permissions are 0600
                    self.assertTrue(grant_file.is_file())
                    self.assertEqual(os.stat(grant_file).st_mode & 0o777, 0o600)
                    saved_grant = json.loads(grant_file.read_text(encoding="utf-8"))
                    self.assertEqual(saved_grant["task_id"], self.task_id)
                    self.assertEqual(saved_grant["signature"]["key_id"], self.signer_key_id)

                    # Verify submit was executed and saved to submitted_file
                    self.assertTrue(submitted_file.is_file())
                    submitted_grant = json.loads(submitted_file.read_text(encoding="utf-8"))
                    self.assertEqual(submitted_grant["task_id"], self.task_id)

                    # Verify show was called twice: once at start, once right before submission
                    show_count = int(counter_file.read_text(encoding="utf-8").strip())
                    self.assertEqual(show_count, 3)  # 1 for prepare, 2 for request (initial + before submit)
            finally:
                server.shutdown()
                server.server_close()

    def test_empty_trust_rejects_before_request(self) -> None:
        """Verify that empty public trust aborts request before contacting issuer."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as cf:
            json.dump({"execution_authorization": {"mfa_issuer_public_keys": {}}}, cf)
            empty_cfg = cf.name

        try:
            req_args = MagicMock(
                task=self.task_id,
                issuer_url="http://127.0.0.1:8090",
                token_file=None,
                token_stdin=True,
                config_file=empty_cfg,
                grant_out=None,
                submit=False,
            )
            with patch("scripts.request_execution_grant.fetch_canonical_task", return_value=self.canonical_task), \
                 patch("scripts.request_execution_grant.post_json") as mock_post:
                with self.assertRaises(RuntimeError) as cm:
                    cli.cmd_request(req_args)
                self.assertIn("missing trusted mfa issuer", str(cm.exception).lower())
                mock_post.assert_not_called()
        finally:
            if os.path.exists(empty_cfg):
                os.unlink(empty_cfg)

    def test_grant_out_symlink_rejection_prevents_clobber(self) -> None:
        """Verify that writing grant to a symlink path fails closed without clobbering target."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            target = td_path / "sensitive-target.txt"
            target.write_text("DO_NOT_OVERWRITE", encoding="utf-8")

            symlink_path = td_path / "grant-out-link"
            symlink_path.symlink_to(target)

            with self.assertRaises(RuntimeError) as cm:
                cli.write_private_exclusive_json(symlink_path, {"marker": "INTRUDER_GRANT"})

            self.assertIn("already exists or is a symlink", str(cm.exception))
            self.assertEqual(target.read_text(encoding="utf-8"), "DO_NOT_OVERWRITE")

    def test_insecure_remote_http_and_userinfo_rejected(self) -> None:
        """Verify that remote plaintext HTTP, embedded userinfo, and fragments are rejected."""
        with self.assertRaises(ValueError) as cm:
            cli.validate_issuer_url("http://remote-server.invalid:8090/v1")
        self.assertIn("insecure http is only permitted for loopback", str(cm.exception).lower())

        with self.assertRaises(ValueError) as cm:
            cli.validate_issuer_url("https://user:password@secure.example.com/v1")
        self.assertIn("embedded userinfo", str(cm.exception).lower())

        with self.assertRaises(ValueError) as cm:
            cli.validate_issuer_url("http://127.0.0.1:8090/v1#fragment")
        self.assertIn("fragments", str(cm.exception).lower())

        # Valid loopback HTTP and remote HTTPS pass
        cli.validate_issuer_url("http://127.0.0.1:8090")
        cli.validate_issuer_url("http://localhost:8090")
        cli.validate_issuer_url("https://secure-issuer.pantheon.trade:8443")

    def test_refetch_canonical_detects_concurrent_change(self) -> None:
        """Verify that a concurrent modification to the canonical task aborts submission."""
        fake_grant = {
            "task_id": self.task_id,
            "audience": self.task_id,
            "signature": {"key_id": self.signer_key_id, "algorithm": "Ed25519", "value": "dummy"},
        }
        modified_task = deepcopy(self.canonical_task)
        modified_task["generation"] = 4  # Generation bumped concurrently

        req_args = MagicMock(
            task=self.task_id,
            issuer_url="http://127.0.0.1:8090",
            token_file=None,
            token_stdin=True,
            config_file=None,
            grant_out=None,
            submit=True,
        )

        with patch("scripts.request_execution_grant.fetch_canonical_task", side_effect=[self.canonical_task, modified_task]), \
             patch("scripts.request_execution_grant.load_trusted_keys", return_value={self.signer_key_id: "test"}), \
             patch("scripts.request_execution_grant.load_token", return_value="fake-token"), \
             patch("scripts.request_execution_grant.post_json", side_effect=[{"challenge_id": "c1"}, {"grant": fake_grant}]), \
             patch("scripts.request_execution_grant.verify_grant_locally", return_value="test-fp"), \
             patch("scripts.request_execution_grant.submit_grant_via_cli") as mock_submit:
            with self.assertRaises(RuntimeError) as cm:
                cli.cmd_request(req_args)
            self.assertIn("generation changed", str(cm.exception).lower())
            mock_submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
