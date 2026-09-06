#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest
import uuid
from itertools import islice
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import common


class StatusCommandRepositoryConfigTests(unittest.TestCase):
    def test_expected_remote_uses_coordination_registry(self) -> None:
        config = {
            "coordination": {
                "repositories": {
                    "pantheon": {"repo": "example/pantheon-fork"}
                }
            }
        }

        self.assertEqual(
            common.status_command_expected_remote(config),
            "example/pantheon-fork",
        )

    def test_expected_remote_has_pantheon_default(self) -> None:
        self.assertEqual(
            common.status_command_expected_remote({}),
            "ajoe734/pantheon",
        )


class WorkerSpawnAuthorityBoundaryTests(unittest.TestCase):
    def test_final_spawn_boundary_removes_control_plane_signing_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "worker.log"
            fake_process = mock.Mock(pid=321)
            with mock.patch.object(
                common.subprocess,
                "Popen",
                return_value=fake_process,
            ) as popen:
                common.spawn_background_process(
                    ["worker"],
                    log_path=log_path,
                    env={
                        "BRIDGE_SIGNING_KEY": "bridge-secret",
                        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": "{}",
                    },
                    runner_enabled=False,
                )
            spawned_env = popen.call_args.kwargs["env"]
            self.assertNotIn("BRIDGE_SIGNING_KEY", spawned_env)
            self.assertIn("BRIDGE_SIGNING_PUBLIC_KEYS_JSON", spawned_env)


def _sigkill_during_activity_rotation(log_path: str, point: str) -> None:
    os.environ["LOOP_TEST_ACTIVITY_ROTATION_SIGKILL_AFTER"] = point
    common.write_activity_log(
        {
            "paths": {
                "activity_log": log_path,
                "activity_log_rotate_bytes": 1,
            }
        },
        {
            "event_id": "child-event-must-not-commit",
            "type": "rotation_test",
        },
    )


def _probe_forked_stable_lock(lock_path: str, connection) -> None:
    try:
        with common.stable_sidecar_lock(
            lock_path,
            plane="task_state",
            shared=False,
            nonblocking=True,
        ):
            connection.send("acquired")
    except BlockingIOError:
        connection.send("blocked")
    finally:
        connection.close()


class RuntimeLogPathTests(unittest.TestCase):
    def test_governed_runtime_logs_use_external_status_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "status-root"
            candidate_orchestrator = Path(tmpdir) / "candidate" / ".orchestrator"
            with mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_ROOT": str(status_root)},
                clear=False,
            ), mock.patch.object(common, "ORCHESTRATOR_DIR", candidate_orchestrator):
                log_path = common.runtime_log_path("codex", "Codex2")

        self.assertEqual(log_path.parent, status_root / ".orchestrator" / "logs")
        self.assertNotIn(candidate_orchestrator, log_path.parents)

    def test_governed_runtime_evidence_uses_external_status_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "status-root"
            candidate_orchestrator = Path(tmpdir) / "candidate" / ".orchestrator"
            with mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_ROOT": str(status_root)},
                clear=False,
            ), mock.patch.object(common, "ORCHESTRATOR_DIR", candidate_orchestrator):
                evidence_path = common.evidence_dir({})

        self.assertEqual(evidence_path, status_root / ".orchestrator" / "evidence")
        self.assertNotIn(candidate_orchestrator, evidence_path.parents)

    def test_local_runtime_logs_keep_repository_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator_dir = Path(tmpdir) / ".orchestrator"
            with mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_ROOT": ""},
                clear=False,
            ), mock.patch.object(common, "ORCHESTRATOR_DIR", orchestrator_dir):
                log_path = common.runtime_log_path("codex", "Codex2")

        self.assertEqual(log_path.parent, orchestrator_dir / "logs")

    def test_governed_runtime_logs_use_config_status_root_without_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "status-root"
            candidate_orchestrator = Path(tmpdir) / "candidate" / ".orchestrator"
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}
            with mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_ROOT": ""},
                clear=False,
            ), mock.patch.object(common, "ORCHESTRATOR_DIR", candidate_orchestrator):
                log_path = common.runtime_log_path(
                    "codex", "Codex2", config=config
                )

        self.assertEqual(log_path.parent, status_root / ".orchestrator" / "logs")
        self.assertNotIn(candidate_orchestrator, log_path.parents)

    def test_local_runtime_evidence_keeps_repository_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator_dir = Path(tmpdir) / ".orchestrator"
            with mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_ROOT": ""},
                clear=False,
            ), mock.patch.object(common, "ORCHESTRATOR_DIR", orchestrator_dir):
                evidence_path = common.evidence_dir({})

        self.assertEqual(evidence_path, orchestrator_dir / "evidence")


class AgentConfigurationTests(unittest.TestCase):
    def test_registered_agent_keeps_its_explicit_delivery_identity(self) -> None:
        agent = common.agent_config_for(
            {
                "agents": {
                    "codex2": {
                        "provider": "codex_shared",
                        "adapter": "codex",
                    }
                }
            },
            "Codex2",
        )

        self.assertEqual(agent["id"], "codex2")
        self.assertEqual(agent["provider"], "codex_shared")
        self.assertEqual(agent["adapter"], "codex")

    def test_unregistered_agent_fails_closed_without_provider_inference(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "agent configuration is required for delivery identity: 'codex2'",
        ):
            common.agent_config_for({"agents": {}}, "Codex2")


class JsonLoadResilienceTests(unittest.TestCase):
    def test_load_json_still_allows_empty_optional_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "optional.json"
            path.write_text("", encoding="utf-8")

            result = common.load_json(path, default={"fallback": True})

        self.assertEqual(result, {"fallback": True})

    def test_load_status_rejects_empty_authoritative_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = Path(tmpdir) / "ai-status.json"
            status_file.write_text("", encoding="utf-8")
            event_log = Path(tmpdir).parent / f"{Path(tmpdir).name}-runtime" / "task-state-events.jsonl"

            with self.assertRaisesRegex(RuntimeError, "journal is empty"):
                common.load_status(
                    {
                        "paths": {"status_file": str(status_file)},
                        "task_state_store": {
                            "mode": "authoritative",
                            "event_log": str(event_log),
                        },
                    }
                )

    def test_ai_status_rejects_empty_journal_without_initializing_projection(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "status"
            status_root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=status_root, check=True)
            status_file = status_root / "ai-status.json"
            status_file.write_text("", encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("PANTHEON_", "ORCH_"))}
            for env_name in (
                "PANTHEON_WORKTREE_ROOT",
                "ORCH_WORKSPACE_PATH",
                "ORCH_RUN_ID",
                "ORCH_TASK_ID",
                "ORCH_RUNNER_STATUS_PATH",
                "ORCH_HEARTBEAT_PATH",
                "PANTHEON_COMMAND_ROOT",
                "PANTHEON_COMMAND_RUNTIME_SHA",
                "PANTHEON_COMMAND_REMOTE",
                "PANTHEON_COMMAND_BASE_REF",
            ):
                env.pop(env_name, None)
            env["PANTHEON_STATUS_ROOT"] = str(status_root)
            env["AI_NAME"] = "Codex2"
            env.update(common.task_state_store_runtime_env({
                "paths": {"status_file": str(status_file)},
                "task_state_store": {"mode": "authoritative", "event_log": str(Path(tmpdir) / "runtime" / "tasks.jsonl")},
            }))


            result = subprocess.run(
                [sys.executable, str(repo_root / "scripts" / "ai_status.py"), "show", "EMPTY"],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            self.assertEqual(status_file.read_bytes(), b"", "rejected command must not initialize the projection")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Authoritative task-state journal is empty; refusing ai-status.json fallback", result.stderr + result.stdout)

    def test_load_json_retries_after_transient_decode_error(self) -> None:
        payload = {"ok": True}
        with (
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(Path, "read_text", side_effect=['{"broken": 1}{"extra": 2}', json.dumps(payload)]),
            mock.patch.object(common.time, "sleep") as sleep,
        ):
            result = common.load_json(Path("/tmp/transient.json"), default={})

        self.assertEqual(result, payload)
        sleep.assert_called_once()

    def test_load_jsonl_retries_after_transient_decode_error(self) -> None:
        with (
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(
                Path,
                "read_text",
                side_effect=['{"id": 1}{"id": 2}\n', '{"id": 1}\n{"id": 2}\n'],
            ),
            mock.patch.object(common.time, "sleep") as sleep,
        ):
            rows = common.load_jsonl(Path("/tmp/transient.jsonl"))

        self.assertEqual(rows, [{"id": 1}, {"id": 2}])
        sleep.assert_called_once()


class FailureSummaryTests(unittest.TestCase):
    def test_summarize_failure_reason_treats_claude_credit_balance_as_quota(self) -> None:
        result = common.summarize_failure_reason("Credit balance is too low", "Claude")

        self.assertEqual(result["kind"], "quota")
        self.assertEqual(result["summary"], "Credit balance is too low")

    def test_summarize_failure_reason_treats_github_cli_auth_as_tool_auth(self) -> None:
        result = common.summarize_failure_reason("GitHub CLI is not authenticated. Run gh auth login.", "Claude2")

        self.assertEqual(result["kind"], "tool_auth")
        self.assertEqual(result["summary"], "GitHub CLI auth unavailable")

    def test_summarize_failure_reason_treats_codex_usage_limit_as_quota(self) -> None:
        result = common.summarize_failure_reason(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
            "Codex",
        )

        self.assertEqual(result["kind"], "quota")
        self.assertEqual(result["summary"], "Codex usage limit reached")

    def test_summarize_failure_reason_treats_codex_revoked_token_as_auth(self) -> None:
        for reason in (
            "ERROR: Your access token could not be refreshed because your refresh token was revoked. Please log out and sign in again.",
            'Failed to refresh token: 401 Unauthorized: {"error": {"code": "refresh_token_invalidated"}}',
            '{"error": {"message": "Your authentication token has been invalidated.", "code": "token_invalidated", "status": 401}}',
            "codex_api::endpoint::responses_websocket: failed to connect to websocket: HTTP error: 401 Unauthorized",
        ):
            with self.subTest(reason=reason):
                result = common.summarize_failure_reason(reason, "Codex2")
                self.assertEqual(result["kind"], "auth")
                self.assertEqual(result["summary"], "Authentication failure")

    def test_summarize_failure_reason_does_not_treat_bare_401_narrative_as_auth(self) -> None:
        # A chair narrative that merely mentions a 401 must not be misclassified.
        result = common.summarize_failure_reason(
            "The chair noted the BFF returned a 401 for the unauthenticated probe, which is expected.",
            "Claude",
        )

        self.assertNotEqual(result["kind"], "auth")


class GithubCliEnvTests(unittest.TestCase):
    def test_preserve_github_cli_auth_env_keeps_source_config_when_home_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            gh_config = root / ".config" / "gh"
            gh_config.mkdir(parents=True)
            env = {"HOME": str(root / ".claude2")}

            common.preserve_github_cli_auth_env(env, {"HOME": str(root)})

        self.assertEqual(env["GH_CONFIG_DIR"], str(gh_config))

    def test_preserve_github_cli_auth_env_respects_explicit_config_dir(self) -> None:
        env = {"GH_CONFIG_DIR": "~/custom-gh"}

        common.preserve_github_cli_auth_env(env, {"HOME": "/tmp/ignored"})

        self.assertEqual(env["GH_CONFIG_DIR"], str(Path("~/custom-gh").expanduser()))


class ClaudeAuthTests(unittest.TestCase):
    def test_claude_auth_ready_accepts_long_lived_oauth_token_env(self) -> None:
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-test-token"}

        with (
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=None),
            mock.patch.object(common, "run_command") as run_command,
        ):
            self.assertTrue(common.claude_auth_ready("claude", env=env))

        run_command.assert_not_called()

    def test_claude_auth_ready_refreshes_expired_env_oauth_token(self) -> None:
        env = {"HOME": "/tmp/test-home", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-old"}
        expired_oauth = {
            "accessToken": "sk-ant-oat01-old",
            "refreshToken": "old-refresh",
            "expiresAt": 1,
            "scopes": ["user:profile"],
        }
        refreshed_oauth = {
            "accessToken": "sk-ant-oat01-new",
            "refreshToken": "new-refresh",
            "expiresAt": int(common.time.time() * 1000) + 3_600_000,
            "scopes": ["user:profile", "user:inference"],
        }
        with (
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=({}, expired_oauth, Path("/tmp/.credentials.json"))),
            mock.patch.object(common, "refresh_claude_oauth_tokens", return_value=refreshed_oauth) as refresh,
            mock.patch.object(common, "run_command") as run_command,
        ):
            self.assertTrue(common.claude_auth_ready("claude", env=env))

        refresh.assert_called_once_with(env, account_lock_key=None)
        run_command.assert_not_called()
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-new")

    def test_claude_auth_ready_prefers_fresh_credentials_over_stale_env_token(self) -> None:
        env = {"HOME": "/tmp/test-home", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-old"}
        fresh_oauth = {
            "accessToken": "sk-ant-oat01-new",
            "refreshToken": "new-refresh",
            "expiresAt": int(common.time.time() * 1000) + 3_600_000,
            "scopes": ["user:profile", "user:inference"],
        }
        with (
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=({}, fresh_oauth, Path("/tmp/.credentials.json"))),
            mock.patch.object(common, "refresh_claude_oauth_tokens") as refresh,
            mock.patch.object(common, "run_command") as run_command,
        ):
            self.assertTrue(common.claude_auth_ready("claude", env=env))

        refresh.assert_not_called()
        run_command.assert_not_called()
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-new")

    def test_claude_auth_ready_accepts_distinct_long_lived_env_token_when_oauth_expired(self) -> None:
        env = {"HOME": "/tmp/test-home", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-long-lived"}
        expired_oauth = {
            "accessToken": "sk-ant-oat01-expired",
            "refreshToken": "old-refresh",
            "expiresAt": 1,
            "scopes": ["user:profile"],
        }
        with (
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=({}, expired_oauth, Path("/tmp/.credentials.json"))),
            mock.patch.object(common, "refresh_claude_oauth_tokens") as refresh,
            mock.patch.object(common, "run_command") as run_command,
        ):
            self.assertTrue(common.claude_auth_ready("claude", env=env))

        refresh.assert_not_called()
        run_command.assert_not_called()
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-long-lived")

    def test_claude_auth_ready_refreshes_expired_oauth(self) -> None:
        env = {"HOME": "/tmp/test-home"}
        status = mock.Mock(returncode=0, stdout=json.dumps({"loggedIn": True}))
        expired_oauth = {
            "accessToken": "old-access",
            "refreshToken": "old-refresh",
            "expiresAt": 1,
            "scopes": ["user:profile"],
        }
        refreshed_oauth = {
            "accessToken": "new-access",
            "refreshToken": "new-refresh",
            "expiresAt": int(common.time.time() * 1000) + 3_600_000,
            "scopes": ["user:profile", "user:inference"],
        }
        with (
            mock.patch.object(common, "run_command", return_value=status),
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=({}, expired_oauth, Path("/tmp/.credentials.json"))),
            mock.patch.object(common, "refresh_claude_oauth_tokens", return_value=refreshed_oauth) as refresh,
        ):
            self.assertTrue(common.claude_auth_ready("claude", env=env))
        refresh.assert_called_once_with(env, account_lock_key=None)

    def test_claude_auth_ready_fails_when_refresh_of_expired_oauth_fails(self) -> None:
        status = mock.Mock(returncode=0, stdout=json.dumps({"loggedIn": True}))
        expired_oauth = {
            "accessToken": "old-access",
            "refreshToken": "old-refresh",
            "expiresAt": 1,
        }
        with (
            mock.patch.object(common, "run_command", return_value=status),
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=({}, expired_oauth, Path("/tmp/.credentials.json"))),
            mock.patch.object(common, "refresh_claude_oauth_tokens", return_value=None),
        ):
            self.assertFalse(common.claude_auth_ready("claude"))

    def test_refresh_claude_oauth_tokens_updates_credentials_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / ".claude" / ".credentials.json"
            credentials_path.parent.mkdir(parents=True)
            credentials_path.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "old-access",
                            "refreshToken": "old-refresh",
                            "expiresAt": 1,
                            "scopes": ["user:profile"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            class _Response:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self):
                    return json.dumps(
                        {
                            "access_token": "new-access",
                            "refresh_token": "new-refresh",
                            "expires_in": 3600,
                            "scope": "user:profile user:inference",
                        }
                    ).encode("utf-8")

            with mock.patch.object(common.urllib.request, "urlopen", return_value=_Response()):
                refreshed = common.refresh_claude_oauth_tokens({"HOME": tmpdir})

            self.assertIsNotNone(refreshed)
            stored = json.loads(credentials_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["claudeAiOauth"]["accessToken"], "new-access")
            self.assertEqual(stored["claudeAiOauth"]["refreshToken"], "new-refresh")
            self.assertEqual(stored["claudeAiOauth"]["scopes"], ["user:profile", "user:inference"])

    def test_refresh_claude_oauth_tokens_returns_none_on_http_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / ".claude" / ".credentials.json"
            credentials_path.parent.mkdir(parents=True)
            credentials_path.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "old-access",
                            "refreshToken": "old-refresh",
                            "expiresAt": 1,
                            "scopes": ["user:profile"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(
                common.urllib.request,
                "urlopen",
                side_effect=HTTPError(common.CLAUDE_OAUTH_TOKEN_URL, 401, "bad", hdrs=None, fp=None),
            ):
                refreshed = common.refresh_claude_oauth_tokens({"HOME": tmpdir})

            self.assertIsNone(refreshed)

    def test_refresh_claude_oauth_tokens_serializes_same_account_lock_key(self) -> None:
        # Two distinct CLI identities (separate credentials files) that share
        # one Anthropic account must never have their refresh network calls
        # overlap in time, since concurrent refreshes against a shared
        # account have been observed to trip rate limiting.
        intervals: list[tuple[float, float]] = []
        intervals_lock = threading.Lock()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
                ).encode("utf-8")

        def slow_urlopen(*_args, **_kwargs):
            start = time.monotonic()
            time.sleep(0.15)
            with intervals_lock:
                intervals.append((start, time.monotonic()))
            return _Response()

        def make_credentials(tmpdir: str) -> None:
            path = Path(tmpdir) / ".claude" / ".credentials.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "old-access",
                            "refreshToken": "old-refresh",
                            "expiresAt": 1,
                            "scopes": ["user:profile"],
                        }
                    }
                ),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as tmpdir_a, tempfile.TemporaryDirectory() as tmpdir_b:
            make_credentials(tmpdir_a)
            make_credentials(tmpdir_b)
            lock_key = f"test-shared-account-{uuid.uuid4()}"
            results: list[dict | None] = [None, None]

            def worker(index: int, home: str) -> None:
                results[index] = common.refresh_claude_oauth_tokens(
                    {"HOME": home}, account_lock_key=lock_key
                )

            with mock.patch.object(common.urllib.request, "urlopen", side_effect=slow_urlopen):
                threads = [
                    threading.Thread(target=worker, args=(0, tmpdir_a)),
                    threading.Thread(target=worker, args=(1, tmpdir_b)),
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)

            self.assertTrue(all(results))
            self.assertEqual(len(intervals), 2)
            (start_a, end_a), (start_b, end_b) = intervals
            overlapped = start_a < end_b and start_b < end_a
            self.assertFalse(overlapped, f"refresh calls overlapped: {intervals}")

    def test_refresh_claude_oauth_tokens_without_lock_key_is_unserialized(self) -> None:
        # account_lock_key is optional and defaults to no locking, matching
        # every existing caller that predates this parameter.
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / ".claude" / ".credentials.json"
            credentials_path.parent.mkdir(parents=True)
            credentials_path.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "old-access",
                            "refreshToken": "old-refresh",
                            "expiresAt": 1,
                            "scopes": ["user:profile"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            class _Response:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self):
                    return json.dumps(
                        {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
                    ).encode("utf-8")

            with mock.patch.object(common.urllib.request, "urlopen", return_value=_Response()):
                refreshed = common.refresh_claude_oauth_tokens({"HOME": tmpdir})

            self.assertIsNotNone(refreshed)
            self.assertEqual(refreshed["accessToken"], "new-access")


class StableCanonicalLockPathTests(unittest.TestCase):
    def test_data_leaf_symlinks_are_rejected_before_lock_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target_dir = root / "target"
            target_dir.mkdir()
            cases = (
                (
                    common.canonical_task_state_lock_path,
                    root / "ai-status.json",
                    target_dir / "ai-status.json",
                    "task-state",
                ),
                (
                    common.activity_audit_lock_path,
                    root / "ai-activity-log.jsonl",
                    target_dir / "ai-activity-log.jsonl",
                    "activity-audit",
                ),
            )
            for lock_path_for, data_path, target, plane in cases:
                with self.subTest(plane=plane):
                    target.write_text("{}\n", encoding="utf-8")
                    data_path.symlink_to(target)
                    with self.assertRaisesRegex(
                        RuntimeError,
                        rf"canonical {plane} data path contains a symlink",
                    ):
                        lock_path_for(data_path)

    def test_lock_paths_reject_parent_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            real_root = root / "real-status-root"
            real_root.mkdir()
            alias_root = root / "status-root-alias"
            alias_root.symlink_to(real_root, target_is_directory=True)

            for lock_path_for, leaf in (
                (common.canonical_task_state_lock_path, "ai-status.json"),
                (common.activity_audit_lock_path, "ai-activity-log.jsonl"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "path contains a symlink",
                ):
                    lock_path_for(alias_root / leaf)

    def test_stable_sidecar_rejects_a_symlink_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "foreign.lock"
            target.touch()
            sidecar = root / "task-state.lock"
            sidecar.symlink_to(target)

            with self.assertRaisesRegex(
                RuntimeError,
                "stable lock sidecar cannot be a symlink",
            ):
                with common.stable_sidecar_lock(
                    sidecar,
                    plane="task_state",
                    shared=False,
                    nonblocking=False,
                ):
                    self.fail("symlinked sidecar must never be acquired")

    def test_stable_sidecar_resolves_a_parent_alias_and_creates_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            real_root = root / "real-root"
            real_root.mkdir()
            alias_root = root / "root-alias"
            alias_root.symlink_to(real_root, target_is_directory=True)
            requested = alias_root / "activity-audit.lock"
            expected = real_root / "activity-audit.lock"

            with common.stable_sidecar_lock(
                requested,
                plane="activity_audit",
                shared=False,
                nonblocking=False,
            ):
                self.assertTrue(expected.is_file())
                self.assertFalse(expected.is_symlink())

    def test_stable_sidecar_rejects_path_swap_after_flock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lock_path = root / "task-state.lock"
            replacement = root / "replacement.lock"
            replacement.touch()
            real_flock = common.fcntl.flock

            def swap_after_flock(descriptor: int, operation: int) -> None:
                real_flock(descriptor, operation)
                os.replace(replacement, lock_path)

            with (
                mock.patch.object(common.fcntl, "flock", side_effect=swap_after_flock),
                self.assertRaisesRegex(
                    RuntimeError,
                    "stable lock sidecar changed while opening",
                ),
            ):
                with common.stable_sidecar_lock(
                    lock_path,
                    plane="task_state",
                    shared=False,
                    nonblocking=False,
                ):
                    self.fail("replaced sidecar pathname must never be admitted")

    def test_stable_sidecar_identity_validation_eagain_is_propagated(self) -> None:
        """Verify that an EAGAIN OSError raised from the post-flock identity validation
        is propagated and NOT converted to LockContentionError."""
        import errno
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lock_path = root / "task-state.lock"

            call_count = 0
            def fake_assert(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    raise OSError(errno.EAGAIN, "EAGAIN during validation")

            mock_flock = mock.Mock(side_effect=common.fcntl.flock)

            with (
                mock.patch("common._assert_stable_lock_identity", fake_assert),
                mock.patch.object(common.fcntl, "flock", mock_flock),
                self.assertRaises(OSError) as ctx,
            ):
                with common.stable_sidecar_lock(
                    lock_path,
                    plane="task_state",
                    shared=False,
                    nonblocking=True,
                ):
                    self.fail("should not reach here")
            self.assertEqual(ctx.exception.errno, errno.EAGAIN)
            self.assertNotIsInstance(ctx.exception, common.LockContentionError)
            mock_flock.assert_called()

    def test_pid_change_resets_inherited_thread_local_state(self) -> None:
        fake_handle = mock.Mock()
        common._STABLE_LOCK_LOCAL.held = {
            "inherited": {
                "handle": fake_handle,
                "depth": 1,
                "rank": 1,
                "plane": "runtime_admission",
                "shared": False,
            }
        }
        common._STABLE_LOCK_LOCAL.stack = ["inherited"]
        common._STABLE_LOCK_LOCAL.pid = 100
        try:
            with mock.patch.object(common.os, "getpid", return_value=200):
                held, stack = common._stable_lock_state()
            fake_handle.close.assert_called_once_with()
            self.assertEqual(held, {})
            self.assertEqual(stack, [])
            self.assertEqual(common._STABLE_LOCK_LOCAL.pid, 200)
        finally:
            common._STABLE_LOCK_LOCAL.held = {}
            common._STABLE_LOCK_LOCAL.stack = []
            common._STABLE_LOCK_LOCAL.pid = os.getpid()

    def test_forked_child_does_not_reenter_inherited_thread_local_lock(self) -> None:
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "task-state.lock"
            parent_connection, child_connection = context.Pipe(duplex=False)
            with common.stable_sidecar_lock(
                lock_path,
                plane="task_state",
                shared=False,
                nonblocking=False,
            ):
                process = context.Process(
                    target=_probe_forked_stable_lock,
                    args=(str(lock_path), child_connection),
                )
                process.start()
                child_connection.close()
                self.assertTrue(
                    parent_connection.poll(5),
                    "forked lock probe did not return",
                )
                self.assertEqual(parent_connection.recv(), "blocked")
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
                    self.fail("forked lock probe hung")
                self.assertEqual(process.exitcode, 0)
            parent_connection.close()


class ActivityAuditRecoveryTests(unittest.TestCase):
    def _leave_pending_rotation(self, log_path: Path) -> dict:
        context = multiprocessing.get_context("fork")
        process = context.Process(
            target=_sigkill_during_activity_rotation,
            args=(str(log_path), "intent"),
        )
        process.start()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
            self.fail("rotation child hung while creating pending intent")
        self.assertEqual(process.exitcode, -9)
        intent_path = common.activity_rotation_intent_path(log_path)
        self.assertTrue(intent_path.exists())
        return json.loads(intent_path.read_text(encoding="utf-8"))

    def _audit_rows(self, log_path: Path) -> list[dict]:
        rows: list[dict] = []
        with common.activity_audit_lock_file(
            log_path,
            shared=True,
            nonblocking=False,
        ):
            for source in common.activity_audit_source_paths_unlocked(log_path):
                if source.suffix == ".gz":
                    with gzip.open(source, "rt", encoding="utf-8") as handle:
                        text = handle.read()
                else:
                    text = source.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if not line:
                        continue
                    row = json.loads(line)
                    if common._is_activity_lineage_head(row):
                        continue
                    rows.append(row)
        return rows

    def test_sigkill_at_each_rotation_boundary_recovers_exactly_once(self) -> None:
        context = multiprocessing.get_context("fork")
        for point in (
            "stage_archive",
            "stage_tail",
            "intent",
            "archive",
            "tail",
            "lineage",
        ):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as tmpdir:
                log_path = Path(tmpdir) / "ai-activity-log.jsonl"
                original = [
                    {
                        "event_id": f"original-{index}",
                        "payload": "x" * 256,
                    }
                    for index in range(3)
                ]
                log_path.write_text(
                    "".join(json.dumps(row) + "\n" for row in original),
                    encoding="utf-8",
                )
                process = context.Process(
                    target=_sigkill_during_activity_rotation,
                    args=(str(log_path), point),
                )
                process.start()
                process.join(timeout=10)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
                    self.fail(f"rotation child hung at {point}")
                self.assertEqual(process.exitcode, -9)

                common.write_activity_log(
                    {
                        "paths": {
                            "activity_log": str(log_path),
                            "activity_log_rotate_bytes": 1,
                        }
                    },
                    {
                        "event_id": "restart-event",
                        "type": "rotation_test",
                    },
                )

                rows = self._audit_rows(log_path)
                counts: dict[str, int] = {}
                for row in rows:
                    event_id = str(row.get("event_id") or "")
                    counts[event_id] = counts.get(event_id, 0) + 1
                self.assertEqual(
                    counts,
                    {
                        "original-0": 1,
                        "original-1": 1,
                        "original-2": 1,
                        "restart-event": 1,
                    },
                )
                self.assertFalse(
                    common.activity_rotation_intent_path(log_path).exists()
                )

    def test_rotation_recovery_rejects_symlinked_intent_and_stage_leaves(self) -> None:
        for target in ("intent", "archive_stage", "tail_stage"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmpdir:
                log_path = Path(tmpdir) / "ai-activity-log.jsonl"
                log_path.write_text(
                    "".join(
                        json.dumps(
                            {
                                "event_id": f"original-{index}",
                                "payload": "x" * 256,
                            }
                        )
                        + "\n"
                        for index in range(3)
                    ),
                    encoding="utf-8",
                )
                intent = self._leave_pending_rotation(log_path)
                transaction_id = str(intent["transaction_id"])
                stage_archive, stage_tail = common._activity_rotation_stage_paths(
                    log_path,
                    transaction_id,
                )
                target_path = {
                    "intent": common.activity_rotation_intent_path(log_path),
                    "archive_stage": stage_archive,
                    "tail_stage": stage_tail,
                }[target]
                external = Path(tmpdir) / f"external-{target}"
                external.write_bytes(target_path.read_bytes())
                target_path.unlink()
                target_path.symlink_to(external)

                with self.assertRaisesRegex(
                    RuntimeError,
                    "stable regular file|path contains a symlink",
                ):
                    common.write_activity_log(
                        {
                            "paths": {
                                "activity_log": str(log_path),
                                "activity_log_rotate_bytes": 1_000_000,
                            }
                        },
                        {
                            "event_id": "must-not-append",
                            "type": "rotation_test",
                        },
                    )

    def test_rotation_guard_rejects_append_behind_pending_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "ai-activity-log.jsonl"
            log_path.write_text(
                "".join(
                    json.dumps(
                        {
                            "event_id": f"original-{index}",
                            "payload": "x" * 256,
                        }
                    )
                    + "\n"
                    for index in range(3)
                ),
                encoding="utf-8",
            )
            intent = self._leave_pending_rotation(log_path)
            intent_path = common.activity_rotation_intent_path(log_path)
            original_log = log_path.read_bytes()
            original_intent = intent_path.read_bytes()

            with (
                mock.patch.dict(
                    os.environ,
                    {common.ACTIVITY_ROTATION_WRITER_GUARD_ENV: "1"},
                ),
                self.assertRaisesRegex(RuntimeError, "recovery is pending"),
            ):
                common.write_activity_log(
                    {
                        "paths": {
                            "activity_log": str(log_path),
                            "activity_log_rotate_bytes": 1_000_000,
                        }
                    },
                    {
                        "event_id": "must-not-append",
                        "type": "rotation_test",
                    },
                )

            self.assertEqual(log_path.read_bytes(), original_log)
            self.assertEqual(intent_path.read_bytes(), original_intent)
            self.assertEqual(
                json.loads(intent_path.read_text(encoding="utf-8")),
                intent,
            )

    def test_append_below_rotation_threshold_does_not_scan_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "ai-activity-log.jsonl"
            log_path.write_text('{"event_id":"old"}\n', encoding="utf-8")

            with mock.patch.object(
                common,
                "activity_audit_source_paths_unlocked",
                side_effect=AssertionError("history must stay unopened"),
            ):
                common.write_activity_log(
                    {
                        "paths": {
                            "activity_log": str(log_path),
                            "activity_log_rotate_bytes": 1024 * 1024,
                        }
                    },
                    {"event_id": "new", "type": "bounded_append_test"},
                )

            self.assertEqual(
                [row["event_id"] for row in self._audit_rows(log_path)],
                ["old", "new"],
            )

    def test_append_above_rotation_threshold_still_scans_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "ai-activity-log.jsonl"
            log_path.write_text(
                json.dumps({"event_id": "old", "payload": "x" * 256}) + "\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    common,
                    "activity_audit_source_paths_unlocked",
                    side_effect=RuntimeError("history validation marker"),
                ),
                self.assertRaisesRegex(RuntimeError, "history validation marker"),
            ):
                common.write_activity_log(
                    {
                        "paths": {
                            "activity_log": str(log_path),
                            "activity_log_rotate_bytes": 1,
                        }
                    },
                    {"event_id": "must-not-append", "type": "rotation_test"},
                )

            self.assertNotIn("must-not-append", log_path.read_text(encoding="utf-8"))

    def test_interrupted_non_newline_tail_is_repaired_before_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "ai-activity-log.jsonl"
            log_path.write_bytes(
                b'{"event_id":"old"}\n{"event_id":"partial"'
            )

            common.write_activity_log(
                {
                    "paths": {
                        "activity_log": str(log_path),
                        "activity_log_rotate_bytes": 1024 * 1024,
                    }
                },
                {"event_id": "new", "type": "tail_recovery_test"},
            )

            rows = self._audit_rows(log_path)
            self.assertEqual(
                [row["event_id"] for row in rows],
                ["old", "new"],
            )

    def test_complete_json_without_newline_is_preserved_before_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "ai-activity-log.jsonl"
            log_path.write_bytes(b'{"event_id":"old"}')

            common.write_activity_log(
                {
                    "paths": {
                        "activity_log": str(log_path),
                        "activity_log_rotate_bytes": 1024 * 1024,
                    }
                },
                {"event_id": "new", "type": "tail_recovery_test"},
            )

            rows = self._audit_rows(log_path)
            self.assertEqual(
                [row["event_id"] for row in rows],
                ["old", "new"],
            )

    def test_symlinked_rotated_archive_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "ai-activity-log.jsonl"
            log_path.write_text('{"event_id":"active"}\n', encoding="utf-8")
            archive_dir = log_path.parent / "archive" / "logs"
            archive_dir.mkdir(parents=True)
            external = Path(tmpdir) / "external-payload.gz"
            with gzip.open(external, "wt", encoding="utf-8") as handle:
                handle.write('{"event_id":"forged"}\n')
            archive_leaf = archive_dir / f"{log_path.name}-deadbeef.gz"
            archive_leaf.symlink_to(external)

            with self.assertRaisesRegex(
                RuntimeError,
                "path contains a symlink|source leaf cannot be a symlink",
            ):
                with common.activity_audit_lock_file(
                    log_path, shared=True, nonblocking=False
                ):
                    common.activity_audit_source_paths_unlocked(log_path)


class LogicalActivityReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.log_path = self.root / "ai-activity-log.jsonl"
        self.archive_dir = self.root / "archive" / "logs"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_dir = self.root / ".orchestrator" / "logs" / "activity-log-archive"
        self.legacy_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_gz(self, path: Path, entries: list[dict]):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")

    def _make_entries(self, start_id: int, count: int) -> list[dict]:
        return [
            {
                "event_id": f"event-{i}",
                "ts": "2026-07-16T12:00:00Z",
                "agent": "Test",
                "message": f"entry {i}"
            }
            for i in range(start_id, start_id + count)
        ]

    def _write_active(self, entries: list[dict]) -> None:
        self.log_path.write_text(
            "".join(json.dumps(entry) + "\n" for entry in entries),
            encoding="utf-8",
        )

    def _write_registered_content_archive(
        self,
        archive_name: str | None,
        entries: list[dict],
        *,
        tail_entries: list[dict] | None = None,
    ) -> Path:
        archive_payload = b"".join(
            (json.dumps(entry) + "\n").encode("utf-8") for entry in entries
        )
        payload_sha256 = hashlib.sha256(archive_payload).hexdigest()
        archive_name = archive_name or (
            f"{self.log_path.name}-{payload_sha256}.gz"
        )
        archive_path = self.archive_dir / archive_name
        self._write_gz(archive_path, entries)
        tail_payload = b"".join(
            (json.dumps(entry) + "\n").encode("utf-8")
            for entry in (tail_entries or [])
        )
        transaction_id = "activity-rotation-test-nonadjacent-tail"
        row = {
            "record_type": common.ACTIVITY_ROTATION_LINEAGE_RECORD_TYPE,
            "schema_version": common.ACTIVITY_LOG_ROTATION_SCHEMA_VERSION,
            "log_name": self.log_path.name,
            "sequence": 1,
            "transaction_id": transaction_id,
            "archive_relative_path": str(archive_path.relative_to(self.root)),
            "archive_payload_sha256": payload_sha256,
            "archive_gzip_sha256": hashlib.sha256(
                archive_path.read_bytes()
            ).hexdigest(),
            "archive_byte_count": len(archive_payload),
            "archive_line_count": len(entries),
            "source_sha256": payload_sha256,
            "source_payload_sha256": payload_sha256,
            "source_byte_count": len(archive_payload),
            "source_line_count": len(entries),
            "tail_sha256": hashlib.sha256(tail_payload).hexdigest(),
            "tail_byte_count": len(tail_payload),
            "tail_line_count": len(tail_payload.splitlines()) if tail_payload else 0,
            "previous_sequence": 0,
            "previous_transaction_id": None,
            "previous_lineage_sha256": hashlib.sha256(b"").hexdigest(),
            "boundary_normalization": None,
        }
        lineage_bytes = common._canonical_json_line(row)
        lineage_path = common.activity_rotation_lineage_path(self.log_path)
        lineage_path.parent.mkdir(parents=True, exist_ok=True)
        lineage_path.write_bytes(lineage_bytes)
        control = {
            "record_type": common.ACTIVITY_ROTATION_HEAD_RECORD_TYPE,
            "schema_version": common.ACTIVITY_LOG_ROTATION_SCHEMA_VERSION,
            "log_name": self.log_path.name,
            "sequence": row["sequence"],
            "transaction_id": transaction_id,
            "archive_payload_sha256": row["archive_payload_sha256"],
            "archive_gzip_sha256": row["archive_gzip_sha256"],
            "lineage_sha256": hashlib.sha256(lineage_bytes).hexdigest(),
            "lineage_row_sha256": common._canonical_json_sha256(row),
            "tail_sha256": row["tail_sha256"],
            "tail_byte_count": row["tail_byte_count"],
            "tail_line_count": row["tail_line_count"],
        }
        self.log_path.write_bytes(common._canonical_json_line(control) + tail_payload)
        return archive_path

    def _append_active(self, entries: list[dict]) -> None:
        with self.log_path.open("ab") as handle:
            for entry in entries:
                handle.write((json.dumps(entry) + "\n").encode("utf-8"))

    def _lineage_rows(self) -> list[dict]:
        lineage_path = common.activity_rotation_lineage_path(self.log_path)
        return [
            json.loads(line)
            for line in lineage_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def _write_lineage_rows(self, rows: list[dict]) -> None:
        common.activity_rotation_lineage_path(self.log_path).write_text(
            "".join(common._canonical_json_line(row).decode("utf-8") for row in rows),
            encoding="utf-8",
        )

    def _append_and_rotate(
        self,
        entries: list[dict],
        *,
        keep_lines: int = 0,
    ) -> Path:
        if self.log_path.exists():
            self._append_active(entries)
        else:
            self._write_active(entries)
        with common.activity_audit_lock_file(self.log_path, shared=False):
            archive = common.rotate_activity_log_unlocked(
                self.log_path,
                max_bytes=1,
                keep_lines=keep_lines,
            )
        assert archive is not None
        return archive

    def _two_content_rotations(self, *, keep_lines: int = 0) -> tuple[Path, Path, bytes]:
        line_count = max(1, keep_lines + 1)
        first = self._append_and_rotate(
            [
                {"event_id": f"content-0-{index}", "message": "first"}
                for index in range(line_count)
            ],
            keep_lines=keep_lines,
        )
        first_control = self.log_path.read_bytes().splitlines(keepends=True)[0]
        second = self._append_and_rotate(
            [
                {"event_id": f"content-1-{index}", "message": "second"}
                for index in range(line_count)
            ],
            keep_lines=keep_lines,
        )
        return first, second, first_control

    def test_exact_1000_line_overlap_two_archives_and_callback(self):
        entries1 = self._make_entries(0, 1500)
        entries2 = self._make_entries(500, 1500)

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz(f1, entries1)
        self._write_gz(f2, entries2)

        collapsed_info = []
        def on_collapse(prev_p, next_p, lines, bytes_count, digest):
            collapsed_info.append({
                "prev_source": str(prev_p),
                "next_source": str(next_p),
                "lines": lines,
                "bytes": bytes_count,
                "digest": digest
            })

        results = list(common.stream_logical_activity(self.log_path, on_collapse=on_collapse))
        self.assertEqual(len(results), 2000)
        for idx, (entry, _, _) in enumerate(results):
            self.assertEqual(entry["event_id"], f"event-{idx}")

        self.assertEqual(len(collapsed_info), 1)
        self.assertEqual(collapsed_info[0]["prev_source"], str(f1))
        self.assertEqual(collapsed_info[0]["next_source"], str(f2))

    def test_unregistered_999_line_overlap_is_rejected(self):
        entries1 = self._make_entries(0, 1500)
        entries2 = self._make_entries(501, 1500)
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz(f1, entries1)
        self._write_gz(f2, entries2)

        with self.assertRaisesRegex(RuntimeError, "Invalid overlap length 999"):
            list(common.stream_logical_activity(self.log_path))

    def test_three_consecutive_legacy_overlaps(self):
        entries1 = self._make_entries(0, 1500)
        entries2 = self._make_entries(500, 2000)
        entries3 = self._make_entries(1500, 1500)

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        f3 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1301Z.gz"
        self._write_gz(f1, entries1)
        self._write_gz(f2, entries2)
        self._write_gz(f3, entries3)

        results = list(common.stream_logical_activity(self.log_path))
        self.assertEqual(len(results), 3000)
        for idx, (entry, _, _) in enumerate(results):
            self.assertEqual(entry["event_id"], f"event-{idx}")

    def test_legacy_archive_to_active_log_overlap(self):
        entries1 = self._make_entries(500, 1000)
        entries_active = self._make_entries(500, 1500)

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1450Z.gz"
        self._write_gz(f1, entries1)
        self.log_path.write_text("".join(json.dumps(e) + "\n" for e in entries_active), encoding="utf-8")

        results = list(common.stream_logical_activity(self.log_path))
        self.assertEqual(len(results), 1500)
        for idx, (entry, _, _) in enumerate(results):
            self.assertEqual(entry["event_id"], f"event-{500 + idx}")

    def test_first_content_rotation_excludes_verified_legacy_active_prefix(self):
        legacy_entries = self._make_entries(0, 1500)
        active_entries = self._make_entries(500, 1800)
        predecessor = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1450Z.gz"
        self._write_gz(predecessor, legacy_entries)
        self._write_active(active_entries)

        with common.activity_audit_lock_file(self.log_path, shared=False):
            archive = common.rotate_activity_log_unlocked(
                self.log_path,
                max_bytes=1,
                keep_lines=1000,
            )

        self.assertIsNotNone(archive)
        with gzip.open(archive, "rt", encoding="utf-8") as handle:
            archived_ids = [
                json.loads(line)["event_id"]
                for line in handle.read().splitlines()
                if line
            ]
        self.assertEqual(archived_ids[0], "event-1500")
        self.assertNotIn("event-500", archived_ids)

        rows = self._lineage_rows()
        self.assertEqual(len(rows), 1)
        boundary = rows[0]["boundary_normalization"]
        self.assertEqual(boundary["excluded_prefix_line_count"], 1000)
        self.assertEqual(boundary["predecessor_relative_path"], str(predecessor.relative_to(self.root)))

        logical_ids = [entry["event_id"] for entry, _, _ in common.stream_logical_activity(self.log_path)]
        self.assertEqual(logical_ids, [f"event-{idx}" for idx in range(2300)])

    def test_boundary_predecessor_replacement_cannot_hide_excluded_rows(self):
        legacy_entries = self._make_entries(0, 1500)
        active_entries = self._make_entries(500, 1800)
        predecessor = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1450Z.gz"
        self._write_gz(predecessor, legacy_entries)
        self._write_active(active_entries)

        with common.activity_audit_lock_file(self.log_path, shared=False):
            common.rotate_activity_log_unlocked(
                self.log_path,
                max_bytes=1,
                keep_lines=1000,
            )

        # Replace the pinned predecessor with a valid gzip that contains only
        # its non-overlap prefix. Trusting the manifest without reopening this
        # source would silently lose the 1000 excluded rows.
        self._write_gz(predecessor, legacy_entries[:500])
        with self.assertRaises(common.ActivityAuditInvariantError) as ctx:
            list(common.stream_logical_activity(self.log_path))

        self.assertEqual(
            ctx.exception.diagnostic["invariant"],
            "activity_content_identity",
        )
        self.assertIn(
            "boundary predecessor identity mismatch",
            ctx.exception.diagnostic["message"],
        )

    def test_first_content_rotation_rejects_bad_boundary_candidates(self):
        predecessor = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1450Z.gz"
        legacy_entries = self._make_entries(0, 1500)
        self._write_gz(predecessor, legacy_entries)

        bad_cases = {
            "999": self._make_entries(501, 1200),
            "1001": self._make_entries(499, 1200),
            "mismatch": [
                *self._make_entries(500, 999),
                {"event_id": "event-1499", "message": "mutated"},
                *self._make_entries(1500, 200),
            ],
        }
        for name, active_entries in bad_cases.items():
            with self.subTest(name=name):
                self._write_active(active_entries)
                with self.assertRaisesRegex(
                    RuntimeError,
                    "invalid first content-addressed boundary",
                ):
                    with common.activity_audit_lock_file(self.log_path, shared=False):
                        common.rotate_activity_log_unlocked(
                            self.log_path,
                            max_bytes=1,
                            keep_lines=1000,
                        )
                self.assertFalse(common.activity_rotation_lineage_path(self.log_path).exists())
                content_archives = [
                    path
                    for path in self.archive_dir.glob("ai-activity-log.jsonl-*.gz")
                    if common.classify_source(path) == "content_addressed"
                ]
                self.assertEqual(content_archives, [])

    def test_content_lineage_order_overrides_hash_lexical_order(self):
        candidates = []
        for index in range(30):
            entry = {"event_id": f"content-{index}", "message": f"payload {index}"}
            payload = (json.dumps(entry) + "\n").encode("utf-8")
            candidates.append((hashlib.sha256(payload).hexdigest(), entry))
        ordered = sorted(candidates, key=lambda item: item[0])
        creation_entries = [ordered[-1][1], ordered[0][1], ordered[len(ordered) // 2][1]]

        for entry in creation_entries:
            if self.log_path.exists():
                self._append_active([entry])
            else:
                self._write_active([entry])
            with common.activity_audit_lock_file(self.log_path, shared=False):
                common.rotate_activity_log_unlocked(
                    self.log_path,
                    max_bytes=1,
                    keep_lines=0,
                )

        rows = self._lineage_rows()
        lineage_names = [Path(row["archive_relative_path"]).name for row in rows]
        self.assertNotEqual(lineage_names, sorted(lineage_names))
        with common.activity_audit_lock_file(self.log_path, shared=True):
            source_names = [
                path.name
                for path in common.activity_audit_source_paths_unlocked(self.log_path)
                if common.classify_source(path) == "content_addressed"
            ]
        self.assertEqual(source_names, lineage_names)
        logical_ids = [entry["event_id"] for entry, _, _ in common.stream_logical_activity(self.log_path)]
        self.assertEqual(logical_ids, [entry["event_id"] for entry in creation_entries])

    def test_content_archive_basename_must_match_payload_digest(self):
        archive = self._write_registered_content_archive(
            f"{self.log_path.name}-{'0' * 64}.gz",
            [{"event_id": "wrong-content-name", "message": "payload"}],
        )

        with self.assertRaises(common.ActivityAuditInvariantError) as ctx:
            list(common.stream_logical_activity(self.log_path))

        self.assertEqual(
            ctx.exception.diagnostic["invariant"],
            "activity_content_identity",
        )
        self.assertEqual(
            ctx.exception.diagnostic["message"],
            "activity content-addressed archive basename digest mismatch",
        )
        self.assertTrue(archive.exists())

    def test_archive_metrics_bind_raw_and_payload_to_one_stream(self):
        first_payload = b'{"event_id":"coherent-first"}\n'
        replacement_payload = b'{"event_id":"coherent-later"}\n'
        self.assertEqual(len(first_payload), len(replacement_payload))
        first_bytes = gzip.compress(first_payload, compresslevel=0, mtime=1)
        replacement_bytes = gzip.compress(
            replacement_payload,
            compresslevel=0,
            mtime=2,
        )
        self.assertEqual(len(first_bytes), len(replacement_bytes))
        self.assertNotEqual(first_bytes, replacement_bytes)

        archive = self.archive_dir / "coherent-pass.gz"
        archive.write_bytes(first_bytes)
        original_stat = archive.stat()
        real_gzip_file = gzip.GzipFile

        def replace_before_decompression(*args, **kwargs):
            with archive.open("r+b") as handle:
                handle.write(replacement_bytes)
                handle.truncate()
            os.utime(
                archive,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            return real_gzip_file(*args, **kwargs)

        with mock.patch.object(
            common.gzip,
            "GzipFile",
            side_effect=replace_before_decompression,
        ):
            metrics = common._stream_activity_archive_metrics(archive)

        self.assertEqual(
            metrics.gzip_sha256,
            hashlib.sha256(replacement_bytes).hexdigest(),
        )
        self.assertEqual(metrics.gzip_byte_count, len(replacement_bytes))
        self.assertEqual(
            metrics.payload_sha256,
            hashlib.sha256(replacement_payload).hexdigest(),
        )
        self.assertEqual(metrics.payload_byte_count, len(replacement_payload))
        self.assertEqual(metrics.payload_line_count, 1)

    def test_archive_metrics_close_descriptor_when_fdopen_fails(self):
        archive = self.archive_dir / "fdopen-failure.gz"
        archive.write_bytes(gzip.compress(b'{"event_id":"fdopen"}\n'))
        real_open = os.open
        opened_descriptors: list[int] = []

        def tracking_open(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            opened_descriptors.append(descriptor)
            return descriptor

        with (
            mock.patch.object(common.os, "open", side_effect=tracking_open),
            mock.patch.object(common.os, "fdopen", side_effect=OSError("injected")),
            self.assertRaisesRegex(
                RuntimeError,
                "activity rotation archive is unreadable",
            ),
        ):
            common._stream_activity_archive_metrics(archive)

        self.assertEqual(len(opened_descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(opened_descriptors[0])

    def test_lineage_tamper_and_rollback_fail_closed(self):
        for keep_lines in (0, 2):
            with self.subTest(keep_lines=keep_lines):
                self.tearDown()
                self.setUp()
                self._write_active(self._make_entries(0, 4))
                with common.activity_audit_lock_file(self.log_path, shared=False):
                    archive = common.rotate_activity_log_unlocked(
                        self.log_path,
                        max_bytes=1,
                        keep_lines=keep_lines,
                    )
                self.assertIsNotNone(archive)
                archive.unlink()
                common.activity_rotation_lineage_path(self.log_path).unlink()
                with self.assertRaisesRegex(
                    RuntimeError,
                    "unexpected active lineage-head control record without lineage",
                ):
                    list(common.stream_logical_activity(self.log_path))

    def test_active_lineage_head_control_tamper_failures(self):
        self._write_active(self._make_entries(0, 4))
        with common.activity_audit_lock_file(self.log_path, shared=False):
            common.rotate_activity_log_unlocked(
                self.log_path,
                max_bytes=1,
                keep_lines=2,
            )

        active_lines = self.log_path.read_bytes().splitlines(keepends=True)
        self.log_path.write_bytes(b"".join(active_lines[1:]))
        with self.assertRaisesRegex(RuntimeError, "missing active lineage-head control record"):
            list(common.stream_logical_activity(self.log_path))

    def test_content_lineage_archive_and_row_tamper_failures(self):
        cases = (
            ("missing_archive", "activity lineage archive is missing"),
            ("modified_gzip", "activity lineage archive gzip digest mismatch"),
            ("sequence_gap", "activity lineage row identity is invalid"),
            ("forked_predecessor", "activity lineage predecessor fork"),
            ("duplicate_transaction", "activity lineage duplicate transaction"),
            ("duplicate_archive", "activity lineage duplicate archive"),
        )
        for case, expected in cases:
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                first, second, _first_control = self._two_content_rotations()
                lineage_path = common.activity_rotation_lineage_path(self.log_path)
                rows = self._lineage_rows()

                if case == "missing_archive":
                    second.unlink()
                elif case == "modified_gzip":
                    self._write_gz(second, [{"event_id": "tampered"}])
                elif case == "sequence_gap":
                    rows[1]["sequence"] = 3
                    self._write_lineage_rows(rows)
                elif case == "forked_predecessor":
                    rows[1]["previous_transaction_id"] = "wrong-transaction"
                    self._write_lineage_rows(rows)
                elif case == "duplicate_transaction":
                    rows[1]["transaction_id"] = rows[0]["transaction_id"]
                    self._write_lineage_rows(rows)
                elif case == "duplicate_archive":
                    for key in (
                        "archive_relative_path",
                        "archive_payload_sha256",
                        "archive_gzip_sha256",
                        "archive_byte_count",
                        "archive_line_count",
                    ):
                        rows[1][key] = rows[0][key]
                    self._write_lineage_rows(rows)

                self.assertTrue(lineage_path.exists())
                self.assertTrue(first.exists())
                with self.assertRaisesRegex(RuntimeError, expected):
                    list(common.stream_logical_activity(self.log_path))

    def test_active_lineage_head_stale_tail_and_newest_row_rollback_fail(self):
        cases = (
            ("stale_control", 0, "active lineage-head control record mismatch"),
            ("tail_digest", 2, "active lineage-head retained tail digest mismatch"),
            ("newest_row_archive_rollback", 0, "active lineage-head control record mismatch"),
        )
        for case, keep_lines, expected in cases:
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                _first, second, first_control = self._two_content_rotations(
                    keep_lines=keep_lines,
                )
                lineage_path = common.activity_rotation_lineage_path(self.log_path)
                active_lines = self.log_path.read_bytes().splitlines(keepends=True)

                if case == "stale_control":
                    self.log_path.write_bytes(first_control + b"".join(active_lines[1:]))
                elif case == "tail_digest":
                    self.assertGreater(len(active_lines), 1)
                    active_lines[1] = active_lines[1].replace(b"content-1", b"content-X")
                    self.log_path.write_bytes(b"".join(active_lines))
                elif case == "newest_row_archive_rollback":
                    rows = self._lineage_rows()
                    self._write_lineage_rows(rows[:-1])
                    second.unlink()

                self.assertTrue(lineage_path.exists())
                with self.assertRaisesRegex(RuntimeError, expected):
                    list(common.stream_logical_activity(self.log_path))

    def test_newest_row_and_archive_rollback_fails_for_both_keep_lines(self):
        # Planner acceptance: removing the newest lineage row plus its
        # archive from a MULTI-ROW lineage must fail closed for both the
        # keep_lines=1000 and keep_lines=0 writer shapes.
        for keep_lines in (0, 1000):
            with self.subTest(keep_lines=keep_lines):
                self.tearDown()
                self.setUp()
                first, second, _first_control = self._two_content_rotations(
                    keep_lines=keep_lines,
                )
                rows = self._lineage_rows()
                self.assertEqual(len(rows), 2)
                self._write_lineage_rows(rows[:-1])
                second.unlink()
                self.assertTrue(first.exists())
                with self.assertRaisesRegex(
                    RuntimeError,
                    "active lineage-head control record mismatch",
                ):
                    list(common.stream_logical_activity(self.log_path))

    def test_active_control_field_level_tamper_matrix_fails_closed(self):
        # Planner acceptance: every bound control field must be tampered
        # independently, plus stale-control and retained-tail truncation.
        digest_fields = (
            "archive_payload_sha256",
            "archive_gzip_sha256",
            "lineage_sha256",
            "lineage_row_sha256",
            "tail_sha256",
        )
        int_fields = ("sequence", "tail_byte_count", "tail_line_count")
        str_fields = ("transaction_id", "log_name")
        cases = [(field, "digest") for field in digest_fields]
        cases += [(field, "int") for field in int_fields]
        cases += [(field, "str") for field in str_fields]
        cases += [("schema_version", "int")]
        for field, kind in cases:
            with self.subTest(field=field):
                self.tearDown()
                self.setUp()
                self._write_active(self._make_entries(0, 4))
                with common.activity_audit_lock_file(self.log_path, shared=False):
                    common.rotate_activity_log_unlocked(
                        self.log_path,
                        max_bytes=1,
                        keep_lines=2,
                    )
                active_lines = self.log_path.read_bytes().splitlines(keepends=True)
                control = json.loads(active_lines[0])
                if kind == "digest":
                    control[field] = "0" * 64
                elif kind == "int":
                    control[field] = int(control[field]) + 1
                else:
                    control[field] = "tampered-" + str(control[field])
                self.log_path.write_bytes(
                    common._canonical_json_line(control)
                    + b"".join(active_lines[1:])
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    "active lineage-head control record mismatch",
                ):
                    list(common.stream_logical_activity(self.log_path))

    def test_active_control_retained_tail_truncation_fails_closed(self):
        self._write_active(self._make_entries(0, 4))
        with common.activity_audit_lock_file(self.log_path, shared=False):
            common.rotate_activity_log_unlocked(
                self.log_path,
                max_bytes=1,
                keep_lines=2,
            )
        active_lines = self.log_path.read_bytes().splitlines(keepends=True)
        self.assertGreaterEqual(len(active_lines), 3)
        self.log_path.write_bytes(b"".join(active_lines[:-1]))
        with self.assertRaisesRegex(
            RuntimeError,
            "retained tail is truncated",
        ):
            list(common.stream_logical_activity(self.log_path))

    def test_extra_content_archive_and_second_boundary_exception_fail(self):
        legacy_entries = self._make_entries(0, 1500)
        active_entries = self._make_entries(500, 1800)
        predecessor = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1450Z.gz"
        self._write_gz(predecessor, legacy_entries)
        self._write_active(active_entries)
        with common.activity_audit_lock_file(self.log_path, shared=False):
            common.rotate_activity_log_unlocked(
                self.log_path,
                max_bytes=1,
                keep_lines=1000,
            )

        extra = self.archive_dir / (
            "ai-activity-log.jsonl-"
            + ("0" * 64)
            + ".gz"
        )
        self._write_gz(extra, [{"event_id": "extra"}])
        with self.assertRaisesRegex(RuntimeError, "content-addressed archives do not match lineage"):
            list(common.stream_logical_activity(self.log_path))
        extra.unlink()

        self._append_active([{"event_id": "after-first"}])
        with common.activity_audit_lock_file(self.log_path, shared=False):
            common.rotate_activity_log_unlocked(
                self.log_path,
                max_bytes=1,
                keep_lines=0,
            )
        lineage_path = common.activity_rotation_lineage_path(self.log_path)
        rows = self._lineage_rows()
        rows[1]["boundary_normalization"] = dict(rows[0]["boundary_normalization"])
        lineage_path.write_text(
            "".join(common._canonical_json_line(row).decode("utf-8") for row in rows),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "invalid boundary normalization|second boundary"):
            list(common.stream_logical_activity(self.log_path))

    def test_invalid_overlaps_rejected(self):
        # 999 lines of overlap
        entries1_999 = self._make_entries(0, 1499)
        entries2_999 = self._make_entries(500, 1499)
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz(f1, entries1_999)
        self._write_gz(f2, entries2_999)
        with self.assertRaisesRegex(RuntimeError, "duplicate across sources|payload mismatch|Invalid overlap length|Non-collapsible 1000-line overlap"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()
        f2.unlink()

        # 1001 lines of overlap
        entries1_1001 = self._make_entries(0, 1501)
        entries2_1001 = self._make_entries(500, 1501)
        self._write_gz(f1, entries1_1001)
        self._write_gz(f2, entries2_1001)
        with self.assertRaisesRegex(RuntimeError, "duplicate across sources|payload mismatch|Invalid overlap length|Non-collapsible 1000-line overlap"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()
        f2.unlink()

        # Content differs by 1 byte in overlap
        entries1 = self._make_entries(0, 1500)
        entries2 = self._make_entries(500, 1500)
        entries2[0]["message"] = "different content"
        self._write_gz(f1, entries1)
        self._write_gz(f2, entries2)
        with self.assertRaisesRegex(RuntimeError, "duplicate across sources|payload mismatch|Invalid overlap length|Non-collapsible 1000-line overlap|mismatch in 1000-line candidate"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()
        f2.unlink()

        # Unknown filename format with overlap
        entries1 = self._make_entries(0, 1500)
        entries2 = self._make_entries(500, 1500)
        f1_bad = self.archive_dir / "ai-activity-log.jsonl-custom-format.gz"
        self._write_gz(f1_bad, entries1)
        self._write_gz(f2, entries2)
        with self.assertRaisesRegex(RuntimeError, "duplicate across sources|payload mismatch|Invalid overlap length|Non-collapsible 1000-line overlap|Unknown source format"):
            list(common.stream_logical_activity(self.log_path))

    def test_strict_name_sequence_violation_rejected(self):
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        entries1 = self._make_entries(0, 1500)
        entries2 = self._make_entries(500, 1500)
        self._write_gz(f1, entries1)
        self._write_gz(f2, entries2)

        with mock.patch("common.activity_audit_source_paths_unlocked", return_value=[f2, f1]):
            with self.assertRaisesRegex(RuntimeError, "Strict name sequence violation"):
                list(common.stream_logical_activity(self.log_path))

    def test_duplicate_checks_and_payload_mismatch(self):
        entries1 = [{"event_id": "dup", "payload": "A"}]
        entries2 = [{"event_id": "dup", "payload": "B"}]
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz(f1, entries1)
        self._write_gz(f2, entries2)
        with self.assertRaisesRegex(RuntimeError, "payload mismatch"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()
        f2.unlink()

        entries = [{"event_id": "dup"}, {"event_id": "dup"}]
        self._write_gz(f1, entries)
        with self.assertRaisesRegex(RuntimeError, "duplicate activity event_id"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()

        entries = self._make_entries(0, 1500)
        entries[600] = entries[500]
        self._write_gz(f1, entries)
        with self.assertRaisesRegex(RuntimeError, "duplicate activity event_id"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()

        entries1 = self._make_entries(0, 1500)
        entries2 = self._make_entries(500, 1500)
        f1_ca = self.archive_dir / "ai-activity-log.jsonl-a5c3586ee6a53b62a47dfb199587d809284961f56705e05e9ccf1bd7c3178afe.gz"
        f2_ca = self.archive_dir / "ai-activity-log.jsonl-b5c3586ee6a53b62a47dfb199587d809284961f56705e05e9ccf1bd7c3178afe.gz"
        self._write_gz(f1_ca, entries1)
        self._write_gz(f2_ca, entries2)
        with self.assertRaisesRegex(RuntimeError, "duplicate across sources|payload mismatch|Non-collapsible 1000-line overlap|content-addressed archives do not match lineage"):
            list(common.stream_logical_activity(self.log_path))

    def test_corruptions_and_security(self):
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f1.write_bytes(b"invalid gzip bytes")
        with self.assertRaisesRegex(RuntimeError, "Truncated or corrupt gzip"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        with gzip.open(f1, "wt", encoding="utf-8") as handle:
            handle.write("{bad json}\n")
        with self.assertRaisesRegex(RuntimeError, "Bad JSON"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        target = self.root / "some-file"
        target.write_text("{}", encoding="utf-8")
        f1.symlink_to(target)
        with self.assertRaisesRegex(
            RuntimeError,
            "path contains a symlink|source leaf cannot be a symlink",
        ):
            list(common.stream_logical_activity(self.log_path))

    def test_active_log_symlink_is_rejected_before_leaf_resolution(self):
        target = self.root / "external-activity.jsonl"
        target.write_text(
            json.dumps({"event_id": "must-not-follow"}) + "\n",
            encoding="utf-8",
        )
        self.log_path.symlink_to(target)

        stream = common.stream_logical_activity(self.log_path)
        with self.assertRaisesRegex(
            RuntimeError,
            "(?:path contains a symlink|source leaf cannot be a symlink)",
        ):
            next(stream)

    def test_dangling_lineage_symlink_fails_closed(self):
        self._write_active([{"event_id": "active"}])
        lineage_path = common.activity_rotation_lineage_path(self.log_path)
        lineage_path.parent.mkdir(parents=True, exist_ok=True)
        lineage_path.symlink_to(self.root / "missing-lineage.jsonl")

        with self.assertRaises(common.ActivityAuditInvariantError) as ctx:
            list(common.stream_logical_activity(self.log_path))

        self.assertEqual(
            ctx.exception.diagnostic["invariant"],
            "activity_source_path",
        )

    def test_archive_parent_symlink_fails_closed(self):
        external_archive_dir = self.root / "external-archives"
        external_archive_dir.mkdir()
        self.archive_dir.rmdir()
        self.archive_dir.symlink_to(external_archive_dir, target_is_directory=True)
        self._write_gz(
            self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1450Z.gz",
            [{"event_id": "external"}],
        )

        with self.assertRaises(common.ActivityAuditInvariantError) as ctx:
            list(common.stream_logical_activity(self.log_path))

        self.assertEqual(
            ctx.exception.diagnostic["invariant"],
            "activity_source_path",
        )

    def test_active_log_symlink_swap_during_leaf_guard_is_rejected(self):
        self._write_active([{"event_id": "original"}])
        target = self.root / "external-activity.jsonl"
        target.write_text(
            json.dumps({"event_id": "redirected"}) + "\n",
            encoding="utf-8",
        )
        real_is_symlink = Path.is_symlink
        swapped = False

        def swap_after_leaf_check(path: Path) -> bool:
            nonlocal swapped
            result = real_is_symlink(path)
            if path == self.log_path and not swapped:
                swapped = True
                self.log_path.unlink()
                self.log_path.symlink_to(target)
            return result

        with mock.patch.object(
            Path,
            "is_symlink",
            autospec=True,
            side_effect=swap_after_leaf_check,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "(?:activity audit source leaf|canonical activity-audit data file) "
                "cannot be a symlink|activity audit source path contains a symlink",
            ):
                list(common.stream_logical_activity(self.log_path))
        self.assertTrue(swapped)

    def test_source_replacement_and_mutation_during_read(self):
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        entries = self._make_entries(0, 100)
        self._write_gz(f1, entries)

        real_final_validation = common._assert_activity_sources_stable_unlocked

        def mutate_before_final_validation(log_path, sources, snapshots):
            f1.write_bytes(b"some new mutated bytes that change size and contents")
            return real_final_validation(log_path, sources, snapshots)

        with mock.patch.object(
            common,
            "_assert_activity_sources_stable_unlocked",
            mutate_before_final_validation,
        ):
            gen = common.stream_logical_activity(self.log_path)
            with self.assertRaisesRegex(
                RuntimeError,
                "Source mutated or truncated during validation",
            ):
                next(gen)

    def test_os_replace_to_different_inode_during_read(self):
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        entries = self._make_entries(0, 1500)
        self._write_gz(f1, entries)

        f_temp = self.root / "temp-replace.gz"
        self._write_gz(f_temp, entries)
        real_final_validation = common._assert_activity_sources_stable_unlocked

        def replace_before_final_validation(log_path, sources, snapshots):
            os.replace(f_temp, f1)
            return real_final_validation(log_path, sources, snapshots)

        with mock.patch.object(
            common,
            "_assert_activity_sources_stable_unlocked",
            replace_before_final_validation,
        ):
            gen = common.stream_logical_activity(self.log_path)
            with self.assertRaisesRegex(
                RuntimeError,
                "Source replaced during validation",
            ):
                next(gen)

    def test_same_inode_same_size_mtime_mutation_during_read(self):
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        entries = self._make_entries(0, 1500)
        self._write_gz(f1, entries)

        real_final_validation = common._assert_activity_sources_stable_unlocked

        def mutate_before_final_validation(log_path, sources, snapshots):
            stat_before = f1.stat()
            mutated_raw = bytearray(f1.read_bytes())
            mutated_raw[-10] ^= 1
            f1.write_bytes(bytes(mutated_raw))
            os.utime(
                f1,
                ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns),
            )
            return real_final_validation(log_path, sources, snapshots)

        with mock.patch.object(
            common,
            "_assert_activity_sources_stable_unlocked",
            mutate_before_final_validation,
        ):
            gen = common.stream_logical_activity(self.log_path)
            with self.assertRaisesRegex(
                RuntimeError,
                "Source content changed during validation",
            ):
                next(gen)

    def test_aba_mutation_cannot_change_bytes_parsed_into_snapshot(self):
        original = b'{"event_id":"original"}\n'
        attacker = b'{"event_id":"attacker"}\n'
        self.assertEqual(len(original), len(attacker))
        self.log_path.write_bytes(original)
        stat_before = self.log_path.stat()
        real_hash = common._sha256_file_descriptor
        hash_calls = 0

        def mutate_after_first_hash(descriptor: int) -> str:
            nonlocal hash_calls
            hash_calls += 1
            if hash_calls == 1:
                digest = real_hash(descriptor)
                self.log_path.write_bytes(attacker)
                os.utime(
                    self.log_path,
                    ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns),
                )
                return digest
            if hash_calls == 2:
                self.log_path.write_bytes(original)
                os.utime(
                    self.log_path,
                    ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns),
                )
            return real_hash(descriptor)

        with mock.patch.object(
            common,
            "_sha256_file_descriptor",
            side_effect=mutate_after_first_hash,
        ):
            entries = list(common.stream_logical_activity(self.log_path))

        self.assertGreaterEqual(hash_calls, 2)
        self.assertEqual([entry[0]["event_id"] for entry in entries], ["original"])
        self.assertEqual(self.log_path.read_bytes(), original)

    def test_late_validation_failure_precedes_first_row_and_collapse_callback(self):
        entries = self._make_entries(0, 1500)
        successor_entries = self._make_entries(500, 1001)
        predecessor = (
            self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        )
        successor = (
            self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        )
        self._write_gz(predecessor, entries)
        with gzip.open(successor, "wt", encoding="utf-8") as handle:
            for entry in successor_entries:
                handle.write(json.dumps(entry) + "\n")
            handle.write("{bad late json}\n")
        callbacks = []
        stream = common.stream_logical_activity(
            self.log_path,
            on_collapse=lambda *args: callbacks.append(args),
        )

        with self.assertRaisesRegex(RuntimeError, "Bad JSON"):
            next(stream)
        self.assertEqual(callbacks, [])

    def test_islice_cannot_hide_late_validation_failure(self):
        self.log_path.write_text(
            json.dumps({"event_id": "first", "message": "must-not-return"})
            + "\n{bad late json}\n",
            encoding="utf-8",
        )

        stream = common.stream_logical_activity(self.log_path)
        with self.assertRaisesRegex(RuntimeError, "Bad JSON"):
            list(islice(stream, 1))

    def test_duplicate_json_keys_rejected_before_first_row_in_active_and_gzip(self):
        ambiguous_rows = {
            "top-level": (
                '{"event_id":"first","event_id":"second",'
                '"message":"ambiguous"}\n'
            ),
            "nested": (
                '{"event_id":"nested","metadata":{"role":"a",'
                '"role":"b"}}\n'
            ),
        }
        for source_kind in ("active", "gzip"):
            for shape, ambiguous in ambiguous_rows.items():
                with self.subTest(source=source_kind, shape=shape):
                    self.log_path.unlink(missing_ok=True)
                    for archive in self.archive_dir.glob("*.gz"):
                        archive.unlink()
                    if source_kind == "active":
                        source = self.log_path
                        source.write_text(ambiguous, encoding="utf-8")
                    else:
                        source = (
                            self.archive_dir
                            / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
                        )
                        with gzip.open(source, "wt", encoding="utf-8") as handle:
                            handle.write(ambiguous)
                    before = source.read_bytes()
                    stream = common.stream_logical_activity(self.log_path)
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "duplicate JSON key.*" + re.escape(str(source)) + ":1",
                    ):
                        next(stream)
                    self.assertEqual(source.read_bytes(), before)

    def test_activity_control_records_reject_duplicate_json_keys(self):
        self.log_path.write_text(
            '{"record_type":"activity_rotation_head",'
            '"record_type":"activity_rotation_head"}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "duplicate JSON key"):
            list(common.stream_logical_activity(self.log_path))
        self.log_path.unlink()

        intent_path = common.activity_rotation_intent_path(self.log_path)
        common.ensure_parent(intent_path)
        intent_path.write_text(
            '{"schema_version":2,"schema_version":2}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "intent is unreadable"):
            common._load_activity_rotation_intent(self.log_path)
        intent_path.unlink()

        lineage_path = common.activity_rotation_lineage_path(self.log_path)
        common.ensure_parent(lineage_path)
        lineage_path.write_text(
            '{"sequence":1,"sequence":1}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "lineage row is unreadable"):
            common._load_activity_rotation_lineage_unlocked(self.log_path)
        lineage_path.unlink()

        resolutions_path = common.activity_rotation_resolutions_path(
            self.log_path
        )
        common.ensure_parent(resolutions_path)
        resolutions_path.write_text(
            '{"sequence":1,"sequence":1}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "resolution row is unreadable"):
            common._load_activity_rotation_resolutions_unlocked(self.log_path)

    def test_validation_snapshot_memory_is_bounded_by_window_not_history(self):
        row_count = 12000
        payload = "x" * 2048
        with self.log_path.open("w", encoding="utf-8") as handle:
            for index in range(row_count):
                handle.write(
                    json.dumps(
                        {
                            "event_id": f"bounded-{index:05d}",
                            "payload": payload,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )

        tracemalloc.start()
        try:
            observed = sum(
                1 for _entry in common.stream_logical_activity(self.log_path)
            )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(observed, row_count)
        self.assertLess(peak, 12 * 1024 * 1024)

    def test_content_addressed_validation_memory_is_bounded_by_window(self):
        row_count = 12000
        payload = "x" * 2048
        with self.log_path.open("w", encoding="utf-8") as handle:
            for index in range(row_count):
                handle.write(
                    json.dumps(
                        {
                            "event_id": f"content-bounded-{index:05d}",
                            "payload": payload,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        with common.activity_audit_lock_file(self.log_path, shared=False):
            archive = common.rotate_activity_log_unlocked(
                self.log_path,
                max_bytes=1,
                keep_lines=0,
            )
        self.assertIsNotNone(archive)

        import gc

        gc.collect()
        tracemalloc.start()
        try:
            observed = sum(
                1 for _entry in common.stream_logical_activity(self.log_path)
            )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(observed, row_count)
        self.assertLess(peak, 12 * 1024 * 1024)

    def test_simultaneous_and_reentrant_readers(self):
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        entries1 = self._make_entries(0, 1500)
        entries2 = self._make_entries(500, 1500)
        self._write_gz(f1, entries1)
        self._write_gz(f2, entries2)

        import threading

        # 1. Real two-thread barrier test
        barrier = threading.Barrier(2)
        errors = []

        def worker_thread():
            try:
                # Wait for both threads to start simultaneously
                barrier.wait()
                results = list(common.stream_logical_activity(self.log_path))
                # Validate length and content
                if len(results) != 2000:
                    errors.append(f"Expected 2000 results, got {len(results)}")
            except Exception as exc:
                errors.append(str(exc))

        t1 = threading.Thread(target=worker_thread)
        t2 = threading.Thread(target=worker_thread)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(errors, [], f"Threaded execution failed: {errors}")

        # 2. Nested read test: nested read invoked from on_collapse callback with nested callback disabled
        nested_results = []
        nested_run_completed = False

        def on_collapse(prev_p, next_p, lines, bytes_count, digest):
            nonlocal nested_run_completed
            if not nested_run_completed:
                # Trigger nested read with on_collapse = None (callback disabled!)
                nested_list = list(common.stream_logical_activity(self.log_path, on_collapse=None))
                nested_results.extend(nested_list)
                nested_run_completed = True

        outer_results = list(common.stream_logical_activity(self.log_path, on_collapse=on_collapse))
        self.assertTrue(nested_run_completed)
        self.assertEqual(len(outer_results), 2000)
        self.assertEqual(len(nested_results), 2000)
        self.assertEqual(
            [e[0]["event_id"] for e in outer_results],
            [e[0]["event_id"] for e in nested_results]
        )

    def test_overlap_failures_without_event_ids(self):
        # Generate logs with NO event_id (e.g. just raw messages)
        no_id_entries = [{"message": f"line {i}"} for i in range(1500)]

        # 1) Exact 1000-line overlap (should succeed and collapse 1000 lines)
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"

        self._write_gz(f1, no_id_entries)
        # Suffix of f1 has lines 500 to 1499 (1000 lines). So f2 starts with lines 500 to 1499.
        f2_entries = no_id_entries[500:] + [{"message": f"extra {i}"} for i in range(500)]
        self._write_gz(f2, f2_entries)

        res = list(common.stream_logical_activity(self.log_path))
        self.assertEqual(len(res), 2000) # 1500 (f1) + 1000 (f2 minus 1000 collapsed)

        f1.unlink()
        f2.unlink()

        # 2) 999-line overlap (should fail closed)
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz(f1, no_id_entries)
        # Suffix of f1 of length 999 is lines 501 to 1499.
        f2_entries = no_id_entries[501:] + [{"message": f"extra {i}"} for i in range(500)]
        self._write_gz(f2, f2_entries)
        with self.assertRaisesRegex(RuntimeError, "Invalid overlap length 999"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()
        f2.unlink()

        # 3) 1001-line overlap (should fail closed)
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz(f1, no_id_entries)
        # Suffix of f1 of length 1001 is lines 499 to 1499.
        f2_entries = no_id_entries[499:] + [{"message": f"extra {i}"} for i in range(500)]
        self._write_gz(f2, f2_entries)
        with self.assertRaisesRegex(RuntimeError, "Invalid overlap length 1001"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()
        f2.unlink()

        # 4) One-byte mismatch in 1000-line overlap (should fail closed)
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz(f1, no_id_entries)
        f2_entries = no_id_entries[500:] + [{"message": f"extra {i}"} for i in range(500)]
        # Mutate one byte in f2's overlap prefix
        f2_entries[0]["message"] = "line 500 mutated"
        self._write_gz(f2, f2_entries)
        with self.assertRaisesRegex(RuntimeError, "Invalid overlap length|mismatch in 1000-line candidate"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()
        f2.unlink()

        # 5) Non-adjacent matching older tail (should fail closed)
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        f3 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1301Z.gz"

        # Write f1
        self._write_gz(f1, no_id_entries)
        # Write f2 with completely different content (no overlap with f1)
        f2_entries = [{"message": f"diff {i}"} for i in range(1500)]
        self._write_gz(f2, f2_entries)
        # Write f3 with prefix matching f1's suffix (1000 lines)
        f3_entries = no_id_entries[500:] + [{"message": f"extra {i}"} for i in range(500)]
        self._write_gz(f3, f3_entries)

        with self.assertRaisesRegex(RuntimeError, "Matching non-adjacent older tail detected"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()
        f2.unlink()
        f3.unlink()

        # 6) Unknown format file immediately rejected without overlap
        f_unknown = self.archive_dir / "ai-activity-log.jsonl-unknown.gz"
        f_unknown.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Unknown source format"):
            list(common.stream_logical_activity(self.log_path))
        f_unknown.unlink()

    def test_truncated_and_corrupt_gzip(self):
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"

        # 1. Valid gzip that is then truncated
        entries = [{"message": f"line {i}"} for i in range(100)]
        self._write_gz(f1, entries)

        # Read raw bytes and write only first half of it
        raw_bytes = f1.read_bytes()
        f1.write_bytes(raw_bytes[:len(raw_bytes)//2])

        with self.assertRaisesRegex(RuntimeError, "Truncated or corrupt gzip/file"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()

    def test_gzip_invalid_utf8(self):
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"

        # Write invalid UTF-8 bytes to gzip
        import gzip
        with gzip.open(f1, "wb") as f:
            f.write(b"{\"message\": \"hello\"}\n")
            f.write(b"\xff\xff\xff\n") # invalid UTF-8

        with self.assertRaisesRegex(RuntimeError, "Bad UTF-8"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()

    def test_incident_minimized_fixture_overlap(self):
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"

        # 500 prefix lines in f1
        entries1 = [{"message": f"prefix {i}"} for i in range(500)]

        # 1000 lines of overlap: 100 with event_id, 900 without
        overlap = []
        for i in range(1000):
            if i % 10 == 0:
                overlap.append({"event_id": f"evt-{i}", "message": f"overlap event {i}"})
            else:
                overlap.append({"message": f"overlap non-event {i}"})

        entries1.extend(overlap)
        self._write_gz(f1, entries1)

        # f2 has the 1000 overlap lines plus 500 suffix lines
        entries2 = list(overlap)
        entries2.extend([{"message": f"suffix {i}"} for i in range(500)])
        self._write_gz(f2, entries2)

        # New logical reader returns 100 event IDs exactly once and preserves all non-overlap lines
        res = list(common.stream_logical_activity(self.log_path))

        # Total rows: 500 (prefix) + 1000 (overlap) + 500 (suffix) = 2000
        self.assertEqual(len(res), 2000)

        # Verify the 100 event IDs are returned exactly once
        yielded_ids = [entry["event_id"] for entry, _, _ in res if "event_id" in entry]
        self.assertEqual(len(yielded_ids), 100)
        self.assertEqual(len(set(yielded_ids)), 100)

        # Demonstrate that if we don't collapse (by introducing 1-byte difference in overlap),
        # it fails with duplicate event ID
        # Mutate f2's overlap by 1 byte
        entries2[1]["message"] = "mutated overlap line"
        self._write_gz(f2, entries2)

        with self.assertRaisesRegex(RuntimeError, "duplicate across sources|payload mismatch|mismatch in 1000-line candidate"):
            list(common.stream_logical_activity(self.log_path))

        f1.unlink()
        f2.unlink()

    def test_inserted_1609_adjacent_lineage_and_non_adjacent_failures(self):
        # 1. Successful case: T1450Z -> T1609Z -> active (produces adjacent folds without false non-adjacent failure)
        f_1450 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1450Z.gz"
        f_1609 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1609Z.gz"

        overlap_1 = [{"message": f"overlap_1_{i}"} for i in range(1000)]
        overlap_2 = [{"message": f"overlap_2_{i}"} for i in range(1000)]

        # f_1450: length 1001. suffix (last 1000 lines) is overlap_1.
        entries_1450 = [{"message": "extra_1450"}] + overlap_1
        self._write_gz(f_1450, entries_1450)

        # f_1609: prefix (first 1000 lines) is overlap_1. suffix (last 1000 lines) is overlap_2.
        entries_1609 = overlap_1 + overlap_2
        self._write_gz(f_1609, entries_1609)

        # active (log_path): prefix is overlap_2.
        entries_active = overlap_2 + [{"message": "extra_active"}]
        self.log_path.write_text(
            "\n".join(json.dumps(e) for e in entries_active) + "\n",
            encoding="utf-8"
        )

        # Run logical activity streaming
        res = list(common.stream_logical_activity(self.log_path))
        # Expected logical entries count: 1 + 1000 (overlap_1) + 1000 (overlap_2) + 1 = 2002
        self.assertEqual(len(res), 2002)

        # Clean up files
        f_1450.unlink()
        f_1609.unlink()
        if self.log_path.exists():
            self.log_path.unlink()

        # 2. Failure case: a truly older non-adjacent match must fail
        f_older = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f_1450 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1450Z.gz"
        f_1609 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1609Z.gz"

        # f_older has overlap_1 as suffix
        entries_older = [{"message": f"older_{i}"} for i in range(500)] + overlap_1
        self._write_gz(f_older, entries_older)

        # f_1450 has completely different content (no overlap_1)
        entries_1450 = [{"message": f"diff_1450_{i}"} for i in range(1001)]
        self._write_gz(f_1450, entries_1450)

        # f_1609 prefix matches overlap_1 (which matches f_older's suffix, not its immediate predecessor f_1450's suffix)
        entries_1609 = overlap_1 + overlap_2
        self._write_gz(f_1609, entries_1609)

        # active log prefix matches overlap_2
        entries_active = overlap_2 + [{"message": "extra_active"}]
        self.log_path.write_text(
            "\n".join(json.dumps(e) for e in entries_active) + "\n",
            encoding="utf-8"
        )

        with self.assertRaisesRegex(RuntimeError, "Matching non-adjacent older tail detected"):
            list(common.stream_logical_activity(self.log_path))

        # Clean up
        f_older.unlink()
        f_1450.unlink()
        f_1609.unlink()
        if self.log_path.exists():
            self.log_path.unlink()

    def test_content_archive_non_adjacent_tail_diagnostic_is_structured_and_bounded(
        self,
    ):
        f_1609 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1609Z.gz"
        f_2337 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T2337Z.gz"
        older_tail = [{"message": f"older-tail-{index}"} for index in range(1000)]
        self._write_gz(
            f_1609,
            [{"message": f"older-prefix-{index}"} for index in range(50)]
            + older_tail,
        )
        self._write_gz(
            f_2337,
            [{"message": f"newer-unrelated-{index}"} for index in range(1200)],
        )
        content_archive = self._write_registered_content_archive(
            None,
            older_tail + [{"message": "content-tail"}],
        )

        started = time.monotonic()
        with self.assertRaises(common.ActivityAuditInvariantError) as ctx:
            list(common.stream_logical_activity(self.log_path))
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2.0)
        diagnostic = ctx.exception.diagnostic
        self.assertEqual(
            diagnostic["record_type"],
            "pantheon.activity.fail_closed.v1",
        )
        self.assertEqual(diagnostic["invariant"], "activity_non_adjacent_tail")
        self.assertEqual(len(diagnostic["evidence_sha256"]), 64)
        evidence = diagnostic["evidence"]
        self.assertEqual(evidence["matched_source"], str(f_1609))
        self.assertEqual(evidence["current_source"], str(content_archive))
        self.assertEqual(evidence["immediate_predecessor"], str(f_2337))
        self.assertEqual(
            evidence["prefix_1000_sha256"],
            evidence["matched_suffix_1000_sha256"],
        )

    def test_recover_status_activity_outbox_integration(self):
        import sys
        sys.path.append(str(common.ROOT / "scripts"))
        import ai_status

        old_status_root = getattr(ai_status, "STATUS_ROOT", None)
        old_status_file = getattr(ai_status, "STATUS_FILE", None)
        old_log_file = getattr(ai_status, "LOG_FILE", None)

        ai_status.STATUS_ROOT = self.root
        ai_status.STATUS_FILE = self.root / "ai-status.json"
        ai_status.LOG_FILE = self.root / "ai-activity-log.jsonl"

        old_env = os.environ.get("PANTHEON_STATUS_ROOT")
        os.environ["PANTHEON_STATUS_ROOT"] = str(self.root)
        try:
            # We must create a dummy ai-status.json in self.root
            status_json_path = self.root / "ai-status.json"
            dummy_state = {
                "schema_version": 1,
                "agents": [],
                "tasks": [],
                "incidents": [],
                "status_activity_outbox": None
            }
            status_json_path.write_text(json.dumps(dummy_state), encoding="utf-8")

            # 1. Identical already-present payload via logical overlap is idempotent and clears
            # Create a 1000-line overlap between f1 and f2
            f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
            f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"

            no_id_entries = [{"message": f"line {i}"} for i in range(1500)]
            # Add one event with event_id in the overlap (e.g. line 500)
            target_event = {"event_id": "event-outbox-1", "message": "hello outbox"}
            no_id_entries[500] = target_event
            self._write_gz(f1, no_id_entries)

            # Same suffix in f2 starts at 500
            f2_entries = no_id_entries[500:] + [{"message": f"extra {i}"} for i in range(500)]
            self._write_gz(f2, f2_entries)

            # Write target_event to the outbox state
            outbox_payload = {
                "schema_version": 1,
                "transaction_id": "ai-status-tx-" + common._canonical_json_sha256([target_event]),
                "events": [target_event]
            }
            dummy_state["status_activity_outbox"] = outbox_payload
            status_json_path.write_text(json.dumps(dummy_state), encoding="utf-8")

            # Now call recover_status_activity_outbox
            # It should return True (outbox recovered) and clear the outbox
            res = ai_status.recover_status_activity_outbox(dummy_state)
            self.assertTrue(res)
            self.assertIsNone(dummy_state["status_activity_outbox"])

            # Read back state from disk and verify it's cleared
            disk_state = json.loads(status_json_path.read_text(encoding="utf-8"))
            self.assertIsNone(disk_state["status_activity_outbox"])

            # 2. Mismatched payload rejects
            # Modify target_event to mismatch
            mismatched_event = {"event_id": "event-outbox-1", "message": "mismatched hello"}
            outbox_payload_mismatch = {
                "schema_version": 1,
                "transaction_id": "ai-status-tx-" + common._canonical_json_sha256([mismatched_event]),
                "events": [mismatched_event]
            }
            dummy_state["status_activity_outbox"] = outbox_payload_mismatch
            status_json_path.write_text(json.dumps(dummy_state), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "activity outbox payload conflict"):
                ai_status.recover_status_activity_outbox(dummy_state)

            # Verify outbox state is unchanged on disk
            disk_state = json.loads(status_json_path.read_text(encoding="utf-8"))
            self.assertEqual(disk_state["status_activity_outbox"], outbox_payload_mismatch)

            # 3. Corruption/source replacement leaves outbox and state bytes unchanged
            # Let's restore the valid outbox payload
            dummy_state["status_activity_outbox"] = outbox_payload_mismatch
            status_json_path.write_text(json.dumps(dummy_state), encoding="utf-8")

            # Truncate f1 to corrupt it
            f1.write_bytes(b"corrupt header bytes")

            with self.assertRaisesRegex(RuntimeError, "Truncated or corrupt gzip/file"):
                ai_status.recover_status_activity_outbox(dummy_state)

            # Verify outbox state on disk is still unchanged
            disk_state = json.loads(status_json_path.read_text(encoding="utf-8"))
            self.assertEqual(disk_state["status_activity_outbox"], outbox_payload_mismatch)

        finally:
            if old_env is not None:
                os.environ["PANTHEON_STATUS_ROOT"] = old_env
            else:
                os.environ.pop("PANTHEON_STATUS_ROOT", None)
            if old_status_root is not None:
                ai_status.STATUS_ROOT = old_status_root
            if old_status_file is not None:
                ai_status.STATUS_FILE = old_status_file
            if old_log_file is not None:
                ai_status.LOG_FILE = old_log_file

    def test_sqlite_snapshot_is_unlinked_during_all_lifecycle_events(self):
        # Prepare a valid file
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        self._write_gz(f1, [{"message": "line 1"}, {"message": "line 2"}])

        created_paths: list[str] = []
        connections: list[sqlite3.Connection] = []
        original_tempfile = common.tempfile.NamedTemporaryFile
        original_open_snapshot = common._open_ephemeral_activity_snapshot_database

        def tracking_tempfile(*args, **kwargs):
            handle = original_tempfile(*args, **kwargs)
            if str(kwargs.get("prefix") or "").startswith(
                "pantheon-activity-snapshot-"
            ):
                created_paths.append(handle.name)
            return handle

        def tracking_open_snapshot() -> sqlite3.Connection:
            connection = original_open_snapshot()
            connections.append(connection)
            return connection

        def assert_all_snapshots_unlinked() -> None:
            self.assertTrue(created_paths)
            self.assertTrue(all(not os.path.exists(path) for path in created_paths))

        def assert_connection_closed(connection: sqlite3.Connection) -> None:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

        with (
            mock.patch.object(
                common.tempfile,
                "NamedTemporaryFile",
                side_effect=tracking_tempfile,
            ),
            mock.patch.object(
                common,
                "_open_ephemeral_activity_snapshot_database",
                side_effect=tracking_open_snapshot,
            ),
        ):
            # Case 1: success. The live SQLite connection retains the disk
            # allocation, but its pathname is already gone before first yield.
            gen = common.stream_logical_activity(self.log_path)
            next(gen)
            assert_all_snapshots_unlinked()
            self.assertEqual(connections[-1].execute("SELECT 1").fetchone(), (1,))
            success_connection = connections[-1]
            list(gen)
            assert_all_snapshots_unlinked()
            assert_connection_closed(success_connection)

            # Case 2: validation failure.
            self._write_gz(
                f1,
                [
                    {"event_id": "ev1", "message": "msg"},
                    {"event_id": "ev1", "message": "msg"},
                ],
            )
            gen = common.stream_logical_activity(self.log_path)
            with self.assertRaisesRegex(RuntimeError, "duplicate activity event_id"):
                next(gen)
            assert_all_snapshots_unlinked()
            assert_connection_closed(connections[-1])

            # Case 3: explicit generator close.
            self._write_gz(f1, [{"message": "line 1"}, {"message": "line 2"}])
            gen = common.stream_logical_activity(self.log_path)
            next(gen)
            assert_all_snapshots_unlinked()
            explicit_close_connection = connections[-1]
            gen.close()
            assert_all_snapshots_unlinked()
            assert_connection_closed(explicit_close_connection)

            # Case 4: a consumer exception followed by explicit close.
            gen = common.stream_logical_activity(self.log_path)
            consumer_exception_connection: sqlite3.Connection | None = None
            with self.assertRaisesRegex(RuntimeError, "consumer failed"):
                try:
                    next(gen)
                    consumer_exception_connection = connections[-1]
                    raise RuntimeError("consumer failed")
                finally:
                    gen.close()
            assert_all_snapshots_unlinked()
            self.assertIsNotNone(consumer_exception_connection)
            assert_connection_closed(consumer_exception_connection)

    def test_deliberate_break_occurs_after_full_validation_and_explicit_close(self):
        self._write_active(self._make_entries(0, 3))
        real_final_validation = common._assert_activity_sources_stable_unlocked

        with mock.patch.object(
            common,
            "_assert_activity_sources_stable_unlocked",
            wraps=real_final_validation,
        ) as final_validation:
            stream = common.stream_logical_activity(self.log_path)
            observed = []
            for entry, _source, _line_number in stream:
                observed.append(entry["event_id"])
                break

            self.assertEqual(observed, ["event-0"])
            final_validation.assert_called_once()
            stream.close()

    def test_validation_complete_event_lookup_omits_unrequested_payloads(self):
        entries = [
            {"event_id": "index-one", "message": "one"},
            {"event_id": "index-two", "message": "two"},
            {"message": "no event id"},
        ]
        self._write_active(entries)

        with (
            common.activity_audit_lock_file(self.log_path, shared=True),
            mock.patch.object(
                common,
                "_build_logical_activity_snapshot_unlocked",
                wraps=common._build_logical_activity_snapshot_unlocked,
            ) as build_snapshot,
        ):
            event_index = common.validated_activity_event_digests_unlocked(
                self.log_path,
                {"index-two", "missing"},
            )

        self.assertEqual(
            {
                "index-two": common._canonical_json_sha256(entries[1]),
            },
            event_index,
        )
        build_snapshot.assert_called_once_with(
            self.log_path,
            capture_logical_entries=False,
        )

    def test_validation_complete_event_lookup_rejects_noncanonical_request(self):
        for event_id in (" index-two", "index-two ", " index-two ", 2):
            with (
                self.subTest(event_id=event_id),
                mock.patch.object(
                    common,
                    "_build_logical_activity_snapshot_unlocked",
                ) as build_snapshot,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "requested activity event_id is not canonical",
                ):
                    common.validated_activity_event_digests_unlocked(
                        self.log_path,
                        [event_id],
                    )
                build_snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
