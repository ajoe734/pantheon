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

import contextlib
import io
import json
import os
import stat
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

# Authoritative-mode task-state env vars are process-ambient in a real
# auto-worker session. This isolated fixture deliberately runs the qualified
# TaskStore in plain repo (non-authoritative) mode against a throwaway status
# root, so any inherited authoritative binding must be suspended for the
# duration of the direct ai_status.save_state/load_state calls below.
_AUTHORITATIVE_TASK_STATE_ENV_KEYS = (
    "PANTHEON_TASK_STATE_STORE_MODE",
    "PANTHEON_TASK_STATE_EVENT_LOG",
    "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON",
)


@contextlib.contextmanager
def _repo_mode_task_state_env():
    saved = {key: os.environ.pop(key, None) for key in _AUTHORITATIVE_TASK_STATE_ENV_KEYS}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def _render_qualified_taskstore_bridge(status_root: Path) -> str:
    """Render a real qualified TaskStore bridge for test_end_to_end_qualified_chain.

    Bootstraps the actual ``scripts/ai_status.py`` module against an
    isolated status root and dispatches to the real production
    ``command_show`` / ``command_execution_grant_submit`` implementations --
    not a hand-rolled stub that fabricates canned JSON. ``request_execution_grant.py``
    invokes this exactly like the real ``scripts/ai-status.sh`` (``argv[1]``
    is the command, e.g. ``show``, and the remaining args follow), so the
    isolated status root is baked into the rendered script rather than
    passed positionally.
    """
    return f"""#!/usr/bin/env python3
import sys
sys.path.insert(0, {str(ROOT_DIR)!r})
sys.path.insert(0, {str(ORCHESTRATOR_DIR)!r})
sys.dont_write_bytecode = True

import scripts.ai_status as ai_status

command = sys.argv[1] if len(sys.argv) > 1 else ""
args = sys.argv[2:]

root = ai_status.configure_status_root_paths({str(status_root)!r})
ai_status.CONFIG_FILE = root / ".orchestrator" / "config.json"

state = ai_status.load_state()
if command == "show":
    ai_status.command_show(state, args)
elif command == "execution-grant-submit":
    ai_status.command_execution_grant_submit(state, args)
    ai_status.save_state(state)
else:
    print(f"Unknown command: {{command}}", file=sys.stderr)
    sys.exit(1)
"""


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
        )
        # verify_token() delegates cryptographic verification to the real
        # firebase-admin SDK (see execution_grant_issuer/test_issuer.py for
        # SDK-level revoked/disabled/expired/invalid denial coverage). This
        # CLI test suite is only concerned with the scoped TRACE client's own
        # behavior, so the SDK call is deterministically stubbed to decode
        # the locally-minted test token's claims without live network access.
        self._verify_id_token_patch = patch(
            "execution_grant_issuer.token_verifier.firebase_auth.verify_id_token",
            side_effect=lambda token_str, app=None, check_revoked=False, clock_skew_seconds=0: jwt.decode(
                token_str,
                options={
                    "verify_signature": False,
                    "verify_aud": False,
                    "verify_iss": False,
                    "verify_exp": False,
                },
            ),
        )
        self._verify_id_token_patch.start()
        self.addCleanup(self._verify_id_token_patch.stop)

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
        """Run the full HTTP issuer and the real qualified TaskStore.

        Exercises the actual production ``scripts/ai_status.py`` ``show`` and
        ``execution-grant-submit`` commands (real state load/save, real
        ``execution_authorization.verify_execution_grant`` and nonce-ledger
        consumption) against an isolated status root -- not a hand-rolled
        shell script that fabricates canned JSON. Persistence is proven by
        reloading the state fresh from disk after submission.
        """
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
            token_file.chmod(0o600)

            grant_file = td_path / "grant.json"

            # Isolated qualified TaskStore status root: a real ai-status.json
            # seeded with one task, and a real .orchestrator/config.json
            # trust root -- both read by the actual scripts/ai_status.py.
            status_root = td_path / "status_root"
            (status_root / ".orchestrator").mkdir(parents=True, exist_ok=True)

            canonical_task_row = {
                **deepcopy(self.spec),
                "id": self.task_id,
                "generation": 3,
                "status": "todo",
                "owner": "Antigravity",
                "reviewer": "Codex",
                "summary_zh": self.spec["summary"],
                "target_repo": "pantheon",
                "execution_resources": ["pantheon-dev"],
                "artifacts": ["docs/deployment/evidence/DEV502-TRACE-001/"],
                "last_update": "2026-09-08T10:00:00Z",
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

            import scripts.ai_status as ai_status  # local import: isolates module-global mutation

            seed_state = ai_status.default_state()
            seed_state["tasks"] = [canonical_task_row]

            orig_status_root = ai_status.STATUS_ROOT
            orig_status_file = ai_status.STATUS_FILE
            orig_log_file = ai_status.LOG_FILE
            orig_current_work = ai_status.CURRENT_WORK_FILE
            orig_docs_site = ai_status.DOCS_SITE_DIR
            orig_orch_state = ai_status.ORCHESTRATOR_STATE_FILE
            orig_approval_queue = ai_status.APPROVAL_QUEUE_FILE
            orig_dashboard_bundle = ai_status.DASHBOARD_BUNDLE_FILE
            orig_config_file = ai_status.CONFIG_FILE
            try:
                ai_status.configure_status_root_paths(status_root)
                ai_status.CONFIG_FILE = status_root / ".orchestrator" / "config.json"
                ai_status.CONFIG_FILE.write_text(
                    json.dumps(
                        {
                            "execution_authorization": {
                                "mfa_issuer_public_keys": {
                                    self.signer_key_id: self.signer.public_key_base64url
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                with _repo_mode_task_state_env():
                    ai_status.save_state(seed_state)
            finally:
                ai_status.STATUS_ROOT = orig_status_root
                ai_status.STATUS_FILE = orig_status_file
                ai_status.LOG_FILE = orig_log_file
                ai_status.CURRENT_WORK_FILE = orig_current_work
                ai_status.DOCS_SITE_DIR = orig_docs_site
                ai_status.ORCHESTRATOR_STATE_FILE = orig_orch_state
                ai_status.APPROVAL_QUEUE_FILE = orig_approval_queue
                ai_status.DASHBOARD_BUNDLE_FILE = orig_dashboard_bundle
                ai_status.CONFIG_FILE = orig_config_file

            # Real qualified command root: scripts/ai-status.sh is a thin
            # bridge into the real scripts/ai_status.py show / execution-
            # grant-submit commands against the isolated status root above.
            cmd_root = td_path / "command_root"
            scripts_dir = cmd_root / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            ai_status_sh = scripts_dir / "ai-status.sh"
            ai_status_sh.write_text(
                _render_qualified_taskstore_bridge(status_root), encoding="utf-8"
            )
            ai_status_sh.chmod(0o755)

            env_patch = {
                "PANTHEON_COMMAND_ROOT": str(cmd_root),
            }

            try:
                with _repo_mode_task_state_env(), patch.dict(os.environ, env_patch):
                    # 1. Prepare (real qualified `show` against the isolated TaskStore)
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
                        config_file=str(status_root / ".orchestrator" / "config.json"),
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
            finally:
                server.shutdown()
                server.server_close()

            # 3. Reload the qualified TaskStore state fresh from disk (a new
            # process would see exactly this) and prove the real
            # execution_authorization gate was actually granted and durably
            # persisted -- not merely echoed back by a fake submit stub.
            try:
                ai_status.configure_status_root_paths(status_root)
                ai_status.CONFIG_FILE = status_root / ".orchestrator" / "config.json"
                with _repo_mode_task_state_env():
                    reloaded_state = ai_status.load_state()
            finally:
                ai_status.STATUS_ROOT = orig_status_root
                ai_status.STATUS_FILE = orig_status_file
                ai_status.LOG_FILE = orig_log_file
                ai_status.CURRENT_WORK_FILE = orig_current_work
                ai_status.DOCS_SITE_DIR = orig_docs_site
                ai_status.ORCHESTRATOR_STATE_FILE = orig_orch_state
                ai_status.APPROVAL_QUEUE_FILE = orig_approval_queue
                ai_status.DASHBOARD_BUNDLE_FILE = orig_dashboard_bundle
                ai_status.CONFIG_FILE = orig_config_file

            reloaded_task = next(t for t in reloaded_state["tasks"] if t["id"] == self.task_id)
            self.assertEqual(reloaded_task["execution_authorization"]["state"], "granted")
            self.assertEqual(
                reloaded_task["execution_authorization"]["grant"]["task_id"], self.task_id
            )
            ledger = reloaded_state.get("execution_authorization_consumed_grants") or {}
            self.assertEqual(len(ledger), 1)

            log_lines = (status_root / "ai-activity-log.jsonl").read_text(encoding="utf-8").splitlines()
            submit_entries = [
                json.loads(line) for line in log_lines if '"execution_grant_submitted"' in line
            ]
            self.assertEqual(len(submit_entries), 1)
            self.assertEqual(submit_entries[0]["agent"], "Human/Ops")

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
