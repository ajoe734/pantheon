"""Focused regressions for OPS-AGY-PROVIDER-ERROR-OBSERVABILITY-001.

`agy --output-format stream-json` only emits opaque ``error_message`` step
updates on stdout when a request fails mid-turn (the CLI keeps retrying
internally), so `detect_worker_failure`'s stdout scan and the auth probe's
combined-stdout/stderr classification never see the actual provider-native
error body (a RESOURCE_EXHAUSTED/429 quota error, for example). Both the
dispatch adapter and the auth probe now bind an explicit `--log-file` to the
exact invocation so that native evidence is available. This module is test
isolation for that invocation-log binding; it does not change
test_adapter_delivery_policy.py (owned by the Git authentication task) or any
runtime mechanism.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import supervisor
from adapters.antigravity import AntigravityAdapter
from adapters.base import DeliveryRequest


class AntigravityAdapterNativeLogBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        # Adapter command-shape test: avoid depending on the live, shared
        # PANTHEON_COMMAND_ROOT checkout's cleanliness (see
        # test_adapter_delivery_policy.py's identical setUp).
        self._task_state_env = mock.patch(
            "common.task_state_store_runtime_env",
            return_value={
                "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
                "PANTHEON_TASK_STATE_EVENT_LOG": "/tmp/task-state-events-v2.jsonl",
            },
        )
        self._task_state_env.start()
        self._status_command_env = mock.patch(
            "common.status_command_runtime_env",
            return_value={
                "PANTHEON_COMMAND_ROOT": "/tmp/mock-command-root",
                "PANTHEON_COMMAND_RUNTIME_SHA": "mocksha",
            },
        )
        self._status_command_env.start()

    def tearDown(self) -> None:
        self._status_command_env.stop()
        self._task_state_env.stop()

    def test_deliver_binds_a_log_file_flag_and_reports_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "agents": {
                    "antigravity": {
                        "id": "antigravity",
                        "display_name": "Antigravity",
                        "provider": "antigravity",
                        "adapter": "antigravity",
                    }
                },
                "providers": {
                    "antigravity": {
                        "delivery_mode": "antigravity",
                        "antigravity": {"cli": "agy"},
                    }
                },
            }
            request = DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="T-AGY",
                metadata={"workspace_path": str(root / "task-worktree")},
            )
            adapter = AntigravityAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=555)
            with (
                mock.patch("adapters.antigravity.command_exists", return_value="agy"),
                mock.patch("adapters.antigravity._auth_ready", return_value=True),
                mock.patch(
                    "adapters.antigravity.spawn_background_process",
                    return_value=(fake_process, root / "agy.log"),
                ) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        self.assertIn("--log-file", result.command)
        native_log_path = result.command[result.command.index("--log-file") + 1]
        self.assertEqual(native_log_path, result.metadata["native_log_path"])
        # Distinct from the worker's own stdout stream log.
        self.assertNotEqual(native_log_path, str(root / "agy.log"))
        self.assertNotEqual(native_log_path, spawn.call_args.kwargs["log_path"])


class DetectWorkerFailureNativeLogFallbackTests(unittest.TestCase):
    def _worker(self, tmpdir: str, *, stream_lines: list[dict], native_lines: list[str] | None) -> dict:
        log_path = Path(tmpdir) / "agy.log"
        log_path.write_text(
            "\n".join(json.dumps(line) for line in stream_lines) + "\n", encoding="utf-8"
        )
        worker = {
            "log_path": str(log_path),
            "command": ["agy", "--output-format", "stream-json"],
        }
        if native_lines is not None:
            native_log_path = Path(tmpdir) / "agy-native.log"
            native_log_path.write_text("\n".join(native_lines) + "\n", encoding="utf-8")
            worker["native_log_path"] = str(native_log_path)
        return worker

    def test_error_message_only_stream_falls_back_to_native_log_quota(self) -> None:
        # Reproduces the reported defect: the CLI keeps retrying against a
        # RESOURCE_EXHAUSTED/429 error and only emits step_update/error_message
        # records with no error text on stdout, so no authoritative envelope is
        # ever observed there.
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = self._worker(
                tmpdir,
                stream_lines=[
                    {"event": "init", "conversation_id": "conv-native-1"},
                    *(
                        {"event": "step_update", "step_update": {"type": "error_message"}}
                        for _ in range(8)
                    ),
                ],
                native_lines=[
                    "2026-09-09T02:54:11Z attempt=1 code=429 RESOURCE_EXHAUSTED Individual quota reached",
                    "2026-09-09T03:00:35Z attempt=8 reset=1h19m5s retry=3m18.514s",
                ],
            )
            reason = supervisor.detect_worker_failure(worker)
        self.assertIsNotNone(reason)
        self.assertIn("individual quota reached", reason.lower())
        classification = supervisor.classify_worker_failure({}, worker, reason)
        self.assertEqual(classification, {"kind": "quota_terminal", "transient": False, "label": "quota terminal"})

    def test_native_log_prefers_later_quota_over_earlier_not_logged_in(self) -> None:
        # A pre-auth "not logged into Antigravity" line can appear before the
        # CLI silently authenticates and later hits quota; the earlier notice
        # must not override the actual later failure.
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = self._worker(
                tmpdir,
                stream_lines=[
                    {"event": "step_update", "step_update": {"type": "error_message"}},
                ],
                native_lines=[
                    "2026-09-09T02:54:01Z error getting token source: You are not logged into Antigravity.",
                    "2026-09-09T02:54:05Z auth succeeded",
                    "2026-09-09T02:54:11Z code=429 RESOURCE_EXHAUSTED Individual quota reached",
                ],
            )
            reason = supervisor.detect_worker_failure(worker)
        self.assertIn("individual quota reached", reason.lower())
        self.assertNotIn("not logged into", reason.lower())

    def test_no_native_log_bound_returns_none_like_before(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = self._worker(
                tmpdir,
                stream_lines=[{"event": "step_update", "step_update": {"type": "agent_response"}}],
                native_lines=None,
            )
            self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_missing_native_log_file_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = self._worker(
                tmpdir,
                stream_lines=[{"event": "step_update", "step_update": {"type": "error_message"}}],
                native_lines=None,
            )
            worker["native_log_path"] = str(Path(tmpdir) / "does-not-exist.log")
            self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_authoritative_stream_result_wins_without_reading_native_log(self) -> None:
        # A structured, authoritative failure result in the stdout stream is
        # still used directly; the native log fallback is for the observation
        # gap only, not a replacement for the existing structured-stream path.
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = self._worker(
                tmpdir,
                stream_lines=[
                    {"event": "result", "result": {"status": "error", "error": "quota"}},
                ],
                native_lines=["unrelated native log content"],
            )
            reason = supervisor.detect_worker_failure(worker)
        self.assertIsNotNone(reason)
        self.assertIn('"result"', reason)

    def test_bounded_timeout_transcript_mention_without_terminal_evidence_is_not_a_failure(self) -> None:
        # Text merely mentioning "quota" in ordinary transcript content (not a
        # provider control envelope, and not the bound native CLI log) must not
        # be treated as an authoritative failure.
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = self._worker(
                tmpdir,
                stream_lines=[
                    {
                        "event": "step_update",
                        "step_update": {
                            "type": "agent_response",
                            "text_delta": "I will check the quota dashboard next.",
                        },
                    }
                ],
                native_lines=None,
            )
            self.assertIsNone(supervisor.detect_worker_failure(worker))


class AntigravityAuthProbeNativeLogBindingTests(unittest.TestCase):
    def test_empty_output_probe_reclassifies_as_quota_from_native_log(self) -> None:
        import subprocess

        import provider_permissions

        config = {"providers": {"antigravity": {"antigravity": {"cli": "agy"}}}}
        token = Path(os.path.expanduser("~/x-token"))
        silent = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout="", stderr="")

        def fake_run_command(command, **kwargs):
            log_path = Path(command[command.index("--log-file") + 1])
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                "code=429 RESOURCE_EXHAUSTED Individual quota reached\n", encoding="utf-8"
            )
            return silent

        with (
            mock.patch.object(
                provider_permissions, "_antigravity_auth_metadata",
                return_value={"oauth_token_exists": True, "gemini_api_key_present": False, "oauth_token": str(token)},
            ),
            mock.patch.object(provider_permissions, "run_command", side_effect=fake_run_command),
        ):
            record = provider_permissions._antigravity_auth_probe(config, "antigravity", "/usr/bin/agy")

        self.assertFalse(record["ready"])
        self.assertEqual(record["status"], "quota_reached")

    def test_genuine_silent_auth_failure_without_native_evidence_stays_empty_output(self) -> None:
        import subprocess

        import provider_permissions

        config = {"providers": {"antigravity": {"antigravity": {"cli": "agy"}}}}
        token = Path(os.path.expanduser("~/x-token"))
        silent = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout="", stderr="")

        def fake_run_command(command, **kwargs):
            # No native log file is produced (matches a genuinely stale
            # OAuth token: the CLI exits before writing anything).
            return silent

        with (
            mock.patch.object(
                provider_permissions, "_antigravity_auth_metadata",
                return_value={"oauth_token_exists": True, "gemini_api_key_present": False, "oauth_token": str(token)},
            ),
            mock.patch.object(provider_permissions, "run_command", side_effect=fake_run_command),
        ):
            record = provider_permissions._antigravity_auth_probe(config, "antigravity", "/usr/bin/agy")

        self.assertFalse(record["ready"])
        self.assertEqual(record["status"], "empty_output")

    def test_success_probe_still_reports_ready(self) -> None:
        import subprocess

        import provider_permissions

        config = {"providers": {"antigravity": {"antigravity": {"cli": "agy"}}}}
        token = Path(os.path.expanduser("~/x-token"))
        ok = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout="OK\n", stderr="")

        def fake_run_command(command, **kwargs):
            log_path = Path(command[command.index("--log-file") + 1])
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                "error getting token source: You are not logged into Antigravity.\n"
                "silent authentication started\n"
                "authenticated successfully\n",
                encoding="utf-8",
            )
            return ok

        with (
            mock.patch.object(
                provider_permissions, "_antigravity_auth_metadata",
                return_value={"oauth_token_exists": True, "gemini_api_key_present": False, "oauth_token": str(token)},
            ),
            mock.patch.object(provider_permissions, "run_command", side_effect=fake_run_command),
        ):
            record = provider_permissions._antigravity_auth_probe(config, "antigravity", "/usr/bin/agy")

        self.assertTrue(record["ready"])

    def test_native_auth_lifecycle_preserves_terminal_and_model_failures(self) -> None:
        import provider_permissions

        startup = "You are not logged into Antigravity.\n"
        authenticated = startup + "authenticated successfully\n"
        cases = [
            (0, "OK", "OK", authenticated, "ready"),
            (0, "", "", authenticated, "empty_output"),
            (0, " \n", "", authenticated, "empty_output"),
            (0, "OK", "OK", startup, "not_logged_in"),
            (0, "OK", "OK", authenticated + "not authenticated\n", "not_logged_in"),
            (0, "OK", "OK", authenticated + "not authenticated successfully\n", "not_logged_in"),
            (0, "OK", "OK", startup + "not authenticated; authenticated successfully\n", "not_logged_in"),
            (0, "OK", "OK\nnot authenticated", authenticated, "not_logged_in"),
            (1, "OK", "failed", authenticated, "exit_1"),
            (0, "OK", "OK", authenticated + "Individual quota reached\n", "quota_reached"),
            (1, "", "", authenticated + "Individual quota reached\n", "quota_reached"),
        ]
        for code, stdout, output, native, expected in cases:
            with self.subTest(code=code, stdout=stdout, output=output, native=native):
                ready, error, status = provider_permissions._antigravity_probe_ready(
                    code, stdout, output, native_log=native
                )
                self.assertEqual(status, expected)
                self.assertEqual(ready, expected == "ready")
                self.assertEqual(error is None, ready)

    def test_probe_removes_its_transient_native_log_after_reading(self) -> None:
        import subprocess

        import provider_permissions

        config = {"providers": {"antigravity": {"antigravity": {"cli": "agy"}}}}
        token = Path(os.path.expanduser("~/x-token"))
        ok = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout="OK\n", stderr="")
        captured_path: dict[str, Path] = {}

        def fake_run_command(command, **kwargs):
            log_path = Path(command[command.index("--log-file") + 1])
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("ok\n", encoding="utf-8")
            captured_path["value"] = log_path
            return ok

        with (
            mock.patch.object(
                provider_permissions, "_antigravity_auth_metadata",
                return_value={"oauth_token_exists": True, "gemini_api_key_present": False, "oauth_token": str(token)},
            ),
            mock.patch.object(provider_permissions, "run_command", side_effect=fake_run_command),
        ):
            provider_permissions._antigravity_auth_probe(config, "antigravity", "/usr/bin/agy")

        self.assertFalse(captured_path["value"].exists())

    def test_two_independent_provider_homes_bind_distinct_native_logs(self) -> None:
        import subprocess

        import provider_permissions

        config = {
            "providers": {
                "antigravity": {"antigravity": {"cli": "agy", "home": "/tmp/pantheon-test-agy-home-a"}},
                "antigravity2": {"antigravity": {"cli": "agy", "home": "/tmp/pantheon-test-agy-home-b"}},
            }
        }
        token = Path(os.path.expanduser("~/x-token"))
        ok = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout="OK\n", stderr="")
        seen_log_paths: list[str] = []

        def fake_run_command(command, **kwargs):
            log_path = Path(command[command.index("--log-file") + 1])
            seen_log_paths.append(str(log_path))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("ok\n", encoding="utf-8")
            return ok

        with (
            mock.patch.object(
                provider_permissions, "_antigravity_auth_metadata",
                return_value={"oauth_token_exists": True, "gemini_api_key_present": False, "oauth_token": str(token)},
            ),
            mock.patch.object(provider_permissions, "run_command", side_effect=fake_run_command),
        ):
            provider_permissions._antigravity_auth_probe(config, "antigravity", "/usr/bin/agy")
            provider_permissions._antigravity_auth_probe(config, "antigravity2", "/usr/bin/agy")

        self.assertEqual(len(seen_log_paths), 2)
        self.assertNotEqual(seen_log_paths[0], seen_log_paths[1])


if __name__ == "__main__":
    unittest.main()
