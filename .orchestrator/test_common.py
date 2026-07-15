#!/usr/bin/env python3
from __future__ import annotations

import json
import gzip
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import common


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


class PlanningSharedFilesTests(unittest.TestCase):
    def test_planning_shared_files_follow_active_session_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            planning_dir = root / "docs" / "02-architecture" / "consensus" / "sessions" / "phase3-test"
            planning_dir.mkdir(parents=True)
            readme = planning_dir / "README.md"
            session_file = planning_dir / "planning-session.json"
            state_file = root / ".orchestrator" / "planning-state.json"
            state_file.parent.mkdir(parents=True)
            readme.write_text("# phase3\n", encoding="utf-8")
            session_file.write_text("{}", encoding="utf-8")
            state_file.write_text(
                json.dumps(
                    {
                        "status": "active",
                        "session_file": str(session_file),
                        "artifacts": {
                            "planning_readme": {
                                "path": str(readme),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(common, "PLANNING_STATE_PATH", state_file):
                files = common.planning_shared_files()

        self.assertEqual(files, [readme, session_file])


class JsonLoadResilienceTests(unittest.TestCase):
    def test_load_json_still_allows_empty_optional_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "optional.json"
            path.write_text("", encoding="utf-8")

            result = common.load_json(path, default={"fallback": True})

        self.assertEqual(result, {"fallback": True})

    def test_load_status_rejects_empty_status_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = Path(tmpdir) / "ai-status.json"
            status_file.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "status file is empty"):
                common.load_status({"paths": {"status_file": str(status_file)}})

    def test_ai_status_sync_rejects_empty_existing_status_file(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir)
            status_file = status_root / "ai-status.json"
            status_file.write_text("", encoding="utf-8")
            env = os.environ.copy()
            env["PANTHEON_STATUS_ROOT"] = str(status_root)

            result = subprocess.run(
                [sys.executable, str(repo_root / "scripts" / "ai_status.py"), "sync"],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to initialize from empty status file", result.stderr + result.stdout)

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

        refresh.assert_called_once_with(env)
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
        refresh.assert_called_once_with(env)

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


class RecentTaskActivityTests(unittest.TestCase):
    def test_recent_task_activity_reads_from_tail_without_full_log_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            activity_log = root / "ai-activity-log.jsonl"
            lines = []
            for idx in range(40):
                lines.append(json.dumps({"task_id": f"OTHER-{idx}", "message": f"other-{idx}"}))
            for idx in range(8):
                lines.append(json.dumps({"task_id": "TASK-1", "message": f"match-{idx}"}))
            activity_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

            result = common._recent_task_activity({"paths": {"activity_log": str(activity_log)}}, "TASK-1", limit=3)

        self.assertEqual([entry["message"] for entry in result], ["match-5", "match-6", "match-7"])

    def test_recent_task_activity_ignores_partial_tail_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            activity_log = root / "ai-activity-log.jsonl"
            activity_log.write_text(
                "\n".join(
                    [
                        json.dumps({"task_id": "TASK-1", "message": "older"}),
                        json.dumps({"task_id": "TASK-1", "message": "newer"}),
                    ]
                )
                + '\n{"task_id": "TASK-1", "message": "partial"',
                encoding="utf-8",
            )

            result = common._recent_task_activity({"paths": {"activity_log": str(activity_log)}}, "TASK-1", limit=3)

        self.assertEqual([entry["message"] for entry in result], ["older", "newer"])


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
                        rf"canonical {plane} data file cannot be a symlink",
                    ):
                        lock_path_for(data_path)

    def test_lock_paths_resolve_the_parent_without_rejecting_parent_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            real_root = root / "real-status-root"
            real_root.mkdir()
            alias_root = root / "status-root-alias"
            alias_root.symlink_to(real_root, target_is_directory=True)

            self.assertEqual(
                common.canonical_task_state_lock_path(
                    alias_root / "ai-status.json"
                ),
                real_root / ".orchestrator" / "task-state.lock",
            )
            self.assertEqual(
                common.activity_audit_lock_path(
                    alias_root / "ai-activity-log.jsonl"
                ),
                real_root / ".orchestrator" / "activity-audit.lock",
            )

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
                rows.extend(json.loads(line) for line in text.splitlines() if line)
        return rows

    def test_sigkill_at_each_rotation_boundary_recovers_exactly_once(self) -> None:
        context = multiprocessing.get_context("fork")
        for point in ("intent", "archive", "tail"):
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


if __name__ == "__main__":
    unittest.main()
