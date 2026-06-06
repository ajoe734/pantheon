#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import common


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

    def test_summarize_failure_reason_treats_codex_config_parse_as_provider_config(self) -> None:
        result = common.summarize_failure_reason(
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`",
            "Codex",
        )

        self.assertEqual(result["kind"], "provider_config")
        self.assertEqual(result["summary"], "Provider config invalid")


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


if __name__ == "__main__":
    unittest.main()
