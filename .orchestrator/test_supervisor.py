#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import inspect
import multiprocessing
import tempfile
import unittest
import os
import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import supervisor
import provider_permissions
import runtime_state
import common


_OLD_ENV = {}


def setUpModule() -> None:
    global _OLD_ENV
    _OLD_ENV = dict(os.environ)
    for k in list(os.environ.keys()):
        if k.startswith("PANTHEON_"):
            del os.environ[k]


def tearDownModule() -> None:
    os.environ.clear()
    os.environ.update(_OLD_ENV)


def _run_supervisor_writer_transaction_until_released(
    config: dict[str, object],
    connection: object,
) -> None:
    """Exercise real nested runtime writers while run_once owns the outer lock."""

    def transaction(_config: dict[str, object], **_kwargs: object) -> bool:
        state = supervisor.load_runtime_state(_config)
        state.setdefault("workers", {})["before-release"] = {
            "run_id": "before-release",
            "task_id": "LOCK-TASK",
            "status": "running",
        }
        supervisor.save_runtime_state(_config, state)
        connection.send(("mid-transaction", os.getpid()))
        if connection.recv() != "release":
            raise RuntimeError("unexpected supervisor transaction command")
        supervisor.enqueue_event(
            _config,
            {
                "event_id": "evt-after-release",
                "task_id": "QUEUE-TASK",
                "status": "queued",
            },
        )
        state = supervisor.load_runtime_state(_config)
        state.setdefault("workers", {})["after-release"] = {
            "run_id": "after-release",
            "task_id": "QUEUE-TASK",
            "status": "running",
            "queue_event_id": "evt-after-release",
        }
        supervisor.save_runtime_state(_config, state)
        return True

    try:
        with mock.patch.object(supervisor, "_run_once_locked", side_effect=transaction):
            changed = supervisor.run_once(config, watch=False)
        connection.send(("completed", changed))
    except BaseException as exc:  # pragma: no cover - reported to the parent
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _interrupt_queue_replace_before_switch(
    config: dict[str, object],
    replacement: list[dict[str, object]],
    connection: object,
) -> None:
    """Pause after a replacement is durable but before its atomic switch."""

    real_replace = runtime_state.os.replace

    def pause_before_replace(source: object, destination: object) -> None:
        connection.send(("replace-ready", str(source), str(destination)))
        command = connection.recv()
        if command != "replace":
            raise RuntimeError("unexpected queue replacement command")
        real_replace(source, destination)

    try:
        with mock.patch.object(runtime_state.os, "replace", side_effect=pause_before_replace):
            runtime_state.replace_event_queue(config, replacement)
        connection.send(("completed", os.getpid()))
    except BaseException as exc:  # pragma: no cover - SIGKILL is the expected exit
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _prune_queue_after_runtime_lock(
    config: dict[str, object],
    state: dict[str, object],
    connection: object,
) -> None:
    """Wait on the real runtime lock, then run the supervisor prune writer."""

    try:
        with supervisor.runtime_state_lock(config, shared=False, nonblocking=False):
            changed = supervisor.prune_event_queue(config, state)
        connection.send(("completed", changed, state))
    except BaseException as exc:  # pragma: no cover - reported to the parent
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


class RuntimeConfigTests(unittest.TestCase):
    def test_load_provider_report_can_skip_refresh_for_one_shot_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            provider_report_path = root / "provider_capabilities.json"
            provider_report_path.write_text(json.dumps({"providers": {"copilot": {"auth_ready": True}}}), encoding="utf-8")
            config = {
                "supervisor": {"auto_refresh_provider_capabilities": True},
                "paths": {"provider_capabilities": str(provider_report_path)},
            }

            with mock.patch.object(supervisor, "build_provider_capabilities") as build_provider_capabilities:
                report = supervisor.load_provider_report(config, refresh=False)

        self.assertEqual(report["providers"]["copilot"]["auth_ready"], True)
        build_provider_capabilities.assert_not_called()

    def test_codex_accounts_allow_four_concurrent_slots(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))

        ready_dispatcher = config["ready_dispatcher"]
        account_caps = config["ready_dispatcher"]["max_concurrent_per_account"]

        self.assertNotIn("max_tasks_per_agent", ready_dispatcher)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Codex"], 4)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Codex2"], 4)
        self.assertEqual(account_caps["codex1"], 4)
        self.assertEqual(account_caps["codex2"], 4)
        self.assertEqual(config["providers"]["codex"]["account"], "codex1")
        self.assertEqual(config["providers"]["codex2"]["account"], "codex2")
        self.assertGreaterEqual(len(config["agents"]["codex"]["worker_slots"]), 4)
        self.assertGreaterEqual(len(config["agents"]["codex2"]["worker_slots"]), 4)
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "codex"), 4)
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "codex2"), 4)

    def test_claude_lanes_are_enabled_with_shared_account_limit(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))

        ready_dispatcher = config["ready_dispatcher"]

        self.assertEqual(ready_dispatcher["disabled_agents"], ["Antigravity2", "Copilot"])
        self.assertEqual(ready_dispatcher["target_workload"]["Claude"], 5)
        self.assertEqual(ready_dispatcher["target_workload"]["Claude2"], 5)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Claude"], 1)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Claude2"], 1)
        self.assertEqual(ready_dispatcher["max_concurrent_per_account"]["claude_account_shared_max_1"], 1)
        self.assertNotIn("Claude", ready_dispatcher["disabled_agents"])
        self.assertNotIn("Claude2", ready_dispatcher["disabled_agents"])
        self.assertEqual(ready_dispatcher["target_workload"]["Copilot"], 0)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Copilot"], 0)
        self.assertEqual(ready_dispatcher["max_concurrent_per_account"]["copilot"], 0)

    def test_claude2_capacity_still_obeys_shared_account_limit(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))

        ready_dispatcher = config["ready_dispatcher"]

        self.assertEqual(ready_dispatcher["target_workload"]["Claude2"], 5)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Claude2"], 1)
        self.assertEqual(ready_dispatcher["max_concurrent_per_account"]["claude_account_shared_max_1"], 1)
        self.assertEqual(config["providers"]["claude"]["account"], "claude_account_shared_max_1")
        self.assertEqual(config["providers"]["claude2"]["account"], "claude_account_shared_max_1")
        self.assertNotIn("Claude2", ready_dispatcher["disabled_agents"])

    def test_primary_antigravity_lane_is_enabled_and_alternate_stays_disabled(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))

        ready_dispatcher = config["ready_dispatcher"]

        self.assertIn("antigravity", config["agents"])
        self.assertIn("antigravity2", config["agents"])
        self.assertEqual(config["providers"]["antigravity"]["delivery_mode"], "antigravity")
        self.assertEqual(config["providers"]["antigravity2"]["delivery_mode"], "antigravity")
        self.assertEqual(ready_dispatcher["target_workload"]["Antigravity"], 5)
        self.assertEqual(ready_dispatcher["target_workload"]["Antigravity2"], 0)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Antigravity"], 1)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Antigravity2"], 0)
        self.assertEqual(ready_dispatcher["max_concurrent_per_account"]["antigravity"], 1)
        self.assertEqual(ready_dispatcher["max_concurrent_per_account"]["antigravity2"], 0)
        self.assertNotIn("Antigravity", ready_dispatcher["disabled_agents"])
        self.assertIn("Antigravity2", ready_dispatcher["disabled_agents"])

    def test_live_provider_account_schema_is_strict_and_complete(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))

        supervisor.validate_provider_accounts(config)

        self.assertTrue(config["ready_dispatcher"]["require_explicit_provider_accounts"])
        self.assertFalse(config["ready_dispatcher"]["allow_legacy_provider_account_aliases"])
        for provider, provider_cfg in config["providers"].items():
            self.assertTrue(provider_cfg.get("account"), provider)
            self.assertFalse(
                any(provider_cfg.get(key) for key in ("account_group", "quota_group", "dispatch_group")),
                provider,
            )

    def test_strict_provider_account_schema_rejects_missing_and_legacy_keys(self) -> None:
        config = {
            "ready_dispatcher": {
                "require_explicit_provider_accounts": True,
                "allow_legacy_provider_account_aliases": False,
            },
            "providers": {
                "missing": {"delivery_mode": "codex"},
                "legacy": {"account": "legacy", "quota_group": "legacy"},
            },
        }

        with self.assertRaisesRegex(ValueError, "providers.missing.account is required"):
            supervisor.validate_provider_accounts(config)

        legacy_cap_config = {
            "ready_dispatcher": {
                "require_explicit_provider_accounts": True,
                "allow_legacy_provider_account_aliases": False,
                "max_concurrent_per_quota_group": {"legacy": 1},
            },
            "providers": {"legacy": {"account": "legacy"}},
        }
        with self.assertRaisesRegex(ValueError, "max_concurrent_per_quota_group is deprecated"):
            supervisor.validate_provider_accounts(legacy_cap_config)


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

    def test_detects_copilot_monthly_quota_failure(self) -> None:
        line = '402 {"error":{"message":"You have exceeded your monthly quota","code":"quota_exceeded"}}'
        worker = self._worker_for_log(line + "\n")

        self.assertEqual(supervisor.detect_worker_failure(worker), line)

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

    def test_classifies_antigravity_individual_quota_as_terminal_quota(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "antigravity"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 34m24s.",
        )

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_claude_weekly_rate_limit_as_terminal_quota(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "rate_limit: You've hit your weekly limit · resets Jun 8, 12pm (UTC)",
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

    def test_detects_claude_weekly_rate_limit_line_as_worker_failure(self) -> None:
        worker = self._worker_for_log("rate_limit: You've hit your weekly limit · resets Jun 8, 12pm (UTC)\n")

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "rate_limit: You've hit your weekly limit · resets Jun 8, 12pm (UTC)",
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

    def test_classifies_require_authenticated_gh_session_as_tool_auth(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "codex2-1"}

        for reason in (
            "Require authenticated gh session. Run gh auth status.",
            "Require authenticated `gh` session. Run `gh auth status`.",
        ):
            with self.subTest(reason=reason):
                result = supervisor.classify_worker_failure(config, worker, reason)

                self.assertEqual(result["kind"], "tool_auth")
                self.assertFalse(result["transient"])
                self.assertFalse(supervisor.should_pause_dispatch_for_failure_kind(result["kind"]))

    def test_auth_failures_pause_provider_dispatch(self) -> None:
        self.assertTrue(supervisor.should_pause_dispatch_for_failure_kind("auth"))

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

    def test_parse_quota_retry_hint_claude_month_day_utc(self) -> None:
        from datetime import datetime, timezone

        now = datetime(2026, 6, 7, 14, 11, 0, tzinfo=timezone.utc)
        hint = supervisor.parse_quota_retry_hint(
            "rate_limit: You've hit your weekly limit · resets Jun 8, 12pm (UTC)",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc))

    def test_parse_quota_retry_hint_codex_full_date(self) -> None:
        from datetime import datetime, timezone

        now = datetime(2026, 5, 16, 10, 5, 36, tzinfo=timezone.utc)
        hint = supervisor.parse_quota_retry_hint(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
            "to purchase more credits or try again at May 19th, 2026 12:40 AM.",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 5, 18, 16, 40, 0, tzinfo=timezone.utc))

    def test_parse_quota_retry_hint_antigravity_relative_duration(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
        hint = supervisor.parse_quota_retry_hint(
            "Error: You have exhausted your capacity on this model. "
            "Your quota will reset after 89h52m2s.",
            now=now,
        )

        self.assertEqual(hint, now + timedelta(hours=89, minutes=52, seconds=2))

    def test_parse_quota_retry_hint_relative_minutes_only(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
        hint = supervisor.parse_quota_retry_hint(
            "quota will reset in 45m", now=now
        )

        self.assertEqual(hint, now + timedelta(minutes=45))

    def test_parse_quota_retry_hint_returns_none_when_absent(self) -> None:
        self.assertIsNone(supervisor.parse_quota_retry_hint("Credit balance is too low"))
        self.assertIsNone(supervisor.parse_quota_retry_hint(None))

    def test_pause_dispatch_for_reaped_worker_quota_log_pauses_for_hint(self) -> None:
        from datetime import datetime, timezone

        worker = self._worker_for_log(
            "Error: You have exhausted your capacity on this model. "
            "Your quota will reset after 89h52m2s.\n"
        )
        worker.update(
            {"provider": "antigravity", "run_id": "antigravity-run-1", "task_id": "TJ-E2E-005"}
        )
        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state: dict = {}
        fake_now = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "write_failure_evidence", return_value="ref-1"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            reason = supervisor.pause_dispatch_for_reaped_worker(config, state, worker)

        self.assertIsNotNone(reason)
        self.assertIn("exhausted your capacity", reason)
        entry = state["provider_guardrails"]["dispatch_pauses"]["antigravity"]
        self.assertEqual(entry["pause_kind"], "quota_terminal")
        # 89h52m2s hint => pause window far beyond the 900s default
        self.assertGreater(entry["reset_after_seconds"], 300000)

    def test_pause_dispatch_for_reaped_worker_generic_log_no_pause(self) -> None:
        worker = self._worker_for_log("normal progress output, nothing wrong\n")
        worker.update({"provider": "antigravity", "run_id": "antigravity-run-2"})
        config = {
            "provider_guardrails": {},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state: dict = {}
        with mock.patch.object(supervisor, "write_activity_log"):
            reason = supervisor.pause_dispatch_for_reaped_worker(config, state, worker)

        self.assertIsNone(reason)
        self.assertEqual(
            state.get("provider_guardrails", {}).get("dispatch_pauses", {}), {}
        )

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

    def test_claude_account_group_pause_crosses_profiles_with_same_auth_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            provider_report_path = Path(tmpdir) / "provider_capabilities.json"
            provider_report_path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "claude": {"account_group": "claude_account_shared", "auth_ready": True},
                            "claude2": {"account_group": "claude_account_shared", "auth_ready": True},
                        }
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
                "paths": {
                    "activity_log": "/tmp/test-activity-log.jsonl",
                    "provider_capabilities": str(provider_report_path),
                },
                "agents": {
                    "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                    "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
                },
                "providers": {
                    "claude": {"delivery_mode": "claude_cli", "quota_group": "claude"},
                    "claude2": {"delivery_mode": "claude_cli", "quota_group": "claude2"},
                    "claude-1": {"delivery_mode": "claude_cli", "quota_group": "claude"},
                    "claude2-1": {"delivery_mode": "claude_cli", "quota_group": "claude2"},
                },
            }
            state: dict = {}

            with mock.patch.object(supervisor, "write_activity_log"):
                supervisor.mark_provider_dispatch_paused(
                    config,
                    state,
                    "claude-1",
                    "rate_limit: You've hit your weekly limit",
                    failure_kind="quota_terminal",
                    pause_kind="quota_terminal",
                )

            pauses = state["provider_guardrails"]["dispatch_pauses"]
            self.assertIn("claude_account_shared", pauses)
            self.assertNotIn("claude", pauses)
            self.assertIs(
                supervisor.current_provider_dispatch_pause(state, "claude2-1", config),
                pauses["claude_account_shared"],
            )
            self.assertTrue(supervisor.agent_dispatch_paused(config, state, "claude2"))

    def test_claude_account_group_keeps_profiles_separate_when_auth_identity_differs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            provider_report_path = Path(tmpdir) / "provider_capabilities.json"
            provider_report_path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "claude": {"account_group": "claude_account_primary", "auth_ready": True},
                            "claude2": {"account_group": "claude_account_secondary", "auth_ready": True},
                        }
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
                "paths": {
                    "activity_log": "/tmp/test-activity-log.jsonl",
                    "provider_capabilities": str(provider_report_path),
                },
                "providers": {
                    "claude": {"delivery_mode": "claude_cli", "quota_group": "claude"},
                    "claude2": {"delivery_mode": "claude_cli", "quota_group": "claude2"},
                },
            }
            state: dict = {}

            with mock.patch.object(supervisor, "write_activity_log"):
                supervisor.mark_provider_dispatch_paused(
                    config,
                    state,
                    "claude",
                    "rate_limit: You've hit your weekly limit",
                    failure_kind="quota_terminal",
                    pause_kind="quota_terminal",
                )

            self.assertIsNotNone(supervisor.current_provider_dispatch_pause(state, "claude", config))
            self.assertIsNone(supervisor.current_provider_dispatch_pause(state, "claude2", config))

    def test_claude_account_group_still_honors_legacy_provider_pause_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            provider_report_path = Path(tmpdir) / "provider_capabilities.json"
            provider_report_path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "claude": {"account_group": "claude_account_shared", "auth_ready": True},
                            "claude2": {"account_group": "claude_account_shared", "auth_ready": True},
                        }
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "paths": {"provider_capabilities": str(provider_report_path)},
                "providers": {
                    "claude": {"delivery_mode": "claude_cli", "quota_group": "claude"},
                    "claude2": {"delivery_mode": "claude_cli", "quota_group": "claude2"},
                    "claude2-1": {"delivery_mode": "claude_cli", "quota_group": "claude2"},
                },
            }
            state = {
                "provider_guardrails": {
                    "dispatch_pauses": {
                        "claude": {
                            "provider": "claude",
                            "blocked_until": "9999-12-31T23:59:59Z",
                            "pause_kind": "quota_terminal",
                        }
                    }
                }
            }

            self.assertIs(
                supervisor.current_provider_dispatch_pause(state, "claude2-1", config),
                state["provider_guardrails"]["dispatch_pauses"]["claude"],
            )

    def test_slot_provider_inherits_configured_account_group_from_quota_parent(self) -> None:
        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "agents": {
                "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2-1"},
            },
            "providers": {
                "claude": {"delivery_mode": "claude_cli", "account_group": "claude_account_manual"},
                "claude2": {"delivery_mode": "claude_cli", "account_group": "claude_account_manual"},
                "claude-1": {"delivery_mode": "claude_cli", "quota_group": "claude"},
                "claude2-1": {"delivery_mode": "claude_cli", "quota_group": "claude2"},
            },
        }
        state: dict = {}

        with mock.patch.object(supervisor, "write_activity_log"):
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "claude-1",
                "rate_limit: You've hit your weekly limit",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        pauses = state["provider_guardrails"]["dispatch_pauses"]
        self.assertIn("claude_account_manual", pauses)
        self.assertTrue(supervisor.agent_dispatch_paused(config, state, "claude2"))

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

    def test_mark_revoked_auth_pause_is_sticky_until_probe(self) -> None:
        from datetime import datetime, timezone

        config = {
            "provider_guardrails": {"auth_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state: dict = {}
        fake_now = datetime(2026, 6, 14, 15, 0, 0, tzinfo=timezone.utc)

        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex2",
                "error refreshing token: refresh-token-revoked",
                failure_kind="auth",
                pause_kind="auth",
            )

        pause = state["provider_guardrails"]["dispatch_pauses"]["codex2"]
        self.assertTrue(pause["sticky_until_auth_probe"])
        self.assertEqual(pause["sticky_reason"], "refresh_token_revoked")
        self.assertEqual(pause["blocked_until"], "9999-12-31T23:59:59Z")

    def test_expire_provider_dispatch_pauses_keeps_revoked_auth_pause(self) -> None:
        config = {
            "provider_guardrails": {"auth_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex2": {
                        "provider": "codex2",
                        "blocked_until": "2026-04-06T12:00:00Z",
                        "pause_kind": "auth",
                        "reason": "error refreshing token: refresh-token-revoked",
                    }
                }
            }
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.expire_provider_dispatch_pauses(config, state)
            active = supervisor.current_provider_dispatch_pause(state, "codex2", config)

        self.assertFalse(changed)
        self.assertIn("codex2", state["provider_guardrails"]["dispatch_pauses"])
        self.assertIs(active, state["provider_guardrails"]["dispatch_pauses"]["codex2"])
        write_activity_log.assert_not_called()

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

    def test_auth_recovery_clears_only_auth_guardrails_for_provider_group(self) -> None:
        config = {
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "agents": {
                "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
                "codex2_1": {
                    "id": "codex2_1",
                    "display_name": "Codex2",
                    "provider": "codex2-1",
                    "dispatch_slot_for": "codex2",
                },
                "codex2_2": {
                    "id": "codex2_2",
                    "display_name": "Codex2",
                    "provider": "codex2-2",
                    "dispatch_slot_for": "codex2",
                },
                "gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"},
            },
            "providers": {
                "codex2": {"delivery_mode": "codex", "quota_group": "codex2"},
                "codex2-1": {"delivery_mode": "codex", "quota_group": "codex2"},
                "codex2-2": {"delivery_mode": "codex", "quota_group": "codex2"},
                "gemini": {"delivery_mode": "gemini", "quota_group": "gemini"},
            },
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex2": {"pause_kind": "auth", "task_id": "OPS-AUTH", "worker_run_id": "codex-run"},
                    "gemini": {"pause_kind": "capacity_retryable", "task_id": "OPS-CAP", "worker_run_id": "gemini-run"},
                },
                "task_failure_streaks": {
                    "OPS-AUTH:codex2_1": {
                        "task_id": "OPS-AUTH",
                        "provider": "codex2_1",
                        "last_failure_kind": "auth",
                        "last_reason": "token_invalidated",
                    },
                    "OPS-AUTH2:codex2": {
                        "task_id": "OPS-AUTH2",
                        "provider": "codex2",
                        "last_failure_kind": "",
                        "last_reason": "not authenticated",
                    },
                    "OPS-QUOTA:codex2_2": {
                        "task_id": "OPS-QUOTA",
                        "provider": "codex2_2",
                        "last_failure_kind": "quota_terminal",
                        "last_reason": "402 You have no quota",
                    },
                    "OPS-GEMINI:gemini": {
                        "task_id": "OPS-GEMINI",
                        "provider": "gemini",
                        "last_failure_kind": "auth",
                        "last_reason": "not authenticated",
                    },
                },
            }
        }
        previous = {"providers": {"codex2-1": {"auth_ready": False}}}
        current = {
            "providers": {
                "codex2-1": {
                    "auth_ready": True,
                    "last_auth_probe_at": "2026-06-06T12:00:00Z",
                    "auth_method": "codex_exec_oauth",
                }
            }
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.reconcile_provider_auth_recovery(config, state, previous, current)

        self.assertTrue(changed)
        self.assertNotIn("codex2", state["provider_guardrails"]["dispatch_pauses"])
        self.assertIn("gemini", state["provider_guardrails"]["dispatch_pauses"])
        streaks = state["provider_guardrails"]["task_failure_streaks"]
        self.assertNotIn("OPS-AUTH:codex2_1", streaks)
        self.assertNotIn("OPS-AUTH2:codex2", streaks)
        self.assertIn("OPS-QUOTA:codex2_2", streaks)
        self.assertIn("OPS-GEMINI:gemini", streaks)
        self.assertGreaterEqual(write_activity_log.call_count, 2)

    def test_sticky_revoked_auth_recovery_requires_live_probe_success(self) -> None:
        config = {
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex2": {"delivery_mode": "codex", "quota_group": "codex2"}},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex2": {
                        "pause_kind": "auth",
                        "reason": "error refreshing token: refresh-token-revoked",
                        "sticky_until_auth_probe": True,
                    }
                },
                "task_failure_streaks": {
                    "OPS-AUTH:codex2": {
                        "task_id": "OPS-AUTH",
                        "provider": "codex2",
                        "last_failure_kind": "auth",
                        "last_reason": "refresh-token-revoked",
                    }
                },
            }
        }
        previous = {"providers": {"codex2": {"auth_ready": False}}}
        cached_current = {
            "providers": {
                "codex2": {
                    "auth_ready": True,
                    "auth_probe": {"ready": True, "source": "cached", "method": "codex_exec_oauth"},
                }
            }
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.reconcile_provider_auth_recovery(config, state, previous, cached_current)

        self.assertFalse(changed)
        self.assertIn("codex2", state["provider_guardrails"]["dispatch_pauses"])
        self.assertIn("OPS-AUTH:codex2", state["provider_guardrails"]["task_failure_streaks"])
        write_activity_log.assert_not_called()

        live_current = {
            "providers": {
                "codex2": {
                    "auth_ready": True,
                    "auth_method": "codex_exec_oauth",
                    "last_auth_probe_at": "2026-06-14T15:10:00Z",
                    "auth_probe": {"ready": True, "source": "live", "method": "codex_exec_oauth"},
                }
            }
        }
        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.reconcile_provider_auth_recovery(config, state, previous, live_current)

        self.assertTrue(changed)
        self.assertNotIn("codex2", state["provider_guardrails"]["dispatch_pauses"])
        self.assertNotIn("OPS-AUTH:codex2", state["provider_guardrails"]["task_failure_streaks"])
        self.assertGreaterEqual(write_activity_log.call_count, 2)


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

    def test_prepare_github_retry_allocates_isolated_worktree_outside_allowlist(self) -> None:
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
                    "execution_reasons": ["owned_ready_dispatch"],
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-RETRY-001",
                reason="github_retry",
                metadata={
                    "workspace_task_id": "OPS-RETRY-001",
                    "require_isolated_worktree": True,
                },
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)) as create_worktree,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-github-retry",
                    target_agent="Codex",
                )

        expected_path = worktree_root / "pantheon" / "ops-retry-001"
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(request.metadata["workspace_mode"], "isolated_worktree")
        self.assertEqual(request.metadata["workspace_path"], str(expected_path))
        create_worktree.assert_called_once_with(
            repo_root.resolve(),
            expected_path,
            "task/OPS-RETRY-001",
            "origin/dev",
        )

    def test_prepare_github_retry_refuses_shared_checkout_when_worktrees_disabled(self) -> None:
        config = {
            **self.config,
            "worker_worktrees": {"enabled": False},
        }
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="OPS-RETRY-001",
            reason="github_retry",
            metadata={
                "workspace_task_id": "OPS-RETRY-001",
                "require_isolated_worktree": True,
            },
        )
        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            ok, message = supervisor.prepare_worker_workspace(
                config,
                {},
                request,
                queue_event_id="evt-github-retry",
                target_agent="Codex",
            )

        self.assertFalse(ok)
        self.assertIn("Refusing shared-checkout fallback", message or "")
        self.assertNotIn("workspace_path", request.metadata)
        self.assertEqual(
            write_activity_log.call_args.args[1]["refresh_status"],
            "isolated_worktrees_disabled",
        )

    def test_prepare_github_retry_rejects_shared_checkout_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "worker_worktrees": {"enabled": True},
            }
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-RETRY-001",
                reason="github_retry",
                metadata={
                    "workspace_task_id": "OPS-RETRY-001",
                    "require_isolated_worktree": True,
                    "workspace_path": str(repo_root),
                },
            )
            with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    {},
                    request,
                    queue_event_id="evt-github-retry",
                    target_agent="Codex",
                )

        self.assertFalse(ok)
        self.assertIn("shared supervisor checkout", message or "")
        self.assertEqual(
            write_activity_log.call_args.args[1]["refresh_status"],
            "shared_checkout_rejected",
        )

    def test_create_worker_worktree_quarantines_incomplete_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ag-ws-ops-002"
            worktree_path.mkdir(parents=True)
            (worktree_path / "partial-checkout.txt").write_text("preserve me\n", encoding="utf-8")

            completed = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="", stderr="")
            with (
                mock.patch.object(supervisor, "_git_ref_exists", return_value=False),
                mock.patch.object(supervisor.subprocess, "run", return_value=completed) as run,
            ):
                ok, error = supervisor._create_worker_worktree(
                    repo_root,
                    worktree_path,
                    "task/AG-WS-OPS-002",
                    "origin/dev",
                )

            self.assertTrue(ok)
            self.assertIsNone(error)
            quarantine_root = worktree_path.parent / ".incomplete-worktree-quarantine"
            quarantined = list(quarantine_root.glob("ag-ws-ops-002-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(
                (quarantined[0] / "partial-checkout.txt").read_text(encoding="utf-8"),
                "preserve me\n",
            )
            self.assertIn("original_path=", (quarantined[0] / "ORCHESTRATOR_QUARANTINE.txt").read_text(encoding="utf-8"))
            run.assert_called_once_with(
                [
                    "git",
                    "worktree",
                    "add",
                    "-b",
                    "task/AG-WS-OPS-002",
                    str(worktree_path),
                    "origin/dev",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_create_worker_worktree_does_not_move_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "active-task"
            worktree_path.mkdir(parents=True)
            (worktree_path / ".git").write_text("gitdir: /safe/metadata\n", encoding="utf-8")

            with mock.patch.object(supervisor.subprocess, "run") as run:
                ok, error = supervisor._create_worker_worktree(
                    repo_root,
                    worktree_path,
                    "task/ACTIVE-TASK",
                    "origin/dev",
                )

            self.assertFalse(ok)
            self.assertIn("already exists and is not empty", error or "")
            self.assertTrue((worktree_path / ".git").exists())
            run.assert_not_called()

    def test_generated_worker_task_brief_mentions_inherited_status_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            brief = supervisor._generated_worker_task_brief(
                {"paths": {"status_file": str(root / "ai-status.json")}},
                "OPS-WORKTREE-001",
            )

        self.assertIn("PANTHEON_STATUS_ROOT", brief)
        self.assertIn("PANTHEON_COMMAND_ROOT", brief)
        self.assertIn("$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh", brief)
        self.assertNotIn("./scripts/ai-status.sh", brief)

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
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-004",
            reason="owned_in_progress_dispatch",
            metadata={"workspace_path": "/tmp/workers/bus-val-004"},
        )
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
        sync_dispatched_task_status.assert_called_once_with(
            self.config,
            queue_payload,
            run_id="run-123",
            workspace_path="/tmp/workers/bus-val-004",
        )

    def test_process_queue_skips_second_event_when_task_worker_is_active(self) -> None:
        current_task = {
            "id": "BUS-VAL-DUP",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-07-26T10:20:30Z",
        }
        queue_payload = {
            "event_id": "evt-duplicate",
            "task_id": "BUS-VAL-DUP",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "github_retry",
            "message": "wake",
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-existing": {
                    "run_id": "run-existing",
                    "task_id": "BUS-VAL-DUP",
                    "agent_id": "codex",
                    "queue_event_id": "evt-original",
                    "status": "running",
                }
            },
        }

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                side_effect=AssertionError("duplicate task event must not start another worker"),
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-duplicate"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["skip_reason"], "task_already_active")
        self.assertEqual(record["active_run_id"], "run-existing")
        write_activity_log.assert_called_once()
        activity = write_activity_log.call_args.args[1]
        self.assertEqual(activity["type"], "wake_skipped")
        self.assertEqual(activity["worker_run_id"], "run-existing")

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

    def test_dispatcher_prioritizes_declared_p0_review(self) -> None:
        status = {
            "tasks": [
                {
                    "id": "OLDER-REVIEW",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "last_update": "2026-07-17T16:00:00Z",
                },
                {
                    "id": "INCIDENT-P0",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "priority": "P0",
                    "depends_on": [],
                    "last_update": "2026-07-17T17:00:00Z",
                },
            ]
        }
        state = {"queue": {"events": {}}, "workers": {}}
        config = json.loads(json.dumps(self.config))
        config["ready_dispatcher"]["max_dispatches_per_tick"] = 1

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "queue_delivery_event",
                return_value=True,
            ) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        self.assertEqual(
            queue_delivery_event.call_args.args[1]["task_id"],
            "INCIDENT-P0",
        )

    def test_task_declared_priority_rank_is_fail_safe(self) -> None:
        self.assertEqual(supervisor.task_declared_priority_rank({"priority": "P0"}), 0)
        self.assertEqual(supervisor.task_declared_priority_rank({"priority": "p12"}), 12)
        self.assertEqual(supervisor.task_declared_priority_rank({"priority": 3}), 3)
        self.assertGreater(
            supervisor.task_declared_priority_rank({"priority": "urgent"}),
            supervisor.task_declared_priority_rank({"priority": "P99"}),
        )

    def test_dispatcher_cools_down_only_an_unchanged_task_signature(self) -> None:
        previous_task = {
            "id": "NO-PROGRESS-REVIEW",
            "status": "review",
            "owner": "Claude",
            "reviewer": "Codex",
            "priority": "P0",
            "depends_on": [],
            "last_update": "2026-07-17T17:00:00Z",
        }
        previous_event = supervisor.build_dispatch_event(
            previous_task,
            "Codex",
            "review_ready_dispatch",
            {previous_task["id"]: previous_task},
        )
        state = {
            "queue": {"events": {}},
            "workers": {},
            "seen_event_keys": {previous_event["key"]: supervisor.utc_now()},
        }
        config = json.loads(json.dumps(self.config))
        config["ready_dispatcher"]["unchanged_task_cooldown_seconds"] = 900

        with (
            mock.patch.object(
                supervisor,
                "load_status",
                return_value={"tasks": [previous_task]},
            ),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "queue_delivery_event",
                return_value=True,
            ) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertFalse(changed)
        queue_delivery_event.assert_not_called()

        updated_task = dict(previous_task)
        updated_task["last_update"] = "2026-07-17T17:01:00Z"
        with (
            mock.patch.object(
                supervisor,
                "load_status",
                return_value={"tasks": [updated_task]},
            ),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "queue_delivery_event",
                return_value=True,
            ) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        self.assertNotEqual(
            queue_delivery_event.call_args.args[1]["key"],
            previous_event["key"],
        )

    def test_dispatcher_redispatches_interrupted_governed_review_exactly_once(self) -> None:
        task = {
            "id": "INTERRUPTED-REVIEW",
            "status": "review",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": [],
            "last_update": "2026-07-24T00:10:00Z",
            "next": "Review the task without mutating its handoff.",
        }
        handoff = {
            "task_id": task["id"],
            "from": "Claude",
            "to": "Codex",
            "message": task["next"],
            "status": "pending",
            "created_at": task["last_update"],
        }
        event = supervisor.build_dispatch_event(
            task,
            "Codex",
            "review_ready_dispatch",
            {task["id"]: task},
        )
        worker = {
            "run_id": "codex-review-interrupted",
            "task_id": task["id"],
            "provider": "codex",
            "agent_id": "codex",
            "status": "completed",
            "runner_finished_at": "2026-07-24T00:11:00Z",
            "last_event_at": "2026-07-24T00:11:00Z",
            "request_snapshot": {
                "agent_id": "codex",
                "provider": "codex",
                "delivery_mode": "codex",
                "message": "review",
                "task_id": task["id"],
                "reason": "review_ready_dispatch",
                "metadata": {"dispatch_event_key": event["key"]},
            },
        }
        state = {
            "queue": {"events": {}},
            "workers": {worker["run_id"]: worker},
            "seen_event_keys": {event["key"]: supervisor.utc_now()},
        }
        status = {"tasks": [task], "handoffs": [handoff]}
        unchanged_status = json.loads(json.dumps(status))
        config = json.loads(json.dumps(self.config))
        config["ready_dispatcher"]["unchanged_task_cooldown_seconds"] = 900

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "queue_delivery_event",
                return_value=True,
            ) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            self.assertTrue(supervisor.dispatch_ready_tasks(config, state))
            self.assertFalse(supervisor.dispatch_ready_tasks(config, state))

        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        redispatch = queued_event["task"]["governed_review_redispatch"]
        self.assertEqual(redispatch["attempt"], 1)
        self.assertEqual(redispatch["parent_worker_run_id"], worker["run_id"])
        self.assertTrue(redispatch["require_isolated_worktree"])
        self.assertEqual(worker["review_redispatch_event_key"], event["key"])
        self.assertEqual(status, unchanged_status)
        self.assertEqual(
            [call.args[1]["type"] for call in write_activity_log.call_args_list],
            ["review_worker_redispatched"],
        )

        request = supervisor.build_request(
            config,
            {
                "event_key": queued_event["key"],
                "target_agent": "codex",
                "message": "review",
                "task_id": task["id"],
                "reason": "review_ready_dispatch",
                "context_files": [],
                "metadata": {"task": queued_event["task"]},
            },
        )
        self.assertTrue(supervisor.worker_request_requires_isolated_worktree(request))
        self.assertEqual(
            request.metadata["governed_review_redispatch"]["parent_worker_run_id"],
            worker["run_id"],
        )

    def test_queue_completion_starts_unchanged_signature_cooldown(self) -> None:
        state = {
            "queue": {
                "events": {
                    "evt-complete": {
                        "status": "started",
                        "event_key": "dispatcher:Codex:TASK:review:same-signature",
                    }
                }
            },
            "workers": {},
        }
        worker = {
            "run_id": "run-complete",
            "queue_event_id": "evt-complete",
        }

        supervisor.finalize_queue_event_record(
            self.config,
            state,
            worker,
            "completed",
        )

        record = state["queue"]["events"]["evt-complete"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(
            state["seen_event_keys"][record["event_key"]],
            record["processed_at"],
        )

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
            def __init__(self, task_lookup, **_kwargs):
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

    def test_dispatcher_reads_archived_dependency_from_configured_status_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir) / "status-root"
            archive_dir = status_root / "ai-task-archive" / "tasks"
            archive_dir.mkdir(parents=True)
            (archive_dir / "REG-300.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "task_id": "REG-300",
                        "terminal_status": "done",
                        "terminal_outcome": "completed",
                        "archived_at": "2026-06-10T01:00:00Z",
                        "task": {
                            "id": "REG-300",
                            "status": "done",
                            "terminal_outcome": "completed",
                            "owner": "Claude",
                            "reviewer": "Codex",
                            "depends_on": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = json.loads(json.dumps(self.config))
            config["paths"] = {"status_file": str(status_root / "ai-status.json")}
            current_task = {
                "id": "FB-006",
                "status": "todo",
                "owner": "Codex",
                "reviewer": "Claude",
                "depends_on": ["REG-300"],
                "last_update": "2026-06-10T01:05:00Z",
            }
            state = {"queue": {"events": {}}, "workers": {}}
            status = {"tasks": [current_task]}

            with (
                mock.patch.object(supervisor, "load_status", return_value=status),
                mock.patch.object(supervisor, "load_event_queue", return_value=[]),
                mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            ):
                changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-006")
        self.assertIn("REG-300:done", queued_event["key"])

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
            def __init__(self, task_lookup, **_kwargs):
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
            def __init__(self, _task_lookup, **_kwargs):
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
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
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

    def test_dispatcher_does_not_helper_claim_catalog_locked_task(self) -> None:
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
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "L12-LOCKED-001",
                    "status": "todo",
                    "owner": "Copilot",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "catalog_task_contract_sha256": "c" * 64,
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(
                config,
                {"queue": {"events": {}}, "workers": {}},
            )

        self.assertTrue(changed)
        persist.assert_not_called()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "L12-LOCKED-001")
        self.assertEqual(queued_event["target_agent"], "Copilot")
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
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
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
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
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

    def test_agent_can_take_task_blocks_auth_down_provider(self) -> None:
        config = {
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
            },
        }
        report = {"providers": {"codex": {"auth_ready": True}, "codex2": {"auth_ready": False}}}
        task = {"id": "T1", "status": "todo", "owner": "Codex2"}
        with mock.patch.object(supervisor, "_cached_provider_capabilities", return_value=report):
            self.assertTrue(supervisor.agent_can_take_task(config, "Codex", task))
            self.assertFalse(supervisor.agent_can_take_task(config, "Codex2", task))

    def test_agent_can_take_task_allows_when_capabilities_unknown(self) -> None:
        # A missing/None capability must NOT block (avoid reassignment churn).
        config = {"agents": {"codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"}}}
        task = {"id": "T1", "status": "todo", "owner": "Codex2"}
        with mock.patch.object(supervisor, "_cached_provider_capabilities", return_value={"providers": {}}):
            self.assertTrue(supervisor.agent_can_take_task(config, "Codex2", task))

    def test_normalize_reassigns_todo_owned_by_auth_down_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "worker_reassignment": {
                "eligible_statuses": ["todo", "in_progress", "review", "review_approved"],
                "owner_fallbacks": {"Codex2": ["Codex"]},
                "reviewer_fallbacks": {"Codex2": ["Codex"]},
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        report = {
            "providers": {
                "codex": {"auth_ready": True},
                "codex2": {"auth_ready": False},
                "claude": {"auth_ready": True},
            }
        }
        task = {"id": "OPS-RTEL-003", "status": "todo", "owner": "Codex2", "reviewer": "Claude", "depends_on": []}
        with (
            mock.patch.object(supervisor, "_cached_provider_capabilities", return_value=report),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.normalize_mainline_task_assignment(config, task)

        self.assertTrue(changed)
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "OPS-RTEL-003")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")

    def test_normalize_does_not_reassign_catalog_locked_task(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "worker_reassignment": {
                "eligible_statuses": ["todo"],
                "owner_fallbacks": {"Codex2": ["Codex"]},
                "reviewer_fallbacks": {"Codex2": ["Codex"]},
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        report = {
            "providers": {
                "codex": {"auth_ready": True},
                "codex2": {"auth_ready": False},
                "claude": {"auth_ready": True},
            }
        }
        task = {
            "id": "L12-LOCKED-001",
            "status": "todo",
            "owner": "Codex2",
            "reviewer": "Claude",
            "depends_on": [],
            "catalog_task_contract_sha256": "a" * 64,
        }
        with (
            mock.patch.object(supervisor, "_cached_provider_capabilities", return_value=report),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.normalize_mainline_task_assignment(config, task)

        self.assertFalse(changed)
        persist.assert_not_called()

    def test_normalize_does_not_reassign_when_owner_auth_ready(self) -> None:
        config = {
            "schema": {"tasks_path": "tasks", "task_id_field": "id", "assignee_field": "owner", "reviewer_field": "reviewer"},
            "worker_reassignment": {"eligible_statuses": ["todo"], "owner_fallbacks": {"Codex": ["Claude"]}},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        report = {"providers": {"codex": {"auth_ready": True}, "claude": {"auth_ready": True}}}
        task = {"id": "OK-1", "status": "todo", "owner": "Codex", "reviewer": "Claude", "depends_on": []}
        with (
            mock.patch.object(supervisor, "_cached_provider_capabilities", return_value=report),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.normalize_mainline_task_assignment(config, task)
        self.assertFalse(changed)
        persist.assert_not_called()

    def test_agent_is_known_rejects_phantom_owner(self) -> None:
        config = {"agents": {"codex": {"id": "codex", "display_name": "Codex", "provider": "codex"}}}
        self.assertTrue(supervisor.agent_is_known(config, "Codex"))
        self.assertTrue(supervisor.agent_is_known(config, "codex"))
        self.assertFalse(supervisor.agent_is_known(config, "Gemini2"))
        self.assertFalse(supervisor.agent_is_known(config, ""))

    def test_agent_can_take_task_blocks_unknown_owner(self) -> None:
        config = {"agents": {"codex": {"id": "codex", "display_name": "Codex", "provider": "codex"}}}
        task = {"id": "T1", "status": "todo", "owner": "Gemini2"}
        with mock.patch.object(supervisor, "_cached_provider_capabilities", return_value={"providers": {}}):
            self.assertFalse(supervisor.agent_can_take_task(config, "Gemini2", task))
            self.assertTrue(supervisor.agent_can_take_task(config, "Codex", task))

    def test_normalize_reassigns_phantom_owner_via_default_fallback(self) -> None:
        config = {
            "schema": {"tasks_path": "tasks", "task_id_field": "id", "assignee_field": "owner", "reviewer_field": "reviewer"},
            "worker_reassignment": {"eligible_statuses": ["todo"], "owner_fallbacks": {}, "reviewer_fallbacks": {}},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        report = {"providers": {"codex": {"auth_ready": True}, "claude": {"auth_ready": True}}}
        # phantom owner "Gemini2" not in roster, no fallback mapping -> default candidates
        task = {"id": "MPOS-P1-TEL-001", "status": "todo", "owner": "Gemini2", "reviewer": "Claude", "depends_on": []}
        with (
            mock.patch.object(supervisor, "_cached_provider_capabilities", return_value=report),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.normalize_mainline_task_assignment(config, task)
        self.assertTrue(changed)
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "MPOS-P1-TEL-001")
        self.assertEqual(kwargs["new_owner"], "Codex")  # first viable default candidate (Claude is reviewer-excluded)

    def test_normalize_skips_dispatch_paused_fallback_owner(self) -> None:
        config = {
            "schema": {"tasks_path": "tasks", "task_id_field": "id", "assignee_field": "owner", "reviewer_field": "reviewer"},
            "worker_reassignment": {
                "eligible_statuses": ["todo"],
                "owner_fallbacks": {"Gemini2": ["Claude", "Codex"]},
                "reviewer_fallbacks": {},
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
            },
            "providers": {"claude": {"quota_group": "claude"}},
        }
        report = {
            "providers": {
                "codex": {"auth_ready": True},
                "claude": {"auth_ready": True},
                "copilot": {"auth_ready": True},
            }
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "claude": {
                        "provider": "claude",
                        "blocked_until": "9999-12-31T23:59:59Z",
                        "pause_kind": "quota_terminal",
                    }
                }
            }
        }
        task = {"id": "AG-DYNUI-FULL-002", "status": "todo", "owner": "Gemini2", "reviewer": "Copilot", "depends_on": []}
        with (
            mock.patch.object(supervisor, "_cached_provider_capabilities", return_value=report),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.normalize_mainline_task_assignment(config, task, state=state)
        self.assertTrue(changed)
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "AG-DYNUI-FULL-002")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Copilot")

    def test_normalize_routes_to_antigravity_when_codex_paused_and_claude_disabled(self) -> None:
        config = {
            "schema": {"tasks_path": "tasks", "task_id_field": "id", "assignee_field": "owner", "reviewer_field": "reviewer"},
            "ready_dispatcher": {"disabled_agents": ["Claude", "Claude2"]},
            "worker_reassignment": {
                "eligible_statuses": ["todo"],
                "owner_fallbacks": {"Codex2": ["Codex", "Claude", "Claude2", "Antigravity", "Antigravity2"]},
                "reviewer_fallbacks": {"Codex2": ["Codex", "Claude", "Claude2", "Antigravity", "Antigravity2"]},
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
                "antigravity": {"id": "antigravity", "display_name": "Antigravity", "provider": "antigravity"},
                "antigravity2": {"id": "antigravity2", "display_name": "Antigravity2", "provider": "antigravity2"},
            },
            "providers": {},
        }
        report = {
            "providers": {
                "codex": {"auth_ready": True},
                "codex2": {"auth_ready": True},
                "claude": {"auth_ready": True},
                "claude2": {"auth_ready": True},
                "antigravity": {"auth_ready": True},
                "antigravity2": {"auth_ready": True},
            }
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "blocked_until": "9999-12-31T23:59:59Z",
                        "pause_kind": "quota_terminal",
                    },
                    "codex2": {
                        "provider": "codex2",
                        "blocked_until": "9999-12-31T23:59:59Z",
                        "pause_kind": "quota_terminal",
                    },
                }
            }
        }
        task = {"id": "AG-DYNUI-FULL-003", "status": "todo", "owner": "Codex2", "reviewer": "Claude", "depends_on": []}

        with (
            mock.patch.object(supervisor, "_cached_provider_capabilities", return_value=report),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.normalize_mainline_task_assignment(config, task, state=state)

        self.assertTrue(changed)
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "AG-DYNUI-FULL-003")
        self.assertEqual(kwargs["new_owner"], "Antigravity")
        self.assertEqual(kwargs["new_reviewer"], "Antigravity2")

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
                    "workspace_path": "/tmp/workers/p3-001",
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
        self.assertEqual(
            sync_dispatched_task_status.call_args.kwargs["run_id"],
            "gemini-run-1",
        )
        self.assertEqual(
            Path(sync_dispatched_task_status.call_args.kwargs["workspace_path"]),
            Path("/tmp/workers/p3-001"),
        )


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

        command_env = {
            "PANTHEON_COMMAND_ROOT": str(self.root),
            "PANTHEON_COMMAND_RUNTIME_SHA": "installed-sha",
        }
        with (
            mock.patch.object(supervisor, "status_command_runtime_env", return_value=command_env),
            mock.patch.object(supervisor.subprocess, "run", return_value=mock.Mock(returncode=0, stderr="", stdout="")) as run_mock,
        ):
            changed = supervisor.sync_dispatched_task_status(self.config, event)

        self.assertTrue(changed)
        command = run_mock.call_args.args[0]
        self.assertEqual(command[2], "start")
        self.assertEqual(command[3], "APP-002-W1-FRONT-HANDOFF")
        self.assertIn("Supervisor auto-started", command[4])
        self.assertEqual(run_mock.call_args.kwargs["env"]["AI_NAME"], "Copilot")
        self.assertEqual(run_mock.call_args.kwargs["env"]["PANTHEON_STATUS_ROOT"], str(self.root))

    def test_sync_dispatched_task_status_issues_worker_lease(self) -> None:
        # ai_status.py treats the dispatched agent as an auto worker and refuses the
        # canonical mutation without a supervisor-issued lease, so the run id must
        # reach the subprocess as ORCH_RUN_ID. Omitting it raised
        # "status command lease required for auto worker" on every dispatch.
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "copilot",
            "target_display_name": "Copilot",
            "reason": "owned_ready_dispatch",
        }
        command_env = {
            "PANTHEON_COMMAND_ROOT": str(self.root),
            "PANTHEON_COMMAND_RUNTIME_SHA": "installed-sha",
        }
        workspace = self.root / "worker"
        workspace.mkdir()

        with (
            mock.patch.dict(
                supervisor.os.environ,
                {
                    "PANTHEON_WORKTREE_ROOT": "/tmp/inherited-other-worker",
                    "ORCH_WORKSPACE_PATH": "/tmp/inherited-other-worker",
                },
                clear=False,
            ),
            mock.patch.object(supervisor, "status_command_runtime_env", return_value=command_env),
            mock.patch.object(supervisor.subprocess, "run", return_value=mock.Mock(returncode=0, stderr="", stdout="")) as run_mock,
        ):
            changed = supervisor.sync_dispatched_task_status(
                self.config,
                event,
                run_id="copilot-run-7",
                workspace_path=workspace,
            )

        self.assertTrue(changed)
        command_process_env = run_mock.call_args.kwargs["env"]
        self.assertEqual(command_process_env["ORCH_RUN_ID"], "copilot-run-7")
        self.assertEqual(command_process_env["PANTHEON_WORKTREE_ROOT"], str(workspace))
        self.assertEqual(command_process_env["ORCH_WORKSPACE_PATH"], str(workspace))

    def test_sync_dispatched_task_status_rejects_run_id_without_workspace(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "copilot",
            "target_display_name": "Copilot",
            "reason": "owned_ready_dispatch",
        }

        with (
            mock.patch.object(supervisor.subprocess, "run") as run_mock,
            mock.patch.object(supervisor, "write_activity_log") as activity_log,
        ):
            changed = supervisor.sync_dispatched_task_status(
                self.config,
                event,
                run_id="copilot-run-7",
            )

        self.assertFalse(changed)
        run_mock.assert_not_called()
        self.assertIn(
            "has no workspace binding",
            activity_log.call_args.args[1]["message"],
        )

    def test_sync_dispatched_task_status_binds_worker_workspace_env(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "copilot",
            "target_display_name": "Copilot",
            "reason": "owned_ready_dispatch",
        }
        workspace = self.root / "worker-worktrees" / "app-002"
        workspace.mkdir(parents=True)
        runner_status_path = self.root / ".orchestrator" / "worker-runtime" / "status" / "copilot-run-7.json"
        heartbeat_path = self.root / ".orchestrator" / "worker-runtime" / "heartbeats" / "copilot-run-7.json"
        runner_status_path.parent.mkdir(parents=True)
        heartbeat_path.parent.mkdir(parents=True)
        worker = {
            "run_id": "copilot-run-7",
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "workspace_path": str(workspace),
            "status_root": str(self.root),
            "runner_status_path": str(runner_status_path),
            "heartbeat_path": str(heartbeat_path),
            "request_snapshot": {
                "metadata": {
                    "workspace_path": str(workspace),
                    "status_root": str(self.root),
                }
            },
        }
        command_env = {
            "PANTHEON_COMMAND_ROOT": str(self.root),
            "PANTHEON_COMMAND_RUNTIME_SHA": "installed-sha",
        }
        self.config["paths"]["state_file"] = str(self.root / ".orchestrator" / "state.json")

        with (
            mock.patch.object(supervisor, "status_command_runtime_env", return_value=command_env),
            mock.patch.object(supervisor, "load_runtime_state", return_value={"workers": {"copilot-run-7": worker}}),
            mock.patch.object(supervisor.subprocess, "run", return_value=mock.Mock(returncode=0, stderr="", stdout="")) as run_mock,
        ):
            changed = supervisor.sync_dispatched_task_status(
                self.config,
                event,
                run_id="copilot-run-7",
                workspace_path=workspace,
            )

        self.assertTrue(changed)
        env = run_mock.call_args.kwargs["env"]
        self.assertEqual(env["ORCH_RUN_ID"], "copilot-run-7")
        self.assertEqual(env["ORCH_TASK_ID"], "APP-002-W1-FRONT-HANDOFF")
        self.assertEqual(env["PANTHEON_WORKTREE_ROOT"], str(workspace.resolve()))
        self.assertEqual(env["ORCH_WORKSPACE_PATH"], str(workspace.resolve()))
        self.assertEqual(env["PANTHEON_STATUS_ROOT"], str(self.root.resolve()))
        self.assertEqual(env["ORCH_RUNNER_STATUS_PATH"], str(runner_status_path.resolve()))
        self.assertEqual(env["ORCH_HEARTBEAT_PATH"], str(heartbeat_path.resolve()))

    def test_sync_dispatched_task_status_without_run_id_does_not_inherit_lease(self) -> None:
        # A stray ORCH_RUN_ID in the supervisor environment must not be borrowed as a
        # lease for a dispatch we have no run id for.
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "copilot",
            "target_display_name": "Copilot",
            "reason": "owned_ready_dispatch",
        }
        command_env = {
            "PANTHEON_COMMAND_ROOT": str(self.root),
            "PANTHEON_COMMAND_RUNTIME_SHA": "installed-sha",
        }

        with (
            mock.patch.dict(
                supervisor.os.environ,
                {
                    "ORCH_RUN_ID": "inherited-run",
                    "ORCH_TASK_ID": "INHERITED-TASK",
                    "PANTHEON_WORKTREE_ROOT": str(self.root / "inherited-worktree"),
                    "ORCH_WORKSPACE_PATH": str(self.root / "inherited-worktree"),
                    "ORCH_RUNNER_STATUS_PATH": str(self.root / "inherited-status.json"),
                    "ORCH_HEARTBEAT_PATH": str(self.root / "inherited-heartbeat.json"),
                },
                clear=False,
            ),
            mock.patch.object(supervisor, "status_command_runtime_env", return_value=command_env),
            mock.patch.object(supervisor.subprocess, "run", return_value=mock.Mock(returncode=0, stderr="", stdout="")) as run_mock,
        ):
            changed = supervisor.sync_dispatched_task_status(self.config, event)

        self.assertTrue(changed)
        env = run_mock.call_args.kwargs["env"]
        for env_name in supervisor.DISPATCH_STATUS_WORKER_ENV_NAMES:
            self.assertNotIn(env_name, env)

    def test_run_once_syncs_dispatch_after_releasing_runtime_lock(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "copilot",
            "target_display_name": "Copilot",
            "reason": "owned_ready_dispatch",
        }
        command_env = {
            "PANTHEON_COMMAND_ROOT": str(self.root),
            "PANTHEON_COMMAND_RUNTIME_SHA": "installed-sha",
        }
        call_order: list[str] = []

        @contextlib.contextmanager
        def runtime_lock(*_args: object, **_kwargs: object):
            call_order.append("lock_enter")
            try:
                yield
            finally:
                call_order.append("lock_exit")

        def locked_cycle(*_args: object, **_kwargs: object) -> bool:
            call_order.append("locked_cycle")
            changed = supervisor.sync_dispatched_task_status(
                self.config,
                event,
                run_id="copilot-run-7",
                workspace_path=self.root,
            )
            self.assertFalse(changed)
            return False

        def status_command(*_args: object, **_kwargs: object) -> mock.Mock:
            call_order.append("status_command")
            return mock.Mock(returncode=0, stderr="", stdout="")

        with (
            mock.patch.object(supervisor, "runtime_state_lock", side_effect=runtime_lock),
            mock.patch.object(supervisor, "_run_once_locked", side_effect=locked_cycle),
            mock.patch.object(supervisor, "status_command_runtime_env", return_value=command_env),
            # Provider probing is its own pre-lock step and shells out too; it is
            # stubbed here so call_order records only the dispatch sync.
            mock.patch.object(supervisor, "probe_provider_reports", return_value=({}, {})),
            mock.patch.object(supervisor.subprocess, "run", side_effect=status_command) as run_mock,
        ):
            changed = supervisor.run_once(self.config, watch=False)

        self.assertTrue(changed)
        self.assertEqual(
            call_order,
            ["lock_enter", "locked_cycle", "lock_exit", "status_command"],
        )
        self.assertEqual(run_mock.call_args.kwargs["env"]["ORCH_RUN_ID"], "copilot-run-7")
        self.assertEqual(
            run_mock.call_args.kwargs["env"]["PANTHEON_WORKTREE_ROOT"],
            str(self.root),
        )
        self.assertEqual(
            run_mock.call_args.kwargs["env"]["ORCH_WORKSPACE_PATH"],
            str(self.root),
        )

    def test_run_once_probes_providers_before_taking_the_runtime_lock(self) -> None:
        """A gh auth probe must not be charged to the exclusive runtime lock.

        Live symptom: supervisor PID 901543 held runtime-admission inode 807896
        while reviewer and status processes queued, and the provider probe was
        one of the unbounded external waits inside that hold.
        """

        call_order: list[str] = []

        @contextlib.contextmanager
        def runtime_lock(*_args: object, **_kwargs: object):
            call_order.append("lock_enter")
            try:
                yield
            finally:
                call_order.append("lock_exit")

        def locked_cycle(*_args: object, **kwargs: object) -> bool:
            call_order.append("locked_cycle")
            self.assertEqual(kwargs["provider_reports"], ({"previous": True}, {"fresh": True}))
            return False

        def probe(*_args: object, **_kwargs: object) -> tuple[dict, dict]:
            call_order.append("provider_probe")
            return ({"previous": True}, {"fresh": True})

        with (
            mock.patch.object(supervisor, "runtime_state_lock", side_effect=runtime_lock),
            mock.patch.object(supervisor, "_run_once_locked", side_effect=locked_cycle),
            mock.patch.object(supervisor, "probe_provider_reports", side_effect=probe),
        ):
            supervisor.run_once(self.config, watch=False)

        self.assertEqual(
            call_order,
            ["provider_probe", "lock_enter", "locked_cycle", "lock_exit"],
        )

    def test_run_once_fetches_worker_base_before_taking_runtime_lock(self) -> None:
        """The exact origin/dev network refresh must precede admission."""

        call_order: list[str] = []

        @contextlib.contextmanager
        def runtime_lock(*_args: object, **_kwargs: object):
            call_order.append("lock_enter")
            try:
                yield
            finally:
                call_order.append("lock_exit")

        def fetch_base(_repo_root: Path, base_ref: str) -> tuple[bool, None]:
            self.assertEqual(base_ref, "origin/dev")
            call_order.append("fetch_base")
            return True, None

        def locked_cycle(*_args: object, **_kwargs: object) -> bool:
            call_order.append("locked_cycle")
            self.assertEqual(
                supervisor._worker_base_ref_precondition("origin/dev"),
                (True, None),
            )
            return False

        with (
            mock.patch.object(supervisor, "probe_provider_reports", return_value=({}, {})),
            mock.patch.object(supervisor, "load_runtime_state_snapshot", return_value={}),
            mock.patch.object(supervisor, "pending_worker_base_refs", return_value={"origin/dev"}),
            mock.patch.object(supervisor, "_fetch_worker_base_ref", side_effect=fetch_base),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "runtime_state_lock", side_effect=runtime_lock),
            mock.patch.object(supervisor, "_run_once_locked", side_effect=locked_cycle),
        ):
            changed = supervisor.run_once(self.config, watch=False)

        self.assertFalse(changed)
        self.assertEqual(
            call_order,
            ["fetch_base", "lock_enter", "locked_cycle", "lock_exit"],
        )

    def test_run_once_syncs_github_bus_before_taking_the_runtime_lock(self) -> None:
        """No gh/API subprocess may extend the exclusive admission hold."""

        call_order: list[str] = []

        @contextlib.contextmanager
        def runtime_lock(*_args: object, **_kwargs: object):
            call_order.append("lock_enter")
            try:
                yield
            finally:
                call_order.append("lock_exit")

        def github_sync(
            _config: dict[str, object],
            runtime_snapshot: dict[str, object],
        ) -> bool:
            call_order.append("github_sync")
            self.assertEqual(runtime_snapshot, {"snapshot": True})
            return True

        def locked_cycle(*_args: object, **kwargs: object) -> bool:
            call_order.append("locked_cycle")
            self.assertTrue(kwargs["prelock_changed"])
            return True

        with (
            mock.patch.object(supervisor, "probe_provider_reports", return_value=({}, {})),
            mock.patch.object(
                supervisor,
                "load_runtime_state_snapshot",
                return_value={"snapshot": True},
            ),
            mock.patch.object(supervisor, "sync_github_bus", side_effect=github_sync),
            mock.patch.object(supervisor, "runtime_state_lock", side_effect=runtime_lock),
            mock.patch.object(supervisor, "_run_once_locked", side_effect=locked_cycle),
        ):
            changed = supervisor.run_once(self.config, watch=False)

        self.assertTrue(changed)
        self.assertEqual(
            call_order,
            ["github_sync", "lock_enter", "locked_cycle", "lock_exit"],
        )

    def test_run_once_prefetches_ownerless_pr_metadata_before_runtime_lock(self) -> None:
        task_id = "SUP-SQUASH-PREFETCH"
        head = "a" * 40
        status = {
            "tasks": [
                {
                    "id": task_id,
                    "status": "in_progress",
                    "owner": "Claude",
                    "reviewer": "Codex2",
                }
            ]
        }
        runtime_snapshot = {
            "workers": {
                "claude-run": {
                    "run_id": "claude-run",
                    "task_id": task_id,
                    "logical_agent_id": "claude",
                    "agent_id": "claude",
                    "provider": "claude",
                    "status": "completed",
                    "runner_status": "completed",
                    "exit_code": 0,
                    "lease_acquired_at": "2026-07-27T14:00:00Z",
                    "commit_progress_count": 1,
                    "last_commit_progress_at": "2026-07-27T14:01:00Z",
                    "work_progress_snapshot": {"commit_sha": head},
                    "request_snapshot": {
                        "reason": supervisor.REASON_OWNED_IN_PROGRESS
                    },
                }
            },
            "queue": {"events": {}},
        }
        self.config["agents"].update(
            {
                "claude": {
                    "id": "claude",
                    "display_name": "Claude",
                    "provider": "claude",
                },
                "codex2": {
                    "id": "codex2",
                    "display_name": "Codex2",
                    "provider": "codex2",
                },
            }
        )
        self.config["branch_workflow"] = {
            "task_branch_prefix": "task/",
            "dev_branch": "dev",
        }
        lock_held = False
        call_order: list[str] = []

        @contextlib.contextmanager
        def runtime_lock(*_args: object, **_kwargs: object):
            nonlocal lock_held
            lock_held = True
            call_order.append("lock_enter")
            try:
                yield
            finally:
                call_order.append("lock_exit")
                lock_held = False

        def gh_lookup(*_args: object, **_kwargs: object) -> mock.Mock:
            self.assertFalse(lock_held, "gh PR lookup ran under runtime lock")
            call_order.append("gh_lookup")
            return mock.Mock(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "number": 4254,
                            "state": "MERGED",
                            "headRefName": f"task/{task_id}",
                            "headRefOid": head,
                            "baseRefName": "dev",
                            "mergedAt": "2026-07-27T14:10:00Z",
                            "mergeCommit": {"oid": "b" * 40},
                            "url": "https://github.com/ajoe734/pantheon/pull/4254",
                        }
                    ]
                ),
            )

        def locked_cycle(*_args: object, **kwargs: object) -> bool:
            call_order.append("locked_cycle")
            snapshot = kwargs["ownerless_pr_snapshots"][task_id]
            self.assertEqual(snapshot["worker_run_id"], "claude-run")
            self.assertEqual(snapshot["delivery_head"], head)
            self.assertEqual(snapshot["records"][0]["number"], 4254)
            return False

        with (
            mock.patch.object(supervisor, "probe_provider_reports", return_value=({}, {})),
            mock.patch.object(
                supervisor,
                "load_runtime_state_snapshot",
                return_value=runtime_snapshot,
            ),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "resolve_gh_binary", return_value="gh"),
            mock.patch.object(
                supervisor,
                "_repository_slug_from_remote",
                return_value="ajoe734/pantheon",
            ),
            mock.patch.object(supervisor, "run_gh_process", side_effect=gh_lookup),
            mock.patch.object(supervisor, "pending_worker_base_refs", return_value=set()),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "runtime_state_lock", side_effect=runtime_lock),
            mock.patch.object(supervisor, "_run_once_locked", side_effect=locked_cycle),
        ):
            changed = supervisor.run_once(self.config, watch=False)

        self.assertFalse(changed)
        self.assertEqual(
            call_order,
            ["gh_lookup", "lock_enter", "locked_cycle", "lock_exit"],
        )

    def test_run_once_confirms_worker_termination_after_runtime_lock_release(self) -> None:
        """Both the signal and confirm/poll path run after admission."""

        call_order: list[str] = []

        @contextlib.contextmanager
        def runtime_lock(*_args: object, **_kwargs: object):
            call_order.append("lock_enter")
            try:
                yield
            finally:
                call_order.append("lock_exit")

        def confirm_kill(pid: int, **_kwargs: object) -> bool:
            self.assertEqual(pid, 4242)
            self.assertTrue(_kwargs["is_alive"](pid))
            call_order.append("confirm_kill")
            _kwargs["send_signal"](pid, signal.SIGTERM)
            return True

        def locked_cycle() -> bool:
            self.assertFalse(supervisor.terminate_worker_pid(4242))
            return True

        with (
            mock.patch.object(supervisor, "runtime_state_lock", side_effect=runtime_lock),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "worker_pid_start_ticks", return_value=777),
            mock.patch.object(
                supervisor.os,
                "kill",
                side_effect=lambda pid, sent_signal: call_order.append(
                    f"signal:{pid}:{sent_signal}"
                ),
            ),
            mock.patch.object(
                supervisor.rewrite_worker_lifecycle,
                "confirm_kill",
                side_effect=confirm_kill,
            ),
        ):
            changed = supervisor._run_with_deferred_dispatch_status_syncs(
                self.config,
                locked_cycle,
            )

        self.assertTrue(changed)
        self.assertEqual(
            call_order,
            [
                "lock_enter",
                "lock_exit",
                "confirm_kill",
                f"signal:4242:{signal.SIGTERM}",
            ],
        )

    def test_deferred_termination_does_not_signal_a_reused_pid(self) -> None:
        """The lock-release confirmer is bound to the worker's start time."""

        call_order: list[str] = []
        start_ticks = iter([111, 222])

        with (
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(
                supervisor,
                "worker_pid_start_ticks",
                side_effect=lambda _pid: next(start_ticks),
            ),
            mock.patch.object(
                supervisor.os,
                "kill",
                side_effect=lambda _pid, sent_signal: call_order.append(
                    f"signal:{sent_signal}"
                ),
            ),
            mock.patch.object(
                supervisor.rewrite_worker_lifecycle,
                "confirm_kill",
            ) as confirm_kill,
        ):
            self.assertFalse(
                supervisor._run_with_deferred_dispatch_status_syncs(
                    self.config,
                    lambda: supervisor.terminate_worker_pid(4242),
                )
            )

        self.assertEqual(call_order, [])
        confirm_kill.assert_not_called()

    def test_deferred_termination_without_start_ticks_fails_closed(self) -> None:
        """An unreadable /proc identity never authorizes a signal."""

        with (
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "worker_pid_start_ticks", return_value=None),
            mock.patch.object(supervisor.os, "kill") as send_signal,
            mock.patch.object(
                supervisor.rewrite_worker_lifecycle,
                "confirm_kill",
            ) as confirm_kill,
        ):
            changed = supervisor._run_with_deferred_dispatch_status_syncs(
                self.config,
                lambda: supervisor.terminate_worker_pid(4242),
            )

        self.assertFalse(changed)
        send_signal.assert_not_called()
        confirm_kill.assert_not_called()

    def test_worker_remains_nonterminal_until_deferred_confirmation(self) -> None:
        worker = {
            "run_id": "chair-run",
            "task_id": "OPS-CHAIR-REVIEW",
            "pid": 4242,
            "status": "running",
        }

        def locked_cycle() -> bool:
            result = supervisor.poll_worker_assignment_stage(
                self.config,
                {"workers": {"chair-run": worker}},
                worker,
                run_id="chair-run",
                provider_report={},
                task_map={},
                active_worker_statuses={"running"},
                alive=True,
            )
            self.assertEqual(result, {"changed": False, "stop": True})
            self.assertEqual(worker["status"], "running")
            return result["changed"]

        with (
            mock.patch.object(
                supervisor,
                "chair_review_worker_artifacts_applied",
                return_value=True,
            ),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "worker_pid_start_ticks", return_value=777),
            mock.patch.object(
                supervisor.rewrite_worker_lifecycle,
                "confirm_kill",
                return_value=True,
            ) as confirm_kill,
        ):
            changed = supervisor._run_with_deferred_dispatch_status_syncs(
                self.config,
                locked_cycle,
            )

        self.assertFalse(changed)
        self.assertEqual(worker["status"], "running")
        confirm_kill.assert_called_once()

    def test_auto_commit_archive_subprocess_runs_after_runtime_admission(self) -> None:
        self.config["paths"].update(
            {
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            }
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        script = self.root / ".orchestrator" / "auto_commit_archive.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        self.config["auto_commit_archive"] = {
            "enabled": True,
            "tick_interval_seconds": 0,
            "script_timeout_seconds": 180,
        }
        lock_depth = 0
        call_order: list[str] = []

        @contextlib.contextmanager
        def runtime_lock(*_args: object, **_kwargs: object):
            nonlocal lock_depth
            lock_depth += 1
            call_order.append("lock_enter")
            try:
                yield
            finally:
                call_order.append("lock_exit")
                lock_depth -= 1

        def run_archive(*_args: object, **_kwargs: object) -> mock.Mock:
            self.assertEqual(lock_depth, 0, "archive subprocess ran under runtime lock")
            call_order.append("archive_subprocess")
            return mock.Mock(
                returncode=0,
                stdout="auto_commit_archive: opened PR for task briefs\n",
                stderr="",
            )

        def locked_cycle() -> bool:
            state = supervisor.load_runtime_state(self.config)
            scheduled = supervisor.maybe_auto_commit_archive(self.config, state)
            self.assertTrue(scheduled)
            supervisor.save_runtime_state(self.config, state)
            return scheduled

        with (
            mock.patch.object(
                supervisor,
                "runtime_state_lock",
                side_effect=runtime_lock,
            ),
            mock.patch.object(
                supervisor.subprocess,
                "run",
                side_effect=run_archive,
            ),
            mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"),
        ):
            changed = supervisor._run_with_deferred_dispatch_status_syncs(
                self.config,
                locked_cycle,
            )

        self.assertTrue(changed)
        self.assertEqual(
            call_order,
            [
                "lock_enter",
                "lock_exit",
                "archive_subprocess",
                "lock_enter",
                "lock_exit",
            ],
        )
        bucket = supervisor.load_runtime_state(self.config)["auto_commit_archive"]
        self.assertIsNone(bucket["pending_token"])
        self.assertIsNone(bucket["pending_since"])
        self.assertEqual(bucket["last_exit"], 0)
        self.assertIsNone(bucket["last_error"])

    def test_auto_commit_archive_result_rejects_a_stale_token(self) -> None:
        self.config["paths"].update(
            {
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            }
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        state = supervisor.load_runtime_state(self.config)
        state["auto_commit_archive"] = {
            "pending_token": "current-token",
            "pending_since": "2026-07-27T15:00:00Z",
        }
        supervisor.save_runtime_state(self.config, state)

        applied = supervisor.apply_auto_commit_archive_result(
            self.config,
            {
                "token": "stale-token",
                "scheduled_at": "2026-07-27T15:00:00Z",
            },
            {
                "finished_at": "2026-07-27T15:00:01Z",
                "last_exit": 0,
                "opened_pr": True,
            },
        )

        self.assertFalse(applied)
        bucket = supervisor.load_runtime_state(self.config)["auto_commit_archive"]
        self.assertEqual(bucket["pending_token"], "current-token")
        self.assertNotIn("last_exit", bucket)

    def test_sync_status_pipeline_uses_installed_command_runtime(self) -> None:
        command_env = {
            "PANTHEON_COMMAND_ROOT": str(self.root),
            "PANTHEON_COMMAND_RUNTIME_SHA": "installed-sha",
        }

        with (
            mock.patch.object(supervisor, "status_command_runtime_env", return_value=command_env),
            mock.patch.object(
                supervisor.subprocess,
                "run",
                return_value=mock.Mock(returncode=0, stderr="", stdout=""),
            ) as run_mock,
        ):
            changed = supervisor.sync_status_pipeline(self.config)

        self.assertTrue(changed)
        command = run_mock.call_args.args[0]
        self.assertEqual(command[:3], [sys.executable, str(self.root / "scripts" / "ai_status.py"), "recover"])
        self.assertEqual(run_mock.call_args.kwargs["cwd"], str(self.root))
        self.assertEqual(run_mock.call_args.kwargs["env"]["PANTHEON_STATUS_ROOT"], str(self.root))

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


class RuntimeLockHoldTests(unittest.TestCase):
    """The exclusive hold is the ceiling on every worker status command's wait."""

    def test_hold_within_budget_is_published_without_a_warning(self) -> None:
        state: dict[str, object] = {}

        held = supervisor.record_runtime_lock_hold(
            {"supervisor": {"runtime_lock_hold_warn_after_seconds": 30}},
            state,
            time.monotonic(),
        )

        supervisor_state = state["supervisor"]
        self.assertLess(held, 30)
        self.assertEqual(supervisor_state["runtime_lock_hold_seconds"], held)
        self.assertFalse(supervisor_state["runtime_lock_hold_exceeded"])

    def test_multi_minute_hold_is_flagged_and_peak_is_retained(self) -> None:
        """The live 771s hold left no trace in runtime state; it must now."""

        state: dict[str, object] = {}
        config = {"supervisor": {"runtime_lock_hold_warn_after_seconds": 30}}

        supervisor.record_runtime_lock_hold(config, state, time.monotonic() - 771.0)
        supervisor_state = state["supervisor"]
        self.assertGreaterEqual(supervisor_state["runtime_lock_hold_seconds"], 771.0)
        self.assertTrue(supervisor_state["runtime_lock_hold_exceeded"])
        peak = supervisor_state["runtime_lock_hold_peak_seconds"]

        # A later healthy cycle clears the flag but does not erase the peak.
        supervisor.record_runtime_lock_hold(config, state, time.monotonic())
        self.assertFalse(supervisor_state["runtime_lock_hold_exceeded"])
        self.assertEqual(supervisor_state["runtime_lock_hold_peak_seconds"], peak)


class TaskStateShadowCatchupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.status_file = self.root / "ai-status.json"
        self.event_log = self.root / "runtime" / "task-state-events.jsonl"
        self.config = {
            "paths": {"status_file": str(self.status_file)},
            "task_state_store": {
                "mode": "shadow",
                "event_log": str(self.event_log),
            },
        }
        self.runtime_state = {"supervisor": {}}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_status(self, status: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "tasks": [{"id": "STATE-CATCHUP-001", "status": status}]
        }
        self.status_file.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def test_catches_up_direct_writes_and_is_idempotent_at_parity(self) -> None:
        first = self.write_status("todo")

        self.assertTrue(supervisor.sync_task_state_shadow(self.config, self.runtime_state))
        events = supervisor.rewrite_task_state_store.load_events(self.event_log)
        self.assertEqual([event["state"] for event in events], [first])
        self.assertTrue(self.runtime_state["supervisor"]["task_state_shadow"]["ok"])

        self.assertFalse(supervisor.sync_task_state_shadow(self.config, self.runtime_state))
        self.assertEqual(len(supervisor.rewrite_task_state_store.load_events(self.event_log)), 1)

        second = self.write_status("in_progress")
        self.assertTrue(supervisor.sync_task_state_shadow(self.config, self.runtime_state))
        events = supervisor.rewrite_task_state_store.load_events(self.event_log)
        self.assertEqual([event["state"] for event in events], [first, second])
        shadow = self.runtime_state["supervisor"]["task_state_shadow"]
        self.assertTrue(shadow["ok"])
        self.assertTrue(shadow["caught_up"])
        self.assertEqual(shadow["event_count"], 2)

    def test_corrupt_journal_is_reported_without_touching_canonical_state(self) -> None:
        expected = self.write_status("todo")
        self.event_log.parent.mkdir(parents=True)
        self.event_log.write_text("{broken\n", encoding="utf-8")

        self.assertFalse(supervisor.sync_task_state_shadow(self.config, self.runtime_state))

        self.assertEqual(json.loads(self.status_file.read_text(encoding="utf-8")), expected)
        shadow = self.runtime_state["supervisor"]["task_state_shadow"]
        self.assertFalse(shadow["ok"])
        self.assertIn("invalid task-state event", shadow["last_error"])

    def test_authoritative_mode_repairs_file_from_journal_without_importing_drift(self) -> None:
        canonical = self.write_status("todo")
        supervisor.rewrite_task_state_store.append_state_commit(
            self.event_log,
            canonical,
            source="migration",
        )
        self.write_status("done")
        self.config["task_state_store"]["mode"] = "authoritative"

        self.assertTrue(supervisor.sync_task_state_shadow(self.config, self.runtime_state))

        self.assertEqual(json.loads(self.status_file.read_text(encoding="utf-8")), canonical)
        events = supervisor.rewrite_task_state_store.load_events(self.event_log)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["state"], canonical)
        store_state = self.runtime_state["supervisor"]["task_state_shadow"]
        self.assertEqual(store_state["mode"], "authoritative")
        self.assertTrue(store_state["ok"])
        self.assertTrue(store_state["caught_up"])

    def test_caught_up_reports_parity_and_repaired_reports_the_write(self) -> None:
        """caught_up used to be the divergence predicate, exactly inverted.

        A healthy cycle published caught_up=false while a cycle that had just
        rewritten a drifted board published caught_up=true, so the field could
        not be used to tell whether the projection matched the journal.
        """

        canonical = self.write_status("todo")
        supervisor.rewrite_task_state_store.append_state_commit(
            self.event_log,
            canonical,
            source="migration",
        )
        self.config["task_state_store"]["mode"] = "authoritative"

        # Board already matches the journal head: nothing to repair, and the
        # projection is by definition caught up.
        self.assertFalse(supervisor.sync_task_state_shadow(self.config, self.runtime_state))
        shadow = self.runtime_state["supervisor"]["task_state_shadow"]
        self.assertTrue(shadow["caught_up"])
        self.assertFalse(shadow["repaired"])

        # Drift the board; the cycle must repair it and still report parity.
        self.write_status("done")
        self.assertTrue(supervisor.sync_task_state_shadow(self.config, self.runtime_state))
        shadow = self.runtime_state["supervisor"]["task_state_shadow"]
        self.assertTrue(shadow["caught_up"])
        self.assertTrue(shadow["repaired"])
        self.assertEqual(
            json.loads(self.status_file.read_text(encoding="utf-8")),
            canonical,
        )

    def test_shadow_mode_reports_parity_and_repair_separately(self) -> None:
        first = self.write_status("todo")

        self.assertTrue(supervisor.sync_task_state_shadow(self.config, self.runtime_state))
        shadow = self.runtime_state["supervisor"]["task_state_shadow"]
        self.assertTrue(shadow["caught_up"])
        self.assertTrue(shadow["repaired"])
        self.assertEqual(shadow["event_count"], 1)

        self.assertFalse(supervisor.sync_task_state_shadow(self.config, self.runtime_state))
        shadow = self.runtime_state["supervisor"]["task_state_shadow"]
        self.assertTrue(shadow["caught_up"])
        self.assertFalse(shadow["repaired"])
        self.assertEqual(
            supervisor.rewrite_task_state_store.load_events(self.event_log)[0]["state"],
            first,
        )

    def test_repair_that_never_lands_is_not_reported_as_caught_up(self) -> None:
        """Parity is asserted about the board on disk, not the value written.

        Comparing the journal head to the in-memory state just handed to
        write_json would make the check tautological: any repair would report
        success whether or not the file changed.
        """

        canonical = self.write_status("todo")
        supervisor.rewrite_task_state_store.append_state_commit(
            self.event_log,
            canonical,
            source="migration",
        )
        drifted = self.write_status("done")
        self.config["task_state_store"]["mode"] = "authoritative"

        with mock.patch.object(supervisor, "write_json"):
            self.assertFalse(supervisor.sync_task_state_shadow(self.config, self.runtime_state))

        shadow = self.runtime_state["supervisor"]["task_state_shadow"]
        self.assertFalse(shadow["ok"])
        self.assertFalse(shadow["caught_up"])
        self.assertIn("remains divergent", shadow["last_error"])
        self.assertEqual(
            json.loads(self.status_file.read_text(encoding="utf-8")),
            drifted,
        )

    def test_reconciliation_replays_the_journal_once_per_cycle(self) -> None:
        """The reconciliation phase must not pay for the journal four times.

        The previous body ran load_events, project_latest_state, and then
        verify_projection -- which loaded and projected the log all over again --
        inside the exclusive canonical lock. On the live 2050-event journal that
        was four full replays per cycle while every reviewer and status command
        queued on the same lock.
        """

        canonical = self.write_status("todo")
        supervisor.rewrite_task_state_store.append_state_commit(
            self.event_log,
            canonical,
            source="migration",
        )
        self.config["task_state_store"]["mode"] = "authoritative"

        store = supervisor.rewrite_task_state_store
        reads: list[str] = []
        real_snapshot = store.load_snapshot
        real_load_events = store.load_events

        with (
            mock.patch.object(
                store,
                "load_snapshot",
                side_effect=lambda *a, **k: (reads.append("snapshot"), real_snapshot(*a, **k))[1],
            ),
            mock.patch.object(
                store,
                "load_events",
                side_effect=lambda *a, **k: (reads.append("events"), real_load_events(*a, **k))[1],
            ),
        ):
            supervisor.sync_task_state_shadow(self.config, self.runtime_state)

        self.assertEqual(reads, ["snapshot"])

    def test_run_once_reconciles_task_state_before_runtime_admission(self) -> None:
        canonical = self.write_status("todo")
        supervisor.rewrite_task_state_store.append_state_commit(
            self.event_log,
            canonical,
            source="migration",
        )
        self.config["task_state_store"]["mode"] = "authoritative"
        runtime_snapshot = {"supervisor": {}}
        lock_held = False
        call_order: list[str] = []

        @contextlib.contextmanager
        def runtime_lock(*_args: object, **_kwargs: object):
            nonlocal lock_held
            lock_held = True
            call_order.append("lock_enter")
            try:
                yield
            finally:
                call_order.append("lock_exit")
                lock_held = False

        real_sync = supervisor.sync_task_state_shadow

        def reconcile_before_lock(
            config: dict[str, object],
            state: dict[str, object],
        ) -> bool:
            self.assertFalse(lock_held)
            call_order.append("task_state_shadow")
            return real_sync(config, state)

        def locked_cycle(*_args: object, **kwargs: object) -> bool:
            call_order.append("locked_cycle")
            snapshot = kwargs["task_state_shadow_snapshot"]
            self.assertTrue(snapshot["report"]["ok"])
            self.assertTrue(snapshot["report"]["caught_up"])
            return False

        with (
            mock.patch.object(
                supervisor,
                "load_runtime_state_snapshot",
                return_value=runtime_snapshot,
            ),
            mock.patch.object(
                supervisor,
                "sync_task_state_shadow",
                side_effect=reconcile_before_lock,
            ),
            mock.patch.object(supervisor, "probe_provider_reports", return_value=({}, {})),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "runtime_state_lock", side_effect=runtime_lock),
            mock.patch.object(supervisor, "_run_once_locked", side_effect=locked_cycle),
        ):
            changed = supervisor.run_once(self.config, watch=False)

        self.assertFalse(changed)
        self.assertEqual(
            call_order,
            ["task_state_shadow", "lock_enter", "locked_cycle", "lock_exit"],
        )

    def test_reconciliation_report_describes_one_journal_generation(self) -> None:
        """The projection report must not straddle two journal generations.

        Live symptom: a verifier started around a lock handoff reported
        event_count=2046 with the expected SHA taken from event 2045 and the
        projected SHA from event 2046, because the board and the journal were
        sampled in two separate lock windows. A stable rerun at event 2049 then
        returned ok=true, so the failure looked like flapping truth.
        """

        canonical = self.write_status("todo")
        supervisor.rewrite_task_state_store.append_state_commit(
            self.event_log,
            canonical,
            source="migration",
        )
        self.config["task_state_store"]["mode"] = "authoritative"

        store = supervisor.rewrite_task_state_store
        real_snapshot = store.load_snapshot

        def snapshot_then_append(*args: object, **kwargs: object) -> dict:
            snapshot = real_snapshot(*args, **kwargs)
            # A concurrent writer commits the moment our snapshot is taken.
            store.append_state_commit(
                self.event_log,
                {"tasks": [{"id": "STATE-CATCHUP-001", "status": "review"}]},
                source="concurrent-writer",
            )
            return snapshot

        with mock.patch.object(store, "load_snapshot", side_effect=snapshot_then_append):
            supervisor.sync_task_state_shadow(self.config, self.runtime_state)

        shadow = self.runtime_state["supervisor"]["task_state_shadow"]
        self.assertTrue(shadow["ok"])
        self.assertTrue(shadow["caught_up"])
        # Count, digests, and last event id all describe the snapshot the phase
        # actually reconciled -- never a mix of the two generations.
        self.assertEqual(shadow["event_count"], 1)
        self.assertEqual(
            shadow["projected_state_sha256"],
            shadow["expected_state_sha256"],
        )

    def test_authoritative_mode_reports_empty_journal_without_touching_file(self) -> None:
        expected = self.write_status("todo")
        self.config["task_state_store"]["mode"] = "authoritative"

        self.assertFalse(supervisor.sync_task_state_shadow(self.config, self.runtime_state))

        self.assertEqual(json.loads(self.status_file.read_text(encoding="utf-8")), expected)
        store_state = self.runtime_state["supervisor"]["task_state_shadow"]
        self.assertEqual(store_state["mode"], "authoritative")
        self.assertFalse(store_state["ok"])
        self.assertIn("journal is empty", store_state["last_error"])


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
                def __init__(self, _task_lookup, **_kwargs):
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
            mock.patch.object(supervisor, "utc_now", return_value="2026-07-14T09:00:00Z"),
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
            stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
            stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
            stack.enter_context(mock.patch.object(supervisor, "prune_orphan_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_chair_review_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "maybe_auto_commit_archive", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
            stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
            stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
            changed = supervisor.run_once(config, watch=False, replay=False)

        self.assertTrue(changed)
        dispatch_chair_review.assert_called_once()
        dispatch_ready_tasks.assert_called_once()

    def test_run_once_isolates_failing_phase_and_still_dispatches(self) -> None:
        """Phase 0: a phase that raises degrades only itself; the cycle does not
        abort and later phases (dispatch) still run. Before per-phase isolation a
        single raise (e.g. a missing activity-log archive) short-circuited every
        later phase and crash-looped the supervisor."""
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
            # An early phase raises — this must NOT abort the cycle.
            stack.enter_context(
                mock.patch.object(
                    supervisor,
                    "sync_coordination_files",
                    side_effect=RuntimeError("simulated activity resolution superseded archive is missing"),
                )
            )
            stack.enter_context(mock.patch.object(supervisor, "poll_workers", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "reconcile_queue_records", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_event_queue", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "load_discussion_planning_state", return_value=None))
            stack.enter_context(mock.patch.object(supervisor, "auto_materialize_discussion_planning", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "watchdog_safe_mode_active", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "chair_review_failure_loop_details", return_value=[]))
            stack.enter_context(mock.patch.object(supervisor, "dispatch_chair_review", return_value=False))
            dispatch_ready_tasks = stack.enter_context(mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=True))
            stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
            stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
            stack.enter_context(mock.patch.object(supervisor, "prune_orphan_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_chair_review_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "maybe_auto_commit_archive", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
            stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
            stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
            # Must return normally despite the failing phase (no raise escapes).
            supervisor.run_once(config, watch=False, replay=False)

        # The later dispatch phase still ran — the failure was isolated.
        dispatch_ready_tasks.assert_called_once()

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
            process_queue = stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=True))
            stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
            stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
            stack.enter_context(mock.patch.object(supervisor, "prune_orphan_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_chair_review_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
            stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
            stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
            write_activity_log = stack.enter_context(mock.patch.object(supervisor, "write_activity_log"))
            changed = supervisor.run_once(config, watch=False, replay=False)

        self.assertTrue(changed)
        dispatch_discussion_planning.assert_not_called()
        dispatch_ready_tasks.assert_not_called()
        dispatch_chair_review.assert_not_called()
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
                                "__init__": lambda self, _task_lookup, **_kwargs: None,
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


class SupervisorRuntimeAdmissionLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.state_path = self.root / "runtime-state.json"
        self.event_queue_path = self.root / "event-queue.jsonl"
        self.approval_queue_path = self.root / "approval-queue.json"
        self.status_path = self.root / "ai-status.json"
        self.activity_path = self.root / "ai-activity-log.jsonl"
        self.config = {
            "paths": {
                "state_file": str(self.state_path),
                "event_queue": str(self.event_queue_path),
                "approval_queue": str(self.approval_queue_path),
                "status_file": str(self.status_path),
                "activity_log": str(self.activity_path),
            },
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "active_worker_statuses": ["running"],
            },
        }
        self.state_path.write_text(
            json.dumps(runtime_state.default_state()) + "\n",
            encoding="utf-8",
        )
        self.event_queue_path.write_text("", encoding="utf-8")
        self.approval_queue_path.write_text(
            json.dumps({"version": 2, "pending": [], "history": []}) + "\n",
            encoding="utf-8",
        )
        self.status_path.write_text(json.dumps({"tasks": []}) + "\n", encoding="utf-8")

    @staticmethod
    def _stop_process(process: multiprocessing.Process) -> None:
        if process.is_alive():
            process.kill()
        process.join(timeout=5)

    def test_run_once_holds_runtime_lock_across_nested_writer_transaction(self) -> None:
        context = multiprocessing.get_context("fork")
        parent_connection, child_connection = context.Pipe()
        process = context.Process(
            target=_run_supervisor_writer_transaction_until_released,
            args=(self.config, child_connection),
        )
        process.start()
        child_connection.close()
        self.addCleanup(parent_connection.close)
        self.addCleanup(self._stop_process, process)

        self.assertTrue(parent_connection.poll(5), "supervisor transaction did not reach its midpoint")
        self.assertEqual(parent_connection.recv()[0], "mid-transaction")
        lock_path = runtime_state.runtime_admission_lock_path(self.config)
        lock_inode = lock_path.stat().st_ino

        with self.assertRaises(BlockingIOError):
            with runtime_state.tasks_runtime_admission_guard(
                self.config,
                ["LOCK-TASK"],
                strict=True,
                shared=True,
                nonblocking=True,
            ):
                self.fail("nonblocking admission entered while supervisor held the writer lock")

        parent_connection.send("release")
        self.assertTrue(parent_connection.poll(5), "supervisor transaction did not finish")
        self.assertEqual(parent_connection.recv(), ("completed", True))
        process.join(timeout=5)
        self.assertEqual(process.exitcode, 0)

        self.assertEqual(lock_path.stat().st_ino, lock_inode)
        state = runtime_state.load_runtime_state(self.config)
        self.assertIn("before-release", state["workers"])
        self.assertIn("after-release", state["workers"])
        self.assertEqual(
            runtime_state.load_event_queue(self.config),
            [
                {
                    "event_id": "evt-after-release",
                    "task_id": "QUEUE-TASK",
                    "status": "queued",
                }
            ],
        )

    def test_process_queue_builds_full_task_brief_inside_run_once_lock_context(self) -> None:
        task_id = "OPS-TASK-BRIEF-LOCK-ORDER-TEST"
        dependency_id = "OPS-TASK-BRIEF-ARCHIVED-DEPENDENCY"
        task = {
            "id": task_id,
            "title": "Generate the complete task brief",
            "summary_zh": "在 supervisor dispatch 鎖上下文內生成完整 brief。",
            "phase": "operations",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Codex2",
            "depends_on": [dependency_id],
            "artifacts": [".orchestrator/common.py"],
            "next": "Use the complete task-scoped context",
        }
        self.status_path.write_text(
            json.dumps({"tasks": [task]}) + "\n",
            encoding="utf-8",
        )
        archive_dir = self.root / "ai-task-archive" / "tasks"
        archive_dir.mkdir(parents=True)
        (archive_dir / f"{dependency_id}.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "task_id": dependency_id,
                    "archived_at": "2026-07-26T00:00:00Z",
                    "terminal_status": "done",
                    "terminal_outcome": "completed",
                    "task": {
                        "id": dependency_id,
                        "title": "Archived dependency",
                        "status": "done",
                    },
                    "handoffs": [],
                    "blockers": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        event = {
            "event_id": "evt-task-brief-lock-order",
            "task_id": task_id,
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "manual_dispatch",
            "message": "wake",
        }
        self.event_queue_path.write_text(
            json.dumps(event) + "\n",
            encoding="utf-8",
        )
        config = {
            **self.config,
            "agents": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                }
            },
            "providers": {"codex": {"delivery_mode": "codex"}},
        }
        state = {"queue": {"events": {}}, "workers": {}}
        brief_path = self.root / "task-brief.md"
        lock_trace_path = self.root / "lock-trace.jsonl"
        captured_requests: list[supervisor.DeliveryRequest] = []
        real_build_request = supervisor.build_request

        def build_request_under_task_lock(
            request_config: dict[str, object],
            request_event: dict[str, object],
            **kwargs: object,
        ) -> supervisor.DeliveryRequest:
            with supervisor.canonical_task_state_lock_file(
                self.status_path,
                shared=True,
                nonblocking=False,
            ):
                request = real_build_request(
                    request_config,
                    request_event,
                    **kwargs,
                )
            captured_requests.append(request)
            return request

        def process_queue_under_nested_runtime_lock(
            request_config: dict[str, object],
            **_kwargs: object,
        ) -> bool:
            with runtime_state.runtime_state_lock(
                request_config,
                shared=True,
                nonblocking=False,
            ):
                return supervisor.process_queue(request_config, state, {})

        with (
            mock.patch.object(
                supervisor,
                "_run_once_locked",
                side_effect=process_queue_under_nested_runtime_lock,
            ),
            mock.patch.object(
                supervisor,
                "build_request",
                side_effect=build_request_under_task_lock,
            ),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(supervisor, "select_dispatch_agent_id", return_value=None),
            mock.patch.object(common, "task_brief_path", return_value=brief_path),
            mock.patch.object(common, "write_activity_log") as common_activity_log,
            mock.patch.dict(
                os.environ,
                {"PANTHEON_RUNTIME_LOCK_TRACE": str(lock_trace_path)},
            ),
        ):
            changed = supervisor.run_once(config, watch=False)

        self.assertTrue(changed)
        self.assertEqual(len(captured_requests), 1)
        self.assertIn(str(brief_path), captured_requests[0].context_files)
        self.assertNotEqual(
            captured_requests[0].context_files,
            ["AI_COLLABORATION_GUIDE.md", "ai-status.json"],
        )
        self.assertTrue(brief_path.is_file())
        rendered = brief_path.read_text(encoding="utf-8")
        self.assertIn("# Task Brief: OPS-TASK-BRIEF-LOCK-ORDER-TEST", rendered)
        self.assertIn("- Status: todo", rendered)
        self.assertIn("- Owner: Codex", rendered)
        self.assertIn("- Reviewer: Codex2", rendered)
        self.assertIn("- Next: Use the complete task-scoped context", rendered)
        self.assertIn(
            "- OPS-TASK-BRIEF-ARCHIVED-DEPENDENCY: done · Archived dependency",
            rendered,
        )
        self.assertIn("## Artifacts\n- .orchestrator/common.py", rendered)
        common_activity_log.assert_not_called()
        lock_trace = [
            line.split(":", 3)[:2]
            for line in lock_trace_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [entry for entry in lock_trace if entry == ["acquire", "task_state"]],
            [["acquire", "task_state"]],
        )
        self.assertLess(
            lock_trace.index(["acquire", "runtime_admission"]),
            lock_trace.index(["acquire", "task_state"]),
        )

    def test_waiting_prune_recovers_after_queue_writer_is_killed_before_replace(self) -> None:
        original_events = [
            {
                "event_id": "evt-keep",
                "task_id": "KEEP",
                "status": "queued",
            },
            {
                "event_id": "evt-drop",
                "task_id": "DROP",
                "status": "completed",
            },
        ]
        self.event_queue_path.write_text(
            "".join(json.dumps(event) + "\n" for event in original_events),
            encoding="utf-8",
        )
        self.status_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {"id": "KEEP", "status": "in_progress", "owner": "Codex"},
                        {"id": "DROP", "status": "done", "owner": "Codex"},
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state = runtime_state.default_state()
        state["queue"]["events"] = {
            "evt-keep": {"status": "started"},
            "evt-drop": {"status": "completed"},
        }
        state["workers"] = {
            "run-keep": {
                "run_id": "run-keep",
                "task_id": "KEEP",
                "status": "running",
                "queue_event_id": "evt-keep",
            }
        }
        self.state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")

        context = multiprocessing.get_context("fork")
        writer_parent, writer_child = context.Pipe()
        writer = context.Process(
            target=_interrupt_queue_replace_before_switch,
            args=(
                self.config,
                [{"event_id": "evt-interrupted", "task_id": "TORN"}],
                writer_child,
            ),
        )
        writer.start()
        writer_child.close()
        self.addCleanup(writer_parent.close)
        self.addCleanup(self._stop_process, writer)

        self.assertTrue(writer_parent.poll(5), "queue writer did not reach atomic replace")
        self.assertEqual(writer_parent.recv()[0], "replace-ready")
        lock_path = runtime_state.runtime_admission_lock_path(self.config)
        lock_inode = lock_path.stat().st_ino
        self.assertEqual(runtime_state.load_jsonl(self.event_queue_path), original_events)

        prune_parent, prune_child = context.Pipe()
        prune = context.Process(
            target=_prune_queue_after_runtime_lock,
            args=(self.config, state, prune_child),
        )
        prune.start()
        prune_child.close()
        self.addCleanup(prune_parent.close)
        self.addCleanup(self._stop_process, prune)

        self.assertFalse(
            prune_parent.poll(0.25),
            "prune bypassed the runtime lock while replace was interrupted",
        )
        self.assertTrue(prune.is_alive())
        writer.kill()
        writer.join(timeout=5)
        self.assertEqual(writer.exitcode, -9)

        self.assertTrue(prune_parent.poll(5), "prune did not recover after writer SIGKILL")
        result = prune_parent.recv()
        self.assertEqual(result[0:2], ("completed", True))
        pruned_state = result[2]
        prune.join(timeout=5)
        self.assertEqual(prune.exitcode, 0)

        self.assertEqual(lock_path.stat().st_ino, lock_inode)
        self.assertEqual(runtime_state.load_jsonl(self.event_queue_path), [original_events[0]])
        self.assertEqual(set(pruned_state["queue"]["events"]), {"evt-keep"})

    def test_supervisor_runtime_writer_surface_uses_canonical_helpers(self) -> None:
        run_once_source = inspect.getsource(supervisor.run_once)
        locked_operation_source = inspect.getsource(
            supervisor._run_with_deferred_dispatch_status_syncs
        )
        locked_cycle_source = inspect.getsource(supervisor._run_once_locked)
        queue_writer_source = inspect.getsource(supervisor.save_event_queue)

        self.assertIn("_run_with_deferred_dispatch_status_syncs(", run_once_source)
        self.assertIn("lambda: _run_once_locked(", run_once_source)
        self.assertIn('"sync_github_bus"', run_once_source)
        self.assertNotIn('"sync_github_bus"', locked_cycle_source)
        self.assertIn(
            "prefetch_ownerless_merged_pr_snapshots",
            run_once_source,
        )
        self.assertNotIn(
            "_merged_pull_requests_for_branch",
            locked_cycle_source,
        )
        self.assertNotIn("probe_provider_reports", locked_cycle_source)
        self.assertIn("prefetch_task_state_shadow", run_once_source)
        self.assertNotIn("sync_task_state_shadow", locked_cycle_source)
        self.assertIn("_fetch_worker_base_ref", run_once_source)
        self.assertNotIn("_fetch_worker_base_ref", locked_cycle_source)
        self.assertIn(
            "with runtime_state_lock(config, shared=False",
            locked_operation_source,
        )
        self.assertIn(
            "for pid, expected_start_ticks in deferred_terminations",
            locked_operation_source,
        )
        self.assertIn(
            "execute_auto_commit_archive(config, action)",
            locked_operation_source,
        )
        self.assertNotIn(
            "execute_auto_commit_archive",
            locked_cycle_source,
        )
        self.assertEqual(
            queue_writer_source.count("replace_event_queue(config, events)"),
            1,
        )
        self.assertNotIn("write_text", queue_writer_source)
        self.assertNotIn("os.replace", queue_writer_source)


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

    def test_startup_recovery_does_not_preempt_still_live_worker(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1"]
        config["agents"]["codex1_1"] = {
            "id": "codex1_1",
            "display_name": "Codex",
            "dispatch_slot_for": "codex",
            "provider": "codex1-1",
        }
        worker = {
            "run_id": "recovered-run",
            "task_id": "LOOP-PROD-000",
            "agent_id": "codex1_1",
            "logical_agent_id": "codex",
            "status": "running",
            "request_snapshot": {"reason": "owned_ready_dispatch"},
        }
        state = {
            "supervisor": {
                "started_at": "2026-07-13T16:03:43Z",
                "last_successful_loop_at": None,
            },
            "queue": {"events": {}},
            "workers": {"recovered-run": worker},
        }
        task_map = {
            "LOOP-PROD-000": {
                "id": "LOOP-PROD-000",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Codex2",
                "depends_on": [],
            },
            "URGENT-REVIEW": {
                "id": "URGENT-REVIEW",
                "status": "review",
                "owner": "Claude",
                "reviewer": "Codex",
                "depends_on": [],
            },
        }

        with mock.patch.object(supervisor, "load_event_queue", return_value=[]):
            self.assertFalse(
                supervisor.higher_priority_ready_task_exists(
                    config,
                    worker,
                    task_map,
                    state,
                )
            )

        state["supervisor"]["last_successful_loop_at"] = "2026-07-13T16:04:30Z"
        with mock.patch.object(supervisor, "load_event_queue", return_value=[]):
            self.assertTrue(
                supervisor.higher_priority_ready_task_exists(
                    config,
                    worker,
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

        with mock.patch.object(supervisor, "start_worker_for_request") as start_worker, mock.patch.object(
            supervisor, "write_activity_log"
        ) as write_activity_log:
            changed = supervisor.process_queue(self.config, state, provider_report={})
            # A second pass must not re-log the same orphaned event.
            changed_again = supervisor.process_queue(self.config, state, provider_report={})

        # Orphaned wake never starts a worker, but it is no longer dropped silently.
        start_worker.assert_not_called()
        self.assertTrue(changed)
        self.assertFalse(changed_again)
        orphan_logs = [
            call.args[1]
            for call in write_activity_log.call_args_list
            if call.args[1].get("type") == "wake_orphaned"
        ]
        self.assertEqual(len(orphan_logs), 1)
        self.assertEqual(orphan_logs[0]["task_id"], "RW-05-artifact-compare")
        self.assertEqual(orphan_logs[0]["queue_event_id"], "coord-old")
        self.assertTrue(state["queue"]["events"]["coord-old"]["orphan_logged"])

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
            mock.patch.object(supervisor, "console_log") as console_log,
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertFalse(changed)
        self.assertEqual(supervisor.load_event_queue(self.config), [])
        self.assertTrue(
            any("max_concurrent_workers 2" in call.args[0] for call in console_log.call_args_list)
        )

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

    def test_persist_task_reassignment_rejects_catalog_assignment_drift(self) -> None:
        status_path = self.root / "ai-status.json"
        original = {
            "tasks": [
                {
                    "id": "L12-LOCKED-001",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Codex2",
                    "catalog_task_contract_sha256": "b" * 64,
                }
            ]
        }
        status_path.write_text(json.dumps(original), encoding="utf-8")

        with mock.patch.object(supervisor, "sync_status_pipeline") as sync:
            applied = supervisor.persist_task_reassignment(
                self.config,
                task_id="L12-LOCKED-001",
                new_owner="Claude",
                new_reviewer="Codex2",
                message="Helper claim must not rewrite a catalog assignment.",
            )

        self.assertFalse(applied)
        sync.assert_not_called()
        self.assertEqual(
            json.loads(status_path.read_text(encoding="utf-8")),
            original,
        )

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
    def test_approval_stage_auto_denies_pending_request_after_worker_exit(self) -> None:
        worker = {
            "run_id": "run-dead-approval",
            "task_id": "TASK-APPROVAL",
            "provider": "codex",
            "status": "waiting_approval",
            "pid": 1234,
            "deferred_action": "approval-1",
            "deferred_tool_use": {"name": "Bash"},
        }
        pending = [{"approval_id": "approval-1"}, {"approval_id": None}]
        with (
            mock.patch.object(supervisor, "worker_supports_approval_resume", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_approval_stage(
                {},
                {},
                worker,
                provider_report={},
                pending=pending,
                resolved=[],
                alive=False,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "failed")
        self.assertIsNone(worker["deferred_action"])
        self.assertIsNone(worker["deferred_tool_use"])
        resolve_approval.assert_called_once_with(
            {},
            "approval-1",
            decision="deny",
            note="Auto-denied because the worker exited before approval could be applied.",
            remember=False,
        )
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")
        finalize_queue_event_record.assert_called_once()

    def test_approval_stage_marks_live_pending_worker_and_queue_manual(self) -> None:
        worker = {
            "run_id": "run-live-approval",
            "task_id": "TASK-APPROVAL",
            "provider": "claude",
            "status": "running",
            "pid": 1234,
            "queue_event_id": "evt-approval",
        }
        state = {"queue": {"events": {"evt-approval": {"status": "started"}}}}
        pending = [{"approval_id": "approval-2", "created_at": "2026-07-20T06:00:00Z"}]
        with (
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            outcome = supervisor.poll_worker_approval_stage(
                {},
                state,
                worker,
                provider_report={},
                pending=pending,
                resolved=[],
                alive=True,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "waiting_approval")
        self.assertEqual(worker["deferred_action"], "approval-2")
        self.assertEqual(state["queue"]["events"]["evt-approval"]["status"], "manual_pending")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_waiting_approval")

    def test_approval_stage_resumes_allowed_claude_worker(self) -> None:
        worker = {
            "run_id": "run-resume",
            "task_id": "TASK-APPROVAL",
            "provider": "claude",
            "status": "suspended_approval",
        }
        resolved = [{"approval_id": "approval-3", "decision": "allow"}]
        resumed = {
            "command": ["claude", "--resume", "session-1"],
            "log_path": "/tmp/resume.log",
            "allowed_tools": ["Bash"],
        }
        with (
            mock.patch.object(supervisor, "_provider_uses_claude_cli", return_value=True),
            mock.patch.object(supervisor, "resume_claude_worker", return_value=resumed) as resume_claude_worker,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            outcome = supervisor.poll_worker_approval_stage(
                {},
                {},
                worker,
                provider_report={"providers": {}},
                pending=[],
                resolved=resolved,
                alive=False,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["last_approval_id"], "approval-3")
        resume_claude_worker.assert_called_once_with(
            {},
            worker,
            {"providers": {}},
            approval=resolved[0],
        )
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_resumed")

    def test_approval_stage_fails_denied_worker(self) -> None:
        worker = {
            "run_id": "run-denied",
            "task_id": "TASK-APPROVAL",
            "provider": "codex",
            "status": "waiting_approval",
        }
        resolved = [{"approval_id": "approval-4", "decision": "deny", "note": "operator denied"}]
        with (
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_approval_stage(
                {},
                {},
                worker,
                provider_report={},
                pending=[],
                resolved=resolved,
                alive=True,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(write_activity_log.call_args.args[1]["message"], "operator denied")
        finalize_queue_event_record.assert_called_once_with(
            {},
            {},
            worker,
            "failed",
            "operator denied",
        )

    def test_approval_stage_restores_live_worker_when_approval_state_disappears(self) -> None:
        worker = {
            "run_id": "run-restored",
            "task_id": "TASK-APPROVAL",
            "provider": "codex",
            "status": "waiting_approval",
            "deferred_action": "approval-missing",
            "deferred_tool_use": {"name": "Bash"},
            "last_approval_id": "approval-old",
        }

        outcome = supervisor.poll_worker_approval_stage(
            {},
            {},
            worker,
            provider_report={},
            pending=[],
            resolved=[],
            alive=True,
        )

        self.assertEqual(outcome, {"changed": True, "stop": False})
        self.assertEqual(worker["status"], "running")
        self.assertIsNone(worker["deferred_action"])
        self.assertIsNone(worker["deferred_tool_use"])
        self.assertIsNone(worker["last_approval_id"])

    def test_stall_stage_defers_dead_worker_to_failure_and_completion_stages(self) -> None:
        outcome = supervisor.poll_worker_stall_stage(
            {},
            {},
            {"run_id": "run-dead"},
            alive=False,
            last_event_advanced=False,
            process_activity_advanced=False,
            now=datetime.now(timezone.utc),
            stall_after=300,
        )

        self.assertEqual(outcome, {"changed": False, "stop": False})

    def test_stall_stage_restores_worker_after_observed_progress(self) -> None:
        worker = {
            "run_id": "run-recovered",
            "task_id": "TASK-STALL",
            "provider": "codex",
            "status": "stalled",
            "last_event_at": "2026-07-20T06:00:00Z",
        }
        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            outcome = supervisor.poll_worker_stall_stage(
                {},
                {},
                worker,
                alive=True,
                last_event_advanced=True,
                process_activity_advanced=False,
                now=datetime(2026, 7, 20, 6, 5, tzinfo=timezone.utc),
                stall_after=300,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "running")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_recovered")

    def test_stall_stage_marks_silent_live_worker_stalled(self) -> None:
        worker = {
            "run_id": "run-silent",
            "task_id": "TASK-STALL",
            "provider": "codex",
            "status": "running",
            "last_event_at": "2026-07-20T06:00:00Z",
        }
        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            outcome = supervisor.poll_worker_stall_stage(
                {},
                {},
                worker,
                alive=True,
                last_event_advanced=False,
                process_activity_advanced=False,
                now=datetime(2026, 7, 20, 6, 5, 1, tzinfo=timezone.utc),
                stall_after=300,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "stalled")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_stalled")

    def test_stall_stage_terminates_worker_after_extended_stall(self) -> None:
        worker = {
            "run_id": "run-extended-stall",
            "task_id": "TASK-STALL",
            "provider": "codex",
            "status": "stalled",
            "pid": 1234,
            "last_event_at": "2026-07-20T06:00:00Z",
        }
        with (
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_stall_stage(
                {},
                {},
                worker,
                alive=True,
                last_event_advanced=False,
                process_activity_advanced=False,
                now=datetime(2026, 7, 20, 6, 10, 1, tzinfo=timezone.utc),
                stall_after=300,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "failed")
        terminate_worker_pid.assert_called_once_with(1234)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")
        finalize_queue_event_record.assert_called_once()

    def test_failure_stage_falls_through_when_runner_succeeded(self) -> None:
        with mock.patch.object(supervisor, "worker_runner_succeeded", return_value=True):
            outcome = supervisor.poll_worker_failure_stage(
                {},
                {},
                {"run_id": "run-success", "status": "running"},
                provider_report={},
            )

        self.assertEqual(outcome, {"changed": False, "stop": False})

    def test_failure_stage_schedules_retry_after_model_rotation(self) -> None:
        worker = {
            "run_id": "run-rotate",
            "task_id": "TASK-FAILURE",
            "provider": "codex",
            "status": "running",
            "retry_count": 0,
        }
        with (
            mock.patch.object(supervisor, "worker_runner_succeeded", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value="rate limit"),
            mock.patch.object(
                supervisor,
                "classify_worker_failure",
                return_value={"kind": "capacity", "label": "capacity", "transient": True},
            ),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "capacity exhausted"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value="raw-ref"),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(supervisor, "worker_retry_settings", return_value={"max_attempts": 5}),
            mock.patch.object(supervisor, "maybe_rotate_provider_model", return_value="rotated"),
            mock.patch.object(
                supervisor,
                "decide_provider_failure_response",
                return_value=supervisor.rewrite_provider_health.FailureResponse.ROTATE,
            ),
            mock.patch.object(supervisor, "clear_task_failure_streaks_for_task") as clear_streak,
            mock.patch.object(supervisor, "schedule_worker_retry") as schedule_retry,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            outcome = supervisor.poll_worker_failure_stage(
                {},
                {},
                worker,
                provider_report={},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        clear_streak.assert_called_once_with({}, "TASK-FAILURE")
        schedule_retry.assert_called_once_with({}, worker, "capacity exhausted")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_retry_scheduled")

    def test_failure_stage_pauses_and_fails_terminal_quota_worker(self) -> None:
        worker = {
            "run_id": "run-quota",
            "task_id": "TASK-FAILURE",
            "provider": "codex",
            "status": "running",
        }
        with (
            mock.patch.object(supervisor, "worker_runner_succeeded", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value="quota exhausted"),
            mock.patch.object(
                supervisor,
                "classify_worker_failure",
                return_value={"kind": "quota_terminal", "label": "quota", "transient": False},
            ),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "quota summary"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value="raw-ref"),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=2),
            mock.patch.object(supervisor, "worker_retry_settings", return_value={"max_attempts": 0}),
            mock.patch.object(
                supervisor,
                "decide_provider_failure_response",
                return_value=supervisor.rewrite_provider_health.FailureResponse.PAUSE,
            ),
            mock.patch.object(supervisor, "mark_provider_dispatch_paused") as mark_paused,
            mock.patch.object(supervisor, "is_terminal_quota_failure_kind", return_value=True),
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_failure_stage(
                {},
                {},
                worker,
                provider_report={},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(worker["last_error"], "quota summary")
        mark_paused.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["raw_ref"], "raw-ref")
        self.assertEqual(finalize_queue_event_record.call_args.args[-1], "quota exhausted")

    def test_failure_stage_stops_after_retry_handler_accepts_failure(self) -> None:
        worker = {
            "run_id": "run-retry",
            "task_id": "TASK-FAILURE",
            "provider": "codex",
            "status": "running",
        }
        with (
            mock.patch.object(supervisor, "worker_runner_succeeded", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value="transient error"),
            mock.patch.object(
                supervisor,
                "classify_worker_failure",
                return_value={"kind": "transient", "label": "transient", "transient": True},
            ),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "retry me"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value="raw-ref"),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(supervisor, "worker_retry_settings", return_value={"max_attempts": 5}),
            mock.patch.object(supervisor, "maybe_rotate_provider_model", return_value="unchanged"),
            mock.patch.object(
                supervisor,
                "decide_provider_failure_response",
                return_value=supervisor.rewrite_provider_health.FailureResponse.RETRY,
            ),
            mock.patch.object(supervisor, "maybe_trigger_retry_or_fallback", return_value=(True, True)) as retry,
        ):
            outcome = supervisor.poll_worker_failure_stage(
                {},
                {},
                worker,
                provider_report={"providers": {}},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        retry.assert_called_once_with(
            {},
            {},
            {"providers": {}},
            worker,
            "transient error",
        )

    def test_completion_stage_keeps_existing_terminal_worker_unchanged(self) -> None:
        worker = {"run_id": "run-terminal", "status": "failed"}

        outcome = supervisor.poll_worker_completion_stage(
            {},
            {},
            worker,
            task_map={},
            redispatch_statuses={"todo"},
        )

        self.assertEqual(outcome, {"changed": False, "stop": True})
        self.assertEqual(worker["status"], "failed")

    def test_completion_stage_completes_chair_worker(self) -> None:
        worker = {
            "run_id": "run-chair",
            "task_id": None,
            "provider": "codex",
            "status": "running",
        }
        with (
            mock.patch.object(supervisor, "worker_is_discussion_planning", return_value=False),
            mock.patch.object(supervisor, "worker_is_coordination_dispatch", return_value=False),
            mock.patch.object(supervisor, "worker_is_chair_review", return_value=True),
            mock.patch.object(supervisor, "clear_task_failure_streak") as clear_streak,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_completion_stage(
                {},
                {},
                worker,
                task_map={},
                redispatch_statuses={"todo"},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "completed")
        clear_streak.assert_called_once()
        self.assertIn("Chair review worker exited", write_activity_log.call_args.args[1]["message"])
        finalize_queue_event_record.assert_called_once_with({}, {}, worker, "completed")

    def test_completion_stage_completes_worker_when_task_is_terminal(self) -> None:
        worker = {
            "run_id": "run-done",
            "task_id": "TASK-DONE",
            "provider": "codex",
            "status": "running",
        }
        config = {"ready_dispatcher": {"worker_terminal_statuses": ["done"]}}
        with (
            mock.patch.object(supervisor, "worker_is_discussion_planning", return_value=False),
            mock.patch.object(supervisor, "worker_is_coordination_dispatch", return_value=False),
            mock.patch.object(supervisor, "worker_is_chair_review", return_value=False),
            mock.patch.object(supervisor, "clear_task_failure_streak") as clear_streak,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_completion_stage(
                config,
                {},
                worker,
                task_map={"TASK-DONE": {"status": "done"}},
                redispatch_statuses={"todo"},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "completed")
        clear_streak.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_completed")
        finalize_queue_event_record.assert_called_once()

    def test_completion_stage_fails_generic_exit_below_reassign_threshold(self) -> None:
        worker = {
            "run_id": "run-generic-exit",
            "task_id": "TASK-TODO",
            "provider": "codex",
            "status": "running",
        }
        with (
            mock.patch.object(supervisor, "worker_is_discussion_planning", return_value=False),
            mock.patch.object(supervisor, "worker_is_coordination_dispatch", return_value=False),
            mock.patch.object(supervisor, "worker_is_chair_review", return_value=False),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(
                supervisor,
                "provider_guardrail_settings",
                return_value={"generic_exit_reassign_after": 2},
            ),
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure") as reassign,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_completion_stage(
                {},
                {},
                worker,
                task_map={"TASK-TODO": {"status": "todo"}},
                redispatch_statuses={"todo"},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "failed")
        reassign.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")
        finalize_queue_event_record.assert_called_once()

    def test_completion_stage_reassigns_repeated_generic_exit(self) -> None:
        worker = {
            "run_id": "run-generic-reassign",
            "task_id": "TASK-TODO",
            "provider": "codex",
            "status": "running",
        }
        with (
            mock.patch.object(supervisor, "worker_is_discussion_planning", return_value=False),
            mock.patch.object(supervisor, "worker_is_coordination_dispatch", return_value=False),
            mock.patch.object(supervisor, "worker_is_chair_review", return_value=False),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=2),
            mock.patch.object(
                supervisor,
                "provider_guardrail_settings",
                return_value={"generic_exit_reassign_after": 2},
            ),
            mock.patch.object(
                supervisor,
                "maybe_reassign_task_after_worker_failure",
                return_value="claude",
            ) as reassign,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_completion_stage(
                {},
                {},
                worker,
                task_map={"TASK-TODO": {"status": "todo"}},
                redispatch_statuses={"todo"},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "reassigned")
        self.assertEqual(worker["reassigned_to"], "claude")
        reassign.assert_called_once()
        finalize_queue_event_record.assert_called_once_with({}, {}, worker, "completed")

    def test_completion_stage_blocks_prepared_head_without_handoff(self) -> None:
        """SUP-PROVIDER-POOL-PROBE-GATE-001.

        An owner run that pushed the exact review head and then exited 0 without
        advancing the task used to be recorded as a generic worker exit. The task
        stayed `in_progress` with the same owner, so the next tick reissued
        `owned_in_progress_dispatch` and the run reproduced the identical clean
        exit -- a token loop instead of reviewer dispatch.
        """
        worker = {
            "run_id": "run-prepared-head",
            "task_id": "TASK-PREPARED",
            "provider": "claude2",
            "agent_id": "claude2",
            "status": "running",
            "pr_url": "https://github.com/ajoe734/pantheon/pull/4270",
            "pr_url_source": "result_payload",
            "request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS},
        }
        state: dict[str, object] = {}
        with (
            mock.patch.object(supervisor, "worker_is_discussion_planning", return_value=False),
            mock.patch.object(supervisor, "worker_is_coordination_dispatch", return_value=False),
            mock.patch.object(supervisor, "worker_is_chair_review", return_value=False),
            mock.patch.object(
                supervisor,
                "record_missing_handoff_blocker",
                return_value={"task_id": "TASK-PREPARED", "message": "blocked"},
            ) as record_blocker,
            mock.patch.object(supervisor, "record_task_failure_streak") as record_streak,
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure") as reassign,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_completion_stage(
                {},
                state,
                worker,
                task_map={"TASK-PREPARED": {"status": "in_progress"}},
                redispatch_statuses={"in_progress"},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(worker["last_error"], supervisor.MISSING_HANDOFF_EXIT_REASON)
        record_blocker.assert_called_once()
        # No generic-exit streak and no owner reassignment: this shape is a
        # missing handoff, not a provider failure.
        record_streak.assert_not_called()
        reassign.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")
        finalize_queue_event_record.assert_called_once_with(
            {}, state, worker, "failed", supervisor.MISSING_HANDOFF_EXIT_REASON
        )

    def test_completion_stage_keeps_generic_exit_when_no_head_was_prepared(self) -> None:
        """An owner exit with no pushed PR head is still a plain generic exit."""
        worker = {
            "run_id": "run-no-head",
            "task_id": "TASK-PREPARED",
            "provider": "claude2",
            "agent_id": "claude2",
            "status": "running",
            "request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS},
        }
        with (
            mock.patch.object(supervisor, "worker_is_discussion_planning", return_value=False),
            mock.patch.object(supervisor, "worker_is_coordination_dispatch", return_value=False),
            mock.patch.object(supervisor, "worker_is_chair_review", return_value=False),
            mock.patch.object(supervisor, "record_missing_handoff_blocker") as record_blocker,
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1) as record_streak,
            mock.patch.object(
                supervisor,
                "provider_guardrail_settings",
                return_value={"generic_exit_reassign_after": 2},
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "finalize_queue_event_record"),
        ):
            outcome = supervisor.poll_worker_completion_stage(
                {},
                {},
                worker,
                task_map={"TASK-PREPARED": {"status": "in_progress"}},
                redispatch_statuses={"in_progress"},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        record_blocker.assert_not_called()
        record_streak.assert_called_once()
        self.assertEqual(worker["last_error"], supervisor.GENERIC_WORKER_EXIT_REASON)

    def test_completion_stage_ignores_mention_only_pr_url(self) -> None:
        """A PR URL scraped from logs is audit data, not prepared-head proof."""
        worker = {
            "run_id": "run-mentioned-pr",
            "task_id": "TASK-PREPARED",
            "provider": "claude2",
            "agent_id": "claude2",
            "status": "running",
            "pr_url": "https://github.com/ajoe734/pantheon/pull/4270",
            "pr_url_source": "log_scrape",
            "request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS},
        }
        with (
            mock.patch.object(supervisor, "worker_is_discussion_planning", return_value=False),
            mock.patch.object(supervisor, "worker_is_coordination_dispatch", return_value=False),
            mock.patch.object(supervisor, "worker_is_chair_review", return_value=False),
            mock.patch.object(supervisor, "record_missing_handoff_blocker") as record_blocker,
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1) as record_streak,
            mock.patch.object(
                supervisor,
                "provider_guardrail_settings",
                return_value={"generic_exit_reassign_after": 2},
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "finalize_queue_event_record"),
        ):
            outcome = supervisor.poll_worker_completion_stage(
                {},
                {},
                worker,
                task_map={"TASK-PREPARED": {"status": "in_progress"}},
                redispatch_statuses={"in_progress"},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        record_blocker.assert_not_called()
        record_streak.assert_called_once()
        self.assertEqual(worker["last_error"], supervisor.GENERIC_WORKER_EXIT_REASON)

    def test_completion_stage_does_not_block_finalize_exit(self) -> None:
        """A review-approved finalize run can exit while waiting for auto-merge."""
        worker = {
            "run_id": "run-finalize-pr",
            "task_id": "TASK-PREPARED",
            "provider": "claude2",
            "agent_id": "claude2",
            "status": "running",
            "pr_url": "https://github.com/ajoe734/pantheon/pull/4270",
            "pr_url_source": "result_payload",
            "request_snapshot": {"reason": supervisor.REASON_OWNED_FINALIZE},
        }
        with (
            mock.patch.object(supervisor, "worker_is_discussion_planning", return_value=False),
            mock.patch.object(supervisor, "worker_is_coordination_dispatch", return_value=False),
            mock.patch.object(supervisor, "worker_is_chair_review", return_value=False),
            mock.patch.object(supervisor, "record_missing_handoff_blocker") as record_blocker,
            mock.patch.object(supervisor, "record_task_failure_streak") as record_streak,
            mock.patch.object(supervisor, "clear_task_failure_streak") as clear_streak,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_completion_stage(
                {},
                {},
                worker,
                task_map={"TASK-PREPARED": {"status": "review_approved"}},
                redispatch_statuses={"review_approved"},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "completed")
        record_blocker.assert_not_called()
        record_streak.assert_not_called()
        clear_streak.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_completed")
        finalize_queue_event_record.assert_called_once_with({}, {}, worker, "completed")

    def test_worker_prepared_review_head_requires_an_owner_dispatch(self) -> None:
        base = {
            "pr_url": "https://github.com/ajoe734/pantheon/pull/4270",
            "pr_url_source": "result_payload",
        }
        for reason in (
            supervisor.REASON_OWNED_READY,
            supervisor.REASON_OWNED_IN_PROGRESS,
        ):
            with self.subTest(reason=reason):
                self.assertTrue(
                    supervisor.worker_prepared_review_head({**base, "request_snapshot": {"reason": reason}})
                )
        self.assertFalse(
            supervisor.worker_prepared_review_head(
                {**base, "request_snapshot": {"reason": supervisor.REASON_OWNED_FINALIZE}}
            )
        )
        # A reviewer run that happens to cite a PR is not a missing owner handoff.
        self.assertFalse(
            supervisor.worker_prepared_review_head(
                {**base, "request_snapshot": {"reason": "review_ready_dispatch"}}
            )
        )
        # A prose-scraped PR URL is also not prepared-head proof.
        self.assertFalse(
            supervisor.worker_prepared_review_head(
                {
                    "pr_url": "https://github.com/ajoe734/pantheon/pull/4270",
                    "pr_url_source": "log_scrape",
                    "request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS},
                }
            )
        )
        self.assertFalse(
            supervisor.worker_prepared_review_head(
                {"request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS}}
            )
        )

    def test_orphan_stage_reaps_dead_worker_after_queue_event_disappears(self) -> None:
        worker = {
            "run_id": "run-orphan",
            "task_id": "TASK-ORPHAN",
            "provider": "codex",
            "status": "running",
            "queue_event_id": "evt-missing",
            "pid": 1234,
        }
        state = {"workers": {"run-orphan": worker}}
        with (
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            outcome = supervisor.poll_worker_orphan_stage(
                {},
                state,
                worker,
                run_id="run-orphan",
                valid_queue_event_ids=set(),
                task_map={"TASK-ORPHAN": {"status": "todo"}},
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertNotIn("run-orphan", state["workers"])
        self.assertIn("redispatched", write_activity_log.call_args.args[1]["message"])

    def test_assignment_stage_completes_lingering_chair_worker(self) -> None:
        worker = {
            "run_id": "run-chair-live",
            "task_id": None,
            "provider": "codex",
            "status": "running",
            "pid": 1234,
        }
        with (
            mock.patch.object(supervisor, "chair_review_worker_artifacts_applied", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "clear_task_failure_streak") as clear_streak,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_assignment_stage(
                {},
                {},
                worker,
                run_id="run-chair-live",
                provider_report={},
                task_map={},
                active_worker_statuses={"running"},
                alive=True,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "completed")
        terminate_worker_pid.assert_called_once_with(1234)
        clear_streak.assert_called_once()
        self.assertIn("artifacts were accepted", write_activity_log.call_args.args[1]["message"])
        finalize_queue_event_record.assert_called_once()

    def test_assignment_stage_requeues_stale_manual_inbox(self) -> None:
        worker = {
            "run_id": "run-manual",
            "task_id": "TASK-MANUAL",
            "provider": "claude",
            "status": "manual_pending",
        }
        with (
            mock.patch.object(supervisor, "chair_review_worker_artifacts_applied", return_value=False),
            mock.patch.object(supervisor, "manual_pending_inbox_can_auto_redeliver", return_value=True),
            mock.patch.object(supervisor, "requeue_stale_manual_pending_worker", return_value=True) as requeue,
        ):
            outcome = supervisor.poll_worker_assignment_stage(
                {},
                {},
                worker,
                run_id="run-manual",
                provider_report={"agent_adapters": {}},
                task_map={},
                active_worker_statuses={"running"},
                alive=False,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        requeue.assert_called_once()

    def test_assignment_stage_supersedes_worker_after_owner_moves(self) -> None:
        worker = {
            "run_id": "run-moved",
            "task_id": "TASK-MOVED",
            "provider": "codex",
            "status": "running",
            "queue_event_id": "evt-moved",
            "pid": 1234,
        }
        with (
            mock.patch.object(supervisor, "chair_review_worker_artifacts_applied", return_value=False),
            mock.patch.object(supervisor, "manual_pending_inbox_can_auto_redeliver", return_value=False),
            mock.patch.object(supervisor, "worker_matches_current_assignment", return_value=False),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_assignment_stage(
                {},
                {},
                worker,
                run_id="run-moved",
                provider_report={},
                task_map={"TASK-MOVED": {"owner": "Claude"}},
                active_worker_statuses={"running"},
                alive=True,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "superseded")
        terminate_worker_pid.assert_called_once_with(1234)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")
        finalize_queue_event_record.assert_called_once()

    def test_assignment_stage_preempts_for_higher_priority_ready_task(self) -> None:
        worker = {
            "run_id": "run-preempted",
            "task_id": "TASK-LOW",
            "provider": "codex",
            "status": "running",
            "queue_event_id": "evt-low",
            "pid": 1234,
        }
        with (
            mock.patch.object(supervisor, "chair_review_worker_artifacts_applied", return_value=False),
            mock.patch.object(supervisor, "manual_pending_inbox_can_auto_redeliver", return_value=False),
            mock.patch.object(supervisor, "worker_matches_current_assignment", return_value=True),
            mock.patch.object(supervisor, "higher_priority_ready_task_exists", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "sync_preempted_task_status") as sync_preempted,
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_assignment_stage(
                {},
                {},
                worker,
                run_id="run-preempted",
                provider_report={},
                task_map={"TASK-LOW": {"status": "in_progress"}},
                active_worker_statuses={"running"},
                alive=True,
            )

        self.assertEqual(outcome, {"changed": True, "stop": True})
        self.assertEqual(worker["status"], "superseded")
        self.assertIn("higher-priority", worker["last_error"])
        terminate_worker_pid.assert_called_once_with(1234)
        sync_preempted.assert_called_once_with({}, worker)
        finalize_queue_event_record.assert_called_once()

    def test_progress_bound_lease_expires_with_fresh_heartbeat(self) -> None:
        now = datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc)
        config = {
            "supervisor": {"lease_requires_work_progress": True},
            "worker_runtime": {"work_progress_stale_seconds": 360},
        }
        worker = {
            "last_heartbeat_at": now.isoformat(),
            "last_event_at": (now - timedelta(seconds=700)).isoformat(),
            "lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
        }

        self.assertFalse(supervisor.worker_heartbeat_is_stale(config, worker, now))
        self.assertFalse(supervisor.worker_lease_can_renew(config, worker, now))
        self.assertTrue(supervisor.worker_lease_is_expired(config, worker, now))

    def test_progress_bound_lease_renews_after_recent_work_progress(self) -> None:
        now = datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc)
        config = {
            "supervisor": {"lease_requires_work_progress": True},
            "worker_runtime": {"work_progress_stale_seconds": 360},
        }
        worker = {
            "last_heartbeat_at": now.isoformat(),
            "last_event_at": (now - timedelta(seconds=700)).isoformat(),
            "last_work_progress_at": (now - timedelta(seconds=60)).isoformat(),
            "lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
        }

        self.assertTrue(supervisor.worker_lease_can_renew(config, worker, now))
        self.assertFalse(supervisor.worker_lease_is_expired(config, worker, now))

    def test_legacy_lease_mode_still_requires_stale_heartbeat_to_expire(self) -> None:
        now = datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc)
        config = {
            "supervisor": {"lease_requires_work_progress": False},
            "worker_runtime": {
                "heartbeat_stale_seconds": 300,
                "heartbeat_grace_seconds": 60,
            },
        }
        worker = {
            "last_heartbeat_at": now.isoformat(),
            "last_event_at": (now - timedelta(seconds=700)).isoformat(),
            "lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
        }

        self.assertFalse(supervisor.worker_lease_is_expired(config, worker, now))
        worker["last_heartbeat_at"] = (now - timedelta(seconds=361)).isoformat()
        self.assertTrue(supervisor.worker_lease_is_expired(config, worker, now))

    def test_progress_bound_lease_is_the_default(self) -> None:
        self.assertTrue(supervisor.worker_lease_requires_work_progress({}))

    def test_observation_stage_refreshes_fresh_worker_lease(self) -> None:
        now = datetime.now(timezone.utc)
        worker = {
            "run_id": "run-lease",
            "status": "running",
            "pid": 1234,
            "last_heartbeat_at": now.isoformat(),
            "queue_event_id": "evt-lease",
        }
        state = {"queue": {"events": {"evt-lease": {"status": "started"}}}}
        counts = {
            "marker_updates": 0,
            "commit_progress_updates": 0,
            "lease_refreshes": 0,
            "expired_lease_workers_failed": 0,
        }
        with (
            mock.patch.object(supervisor, "update_worker_runtime_markers", return_value=False),
            mock.patch.object(supervisor, "update_from_log"),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_worker_commit_progress", return_value=(False, False)),
            mock.patch.object(supervisor, "worker_process_activity_snapshot", return_value={}),
            mock.patch.object(supervisor, "worker_heartbeat_is_stale", return_value=False),
            mock.patch.object(supervisor, "refresh_worker_lease") as refresh_worker_lease,
            mock.patch.object(supervisor, "worker_lease_is_expired", return_value=False),
            mock.patch.object(supervisor, "queue_lease_expiry", return_value="2026-07-20T06:00:00Z"),
        ):
            outcome = supervisor.poll_worker_observation_stage(
                {"supervisor": {"adaptive_stall_detection": False}},
                state,
                worker,
                now=now,
                active_worker_statuses={"running"},
                poll_counts=counts,
            )

        self.assertFalse(outcome["stop"])
        self.assertTrue(outcome["alive"])
        self.assertEqual(counts["lease_refreshes"], 1)
        self.assertEqual(state["queue"]["events"]["evt-lease"]["lease_owner"], "run-lease")
        refresh_worker_lease.assert_called_once_with(
            {"supervisor": {"adaptive_stall_detection": False}},
            worker,
            now,
        )

    def test_observation_stage_expires_stale_progress_with_fresh_heartbeat(self) -> None:
        now = datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc)
        config = {
            "supervisor": {
                "adaptive_stall_detection": False,
                "observe_worker_commit_progress": False,
                "lease_requires_work_progress": True,
            },
            "worker_runtime": {"work_progress_stale_seconds": 360},
        }
        worker = {
            "run_id": "run-progress-expired",
            "task_id": "TASK-PROGRESS-EXPIRED",
            "provider": "codex",
            "status": "running",
            "pid": 1234,
            "last_heartbeat_at": now.isoformat(),
            "last_event_at": (now - timedelta(seconds=700)).isoformat(),
            "lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
        }
        counts = {
            "marker_updates": 0,
            "commit_progress_updates": 0,
            "lease_refreshes": 0,
            "expired_lease_workers_failed": 0,
        }
        with (
            mock.patch.object(supervisor, "update_worker_runtime_markers", return_value=False),
            mock.patch.object(supervisor, "update_from_log"),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "refresh_worker_lease") as refresh_worker_lease,
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "pause_dispatch_for_reaped_worker", return_value=None),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_observation_stage(
                config,
                {},
                worker,
                now=now,
                active_worker_statuses={"running"},
                poll_counts=counts,
            )

        self.assertTrue(outcome["changed"])
        self.assertTrue(outcome["stop"])
        self.assertEqual(worker["status"], "failed")
        self.assertIn("work progress became stale", worker["last_error"])
        self.assertEqual(counts["lease_refreshes"], 0)
        self.assertEqual(counts["expired_lease_workers_failed"], 1)
        refresh_worker_lease.assert_not_called()
        terminate_worker_pid.assert_called_once_with(1234)
        finalize_queue_event_record.assert_called_once()

    def test_observation_stage_stops_after_expired_lease(self) -> None:
        now = datetime.now(timezone.utc)
        config = {"supervisor": {"adaptive_stall_detection": False}}
        worker = {
            "run_id": "run-expired",
            "task_id": "TASK-EXPIRED",
            "provider": "codex",
            "status": "running",
            "pid": 1234,
        }
        counts = {
            "marker_updates": 0,
            "commit_progress_updates": 0,
            "lease_refreshes": 0,
            "expired_lease_workers_failed": 0,
        }
        with (
            mock.patch.object(supervisor, "update_worker_runtime_markers", return_value=False),
            mock.patch.object(supervisor, "update_from_log"),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_worker_commit_progress", return_value=(False, False)),
            mock.patch.object(supervisor, "worker_lease_is_expired", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "pause_dispatch_for_reaped_worker", return_value=None),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "finalize_queue_event_record") as finalize_queue_event_record,
        ):
            outcome = supervisor.poll_worker_observation_stage(
                config,
                {},
                worker,
                now=now,
                active_worker_statuses={"running"},
                poll_counts=counts,
            )

        self.assertTrue(outcome["changed"])
        self.assertTrue(outcome["stop"])
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(counts["expired_lease_workers_failed"], 1)
        terminate_worker_pid.assert_called_once_with(1234)
        finalize_queue_event_record.assert_called_once()

    def test_commit_progress_ignores_shared_workspace(self) -> None:
        with mock.patch.object(supervisor.subprocess, "run") as run:
            sha = supervisor.isolated_workspace_commit_sha(
                "shared_root",
                "/home/lupin/pantheon",
            )

        self.assertIsNone(sha)
        run.assert_not_called()

    def test_commit_progress_reads_real_isolated_worktree_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "worker"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "baseline",
                ],
                check=True,
            )
            expected = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            observed = supervisor.isolated_workspace_commit_sha(
                "isolated_worktree",
                repo,
            )

        self.assertEqual(observed, expected)

    def test_commit_progress_records_new_isolated_head(self) -> None:
        now = datetime(2026, 7, 20, 5, 20, tzinfo=timezone.utc)
        worker = {
            "workspace_mode": "isolated_worktree",
            "workspace_path": "/tmp/task-worktree",
            "work_progress_snapshot": {"commit_sha": "a" * 40},
            "commit_progress_count": 0,
        }
        with mock.patch.object(
            supervisor,
            "isolated_workspace_commit_sha",
            return_value="b" * 40,
        ):
            state_changed, progress_advanced = supervisor.update_worker_commit_progress(
                worker,
                now,
            )

        self.assertTrue(state_changed)
        self.assertTrue(progress_advanced)
        self.assertEqual(worker["work_progress_snapshot"]["commit_sha"], "b" * 40)
        self.assertEqual(worker["last_commit_progress_at"], "2026-07-20T05:20:00Z")
        self.assertEqual(worker["last_work_progress_at"], "2026-07-20T05:20:00Z")
        self.assertEqual(worker["commit_progress_count"], 1)

    def test_first_observation_is_baseline_not_manufactured_progress(self) -> None:
        worker = {
            "workspace_mode": "isolated_worktree",
            "workspace_path": "/tmp/old-worker-worktree",
        }
        with mock.patch.object(
            supervisor,
            "isolated_workspace_commit_sha",
            return_value="c" * 40,
        ):
            state_changed, progress_advanced = supervisor.update_worker_commit_progress(
                worker,
                datetime.now(timezone.utc),
            )

        self.assertTrue(state_changed)
        self.assertFalse(progress_advanced)
        self.assertNotIn("last_commit_progress_at", worker)

    def test_poll_workers_wires_commit_progress_into_stall_signal(self) -> None:
        now = datetime.now(timezone.utc)
        old_event = (now - timedelta(seconds=301)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        fresh_heartbeat = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {
                "stall_after_seconds": 300,
                "adaptive_stall_detection": False,
                "observe_worker_commit_progress": True,
            },
            "worker_runtime": {"heartbeat_stale_seconds": 300, "worker_lease_seconds": 1800},
            "ready_dispatcher": {"active_worker_statuses": ["running", "stalled"]},
            "providers": {},
            "agents": {"codex": {"id": "codex", "display_name": "Codex"}},
        }
        worker = {
            "run_id": "run-commit",
            "task_id": "TEST-COMMIT-001",
            "provider": "codex",
            "agent_id": "codex",
            "status": "running",
            "queue_event_id": "evt-commit",
            "pid": 1234,
            "last_event_at": old_event,
            "last_heartbeat_at": fresh_heartbeat,
            "workspace_mode": "isolated_worktree",
            "workspace_path": "/tmp/task-worktree",
            "work_progress_snapshot": {"commit_sha": "a" * 40},
        }
        state = {
            "queue": {"events": {"evt-commit": {"status": "started"}}},
            "workers": {"run-commit": worker},
        }
        status = {
            "tasks": [
                {
                    "id": "TEST-COMMIT-001",
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=lambda *_args, **_kwargs: None),
            mock.patch.object(supervisor, "isolated_workspace_commit_sha", return_value="b" * 40),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(worker["status"], "running")
        self.assertEqual(worker["work_progress_snapshot"]["commit_sha"], "b" * 40)
        self.assertEqual(worker["commit_progress_count"], 1)
        self.assertIsNotNone(worker.get("last_work_progress_at"))
        terminate_worker_pid.assert_not_called()

    def test_process_activity_snapshot_walks_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proc_root = Path(tmpdir)

            def write_process(pid: int, parent: int, children: list[int], cpu: tuple[int, int], io_bytes: tuple[int, int], command: str) -> None:
                process_root = proc_root / str(pid)
                task_root = process_root / "task" / str(pid)
                task_root.mkdir(parents=True)
                task_root.joinpath("children").write_text(" ".join(str(child) for child in children), encoding="utf-8")
                fields = ["S", str(parent), "0", "0", "0", "0", "0", "0", "0", "0", "0", str(cpu[0]), str(cpu[1]), "0", "0", "0", "0", "1", "0", str(pid * 10)]
                process_root.joinpath("stat").write_text(f"{pid} ({command}) " + " ".join(fields), encoding="utf-8")
                process_root.joinpath("io").write_text(
                    f"read_bytes: {io_bytes[0]}\nwrite_bytes: {io_bytes[1]}\n",
                    encoding="utf-8",
                )
                process_root.joinpath("comm").write_text(f"{command}\n", encoding="utf-8")

            write_process(100, 1, [101], (1, 1), (1, 1), "runner")
            write_process(101, 100, [102], (10, 5), (100, 50), "agy")
            write_process(102, 101, [], (20, 10), (200, 100), "pytest")

            snapshot = supervisor.worker_process_activity_snapshot(100, proc_root)

        self.assertEqual(snapshot["processes"], ["101:1010", "102:1020"])
        self.assertEqual(snapshot["cpu_ticks"], 45)
        self.assertEqual(snapshot["io_bytes"], 450)
        self.assertEqual(snapshot["commands"], ["agy", "pytest"])

    def test_process_activity_advance_requires_measurable_progress(self) -> None:
        baseline = {
            "processes": ["101:500"],
            "cpu_ticks": 100,
            "io_bytes": 200,
            "commands": ["pytest"],
        }
        self.assertTrue(
            supervisor.worker_process_activity_advanced(
                baseline,
                {**baseline, "cpu_ticks": 101},
            )
        )
        self.assertTrue(
            supervisor.worker_process_activity_advanced(
                baseline,
                {**baseline, "io_bytes": 201},
            )
        )
        self.assertTrue(
            supervisor.worker_process_activity_advanced(
                baseline,
                {**baseline, "processes": ["102:600"]},
            )
        )
        self.assertFalse(supervisor.worker_process_activity_advanced(baseline, dict(baseline)))
        self.assertFalse(supervisor.worker_process_activity_advanced(None, baseline))

    def test_active_process_tree_defers_stall_despite_silent_provider_log(self) -> None:
        now = datetime.now(timezone.utc)
        old_event = (now - timedelta(seconds=700)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        fresh_heartbeat = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        config = {
            "schema": {"tasks_path": "tasks", "task_id_field": "id", "assignee_field": "owner", "reviewer_field": "reviewer"},
            "supervisor": {"stall_after_seconds": 300, "adaptive_stall_detection": True},
            "worker_runtime": {"heartbeat_stale_seconds": 300, "worker_lease_seconds": 1800},
            "ready_dispatcher": {"active_worker_statuses": ["running", "stalled"]},
            "providers": {},
            "agents": {"antigravity": {"id": "antigravity", "display_name": "Antigravity"}},
        }
        baseline = {"processes": ["101:500"], "cpu_ticks": 100, "io_bytes": 200, "commands": ["pytest"]}
        current = {**baseline, "cpu_ticks": 140, "io_bytes": 240}
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "TEST-001",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                    "last_event_at": old_event,
                    "last_heartbeat_at": fresh_heartbeat,
                    "process_activity_snapshot": baseline,
                }
            },
        }
        status = {"tasks": [{"id": "TEST-001", "status": "in_progress", "owner": "Antigravity", "reviewer": "Claude"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=lambda *_args, **_kwargs: None),
            mock.patch.object(supervisor, "worker_process_activity_snapshot", return_value=current),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "running")
        self.assertEqual(worker["process_activity_snapshot"], current)
        self.assertIsNotNone(worker.get("last_process_activity_at"))
        terminate_worker_pid.assert_not_called()
        self.assertIn(
            "worker_stall_deferred",
            [call.args[1].get("type") for call in write_activity_log.call_args_list],
        )

    def test_silent_worker_without_process_progress_is_marked_stalled(self) -> None:
        now = datetime.now(timezone.utc)
        old_event = (now - timedelta(seconds=301)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        fresh_heartbeat = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        config = {
            "schema": {"tasks_path": "tasks", "task_id_field": "id", "assignee_field": "owner", "reviewer_field": "reviewer"},
            "supervisor": {"stall_after_seconds": 300, "adaptive_stall_detection": True},
            "worker_runtime": {"heartbeat_stale_seconds": 300, "worker_lease_seconds": 1800},
            "ready_dispatcher": {"active_worker_statuses": ["running", "stalled"]},
            "providers": {},
            "agents": {"antigravity": {"id": "antigravity", "display_name": "Antigravity"}},
        }
        snapshot = {"processes": ["101:500"], "cpu_ticks": 100, "io_bytes": 200, "commands": ["agy"]}
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "TEST-002",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                    "last_event_at": old_event,
                    "last_heartbeat_at": fresh_heartbeat,
                    "process_activity_snapshot": snapshot,
                }
            },
        }
        status = {"tasks": [{"id": "TEST-002", "status": "in_progress", "owner": "Antigravity", "reviewer": "Claude"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=lambda *_args, **_kwargs: None),
            mock.patch.object(supervisor, "worker_process_activity_snapshot", return_value=dict(snapshot)),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "stalled")
        terminate_worker_pid.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_stalled")

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
                mock.patch.object(supervisor, "cleanup_inactive_worker_worktrees", return_value=True) as cleanup_worktrees,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                changed = supervisor.poll_workers(config, state)

            self.assertTrue(changed)
            self.assertEqual(state["workers"]["chair-run"]["status"], "completed")
            self.assertEqual(state["queue"]["events"]["evt-chair"]["status"], "completed")
            mark_provider_dispatch_paused.assert_not_called()
            cleanup_worktrees.assert_called_once_with(config, state)
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

    def test_stale_pruned_suspended_approval_fails_worker_for_cooldown_bounded_redispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task = {
                "id": "OPS-STALE-APPROVAL",
                "status": "in_progress",
                "owner": "Claude",
                "reviewer": "Codex",
                "depends_on": [],
                "last_update": "2026-07-18T00:00:00Z",
            }
            config = {
                "paths": {
                    "approval_queue": str(root / "approval-queue.json"),
                    "state_file": str(root / "state.json"),
                    "event_queue": str(root / "event-queue.jsonl"),
                    "activity_log": str(root / "activity-log.jsonl"),
                    "evidence_dir": str(root / "evidence"),
                    "status_file": str(root / "ai-status.json"),
                },
                "schema": {
                    "tasks_path": "tasks",
                    "task_id_field": "id",
                    "assignee_field": "owner",
                    "reviewer_field": "reviewer",
                },
                "approvals": {"stale_pending_seconds": 1800},
                "supervisor": {"stall_after_seconds": 300},
                "ready_dispatcher": {
                    "enabled": True,
                    "review_statuses": ["review"],
                    "finalize_statuses": ["review_approved"],
                    "owned_statuses": ["todo", "in_progress"],
                    "dependency_done_statuses": ["done"],
                    "active_worker_statuses": [
                        "running",
                        "waiting_approval",
                        "suspended_approval",
                        "manual_pending",
                        "retry_backoff",
                        "stalled",
                    ],
                    "max_dispatches_per_tick": 1,
                    "max_tasks_per_agent": 1,
                    "unchanged_task_cooldown_seconds": 900,
                },
                "providers": {"claude": {"delivery_mode": "claude_cli"}},
                "agents": {
                    "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                    "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                },
            }
            event = supervisor.build_dispatch_event(
                task,
                "Claude",
                "owned_in_progress_dispatch",
                {"OPS-STALE-APPROVAL": task},
            )
            event["event_id"] = "evt-stale-approval"
            event["created_at"] = "2026-07-18T00:00:00Z"
            state = {
                "queue": {
                    "events": {
                        event["event_id"]: {
                            "status": "manual_pending",
                            "event_key": event["key"],
                            "run_id": "run-stale-approval",
                        }
                    }
                },
                "workers": {
                    "run-stale-approval": {
                        "run_id": "run-stale-approval",
                        "task_id": "OPS-STALE-APPROVAL",
                        "provider": "claude",
                        "agent_id": "claude",
                        "status": "suspended_approval",
                        "queue_event_id": event["event_id"],
                        "pid": 999999,
                        "session_id": "sess-stale",
                        "resume_token": "sess-stale",
                        "last_event_at": "2026-07-18T00:00:00Z",
                    }
                },
            }
            stale_created_at = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat().replace("+00:00", "Z")
            (root / "approval-queue.json").write_text(
                json.dumps(
                    {
                        "pending": [
                            {
                                "approval_id": "apr-stale-approval",
                                "status": "pending",
                                "created_at": stale_created_at,
                                "provider": "claude",
                                "task_id": "OPS-STALE-APPROVAL",
                                "worker_run_id": "run-stale-approval",
                                "tool_name": "Agent",
                            }
                        ],
                        "history": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            (root / "ai-status.json").write_text(json.dumps({"tasks": [task]}, indent=2) + "\n", encoding="utf-8")
            (root / "event-queue.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

            pruned = supervisor.prune_stale_approvals(config)

            self.assertEqual([item["approval_id"] for item in pruned], ["apr-stale-approval"])
            self.assertEqual(pruned[0]["decision"], "deny")

            with (
                mock.patch.object(supervisor, "load_provider_report", return_value={}),
                mock.patch.object(supervisor, "retry_due_workers", return_value=False),
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                changed = supervisor.poll_workers(config, state, provider_report={})

            self.assertTrue(changed)
            worker = state["workers"]["run-stale-approval"]
            self.assertEqual(worker["status"], "failed")
            self.assertEqual(state["queue"]["events"][event["event_id"]]["status"], "failed")
            self.assertIn(event["key"], state.get("seen_event_keys", {}))
            self.assertEqual(state.get("provider_guardrails", {}).get("task_failure_streaks", {}), {})

            self.assertTrue(supervisor.prune_event_queue(config, state))
            self.assertEqual(state["queue"]["events"], {})
            self.assertEqual((root / "event-queue.jsonl").read_text(encoding="utf-8"), "")

            queued: list[dict[str, object]] = []
            with mock.patch.object(
                supervisor,
                "queue_delivery_event",
                side_effect=lambda _config, queued_event: queued.append(queued_event) or True,
            ):
                self.assertFalse(
                    supervisor.dispatch_ready_tasks(
                        config,
                        state,
                        provider_report={},
                        agent_ids_override=["claude"],
                        max_dispatches_override=1,
                    )
                )
            self.assertEqual(queued, [])

            config["ready_dispatcher"]["unchanged_task_cooldown_seconds"] = 0
            with mock.patch.object(
                supervisor,
                "queue_delivery_event",
                side_effect=lambda _config, queued_event: queued.append(queued_event) or True,
            ):
                self.assertTrue(
                    supervisor.dispatch_ready_tasks(
                        config,
                        state,
                        provider_report={},
                        agent_ids_override=["claude"],
                        max_dispatches_override=1,
                    )
                )
            self.assertEqual(queued[-1]["reason"], "owned_in_progress_dispatch")

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

    def test_status_root_consistency_gate_fail_fast(self) -> None:
        """Verify that when PANTHEON_STATUS_ROOT environment variable does not match
        the config-derived status root, check_status_root_consistency raises SystemExit."""
        config = {"paths": {"state_file": "/tmp/test-worktree/.orchestrator/state.json"}}
        # When environment has a mismatched status root, it should fail-fast
        with (
            mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": "/home/lupin/code/pantheon"}),
            self.assertRaises(SystemExit) as cm
        ):
            supervisor.check_status_root_consistency(config, allow_isolated=False)
        self.assertEqual(cm.exception.code, 1)

        # Bypassed when --allow-isolated-status-root is set
        try:
            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": "/home/lupin/code/pantheon"}):
                supervisor.check_status_root_consistency(config, allow_isolated=True)
        except SystemExit:
            self.fail("check_status_root_consistency exited unexpectedly when allow_isolated=True")

        # Bypassed when env variable is not set or empty
        try:
            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": ""}):
                supervisor.check_status_root_consistency(config, allow_isolated=False)
        except SystemExit:
            self.fail("check_status_root_consistency exited unexpectedly when env is empty")

    def test_multiple_supervisors_same_status_root_collision(self) -> None:
        """Verify that two supervisors pointing to the same status root (but different state file folders / cwds)
        will collide on the authoritative status root lock."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            status_root = Path(tmp1)
            # Setup configs for two instances sharing same status_root
            config1 = {
                "paths": {
                    "status_file": str(status_root / "ai-status.json"),
                    "state_file": str(Path(tmp1) / ".orchestrator" / "state.json")
                }
            }
            config2 = {
                "paths": {
                    "status_file": str(status_root / "ai-status.json"),
                    "state_file": str(Path(tmp2) / ".orchestrator" / "state.json")
                }
            }

            # Acquire first lock
            self.assertTrue(supervisor.acquire_singleton_lock(config1))
            first_handle = supervisor._SINGLETON_LOCK_HANDLE
            self.assertIsNotNone(first_handle)

            # Second contender fails to acquire
            self.assertFalse(supervisor.acquire_singleton_lock(config2))

            # Clean up first lock
            first_handle.close()
            supervisor._SINGLETON_LOCK_HANDLE = None

            # Now second contender succeeds
            self.assertTrue(supervisor.acquire_singleton_lock(config2))
            second_handle = supervisor._SINGLETON_LOCK_HANDLE
            self.addCleanup(second_handle.close)


class WorktreeDirtClassificationTests(unittest.TestCase):
    def _init_git_repo(self, tmpdir: str) -> Path:
        repo = Path(tmpdir) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
        return repo

    def _commit_all(self, repo: Path, message: str) -> None:
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)

    def test_worker_base_fetch_updates_origin_dev_with_master_only_refspec(self) -> None:
        """An explicit branch fetch must update the ref freshness checks consume."""

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            origin = root / "origin.git"
            source = root / "source"
            consumer = root / "consumer"
            subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
            source.mkdir()
            consumer.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "master"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=source, check=True)
            (source / "tracked.txt").write_text("initial\n", encoding="utf-8")
            self._commit_all(source, "initial")
            initial_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=source, check=True)
            subprocess.run(["git", "push", "-q", "origin", "master"], cwd=source, check=True)
            subprocess.run(["git", "checkout", "-q", "-b", "dev"], cwd=source, check=True)
            subprocess.run(["git", "push", "-q", "origin", "dev"], cwd=source, check=True)

            subprocess.run(["git", "init", "-q"], cwd=consumer, check=True)
            subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=consumer, check=True)
            subprocess.run(
                [
                    "git",
                    "config",
                    "remote.origin.fetch",
                    "+refs/heads/master:refs/remotes/origin/master",
                ],
                cwd=consumer,
                check=True,
            )
            subprocess.run(["git", "fetch", "-q", "origin", "master"], cwd=consumer, check=True)
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/dev", initial_sha],
                cwd=consumer,
                check=True,
            )

            (source / "tracked.txt").write_text("advanced\n", encoding="utf-8")
            self._commit_all(source, "advance dev")
            advanced_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            subprocess.run(["git", "push", "-q", "origin", "dev"], cwd=source, check=True)

            # This is the live failure shape: FETCH_HEAD advances, but the
            # master-only configured refspec leaves origin/dev untouched.
            subprocess.run(["git", "fetch", "-q", "origin", "dev"], cwd=consumer, check=True)
            stale_sha = subprocess.run(
                ["git", "rev-parse", "origin/dev"],
                cwd=consumer,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(stale_sha, initial_sha)

            fetched, error = supervisor._fetch_worker_base_ref(consumer, "origin/dev")

            self.assertTrue(fetched, error)
            refreshed_sha = subprocess.run(
                ["git", "rev-parse", "origin/dev"],
                cwd=consumer,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(refreshed_sha, advanced_sha)

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

    def test_index_split_matching_head_is_restorable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(tmpdir)
            tracked = repo / "tracked.txt"
            created = repo / "created.txt"

            tracked.write_text("old\n", encoding="utf-8")
            self._commit_all(repo, "initial")
            tracked.write_text("new\n", encoding="utf-8")
            created.write_text("created\n", encoding="utf-8")
            self._commit_all(repo, "head content")

            # Simulate the stale reused-worker split: index stages reverse dirt,
            # while the worktree already contains the HEAD bytes.
            tracked.write_text("old\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            tracked.write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "rm", "--cached", "-q", "created.txt"], cwd=repo, check=True)

            paths = supervisor._staged_index_split_paths_matching_head(repo)
            self.assertEqual(set(paths), {"tracked.txt", "created.txt"})
            self.assertTrue(supervisor._restore_reused_index_split(repo, paths))

            status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(status.stdout, "")

    def test_index_split_helper_rejects_real_staged_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(tmpdir)
            tracked = repo / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            self._commit_all(repo, "initial")

            tracked.write_text("changed\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            self.assertEqual(supervisor._staged_index_split_paths_matching_head(repo), [])

    def test_index_split_helper_rejects_staged_additions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(tmpdir)
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            self._commit_all(repo, "initial")

            (repo / "new.txt").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "add", "new.txt"], cwd=repo, check=True)
            self.assertEqual(supervisor._staged_index_split_paths_matching_head(repo), [])


    def test_refresh_reused_worktree_anchors_unstaged_real_dirt(self) -> None:
        # Regression: a reused worktree with plain *unstaged* real dirt (no staged
        # index-split) must auto-anchor, not hard-block. The original gate
        # early-returned skipped_dirty_worktree before the anchor could run, so a
        # superseded run that left modified-but-unstaged task files jammed the
        # owning agent forever (MPOS-P1-VERIFY-001 incident).
        with tempfile.TemporaryDirectory() as tmpdir:
            origin = Path(tmpdir) / "origin.git"
            subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
            repo = self._init_git_repo(tmpdir)
            subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True)
            (repo / "svc.py").write_text("base\n", encoding="utf-8")
            self._commit_all(repo, "initial")
            subprocess.run(["git", "branch", "-M", "dev"], cwd=repo, check=True)
            subprocess.run(["git", "push", "-q", "origin", "dev"], cwd=repo, check=True)
            subprocess.run(["git", "checkout", "-q", "-b", "task/OPS-GATEFIX-001"], cwd=repo, check=True)
            # Modified-but-unstaged real change (the common superseded-run residue).
            (repo / "svc.py").write_text("unstaged worker WIP\n", encoding="utf-8")
            fetched, fetch_error = supervisor._fetch_worker_base_ref(repo, "origin/dev")
            self.assertTrue(fetched, fetch_error)

            ok, detail = supervisor._refresh_reused_worker_worktree(
                repo, repo, "origin/dev",
                task_id="OPS-GATEFIX-001", branch="task/OPS-GATEFIX-001",
            )

            self.assertTrue(ok, detail)
            self.assertTrue(detail.startswith("autoanchored_"), detail)
            status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
            )
            self.assertEqual(status.stdout, "")
            body = subprocess.run(
                ["git", "log", "-1", "--format=%B"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout
            self.assertIn("Task-ID: OPS-GATEFIX-001", body)

    def test_anchor_commit_task_wip_commits_real_dirt_on_task_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(tmpdir)
            (repo / "svc.py").write_text("base\n", encoding="utf-8")
            self._commit_all(repo, "initial")
            subprocess.run(["git", "checkout", "-q", "-b", "task/OPS-ANCHOR-001"], cwd=repo, check=True)
            # Real task WIP a superseded run left uncommitted.
            (repo / "svc.py").write_text("worker WIP\n", encoding="utf-8")
            (repo / "new_module.py").write_text("created by worker\n", encoding="utf-8")

            ok, detail = supervisor._anchor_commit_task_wip(repo, "OPS-ANCHOR-001", "task/OPS-ANCHOR-001")

            self.assertTrue(ok, detail)
            # Worktree is now clean (the dirty-tree lease block precondition).
            status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
            )
            self.assertEqual(status.stdout, "")
            # The WIP is preserved as a commit carrying the required trailers.
            body = subprocess.run(
                ["git", "log", "-1", "--format=%B"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout
            self.assertTrue(body.startswith("OPS-ANCHOR-001: anchor"))
            self.assertIn("LLM-Agent: supervisor", body)
            self.assertIn("Task-ID: OPS-ANCHOR-001", body)
            self.assertIn("Reviewer: local", body)

    def test_anchor_commit_task_wip_refuses_when_branch_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(tmpdir)
            (repo / "svc.py").write_text("base\n", encoding="utf-8")
            self._commit_all(repo, "initial")
            subprocess.run(["git", "checkout", "-q", "-b", "task/OTHER-999"], cwd=repo, check=True)
            (repo / "svc.py").write_text("dirty\n", encoding="utf-8")
            head_before = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()

            ok, detail = supervisor._anchor_commit_task_wip(repo, "OPS-ANCHOR-001", "task/OPS-ANCHOR-001")

            self.assertFalse(ok)
            self.assertTrue(detail.startswith("branch_mismatch"), detail)
            # No commit was made on the wrong branch; dirt is left for the caller to block.
            head_after = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()
            self.assertEqual(head_before, head_after)


class CachedProviderCapabilityLoopTests(unittest.TestCase):
    """SUP-PROVIDER-POOL-PROBE-GATE-001 acceptance 4.

    `run_once` calls `probe_provider_reports` before every loop. While that path
    forced the Codex probe, an intended telemetry refresh re-ran `codex exec`
    (and, per alias, `agy --prompt`) on every supervisor tick regardless of
    `provider_auth.probe_interval_seconds`.
    """

    @staticmethod
    def _is_provider_cli_smoke(command: list[str]) -> bool:
        if len(command) >= 2 and Path(command[0]).name.startswith("codex") and command[1] == "exec":
            return True
        return bool(Path(command[0]).name.startswith("agy") and "--prompt" in command)

    def test_loop_report_reuses_cached_probes_when_none_are_due(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex-home"
            codex_home.mkdir()
            (codex_home / "auth.json").write_text(
                '{"tokens":{"access_token":"redacted","refresh_token":"redacted"}}',
                encoding="utf-8",
            )
            agy_home = root / "agy-home"
            (agy_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
            (agy_home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").write_text(
                "token", encoding="utf-8"
            )
            stub_bin = root / "bin"
            stub_bin.mkdir()
            codex_cli = stub_bin / "codex"
            codex_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            codex_cli.chmod(0o755)
            agy_cli = stub_bin / "agy"
            agy_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            agy_cli.chmod(0o755)
            recent = (
                datetime.now(timezone.utc) - timedelta(seconds=60)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

            def cached(provider_id: str, kind: str) -> dict:
                return {
                    "provider": provider_id,
                    "kind": kind,
                    "ready": True,
                    "status": "ready",
                    "method": "cached",
                    "error": None,
                    "checked_at": recent,
                    "last_auth_probe_at": recent,
                    "source": "live",
                }

            capabilities = root / "provider-capabilities.json"
            capabilities.write_text(
                json.dumps(
                    {
                        "providers": {
                            "codex": {"auth_ready": True, "auth_probe": cached("codex", "codex")},
                            "codex2": {"auth_ready": True, "auth_probe": cached("codex2", "codex")},
                            "antigravity": {
                                "auth_ready": True,
                                "auth_probe": cached("antigravity", "antigravity"),
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            config = {
                "paths": {
                    "status_file": str(root / "ai-status.json"),
                    "activity_log": str(root / "ai-activity-log.jsonl"),
                    "current_work": str(root / "current-work.md"),
                    "dashboard": str(root / "dashboard-bundle.json"),
                    "claude_mcp_config": str(root / "claude-approval-broker.mcp.json"),
                    "provider_capabilities": str(capabilities),
                },
                "provider_auth": {"probe_interval_seconds": 900},
                "agents": {},
                "providers": {
                    "codex": {
                        "delivery_mode": "codex",
                        "codex": {"cli": str(codex_cli), "codex_home": str(codex_home)},
                    },
                    "codex2": {
                        "delivery_mode": "codex",
                        "codex": {"cli": str(codex_cli), "codex_home": str(codex_home)},
                    },
                    "antigravity": {
                        "delivery_mode": "antigravity",
                        "antigravity": {"cli": str(agy_cli), "home": str(agy_home)},
                    },
                },
            }

            commands: list[list[str]] = []

            def spy(command, **_kwargs):
                commands.append(list(command))
                return subprocess.CompletedProcess(command, 1, "", "")

            with mock.patch.object(provider_permissions, "run_command", side_effect=spy):
                _previous, report = supervisor.probe_provider_reports(config, quiet=True)

        smokes = [command for command in commands if self._is_provider_cli_smoke(command)]
        self.assertEqual(smokes, [], f"unexpected provider CLI smoke probes: {smokes}")
        for provider_id in ("codex", "codex2", "antigravity"):
            self.assertEqual(
                report["providers"][provider_id]["auth_probe"]["source"],
                "cached",
                provider_id,
            )
            self.assertTrue(report["providers"][provider_id]["auth_ready"], provider_id)


class WorkerBaseRefPreconditionTests(unittest.TestCase):
    """SUP-PROVIDER-POOL-PROBE-GATE-001.

    The per-loop `_PREFETCHED_WORKER_BASE_REFS` context proved that *this* cycle
    fetched the base. After a provider probe, a worker failure, a redispatch, or
    a split-root restart, a dispatch can cross into a cycle whose context never
    listed the base even though `origin/dev` resolves fine. Treating the missing
    flag as proof of a missing fetch stalled the scheduler with
    `base_ref_not_prefetched:origin/dev`. The invariant is the git ref.
    """

    def _origin_backed_repo(self, tmpdir: str) -> Path:
        root = Path(tmpdir)
        origin = root / "origin.git"
        repo = root / "repo"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
        subprocess.run(["git", "push", "-q", "origin", "dev"], cwd=repo, check=True)
        subprocess.run(
            ["git", "fetch", "-q", "origin", "+refs/heads/dev:refs/remotes/origin/dev"],
            cwd=repo,
            check=True,
        )
        return repo

    def test_prefetched_context_is_still_the_fast_path(self) -> None:
        token = supervisor._PREFETCHED_WORKER_BASE_REFS.set(frozenset({"origin/dev"}))
        try:
            with mock.patch.object(supervisor, "_fetch_worker_base_ref") as fetch:
                self.assertEqual(
                    supervisor._worker_base_ref_precondition("origin/dev", Path("/nonexistent")),
                    (True, None),
                )
            fetch.assert_not_called()
        finally:
            supervisor._PREFETCHED_WORKER_BASE_REFS.reset(token)

    def test_empty_context_accepts_a_resolvable_freshly_fetched_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._origin_backed_repo(tmpdir)
            token = supervisor._PREFETCHED_WORKER_BASE_REFS.set(frozenset())
            try:
                ready, error = supervisor._worker_base_ref_precondition("origin/dev", repo)
                self.assertTrue(ready, error)
                self.assertIsNone(error)
                # The recovered ref is cached for the rest of the cycle.
                self.assertIn("origin/dev", supervisor._PREFETCHED_WORKER_BASE_REFS.get())
            finally:
                supervisor._PREFETCHED_WORKER_BASE_REFS.reset(token)

    def test_worktree_creation_succeeds_across_a_redispatch_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._origin_backed_repo(tmpdir)
            worktree = Path(tmpdir) / "leases" / "task-redispatch"
            token = supervisor._PREFETCHED_WORKER_BASE_REFS.set(frozenset())
            try:
                created, error = supervisor._create_worker_worktree(
                    repo, worktree, "task/OPS-REDISPATCH-001", "origin/dev"
                )
            finally:
                supervisor._PREFETCHED_WORKER_BASE_REFS.reset(token)

            self.assertTrue(created, error)
            self.assertTrue((worktree / "tracked.txt").exists())

    def test_recovery_fetch_is_time_bounded_inside_the_cycle(self) -> None:
        """The pre-admission fetch runs outside every lock; this one does not.

        An unbounded recovery fetch would charge its network wait to the
        runtime-admission hold that approve/assign/note commands queue behind.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._origin_backed_repo(tmpdir)
            token = supervisor._PREFETCHED_WORKER_BASE_REFS.set(frozenset())
            try:
                with mock.patch.object(
                    supervisor, "_fetch_worker_base_ref", return_value=(True, None)
                ) as fetch:
                    ready, error = supervisor._worker_base_ref_precondition("origin/dev", repo)
            finally:
                supervisor._PREFETCHED_WORKER_BASE_REFS.reset(token)

        self.assertTrue(ready, error)
        self.assertEqual(
            fetch.call_args.kwargs["timeout_seconds"],
            supervisor.WORKER_BASE_REF_RECOVERY_FETCH_TIMEOUT_SECONDS,
        )

    def test_a_hung_recovery_fetch_still_accepts_an_already_resolving_ref(self) -> None:
        """A fetch timeout is not proof the ref is missing; only rev-parse is."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._origin_backed_repo(tmpdir)
            token = supervisor._PREFETCHED_WORKER_BASE_REFS.set(frozenset())
            try:
                with mock.patch.object(
                    supervisor,
                    "_fetch_worker_base_ref",
                    return_value=(False, "git fetch timed out after 30s"),
                ):
                    ready, error = supervisor._worker_base_ref_precondition("origin/dev", repo)
            finally:
                supervisor._PREFETCHED_WORKER_BASE_REFS.reset(token)

        self.assertTrue(ready, error)

    def test_unresolvable_base_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._origin_backed_repo(tmpdir)
            token = supervisor._PREFETCHED_WORKER_BASE_REFS.set(frozenset())
            try:
                ready, error = supervisor._worker_base_ref_precondition("origin/no-such-branch", repo)
            finally:
                supervisor._PREFETCHED_WORKER_BASE_REFS.reset(token)

            self.assertFalse(ready)
            self.assertTrue(str(error).startswith("base_ref_unresolved:origin/no-such-branch"), error)

    def test_missing_base_ref_name_fails_closed(self) -> None:
        token = supervisor._PREFETCHED_WORKER_BASE_REFS.set(frozenset())
        try:
            self.assertEqual(
                supervisor._worker_base_ref_precondition("", Path("/nonexistent")),
                (False, "base_ref_not_prefetched:missing"),
            )
        finally:
            supervisor._PREFETCHED_WORKER_BASE_REFS.reset(token)

    def test_standalone_maintenance_outside_a_cycle_is_unaffected(self) -> None:
        self.assertEqual(
            supervisor._worker_base_ref_precondition("origin/dev"),
            (True, None),
        )


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

    def test_failure_streak_sweep_reassigns_claude_weekly_limit_review(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reviewer_fallbacks": {
                    "Claude": ["Codex", "Codex2"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude", "provider": "claude"},
                "codex": {"display_name": "Codex", "provider": "codex"},
                "codex2": {"display_name": "Codex2", "provider": "codex2"},
            },
        }
        state = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "MGMT-AI-LIVE-BRIDGE-SMOKE-20260607101732:claude": {
                        "task_id": "MGMT-AI-LIVE-BRIDGE-SMOKE-20260607101732",
                        "provider": "claude",
                        "count": 2,
                        "last_failure_kind": "terminal",
                        "last_reason": "rate_limit: You've hit your weekly limit · resets Jun 8, 12pm (UTC)",
                    }
                }
            }
        }
        status = {
            "tasks": [
                {
                    "id": "MGMT-AI-LIVE-BRIDGE-SMOKE-20260607101732",
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
            changed = supervisor.maybe_reassign_tasks_from_failure_streaks(config, state)

        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Codex")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Codex2")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"], {})

    def test_failure_streak_sweep_waits_for_threshold(self) -> None:
        config = {
            "worker_reassignment": {"enabled": True, "after_attempts": 2},
            "agents": {
                "claude": {"display_name": "Claude", "provider": "claude"},
                "codex": {"display_name": "Codex", "provider": "codex"},
            },
        }
        state = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "P3-004:claude": {
                        "task_id": "P3-004",
                        "provider": "claude",
                        "count": 1,
                        "last_failure_kind": "terminal",
                        "last_reason": "rate_limit: You've hit your weekly limit · resets Jun 8, 12pm (UTC)",
                    }
                }
            }
        }

        with mock.patch.object(supervisor, "persist_task_reassignment") as persist:
            changed = supervisor.maybe_reassign_tasks_from_failure_streaks(config, state)

        self.assertFalse(changed)
        persist.assert_not_called()

    def test_failure_streak_sweep_reassigns_first_terminal_quota_failure(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "owner_fallbacks": {"Codex2": ["Antigravity", "Claude", "Codex"]},
                "reviewer_fallbacks": {"Codex2": ["Codex", "Claude"]},
                "eligible_statuses": ["todo", "in_progress", "review", "review_approved"],
            },
            "agents": {
                "codex2": {"display_name": "Codex2", "provider": "codex2"},
                "codex2_1": {
                    "display_name": "Codex2",
                    "provider": "codex2-1",
                    "dispatch_slot_for": "codex2",
                },
                "antigravity": {"display_name": "Antigravity", "provider": "antigravity"},
                "claude": {"display_name": "Claude", "provider": "claude"},
                "codex": {"display_name": "Codex", "provider": "codex"},
            },
        }
        state = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "OPS-QUOTA-001:codex2_1": {
                        "task_id": "OPS-QUOTA-001",
                        "provider": "codex2_1",
                        "count": 1,
                        "last_failure_kind": "quota_terminal",
                        "last_reason": "You've hit your usage limit",
                    }
                }
            }
        }
        status = {
            "tasks": [
                {
                    "id": "OPS-QUOTA-001",
                    "status": "in_progress",
                    "owner": "Codex2",
                    "reviewer": "Codex",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.maybe_reassign_tasks_from_failure_streaks(config, state)

        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Antigravity")
        self.assertEqual(persist.call_args.kwargs["new_status"], "todo")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"], {})

    def test_failure_streaks_aggregate_dispatch_slots_by_logical_agent(self) -> None:
        state: dict = {}
        worker_one = {
            "task_id": "OPS-CHURN-001",
            "provider": "codex1-1",
            "request_snapshot": {"metadata": {"logical_agent_id": "codex"}},
        }
        worker_two = {
            "task_id": "OPS-CHURN-001",
            "provider": "codex1-2",
            "request_snapshot": {"metadata": {"logical_agent_id": "codex"}},
        }

        self.assertEqual(
            supervisor.record_task_failure_streak(state, worker_one, "no progress", failure_kind="generic_exit"),
            1,
        )
        self.assertEqual(
            supervisor.record_task_failure_streak(state, worker_two, "no progress", failure_kind="generic_exit"),
            2,
        )
        self.assertEqual(
            list(state["provider_guardrails"]["task_failure_streaks"]),
            ["OPS-CHURN-001:codex"],
        )

        supervisor.clear_task_failure_streak(state, worker=worker_two)
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"], {})

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


class MissingHandoffBlockerTests(unittest.TestCase):
    """SUP-PROVIDER-POOL-PROBE-GATE-001 acceptance 7."""

    def setUp(self) -> None:
        self.config = {
            "paths": {"status_file": "ai-status.json"},
            "agents": {"claude2": {"id": "claude2", "display_name": "Claude2"}},
        }
        self.worker = {
            "run_id": "claude2-run-9",
            "task_id": "SUP-LOOP-001",
            "agent_id": "claude2",
            "provider": "claude2",
            "pr_url": "https://github.com/ajoe734/pantheon/pull/4270",
            "request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS},
        }

    def _status(self) -> dict:
        return {
            "tasks": [
                {
                    "id": "SUP-LOOP-001",
                    "status": "in_progress",
                    "owner": "Claude2",
                    "reviewer": "Codex2",
                }
            ]
        }

    def test_blocker_takes_the_task_out_of_owned_in_progress_dispatch(self) -> None:
        status = self._status()
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_status") as write_status,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-07-27T19:00:00Z"),
        ):
            event = supervisor.record_missing_handoff_blocker(self.config, self.worker)

        self.assertIsNotNone(event)
        task = status["tasks"][0]
        # `in_progress` is what owned_in_progress_dispatch selects on; leaving it
        # there is exactly the loop. `blocked` is not an owned dispatch status.
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(task["waiting_for"], "Codex2")
        blocker = status["blockers"][0]
        self.assertEqual(blocker["blocker_kind"], "missing_handoff")
        self.assertEqual(blocker["status"], "open")
        self.assertEqual(blocker["waiting_for"], "Codex2")
        self.assertIn("pull/4270", blocker["message"])
        write_status.assert_called_once_with(
            self.config, status, source="supervisor-missing-handoff"
        )
        self.assertEqual(
            status["status_activity_outbox"]["events"][0]["type"],
            "task_missing_handoff_blocked",
        )
        self.assertEqual(
            write_activity_log.call_args.args[1]["type"], "task_missing_handoff_blocked"
        )

    def test_blocker_is_not_duplicated_on_a_later_tick(self) -> None:
        status = self._status()
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_status"),
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-07-27T19:00:00Z"),
        ):
            self.assertIsNotNone(supervisor.record_missing_handoff_blocker(self.config, self.worker))
            status.pop("status_activity_outbox", None)
            self.assertIsNone(supervisor.record_missing_handoff_blocker(self.config, self.worker))

        self.assertEqual(len(status["blockers"]), 1)

    def test_blocker_is_skipped_when_the_owner_no_longer_owns_the_task(self) -> None:
        status = self._status()
        status["tasks"][0]["owner"] = "Codex"
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_status") as write_status,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertIsNone(supervisor.record_missing_handoff_blocker(self.config, self.worker))

        write_status.assert_not_called()
        self.assertEqual(status["tasks"][0]["status"], "in_progress")


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
            mock.patch.object(supervisor, "write_status") as write_status,
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
        write_status.assert_called_once_with(config, status, source="supervisor-preempt")
        self.assertEqual(
            status["status_activity_outbox"]["events"][0]["type"],
            "task_preempted_synced",
        )
        write_activity_log.assert_not_called()

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
            mock.patch.object(supervisor, "write_status") as write_status,
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
        write_status.assert_called_once_with(config, status, source="supervisor-preempt")
        self.assertEqual(
            status["status_activity_outbox"]["events"][0]["type"],
            "task_preempted_synced",
        )
        write_activity_log.assert_not_called()

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
                111: "python3 .orchestrator/worker_runner.py --run-id run-1 --heartbeat-path h1 --status-path s1 -- codex exec -C /tmp/wt 你的 auto worker 身分是：Codex 。 Task ID: T1",
                222: "python3 .orchestrator/worker_runner.py --run-id run-2 --heartbeat-path h2 --status-path s2 -- codex exec -C /tmp/wt2 你的 auto worker 身分是：Codex2 。 Task ID: T2",
                333: "python3 .orchestrator/worker_runner.py --run-id run-3 --heartbeat-path h3 --status-path s3 -- codex exec -C /tmp/wt3 你的 auto worker 身分是：Codex 。 Task ID: T3",
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
            {os.getpid(): "--run-id run-1 -- auto worker 身分是：Codex"}
        )
        self.assertEqual(supervisor.scan_live_worker_pids_by_agent(proc_root=proc), {})

    def test_scan_dedupes_one_run_worth_of_wrapper_and_child_pids(self) -> None:
        # A single worker run spawns ~3 processes sharing the same wakeup
        # prompt in their cmdline: the worker_runner.py wrapper, a node CLI
        # shim, and the real CLI binary underneath it. Only the wrapper's
        # cmdline names worker_runner.py, so only it should be counted --
        # otherwise live_total is ~3x actual worker runs
        # (OPS-DISPATCH-PIDCOUNT-001).
        proc = self._make_fake_proc(
            {
                111: "python3 .orchestrator/worker_runner.py --run-id run-abc --heartbeat-path h --status-path s -- claude --print 你的 auto worker 身分是：Claude 。 Task ID: T1",
                222: "claude --print 你的 auto worker 身分是：Claude 。 Task ID: T1",
                333: "node /opt/claude/cli.js --print 你的 auto worker 身分是：Claude 。 Task ID: T1",
            }
        )
        result = supervisor.scan_live_worker_pids_by_agent(proc_root=proc)
        self.assertEqual(result["Claude"], [111])
        self.assertEqual(sum(len(pids) for pids in result.values()), 1)

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

    def test_block_reason_blocks_auth_down_provider(self) -> None:
        config = {
            "agents": {"codex2": {"provider": "codex2"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        provider_report = {"providers": {"codex2": {"auth_ready": False}}}
        with (
            mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False),
            mock.patch.object(supervisor, "scan_live_worker_pids_by_agent") as scan,
        ):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config,
                {},
                "codex2",
                provider_report,
            )

        self.assertEqual(reason, "codex2 authentication is not ready")
        scan.assert_not_called()

    def test_pre_dispatch_probe_revokes_selected_owner_before_launch(self) -> None:
        config = {
            "agents": {"codex2": {"provider": "codex2"}},
            "providers": {"codex2": {"delivery_mode": "codex"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": False},
        }
        provider_report = {
            "providers": {
                "codex2": {
                    "auth_ready": True,
                    "local_cli_worker_supported": True,
                    "supports_auto_approve": True,
                }
            }
        }
        probe = {
            "provider": "codex2",
            "ready": False,
            "status": "refresh_token_revoked",
            "method": "codex_exec_oauth",
            "error": "refresh token revoked",
            "checked_at": "2026-07-20T00:00:00Z",
        }
        with mock.patch.object(supervisor, "probe_provider_auth", return_value=probe) as auth_probe:
            health = supervisor.refresh_provider_auth_before_dispatch(
                config,
                provider_report,
                "codex2",
            )

        self.assertEqual(health, supervisor.rewrite_provider_health.AccountHealth.REVOKED)
        capability = provider_report["providers"]["codex2"]
        self.assertFalse(capability["auth_ready"])
        self.assertFalse(capability["local_cli_worker_supported"])
        self.assertEqual(capability["account_health"], "revoked")
        auth_probe.assert_called_once_with(config, "codex2", force=True)

        recovered_probe = {
            **probe,
            "ready": True,
            "status": "ready",
            "error": None,
        }
        with mock.patch.object(supervisor, "probe_provider_auth", return_value=recovered_probe):
            recovered = supervisor.refresh_provider_auth_before_dispatch(
                config,
                provider_report,
                "codex2",
            )
        self.assertEqual(recovered, supervisor.rewrite_provider_health.AccountHealth.HEALTHY)
        self.assertTrue(capability["auth_ready"])
        self.assertTrue(capability["local_cli_worker_supported"])
        self.assertEqual(capability["account_health"], "healthy")

    def test_pre_dispatch_probe_preserves_hyphenated_provider_profile_key(self) -> None:
        config = {
            "agents": {"codex1_1": {"provider": "codex1-1"}},
            "providers": {"codex1-1": {"delivery_mode": "codex"}},
        }
        provider_report = {"providers": {"codex1-1": {"auth_ready": True}}}
        probe = {
            "provider": "codex1-1",
            "ready": True,
            "status": "ready",
            "method": "codex_exec_oauth",
        }
        with mock.patch.object(supervisor, "probe_provider_auth", return_value=probe) as auth_probe:
            health = supervisor.refresh_provider_auth_before_dispatch(
                config,
                provider_report,
                "codex1_1",
            )

        self.assertEqual(health, supervisor.rewrite_provider_health.AccountHealth.HEALTHY)
        auth_probe.assert_called_once_with(config, "codex1-1", force=True)

        provider_report["providers"]["codex1-1"].update(
            {
                "auth_ready": False,
                "local_cli_worker_supported": False,
            }
        )
        with mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config,
                {},
                "codex1_1",
                provider_report,
            )
        self.assertEqual(reason, "codex1_1 local CLI worker is not ready")

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

    def test_reconcile_runtime_redispatches_completed_review_worker_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            config["ready_dispatcher"]["unchanged_task_cooldown_seconds"] = 900
            task = {
                "id": "OPS-REVIEW-RECOVERY",
                "status": "review",
                "owner": "Claude",
                "reviewer": "Codex",
                "depends_on": [],
                "last_update": "2026-07-24T00:10:00Z",
                "next": "Review pending after runtime replacement.",
            }
            handoff = {
                "task_id": task["id"],
                "from": "Claude",
                "to": "Codex",
                "message": task["next"],
                "status": "pending",
                "created_at": task["last_update"],
            }
            status = {"tasks": [task], "handoffs": [handoff]}
            event = supervisor.build_dispatch_event(
                task,
                "Codex",
                "review_ready_dispatch",
                {task["id"]: task},
            )
            event.update(
                {
                    "event_id": "evt-review-recovery",
                    "created_at": "2026-07-24T00:10:01Z",
                    "target_agent": "codex",
                    "target_display_name": "Codex",
                    "message": "review",
                }
            )
            (root / "ai-status.json").write_text(
                json.dumps(status),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps(event) + "\n",
                encoding="utf-8",
            )
            (root / "activity-log.jsonl").write_text("", encoding="utf-8")
            worker = {
                "run_id": "codex-review-recovery",
                "status": "running",
                "provider": "codex",
                "agent_id": "codex",
                "task_id": task["id"],
                "queue_event_id": event["event_id"],
                "pid": 987654,
                "runner_status": "completed",
                "runner_finished_at": "2026-07-24T00:11:00Z",
                "exit_code": 0,
                "request_snapshot": {
                    "agent_id": "codex",
                    "provider": "codex",
                    "delivery_mode": "codex",
                    "message": "review",
                    "task_id": task["id"],
                    "reason": "review_ready_dispatch",
                    "metadata": {"dispatch_event_key": event["key"]},
                },
            }
            state = {
                "queue": {
                    "events": {
                        event["event_id"]: {
                            "status": "started",
                            "run_id": worker["run_id"],
                            "event_key": event["key"],
                        }
                    }
                },
                "workers": {worker["run_id"]: worker},
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(supervisor.reconcile_runtime_on_boot(config, state))

            self.assertEqual(worker["status"], "completed")
            self.assertEqual(
                state["seen_event_keys"][event["key"]],
                state["queue"]["events"][event["event_id"]]["processed_at"],
            )
            self.assertTrue(supervisor.prune_event_queue(config, state))
            self.assertEqual(state["queue"]["events"], {})

            queued: list[dict[str, object]] = []
            with (
                mock.patch.object(
                    supervisor,
                    "queue_delivery_event",
                    side_effect=lambda _config, item: queued.append(item) or True,
                ),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(
                    supervisor.dispatch_ready_tasks(
                        config,
                        state,
                        provider_report={},
                        agent_ids_override=["codex"],
                        max_dispatches_override=1,
                    )
                )
                self.assertFalse(
                    supervisor.dispatch_ready_tasks(
                        config,
                        state,
                        provider_report={},
                        agent_ids_override=["codex"],
                        max_dispatches_override=1,
                    )
                )

            self.assertEqual(len(queued), 1)
            redispatch = queued[0]["task"]["governed_review_redispatch"]
            self.assertEqual(redispatch["parent_worker_run_id"], worker["run_id"])
            self.assertTrue(redispatch["require_isolated_worktree"])
            self.assertEqual(
                json.loads((root / "ai-status.json").read_text(encoding="utf-8")),
                status,
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

    def test_reconcile_runtime_expires_stale_progress_despite_fresh_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            config["supervisor"] = {"lease_requires_work_progress": True}
            config["worker_runtime"] = {"work_progress_stale_seconds": 360}
            (root / "ai-status.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
            now = datetime.now(timezone.utc)
            worker = {
                "run_id": "codex-run-progress-expired",
                "status": "running",
                "provider": "codex",
                "agent_id": "codex",
                "task_id": "OPS-LEASE-PROGRESS",
                "pid": 1234,
                "last_heartbeat_at": now.isoformat(),
                "last_event_at": (now - timedelta(seconds=700)).isoformat(),
                "lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
            }
            state = {"queue": {"events": {}}, "workers": {worker["run_id"]: worker}}

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=True),
                mock.patch.object(supervisor, "update_worker_runtime_markers", return_value=False),
                mock.patch.object(supervisor, "refresh_worker_lease") as refresh_worker_lease,
                mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            self.assertEqual(worker["status"], "failed")
            self.assertEqual(
                worker["last_error"],
                "Worker lease expired during supervisor boot reconciliation.",
            )
            refresh_worker_lease.assert_not_called()
            terminate_worker_pid.assert_called_once_with(1234)
            self.assertEqual(
                state["worker_runtime_metrics"]["totals"]["expired_lease_workers_failed"],
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

    def test_reconcile_runtime_uses_log_failure_for_copilot_monthly_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            config["providers"]["copilot"] = {"delivery_mode": "copilot_local"}
            config["agents"]["copilot"] = {"id": "copilot", "display_name": "Copilot", "provider": "copilot"}
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "MPOS-P1-PER-002",
                                "status": "in_progress",
                                "owner": "Copilot",
                                "reviewer": "Codex",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-copilot", "task_id": "MPOS-P1-PER-002", "target_agent": "copilot"})
                + "\n",
                encoding="utf-8",
            )
            log_path = root / "copilot-quota.log"
            log_path.write_text(
                '402 {"error":{"message":"You have exceeded your monthly quota","code":"quota_exceeded"}}\n',
                encoding="utf-8",
            )
            state = {
                "queue": {"events": {"evt-copilot": {"status": "started", "run_id": "copilot-run-dead"}}},
                "provider_guardrails": {"dispatch_pauses": {}},
                "workers": {
                    "copilot-run-dead": {
                        "run_id": "copilot-run-dead",
                        "status": "running",
                        "provider": "copilot",
                        "agent_id": "copilot",
                        "task_id": "MPOS-P1-PER-002",
                        "queue_event_id": "evt-copilot",
                        "pid": 987654,
                        "log_path": str(log_path),
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_failure_evidence", return_value="evidence/copilot.json"),
                mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value="Claude"),
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["copilot-run-dead"]
            self.assertEqual(worker["status"], "reassigned")
            self.assertEqual(worker["reassigned_to"], "Claude")
            self.assertEqual(state["queue"]["events"]["evt-copilot"]["status"], "completed")
            pause = state["provider_guardrails"]["dispatch_pauses"]["copilot"]
            self.assertEqual(pause["pause_kind"], "quota_terminal")
            self.assertEqual(pause["worker_run_id"], "copilot-run-dead")
            streak = state["provider_guardrails"]["task_failure_streaks"]["MPOS-P1-PER-002:copilot"]
            self.assertEqual(streak["last_failure_kind"], "quota_terminal")
            self.assertIn("monthly quota", worker["last_error"].lower())

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

    def test_explicit_account_cap_uses_one_identity_and_counts_legacy_worker_field(self) -> None:
        config = {
            "ready_dispatcher": {"max_concurrent_per_account": {"shared": 1}},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
            },
            "providers": {
                "claude": {"account": "shared"},
                "claude2": {"account": "shared"},
            },
        }
        state = {
            "workers": {
                "run-1": {
                    "status": "running",
                    "agent_id": "claude",
                    "provider": "claude",
                    "quota_group": "shared",
                }
            }
        }

        self.assertEqual(supervisor.provider_dispatch_identity_ids(config, "claude"), ["shared"])
        self.assertEqual(supervisor.agent_quota_identity_ids(config, "claude2"), ["shared"])
        self.assertEqual(
            supervisor.active_quota_group_counts(config, state, {"running"}),
            {"shared": 1},
        )
        reason = supervisor.agent_auto_dispatch_block_reason(
            config, state, "claude2", provider_report={}
        )

        self.assertIsNotNone(reason)
        self.assertIn("account shared", reason or "")

    def test_account_group_uses_legacy_quota_cap_and_counts_legacy_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            provider_report_path = Path(tmpdir) / "provider_capabilities.json"
            provider_report_path.write_text(
                json.dumps(
                    {
                        "providers": {
                            "claude": {"account_group": "claude_account_shared", "auth_ready": True},
                            "claude2": {"account_group": "claude_account_shared", "auth_ready": True},
                        }
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "ready_dispatcher": {"max_concurrent_per_quota_group": {"claude": 1}},
                "paths": {"provider_capabilities": str(provider_report_path)},
                "agents": {
                    "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                    "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
                },
                "providers": {
                    "claude": {"delivery_mode": "claude_cli", "quota_group": "claude"},
                    "claude2": {"delivery_mode": "claude_cli", "quota_group": "claude2"},
                    "claude-1": {"delivery_mode": "claude_cli", "quota_group": "claude"},
                },
            }
            state = {
                "workers": {
                    "run-1": {
                        "run_id": "run-1",
                        "status": "running",
                        "agent_id": "claude",
                        "provider": "claude-1",
                        "quota_group": "claude",
                    }
                }
            }

            reason = supervisor.agent_auto_dispatch_block_reason(config, state, "claude2", provider_report={})

        self.assertIsNotNone(reason)
        self.assertIn("quota group claude_account_shared", reason or "")


class AgentDispatchLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "agents": {
                "codex2_2": {
                    "id": "codex2_2",
                    "display_name": "Codex2",
                    "provider": "codex2-2",
                },
            },
        }

    def test_active_worker_queue_event_is_counted_once(self) -> None:
        state = {
            "workers": {
                "run-active": {
                    "status": "running",
                    "agent_id": "codex2_2",
                    "queue_event_id": "evt-active",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                },
            },
            "queue": {
                "events": {
                    "evt-active": {"status": "started"},
                    "evt-pending": {"status": "queued"},
                },
            },
        }
        queued_events = [
            {
                "event_id": "evt-active",
                "target_display_name": "Codex2",
                "reason": "owned_finalize_dispatch",
            },
            {
                "event_id": "evt-pending",
                "target_display_name": "Codex2",
                "reason": "owned_in_progress_dispatch",
            },
        ]

        with mock.patch.object(supervisor, "load_event_queue", return_value=queued_events):
            loads = supervisor.agent_dispatch_loads(self.config, state, {"running"})

        self.assertEqual(
            loads,
            {
                "Codex2": [
                    supervisor.dispatch_reason_priority("owned_finalize_dispatch"),
                    supervisor.dispatch_reason_priority("owned_in_progress_dispatch"),
                ],
            },
        )

    def test_queue_event_remains_fallback_when_worker_reason_is_missing(self) -> None:
        state = {
            "workers": {
                "run-active": {
                    "status": "running",
                    "agent_id": "codex2_2",
                    "queue_event_id": "evt-active",
                    "request_snapshot": {},
                },
            },
            "queue": {"events": {"evt-active": {"status": "started"}}},
        }
        queued_events = [
            {
                "event_id": "evt-active",
                "target_display_name": "Codex2",
                "reason": "owned_in_progress_dispatch",
            },
        ]

        with mock.patch.object(supervisor, "load_event_queue", return_value=queued_events):
            loads = supervisor.agent_dispatch_loads(self.config, state, {"running"})

        self.assertEqual(
            loads,
            {"Codex2": [supervisor.dispatch_reason_priority("owned_in_progress_dispatch")]},
        )


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
            mock.patch.object(supervisor, "console_log") as console_log,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)
        scan.assert_called()
        start.assert_not_called()
        self.assertFalse(changed)
        self.assertTrue(
            any("live worker count 3 >= max_concurrent_workers 2" in call.args[0] for call in console_log.call_args_list)
        )

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

    def test_dispatch_wave_is_clamped_to_remaining_global_slots(self) -> None:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))
        config["ready_dispatcher"]["max_concurrent_workers"] = 2
        config["ready_dispatcher"]["max_dispatches_per_tick"] = 4
        status = {
            "tasks": [
                {
                    "id": f"CAP-{index}",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "last_update": f"2026-07-13T16:0{index}:00Z",
                }
                for index in range(1, 5)
            ]
        }
        state = {"queue": {"events": {}}, "workers": {}}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "normalize_mainline_task_assignment", return_value=False),
            mock.patch.object(
                supervisor,
                "scan_live_worker_pids_by_agent",
                return_value={"Codex": [101]},
            ),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
        ):
            changed = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["codex"],
            )

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        self.assertEqual(queue_delivery_event.call_args.args[1]["task_id"], "CAP-1")


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

    def test_skips_dirty_worktree_when_dirty_archive_disabled(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
        dirty_status = subprocess.CompletedProcess(args=[], returncode=0, stdout=" M foo.py\n", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
            ("git", "-C", record_path, "status", "--porcelain"): dirty_status,
        }
        config = {
            "worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0},
            "worker_worktree_cleanup": {"archive_dirty_worktrees": False},
        }
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

    def test_archives_and_force_removes_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = (Path(tmpdir) / "wt").resolve()
            wt_path = base / "task-x"
            wt_path.mkdir(parents=True)
            record_path = str(wt_path)
            records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
            merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
            dirty_status = subprocess.CompletedProcess(args=[], returncode=0, stdout=" M foo.py\n", stderr="")
            remove_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            runs = {
                ("git", "branch", "--merged"): merged_proc,
                ("git", "-C", record_path, "status", "--porcelain"): dirty_status,
                ("git", "-C", "/repo", "worktree", "remove", "--force", record_path): remove_ok,
            }
            config = {
                "worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0},
                "worker_worktree_cleanup": {
                    "archive_dirty_worktrees": True,
                    "force_remove_archived_dirty": True,
                },
            }
            state = {
                "worker_worktrees": {
                    "leases": {
                        "task-x": {
                            "path": record_path,
                            "branch": "task/X",
                        }
                    }
                }
            }
            with (
                mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
                mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
                mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
                mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
                mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
                mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
                mock.patch.object(supervisor, "_archive_dirty_worktree", return_value=Path("/archive/task-x")) as archive,
                mock.patch.object(supervisor, "write_activity_log"),
                mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
            ):
                result = supervisor.prune_orphan_worktrees(config, state)
            self.assertTrue(result)
            archive.assert_called_once()
            self.assertNotIn("task-x", state["worker_worktrees"]["leases"])
            self.assertEqual(state["worker_worktree_cleanup"]["last_run"]["archived"], 1)

    def test_lifecycle_cleanup_removes_inactive_registered_worktree_without_merge_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = (Path(tmpdir) / "wt").resolve()
            wt_path = base / "task-x"
            wt_path.mkdir(parents=True)
            record_path = str(wt_path)
            clean_status = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            remove_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            runs = {
                ("git", "-C", record_path, "status", "--porcelain"): clean_status,
                ("git", "-C", "/repo", "worktree", "remove", record_path): remove_ok,
            }
            config = {"worker_worktree_cleanup": {"enabled": True, "cleanup_inactive_leases": True}}
            state = {
                "worker_worktrees": {
                    "leases": {
                        "task-x": {
                            "path": record_path,
                            "branch": "task/X",
                        }
                    }
                },
                "workers": {},
            }
            with (
                mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
                mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
                mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
                mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
                mock.patch.object(supervisor, "_git_worktree_records", return_value=[]),
                mock.patch.object(supervisor, "write_activity_log"),
                mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
            ):
                result = supervisor.cleanup_inactive_worker_worktrees(config, state)
            self.assertTrue(result)
            self.assertEqual(state["worker_worktrees"]["leases"], {})
            self.assertEqual(state["worker_worktree_cleanup"]["last_run"]["removed"], 1)

    def test_skips_worktree_claimed_by_active_worker(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state = {"workers": {"r-1": {"workspace_path": record_path, "status": "running"}}}
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


class PruneChairReviewWorktreesTests(unittest.TestCase):
    def _stub_subprocess_run(self, results):
        def fake_run(cmd, *args, **kwargs):
            cmd_tuple = tuple(str(c) for c in cmd)
            for key, value in results.items():
                if cmd_tuple[: len(key)] == key:
                    return value
            raise AssertionError(f"unexpected subprocess.run call: {cmd_tuple}")
        return fake_run

    def _fake_stat(self, mtime_by_name):
        import types

        def fake_stat(self, *args, **kwargs):
            return types.SimpleNamespace(st_mtime=mtime_by_name.get(self.name, 0.0))
        return fake_stat

    def test_returns_false_when_disabled(self) -> None:
        config = {"worker_worktree_housekeeping": {"enabled": False}}
        self.assertFalse(supervisor.prune_chair_review_worktrees(config, {}))

    def test_throttled_within_interval(self) -> None:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        recent_ts = (_dt.now(_tz.utc) - _td(seconds=30)).isoformat().replace("+00:00", "Z")
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 600}}
        state = {"chair_review_worktree_housekeeping": {"last_run_at": recent_ts}}
        with mock.patch.object(supervisor, "worker_worktree_settings") as ws:
            result = supervisor.prune_chair_review_worktrees(config, state)
        self.assertFalse(result)
        ws.assert_not_called()

    def test_removes_old_chair_review_worktree(self) -> None:
        base = Path("/tmp/wt").resolve()
        name = "chair-review-20260620-061500-claude"
        record_path = str(base / name)
        records = [
            {"worktree": record_path, "branch": "refs/heads/dev"},
            {"worktree": "/repo", "branch": "refs/heads/main"},
        ]
        remove_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        runs = {("git", "-C", "/repo", "worktree", "remove", "--force", record_path): remove_ok}
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(supervisor, "write_activity_log") as log,
            mock.patch.object(supervisor.time, "time", return_value=1_000_000.0),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(Path, "stat", self._fake_stat({name: 1_000_000.0 - 10_000})),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            result = supervisor.prune_chair_review_worktrees(config, state)
        self.assertTrue(result)
        log.assert_called_once()

    def test_skips_recent_chair_review_worktree(self) -> None:
        base = Path("/tmp/wt").resolve()
        name = "chair-review-20260620-061500-claude"
        record_path = str(base / name)
        records = [{"worktree": record_path, "branch": "refs/heads/dev"}]
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(supervisor.time, "time", return_value=1_000_000.0),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(Path, "stat", self._fake_stat({name: 1_000_000.0 - 100})),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run({})),
        ):
            result = supervisor.prune_chair_review_worktrees(config, state)
        self.assertFalse(result)

    def test_skips_non_chair_review_worktree(self) -> None:
        base = Path("/tmp/wt").resolve()
        name = "task-x"
        record_path = str(base / name)
        records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(supervisor.time, "time", return_value=1_000_000.0),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(Path, "stat", self._fake_stat({name: 1_000_000.0 - 10_000})),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run({})),
        ):
            result = supervisor.prune_chair_review_worktrees(config, state)
        self.assertFalse(result)

    def test_skips_claimed_chair_review_worktree(self) -> None:
        base = Path("/tmp/wt").resolve()
        name = "chair-review-20260620-061500-claude"
        record_path = str(base / name)
        records = [{"worktree": record_path, "branch": "refs/heads/dev"}]
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state = {"workers": {"r-1": {"workspace_path": record_path}}}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(supervisor.time, "time", return_value=1_000_000.0),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(Path, "stat", self._fake_stat({name: 1_000_000.0 - 10_000})),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run({})),
        ):
            result = supervisor.prune_chair_review_worktrees(config, state)
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


# SUP-WORKER-TRUTH-RECONCILE-001 -----------------------------------------------
# Regression coverage for the four worker-truth defects observed on the live
# fleet: nonthrottling rate-limit notices read as worker failures, auth pauses
# that expired on a timer instead of on a fresh probe, ownerless in_progress
# rows redispatched to their owner forever, and the queue/lease state left
# dangling by that reconciliation.


ALLOWED_WARNING_RATE_LIMIT_LINE = json.dumps(
    {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed_warning",
            "resetsAt": 1785153600,
            "rateLimitType": "seven_day",
            "utilization": 0.83,
            "isUsingOverage": False,
            "surpassedThreshold": 0.75,
        },
        "uuid": "466e8308-da86-4dbd-a188-985b8558a428",
        "session_id": "30c27323-d5f9-41ec-8d84-6ea882f1ba15",
    },
    separators=(",", ":"),
)


class AllowedRateLimitNoticeTests(unittest.TestCase):
    """An `allowed_warning` quota notice is not a worker failure."""

    def _worker_with_log(self, *lines: str) -> dict[str, object]:
        handle = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        self.addCleanup(os.unlink, handle.name)
        with handle:
            handle.write("\n".join(lines) + "\n")
        return {"run_id": "claude1-2-run", "provider": "claude1-2", "log_path": handle.name}

    def test_allowed_warning_event_is_not_detected_as_worker_failure(self) -> None:
        worker = self._worker_with_log(
            '{"type":"assistant","message":{"role":"assistant"}}',
            ALLOWED_WARNING_RATE_LIMIT_LINE,
        )
        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_allowed_status_event_is_still_not_a_worker_failure(self) -> None:
        payload = json.loads(ALLOWED_WARNING_RATE_LIMIT_LINE)
        payload["rate_limit_info"]["status"] = "allowed"
        worker = self._worker_with_log(json.dumps(payload, separators=(",", ":")))
        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_rejected_rate_limit_event_is_still_a_worker_failure(self) -> None:
        payload = json.loads(ALLOWED_WARNING_RATE_LIMIT_LINE)
        payload["rate_limit_info"]["status"] = "rejected"
        line = json.dumps(payload, separators=(",", ":"))
        worker = self._worker_with_log(line)
        self.assertEqual(supervisor.detect_worker_failure(worker), line)

    def test_truncated_allowed_warning_line_is_not_a_worker_failure(self) -> None:
        truncated = ALLOWED_WARNING_RATE_LIMIT_LINE[: ALLOWED_WARNING_RATE_LIMIT_LINE.index("resetsAt") - 2]
        with self.assertRaises(json.JSONDecodeError):
            json.loads(truncated)
        worker = self._worker_with_log(truncated)
        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_allowed_warning_reason_classifies_nonterminal_and_never_pauses(self) -> None:
        failure = supervisor.classify_worker_failure(
            {}, {"provider": "claude1-2"}, ALLOWED_WARNING_RATE_LIMIT_LINE
        )
        self.assertEqual(failure["kind"], "transient")
        self.assertTrue(failure["transient"])
        self.assertFalse(supervisor.should_pause_dispatch_for_failure_kind(failure["kind"]))

    def test_reaped_worker_is_not_paused_for_an_allowed_warning(self) -> None:
        worker = self._worker_with_log(ALLOWED_WARNING_RATE_LIMIT_LINE)
        state: dict[str, object] = {}
        with mock.patch.object(supervisor, "write_activity_log"):
            reason = supervisor.pause_dispatch_for_reaped_worker(
                {"paths": {"activity_log": "/tmp/test-activity-log.jsonl"}}, state, worker
            )
        self.assertIsNone(reason)
        self.assertEqual(state.get("provider_guardrails", {}), {})


class FreshAuthProbeLaneHoldTests(unittest.TestCase):
    """A fresh not-ready probe holds the lane until a fresh success, no config edit."""

    def setUp(self) -> None:
        self.config = {
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "agents": {"codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"}},
            "providers": {"codex2": {"delivery_mode": "codex", "quota_group": "codex2"}},
            "provider_guardrails": {"auth_pause_seconds": 900},
        }
        self.config_snapshot = json.dumps(self.config, sort_keys=True)

    def _refresh(self, probe: dict[str, object], state: dict[str, object]) -> dict[str, object]:
        report = {"providers": {"codex2": {"auth_ready": True}}}
        with (
            mock.patch.object(supervisor, "probe_provider_auth", return_value=probe),
            mock.patch.object(supervisor, "write_provider_capabilities") as write_caps,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            supervisor.refresh_provider_auth_before_dispatch(self.config, report, "codex2", state)
        self.write_caps = write_caps
        return report

    def test_live_not_ready_probe_holds_lane_until_a_live_success(self) -> None:
        state: dict[str, object] = {}
        probe = {
            "provider": "codex2",
            "ready": False,
            "status": "not_ready",
            "method": "codex_exec_oauth",
            "error": "login required",
            "checked_at": "2026-07-26T19:00:00Z",
            "last_auth_probe_at": "2026-07-26T19:00:00Z",
            "source": "live",
        }
        report = self._refresh(probe, state)

        self.assertIs(report["providers"]["codex2"]["auth_ready"], False)
        pause = state["provider_guardrails"]["dispatch_pauses"]["codex2"]
        self.assertEqual(pause["pause_kind"], "auth")
        self.assertIs(pause["requires_live_auth_probe"], True)
        self.assertEqual(pause["blocked_until"], supervisor.STICKY_AUTH_BLOCKED_UNTIL)
        self.assertTrue(supervisor.provider_dispatch_paused(self.config, state, "codex2"))
        # The refreshed report is persisted so the next capability scan re-probes
        # on the failed-probe interval rather than reusing the ready cache.
        self.write_caps.assert_called_once_with(self.config, report=report)

        # A wall-clock sweep must not reopen the lane.
        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            expired = supervisor.expire_provider_dispatch_pauses(self.config, state)
        self.assertFalse(expired)
        write_activity_log.assert_not_called()
        self.assertTrue(supervisor.provider_dispatch_paused(self.config, state, "codex2"))

        # Neither may a cached "ready" report.
        cached = {
            "providers": {
                "codex2": {
                    "auth_ready": True,
                    "auth_probe": {"ready": True, "source": "cached", "method": "codex_exec_oauth"},
                }
            }
        }
        with mock.patch.object(supervisor, "write_activity_log"):
            recovered = supervisor.reconcile_provider_auth_recovery(self.config, state, report, cached)
        self.assertFalse(recovered)
        self.assertTrue(supervisor.provider_dispatch_paused(self.config, state, "codex2"))

        # Only a fresh live success clears it.
        live = {
            "providers": {
                "codex2": {
                    "auth_ready": True,
                    "auth_method": "codex_exec_oauth",
                    "last_auth_probe_at": "2026-07-26T20:00:00Z",
                    "auth_probe": {"ready": True, "source": "live", "method": "codex_exec_oauth"},
                }
            }
        }
        with mock.patch.object(supervisor, "write_activity_log"):
            recovered = supervisor.reconcile_provider_auth_recovery(self.config, state, report, live)
        self.assertTrue(recovered)
        self.assertFalse(supervisor.provider_dispatch_paused(self.config, state, "codex2"))
        self.assertEqual(json.dumps(self.config, sort_keys=True), self.config_snapshot)

    def test_cached_not_ready_probe_does_not_raise_a_lane_hold(self) -> None:
        state: dict[str, object] = {}
        probe = {
            "provider": "codex2",
            "ready": False,
            "status": "not_ready",
            "method": "codex_exec_oauth",
            "checked_at": "2026-07-26T19:00:00Z",
            "source": "cached",
        }
        self._refresh(probe, state)
        self.assertEqual(state.get("provider_guardrails", {}).get("dispatch_pauses", {}), {})

    def test_probe_gated_auth_pause_survives_its_wall_clock_window(self) -> None:
        """The observed regression: an auth pause reopened the lane on a timer."""
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex2": {
                        "provider": "codex2",
                        "pause_kind": "auth",
                        "requires_live_auth_probe": True,
                        "blocked_until": "2020-01-01T00:00:00Z",
                    }
                }
            }
        }
        with mock.patch.object(supervisor, "write_activity_log"):
            expired = supervisor.expire_provider_dispatch_pauses(self.config, state)
        self.assertFalse(expired)
        self.assertIn("codex2", state["provider_guardrails"]["dispatch_pauses"])
        self.assertTrue(supervisor.provider_dispatch_paused(self.config, state, "codex2"))

    def test_capacity_pause_still_expires_on_its_window(self) -> None:
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex2": {
                        "provider": "codex2",
                        "pause_kind": "capacity_retryable",
                        "blocked_until": "2020-01-01T00:00:00Z",
                    }
                }
            }
        }
        with mock.patch.object(supervisor, "write_activity_log"):
            expired = supervisor.expire_provider_dispatch_pauses(self.config, state)
        self.assertTrue(expired)
        self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})


class OwnerlessInProgressReconciliationTests(unittest.TestCase):
    """Seven ownerless in_progress fixtures, one authoritative decision each."""

    def setUp(self) -> None:
        self.config = {
            "paths": {"status_file": "ai-status.json", "activity_log": "/tmp/test-activity-log.jsonl"},
            "schema": {"tasks_path": "tasks", "handoffs_path": "handoffs"},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex2": {"id": "codex2", "display_name": "Codex2"},
            },
            "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            "worker_worktree_cleanup": {"base_branches": ["dev"]},
        }

    # -- fixture helpers ---------------------------------------------------

    def _task(self, task_id: str, **overrides: object) -> dict[str, object]:
        task = {
            "id": task_id,
            "status": "in_progress",
            "owner": "Claude",
            "reviewer": "Codex2",
        }
        task.update(overrides)
        return task

    def _worker(self, task_id: str, **overrides: object) -> dict[str, object]:
        worker = {
            "run_id": f"claude1-1-{task_id}",
            "task_id": task_id,
            "provider": "claude1-1",
            "agent_id": "claude1_1",
            "logical_agent_id": "claude",
            "status": "completed",
            "runner_status": "completed",
            "exit_code": 0,
            "lease_acquired_at": "2026-07-26T17:00:00Z",
            "last_event_at": "2026-07-26T18:00:00Z",
            "runner_finished_at": "2026-07-26T18:00:00Z",
            "queue_event_id": f"evt-{task_id}",
            "commit_progress_count": 2,
            "last_commit_progress_at": "2026-07-26T17:45:00Z",
            "work_progress_snapshot": {"commit_sha": "a" * 40},
            "request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS},
        }
        worker.update(overrides)
        return worker

    def _run(
        self,
        *,
        tasks: list[dict[str, object]],
        state: dict[str, object],
        merged_task_ids: set[str],
        unmerged_task_ids: set[str] | None = None,
    ) -> tuple[bool, dict[str, object], mock.Mock]:
        status = {"tasks": tasks, "handoffs": []}

        def fake_load_status(_config: dict[str, object]) -> dict[str, object]:
            return status

        def fake_write_status(_config: dict[str, object], payload: dict[str, object], *, source: str) -> None:
            self.write_sources.append(source)

        def fake_merged(
            _config: dict[str, object],
            task_id: str,
            *,
            # Defaults keep this fake callable under the pre-fix (PR #4212)
            # signature so the prefix reproduction shows the real false
            # positive instead of a TypeError.
            delivery_head: str = "",
            since: str = "",
        ) -> dict[str, object] | None:
            if task_id not in merged_task_ids:
                return None
            return {
                "base_ref": "origin/dev",
                "commits": [f"{task_id}-sha"],
                "delivery_head": delivery_head,
                "merge_commit": f"{task_id}-merge",
                "trailer_commits_since": since,
            }

        def fake_unmerged(
            _config: dict[str, object],
            task_id: str,
            _base_ref: str,
            *,
            delivery_head: str | None = None,
        ) -> bool:
            return task_id in (unmerged_task_ids or set())

        self.write_sources: list[str] = []
        with (
            mock.patch.object(supervisor, "load_status", side_effect=fake_load_status),
            mock.patch.object(supervisor, "write_status", side_effect=fake_write_status),
            mock.patch.object(supervisor, "merged_delivery_commits", side_effect=fake_merged),
            mock.patch.object(supervisor, "task_branch_has_unmerged_commits", side_effect=fake_unmerged),
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True) as sync_pipeline,
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "utc_now", return_value="2026-07-26T20:00:00Z"),
        ):
            changed = supervisor.reconcile_ownerless_in_progress_tasks(self.config, state)
        return changed, status, sync_pipeline

    # -- fixture 1 ---------------------------------------------------------

    def test_merged_owner_delivery_moves_to_governed_review_handoff(self) -> None:
        worker = self._worker("SUP-A")
        state = {
            "workers": {worker["run_id"]: worker},
            "queue": {"events": {"evt-SUP-A": {"task_id": "SUP-A", "status": "started"}}},
        }
        # The queue record is finalized by the worker terminal outcome first.
        state["queue"]["events"]["evt-SUP-A"]["status"] = "completed"
        changed, status, sync_pipeline = self._run(
            tasks=[self._task("SUP-A")], state=state, merged_task_ids={"SUP-A"}
        )

        self.assertTrue(changed)
        task = status["tasks"][0]
        self.assertEqual(task["status"], "review")
        self.assertEqual(task["last_update"], "2026-07-26T20:00:00Z")
        self.assertIn("moves to review for Codex2", task["next"])
        self.assertEqual(self.write_sources, ["supervisor-ownerless-review-handoff"])
        self.assertEqual(
            status["status_activity_outbox"]["events"][0]["type"],
            "task_ownerless_review_handoff",
        )
        handoff = status["handoffs"][0]
        self.assertEqual(handoff["from"], "Claude")
        self.assertEqual(handoff["to"], "Codex2")
        self.assertEqual(handoff["status"], "pending")
        sync_pipeline.assert_called_once_with(self.config)
        # Queue and lease truth stay consistent with the reconciled outcome.
        record = state["queue"]["events"]["evt-SUP-A"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["lease_owner"], worker["run_id"])
        self.assertEqual(record["lease_released_at"], "2026-07-26T20:00:00Z")
        self.assertEqual(worker["ownerless_reconciled_task_status"], "review")

    # -- fixture 2 ---------------------------------------------------------

    def test_live_worker_task_is_never_reset(self) -> None:
        worker = self._worker("SUP-B", status="running")
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, sync_pipeline = self._run(
            tasks=[self._task("SUP-B")], state=state, merged_task_ids={"SUP-B"}
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")
        self.assertEqual(status["handoffs"], [])
        self.assertEqual(self.write_sources, [])
        sync_pipeline.assert_not_called()

    # -- fixture 3 ---------------------------------------------------------

    def test_in_flight_queue_event_is_left_for_dispatch(self) -> None:
        worker = self._worker("SUP-C")
        state = {
            "workers": {worker["run_id"]: worker},
            "queue": {"events": {"evt-SUP-C": {"task_id": "SUP-C", "status": "queued"}}},
        }
        changed, status, _ = self._run(
            tasks=[self._task("SUP-C")], state=state, merged_task_ids={"SUP-C"}
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")
        self.assertEqual(state["queue"]["events"]["evt-SUP-C"]["status"], "queued")

    # -- fixture 4 ---------------------------------------------------------

    def test_task_without_any_terminal_worker_evidence_is_untouched(self) -> None:
        state = {"workers": {}, "queue": {"events": {}}}
        changed, status, _ = self._run(
            tasks=[self._task("SUP-D")], state=state, merged_task_ids={"SUP-D"}
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")

    # -- fixture 5 ---------------------------------------------------------

    def test_failed_terminal_outcome_stays_with_the_failure_ladder(self) -> None:
        worker = self._worker(
            "SUP-E",
            status="failed",
            runner_status="failed",
            exit_code=1,
            last_error="Worker exited before the task reached a terminal status.",
        )
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, _ = self._run(
            tasks=[self._task("SUP-E")], state=state, merged_task_ids={"SUP-E"}
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")
        self.assertNotIn("ownerless_reconciled_task_status", worker)

    # -- fixture 6 ---------------------------------------------------------

    def test_unmerged_delivery_is_left_for_owner_redispatch(self) -> None:
        worker = self._worker("SUP-F")
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, _ = self._run(
            tasks=[self._task("SUP-F")],
            state=state,
            merged_task_ids={"SUP-F"},
            unmerged_task_ids={"SUP-F"},
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")

    # -- fixture 7 ---------------------------------------------------------

    def test_task_without_a_distinct_reviewer_is_untouched(self) -> None:
        worker = self._worker("SUP-G")
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, _ = self._run(
            tasks=[self._task("SUP-G", reviewer="Claude")],
            state=state,
            merged_task_ids={"SUP-G"},
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")

    # -- all seven together ------------------------------------------------

    def test_seven_ownerless_fixtures_reconcile_in_one_pass(self) -> None:
        fixtures = [
            self._task("SUP-A"),
            self._task("SUP-B"),
            self._task("SUP-C"),
            self._task("SUP-D"),
            self._task("SUP-E"),
            self._task("SUP-F"),
            self._task("SUP-G", reviewer="Claude"),
        ]
        workers = {
            "SUP-A": self._worker("SUP-A"),
            "SUP-B": self._worker("SUP-B", status="running"),
            "SUP-C": self._worker("SUP-C"),
            "SUP-E": self._worker("SUP-E", status="failed", runner_status="failed", exit_code=1),
            "SUP-F": self._worker("SUP-F"),
            "SUP-G": self._worker("SUP-G"),
        }
        state = {
            "workers": {worker["run_id"]: worker for worker in workers.values()},
            "queue": {"events": {"evt-SUP-C": {"task_id": "SUP-C", "status": "queued"}}},
        }
        changed, status, sync_pipeline = self._run(
            tasks=fixtures,
            state=state,
            merged_task_ids={"SUP-A", "SUP-B", "SUP-C", "SUP-D", "SUP-E", "SUP-F", "SUP-G"},
            unmerged_task_ids={"SUP-F"},
        )

        self.assertTrue(changed)
        resolved = {task["id"]: task["status"] for task in status["tasks"]}
        self.assertEqual(
            resolved,
            {
                "SUP-A": "review",
                "SUP-B": "in_progress",
                "SUP-C": "in_progress",
                "SUP-D": "in_progress",
                "SUP-E": "in_progress",
                "SUP-F": "in_progress",
                "SUP-G": "in_progress",
            },
        )
        self.assertEqual([handoff["task_id"] for handoff in status["handoffs"]], ["SUP-A"])
        sync_pipeline.assert_called_once_with(self.config)
        self.assertEqual(
            state["worker_runtime_metrics"]["totals"]["ownerless_in_progress_review_handoffs"],
            1,
        )

    def test_reconciled_task_is_not_handed_off_twice(self) -> None:
        worker = self._worker("SUP-H")
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, _ = self._run(
            tasks=[self._task("SUP-H")], state=state, merged_task_ids={"SUP-H"}
        )
        self.assertTrue(changed)
        self.assertEqual(status["tasks"][0]["status"], "review")

        status["tasks"][0]["status"] = "in_progress"
        changed, status, sync_pipeline = self._run(
            tasks=status["tasks"], state=state, merged_task_ids={"SUP-H"}
        )
        self.assertFalse(changed)
        sync_pipeline.assert_not_called()

    # -- ownership / delivery binding negatives ----------------------------
    #
    # Each of these has merged Task-ID evidence for the task id and a clean,
    # successful terminal worker. Only the binding between that worker, that
    # delivery, and the task's current owner is missing, and each one must
    # leave the row untouched.

    def test_reassigned_owner_blocks_the_previous_owners_worker(self) -> None:
        worker = self._worker("SUP-REASSIGNED")
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, sync_pipeline = self._run(
            tasks=[self._task("SUP-REASSIGNED", owner="Codex2", reviewer="Claude")],
            state=state,
            merged_task_ids={"SUP-REASSIGNED"},
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")
        self.assertEqual(status["handoffs"], [])
        sync_pipeline.assert_not_called()
        self.assertNotIn("ownerless_reconciled_task_status", worker)

    def test_rerun_without_commit_progress_is_not_evidence(self) -> None:
        """A reopened task re-dispatched over an already merged branch.

        The worker exits cleanly, and its head is the previously merged tip, but
        it never advanced its worktree, so it delivered nothing this round.
        """
        worker = self._worker(
            "SUP-REOPENED",
            commit_progress_count=0,
            last_commit_progress_at=None,
        )
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, sync_pipeline = self._run(
            tasks=[self._task("SUP-REOPENED")], state=state, merged_task_ids={"SUP-REOPENED"}
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")
        sync_pipeline.assert_not_called()

    def test_worker_without_a_delivery_head_fails_closed(self) -> None:
        worker = self._worker("SUP-NOHEAD", work_progress_snapshot={})
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, _ = self._run(
            tasks=[self._task("SUP-NOHEAD")], state=state, merged_task_ids={"SUP-NOHEAD"}
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")

    def test_worker_without_a_dispatch_timestamp_fails_closed(self) -> None:
        worker = self._worker("SUP-NOSTART", lease_acquired_at=None)
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, _ = self._run(
            tasks=[self._task("SUP-NOSTART")], state=state, merged_task_ids={"SUP-NOSTART"}
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")

    def test_unregistered_worker_identity_fails_closed(self) -> None:
        worker = self._worker(
            "SUP-UNKNOWN-AGENT",
            logical_agent_id="ghost",
            agent_id="ghost_1",
            provider="ghost-1",
        )
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, _ = self._run(
            tasks=[self._task("SUP-UNKNOWN-AGENT")],
            state=state,
            merged_task_ids={"SUP-UNKNOWN-AGENT"},
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")

    def test_stale_terminal_worker_with_new_branch_work_is_not_reconciled(self) -> None:
        worker = self._worker("SUP-STALE")
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, _ = self._run(
            tasks=[self._task("SUP-STALE")],
            state=state,
            merged_task_ids={"SUP-STALE"},
            unmerged_task_ids={"SUP-STALE"},
        )

        self.assertFalse(changed)
        self.assertEqual(status["tasks"][0]["status"], "in_progress")

    def test_squash_merged_delivery_reconciles_and_skips_base_comparison(self) -> None:
        """The live #4213 shape, driven end to end through the phase."""
        worker = self._worker("SUP-SQUASH", work_progress_snapshot={"commit_sha": "9e" + "0" * 38})
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        status = {"tasks": [self._task("SUP-SQUASH")], "handoffs": []}
        squashed = {
            "base_ref": "origin/dev",
            "commits": ["0410a89f0e4ac3c53e7bc5192aebe6925423b4da"],
            "delivery_head": "9e" + "0" * 38,
            "merge_commit": "0410a89f0e4ac3c53e7bc5192aebe6925423b4da",
            "trailer_commits_since": "2026-07-26T17:00:00Z",
            "delivery_shape": "squash_pr_metadata",
            "pull_request_number": 4213,
            "pull_request_url": "https://github.com/ajoe734/pantheon/pull/4213",
            "pull_request_head_ref_oid": "9e" + "0" * 38,
            "pull_request_base_ref_name": "dev",
            "pull_request_merged_at": "2026-07-26T20:18:15Z",
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_status"),
            mock.patch.object(supervisor, "merged_delivery_commits", return_value=squashed),
            mock.patch.object(
                supervisor, "task_branch_has_unmerged_commits", return_value=False
            ) as branch_check,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "utc_now", return_value="2026-07-26T20:00:00Z"),
        ):
            changed = supervisor.reconcile_ownerless_in_progress_tasks(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(status["tasks"][0]["status"], "review")
        # A squashed branch is never an ancestor of the base, so the base
        # comparison must be skipped; only the delivery head still applies.
        self.assertEqual(branch_check.call_args.args[2], "")
        self.assertEqual(branch_check.call_args.kwargs["delivery_head"], "9e" + "0" * 38)
        evidence = status["status_activity_outbox"]["events"][0]["evidence"]
        self.assertEqual(evidence["delivery_shape"], "squash_pr_metadata")
        self.assertEqual(evidence["pull_request_number"], 4213)
        self.assertEqual(evidence["pull_request_merged_at"], "2026-07-26T20:18:15Z")

    def test_prefetched_squash_metadata_never_falls_back_to_locked_gh(self) -> None:
        task_id = "SUP-PREFETCHED-SQUASH"
        head = "b" * 40
        merge = "c" * 40
        task = self._task(task_id)
        worker = self._worker(
            task_id,
            work_progress_snapshot={"commit_sha": head},
        )
        state = {
            "workers": {worker["run_id"]: worker},
            "queue": {"events": {}},
        }
        status = {"tasks": [task], "handoffs": []}
        identity = supervisor._ownerless_pr_snapshot_identity(
            self.config,
            task_id,
            task,
            worker,
        )
        self.assertIsNotNone(identity)
        snapshot = {
            **identity,
            "fetched_at": "2026-07-26T19:59:59Z",
            "records": [
                {
                    "number": 4213,
                    "state": "MERGED",
                    "headRefName": f"task/{task_id}",
                    "headRefOid": head,
                    "baseRefName": "dev",
                    "mergedAt": "2026-07-26T20:18:15Z",
                    "mergeCommit": {"oid": merge},
                    "url": "https://github.com/ajoe734/pantheon/pull/4213",
                }
            ],
        }

        def fake_ancestor(_root: object, commit: str, _ref: str) -> bool:
            return commit == merge

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(
                supervisor,
                "_git_ref_exists",
                side_effect=lambda _root, ref: ref == "origin/dev",
            ),
            mock.patch.object(
                supervisor,
                "_git_commit_is_ancestor",
                side_effect=fake_ancestor,
            ),
            mock.patch.object(supervisor, "_git_capture", return_value=f"{merge}\n"),
            mock.patch.object(
                supervisor,
                "task_branch_has_unmerged_commits",
                return_value=False,
            ),
            mock.patch.object(
                supervisor,
                "_merged_pull_requests_for_branch",
                side_effect=AssertionError("locked reconciliation retried gh"),
            ),
            mock.patch.object(
                supervisor,
                "_prepare_ownerless_review_handoff_locked",
                return_value=None,
            ) as prepare_handoff,
        ):
            changed = supervisor.reconcile_ownerless_in_progress_tasks(
                self.config,
                state,
                prefetched_merged_prs={task_id: snapshot},
            )

        self.assertFalse(changed)
        evidence = prepare_handoff.call_args.kwargs["evidence"]
        self.assertEqual(evidence["delivery_shape"], "squash_pr_metadata")
        self.assertEqual(evidence["pull_request_number"], 4213)

    def test_stale_prefetched_pr_identity_fails_closed(self) -> None:
        task_id = "SUP-STALE-PREFETCH"
        task = self._task(task_id)
        worker = self._worker(task_id)
        state = {
            "workers": {worker["run_id"]: worker},
            "queue": {"events": {}},
        }
        snapshot = {
            **supervisor._ownerless_pr_snapshot_identity(
                self.config,
                task_id,
                task,
                worker,
            ),
            "owner": "DifferentOwner",
            "records": [],
        }

        with (
            mock.patch.object(
                supervisor,
                "load_status",
                return_value={"tasks": [task], "handoffs": []},
            ),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(
                supervisor,
                "merged_owner_delivery_evidence",
            ) as merged_evidence,
        ):
            changed = supervisor.reconcile_ownerless_in_progress_tasks(
                self.config,
                state,
                prefetched_merged_prs={task_id: snapshot},
            )

        self.assertFalse(changed)
        self.assertEqual(task["status"], "in_progress")
        merged_evidence.assert_not_called()

    def test_reconciled_evidence_records_the_bound_delivery(self) -> None:
        worker = self._worker("SUP-BOUND")
        state = {"workers": {worker["run_id"]: worker}, "queue": {"events": {}}}
        changed, status, _ = self._run(
            tasks=[self._task("SUP-BOUND")], state=state, merged_task_ids={"SUP-BOUND"}
        )

        self.assertTrue(changed)
        evidence = status["status_activity_outbox"]["events"][0]["evidence"]
        self.assertEqual(evidence["delivery_head_commit"], "a" * 40)
        self.assertEqual(evidence["worker_target_agent"], "Claude")
        self.assertEqual(evidence["task_owner"], "Claude")
        self.assertEqual(evidence["dispatched_at"], "2026-07-26T17:00:00Z")
        self.assertEqual(evidence["trailer_commits_since"], "2026-07-26T17:00:00Z")
        self.assertEqual(evidence["merge_commit"], "SUP-BOUND-merge")
        self.assertEqual(evidence["commit_progress_count"], 2)
        self.assertFalse(evidence["pr_url_is_authoritative"])
        self.assertIn("a" * 12, status["tasks"][0]["next"])


class MergedDeliveryEvidenceTests(unittest.TestCase):
    """Merged evidence is bound to one delivery head, not to a task id."""

    HEAD = "b" * 40

    def setUp(self) -> None:
        self.config = {
            "paths": {"status_file": "ai-status.json"},
            "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            "worker_worktree_cleanup": {"base_branches": ["dev"]},
        }

    def _merged(self, task_id: str, **overrides: object) -> dict[str, object] | None:
        kwargs = {"delivery_head": self.HEAD, "since": "2026-07-26T17:00:00Z"}
        kwargs.update(overrides)
        return supervisor.merged_delivery_commits(self.config, task_id, **kwargs)

    def test_trailer_commit_reachable_from_the_delivery_head_is_merged_evidence(self) -> None:
        def fake_capture(_root: object, args: list[str]) -> str:
            return "abc123\ndef456\n" if args[0] == "log" else "merge999\n"

        with (
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_commit_is_ancestor", return_value=True) as ancestor,
            mock.patch.object(supervisor, "_git_capture", side_effect=fake_capture) as capture,
        ):
            evidence = self._merged("SUP-MERGED-001")

        self.assertEqual(
            evidence,
            {
                "base_ref": "origin/dev",
                "commits": ["abc123", "def456"],
                "delivery_head": self.HEAD,
                "merge_commit": "merge999",
                "trailer_commits_since": "2026-07-26T17:00:00Z",
                "delivery_shape": "merge_ancestry",
            },
        )
        ancestor.assert_called_once()
        self.assertEqual(ancestor.call_args.args[1:], (self.HEAD, "origin/dev"))
        log_args = capture.call_args_list[0].args[1]
        self.assertIn("--fixed-strings", log_args)
        self.assertIn("--grep=Task-ID: SUP-MERGED-001", log_args)
        self.assertIn("--since=2026-07-26T17:00:00Z", log_args)
        # The search is scoped to the delivery head, not to the base ref.
        self.assertEqual(log_args[-1], self.HEAD)

    def test_older_only_merged_trailer_commits_are_not_this_delivery(self) -> None:
        """The reopened-task case: the id merged before, nothing merged since."""
        with (
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_commit_is_ancestor", return_value=True),
            mock.patch.object(supervisor, "_git_capture", return_value=""),
        ):
            self.assertIsNone(self._merged("SUP-REOPENED-001"))

    def test_delivery_head_not_merged_into_the_base_is_not_evidence(self) -> None:
        """Unpushed work, with no merged PR metadata to fall back on."""
        with (
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_commit_is_ancestor", return_value=False),
            mock.patch.object(supervisor, "_merged_pull_requests_for_branch", return_value=[]),
            mock.patch.object(supervisor, "_git_capture", return_value="") as capture,
        ):
            self.assertIsNone(self._merged("SUP-UNPUSHED-001"))
        # The trailer search never runs: nothing established a merged delivery.
        self.assertEqual([call.args[1][0] for call in capture.call_args_list], [])

    def test_git_log_failure_fails_closed(self) -> None:
        with (
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_commit_is_ancestor", return_value=True),
            mock.patch.object(supervisor, "_git_capture", return_value=None),
        ):
            self.assertIsNone(self._merged("SUP-GITFAIL-001"))

    def test_missing_delivery_head_or_since_fails_closed(self) -> None:
        with (
            mock.patch.object(supervisor, "_git_ref_exists", return_value=True),
            mock.patch.object(supervisor, "_git_commit_is_ancestor", return_value=True),
            mock.patch.object(supervisor, "_git_capture", return_value="abc123\n"),
        ):
            self.assertIsNone(self._merged("SUP-NOHEAD-001", delivery_head=""))
            self.assertIsNone(self._merged("SUP-NOHEAD-001", delivery_head="task/branch"))
            self.assertIsNone(self._merged("SUP-NOSINCE-001", since=""))

    def test_fast_forward_merge_without_a_merge_commit_still_binds(self) -> None:
        def fake_capture(_root: object, args: list[str]) -> str:
            return "abc123\n" if args[0] == "log" else ""

        with (
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_commit_is_ancestor", return_value=True),
            mock.patch.object(supervisor, "_git_capture", side_effect=fake_capture),
        ):
            evidence = self._merged("SUP-FASTFORWARD-001")

        self.assertIsNotNone(evidence)
        self.assertIsNone(evidence["merge_commit"])

    def test_deleted_task_branch_reports_no_unmerged_commits(self) -> None:
        with mock.patch.object(supervisor, "_git_ref_exists", return_value=False):
            self.assertFalse(
                supervisor.task_branch_has_unmerged_commits(
                    self.config, "SUP-MERGED-001", "origin/dev", delivery_head=self.HEAD
                )
            )

    def test_task_branch_ahead_of_base_reports_unmerged_commits(self) -> None:
        with (
            mock.patch.object(
                supervisor,
                "_git_ref_exists",
                side_effect=lambda _root, ref: ref == "task/SUP-OPEN-001",
            ),
            mock.patch.object(supervisor, "_git_capture", return_value="3\n"),
        ):
            self.assertTrue(
                supervisor.task_branch_has_unmerged_commits(self.config, "SUP-OPEN-001", "origin/dev")
            )

    def test_branch_moved_past_the_delivery_head_reports_unmerged_commits(self) -> None:
        """Deleted remote branch, local branch carrying newer unpushed work."""

        def fake_capture(_root: object, args: list[str]) -> str:
            # Everything the terminal worker delivered is on the base, but the
            # surviving local branch has advanced past that delivery head.
            return "0\n" if args[-1].startswith("origin/dev..") else "2\n"

        with (
            mock.patch.object(
                supervisor,
                "_git_ref_exists",
                side_effect=lambda _root, ref: ref == "task/SUP-STALE-001",
            ),
            mock.patch.object(supervisor, "_git_capture", side_effect=fake_capture),
        ):
            self.assertTrue(
                supervisor.task_branch_has_unmerged_commits(
                    self.config, "SUP-STALE-001", "origin/dev", delivery_head=self.HEAD
                )
            )

    def test_rev_list_failure_reports_unmerged_commits(self) -> None:
        with (
            mock.patch.object(
                supervisor,
                "_git_ref_exists",
                side_effect=lambda _root, ref: ref == "task/SUP-GITFAIL-001",
            ),
            mock.patch.object(supervisor, "_git_capture", return_value=None),
        ):
            self.assertTrue(
                supervisor.task_branch_has_unmerged_commits(
                    self.config, "SUP-GITFAIL-001", "origin/dev", delivery_head=self.HEAD
                )
            )


class SquashMergedDeliveryEvidenceTests(unittest.TestCase):
    """A squash merge rewrites the head, so PR metadata is the only binding.

    The fixture is the live 2026-07-26 shape: PR #4213, head ``9e484e252``,
    squash-merged to ``0410a89f0`` on ``dev``. Git ancestry can never recognise
    it -- ``_git_commit_is_ancestor(head, dev)`` is false forever by design.
    """

    HEAD = "9e484e2522cd8778b85a4c880e4cd33d07ef401f"
    MERGE = "0410a89f0e4ac3c53e7bc5192aebe6925423b4da"
    TASK = "OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001"
    SINCE = "2026-07-26T19:53:14Z"

    def setUp(self) -> None:
        self.config = {
            "paths": {"status_file": "ai-status.json"},
            "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            "worker_worktree_cleanup": {"base_branches": ["dev"]},
        }

    def _pr(self, **overrides: object) -> dict[str, object]:
        record = {
            "number": 4213,
            "state": "MERGED",
            "headRefName": f"task/{self.TASK}",
            "headRefOid": self.HEAD,
            "baseRefName": "dev",
            "mergedAt": "2026-07-26T20:18:15Z",
            "mergeCommit": {"oid": self.MERGE},
            "url": "https://github.com/ajoe734/pantheon/pull/4213",
        }
        record.update(overrides)
        return record

    def _merged(self, records: list[dict[str, object]], *, trailer: str | None = None):
        """Run the full lookup with the squash shape wired up.

        ``_git_commit_is_ancestor`` answers false for the delivery head (the
        squash rewrote it) and true for the merge commit.
        """
        if trailer is None:
            trailer = f"{self.MERGE}\n"

        def fake_ancestor(_root: object, commit: str, _ref: str) -> bool:
            return commit == self.MERGE

        with (
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_commit_is_ancestor", side_effect=fake_ancestor),
            mock.patch.object(supervisor, "_merged_pull_requests_for_branch", return_value=records) as lookup,
            mock.patch.object(supervisor, "_git_capture", return_value=trailer) as capture,
        ):
            evidence = supervisor.merged_delivery_commits(
                self.config, self.TASK, delivery_head=self.HEAD, since=self.SINCE
            )
        return evidence, lookup, capture

    def test_live_4213_squash_shape_binds_through_pr_metadata(self) -> None:
        evidence, lookup, capture = self._merged([self._pr()])

        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["delivery_shape"], "squash_pr_metadata")
        self.assertEqual(evidence["base_ref"], "origin/dev")
        self.assertEqual(evidence["delivery_head"], self.HEAD)
        self.assertEqual(evidence["merge_commit"], self.MERGE)
        self.assertEqual(evidence["commits"], [self.MERGE])
        self.assertEqual(evidence["pull_request_number"], 4213)
        self.assertEqual(evidence["pull_request_head_ref_oid"], self.HEAD)
        self.assertEqual(evidence["pull_request_base_ref_name"], "dev")
        self.assertEqual(evidence["pull_request_merged_at"], "2026-07-26T20:18:15Z")
        # The branch name is only the lookup key.
        self.assertEqual(lookup.call_args.args[2], f"task/{self.TASK}")
        # The trailer is read off the squashed commit itself, not its ancestry.
        log_args = capture.call_args.args[1]
        self.assertIn("--no-walk", log_args)
        self.assertIn(f"--grep=Task-ID: {self.TASK}", log_args)
        self.assertIn(f"--since={self.SINCE}", log_args)
        self.assertEqual(log_args[-1], self.MERGE)

    def test_wrong_pr_head_is_not_this_delivery(self) -> None:
        evidence, _, _ = self._merged([self._pr(headRefOid="f" * 40)])
        self.assertIsNone(evidence)

    def test_wrong_base_branch_fails_closed(self) -> None:
        evidence, _, _ = self._merged([self._pr(baseRefName="master")])
        self.assertIsNone(evidence)

    def test_merge_before_the_worker_was_dispatched_fails_closed(self) -> None:
        evidence, _, _ = self._merged([self._pr(mergedAt="2026-07-26T10:00:00Z")])
        self.assertIsNone(evidence)

    def test_unmergeable_or_unparseable_merged_at_fails_closed(self) -> None:
        self.assertIsNone(self._merged([self._pr(mergedAt=None)])[0])
        self.assertIsNone(self._merged([self._pr(mergedAt="whenever")])[0])

    def test_pr_not_actually_merged_fails_closed(self) -> None:
        evidence, _, _ = self._merged([self._pr(state="OPEN", mergeCommit=None)])
        self.assertIsNone(evidence)

    def test_unrelated_merge_commit_fails_closed(self) -> None:
        """The recorded mergeCommit is not on the integration base."""

        def fake_ancestor(_root: object, _commit: str, _ref: str) -> bool:
            return False

        with (
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_commit_is_ancestor", side_effect=fake_ancestor),
            mock.patch.object(supervisor, "_merged_pull_requests_for_branch", return_value=[self._pr()]),
            mock.patch.object(supervisor, "_git_capture", return_value=f"{self.MERGE}\n"),
        ):
            self.assertIsNone(
                supervisor.merged_delivery_commits(
                    self.config, self.TASK, delivery_head=self.HEAD, since=self.SINCE
                )
            )

    def test_merge_commit_without_this_tasks_trailer_fails_closed(self) -> None:
        evidence, _, _ = self._merged([self._pr()], trailer="")
        self.assertIsNone(evidence)

    def test_missing_merge_commit_oid_fails_closed(self) -> None:
        self.assertIsNone(self._merged([self._pr(mergeCommit=None)])[0])
        self.assertIsNone(self._merged([self._pr(mergeCommit={"oid": "0410a89"})])[0])

    def test_deleted_branch_with_no_merged_pr_fails_closed(self) -> None:
        evidence, _, _ = self._merged([])
        self.assertIsNone(evidence)

    def test_github_lookup_failure_fails_closed(self) -> None:
        evidence, _, _ = self._merged(None)
        self.assertIsNone(evidence)

    def test_multiple_prs_for_the_task_resolve_by_exact_head(self) -> None:
        """Two merged PRs on the same task branch; only one delivered this head."""
        earlier = self._pr(number=4100, headRefOid="c" * 40, mergeCommit={"oid": "d" * 40})
        evidence, _, _ = self._merged([earlier, self._pr()])

        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["pull_request_number"], 4213)

    def test_ambiguous_duplicate_head_metadata_fails_closed(self) -> None:
        evidence, _, _ = self._merged([self._pr(), self._pr(number=4214)])
        self.assertIsNone(evidence)

    def test_squash_delivery_does_not_require_branch_ancestry(self) -> None:
        """The surviving branch is never an ancestor of the base after a squash.

        Only movement past the delivery head may block that shape, so the base
        comparison must be skipped for it.
        """
        with mock.patch.object(supervisor, "_git_ref_exists", return_value=False):
            self.assertFalse(
                supervisor.task_branch_has_unmerged_commits(
                    self.config, self.TASK, "", delivery_head=self.HEAD
                )
            )

        def fake_capture(_root: object, args: list[str]) -> str:
            return "4\n" if args[-1].startswith(f"{self.HEAD}..") else "0\n"

        with (
            mock.patch.object(
                supervisor,
                "_git_ref_exists",
                side_effect=lambda _root, ref: ref == f"task/{self.TASK}",
            ),
            mock.patch.object(supervisor, "_git_capture", side_effect=fake_capture),
        ):
            self.assertTrue(
                supervisor.task_branch_has_unmerged_commits(
                    self.config, self.TASK, "", delivery_head=self.HEAD
                )
            )

    def test_ancestry_shape_is_preferred_and_skips_the_pr_lookup(self) -> None:
        with (
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_commit_is_ancestor", return_value=True),
            mock.patch.object(supervisor, "_merged_pull_requests_for_branch") as lookup,
            mock.patch.object(supervisor, "_git_capture", return_value="abc123\n"),
        ):
            evidence = supervisor.merged_delivery_commits(
                self.config, self.TASK, delivery_head=self.HEAD, since=self.SINCE
            )

        self.assertEqual(evidence["delivery_shape"], "merge_ancestry")
        lookup.assert_not_called()


class MergedPullRequestLookupTests(unittest.TestCase):
    """The PR lookup itself: authoritative or nothing."""

    def setUp(self) -> None:
        self.config = {"paths": {"status_file": "ai-status.json"}}
        self.repo_root = Path("/repo")

    def _lookup(self, **patches: object):
        defaults = {
            "resolve_gh_binary": mock.patch.object(supervisor, "resolve_gh_binary", return_value="gh"),
            "_repository_slug_from_remote": mock.patch.object(
                supervisor, "_repository_slug_from_remote", return_value="ajoe734/pantheon"
            ),
        }
        defaults.update(patches)
        with contextlib.ExitStack() as stack:
            for patcher in defaults.values():
                stack.enter_context(patcher)
            return supervisor._merged_pull_requests_for_branch(
                self.config, self.repo_root, "task/SUP-001"
            )

    def _proc(self, returncode: int = 0, stdout: str = "[]") -> mock.Mock:
        return mock.Mock(returncode=returncode, stdout=stdout)

    def test_successful_lookup_returns_the_records(self) -> None:
        runner = mock.Mock(return_value=self._proc(stdout='[{"number": 1}]'))
        records = self._lookup(
            run_gh_process=mock.patch.object(supervisor, "run_gh_process", runner)
        )
        self.assertEqual(records, [{"number": 1}])
        args = runner.call_args.args[0]
        self.assertEqual(args[:2], ["pr", "list"])
        self.assertIn("--head", args)
        self.assertIn("task/SUP-001", args)
        self.assertIn("merged", args)
        self.assertIn("ajoe734/pantheon", args)

    def test_missing_gh_binary_fails_closed(self) -> None:
        self.assertIsNone(
            self._lookup(resolve_gh_binary=mock.patch.object(supervisor, "resolve_gh_binary", return_value=None))
        )

    def test_unknown_repository_fails_closed(self) -> None:
        self.assertIsNone(
            self._lookup(
                _repository_slug_from_remote=mock.patch.object(
                    supervisor, "_repository_slug_from_remote", return_value=None
                )
            )
        )

    def test_nonzero_exit_fails_closed(self) -> None:
        self.assertIsNone(
            self._lookup(
                run_gh_process=mock.patch.object(
                    supervisor, "run_gh_process", return_value=self._proc(returncode=1, stdout="")
                )
            )
        )

    def test_timeout_fails_closed(self) -> None:
        self.assertIsNone(
            self._lookup(
                run_gh_process=mock.patch.object(
                    supervisor,
                    "run_gh_process",
                    side_effect=subprocess.TimeoutExpired(cmd=["gh"], timeout=1),
                )
            )
        )

    def test_unparseable_or_unexpected_json_fails_closed(self) -> None:
        for payload in ("not json", '{"number": 1}'):
            self.assertIsNone(
                self._lookup(
                    run_gh_process=mock.patch.object(
                        supervisor, "run_gh_process", return_value=self._proc(stdout=payload)
                    )
                )
            )

    def test_disabled_lookup_fails_closed(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "ready_dispatcher": {"ownerless_in_progress": {"github_pr_lookup_enabled": False}},
        }
        with mock.patch.object(supervisor, "run_gh_process") as runner:
            self.assertIsNone(
                supervisor._merged_pull_requests_for_branch(config, self.repo_root, "task/SUP-001")
            )
        runner.assert_not_called()

    def test_repository_slug_is_read_from_the_origin_remote(self) -> None:
        for url in (
            "git@github.com:ajoe734/pantheon.git",
            "https://github.com/ajoe734/pantheon.git",
            "https://github.com/ajoe734/pantheon",
            "ssh://git@github.com/ajoe734/pantheon.git",
        ):
            with mock.patch.object(supervisor, "_git_capture", return_value=f"{url}\n"):
                self.assertEqual(
                    supervisor._repository_slug_from_remote(self.repo_root), "ajoe734/pantheon"
                )
        for url in ("", "git@gitlab.com:ajoe734/pantheon.git"):
            with mock.patch.object(supervisor, "_git_capture", return_value=url):
                self.assertIsNone(supervisor._repository_slug_from_remote(self.repo_root))


class WorkerDeliveryIdentityTests(unittest.TestCase):
    """Which worker, dispatched as whom, delivered which commit."""

    def setUp(self) -> None:
        self.config = {
            "agents": {
                "claude": {"display_name": "Claude"},
                "claude1_1": {"display_name": "Claude"},
                "codex2": {"display_name": "Codex2"},
            }
        }

    def test_logical_agent_id_resolves_the_dispatched_display_name(self) -> None:
        worker = {"logical_agent_id": "claude", "agent_id": "claude1_1", "provider": "claude1-1"}
        self.assertEqual(supervisor.worker_target_agent_display_name(self.config, worker), "Claude")

    def test_unregistered_agent_id_is_unresolved_not_echoed(self) -> None:
        worker = {"logical_agent_id": "ghost", "agent_id": "ghost_1", "provider": "ghost-1"}
        self.assertEqual(supervisor.worker_target_agent_display_name(self.config, worker), "")

    def test_delivery_head_requires_a_full_commit_sha(self) -> None:
        self.assertEqual(
            supervisor.worker_delivery_head_commit({"work_progress_snapshot": {"commit_sha": "C" * 40}}),
            "c" * 40,
        )
        self.assertIsNone(supervisor.worker_delivery_head_commit({"work_progress_snapshot": {}}))
        self.assertIsNone(
            supervisor.worker_delivery_head_commit({"work_progress_snapshot": {"commit_sha": "abc123"}})
        )
        self.assertIsNone(supervisor.worker_delivery_head_commit({"work_progress_snapshot": None}))

    def test_scraped_pr_url_is_never_the_delivery_binding(self) -> None:
        """Pinned from live .orchestrator/state.json at 2026-07-26T20:21Z.

        That worker was running SUP/OPS task work whose delivery head was
        8703d1f5d, while its scraped pr_url was a malformed string naming an
        unrelated PR. Any binding that trusted pr_url would bind the wrong PR.
        """
        worker = {
            "task_id": "OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001",
            "pr_url": 'https://github.com/ajoe734/pantheon/pull/4170\\"\\n',
            "work_progress_snapshot": {"commit_sha": "8703d1f5db76fc16f8c579177fc35dec4f526922"},
        }
        self.assertEqual(
            supervisor.worker_delivery_head_commit(worker),
            "8703d1f5db76fc16f8c579177fc35dec4f526922",
        )

    def test_dispatch_start_prefers_the_lease_acquisition(self) -> None:
        worker = {"lease_acquired_at": "2026-07-26T17:00:00Z", "started_at": "2026-07-26T10:00:00Z"}
        self.assertEqual(
            supervisor._isoformat_utc(supervisor.worker_dispatch_started_at(worker)),
            "2026-07-26T17:00:00Z",
        )
        self.assertIsNone(supervisor.worker_dispatch_started_at({"lease_acquired_at": "not-a-date"}))
        self.assertIsNone(supervisor.worker_dispatch_started_at({}))


if __name__ == "__main__":
    unittest.main()
