#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import tempfile
import unittest
import os
import json
import subprocess
from pathlib import Path
from unittest import mock

import supervisor


class RuntimeConfigTests(unittest.TestCase):
    def test_codex_quota_groups_allow_four_concurrent_slots(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))

        ready_dispatcher = config["ready_dispatcher"]
        quota_caps = config["ready_dispatcher"]["max_concurrent_per_quota_group"]

        self.assertNotIn("max_tasks_per_agent", ready_dispatcher)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Codex"], 4)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Codex2"], 4)
        self.assertEqual(quota_caps["codex1"], 4)
        self.assertEqual(quota_caps["codex2"], 4)
        self.assertGreaterEqual(len(config["agents"]["codex"]["worker_slots"]), 4)
        self.assertGreaterEqual(len(config["agents"]["codex2"]["worker_slots"]), 4)
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "codex"), 4)
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "codex2"), 4)

    def test_claude_concurrency_is_explicitly_capped_at_three(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))

        ready_dispatcher = config["ready_dispatcher"]

        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Claude"], 3)
        self.assertEqual(ready_dispatcher["max_concurrent_per_quota_group"]["claude"], 3)
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "claude"), 3)

    def test_claude2_new_work_target_and_concurrency_are_capped(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))

        ready_dispatcher = config["ready_dispatcher"]

        self.assertEqual(ready_dispatcher["target_workload"]["Claude2"], 5)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Claude2"], 1)
        self.assertEqual(ready_dispatcher["max_concurrent_per_quota_group"]["claude2"], 1)


class DetectWorkerFailureTests(unittest.TestCase):
    def _worker_for_log(self, content: str) -> dict[str, str]:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        handle.write(content)
        handle.flush()
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return {"log_path": handle.name}

    def test_ignores_error_markers_inside_captured_log_output(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "codex",
                    "I am reading ai-activity-log.jsonl for context.",
                    '262-{"ts": "2026-04-05T13:36:01Z", "message": "Error: Model \\"grok-code-fast-1\\" from --model flag is not available."}',
                    'worker_retry_scheduled: {"message": "Transient worker failure detected; retry 1 scheduled at 2026-04-05T13:48:48Z: reason: \\"QUOTA_EXHAUSTED\\""}',
                    "No local failure happened in this session.",
                ]
            )
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_real_model_availability_failure(self) -> None:
        worker = self._worker_for_log('Error: Model "grok-code-fast-1" from --model flag is not available.\n')

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            'Error: Model "grok-code-fast-1" from --model flag is not available.',
        )

    def test_detects_real_gemini_quota_failure(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
                    "retryDelayMs: 1807388.816191,",
                    "reason: 'QUOTA_EXHAUSTED'",
                    "An unexpected critical error occurred:[object Object]",
                ]
            )
            + "\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
        )

    def test_detects_claude_auth_failure_from_cli_log(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    '{"type":"system","subtype":"api_retry","attempt":1,"max_retries":10,"retry_delay_ms":590.5,"error_status":401,"error":"authentication_failed"}',
                    '{"type":"assistant","message":{"content":[{"type":"text","text":"Failed to authenticate. API Error: 401 {\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"authentication_error\\",\\"message\\":\\"Invalid authentication credentials\\"}}"}]}}',
                ]
            )
            + "\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Failed to authenticate. API Error: 401 {\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"authentication_error\\",\\"message\\":\\"Invalid authentication credentials\\"}}"}]}}',
        )

    def test_ignores_auth_text_inside_tool_result_user_message(self) -> None:
        worker = self._worker_for_log(
            '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"prior state said not authenticated, but this is just captured inspection output"}]}}\n'
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_transcribed_limit_error_inside_review_notes(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "Reviewer note:",
                    'Auto-reassigned ownership from Claude to Copilot after repeated provider failure: {"type":"result","result":"You\'ve hit your limit · resets 12am (Asia/Taipei)","worker_run_id":"claude-123"}',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_search_result_json_field_that_mentions_quota(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "exec",
                    '718:      "next": "Auto-reassigned ownership from Copilot to Codex after repeated Copilot capacity/429: 402 You have no quota",',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_activity_log_bullet_that_mentions_prior_quota_reassignment(self) -> None:
        worker = self._worker_for_log(
            "- 2026-05-09T07:29:01Z · Orchestrator · task_reassigned · Auto-reassigned review from Copilot to Codex2 after repeated Copilot quota terminal: 402 You have no quota\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_captured_queue_event_json_that_mentions_prior_quota_reassignment(self) -> None:
        worker = self._worker_for_log(
            json.dumps(
                {
                    "event_id": "evt-1",
                    "event_key": "dispatcher:Codex2:BFF-LUV-SEM-001",
                    "target_agent": "codex2",
                    "message": "Wake-up queued for supervisor: review_ready_dispatch",
                    "metadata": {
                        "task": {
                            "next": "Auto-reassigned review from Copilot to Codex2 after repeated Copilot quota terminal: 402 You have no quota"
                        }
                    },
                }
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_allowed_rate_limit_event(self) -> None:
        worker = self._worker_for_log(
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "allowed",
                        "resetsAt": 1778324400,
                        "rateLimitType": "five_hour",
                        "overageStatus": "rejected",
                        "overageDisabledReason": "org_level_disabled",
                        "isUsingOverage": False,
                    },
                }
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_non_allowed_rate_limit_event(self) -> None:
        line = json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rate_limited",
                    "rateLimitType": "five_hour",
                },
            }
        )
        worker = self._worker_for_log(line + "\n")

        self.assertEqual(supervisor.detect_worker_failure(worker), line)

    def test_detects_real_no_quota_line(self) -> None:
        worker = self._worker_for_log("402 You have no quota\n")

        self.assertEqual(supervisor.detect_worker_failure(worker), "402 You have no quota")

    def test_ignores_git_fatal_from_tool_command_output(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "exec",
                    "/bin/bash -lc 'git show abc:missing.md' in /repo",
                    " exited 128 in 0ms:",
                    "fatal: path 'missing.md' does not exist in 'abc'",
                    "worker continued reviewing after this probe.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_standalone_fatal_line(self) -> None:
        worker = self._worker_for_log("fatal: provider process crashed\n")

        self.assertEqual(supervisor.detect_worker_failure(worker), "fatal: provider process crashed")

    def test_ignores_log_search_result_json_that_mentions_quota(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "exec",
                    '.orchestrator/logs/20260417T134622225365Z-claude.log:24:{"type":"user","message":{"content":"402 You have no quota"}}',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_pretty_json_field_that_mentions_auth_failure(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "succeeded in 252ms:",
                    '"next": "Auto-reassigned ownership from Gemini2 after repeated Gemini2 auth: not authenticated",',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_diff_assignment_that_quotes_auth_failure(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "**Blocker**",
                    '+ completed.stderr = b"Error: not authenticated, please login first"',
                    "The quoted failure came from a reviewed diff, not this worker process.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_classifies_gemini_capacity_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "status: 429 RESOURCE_EXHAUSTED")

        self.assertEqual(result["kind"], "capacity_retryable")
        self.assertTrue(result["transient"])

    def test_classifies_gemini_terminal_quota_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
        )

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_copilot_no_quota_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "copilot"}

        result = supervisor.classify_worker_failure(config, worker, "402 You have no quota")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_claude_credit_balance_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude"}

        result = supervisor.classify_worker_failure(config, worker, "Credit balance is too low")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_qwen_free_tier_quota_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "qwen"}

        result = supervisor.classify_worker_failure(config, worker, "[API Error: Qwen OAuth free tier quota exceeded.]")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_codex_usage_limit_failure_as_terminal_quota(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "codex"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
        )

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_detects_codex_usage_limit_line_as_worker_failure(self) -> None:
        worker = self._worker_for_log(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
        )

    def test_detects_codex_config_parse_failure_as_worker_failure(self) -> None:
        worker = self._worker_for_log(
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`",
        )

    def test_classifies_gemini_auth_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "status: 401 unauthorized")

        self.assertEqual(result["kind"], "auth")
        self.assertFalse(result["transient"])

    def test_classifies_not_authenticated_failure_as_auth(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude2"}

        result = supervisor.classify_worker_failure(config, worker, "Claude CLI is not authenticated; inbox fallback is disabled.")

        self.assertEqual(result["kind"], "auth")
        self.assertFalse(result["transient"])

    def test_classifies_github_cli_auth_failure_as_tool_auth(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude2"}

        result = supervisor.classify_worker_failure(config, worker, "GitHub CLI is not authenticated. Run gh auth login.")

        self.assertEqual(result["kind"], "tool_auth")
        self.assertFalse(result["transient"])

    def test_classifies_codex_config_parse_failure_as_provider_config(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "codex1-1"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`",
        )

        self.assertEqual(result["kind"], "provider_config")
        self.assertFalse(result["transient"])

    def test_auth_failures_pause_provider_dispatch(self) -> None:
        self.assertTrue(supervisor.should_pause_dispatch_for_failure_kind("auth"))

    def test_provider_config_failures_pause_provider_dispatch(self) -> None:
        self.assertTrue(supervisor.should_pause_dispatch_for_failure_kind("provider_config"))

    def test_tool_auth_failures_do_not_pause_provider_dispatch(self) -> None:
        self.assertFalse(supervisor.should_pause_dispatch_for_failure_kind("tool_auth"))

    def test_classifies_gemini_unknown_critical_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "An unexpected critical error occurred:[object Object]")

        self.assertEqual(result["kind"], "unknown_critical")
        self.assertFalse(result["transient"])

    def test_formats_runtime_timestamp_in_taipei_time(self) -> None:
        self.assertEqual(
            supervisor.format_runtime_timestamp_local("2026-04-06T14:35:42Z"),
            "2026-04-06 22:35:42",
        )

    @mock.patch("supervisor.os.kill")
    @mock.patch("supervisor.os.waitpid", return_value=(43210, 0))
    def test_pid_is_alive_treats_reaped_child_as_dead(self, _waitpid: mock.Mock, _kill: mock.Mock) -> None:
        self.assertFalse(supervisor.pid_is_alive(43210))

    def test_parse_quota_retry_hint_codex_pm(self) -> None:
        from datetime import datetime, timezone

        # 03:05Z on 2026-04-28 = 11:05 LOCAL (Asia/Taipei). "7:00 PM" in local
        # time = 19:00 LOCAL = 11:00 UTC same day.
        now = datetime(2026, 4, 28, 3, 5, 0, tzinfo=timezone.utc)
        hint = supervisor.parse_quota_retry_hint(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 4, 28, 11, 0, 0, tzinfo=timezone.utc))

    def test_parse_quota_retry_hint_rolls_to_next_day_when_past(self) -> None:
        from datetime import datetime, timezone

        # 06:00Z on 2026-04-28 = 14:00 LOCAL same day (Asia/Taipei). "1pm" = 13:00
        # LOCAL is already past, so the hint should roll forward to the next day:
        # 2026-04-29 13:00 LOCAL = 2026-04-29 05:00 UTC.
        now = datetime(2026, 4, 28, 6, 0, 0, tzinfo=timezone.utc)
        hint = supervisor.parse_quota_retry_hint(
            "You've hit your limit · resets 1pm (Asia/Taipei)",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 4, 29, 5, 0, 0, tzinfo=timezone.utc))

    def test_parse_quota_retry_hint_honors_explicit_utc(self) -> None:
        from datetime import datetime, timezone

        now = datetime(2026, 5, 8, 16, 53, 27, tzinfo=timezone.utc)
        hint = supervisor.parse_quota_retry_hint(
            "You've hit your limit · resets 8:40pm (UTC)",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 5, 8, 20, 40, 0, tzinfo=timezone.utc))

    def test_parse_quota_retry_hint_codex_full_date(self) -> None:
        from datetime import datetime, timezone

        now = datetime(2026, 5, 16, 10, 5, 36, tzinfo=timezone.utc)
        hint = supervisor.parse_quota_retry_hint(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
            "to purchase more credits or try again at May 19th, 2026 12:40 AM.",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 5, 18, 16, 40, 0, tzinfo=timezone.utc))

    def test_parse_quota_retry_hint_returns_none_when_absent(self) -> None:
        self.assertIsNone(supervisor.parse_quota_retry_hint("Credit balance is too low"))
        self.assertIsNone(supervisor.parse_quota_retry_hint(None))

    def test_mark_provider_dispatch_paused_honors_codex_retry_at(self) -> None:
        from datetime import datetime, timezone

        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state: dict = {}

        fake_now = datetime(2026, 4, 28, 3, 5, 0, tzinfo=timezone.utc)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex",
                "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
                task_id="SD-FND-003",
                worker_run_id="codex-run-1",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["codex"]
        # 7pm Asia/Taipei = 11:00 UTC same day, far longer than the default 900s
        self.assertEqual(entry["blocked_until"], "2026-04-28T11:00:00Z")
        self.assertEqual(entry["pause_kind"], "quota_terminal")
        # reset_after_seconds should reflect the actual hint window, not the default
        self.assertGreater(entry["reset_after_seconds"], 900)
        self.assertEqual(entry["reset_after_seconds"], int((11 - 3) * 3600 - 5 * 60))

    def test_mark_provider_dispatch_paused_honors_codex_full_date_retry_at(self) -> None:
        from datetime import datetime, timezone

        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex2-3": {"quota_group": "codex2"}},
        }
        state: dict = {}

        fake_now = datetime(2026, 5, 16, 10, 5, 36, tzinfo=timezone.utc)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex2-3",
                "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
                "to purchase more credits or try again at May 19th, 2026 12:40 AM.",
                task_id="TRN-002",
                worker_run_id="codex-run-1",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["codex2"]
        self.assertEqual(entry["trigger_provider"], "codex2_3")
        self.assertEqual(entry["blocked_until"], "2026-05-18T16:40:00Z")
        self.assertEqual(entry["reset_after_seconds"], 196464)

    def test_mark_provider_dispatch_paused_caps_codex_retry_hint_when_configured(self) -> None:
        from datetime import datetime, timezone

        config = {
            "provider_guardrails": {
                "capacity_pause_seconds": 900,
                "quota_terminal_pause_seconds": 900,
                "quota_terminal_hint_max_seconds": 3600,
            },
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex2-3": {"quota_group": "codex2"}},
        }
        state: dict = {}

        fake_now = datetime(2026, 5, 17, 20, 2, 2, tzinfo=timezone.utc)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex2-3",
                "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
                "to purchase more credits or try again at May 19th, 2026 12:40 AM.",
                task_id="OODA-E2E-005",
                worker_run_id="codex-run-1",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["codex2"]
        self.assertEqual(entry["blocked_until"], "2026-05-17T21:02:02Z")
        self.assertEqual(entry["hint_blocked_until"], "2026-05-18T16:40:00Z")
        self.assertTrue(entry["hint_capped"])
        self.assertEqual(entry["reset_after_seconds"], 3600)

    def test_mark_provider_dispatch_paused_uses_default_when_no_hint(self) -> None:
        from datetime import datetime, timezone

        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state: dict = {}

        fake_now = datetime(2026, 4, 28, 3, 5, 0, tzinfo=timezone.utc)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "claude",
                "Credit balance is too low",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["claude"]
        # 03:05Z + 900s = 03:20Z
        self.assertEqual(entry["blocked_until"], "2026-04-28T03:20:00Z")
        self.assertEqual(entry["reset_after_seconds"], 900)

    def test_codex_slot_pause_uses_shared_quota_group(self) -> None:
        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {
                "codex1-1": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-2": {"delivery_mode": "codex", "quota_group": "codex1"},
            },
        }
        state: dict = {}

        with mock.patch.object(supervisor, "write_activity_log"):
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex1-1",
                "status: 429 RESOURCE_EXHAUSTED",
                failure_kind="capacity_retryable",
                pause_kind="capacity_retryable",
            )

        pauses = state["provider_guardrails"]["dispatch_pauses"]
        self.assertIn("codex1", pauses)
        self.assertNotIn("codex1_1", pauses)
        self.assertEqual(pauses["codex1"]["trigger_provider"], "codex1_1")
        self.assertIs(supervisor.current_provider_dispatch_pause(state, "codex1-2", config), pauses["codex1"])

    def test_expire_provider_dispatch_pauses_removes_expired_entry(self) -> None:
        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "copilot": {
                        "provider": "copilot",
                        "blocked_until": "2026-04-06T12:00:00Z",
                        "pause_kind": "quota_terminal",
                        "task_id": "PKT-001",
                        "worker_run_id": "copilot-run",
                        "raw_ref": ".orchestrator/evidence/copilot.json",
                    }
                }
            }
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.expire_provider_dispatch_pauses(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "provider_dispatch_resumed")

    def test_clear_provider_dispatch_pause_removes_group_pause(self) -> None:
        config = {
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex2-3": {"delivery_mode": "codex", "quota_group": "codex2"}},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex2": {
                        "task_id": "OODA-E2E-005",
                        "worker_run_id": "codex-run-1",
                        "raw_ref": ".orchestrator/evidence/codex.json",
                    }
                }
            }
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.clear_provider_dispatch_pause(config, state, "codex2-3")

        self.assertTrue(changed)
        self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "provider_dispatch_resumed")
        self.assertEqual(write_activity_log.call_args.args[1]["provider"], "codex2")


class ProcessQueueDispatchGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "agents": {
                "codex": {
                    "id": "codex",
                    "name": "Codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                }
            },
            "providers": {
                "codex": {
                    "delivery_mode": "codex",
                }
            },
        }
        self.provider_report: dict[str, object] = {}

    def test_worker_tree_guard_warns_without_blocking(self) -> None:
        config = {
            **self.config,
            "worker_tree_guard": {
                "enabled": True,
                "mode": "warn",
                "blocking_globs": [".orchestrator/skills/**"],
            },
        }

        with (
            mock.patch.object(
                supervisor,
                "_git_dirty_entries",
                return_value=[{"status": " M", "path": ".orchestrator/skills/worker-anchor-commit.md"}],
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            ok, message = supervisor.check_worker_tree_clean(
                config,
                run_id="evt-1",
                task_id="OPS-WORKER-ANCHOR-001",
                target_agent="Codex",
                queue_event_id="evt-1",
            )

        self.assertTrue(ok)
        self.assertIn("anchor or close out", message or "")
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "dispatch_dirty_tree_warning")

    def test_worker_tree_guard_blocks_in_block_mode(self) -> None:
        config = {
            **self.config,
            "worker_tree_guard": {
                "enabled": True,
                "mode": "block",
                "blocking_globs": ["docs/**"],
            },
        }

        with (
            mock.patch.object(
                supervisor,
                "_git_dirty_entries",
                return_value=[{"status": " M", "path": "docs/conventions/GIT_WORKFLOW.md"}],
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            ok, message = supervisor.check_worker_tree_clean(
                config,
                run_id="evt-1",
                task_id="OPS-WORKER-ANCHOR-001",
                target_agent="Codex",
                queue_event_id="evt-1",
            )

        self.assertFalse(ok)
        self.assertIn("docs/conventions/GIT_WORKFLOW.md", message or "")
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "dispatch_blocked_dirty_tree")

    def test_worker_tree_guard_ignores_runtime_state_only(self) -> None:
        config = {
            **self.config,
            "worker_tree_guard": {
                "enabled": True,
                "mode": "block",
                "blocking_globs": [".orchestrator/skills/**"],
                "auto_restore_globs": ["ai-status.json", "docs-site/**"],
            },
        }

        with (
            mock.patch.object(
                supervisor,
                "_git_dirty_entries",
                return_value=[
                    {"status": " M", "path": "ai-status.json"},
                    {"status": " M", "path": "docs-site/current-work.md"},
                ],
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            ok, message = supervisor.check_worker_tree_clean(
                config,
                run_id="evt-1",
                task_id="OPS-WORKER-ANCHOR-001",
                target_agent="Codex",
                queue_event_id="evt-1",
            )

        self.assertTrue(ok)
        self.assertIsNone(message)
        write_activity_log.assert_not_called()

    def test_prepare_worker_workspace_allocates_task_worktree_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)) as create_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-1",
                    target_agent="Codex",
                )

        expected_path = worktree_root / "pantheon" / "ops-worktree-001"
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(request.metadata["workspace_mode"], "isolated_worktree")
        self.assertEqual(request.metadata["workspace_path"], str(expected_path))
        self.assertEqual(request.metadata["workspace_branch"], "task/OPS-WORKTREE-001")
        self.assertEqual(request.metadata["status_root"], str(repo_root.resolve()))
        self.assertEqual(state["worker_worktrees"]["leases"]["OPS-WORKTREE-001"]["path"], str(expected_path))
        create_worktree.assert_called_once_with(repo_root.resolve(), expected_path, "task/OPS-WORKTREE-001", "origin/dev")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_worktree_allocated")

    def test_prepare_worker_workspace_allocates_chair_review_worktree_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                    "execution_reasons": ["chair_review:*"],
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=None,
                reason="chair_review:operational_review",
                metadata={"workspace_task_id": "chair-review-20260531-153804-codex2"},
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)) as create_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-chair",
                    target_agent="Codex2",
                )

        expected_path = worktree_root / "pantheon" / "chair-review-20260531-153804-codex2"
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(request.metadata["workspace_mode"], "isolated_worktree")
        self.assertEqual(request.metadata["workspace_path"], str(expected_path))
        self.assertEqual(request.metadata["workspace_branch"], "task/chair-review-20260531-153804-codex2")
        self.assertIsNone(state["worker_worktrees"]["leases"]["chair-review-20260531-153804-codex2"]["task_id"])
        create_worktree.assert_called_once_with(
            repo_root.resolve(),
            expected_path,
            "task/chair-review-20260531-153804-codex2",
            "origin/dev",
        )
        self.assertEqual(write_activity_log.call_args.args[1]["workspace_task_id"], "chair-review-20260531-153804-codex2")

    def test_prepare_worker_workspace_materializes_task_brief_into_isolated_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            source_brief = repo_root / ".orchestrator" / "task-briefs" / "ops_brief_001.md"
            source_brief.parent.mkdir(parents=True)
            source_brief.write_text("# Source brief\n", encoding="utf-8")
            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "activity-log.jsonl"),
                },
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-BRIEF-001",
                reason="owned_in_progress_dispatch",
                context_files=[".orchestrator/task-briefs/ops_brief_001.md"],
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-brief",
                    target_agent="Codex",
                )

            self.assertTrue(ok)
            self.assertIsNone(message)
            copied_brief = Path(request.metadata["workspace_path"]) / ".orchestrator" / "task-briefs" / "ops_brief_001.md"
            self.assertEqual(copied_brief.read_text(encoding="utf-8"), "# Source brief\n")
            self.assertEqual(request.metadata["materialized_context_files"], [".orchestrator/task-briefs/ops_brief_001.md"])

    def test_prepare_worker_workspace_blocks_dirty_reused_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ops-worktree-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(
                    supervisor,
                    "_refresh_reused_worker_worktree",
                    return_value=(False, "skipped_dirty_worktree"),
                ) as refresh_worktree,
                mock.patch.object(supervisor, "_create_worker_worktree") as create_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-dirty",
                    target_agent="Codex",
                )

        self.assertFalse(ok)
        assert message is not None
        self.assertIn("dirty tracked or staged changes", message)
        self.assertNotIn("workspace_path", request.metadata)
        self.assertNotIn("worker_worktrees", state)
        refresh_worktree.assert_called_once()
        create_worktree.assert_not_called()
        self.assertEqual(
            [call.args[1]["type"] for call in write_activity_log.call_args_list],
            ["worker_worktree_refreshed", "dispatch_blocked_worktree_lease"],
        )
        self.assertEqual(write_activity_log.call_args_list[-1].args[1]["refresh_status"], "skipped_dirty_worktree")

    def test_process_queue_checks_worker_guard_inside_isolated_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            workspace = Path(tmpdir) / "workers" / "pantheon" / "bus-val-004"
            repo_root.mkdir()
            workspace.mkdir(parents=True)
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "worker_worktrees": {"enabled": True, "root": str(workspace.parent.parent)},
            }
            current_task = {
                "id": "BUS-VAL-004",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Gemini",
                "depends_on": [],
                "last_update": "2026-04-05T14:54:01Z",
            }
            queue_payload = {
                "event_id": "evt-current",
                "task_id": "BUS-VAL-004",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "owned_in_progress_dispatch",
                "message": "wake",
            }
            state = {"queue": {"events": {}}, "workers": {}}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="BUS-VAL-004",
                reason="owned_in_progress_dispatch",
            )

            def prepare_workspace(_config, _state, prepared_request, **_kwargs):
                prepared_request.metadata.update(
                    {
                        "workspace_path": str(workspace),
                        "workspace_branch": "task/BUS-VAL-004",
                        "workspace_mode": "isolated_worktree",
                        "status_root": str(repo_root.resolve()),
                    }
                )
                return True, None

            with (
                mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
                mock.patch.object(supervisor, "build_request", return_value=request),
                mock.patch.object(supervisor, "prepare_worker_workspace", side_effect=prepare_workspace),
                mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, None)) as guard,
                mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "run-123", {"manual_confirmation_required": False, "auto_delivered": True})),
                mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
            ):
                changed = supervisor.process_queue(config, state, self.provider_report)

        self.assertTrue(changed)
        self.assertEqual(guard.call_args.kwargs["cwd"], workspace)

    def test_build_request_uses_provider_model_preference_for_qwen_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "qwen": {
                    "id": "qwen",
                    "display_name": "Qwen",
                    "provider": "qwen",
                    "adapter": "qwen",
                }
            },
            "providers": {
                "qwen": {
                    "delivery_mode": "qwen",
                    "model_preference": {
                        "qwen": "qwen3-coder-plus",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "qwen",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "qwen")
        self.assertEqual(request.provider, "qwen")
        self.assertEqual(request.metadata["model_preference"], "qwen3-coder-plus")

    def test_build_request_skips_default_model_for_primary_copilot_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "copilot": {
                    "id": "copilot",
                    "display_name": "Copilot",
                    "provider": "copilot",
                    "adapter": "copilot_local",
                }
            },
            "providers": {
                "copilot": {
                    "delivery_mode": "copilot_local",
                    "model_preference": {
                        "default": None,
                        "grok": "grok-code-fast-1",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "copilot",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "copilot")
        self.assertEqual(request.provider, "copilot")
        self.assertNotIn("model_preference", request.metadata)

    def test_build_request_keeps_agent_specific_model_for_copilot_alias(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "grok": {
                    "id": "grok",
                    "display_name": "Copilot (legacy alias)",
                    "provider": "copilot",
                    "adapter": "copilot_local",
                }
            },
            "providers": {
                "copilot": {
                    "delivery_mode": "copilot_local",
                    "model_preference": {
                        "default": None,
                        "grok": "grok-code-fast-1",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "grok",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "grok")
        self.assertEqual(request.provider, "copilot")
        self.assertEqual(request.metadata["model_preference"], "grok-code-fast-1")

    def test_build_request_can_target_codex_worker_slot_with_logical_identity(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "display_name": "Codex",
                    "provider": "codex1-1",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                    "slot_id": "codex1-1",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "display_name": "Codex",
                    "provider": "codex1-2",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                    "slot_id": "codex1-2",
                },
            },
            "providers": {
                "codex": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-1": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-2": {"delivery_mode": "codex", "quota_group": "codex1"},
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "codex",
                "target_display_name": "Codex",
                "message": "wake",
                "task_id": "BFF-CONSOL-011",
                "context_files": [],
            },
            agent_id_override="codex1_2",
        )

        self.assertEqual(request.agent_id, "codex1_2")
        self.assertEqual(request.provider, "codex1-2")
        self.assertEqual(request.metadata["logical_agent_id"], "codex")
        self.assertEqual(request.metadata["dispatch_slot_id"], "codex1_2")
        self.assertEqual(request.metadata["dispatch_slot"], "codex1-2")
        self.assertEqual(request.metadata["target_display_name"], "Codex")

    def test_select_dispatch_agent_id_chooses_free_codex_slot(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "display_name": "Codex",
                    "provider": "codex1-1",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "display_name": "Codex",
                    "provider": "codex1-2",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "providers": {
                "codex1-1": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-2": {"delivery_mode": "codex", "quota_group": "codex1"},
            },
        }
        state = {
            "workers": {
                "run-1": {
                    "agent_id": "codex1_1",
                    "provider": "codex1-1",
                    "status": "running",
                }
            }
        }

        selected = supervisor.select_dispatch_agent_id(config, state, "codex", {"running"})

        self.assertEqual(selected, "codex1_2")

    def test_skips_stale_owned_dispatch_event_after_task_completion(self) -> None:
        queued_task = {
            "id": "BUS-VAL-001",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-05T11:45:16Z",
        }
        queued_event = supervisor.build_dispatch_event(
            queued_task,
            "Codex",
            "owned_in_progress_dispatch",
            {"BUS-VAL-001": queued_task},
        )
        queue_payload = {
            "event_id": "evt-stale",
            "event_key": queued_event["key"],
            "task_id": "BUS-VAL-001",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        current_status = {
            "tasks": [
                {
                    **queued_task,
                    "status": "done",
                    "last_update": "2026-04-05T12:00:00Z",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value=current_status),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("stale event should not start a worker")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-stale"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["skip_reason"], "stale_dispatch_event")
        self.assertIn("processed_at", record)
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "wake_skipped")

    def test_starts_current_owned_dispatch_event(self) -> None:
        current_task = {
            "id": "BUS-VAL-004",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-05T14:54:01Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Codex",
            "owned_in_progress_dispatch",
            {"BUS-VAL-004": current_task},
        )
        queue_payload = {
            "event_id": "evt-current",
            "event_key": current_event["key"],
            "task_id": "BUS-VAL-004",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = object()
        delivery = {"manual_confirmation_required": False, "auto_delivered": True}

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request) as build_request,
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "run-123", delivery)) as start_worker,
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True) as sync_dispatched_task_status,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-current"]
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["run_id"], "run-123")
        build_request.assert_called_once_with(self.config, queue_payload)
        start_worker.assert_called_once()
        sync_dispatched_task_status.assert_called_once_with(self.config, queue_payload)

    def test_failed_auto_lane_dispatch_does_not_create_manual_pending_worker(self) -> None:
        current_task = {
            "id": "BUS-VAL-005",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-failed-auto",
            "task_id": "BUS-VAL-005",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-005",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(False, "CLI auth unavailable", None)),
            mock.patch.object(supervisor, "classify_worker_failure", return_value={"kind": "auth", "label": "authentication"}),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "CLI auth unavailable", "kind": "auth"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value=None),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(supervisor, "mark_provider_dispatch_paused", return_value=True) as mark_provider_dispatch_paused,
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value=None),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-failed-auto"]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "CLI auth unavailable")
        self.assertEqual(state["workers"], {})
        mark_provider_dispatch_paused.assert_called_once()

    def test_process_queue_skips_not_auto_ready_provider_without_starting_worker(self) -> None:
        current_task = {
            "id": "BUS-VAL-005B",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude2",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Claude2",
            "review_ready_dispatch",
            {"BUS-VAL-005B": current_task},
        )
        queue_payload = {
            "event_id": "evt-not-ready",
            "event_key": current_event["key"],
            "task_id": "BUS-VAL-005B",
            "target_agent": "claude2",
            "target_display_name": "Claude2",
            "provider": "claude2",
            "reason": "review_ready_dispatch",
            "message": "wake",
            "context_files": [],
        }
        provider_report = {
            "agent_adapters": {
                "claude2": {
                    "supported": True,
                    "can_auto_deliver": False,
                    "notes": "Claude CLI is installed but not authenticated.",
                }
            },
            "providers": {"claude2": {"auth_ready": False}},
        }
        state = {"queue": {"events": {}}, "workers": {}}

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("not-ready provider should not start")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.process_queue(self.config, state, provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-not-ready"]
        self.assertEqual(record["status"], "failed")
        self.assertIn("Auto dispatch unavailable for claude2", record["error"])
        self.assertEqual(state["workers"], {})
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "wake_skipped")

    def test_process_queue_records_capacity_wait_metrics(self) -> None:
        current_task = {
            "id": "BUS-VAL-CAP",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-capacity-wait",
            "task_id": "BUS-VAL-CAP",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-CAP",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(
                supervisor,
                "agent_auto_dispatch_block_reason",
                return_value="quota group codex1 already has 1/1 active worker(s)",
            ),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("capacity wait should not start")),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-capacity-wait"]
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["capacity_wait_count"], 1)
        metrics = state["worker_runtime_metrics"]
        self.assertEqual(metrics["totals"]["capacity_pending_queue_events"], 1)
        self.assertEqual(
            metrics["last_measurements"]["dispatch_capacity_wait"]["details"]["queue_event_id"],
            "evt-capacity-wait",
        )

    def test_retryable_capacity_start_failure_schedules_queue_retry(self) -> None:
        current_task = {
            "id": "BUS-VAL-006",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-retryable-capacity",
            "task_id": "BUS-VAL-006",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-006",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(False, "status: 429 RESOURCE_EXHAUSTED", None)),
            mock.patch.object(
                supervisor,
                "classify_worker_failure",
                return_value={"kind": "capacity_retryable", "label": "capacity/429", "transient": True},
            ),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "Rate limited", "kind": "capacity_retryable"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value=None),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(supervisor, "mark_provider_dispatch_paused", return_value=True),
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure") as maybe_reassign_task_after_worker_failure,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-retryable-capacity"]
        self.assertEqual(record["status"], "retry_backoff")
        self.assertEqual(record["error"], "Rate limited")
        self.assertEqual(record["retry_count"], 1)
        self.assertIsNotNone(record["next_retry_at"])
        maybe_reassign_task_after_worker_failure.assert_not_called()
        self.assertEqual(state["workers"], {})

    def test_dispatcher_can_requeue_same_task_after_previous_failure(self) -> None:
        current_task = {
            "id": "REG-002",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "last_update": "2026-04-06T09:00:00Z",
            "artifacts": ["services/registry/promotion/"],
            "next": "continue",
        }
        state = {
            "queue": {
                "events": {
                    "evt-old": {
                        "status": "failed",
                        "run_id": "old-run",
                    }
                }
            },
            "workers": {
                "old-run": {
                    "run_id": "old-run",
                    "queue_event_id": "evt-old",
                    "task_id": "REG-002",
                    "agent_id": "codex",
                    "status": "failed",
                }
            },
            "seen_event_keys": {"dispatcher:Codex:REG-002:owned_in_progress_dispatch:stale-signature": "2026-04-06T08:59:00Z"},
        }
        status = {"tasks": [current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "REG-002")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_queues_multiple_codex_tasks_up_to_worker_slot_capacity(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2", "codex1_3", "codex1_4"]
        for index in range(1, 5):
            config["agents"][f"codex1_{index}"] = {
                "id": f"codex1_{index}",
                "display_name": "Codex",
                "provider": f"codex1-{index}",
                "adapter": "codex",
                "dispatch_slot_for": "codex",
            }
            config["providers"][f"codex1-{index}"] = {
                "delivery_mode": "codex",
                "quota_group": "codex1",
            }
        status = {
            "tasks": [
                {
                    "id": f"BFF-CONSOL-0{index}",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "last_update": f"2026-05-13T04:0{index}:00Z",
                }
                for index in range(1, 5)
            ]
        }
        state = {"queue": {"events": {}}, "workers": {}}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertEqual(queued_task_ids, ["BFF-CONSOL-01", "BFF-CONSOL-02", "BFF-CONSOL-03", "BFF-CONSOL-04"])
        self.assertTrue(all(call.args[1]["target_agent"] == "Codex" for call in queue_delivery_event.call_args_list))

    def test_weighted_dispatch_agent_ids_match_target_workload_ratio(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["ready_dispatcher"] = {
            "target_workload": {
                "Claude": 10,
                "Claude2": 5,
                "Gemini": 5,
                "Gemini2": 5,
                "Codex": 35,
                "Codex2": 35,
                "Copilot": 5,
            }
        }
        config["agents"] = {
            "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
            "gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"},
            "gemini2": {"id": "gemini2", "display_name": "Gemini2", "provider": "gemini2"},
            "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
            "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
        }

        sequence = supervisor.weighted_dispatch_agent_ids(config, supervisor.ready_dispatch_settings(config))
        counts = {agent_id: sequence.count(agent_id) for agent_id in config["agents"]}

        self.assertEqual(len(sequence), 20)
        self.assertEqual(
            counts,
            {
                "claude": 2,
                "claude2": 1,
                "gemini": 1,
                "gemini2": 1,
                "codex": 7,
                "codex2": 7,
                "copilot": 1,
            },
        )

    def test_dispatcher_queues_owner_finalize_after_review_approved(self) -> None:
        current_task = {
            "id": "REG-002",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-001"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        dependency = {
            "id": "REG-001",
            "status": "done",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-06T14:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [dependency, current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "REG-002")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_finalize_dispatch")

    def test_dispatcher_waits_for_done_not_review_approved_dependencies(self) -> None:
        current_task = {
            "id": "FB-003",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": ["REG-002"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        dependency = {
            "id": "REG-002",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-001"],
            "last_update": "2026-04-06T14:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [dependency, current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertNotIn("FB-003", queued_task_ids)

    def test_dispatcher_accepts_archived_done_dependency(self) -> None:
        current_task = {
            "id": "FB-004",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-100"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [current_task]}

        class FakeResolver:
            def __init__(self, task_lookup):
                self.task_lookup = task_lookup

            def dependency_status(self, task_id):
                if task_id == "REG-100":
                    return "done"
                task = self.task_lookup.get(task_id) or {}
                return str(task.get("status") or "missing")

            def dependency_satisfied(self, task_id):
                return task_id == "REG-100"

        with (
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertIn("FB-004", queued_task_ids)

    def test_dispatcher_rejects_archived_superseded_dependency(self) -> None:
        current_task = {
            "id": "FB-005",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-200"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [current_task]}

        class FakeResolver:
            def __init__(self, task_lookup):
                self.task_lookup = task_lookup

            def dependency_status(self, task_id):
                if task_id == "REG-200":
                    return "superseded"
                task = self.task_lookup.get(task_id) or {}
                return str(task.get("status") or "missing")

            def dependency_satisfied(self, task_id):
                return False

        with (
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertFalse(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertNotIn("FB-005", queued_task_ids)

    def test_discussion_planning_materialization_treats_archived_task_as_already_materialized(self) -> None:
        planning_state = {
            "status": "accepted",
            "human_gate_status": "approved",
            "session_id": "phase3-2026-04-14-pantheon-console-loop",
            "proposed_execution_tasks": [{"id": "LOOP-001"}],
        }

        class FakeResolver:
            def __init__(self, _task_lookup):
                pass

            def snapshot(self, task_id):
                if task_id == "LOOP-001":
                    return {"task_id": "LOOP-001"}
                return None

        with (
            mock.patch.object(supervisor, "load_json", return_value={"tasks": []}),
            mock.patch.object(supervisor, "config_path", return_value=Path("/tmp/ai-status.json")),
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
        ):
            needs_materialization = supervisor.discussion_planning_needs_materialization(self.config, planning_state)

        self.assertFalse(needs_materialization)

    def test_dispatcher_helper_claims_ready_todo_when_owner_is_busy_with_finalize(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex", "Claude", "Gemini"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "LP-005",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "LP-005", "status": "review_approved", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "FB-003")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Copilot")
        self.assertEqual(kwargs["handoff_to"], "Codex")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-003")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_does_not_helper_claim_when_target_workload_would_exceed_cap(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Claude"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "LP-005",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        status = {
            "workload": {"Claude": 5, "Copilot": 95},
            "tasks": [
                {"id": "CL-001", "status": "blocked", "owner": "Claude", "reviewer": "Copilot", "depends_on": []},
                {"id": "LP-005", "status": "review_approved", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
                *[
                    {
                        "id": f"CP-{index:03d}",
                        "status": "todo",
                        "owner": "Copilot",
                        "reviewer": "Claude",
                        "depends_on": [],
                    }
                    for index in range(17)
                ],
            ],
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertFalse(changed)
        persist.assert_not_called()
        queue_delivery_event.assert_not_called()

    def test_dispatcher_does_not_helper_claim_when_owner_is_not_busy(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex", "Claude", "Gemini"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {},
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {
            "tasks": [
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_not_called()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-003")
        self.assertEqual(queued_event["target_agent"], "Copilot")

    def test_dispatcher_helper_claims_ready_todo_when_idle_claim_enabled(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "claim_idle_work": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex"],
                }
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
            ]
        }
        persisted_status = {
            "tasks": [
                {
                    "id": "FB-003",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Copilot",
                    "depends_on": [],
                    "last_update": "2026-05-13T09:30:00Z",
                    "next": "Helper-claimed by idle Codex; previous owner Copilot becomes reviewer.",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "FB-003")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Copilot")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-003")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_helper_claims_unrelated_task_during_failure_loop(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "claim_idle_work": True,
                    "disable_when_failure_loops": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex"],
                }
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:copilot": {
                        "task_id": "T-REVIEW",
                        "provider": "copilot",
                        "count": 3,
                    }
                }
            },
        }
        initial_status = {
            "tasks": [
                {"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Copilot", "depends_on": []},
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
            ]
        }
        persisted_status = {
            "tasks": [
                {"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Copilot", "depends_on": []},
                {
                    "id": "FB-003",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Copilot",
                    "depends_on": [],
                    "last_update": "2026-05-13T09:30:00Z",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        self.assertEqual(persist.call_args.kwargs["task_id"], "FB-003")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-003")
        self.assertEqual(queued_event["target_agent"], "Codex")

    def test_dispatcher_prefers_owned_work_before_idle_helper_claim(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "claim_idle_work": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex"],
                }
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {"id": "FOREIGN-001", "status": "todo", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
                {"id": "OWN-001", "status": "todo", "owner": "Codex", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_not_called()
        queued_events = [call.args[1] for call in queue_delivery_event.call_args_list]
        self.assertEqual(queued_events[0]["task_id"], "OWN-001")
        self.assertEqual(queued_events[0]["target_agent"], "Codex")

    def test_dispatcher_helper_claims_todo_when_owner_lane_is_disabled(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "disabled_agents": ["Gemini2"],
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                },
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Gemini2": ["Codex", "Claude"],
                }
            },
            "agents": {
                "gemini2": {"id": "gemini2", "display_name": "Gemini2", "provider": "gemini2"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {
            "tasks": [
                {
                    "id": "FB-009-SIDECAR-BFF-HANDOFF",
                    "status": "todo",
                    "owner": "Gemini2",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "FB-009",
                    "helper_kind": "bff_handoff_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "FB-009-SIDECAR-BFF-HANDOFF")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Gemini2")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-009-SIDECAR-BFF-HANDOFF")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_helper_claims_sidecar_when_idle_claim_allows_sidecars(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "claim_idle_work": True,
                    "claim_sidecars_when_idle": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Gemini2": ["Codex"],
                }
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "gemini2": {"id": "gemini2", "display_name": "Gemini2", "provider": "gemini2"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {
                    "id": "FB-009-SIDECAR-BFF-HANDOFF",
                    "status": "todo",
                    "owner": "Gemini2",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "FB-009",
                    "helper_kind": "bff_handoff_packet",
                },
            ]
        }
        persisted_status = {
            "tasks": [
                {
                    "id": "FB-009-SIDECAR-BFF-HANDOFF",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Gemini2",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "FB-009",
                    "helper_kind": "bff_handoff_packet",
                    "last_update": "2026-05-13T09:31:00Z",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "FB-009-SIDECAR-BFF-HANDOFF")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Gemini2")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-009-SIDECAR-BFF-HANDOFF")
        self.assertEqual(queued_event["target_agent"], "Codex")

    def test_dispatcher_does_not_helper_claim_sidecar_when_owner_is_only_busy(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex", "Claude", "Gemini"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "LP-005",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "LP-005", "status": "review_approved", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
                {
                    "id": "FB-009-SIDECAR-BFF-HANDOFF",
                    "status": "todo",
                    "owner": "Copilot",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "FB-009",
                    "helper_kind": "bff_handoff_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertFalse(changed)
        persist.assert_not_called()
        queue_delivery_event.assert_not_called()

    def test_dispatcher_helper_claims_in_progress_when_owner_lane_is_paused(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "paused_owner_task_statuses": ["in_progress"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Copilot", "Codex", "Claude"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "qwen": {
                        "provider": "qwen",
                        "blocked_until": "2999-01-01T00:00:00Z",
                        "summary": "Capacity / rate limit failure",
                    }
                }
            },
        }
        status = {
            "tasks": [
                {"id": "WB-006", "status": "in_progress", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-006")
        self.assertEqual(kwargs["new_owner"], "Copilot")
        self.assertEqual(kwargs["new_reviewer"], "Qwen")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-006")
        self.assertEqual(queued_event["target_agent"], "Copilot")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_does_not_helper_claim_in_progress_when_owner_lane_is_healthy(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "paused_owner_task_statuses": ["in_progress"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Copilot", "Codex", "Claude"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {
            "tasks": [
                {"id": "WB-006", "status": "in_progress", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_not_called()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-006")
        self.assertEqual(queued_event["target_agent"], "Qwen")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_helper_claims_in_progress_when_owner_has_higher_priority_load(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo", "in_progress"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Claude"],
                }
            },
            "agents": {
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "WB-005",
                    "provider": "qwen",
                    "agent_id": "qwen",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "WB-005", "status": "review_approved", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
                {"id": "WB-006", "status": "in_progress", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-006")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Qwen")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-006")
        self.assertEqual(queued_event["target_agent"], "Claude")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_helper_claim_uses_persisted_reassignment_timestamp_for_event_key(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo", "in_progress"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Claude"],
                }
            },
            "agents": {
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "WB-005", "status": "review_approved", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
                {
                    "id": "WB-006",
                    "status": "in_progress",
                    "owner": "Qwen",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "last_update": "2026-05-09T09:00:00Z",
                },
            ]
        }
        persisted_status = {
            "tasks": [
                {"id": "WB-005", "status": "review_approved", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
                {
                    "id": "WB-006",
                    "status": "in_progress",
                    "owner": "Claude",
                    "reviewer": "Qwen",
                    "depends_on": [],
                    "last_update": "2026-05-09T10:00:00Z",
                    "next": "Helper-claimed by Claude while Qwen completes higher-priority work.",
                },
            ]
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "WB-005",
                    "provider": "qwen",
                    "agent_id": "qwen",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertIn('"last_update": "2026-05-09T10:00:00Z"', queued_event["key"])
        self.assertEqual(queued_event["target_agent"], "Claude")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_reassigns_mainline_qwen_owner_before_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
                "reviewer_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "WB-011", "status": "todo", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }
        normalized_status = {
            "tasks": [
                {"id": "WB-011", "status": "todo", "owner": "Codex", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, normalized_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-011")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-011")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_reassigns_mainline_qwen_reviewer_before_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
                "reviewer_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "WB-012", "status": "review", "owner": "Claude", "reviewer": "Qwen", "depends_on": []},
            ]
        }
        normalized_status = {
            "tasks": [
                {"id": "WB-012", "status": "review", "owner": "Claude", "reviewer": "Codex", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, normalized_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-012")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-012")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "review_ready_dispatch")

    def test_dispatcher_still_allows_qwen_sidecar_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "agents": {
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "WB-013-SIDECAR-REVIEW",
                    "status": "todo",
                    "owner": "Qwen",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "WB-013",
                    "helper_kind": "review_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-013-SIDECAR-REVIEW")
        self.assertEqual(queued_event["target_agent"], "Qwen")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_prefers_mainline_work_over_sidecar_review(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "BFF-FINAL-006",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Codex2",
                    "depends_on": [],
                },
                {
                    "id": "BFF-FINAL-010-SIDECAR-SMOKE",
                    "status": "review",
                    "owner": "Codex2",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "BFF-FINAL-010",
                    "helper_kind": "smoke_matrix",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "BFF-FINAL-006")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_uses_spare_codex_slot_for_sidecar_after_primary_work(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2"]
        for index in range(1, 3):
            config["agents"][f"codex1_{index}"] = {
                "id": f"codex1_{index}",
                "display_name": "Codex",
                "provider": f"codex1-{index}",
                "adapter": "codex",
                "dispatch_slot_for": "codex",
            }
            config["providers"][f"codex1-{index}"] = {
                "delivery_mode": "codex",
                "quota_group": "codex1",
            }
        status = {
            "tasks": [
                {
                    "id": "SPRINT-8-CLOSEOUT",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "last_update": "2026-05-18T04:05:09Z",
                },
                {
                    "id": "OSS-FINRL-V2-001-SIDECAR-REVIEW",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Gemini2",
                    "depends_on": [],
                    "last_update": "2026-05-18T02:55:51Z",
                    "task_class": "sidecar",
                    "helper_parent": "OSS-FINRL-V2-001",
                    "helper_kind": "review_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertEqual(queued_task_ids, ["SPRINT-8-CLOSEOUT", "OSS-FINRL-V2-001-SIDECAR-REVIEW"])

    def test_dispatcher_dispatches_existing_sidecar_when_parent_blocks_new_sidecars(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "agents": {
                "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "BFF-FINAL-010-SIDECAR-BFF-HANDOFF",
                    "status": "todo",
                    "owner": "Codex2",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "BFF-FINAL-010",
                    "helper_kind": "bff_handoff_packet",
                },
            ]
        }
        state = {
            "queue": {"events": {}},
            "workers": {},
            "chair_rotation": {"sidecar_blocked_parents": ["BFF-FINAL-010"]},
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "BFF-FINAL-010-SIDECAR-BFF-HANDOFF")
        self.assertEqual(queued_event["target_agent"], "Codex2")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_skips_agent_when_provider_report_says_not_auto_ready(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {"id": "AUTO-READY-001", "status": "review", "owner": "Codex", "reviewer": "Claude2", "depends_on": []},
            ]
        }
        provider_report = {
            "agent_adapters": {
                "claude2": {
                    "supported": True,
                    "can_auto_deliver": False,
                    "notes": "Claude CLI is installed but not authenticated.",
                }
            },
            "providers": {
                "claude2": {
                    "local_cli_worker_supported": False,
                    "supports_auto_approve": False,
                    "auth_ready": False,
                }
            },
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(
                config,
                {"queue": {"events": {}}, "workers": {}},
                provider_report=provider_report,
            )

        self.assertFalse(changed)
        queue_delivery_event.assert_not_called()

    def test_skips_duplicate_start_when_active_worker_already_exists(self) -> None:
        current_task = {
            "id": "P3-001",
            "status": "review",
            "owner": "Claude",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-06T05:30:43Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Gemini",
            "review_ready_dispatch",
            {"P3-001": current_task},
        )
        queue_payload = {
            "event_id": "evt-current",
            "event_key": current_event["key"],
            "task_id": "P3-001",
            "target_agent": "gemini",
            "target_display_name": "Gemini",
            "reason": "review_ready_dispatch",
            "message": "wake",
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "gemini-run-1": {
                    "run_id": "gemini-run-1",
                    "queue_event_id": "evt-current",
                    "status": "running",
                }
            },
        }

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("duplicate queue event should not start another worker")),
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True) as sync_dispatched_task_status,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-current"]
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["run_id"], "gemini-run-1")
        sync_dispatched_task_status.assert_called_once_with(self.config, queue_payload)


class DispatchStatusSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "scripts" / "ai_status.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        self.status_path = self.root / "ai-status.json"
        self.status_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "APP-002-W1-FRONT-HANDOFF",
                            "status": "todo",
                            "owner": "Copilot",
                            "reviewer": "Codex",
                            "depends_on": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.status_path),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
        }

    def test_sync_dispatched_task_status_starts_owned_todo_task(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "copilot",
            "target_display_name": "Copilot",
            "reason": "owned_ready_dispatch",
        }

        with mock.patch.object(supervisor.subprocess, "run", return_value=mock.Mock(returncode=0, stderr="", stdout="")) as run_mock:
            changed = supervisor.sync_dispatched_task_status(self.config, event)

        self.assertTrue(changed)
        command = run_mock.call_args.args[0]
        self.assertEqual(command[2], "start")
        self.assertEqual(command[3], "APP-002-W1-FRONT-HANDOFF")
        self.assertIn("Supervisor auto-started", command[4])
        self.assertEqual(run_mock.call_args.kwargs["env"]["AI_NAME"], "Copilot")

    def test_sync_dispatched_task_status_skips_review_dispatch(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "review_ready_dispatch",
        }

        with mock.patch.object(supervisor.subprocess, "run") as run_mock:
            changed = supervisor.sync_dispatched_task_status(self.config, event)

        self.assertFalse(changed)
        run_mock.assert_not_called()


class RunOnceSupervisorStateTests(unittest.TestCase):
    def test_discussion_planning_needs_materialization_for_accepted_approved_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            class FakeResolver:
                def __init__(self, _task_lookup):
                    pass

                def snapshot(self, _task_id):
                    return None

            with mock.patch.object(supervisor, "TaskResolver", FakeResolver):
                self.assertTrue(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_discussion_planning_skips_materialization_when_current_session_tasks_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "LOOP-001",
                                "source_plane": "planning",
                                "source_ref": {"session_id": "phase3-session"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            self.assertFalse(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_discussion_planning_skips_materialization_when_session_already_stamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "materialized_at": "2026-04-19T03:40:25Z",
                "session_id": "phase7-session",
                "proposed_execution_tasks": [{"id": "OSS-004A"}],
            }

            self.assertFalse(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_heartbeat_lag_seconds_reports_gap(self) -> None:
        lag = supervisor.heartbeat_lag_seconds(
            "2026-04-06T12:00:00Z",
            "2026-04-06T12:00:12Z",
        )

        self.assertEqual(lag, 12.0)

    def test_run_once_re_stamps_current_pid_after_watch_reload(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }
        saved_state: dict[str, object] = {}

        def capture_save(_config: dict[str, object], state: dict[str, object]) -> None:
            saved_state.clear()
            saved_state.update(state)

        with (
            mock.patch.object(supervisor, "write_supervisor_pid"),
            mock.patch.object(supervisor, "load_runtime_state", side_effect=[dict(initial_state), dict(initial_state)]),
            mock.patch.object(supervisor, "prune_stale_approvals", return_value=False),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "run_scan", return_value=False),
            mock.patch.object(supervisor, "poll_workers", return_value=False),
            mock.patch.object(supervisor, "reconcile_queue_records", return_value=False),
            mock.patch.object(supervisor, "prune_event_queue", return_value=False),
            mock.patch.object(supervisor, "load_discussion_planning_state", return_value=None),
            mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False),
            mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=False),
            mock.patch.object(supervisor, "dispatch_chair_review", return_value=False),
            mock.patch.object(supervisor, "process_queue", return_value=False),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "trim_worker_history"),
            mock.patch.object(supervisor, "trim_seen_events"),
            mock.patch.object(supervisor, "save_runtime_state", side_effect=capture_save),
        ):
            supervisor.run_once(config, watch=True, replay=False)

        self.assertEqual(saved_state["supervisor"]["pid"], os.getpid())
        self.assertIsNotNone(saved_state["supervisor"]["last_heartbeat_at"])
        self.assertEqual(saved_state["supervisor"]["started_at"], saved_state["supervisor"]["last_heartbeat_at"])

    def test_run_once_prioritizes_discussion_planning_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }

        with (
            mock.patch.object(supervisor, "write_supervisor_pid"),
            mock.patch.object(supervisor, "load_runtime_state", side_effect=[dict(initial_state), dict(initial_state)]),
            mock.patch.object(supervisor, "prune_stale_approvals", return_value=False),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "run_scan", return_value=False),
            mock.patch.object(supervisor, "sync_coordination_files", return_value=False),
            mock.patch.object(supervisor, "poll_workers", return_value=False),
            mock.patch.object(supervisor, "reconcile_queue_records", return_value=False),
            mock.patch.object(supervisor, "prune_event_queue", return_value=False),
            mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False),
            mock.patch.object(supervisor, "load_discussion_planning_state", return_value={"status": "active", "planning_mode": "discussion_planning", "readouts": {}}),
            mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=True) as dispatch_discussion_planning,
            mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=False) as dispatch_ready_tasks,
            mock.patch.object(supervisor, "dispatch_chair_review", return_value=False) as dispatch_chair_review,
            mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=False) as dispatch_underutilization_sidecars,
            mock.patch.object(supervisor, "process_queue", return_value=False),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "trim_worker_history"),
            mock.patch.object(supervisor, "trim_seen_events"),
            mock.patch.object(supervisor, "save_runtime_state"),
        ):
            supervisor.run_once(config, watch=True, replay=False)

        dispatch_discussion_planning.assert_called_once()
        dispatch_ready_tasks.assert_not_called()
        dispatch_chair_review.assert_not_called()
        dispatch_underutilization_sidecars.assert_not_called()

    def test_run_once_dispatches_ready_tasks_after_failure_loop_chair_review(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(supervisor, "write_supervisor_pid"))
            stack.enter_context(mock.patch.object(supervisor, "load_runtime_state", return_value=dict(initial_state)))
            stack.enter_context(mock.patch.object(supervisor, "continue_or_skip_empty"))
            stack.enter_context(mock.patch.object(supervisor, "expire_provider_dispatch_pauses", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_stale_approvals", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "load_provider_report", return_value={}))
            stack.enter_context(mock.patch.object(supervisor, "sync_coordination_files", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "poll_workers", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "reconcile_queue_records", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_event_queue", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "load_discussion_planning_state", return_value=None))
            stack.enter_context(mock.patch.object(supervisor, "auto_materialize_discussion_planning", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "watchdog_safe_mode_active", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "chair_review_failure_loop_details", return_value=[{"task_id": "ASST-OCGW-004"}]))
            dispatch_chair_review = stack.enter_context(mock.patch.object(supervisor, "dispatch_chair_review", return_value=True))
            dispatch_ready_tasks = stack.enter_context(mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=True))
            dispatch_underutilization_sidecars = stack.enter_context(
                mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=False)
            )
            stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
            stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
            stack.enter_context(mock.patch.object(supervisor, "prune_orphan_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "maybe_auto_commit_archive", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
            stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
            stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
            changed = supervisor.run_once(config, watch=False, replay=False)

        self.assertTrue(changed)
        dispatch_chair_review.assert_called_once()
        dispatch_ready_tasks.assert_called_once()
        dispatch_underutilization_sidecars.assert_called_once()

    def test_run_once_watchdog_safe_mode_suppresses_new_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "watchdog": {
                "safe_mode_until": "2999-01-01T00:00:00Z",
                "safe_mode_reason": "stale_heartbeat",
            },
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(supervisor, "write_supervisor_pid"))
            stack.enter_context(mock.patch.object(supervisor, "load_runtime_state", return_value=dict(initial_state)))
            stack.enter_context(mock.patch.object(supervisor, "continue_or_skip_empty"))
            stack.enter_context(mock.patch.object(supervisor, "expire_provider_dispatch_pauses", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_stale_approvals", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "load_provider_report", return_value={}))
            stack.enter_context(mock.patch.object(supervisor, "sync_coordination_files", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "poll_workers", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "reconcile_queue_records", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_event_queue", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False))
            stack.enter_context(
                mock.patch.object(
                    supervisor,
                    "load_discussion_planning_state",
                    return_value={"status": "active", "planning_mode": "discussion_planning"},
                )
            )
            stack.enter_context(mock.patch.object(supervisor, "auto_materialize_discussion_planning", return_value=False))
            dispatch_discussion_planning = stack.enter_context(
                mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=True)
            )
            dispatch_ready_tasks = stack.enter_context(mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=True))
            dispatch_chair_review = stack.enter_context(mock.patch.object(supervisor, "dispatch_chair_review", return_value=True))
            dispatch_underutilization_sidecars = stack.enter_context(
                mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=True)
            )
            process_queue = stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=True))
            stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
            stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
            stack.enter_context(mock.patch.object(supervisor, "prune_orphan_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
            stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
            stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
            write_activity_log = stack.enter_context(mock.patch.object(supervisor, "write_activity_log"))
            changed = supervisor.run_once(config, watch=False, replay=False)

        self.assertTrue(changed)
        dispatch_discussion_planning.assert_not_called()
        dispatch_ready_tasks.assert_not_called()
        dispatch_chair_review.assert_not_called()
        dispatch_underutilization_sidecars.assert_not_called()
        process_queue.assert_not_called()
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "watchdog_safe_mode_dispatch_suppressed")

    def test_run_supervisor_cycle_logs_and_continues_after_error(self) -> None:
        config = {"supervisor": {}}

        with (
            mock.patch.object(supervisor, "run_once", side_effect=RuntimeError("boom")) as run_once,
            mock.patch.object(supervisor, "console_log") as console_log,
        ):
            changed = supervisor.run_supervisor_cycle(config, watch=True, replay=True, quiet=True, verbose=False)

        self.assertFalse(changed)
        run_once.assert_called_once_with(
            config,
            watch=True,
            replay=True,
            quiet=True,
            verbose=False,
            once=False,
        )
        self.assertIn("RuntimeError: boom", console_log.call_args.args[0])
        self.assertTrue(console_log.call_args.kwargs["quiet"])

    def test_run_once_auto_materializes_accepted_session_before_execution_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            script_dir = root / "scripts"
            script_dir.mkdir(parents=True, exist_ok=True)
            (script_dir / "planning_state.py").write_text("# test stub\n", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(status_file),
                    "activity_log": str(root / "activity-log.jsonl"),
                },
                "schema": {
                    "tasks_path": "tasks",
                    "task_id_field": "id",
                    "assignee_field": "owner",
                    "reviewer_field": "reviewer",
                },
                "supervisor": {},
                "watcher": {},
                "ready_dispatcher": {},
                "providers": {},
                "agents": {},
            }
            initial_state = {
                "queue": {"events": {}},
                "workers": {},
                "approvals": {},
                "supervisor": {
                    "pid": 61209,
                    "started_at": "2026-04-05T12:44:57Z",
                    "last_heartbeat_at": "2026-04-06T04:17:26Z",
                },
            }
            planning_state = {
                "status": "accepted",
                "planning_mode": "discussion_planning",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(supervisor, "write_supervisor_pid"))
                stack.enter_context(mock.patch.object(supervisor, "load_runtime_state", return_value=dict(initial_state)))
                stack.enter_context(mock.patch.object(supervisor, "continue_or_skip_empty"))
                stack.enter_context(mock.patch.object(supervisor, "prune_stale_approvals", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "load_provider_report", return_value={}))
                stack.enter_context(mock.patch.object(supervisor, "sync_coordination_files", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "poll_workers", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "reconcile_queue_records", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "prune_event_queue", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "load_discussion_planning_state", return_value=planning_state))
                dispatch_discussion_planning = stack.enter_context(
                    mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=False)
                )
                dispatch_ready_tasks = stack.enter_context(
                    mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=True)
                )
                stack.enter_context(mock.patch.object(supervisor, "dispatch_chair_review", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
                stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
                stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
                stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
                stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
                stack.enter_context(
                    mock.patch.object(
                        supervisor,
                        "TaskResolver",
                        type(
                            "FakeResolver",
                            (),
                            {
                                "__init__": lambda self, _task_lookup: None,
                                "snapshot": lambda self, _task_id: None,
                            },
                        ),
                    )
                )
                run_mock = stack.enter_context(
                    mock.patch.object(
                        supervisor.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess(
                            args=["python3", str(script_dir / "planning_state.py"), "materialize"],
                            returncode=0,
                            stdout="materialized",
                            stderr="",
                        ),
                    )
                )
                changed = supervisor.run_once(config, watch=False, replay=False)

            self.assertTrue(changed)
            dispatch_discussion_planning.assert_not_called()
            dispatch_ready_tasks.assert_called_once()
            run_mock.assert_called_once()
            self.assertEqual(run_mock.call_args.args[0][-1], "materialize")


class SupervisorRuntimeFocusTests(unittest.TestCase):
    def test_discussion_planning_focus_overrides_execution_draining(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "event-queue.jsonl").write_text("", encoding="utf-8")
            config = {
                "paths": {
                    "event_queue": str(root / "event-queue.jsonl"),
                    "status_file": str(root / "ai-status.json"),
                },
                "schema": {
                    "tasks_path": "tasks",
                    "task_id_field": "id",
                    "assignee_field": "owner",
                    "reviewer_field": "reviewer",
                },
                "ready_dispatcher": {},
            }
            state = {
                "queue": {"events": {}},
                "workers": {
                    "exec-worker": {
                        "status": "manual_pending",
                        "reason": "owned_dispatch",
                    },
                    "planning-worker": {
                        "status": "started",
                        "reason": "discussion_planning_baton_dispatch",
                        "request_snapshot": {
                            "reason": "discussion_planning_baton_dispatch",
                            "metadata": {
                                "planning": {
                                    "session_id": "phase7-2026-04-18-ep4-ep5-execution-proof",
                                    "mode": "discussion_planning",
                                }
                            },
                        },
                    },
                },
                "supervisor": {
                    "pid": 61209,
                    "focus_mode": "execution",
                    "mode_status": "active",
                },
            }
            planning_state = {
                "status": "active",
                "planning_mode": "discussion_planning",
                "session_id": "phase7-2026-04-18-ep4-ep5-execution-proof",
            }

            supervisor.stamp_supervisor_runtime_state(
                config,
                state,
                planning_state=planning_state,
                heartbeat_at="2026-04-18T14:40:00Z",
                lifecycle="running",
            )

            supervisor_state = state["supervisor"]
            self.assertEqual(supervisor_state["focus_mode"], "planning")
            self.assertEqual(supervisor_state["mode_status"], "active")
            self.assertIsNone(supervisor_state["mode_switch_requested"])
            self.assertEqual(supervisor_state["last_mode_switch_at"], "2026-04-18T14:40:00Z")
            self.assertEqual(supervisor_state["mode_occupancy"]["planning"]["running"], 1)
            self.assertEqual(supervisor_state["mode_occupancy"]["execution"]["pending"], 1)


class DiscussionPlanningDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "event_queue": str(self.root / "event-queue.jsonl"),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "manual_pending",
                    "retry_backoff",
                    "suspended_approval",
                    "stalled",
                    "fallback",
                ],
            },
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
            },
            "providers": {
                "claude": {"delivery_mode": "claude_cli"},
                "gemini": {"delivery_mode": "gemini"},
                "codex": {"delivery_mode": "codex"},
                "copilot": {"delivery_mode": "copilot_local"},
                "qwen": {"delivery_mode": "qwen"},
            },
        }

    def test_dispatch_discussion_planning_queues_pending_readouts(self) -> None:
        planning_state = {
            "session_id": "phase1-2026-04-11",
            "status": "active",
            "planning_mode": "discussion_planning",
            "summary": "Plan the Pantheon backend completion wave.",
            "baton_owner": "Codex",
            "next_reviewer": "Qwen",
            "current_round": 0,
            "consensus_status": "draft",
            "readouts": {
                "Claude": {"status": "pending"},
                "Codex": {"status": "pending"},
                "Gemini": {"status": "pending"},
                "Qwen": {"status": "pending"},
                "Copilot": {"status": "pending"},
            },
        }
        state = {"queue": {"events": {}}, "workers": {}, "seen_event_keys": {}}

        with mock.patch.object(supervisor, "selected_shared_files", return_value=[self.root / "shared.md"]):
            changed = supervisor.dispatch_discussion_planning(self.config, state, planning_state)

        self.assertTrue(changed)
        rows = [
            json.loads(line)
            for line in (self.root / "event-queue.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 5)
        codex_event = next(row for row in rows if row["target_display_name"] == "Codex")
        self.assertEqual(codex_event["reason"], "discussion_planning_baton_dispatch")
        self.assertIn("starter-draft.md", "\n".join(codex_event["target_files"]))
        claude_event = next(row for row in rows if row["target_display_name"] == "Claude")
        self.assertIn("consensus-packet.md", "\n".join(claude_event["target_files"]))

    def test_dispatch_discussion_planning_uses_active_session_paths_and_owned_outputs(self) -> None:
        planning_dir = "docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop"
        planning_state = {
            "session_id": "phase3-2026-04-14-pantheon-console-loop",
            "planning_dir": planning_dir,
            "session_file": f"{planning_dir}/planning-session.json",
            "status": "active",
            "planning_mode": "discussion_planning",
            "summary": "Formalize the Pantheon Console closed loop.",
            "objective": "Define the canonical closed-loop coordination protocol and execution backlog for all 8 workbenches.",
            "baton_owner": "Codex",
            "next_reviewer": "Qwen",
            "current_round": 0,
            "consensus_status": "draft",
            "brief_files": [
                "Pantheon_總索引版系統分析文件.md",
                ".coordination/README.md",
            ],
            "artifacts": {
                "planning_readme": {"path": f"{planning_dir}/README.md"},
                "starter_draft": {"path": f"{planning_dir}/starter-draft.md"},
                "consensus_packet": {"path": f"{planning_dir}/consensus-packet.md"},
            },
            "expected_outputs": [
                {
                    "id": "coordination_loop_spec",
                    "path": f"{planning_dir}/coordination-loop-spec.md",
                    "owner": "Codex",
                }
            ],
            "readouts": {
                "Claude": {"status": "pending", "path": f"{planning_dir}/claude-readout.md"},
                "Codex": {"status": "pending", "path": f"{planning_dir}/codex-readout.md"},
                "Gemini": {"status": "pending", "path": f"{planning_dir}/gemini-readout.md"},
                "Qwen": {"status": "pending", "path": f"{planning_dir}/qwen-readout.md"},
                "Copilot": {"status": "pending", "path": f"{planning_dir}/copilot-readout.md"},
            },
        }
        state = {"queue": {"events": {}}, "workers": {}, "seen_event_keys": {}}

        with mock.patch.object(supervisor, "selected_shared_files", return_value=[self.root / "shared.md"]):
            changed = supervisor.dispatch_discussion_planning(self.config, state, planning_state)

        self.assertTrue(changed)
        rows = [
            json.loads(line)
            for line in (self.root / "event-queue.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        codex_event = next(row for row in rows if row["target_display_name"] == "Codex")
        self.assertIn(f"{planning_dir}/README.md", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/planning-session.json", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/codex-readout.md", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/coordination-loop-spec.md", codex_event["target_files"])
        self.assertIn("本輪目標：Define the canonical closed-loop coordination protocol", codex_event["message"])

    def test_planning_worker_matches_assignment_without_taskboard_entry(self) -> None:
        worker = {
            "task_id": "phase1-2026-04-11-backend-completion",
            "agent_id": "codex",
            "request_snapshot": {
                "reason": "discussion_planning_baton_dispatch",
                "metadata": {
                    "planning": {
                        "session_id": "phase1-2026-04-11-backend-completion",
                        "mode": "discussion_planning",
                    }
                },
            },
        }

        self.assertTrue(supervisor.worker_matches_current_assignment(self.config, worker, {}))
        self.assertFalse(supervisor.higher_priority_ready_task_exists(self.config, worker, {}))

    def test_coordination_worker_matches_assignment_without_taskboard_entry(self) -> None:
        worker = {
            "task_id": "F-042",
            "agent_id": "codex",
            "request_snapshot": {
                "reason": "coordination:ui-done",
                "metadata": {
                    "coordination": {
                        "feature_id": "F-042",
                        "worker_kind": "front-sync-worker",
                        "payload_type": "ui-done",
                    }
                },
            },
        }

        self.assertTrue(supervisor.worker_matches_current_assignment(self.config, worker, {}))
        self.assertFalse(supervisor.higher_priority_ready_task_exists(self.config, worker, {}))

    def test_detect_worker_failure_ignores_code_snippet_error_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "worker.log"
            log_path.write_text(
                "\n".join(
                    [
                        "class CommandStatusResponse(BaseModel):",
                        "    result: Optional[Dict[str, Any]] = None",
                        "    error: Optional[Dict[str, Any]] = None,",
                        "    audit: Optional[Dict[str, Any]] = None",
                        "class BffErrorEnvelope(BaseModel):",
                        "    error: BffErrorPayload",
                        "class ErrorResponse(BffErrorEnvelope):",
                        "    error: BFFError",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            worker = {"log_path": str(log_path)}
            self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_sidecar_review_does_not_preempt_mainline_worker(self) -> None:
        worker = {
            "task_id": "BFF-FINAL-006",
            "agent_id": "codex",
            "request_snapshot": {"reason": "owned_ready_dispatch"},
        }
        task_map = {
            "BFF-FINAL-006": {
                "id": "BFF-FINAL-006",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Codex2",
                "depends_on": [],
            },
            "BFF-FINAL-010-SIDECAR-SMOKE": {
                "id": "BFF-FINAL-010-SIDECAR-SMOKE",
                "status": "review",
                "owner": "Codex2",
                "reviewer": "Codex",
                "depends_on": [],
                "task_class": "sidecar",
                "helper_parent": "BFF-FINAL-010",
                "helper_kind": "smoke_matrix",
            },
        }

        self.assertFalse(supervisor.higher_priority_ready_task_exists(self.config, worker, task_map))

    def test_priority_preemption_respects_logical_agent_slot_capacity(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2", "codex1_3", "codex1_4"]
        for slot_id in config["agents"]["codex"]["worker_slots"]:
            config["agents"][slot_id] = {
                "id": slot_id,
                "display_name": "Codex",
                "dispatch_slot_for": "codex",
                "provider": slot_id.replace("_", "-"),
            }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-high": {
                    "run_id": "run-high",
                    "task_id": "BFF-CONSOL-016",
                    "agent_id": "codex1_1",
                    "logical_agent_id": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                },
                "run-low": {
                    "run_id": "run-low",
                    "task_id": "BFF-CONSOL-017",
                    "agent_id": "codex1_2",
                    "logical_agent_id": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                },
            },
        }
        task_map = {
            "BFF-CONSOL-016": {
                "id": "BFF-CONSOL-016",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Codex2",
                "depends_on": [],
            },
            "BFF-CONSOL-017": {
                "id": "BFF-CONSOL-017",
                "status": "todo",
                "owner": "Codex",
                "reviewer": "Codex2",
                "depends_on": [],
            },
        }

        self.assertFalse(
            supervisor.higher_priority_ready_task_exists(
                config,
                state["workers"]["run-low"],
                task_map,
                state,
            )
        )

    def test_slotted_worker_is_not_preempted_for_non_urgent_owned_backlog(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2", "codex1_3", "codex1_4"]
        for slot_id in config["agents"]["codex"]["worker_slots"]:
            config["agents"][slot_id] = {
                "id": slot_id,
                "display_name": "Codex",
                "dispatch_slot_for": "codex",
                "provider": slot_id.replace("_", "-"),
            }
        state = {
            "queue": {"events": {}},
            "workers": {
                f"run-low-{index}": {
                    "run_id": f"run-low-{index}",
                    "task_id": f"BFF-CONSOL-0{20 + index}",
                    "agent_id": f"codex1_{index}",
                    "logical_agent_id": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
                for index in range(1, 5)
            },
        }
        task_map = {
            f"BFF-CONSOL-0{20 + index}": {
                "id": f"BFF-CONSOL-0{20 + index}",
                "status": "todo",
                "owner": "Codex",
                "reviewer": "Claude",
                "depends_on": [],
            }
            for index in range(1, 5)
        }
        task_map["BFF-CONSOL-099"] = {
            "id": "BFF-CONSOL-099",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
        }

        with mock.patch.object(supervisor, "load_event_queue", return_value=[]):
            self.assertFalse(
                supervisor.higher_priority_ready_task_exists(
                    config,
                    state["workers"]["run-low-1"],
                    task_map,
                    state,
                )
            )

    def test_dead_coordination_worker_is_completed_without_taskboard_entry(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "F-042",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                    "request_snapshot": {
                        "reason": "coordination:ui-done",
                        "metadata": {
                            "coordination": {
                                "feature_id": "F-042",
                                "worker_kind": "front-sync-worker",
                                "payload_type": "ui-done",
                            }
                        },
                    },
                }
            },
        }
        status = {"tasks": []}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_completed")


class OrphanedQueueEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "activity-log.jsonl"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                    "retry_backoff",
                    "stalled",
                ],
                "orphaned_queue_event_grace_seconds": 300,
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }

    def _write_event(self, payload: dict[str, object]) -> None:
        (self.root / "event-queue.jsonl").write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_outstanding_delivery_indexes_ignore_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        agents, task_agents, event_keys = supervisor.outstanding_delivery_indexes(self.config, state)

        self.assertEqual(agents, set())
        self.assertEqual(task_agents, set())
        self.assertEqual(event_keys, set())

    def test_process_queue_skips_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        with mock.patch.object(supervisor, "start_worker_for_request") as start_worker:
            changed = supervisor.process_queue(self.config, state, provider_report={})

        self.assertFalse(changed)
        start_worker.assert_not_called()
        self.assertEqual(state["queue"]["events"], {})

    def test_prune_event_queue_drops_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.prune_event_queue(self.config, state)

        self.assertTrue(changed)
        self.assertEqual((self.root / "event-queue.jsonl").read_text(encoding="utf-8"), "")
        self.assertEqual(state["queue"]["events"], {})
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "queue_event_pruned")


class UnderutilizationSidecarDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (self.root / "sidecar_catalog.json").write_text('{"templates": []}\n', encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "sidecar_catalog": str(self.root / "sidecar_catalog.json"),
                "activity_log": str(self.root / "activity-log.jsonl"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "manual_pending",
                    "retry_backoff",
                    "suspended_approval",
                    "stalled",
                    "fallback",
                ],
                "dependency_done_statuses": ["done"],
            },
            "underutilization_dispatch": {
                "enabled": True,
                "require_recent_chair_signal": False,
                "threshold_ratio": 0.5,
                "continuous_window_seconds": 900,
                "cooldown_seconds": 900,
                "max_active_sidecars_per_agent": 1,
                "productive_worker_statuses": ["running", "waiting_approval", "suspended_approval", "retry_backoff"],
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"},
            },
        }

    def test_waits_full_window_before_creating_sidecars(self) -> None:
        state = {"queue": {"events": {}}, "workers": {}, "underutilization": {}}

        with (
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("should not create before the window")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertIsNotNone(state["underutilization"]["below_threshold_since"])
        self.assertIsNone(state["underutilization"].get("last_sidecar_wave_at"))
        write_activity_log.assert_not_called()

    def test_creates_visible_sidecar_after_continuous_low_utilization_window(self) -> None:
        self.config["underutilization_dispatch"]["require_recent_chair_signal"] = True
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "TEL-001",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                }
            },
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
            "chair_rotation": {
                "sidecar_approved_until": "2026-04-10T01:00:00Z",
                "sidecar_approval_max_sidecars": 1,
            },
        }
        parent_task = {
            "id": "APP-001",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": [],
            "title": "Define BFF query surfaces",
            "summary_zh": "整理 operator console 與 workbench 的 BFF query contract。",
            "artifacts": ["services/control-plane/bff/"],
            "last_update": "2026-04-10T00:05:00Z",
        }
        created_sidecar = {
            "id": "APP-001-SIDECAR-BFF-HANDOFF",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "todo",
            "owner": "Gemini",
            "reviewer": "Claude",
            "depends_on": [],
            "title": "Prepare APP-001 BFF and frontend handoff packet",
            "summary_zh": "平行支援 APP-001，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。",
            "artifacts": ["support/sidecars/APP-001/APP-001-SIDECAR-BFF-HANDOFF.md"],
            "task_class": "sidecar",
            "auto_generated": True,
            "helper_parent": "APP-001",
            "helper_kind": "bff_handoff_packet",
            "mutates_canonical": False,
            "auto_created_by": "supervisor-underutilization",
            "last_update": "2026-04-10T00:16:05Z",
        }
        status_before = {"tasks": [parent_task]}
        status_after = {"tasks": [parent_task, created_sidecar]}

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[status_before, status_after]),
            mock.patch.object(supervisor, "load_sidecar_catalog", return_value=[]),
            mock.patch.object(supervisor, "create_sidecar_task", return_value=(True, "")) as create_sidecar_task,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        create_sidecar_task.assert_called_once()
        kwargs = create_sidecar_task.call_args.kwargs
        self.assertEqual(kwargs["sidecar_id"], "APP-001-SIDECAR-BFF-HANDOFF")
        self.assertEqual(kwargs["owner"], "Gemini")
        self.assertEqual(kwargs["reviewer"], "Claude")
        self.assertEqual(kwargs["helper_parent"], "APP-001")
        self.assertEqual(kwargs["helper_kind"], "bff_handoff_packet")
        self.assertFalse(kwargs["mutates_canonical"])
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "APP-001-SIDECAR-BFF-HANDOFF")
        self.assertEqual(queued_event["target_agent"], "Gemini")
        self.assertEqual(queued_event["task"]["task_class"], "sidecar")
        self.assertEqual(state["underutilization"]["last_sidecar_wave_at"], "2026-04-10T00:16:05Z")
        self.assertIn("created 1 visible sidecar", state["underutilization"]["last_sidecar_wave_reason"])
        self.assertIn("APP-001-SIDECAR-BFF-HANDOFF", state.get("tasks", {}))
        activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertIn("sidecar_task_created", activity_types)
        self.assertIn("sidecar_wave_started", activity_types)

    def test_creates_all_assignable_sidecars_when_wave_limit_is_unset(self) -> None:
        self.config["underutilization_dispatch"]["require_recent_chair_signal"] = True
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
            "chair_rotation": {
                "sidecar_approved_until": "2026-04-10T01:00:00Z",
                "sidecar_approval_max_sidecars": 1,
            },
        }
        candidates = [
            {
                "sidecar_id": f"APP-00{index}-SIDECAR-REVIEW",
                "parent_task_id": f"APP-00{index}",
                "kind": "review_packet",
                "phase": "Phase 5",
                "title": f"Prepare APP-00{index} review packet",
                "summary_zh": "支援 review packet。",
                "reviewer": "Reviewer",
                "depends_on": [],
                "artifacts": [f"support/sidecars/APP-00{index}/packet.md"],
                "mutates_canonical": False,
                "priority": index,
            }
            for index in range(1, 4)
        ]
        status_before = {"tasks": []}
        status_after = [
            {
                "tasks": [
                    {
                        "id": candidate["sidecar_id"],
                        "status": "todo",
                        "owner": "Codex",
                        "reviewer": "Reviewer",
                        "task_class": "sidecar",
                        "helper_parent": candidate["parent_task_id"],
                        "helper_kind": candidate["kind"],
                    }
                ]
            }
            for candidate in candidates
        ]

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[status_before, *status_after]),
            mock.patch.object(supervisor, "eligible_idle_agents_for_sidecars", return_value=["Codex", "Gemini", "Claude"]),
            mock.patch.object(supervisor, "build_catalog_sidecar_candidates", return_value=candidates),
            mock.patch.object(supervisor, "create_sidecar_task", return_value=(True, "")) as create_sidecar_task,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(create_sidecar_task.call_count, 3)
        self.assertEqual(queue_delivery_event.call_count, 3)
        self.assertIn("created 3 visible sidecar", state["underutilization"]["last_sidecar_wave_reason"])

    def test_explicit_sidecar_wave_limit_still_caps_for_incident_response(self) -> None:
        self.config["underutilization_dispatch"]["max_new_sidecars_per_wave"] = 1
        self.config["underutilization_dispatch"]["require_recent_chair_signal"] = True
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
            "chair_rotation": {"sidecar_approved_until": "2026-04-10T01:00:00Z"},
        }
        candidates = [
            {
                "sidecar_id": f"APP-00{index}-SIDECAR-REVIEW",
                "parent_task_id": f"APP-00{index}",
                "kind": "review_packet",
                "phase": "Phase 5",
                "title": f"Prepare APP-00{index} review packet",
                "summary_zh": "支援 review packet。",
                "reviewer": "Reviewer",
                "depends_on": [],
                "artifacts": [f"support/sidecars/APP-00{index}/packet.md"],
                "mutates_canonical": False,
                "priority": index,
            }
            for index in range(1, 3)
        ]
        status_after = {
            "tasks": [
                {
                    "id": "APP-001-SIDECAR-REVIEW",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Reviewer",
                    "task_class": "sidecar",
                    "helper_parent": "APP-001",
                    "helper_kind": "review_packet",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[{"tasks": []}, status_after]),
            mock.patch.object(supervisor, "eligible_idle_agents_for_sidecars", return_value=["Codex", "Gemini"]),
            mock.patch.object(supervisor, "build_catalog_sidecar_candidates", return_value=candidates),
            mock.patch.object(supervisor, "create_sidecar_task", return_value=(True, "")) as create_sidecar_task,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(create_sidecar_task.call_count, 1)

    def test_chair_blocked_parent_prevents_new_sidecar_generation_only(self) -> None:
        self.config["underutilization_dispatch"]["require_recent_chair_signal"] = True
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
            "chair_rotation": {
                "sidecar_approved_until": "2026-04-10T01:00:00Z",
                "sidecar_approval_max_sidecars": 1,
                "sidecar_blocked_parents": ["APP-001"],
            },
        }
        status = {
            "tasks": [
                {
                    "id": "APP-001",
                    "phase": "Phase 5: Persona and Application Surfaces",
                    "status": "todo",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "title": "Define BFF query surfaces",
                    "summary_zh": "整理 operator console 與 workbench 的 BFF query contract。",
                    "artifacts": ["services/control-plane/bff/"],
                    "last_update": "2026-04-10T00:05:00Z",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_sidecar_catalog", return_value=[]),
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("blocked parent should not create a new sidecar")),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["underutilization"]["last_sidecar_wave_at"], "2026-04-10T00:16:05Z")
        self.assertEqual(
            state["underutilization"]["last_sidecar_wave_reason"],
            "underutilized but no sidecar candidates matched the catalog or dynamic fallback",
        )

    def test_resets_underutilization_timer_when_utilization_recovers(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-1": {"run_id": "run-1", "task_id": "REG-004", "agent_id": "codex", "provider": "codex", "status": "running"},
                "run-2": {"run_id": "run-2", "task_id": "OSS-001", "agent_id": "gemini", "provider": "gemini", "status": "running"},
            },
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
        }

        changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertIsNone(state["underutilization"]["below_threshold_since"])

    def test_cooldown_prevents_duplicate_sidecar_wave(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": "2026-04-10T00:10:00Z",
                "last_sidecar_wave_reason": "already created a wave recently",
            },
        }

        with (
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("cooldown should prevent new sidecars")),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:20:00Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertFalse(changed)
        self.assertEqual(state["underutilization"]["last_sidecar_wave_reason"], "already created a wave recently")

    def test_skips_duplicate_signature_when_matching_sidecar_already_exists(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
        }
        parent_task = {
            "id": "APP-001",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": [],
            "title": "Define BFF query surfaces",
            "summary_zh": "整理 operator console 與 workbench 的 BFF query contract。",
            "artifacts": ["services/control-plane/bff/"],
            "last_update": "2026-04-10T00:05:00Z",
        }
        existing_sidecar = {
            "id": "APP-001-SIDECAR-BFF-HANDOFF",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "done",
            "owner": "Gemini",
            "reviewer": "Claude",
            "depends_on": [],
            "title": "Prepare APP-001 BFF and frontend handoff packet",
            "summary_zh": "已完成支援包。",
            "artifacts": ["support/sidecars/APP-001/APP-001-SIDECAR-BFF-HANDOFF.md"],
            "task_class": "sidecar",
            "auto_generated": True,
            "helper_parent": "APP-001",
            "helper_kind": "bff_handoff_packet",
            "mutates_canonical": False,
            "auto_created_by": "supervisor-underutilization",
            "last_update": "2026-04-10T00:07:00Z",
        }
        status = {"tasks": [parent_task, existing_sidecar]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_sidecar_catalog", return_value=[]),
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("duplicate signature should not create another sidecar")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(
            state["underutilization"]["last_sidecar_wave_reason"],
            "underutilized but no sidecar candidates matched the catalog or dynamic fallback",
        )
        activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertEqual(activity_types, ["sidecar_wave_skipped"])

    def test_requires_recent_chair_signal_when_gate_enabled(self) -> None:
        self.config["underutilization_dispatch"]["require_recent_chair_signal"] = True
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {},
            "chair_rotation": {"sidecar_approved_until": None},
        }

        changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertFalse(changed)
        self.assertEqual(
            state["underutilization"]["last_sidecar_wave_reason"],
            "awaiting chair review approval before creating sidecars",
        )


class ChairReviewDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
                "state_file": str(self.root / "state.json"),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "providers": {},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "manual_pending",
                    "retry_backoff",
                    "suspended_approval",
                    "stalled",
                    "fallback",
                ],
                "dependency_done_statuses": ["done"],
            },
            "chair_review": {
                "enabled": True,
                "cooldown_seconds": 1800,
                "candidates": ["Codex", "Codex2", "Claude", "Claude2"],
                "output_dir": str(self.root / "chair-reviews"),
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
            },
        }

    def test_dispatch_chair_review_rotates_and_records_pending_report(self) -> None:
        state = {"queue": {"events": {}}, "workers": {}, "chair_rotation": {"current_index": 0}}

        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:00:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_chair_agent"], "Codex")
        self.assertEqual(state["chair_rotation"]["current_index"], 1)
        self.assertEqual(state["chair_rotation"]["pending_review_agent"], "Codex")
        self.assertTrue(str(state["chair_rotation"]["pending_review_path"]).endswith("-codex.md"))
        self.assertTrue(str(state["chair_rotation"]["pending_decision_path"]).endswith("-codex.json"))
        events = supervisor.load_event_queue(self.config)
        self.assertEqual(len(events), 1)
        self.assertTrue(any(path.endswith("-codex.md") for path in events[0]["target_files"]))
        self.assertTrue(any(path.endswith("-codex.json") for path in events[0]["target_files"]))
        self.assertIn("Required Decision JSON Output", events[0]["message"])
        self.assertEqual(events[0]["metadata"]["workspace_task_id"], "chair-review-20260428-120000-codex")

    def test_dispatch_chair_review_skips_when_planning_active(self) -> None:
        state = {"queue": {"events": {}}, "workers": {}, "chair_rotation": {"current_index": 0}}

        changed = supervisor.dispatch_chair_review(
            self.config,
            state,
            planning_state={"status": "active", "planning_mode": "discussion_planning", "readouts": {}},
        )

        self.assertFalse(changed)

    def test_dispatch_chair_review_respects_global_worker_cap(self) -> None:
        self.config["ready_dispatcher"]["max_concurrent_workers"] = 2
        state = {"queue": {"events": {}}, "workers": {}, "chair_rotation": {"current_index": 0}}

        with (
            mock.patch.object(supervisor, "active_worker_indexes", return_value=(set(), set())),
            mock.patch.object(
                supervisor,
                "outstanding_delivery_indexes",
                return_value=({"codex", "codex2"}, set(), set()),
            ),
            mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertFalse(changed)
        self.assertEqual(supervisor.load_event_queue(self.config), [])

    def test_dispatch_chair_review_falls_through_busy_candidate(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                }
            },
            "chair_rotation": {"current_index": 0},
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:00:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_chair_agent"], "Codex2")
        self.assertEqual(state["chair_rotation"]["current_index"], 2)

    def test_dispatch_chair_review_falls_through_not_auto_ready_candidate(self) -> None:
        self.config["chair_review"]["candidates"] = ["Claude2", "Codex"]
        state = {"queue": {"events": {}}, "workers": {}, "chair_rotation": {"current_index": 0}}
        provider_report = {
            "agent_adapters": {
                "claude2": {
                    "supported": True,
                    "can_auto_deliver": False,
                    "notes": "Claude2 profile is not authenticated.",
                },
                "codex": {"supported": True, "can_auto_deliver": True},
            },
            "providers": {
                "claude2": {
                    "local_cli_worker_supported": False,
                    "supports_auto_approve": False,
                    "auth_ready": False,
                },
                "codex": {
                    "local_cli_worker_supported": True,
                    "supports_auto_approve": True,
                },
            },
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:00:00Z"),
            mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
        ):
            changed = supervisor.dispatch_chair_review(
                self.config,
                state,
                planning_state=None,
                provider_report=provider_report,
            )

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_chair_agent"], "Codex")
        self.assertEqual(state["chair_rotation"]["current_index"], 0)

    def test_dispatch_chair_review_bypasses_cooldown_for_pending_approval(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "chair_rotation": {
                "current_index": 0,
                "last_chair_run_at": "2026-04-28T12:00:00Z",
            },
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(
                supervisor,
                "safe_load_approval_state",
                return_value={
                    "pending": [
                        {
                            "approval_id": "apr-1",
                            "provider": "claude",
                            "task_id": "SVC-GOVERNANCE-API",
                            "worker_run_id": "run-1",
                            "tool_name": "Bash",
                            "risk_class": "needs_review",
                            "created_at": "2026-04-28T12:00:10Z",
                            "tool_input_preview": "docker compose config --quiet",
                        }
                    ],
                    "history": [],
                },
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:05:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        events = supervisor.load_event_queue(self.config)
        self.assertEqual(events[0]["reason"], "chair_review:approval_triage")
        self.assertIn("approval_id=apr-1", events[0]["message"])

    def test_dispatch_chair_review_uses_idle_candidate_with_primary_work_for_pending_approval(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-codex2": {
                    "run_id": "run-codex2",
                    "agent_id": "codex2",
                    "provider": "codex2",
                    "status": "running",
                }
            },
            "chair_rotation": {
                "current_index": 0,
                "last_chair_run_at": "2026-04-28T12:00:00Z",
            },
        }
        status = {
            "tasks": [
                {
                    "id": "PRIMARY-CODEX",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(
                supervisor,
                "safe_load_approval_state",
                return_value={
                    "pending": [
                        {
                            "approval_id": "apr-1",
                            "provider": "claude",
                            "task_id": "BFF-LUV-FE-002",
                            "worker_run_id": "run-1",
                            "tool_name": "Agent",
                            "risk_class": "unknown",
                            "created_at": "2026-04-28T12:00:10Z",
                            "tool_input_preview": "Explore execute-plans repo BFF structure",
                        }
                    ],
                    "history": [],
                },
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:05:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_chair_agent"], "Codex")
        events = supervisor.load_event_queue(self.config)
        self.assertEqual(events[0]["reason"], "chair_review:approval_triage")

    def test_dispatch_chair_review_bypasses_cooldown_for_failure_loop(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "chair_rotation": {
                "current_index": 0,
                "last_chair_run_at": "2026-04-28T12:00:00Z",
            },
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                        "last_reason": "Worker exited before terminal state.",
                    }
                }
            },
        }
        status = {"tasks": [{"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"}]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:05:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        events = supervisor.load_event_queue(self.config)
        self.assertEqual(events[0]["reason"], "chair_review:reassignment_triage")
        self.assertIn("Repeated Failure Details:", events[0]["message"])
        self.assertIn("task=T-REVIEW", events[0]["message"])
        self.assertIn('"reassignment_actions"', events[0]["message"])

    def test_dispatch_chair_review_skips_agent_in_failure_loop(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "chair_rotation": {
                "current_index": 1,
                "last_chair_run_at": "2026-04-28T12:00:00Z",
            },
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                        "last_reason": "Worker exited before terminal state.",
                    }
                }
            },
        }
        status = {"tasks": [{"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"}]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:05:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_chair_agent"], "Claude")

    def test_dispatch_ready_skips_task_waiting_for_chair_reassignment_triage(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                        "last_reason": "Worker exited before terminal state.",
                    }
                }
            },
        }
        status = {"tasks": [{"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"}]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertFalse(changed)
        queue_delivery_event.assert_not_called()

    def test_dispatch_ready_skips_only_task_agent_pair_in_failure_loop(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                        "last_reason": "Worker exited before terminal state.",
                    }
                }
            },
        }
        status = {
            "tasks": [
                {"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"},
                {"id": "T-FINALIZE", "status": "review_approved", "owner": "Codex2", "reviewer": "Codex"},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "T-FINALIZE")
        self.assertEqual(queued_event["target_agent"], "Codex2")
        self.assertEqual(queued_event["reason"], "owned_finalize_dispatch")

    def test_chair_worker_matches_current_assignment_without_task(self) -> None:
        worker = {
            "run_id": "chair-1",
            "agent_id": "codex",
            "provider": "codex",
            "task_id": None,
            "status": "running",
            "request_snapshot": {
                "reason": "chair_review:operational_review",
                "metadata": {
                    "chair": {
                        "mode": "chair_review",
                        "review_path": str(self.root / "chair-reviews" / "review.md"),
                    }
                },
            },
        }

        self.assertTrue(supervisor.worker_matches_current_assignment(self.config, worker, {}))

    def test_refresh_chair_review_approves_sidecars_from_decision_json(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nApprove a small sidecar wave.\n", encoding="utf-8")
        decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "max_sidecars": 2,
                    "reason": "Idle workers are available and runnable support work exists.",
                    "blocked_by": [],
                    "blocked_sidecar_parents": ["SVC-RUNTIME-CONTROL-CLOSEOUT"],
                    "recommended_focus": ["SVC-EVIDENCE"],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
            },
        }

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        rotation = state["chair_rotation"]
        self.assertIsNone(rotation["pending_review_path"])
        self.assertIsNone(rotation["pending_decision_path"])
        self.assertEqual(rotation["sidecar_approved_until"], "2026-04-28T13:00:00Z")
        self.assertEqual(rotation["sidecar_approval_max_sidecars"], 2)
        self.assertTrue(rotation["last_review_sidecar_approved"])
        self.assertEqual(rotation["last_review_decision"], "approve_sidecars")
        self.assertEqual(rotation["sidecar_blocked_parents"], ["SVC-RUNTIME-CONTROL-CLOSEOUT"])
        self.assertEqual(rotation["last_review_recommended_focus"], ["SVC-EVIDENCE"])
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "chair_review_approved_sidecars")

    def test_refresh_chair_review_syncs_completed_worktree_artifacts(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        workspace_path = self.root / "workers" / "pantheon" / "chair-review-20260428-codex"
        workspace_review_path = workspace_path / "chair-reviews" / "20260428-codex.md"
        workspace_decision_path = workspace_review_path.with_suffix(".json")
        workspace_review_path.parent.mkdir(parents=True, exist_ok=True)
        workspace_review_path.write_text("# Summary\n\nApprove sidecars from worktree.\n", encoding="utf-8")
        workspace_decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "reason": "Chair artifacts were produced in the isolated worker workspace.",
                    "blocked_by": [],
                    "recommended_focus": [],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-chair": {"status": "completed"}}},
            "workers": {
                "chair-run": {
                    "status": "completed",
                    "workspace_path": str(workspace_path),
                    "request_snapshot": {
                        "reason": "chair_review:operational_review",
                        "metadata": {"chair": {"review_path": str(review_path)}},
                    },
                }
            },
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-chair",
                "pending_review_agent": "Codex",
            },
        }

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(review_path.read_text(encoding="utf-8"), workspace_review_path.read_text(encoding="utf-8"))
        self.assertEqual(decision_path.read_text(encoding="utf-8"), workspace_decision_path.read_text(encoding="utf-8"))
        self.assertTrue(state["chair_rotation"]["last_review_valid"])
        event_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertIn("chair_review_artifact_synced_from_worktree", event_types)
        self.assertIn("chair_review_approved_sidecars", event_types)

    def test_refresh_chair_review_syncs_worktree_artifacts_before_state_reconciles(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex2.md"
        decision_path = review_path.with_suffix(".json")
        workspace_path = self.root / "workers" / "pantheon" / "chair-review-20260428-codex2"
        workspace_review_path = workspace_path / "chair-reviews" / "20260428-codex2.md"
        workspace_decision_path = workspace_review_path.with_suffix(".json")
        workspace_review_path.parent.mkdir(parents=True, exist_ok=True)
        workspace_review_path.write_text("# Summary\n\nApprove sidecars before reconcile.\n", encoding="utf-8")
        workspace_decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "reason": "Runner finished before worker state reconciled.",
                    "blocked_by": [],
                    "recommended_focus": [],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-chair": {"status": "started"}}},
            "workers": {
                "chair-run": {
                    "status": "running",
                    "workspace_path": str(workspace_path),
                    "request_snapshot": {
                        "reason": "chair_review:reassignment_triage",
                        "metadata": {"chair": {"review_path": str(review_path)}},
                    },
                    "runner_status": "completed",
                    "exit_code": 0,
                }
            },
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-chair",
                "pending_review_agent": "Codex2",
            },
        }

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:20:00Z"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        self.assertTrue(review_path.exists())
        self.assertTrue(decision_path.exists())
        self.assertTrue(state["chair_rotation"]["last_review_valid"])
        event_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertIn("chair_review_artifact_synced_from_worktree", event_types)
        self.assertIn("chair_review_approved_sidecars", event_types)

    def test_refresh_chair_review_denies_sidecars_and_clears_approval(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nHold sidecars.\n", encoding="utf-8")
        decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "deny_sidecars",
                    "sidecar_approved": False,
                    "reason": "Human approval queue is blocking execution.",
                    "blocked_by": ["pending human approval"],
                    "recommended_focus": [],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
                "sidecar_approved_until": "2026-04-28T13:00:00Z",
            },
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        rotation = state["chair_rotation"]
        self.assertIsNone(rotation["sidecar_approved_until"])
        self.assertFalse(rotation["last_review_sidecar_approved"])
        self.assertEqual(rotation["last_review_blocked_by"], ["pending human approval"])
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "chair_review_denied_sidecars")

    def test_refresh_chair_review_applies_approval_actions(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nApprove compose validation.\n", encoding="utf-8")
        decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "reason": "Execution can proceed.",
                    "blocked_by": [],
                    "recommended_focus": [],
                    "approval_actions": [
                        {
                            "approval_id": "apr-1",
                            "decision": "allow",
                            "reason": "Low-risk compose config validation.",
                            "remember": False,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
            },
        }

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(
                supervisor,
                "safe_load_approval_state",
                return_value={"pending": [{"approval_id": "apr-1"}], "history": []},
            ),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_review_approval_actions"][0]["approval_id"], "apr-1")
        resolve_approval.assert_called_once_with(
            self.config,
            "apr-1",
            decision="allow",
            note=f"Chair review {supervisor.relpath(review_path)}: Low-risk compose config validation.",
            remember=False,
        )

    def test_refresh_chair_review_applies_reassignment_actions(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nMove the stuck review lane.\n", encoding="utf-8")
        decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "reason": "Execution can proceed after moving the stuck reviewer.",
                    "blocked_by": [],
                    "recommended_focus": [],
                    "reassignment_actions": [
                        {
                            "task_id": "T-REVIEW",
                            "role": "reviewer",
                            "from": "Codex2",
                            "to": "Claude",
                            "reason": "Codex2 repeatedly exits without approve/reject.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                    }
                }
            },
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
            },
        }
        status = {"tasks": [{"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"}]}

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist_task_reassignment,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_review_reassignment_actions"][0]["to"], "Claude")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"], {})
        persist_task_reassignment.assert_called_once_with(
            self.config,
            task_id="T-REVIEW",
            new_owner="Codex",
            new_reviewer="Claude",
            message="Chair reassigned review from Codex2 to Claude: Codex2 repeatedly exits without approve/reject.",
            handoff_to="Claude",
            handoff_from="Codex2",
        )

    def test_refresh_chair_review_applies_blocked_owner_rescue_action(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nRescue blocked owner lane.\n", encoding="utf-8")
        decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "reason": "Execution can proceed after moving the auth-blocked owner lane.",
                    "blocked_by": [],
                    "recommended_focus": ["T-PUSH"],
                    "reassignment_actions": [
                        {
                            "task_id": "T-PUSH",
                            "role": "owner",
                            "from": "Gemini2",
                            "to": "Codex",
                            "reason": "Gemini2 PR push is blocked by authentication failure; Codex is an available fallback.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-PUSH:gemini2": {
                        "task_id": "T-PUSH",
                        "provider": "gemini2",
                        "count": 3,
                    }
                }
            },
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Claude",
            },
        }
        status = {
            "tasks": [
                {
                    "id": "T-PUSH",
                    "status": "blocked",
                    "owner": "Gemini2",
                    "reviewer": "Claude",
                    "waiting_for": "Gemini",
                    "next": "PR push blocked by auth failure.",
                }
            ],
            "blockers": [
                {
                    "task_id": "T-PUSH",
                    "owner": "Gemini2",
                    "waiting_for": "Gemini",
                    "status": "open",
                }
            ],
        }

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist_task_reassignment,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_review_reassignment_actions"][0]["to"], "Codex")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"], {})
        persist_task_reassignment.assert_called_once_with(
            self.config,
            task_id="T-PUSH",
            new_owner="Codex",
            new_reviewer="Claude",
            message=(
                "Chair reassigned owner from Gemini2 to Codex: Gemini2 PR push is blocked by authentication "
                "failure; Codex is an available fallback. Task returned to todo for a blocked-owner rescue dispatch."
            ),
            new_status="todo",
            handoff_to="Codex",
            handoff_from="Gemini2",
            resolve_open_blockers=True,
        )

    def test_chair_review_prompt_includes_pending_approval_details(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        with (
            mock.patch.object(
                supervisor,
                "safe_load_approval_state",
                return_value={
                    "pending": [
                        {
                            "approval_id": "apr-1",
                            "provider": "claude",
                            "task_id": "SVC-GOVERNANCE-API",
                            "worker_run_id": "run-1",
                            "tool_name": "Bash",
                            "risk_class": "needs_review",
                            "created_at": "2026-04-28T12:00:00Z",
                            "tool_input_preview": "docker compose config --quiet",
                        }
                    ]
                },
            ),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        ):
            message = supervisor.build_chair_review_message(self.config, {}, agent_name="Codex", review_path=review_path)

        self.assertIn("Pending Approval Details:", message)
        self.assertIn("approval_id=apr-1", message)
        self.assertIn("docker compose config --quiet", message)
        self.assertIn('"approval_actions"', message)

    def test_chair_review_prompt_includes_blocked_owner_rescue_candidates(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        status = {
            "tasks": [
                {
                    "id": "T-PUSH",
                    "status": "blocked",
                    "owner": "Gemini2",
                    "reviewer": "Claude",
                    "waiting_for": "Gemini",
                    "next": "PR push blocked by auth failure.",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "safe_load_approval_state", return_value={"pending": []}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "load_status", return_value=status),
        ):
            message = supervisor.build_chair_review_message(self.config, {}, agent_name="Codex", review_path=review_path)

        self.assertIn("Blocked Owner Rescue Candidates:", message)
        self.assertIn("task=T-PUSH", message)
        self.assertIn('targets=["Codex", "Codex2"]', message)

    def test_persist_task_reassignment_can_clear_blocked_owner_handoff(self) -> None:
        status_path = self.root / "ai-status.json"
        status_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "T-PUSH",
                            "status": "blocked",
                            "owner": "Gemini2",
                            "reviewer": "Claude",
                            "waiting_for": "Gemini",
                            "next": "PR push blocked by auth failure.",
                        }
                    ],
                    "blockers": [
                        {
                            "task_id": "T-PUSH",
                            "owner": "Gemini2",
                            "waiting_for": "Gemini",
                            "status": "open",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
        ):
            applied = supervisor.persist_task_reassignment(
                self.config,
                task_id="T-PUSH",
                new_owner="Codex",
                new_reviewer="Claude",
                message="Chair reassigned owner from Gemini2 to Codex.",
                new_status="todo",
                handoff_to="Codex",
                handoff_from="Gemini2",
                resolve_open_blockers=True,
            )

        self.assertTrue(applied)
        saved = json.loads(status_path.read_text(encoding="utf-8"))
        task = saved["tasks"][0]
        blocker = saved["blockers"][0]
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["owner"], "Codex")
        self.assertNotIn("waiting_for", task)
        self.assertEqual(blocker["status"], "resolved")
        self.assertEqual(blocker["resolution_ref"], "chair_reassignment:T-PUSH")

    def test_refresh_chair_review_invalid_decision_retries_next_chair(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nMalformed decision.\n", encoding="utf-8")
        decision_path.write_text('{"version": 1, "decision": "maybe"}\n', encoding="utf-8")
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "chair_rotation": {
                "last_chair_run_at": "2026-04-28T12:00:00Z",
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
            },
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        rotation = state["chair_rotation"]
        self.assertIsNone(rotation["pending_review_path"])
        self.assertIsNone(rotation["last_chair_run_at"])
        self.assertFalse(rotation["last_review_valid"])
        self.assertEqual(rotation["last_chair_problem"], "chair_review_invalid_schema")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "chair_review_invalid_schema")

    def test_refresh_chair_review_missing_report_retries_next_chair(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {
                "chair-1": {
                    "run_id": "chair-1",
                    "agent_id": "codex",
                    "provider": "codex",
                    "task_id": None,
                    "status": "completed",
                    "queue_event_id": "evt-1",
                    "request_snapshot": {
                        "reason": "chair_review:operational_review",
                        "metadata": {
                            "chair": {
                                "mode": "chair_review",
                                "review_path": str(review_path),
                                "decision_path": str(decision_path),
                            }
                        },
                    },
                }
            },
            "chair_rotation": {
                "last_chair_run_at": "2026-04-28T12:00:00Z",
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
            },
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        rotation = state["chair_rotation"]
        self.assertIsNone(rotation["pending_review_path"])
        self.assertIsNone(rotation["last_chair_run_at"])
        self.assertEqual(rotation["last_chair_problem"], "chair_review_missing_report")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "chair_review_missing_report")


class PollWorkersRecoveryTests(unittest.TestCase):
    def test_successful_chair_worker_does_not_scan_report_text_as_provider_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "chair.log"
            log_path.write_text(
                "+   - ASST-OCGW-004 recorded `codex1_1` auth failures with `not authenticated, please login first`.\n",
                encoding="utf-8",
            )
            config = {
                "schema": {"tasks_path": "tasks"},
                "supervisor": {"stall_after_seconds": 300},
                "ready_dispatcher": {
                    "active_worker_statuses": [
                        "running",
                        "started",
                        "waiting_approval",
                        "manual_pending",
                        "retry_backoff",
                        "suspended_approval",
                        "stalled",
                        "fallback",
                    ],
                    "worker_terminal_statuses": ["done", "review_approved", "review"],
                },
                "providers": {},
                "agents": {"codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"}},
            }
            state = {
                "queue": {"events": {"evt-chair": {"status": "started"}}},
                "workers": {
                    "chair-run": {
                        "run_id": "chair-run",
                        "provider": "codex2-1",
                        "agent_id": "codex2_1",
                        "task_id": None,
                        "status": "running",
                        "queue_event_id": "evt-chair",
                        "pid": 12345,
                        "log_path": str(log_path),
                        "runner_status": "completed",
                        "exit_code": 0,
                        "request_snapshot": {
                            "reason": "chair_review:reassignment_triage",
                            "metadata": {"chair": {"mode": "chair_review", "review_path": "chair-reviews/20260428-codex2.md"}},
                        },
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
                mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
                mock.patch.object(supervisor, "load_provider_report", return_value={}),
                mock.patch.object(supervisor, "retry_due_workers", return_value=False),
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "detect_worker_failure", side_effect=AssertionError("should not scan successful chair log")),
                mock.patch.object(supervisor, "mark_provider_dispatch_paused") as mark_provider_dispatch_paused,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                changed = supervisor.poll_workers(config, state)

            self.assertTrue(changed)
            self.assertEqual(state["workers"]["chair-run"]["status"], "completed")
            self.assertEqual(state["queue"]["events"]["evt-chair"]["status"], "completed")
            mark_provider_dispatch_paused.assert_not_called()
            self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_completed")

    def test_lower_priority_worker_is_superseded_when_finalize_backlog_exists(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": ["running", "started", "waiting_approval", "manual_pending", "retry_backoff", "suspended_approval", "stalled", "fallback"],
                "finalize_statuses": ["review_approved"],
                "dependency_done_statuses": ["done"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
                "codex": {"id": "codex", "display_name": "Codex"},
                "claude": {"id": "claude", "display_name": "Claude"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "FB-003",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 12345,
                    "last_event_at": "2026-04-06T09:00:00Z",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
                {"id": "EX-001", "status": "review_approved", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "superseded")
        self.assertIn("prioritize higher-priority review/finalize work", worker["last_error"])
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(12345)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")

    def test_parent_worker_is_not_superseded_for_its_sidecar_review(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "finalize_statuses": ["review_approved"],
                "dependency_done_statuses": ["done"],
                "active_worker_statuses": ["running", "started", "waiting_approval", "manual_pending", "retry_backoff", "suspended_approval", "stalled", "fallback"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
                "claude": {"id": "claude", "display_name": "Claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "BP5-SVC-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 12345,
                    "last_event_at": "2099-04-15T15:29:37Z",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Gemini",
                    "depends_on": [],
                },
                {
                    "id": "BP5-SVC-001-SIDECAR-ACCEPTANCE",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "auto_generated": True,
                    "helper_parent": "BP5-SVC-001",
                    "helper_kind": "acceptance_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertIsInstance(changed, bool)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "running")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")
        terminate_worker_pid.assert_not_called()
        write_activity_log.assert_not_called()

    def test_dead_worker_for_open_task_is_marked_failed_not_completed(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "EX-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "EX-001", "status": "in_progress", "owner": "Codex", "reviewer": "Claude"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(worker["last_error"], "Worker exited before the task reached a terminal status.")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_dead_waiting_approval_worker_is_failed_and_approval_is_resolved(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "OC-002",
                    "provider": "claude",
                    "agent_id": "claude",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "OC-002", "status": "review", "owner": "Codex", "reviewer": "Claude"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "worker_run_id": "run-1",
                    "task_id": "OC-002",
                    "provider": "claude",
                    "tool_name": "Bash",
                    "created_at": "2026-04-06T09:01:00Z",
                }
            ],
            "history": [],
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value=approval_state),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(worker["last_error"], "Worker exited while waiting for approval.")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        resolve_approval.assert_called_once_with(
            config,
            "apr-1",
            decision="deny",
            note="Auto-denied because the worker exited before approval could be applied.",
            remember=False,
        )
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_dead_claude_waiting_approval_worker_with_session_is_suspended(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                ]
            },
            "providers": {},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-004",
                    "provider": "claude",
                    "agent_id": "claude",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "session_id": "sess-123",
                    "resume_token": "sess-123",
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-004", "status": "in_progress", "owner": "Claude", "reviewer": "Codex"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "worker_run_id": "run-1",
                    "task_id": "LP-004",
                    "provider": "claude",
                    "tool_name": "ToolSearch",
                    "created_at": "2026-04-06T09:01:00Z",
                }
            ],
            "history": [],
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value=approval_state),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "suspended_approval")
        self.assertEqual(worker["deferred_action"], "apr-1")
        self.assertEqual(worker["last_event_at"], "2026-04-06T09:01:00Z")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "manual_pending")
        resolve_approval.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_waiting_approval")

    def test_dead_claude2_waiting_approval_worker_with_session_is_suspended(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                ]
            },
            "providers": {"claude2": {"delivery_mode": "claude_cli"}},
            "agents": {
                "claude2": {"id": "claude2", "display_name": "Claude2"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-005",
                    "provider": "claude2",
                    "agent_id": "claude2",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "session_id": "sess-456",
                    "resume_token": "sess-456",
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-005", "status": "in_progress", "owner": "Claude2", "reviewer": "Codex"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-2",
                    "worker_run_id": "run-1",
                    "task_id": "LP-005",
                    "provider": "claude2",
                    "tool_name": "ToolSearch",
                    "created_at": "2026-04-06T09:01:00Z",
                }
            ],
            "history": [],
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value=approval_state),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "suspended_approval")
        self.assertEqual(worker["deferred_action"], "apr-2")
        self.assertEqual(worker["last_event_at"], "2026-04-06T09:01:00Z")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "manual_pending")
        resolve_approval.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_waiting_approval")

    def test_dead_stale_worker_is_reaped_when_task_assignment_moved(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "name": "Codex"},
                "claude": {"id": "claude", "name": "Claude"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "EX-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "manual_pending",
                    "queue_event_id": "evt-1",
                    "pid": None,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "EX-001", "status": "review", "owner": "Grok", "reviewer": "Claude"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "superseded")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")

    def test_stalled_worker_returns_to_running_after_new_log_activity(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-002",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                    "last_event_at": "2026-04-06T14:20:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-002", "status": "in_progress", "owner": "Codex", "reviewer": "Copilot"}]}

        def bump_log_activity(_config, worker):
            worker["last_event_at"] = "2026-04-06T14:31:28Z"

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=bump_log_activity),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "running")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_recovered")

    def test_stalled_worker_is_terminated_after_extended_stall(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["todo", "in_progress"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "FB-003",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                    "last_event_at": "2026-04-06T14:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=lambda *_args, **_kwargs: None),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "failed")
        terminate_worker_pid.assert_called_once_with(1234)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_alive_worker_is_superseded_after_reassignment(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
                "gemini": {"id": "gemini", "display_name": "Gemini"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "REG-002",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 2222,
                    "last_event_at": "2026-04-06T14:19:47Z",
                }
            },
        }
        status = {"tasks": [{"id": "REG-002", "status": "review", "owner": "Codex", "reviewer": "Gemini"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "superseded")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(2222)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")

    def test_alive_chair_worker_is_completed_after_valid_artifacts_apply(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {"codex2": {"id": "codex2", "display_name": "Codex2"}},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            review_path = root / "20260601-125122-codex2.md"
            decision_path = root / "20260601-125122-codex2.json"
            review_path.write_text("# Review\n", encoding="utf-8")
            decision_path.write_text('{"version":1}\n', encoding="utf-8")
            state = {
                "queue": {"events": {"evt-chair": {"status": "started"}}},
                "chair_rotation": {
                    "last_review_path": str(review_path),
                    "last_review_decision_path": str(decision_path),
                    "last_review_valid": True,
                },
                "workers": {
                    "run-chair": {
                        "run_id": "run-chair",
                        "task_id": None,
                        "provider": "codex2-1",
                        "agent_id": "codex2_1",
                        "status": "running",
                        "queue_event_id": "evt-chair",
                        "pid": 4242,
                        "last_event_at": "2026-06-01T12:58:00Z",
                        "request_snapshot": {
                            "reason": "chair_review:reassignment_triage",
                            "metadata": {"chair": {"review_path": str(review_path)}},
                        },
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
                mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
                mock.patch.object(supervisor, "load_provider_report", return_value={}),
                mock.patch.object(supervisor, "retry_due_workers", return_value=False),
                mock.patch.object(supervisor, "pid_is_alive", return_value=True),
                mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as terminate_worker_pid,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
                mock.patch.object(supervisor, "utc_now", return_value="2026-06-01T12:59:30Z"),
            ):
                changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-chair"]
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(worker["last_event_at"], "2026-06-01T12:59:30Z")
        self.assertEqual(state["queue"]["events"]["evt-chair"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(4242)
        payload = write_activity_log.call_args.args[1]
        self.assertEqual(payload["type"], "worker_completed")
        self.assertIn("artifacts were accepted", payload["message"])


class SingleSupervisorGuardTests(unittest.TestCase):
    def test_cmdline_match_requires_supervisor_as_executable_or_python_script(self) -> None:
        script = str(Path(supervisor.__file__).resolve())

        self.assertTrue(supervisor.cmdline_is_supervisor_process(["python3", ".orchestrator/supervisor.py", "--verbose"]))
        self.assertTrue(supervisor.cmdline_is_supervisor_process(["python3", script, "--poll-interval", "15"]))
        self.assertTrue(supervisor.cmdline_is_supervisor_process([".orchestrator/supervisor.py", "--once"]))

    def test_cmdline_match_ignores_wrapper_processes(self) -> None:
        self.assertFalse(
            supervisor.cmdline_is_supervisor_process(["timeout", "20s", "python3", ".orchestrator/supervisor.py", "--once"])
        )
        self.assertFalse(
            supervisor.cmdline_is_supervisor_process(["bash", "-lc", "python3 .orchestrator/supervisor.py --verbose"])
        )

    def test_terminate_other_supervisors_kills_all_matching_except_self(self) -> None:
        # Singleton semantics: the flock winner terminates every other matching
        # supervisor regardless of PID ordering. 404 > 202 must still be killed
        # (PID wraparound previously let a higher-PID older supervisor survive).
        config = {"activity_log": "/tmp/fake-log.jsonl"}
        killed: list[tuple[int, int]] = []
        alive = {101: True, 202: True, 404: True}

        def fake_kill(pid: int, sig: int) -> None:
            killed.append((pid, sig))
            if sig in {supervisor.signal.SIGTERM, supervisor.signal.SIGKILL}:
                alive[pid] = False

        with (
            mock.patch.object(supervisor, "iter_matching_supervisor_pids", return_value=[101, 202, 404]),
            mock.patch.object(supervisor, "pid_is_alive", side_effect=lambda pid: alive.get(pid, False)),
            mock.patch.object(supervisor.os, "getpid", return_value=202),
            mock.patch.object(supervisor.os, "kill", side_effect=fake_kill),
            mock.patch.object(supervisor.time, "sleep"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            supervisor.terminate_other_supervisors(config)

        self.assertEqual(
            killed,
            [(101, supervisor.signal.SIGTERM), (404, supervisor.signal.SIGTERM)],
        )
        self.assertEqual(write_activity_log.call_count, 2)
        terminated_pids = {
            call.args[1]["old_pid"] for call in write_activity_log.call_args_list
        }
        self.assertEqual(terminated_pids, {101, 404})
        for call in write_activity_log.call_args_list:
            self.assertEqual(call.args[1]["type"], "supervisor_replaced")
            self.assertEqual(call.args[1]["new_pid"], 202)

    def test_singleton_lock_is_exclusive_and_released_on_close(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = {"paths": {"state_file": str(Path(tmp) / "runtime-state.json")}}

            # First acquirer wins.
            self.assertTrue(supervisor.acquire_singleton_lock(config))
            first_handle = supervisor._SINGLETON_LOCK_HANDLE
            self.assertIsNotNone(first_handle)
            # pid file content reflects the owner.
            self.assertEqual(
                supervisor.supervisor_lock_path(config).read_text(encoding="utf-8").strip(),
                str(supervisor.os.getpid()),
            )

            # A concurrent acquirer (separate fd) is refused while the lock is held.
            import fcntl as _fcntl

            contender = open(supervisor.supervisor_lock_path(config), "a+", encoding="utf-8")
            try:
                with self.assertRaises(OSError):
                    _fcntl.flock(
                        contender.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB
                    )
            finally:
                contender.close()

            # Releasing (process exit simulated by closing the fd) frees the lock.
            first_handle.close()
            regained = open(supervisor.supervisor_lock_path(config), "a+", encoding="utf-8")
            try:
                _fcntl.flock(regained.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            finally:
                _fcntl.flock(regained.fileno(), _fcntl.LOCK_UN)
                regained.close()


class WorktreeDirtClassificationTests(unittest.TestCase):
    def test_clean_status(self) -> None:
        self.assertEqual(supervisor._classify_worktree_dirt(""), ("clean", []))
        self.assertEqual(supervisor._classify_worktree_dirt("\n  \n"), ("clean", []))

    def test_scratch_only_is_reusable(self) -> None:
        # Exactly the dirt that jammed the fleet: brief modified + review re-staged.
        status = (
            "MM .orchestrator/task-briefs/mgmt_ai_persist_p1_attach_007.md\n"
            "D  .orchestrator/reviews/mgmt_ai_persist_p1_attach_007_review.md\n"
        )
        kind, paths = supervisor._classify_worktree_dirt(status)
        self.assertEqual(kind, "scratch_only")
        self.assertEqual(
            set(paths),
            {
                ".orchestrator/task-briefs/mgmt_ai_persist_p1_attach_007.md",
                ".orchestrator/reviews/mgmt_ai_persist_p1_attach_007_review.md",
            },
        )

    def test_real_product_dirt_still_blocks(self) -> None:
        status = (
            " M .orchestrator/task-briefs/asst_integ_004.md\n"
            " M services/control-plane/bff/main.py\n"
        )
        kind, paths = supervisor._classify_worktree_dirt(status)
        self.assertEqual(kind, "real")
        self.assertEqual(paths, [])

    def test_rename_uses_new_path(self) -> None:
        status = "R  old/file.py -> services/new/file.py\n"
        kind, _ = supervisor._classify_worktree_dirt(status)
        self.assertEqual(kind, "real")


class WorkerReassignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "owner_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
                "reviewer_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude"},
                "gemini": {"display_name": "Gemini"},
                "codex": {"display_name": "Codex"},
                "grok": {"display_name": "Grok"},
            },
        }

    def test_reassigns_review_task_to_new_reviewer_after_repeated_failure(self) -> None:
        worker = {
            "task_id": "P3-001",
            "agent_id": "gemini",
            "retry_count": 1,
            "run_id": "gemini-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-001",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Gemini",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                worker,
                "status: 429",
            )

        self.assertEqual(reassigned_to, "Codex")
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "P3-001")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        self.assertEqual(kwargs["handoff_to"], "Codex")
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_reassigned")

    def test_reassign_review_skips_paused_reviewer_candidates(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "reviewer_fallbacks": {
                    "Claude": ["Codex", "Qwen", "Copilot", "Gemini"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude", "provider": "claude"},
                "qwen": {"display_name": "Qwen", "provider": "qwen"},
                "codex": {"display_name": "Codex", "provider": "codex"},
                "copilot": {"display_name": "Copilot", "provider": "copilot"},
                "gemini": {"display_name": "Gemini", "provider": "gemini"},
            },
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "qwen": {
                        "provider": "qwen",
                        "blocked_until": "2099-01-01T00:00:00Z",
                    }
                }
            }
        }
        worker = {
            "task_id": "P3-002",
            "agent_id": "claude",
            "retry_count": 1,
            "run_id": "claude-run-2",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-002",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                config,
                state,
                worker,
                "status: 401 unauthorized",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Copilot")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Copilot")

    def test_reassign_review_can_fall_back_to_codex2_when_codex_is_owner(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "reviewer_fallbacks": {
                    "Claude": ["Codex", "Codex2", "Qwen", "Copilot", "Gemini"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude", "provider": "claude"},
                "qwen": {"display_name": "Qwen", "provider": "qwen"},
                "codex": {"display_name": "Codex", "provider": "codex"},
                "codex2": {"display_name": "Codex2", "provider": "codex2"},
                "copilot": {"display_name": "Copilot", "provider": "copilot"},
                "gemini": {"display_name": "Gemini", "provider": "gemini"},
            },
        }
        worker = {
            "task_id": "P3-003",
            "agent_id": "claude",
            "retry_count": 1,
            "run_id": "claude-run-3",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-003",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                config,
                worker,
                "Credit balance is too low",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Codex2")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Codex2")

    def test_reassigns_owned_task_to_new_owner_after_repeated_failure(self) -> None:
        worker = {
            "task_id": "LP-003",
            "agent_id": "gemini",
            "retry_count": 1,
            "run_id": "gemini-run-2",
        }
        status = {
            "tasks": [
                {
                    "id": "LP-003",
                    "status": "in_progress",
                    "owner": "Gemini",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                worker,
                "status: 429",
            )

        self.assertEqual(reassigned_to, "Codex")
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "LP-003")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        self.assertEqual(kwargs["new_status"], "todo")
        self.assertIn("Task returned to todo until Codex starts a fresh run.", kwargs["message"])


class WorkerPreemptionSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "owner_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
                "reviewer_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude"},
                "gemini": {"display_name": "Gemini"},
                "codex": {"display_name": "Codex"},
                "grok": {"display_name": "Grok"},
            },
        }

    def test_sync_preempted_owned_task_returns_in_progress_task_to_todo(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        worker = {
            "task_id": "BP5-CICD-001",
            "agent_id": "codex",
            "provider": "codex",
            "request_snapshot": {"reason": "owned_ready_dispatch"},
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-CICD-001",
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Gemini",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_json") as write_json,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-15T16:09:52Z"),
        ):
            synced = supervisor.sync_preempted_task_status(config, worker)

        self.assertTrue(synced)
        task = status["tasks"][0]
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["last_update"], "2026-04-15T16:09:52Z")
        self.assertIn("returned to todo until a fresh run restarts it", task["next"])
        write_json.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_preempted_synced")

    def test_sync_preempted_finalize_task_keeps_review_approved(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        worker = {
            "task_id": "BP5-SVC-001",
            "agent_id": "codex",
            "provider": "codex",
            "request_snapshot": {"reason": "owned_finalize_dispatch"},
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Qwen",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_json") as write_json,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-15T16:09:52Z"),
        ):
            synced = supervisor.sync_preempted_task_status(config, worker)

        self.assertTrue(synced)
        task = status["tasks"][0]
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["last_update"], "2026-04-15T16:09:52Z")
        self.assertIn("task remains review_approved", task["next"])
        write_json.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_preempted_synced")

    def test_reassigns_finalize_task_to_new_owner_after_repeated_failure(self) -> None:
        config = {
            **self.config,
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "worker_reassignment": {
                **self.config["worker_reassignment"],
                "owner_fallbacks": {
                    **self.config["worker_reassignment"]["owner_fallbacks"],
                    "Claude": ["Qwen", "Grok", "Gemini"],
                },
                "reviewer_fallbacks": {
                    **self.config["worker_reassignment"]["reviewer_fallbacks"],
                    "Claude": ["Qwen", "Grok", "Gemini"],
                },
            },
            "agents": {
                **self.config["agents"],
                "qwen": {"display_name": "Qwen"},
            },
        }
        worker = {
            "task_id": "RUN-001",
            "agent_id": "claude",
            "retry_count": 5,
            "run_id": "claude-run-9",
        }
        status = {
            "tasks": [
                {
                    "id": "RUN-001",
                    "status": "review_approved",
                    "owner": "Claude",
                    "reviewer": "Codex",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                config,
                worker,
                "You've hit your limit · resets 1pm (Asia/Taipei)",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Grok")
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "RUN-001")
        self.assertEqual(kwargs["new_owner"], "Grok")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        self.assertIsNone(kwargs["new_status"])


class WorkerOsDuplicateGuardTests(unittest.TestCase):
    def _make_fake_proc(self, entries: dict[int, str | None]) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup_proc, root)
        for pid, cmdline in entries.items():
            pid_dir = root / str(pid)
            pid_dir.mkdir()
            if cmdline is not None:
                (pid_dir / "cmdline").write_bytes(cmdline.replace(" ", "\x00").encode("utf-8"))
        return root

    @staticmethod
    def _cleanup_proc(root: Path) -> None:
        for child in root.glob("**/*"):
            if child.is_file():
                child.unlink()
        for child in sorted(root.glob("**/*"), reverse=True):
            if child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_scan_groups_pids_by_agent_marker(self) -> None:
        proc = self._make_fake_proc(
            {
                111: "codex exec -C /tmp/wt 你的 auto worker 身分是：Codex 。 Task ID: T1",
                222: "codex exec -C /tmp/wt2 你的 auto worker 身分是：Codex2 。 Task ID: T2",
                333: "codex exec -C /tmp/wt3 你的 auto worker 身分是：Codex 。 Task ID: T3",
                444: "vim",
                555: None,
            }
        )
        result = supervisor.scan_live_worker_pids_by_agent(proc_root=proc)
        self.assertEqual(sorted(result["Codex"]), [111, 333])
        self.assertEqual(result["Codex2"], [222])
        self.assertNotIn("vim", result)

    def test_scan_skips_self_pid(self) -> None:
        proc = self._make_fake_proc(
            {os.getpid(): "auto worker 身分是：Codex"}
        )
        self.assertEqual(supervisor.scan_live_worker_pids_by_agent(proc_root=proc), {})

    def test_block_reason_flags_live_duplicate(self) -> None:
        config = {
            "agents": {"codex": {"provider": "codex"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state: dict = {}
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with (
            mock.patch.object(supervisor, "display_name_for", return_value="Codex"),
            mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False),
            mock.patch.object(
                supervisor, "scan_live_worker_pids_by_agent",
                return_value={"Codex": [42, 99]},
            ),
        ):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex", provider_report
            )
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("Codex", reason)
        self.assertIn("42", reason)
        self.assertIn("99", reason)

    def test_block_reason_passes_when_guard_disabled(self) -> None:
        config = {
            "agents": {"codex": {"provider": "codex"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": False},
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with (
            mock.patch.object(supervisor, "display_name_for", return_value="Codex"),
            mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False),
            mock.patch.object(
                supervisor, "scan_live_worker_pids_by_agent",
                return_value={"Codex": [42]},
            ) as scan,
        ):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, {}, "codex", provider_report
            )
        self.assertIsNone(reason)
        scan.assert_not_called()

    def test_block_reason_rejects_invalid_codex_service_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            (codex_home / "config.toml").write_text('service_tier = "priority"\n', encoding="utf-8")
            config = {
                "agents": {"codex": {"provider": "codex"}},
                "providers": {
                    "codex": {
                        "delivery_mode": "codex",
                        "codex": {"codex_home": str(codex_home)},
                    }
                },
                "ready_dispatcher": {"worker_os_duplicate_guard": False},
            }
            provider_report = {"providers": {"codex": {"local_cli_worker_supported": True, "supports_auto_approve": True}}}

            reason = supervisor.agent_auto_dispatch_block_reason(config, {}, "codex", provider_report)

        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("unsupported service_tier", reason)
        self.assertIn("priority", reason)

    def test_block_reason_uses_hyphenated_provider_key_for_codex_slot_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            (codex_home / "config.toml").write_text('service_tier = "fast"\n', encoding="utf-8")
            config = {
                "agents": {
                    "codex1_1": {
                        "id": "codex1_1",
                        "provider": "codex1-1",
                        "display_name": "Codex",
                        "dispatch_slot_for": "codex",
                    }
                },
                "providers": {
                    "codex1-1": {
                        "delivery_mode": "codex",
                        "codex": {"codex_home": str(codex_home)},
                    }
                },
                "ready_dispatcher": {"worker_os_duplicate_guard": False},
            }
            provider_report = {"providers": {"codex1-1": {"local_cli_worker_supported": True, "supports_auto_approve": True}}}

            reason = supervisor.agent_auto_dispatch_block_reason(config, {}, "codex1_1", provider_report)

        self.assertIsNone(reason)

    def test_block_reason_ignores_other_agents_processes(self) -> None:
        config = {
            "agents": {"codex": {"provider": "codex"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with (
            mock.patch.object(supervisor, "display_name_for", return_value="Codex"),
            mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False),
            mock.patch.object(
                supervisor, "scan_live_worker_pids_by_agent",
                return_value={"Claude": [42], "Codex2": [99]},
            ),
        ):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, {}, "codex", provider_report
            )
        self.assertIsNone(reason)

    def test_block_reason_allows_slotted_logical_agent_with_free_slot(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "provider": "codex",
                    "display_name": "Codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "provider": "codex1-1",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "provider": "codex1-2",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "agent_id": "codex1_1",
                    "status": "running",
                    "pid": 42,
                }
            }
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with mock.patch.object(
            supervisor,
            "scan_live_worker_pids_by_agent",
            return_value={"Codex": [42]},
        ) as scan:
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex", provider_report
            )
        self.assertIsNone(reason)
        scan.assert_not_called()

    def test_block_reason_blocks_exact_slot_with_active_worker(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "provider": "codex",
                    "display_name": "Codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "provider": "codex1-1",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "provider": "codex1-2",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "agent_id": "codex1_1",
                    "status": "running",
                    "pid": 42,
                }
            }
        }
        provider_report = {"providers": {"codex1-1": {"auth_ready": True}, "codex1-2": {"auth_ready": True}}}
        with mock.patch.object(supervisor, "scan_live_worker_pids_by_agent") as scan:
            blocked = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex1_1", provider_report
            )
            available = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex1_2", provider_report
            )
        self.assertIsNotNone(blocked)
        assert blocked is not None
        self.assertIn("codex1_1", blocked)
        self.assertIn("42", blocked)
        self.assertIsNone(available)
        scan.assert_not_called()

    def test_block_reason_blocks_slotted_logical_agent_when_all_slots_busy(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "provider": "codex",
                    "display_name": "Codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "provider": "codex1-1",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "provider": "codex1-2",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state = {
            "workers": {
                "run-1": {"run_id": "run-1", "agent_id": "codex1_1", "status": "running", "pid": 42},
                "run-2": {"run_id": "run-2", "agent_id": "codex1_2", "status": "running", "pid": 99},
            }
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with mock.patch.object(supervisor, "scan_live_worker_pids_by_agent") as scan:
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex", provider_report
            )
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("all dispatch slots", reason)
        self.assertIn("codex1_1", reason)
        self.assertIn("codex1_2", reason)
        scan.assert_not_called()


class RuntimeLeaseReconciliationTests(unittest.TestCase):
    def _config(self, root: Path) -> dict:
        return {
            "paths": {
                "status_file": str(root / "ai-status.json"),
                "activity_log": str(root / "activity-log.jsonl"),
                "event_queue": str(root / "event-queue.jsonl"),
            },
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "providers": {"codex": {"delivery_mode": "codex", "quota_group": "codex1"}},
            "agents": {"codex": {"id": "codex", "display_name": "Codex", "provider": "codex"}},
        }

    def test_reconcile_runtime_requeues_started_event_without_active_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-001",
                                "status": "in_progress",
                                "owner": "Codex",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps(
                    {
                        "event_id": "evt-lease",
                        "task_id": "OPS-LEASE-001",
                        "target_agent": "codex",
                        "target_display_name": "Codex",
                        "reason": "owned_in_progress_dispatch",
                        "message": "wake",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {
                    "events": {
                        "evt-lease": {
                            "status": "started",
                            "run_id": "codex-run-missing",
                            "lease_owner": "codex-run-missing",
                        }
                    }
                },
                "workers": {},
            }

            changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            record = state["queue"]["events"]["evt-lease"]
            self.assertEqual(record["status"], "queued")
            self.assertEqual(
                record["requeue_reason"],
                "started queue record had no active worker during supervisor boot reconciliation",
            )
            self.assertNotIn("lease_owner", record)
            metrics = state["worker_runtime_metrics"]
            self.assertEqual(metrics["totals"]["started_queue_records_requeued"], 1)
            self.assertEqual(
                metrics["last_measurements"]["boot_reconciliation"]["counts"]["started_queue_records_requeued"],
                1,
            )

    def test_reconcile_runtime_fails_running_worker_when_pid_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-002",
                                "status": "in_progress",
                                "owner": "Codex",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-worker", "task_id": "OPS-LEASE-002", "target_agent": "codex"})
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {"events": {"evt-worker": {"status": "started", "run_id": "codex-run-dead"}}},
                "workers": {
                    "codex-run-dead": {
                        "run_id": "codex-run-dead",
                        "status": "running",
                        "provider": "codex",
                        "agent_id": "codex",
                        "task_id": "OPS-LEASE-002",
                        "queue_event_id": "evt-worker",
                        "pid": 987654,
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["codex-run-dead"]
            self.assertEqual(worker["status"], "failed")
            self.assertEqual(state["queue"]["events"]["evt-worker"]["status"], "failed")
            self.assertIn("process missing", worker["last_error"])
            activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
            self.assertEqual(activity_types, ["worker_failed", "worker_runtime_metrics"])
            metrics = state["worker_runtime_metrics"]
            self.assertEqual(metrics["totals"]["missing_process_workers_failed"], 1)
            self.assertEqual(
                metrics["last_measurements"]["boot_reconciliation"]["counts"]["missing_process_workers_failed"],
                1,
            )

    def test_reconcile_runtime_does_not_scan_successful_missing_worker_log_for_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-003",
                                "status": "review",
                                "owner": "Claude",
                                "reviewer": "Codex",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-worker", "task_id": "OPS-LEASE-003", "target_agent": "codex"})
                + "\n",
                encoding="utf-8",
            )
            log_path = root / "codex-review.log"
            log_path.write_text(
                "\n".join(
                    [
                        "**Blocker**",
                        '+ completed.stderr = b"Error: not authenticated, please login first"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            status_path = root / "runner-status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "exit_code": 0,
                        "finished_at": "2026-06-01T13:07:54Z",
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "queue": {"events": {"evt-worker": {"status": "started", "run_id": "codex-run-done"}}},
                "provider_guardrails": {"dispatch_pauses": {}},
                "workers": {
                    "codex-run-done": {
                        "run_id": "codex-run-done",
                        "status": "running",
                        "provider": "codex",
                        "agent_id": "codex",
                        "task_id": "OPS-LEASE-003",
                        "queue_event_id": "evt-worker",
                        "pid": 987654,
                        "log_path": str(log_path),
                        "runner_status_path": str(status_path),
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_failure_evidence") as write_failure_evidence,
                mock.patch.object(supervisor, "mark_provider_dispatch_paused") as mark_provider_dispatch_paused,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["codex-run-done"]
            self.assertEqual(worker["status"], "completed")
            self.assertNotIn("last_error", worker)
            self.assertEqual(worker["runner_status"], "completed")
            self.assertEqual(worker["exit_code"], 0)
            self.assertEqual(state["queue"]["events"]["evt-worker"]["status"], "completed")
            self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
            write_failure_evidence.assert_not_called()
            mark_provider_dispatch_paused.assert_not_called()

    def test_reconcile_runtime_uses_log_failure_for_missing_process_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            config["providers"]["gemini"] = {"delivery_mode": "gemini"}
            config["agents"]["gemini"] = {"id": "gemini", "display_name": "Gemini", "provider": "gemini"}
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-003",
                                "status": "in_progress",
                                "owner": "Gemini",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-gemini", "task_id": "OPS-LEASE-003", "target_agent": "gemini"})
                + "\n",
                encoding="utf-8",
            )
            log_path = root / "gemini-quota.log"
            log_path.write_text(
                "\n".join(
                    [
                        "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
                        "reason: 'QUOTA_EXHAUSTED'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {"events": {"evt-gemini": {"status": "started", "run_id": "gemini-run-dead"}}},
                "provider_guardrails": {"dispatch_pauses": {}},
                "workers": {
                    "gemini-run-dead": {
                        "run_id": "gemini-run-dead",
                        "status": "running",
                        "provider": "gemini",
                        "agent_id": "gemini",
                        "task_id": "OPS-LEASE-003",
                        "queue_event_id": "evt-gemini",
                        "pid": 987654,
                        "log_path": str(log_path),
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_failure_evidence", return_value="evidence/gemini.json"),
                mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value="Codex"),
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["gemini-run-dead"]
            self.assertEqual(worker["status"], "reassigned")
            self.assertEqual(worker["reassigned_to"], "Codex")
            self.assertEqual(state["queue"]["events"]["evt-gemini"]["status"], "completed")
            pause = state["provider_guardrails"]["dispatch_pauses"]["gemini"]
            self.assertEqual(pause["pause_kind"], "quota_terminal")
            self.assertEqual(pause["worker_run_id"], "gemini-run-dead")
            streak = state["provider_guardrails"]["task_failure_streaks"]["OPS-LEASE-003:gemini"]
            self.assertEqual(streak["last_failure_kind"], "quota_terminal")
            self.assertIn("capacity", worker["last_error"].lower())

    def test_quota_group_cap_blocks_second_slot(self) -> None:
        config = {
            "ready_dispatcher": {"max_concurrent_per_quota_group": {"codex1": 1}},
            "agents": {
                "codex1_1": {"id": "codex1_1", "display_name": "Codex", "provider": "codex1-1"},
                "codex1_2": {"id": "codex1_2", "display_name": "Codex", "provider": "codex1-2"},
            },
            "providers": {
                "codex1-1": {"quota_group": "codex1"},
                "codex1-2": {"quota_group": "codex1"},
            },
        }
        state = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "status": "running",
                    "agent_id": "codex1_1",
                    "provider": "codex1-1",
                    "quota_group": "codex1",
                }
            }
        }

        reason = supervisor.agent_auto_dispatch_block_reason(config, state, "codex1_2", provider_report={})

        self.assertIsNotNone(reason)
        self.assertIn("quota group codex1", reason or "")


class MaxConcurrentWorkersCapTests(unittest.TestCase):
    def _base_config(self) -> dict:
        return {
            "ready_dispatcher": {
                "max_concurrent_workers": 2,
                "max_dispatches_per_tick": 4,
                "enabled": True,
            },
            "schema": {},
            "agents": {},
        }

    def test_dispatch_ready_tasks_skips_when_global_cap_reached(self) -> None:
        config = self._base_config()
        state: dict = {}
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(
                supervisor,
                "scan_live_worker_pids_by_agent",
                return_value={"Codex": [1, 2], "Claude": [3]},
            ) as scan,
            mock.patch.object(supervisor, "weighted_dispatch_agent_ids", return_value=["codex"]),
            mock.patch.object(supervisor, "active_worker_indexes", return_value=(set(), set())),
            mock.patch.object(
                supervisor, "outstanding_delivery_indexes", return_value=(set(), set(), set())
            ),
            mock.patch.object(supervisor, "agent_dispatch_loads", return_value={}),
            mock.patch.object(supervisor, "failure_loop_agents_for_task_map", return_value=set()),
            mock.patch.object(supervisor, "chair_rotation_state", return_value={}),
            mock.patch.object(supervisor, "helper_claim_settings", return_value={}),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(supervisor, "start_worker_for_request") as start,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)
        scan.assert_called()
        start.assert_not_called()
        self.assertFalse(changed)

    def test_dispatch_ready_tasks_proceeds_when_under_cap(self) -> None:
        config = self._base_config()
        state: dict = {}
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(
                supervisor,
                "scan_live_worker_pids_by_agent",
                return_value={"Codex": [1]},
            ),
            mock.patch.object(supervisor, "weighted_dispatch_agent_ids", return_value=["codex"]),
            mock.patch.object(supervisor, "active_worker_indexes", return_value=(set(), set())),
            mock.patch.object(
                supervisor, "outstanding_delivery_indexes", return_value=(set(), set(), set())
            ),
            mock.patch.object(supervisor, "agent_dispatch_loads", return_value={}),
            mock.patch.object(supervisor, "failure_loop_agents_for_task_map", return_value=set()),
            mock.patch.object(supervisor, "chair_rotation_state", return_value={}),
            mock.patch.object(supervisor, "helper_claim_settings", return_value={}),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value="blocked"),
            mock.patch.object(supervisor, "start_worker_for_request") as start,
        ):
            supervisor.dispatch_ready_tasks(config, state)
        start.assert_not_called()


class PruneOrphanWorktreesTests(unittest.TestCase):
    def _stub_subprocess_run(self, results):
        def fake_run(cmd, *args, **kwargs):
            cmd_tuple = tuple(str(c) for c in cmd)
            for key, value in results.items():
                if cmd_tuple[: len(key)] == key:
                    return value
            raise AssertionError(f"unexpected subprocess.run call: {cmd_tuple}")
        return fake_run

    def test_returns_false_when_disabled(self) -> None:
        config = {"worker_worktree_housekeeping": {"enabled": False}}
        state: dict = {}
        self.assertFalse(supervisor.prune_orphan_worktrees(config, state))

    def test_throttled_within_interval(self) -> None:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        recent_ts = (_dt.now(_tz.utc) - _td(seconds=30)).isoformat().replace("+00:00", "Z")
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 600}}
        state = {"worker_worktree_housekeeping": {"last_run_at": recent_ts}}
        with mock.patch.object(supervisor, "worker_worktree_settings") as ws:
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)
        ws.assert_not_called()

    def test_skips_when_no_merged_branches(self) -> None:
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True, "root": "/tmp/wt"}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=Path("/tmp/wt")),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", return_value=False),
            mock.patch.object(Path, "exists", return_value=True),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)

    def test_removes_clean_merged_orphan(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [
            {"worktree": record_path, "branch": "refs/heads/task/X"},
            {"worktree": "/repo", "branch": "refs/heads/main"},
        ]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="  task/X\n", stderr="")
        clean_status = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        remove_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
            ("git", "-C", record_path, "status", "--porcelain"): clean_status,
            ("git", "-C", "/repo", "worktree", "remove", record_path): remove_ok,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertTrue(result)

    def test_skips_dirty_worktree(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
        dirty_status = subprocess.CompletedProcess(args=[], returncode=0, stdout=" M foo.py\n", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
            ("git", "-C", record_path, "status", "--porcelain"): dirty_status,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)

    def test_skips_worktree_claimed_by_active_worker(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state = {"workers": {"r-1": {"workspace_path": record_path}}}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)


class ResolvePollIntervalTests(unittest.TestCase):
    def test_default_uses_config_value(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        value, source = supervisor.resolve_poll_interval(
            config, cli_value=None, allow_fast_poll=False
        )
        self.assertEqual(value, 300.0)
        self.assertEqual(source, "config")

    def test_cli_value_at_or_above_config_does_not_require_authorization(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        value, source = supervisor.resolve_poll_interval(
            config, cli_value=600.0, allow_fast_poll=False
        )
        self.assertEqual(value, 600.0)
        self.assertEqual(source, "cli")

    def test_cli_value_below_config_requires_allow_fast_poll(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        with self.assertRaises(SystemExit) as ctx:
            supervisor.resolve_poll_interval(
                config, cli_value=60.0, allow_fast_poll=False
            )
        self.assertIn("--allow-fast-poll", str(ctx.exception))

    def test_cli_value_below_config_allowed_when_authorized(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        value, source = supervisor.resolve_poll_interval(
            config, cli_value=60.0, allow_fast_poll=True
        )
        self.assertEqual(value, 60.0)
        self.assertEqual(source, "cli")

    def test_zero_or_negative_cli_value_rejected(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        with self.assertRaises(SystemExit):
            supervisor.resolve_poll_interval(
                config, cli_value=0.0, allow_fast_poll=True
            )
        with self.assertRaises(SystemExit):
            supervisor.resolve_poll_interval(
                config, cli_value=-5.0, allow_fast_poll=True
            )

    def test_missing_config_falls_back_to_default(self) -> None:
        value, source = supervisor.resolve_poll_interval(
            {}, cli_value=None, allow_fast_poll=False
        )
        self.assertEqual(value, supervisor.CONFIG_DEFAULT_POLL_INTERVAL_SECONDS)
        self.assertEqual(source, "config")


class RunSupervisorShellGuardTests(unittest.TestCase):
    def _script(self) -> Path:
        return Path(supervisor.__file__).resolve().parent.parent / "scripts" / "run-supervisor.sh"

    def _run(self, args: list[str], stub_body: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "python3"
            stub.write_text(stub_body)
            stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{tmp}:{env.get('PATH', '')}"
            return subprocess.run(
                ["bash", str(self._script()), *args],
                env=env,
                capture_output=True,
                text=True,
            )

    def test_poll_interval_without_allow_fast_poll_is_rejected(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(["--poll-interval", "60"], "#!/bin/sh\necho 'should not run' >&2\nexit 99\n")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("--allow-fast-poll", proc.stderr)

    def test_poll_interval_equals_form_also_rejected(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(["--poll-interval=60"], "#!/bin/sh\necho 'should not run' >&2\nexit 99\n")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("--allow-fast-poll", proc.stderr)

    def test_poll_interval_with_allow_fast_poll_passes_through(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(
            ["--poll-interval", "60", "--allow-fast-poll"], '#!/bin/sh\nexit 7\n'
        )
        self.assertEqual(proc.returncode, 7, proc.stderr)

    def test_no_poll_interval_passes_through(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(["--verbose"], '#!/bin/sh\nexit 11\n')
        self.assertEqual(proc.returncode, 11, proc.stderr)



if __name__ == "__main__":
    unittest.main()
