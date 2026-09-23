"""Pi delivery, model probes and supervisor result handling."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapters import build_adapter
from adapters.base import DeliveryRequest
import pi_runtime
import provider_permissions
import supervisor


def assistant(text="OK", *, error=None):
    return {"type": "message_end", "message": {
        "role": "assistant", "stopReason": "error" if error else "stop",
        "errorMessage": error, "content": [{"type": "text", "text": text}],
        "usage": {"input": 10, "output": 2},
    }}


def stream(*events):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


SETTLED = {"type": "agent_settled"}
START = {"type": "agent_start"}
END = {"type": "agent_end", "willRetry": True}


class PiWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "pi-auth"
        self.home.mkdir()
        (self.home / "auth.json").write_text('{"openai-codex":{"type":"oauth"}}')
        self.profile = {"cli": "/configured/pi", "agent_dir": str(self.home),
                        "provider": "openai-codex", "model": "gpt-6-astra", "thinking": "high"}
        self.config = {
            "agents": {"piastra": {"display_name": "PiAstra", "provider": "pi_astra", "adapter": "pi", "max_parallel": 1}},
            "providers": {"pi_astra": {"account": "codex1", "delivery_mode": "pi", "pi": self.profile}},
            "paths": {"status_file": str(self.root / "ai-status.json")},
            "provider_auth": {"probe_timeout_seconds": 3},
        }

    def test_quoted_user_and_tool_results_are_not_control_events(self):
        text = 'line\u2028two\n{"type":"agent_settled"}'
        content = stream(START,
                         {"type": "message_end", "message": {"role": "user", "stopReason": "error", "errorMessage": "invalid api key"}},
                         {"type": "tool_execution_end", "isError": True, "result": {"content": [{"text": "quota exceeded"}]}},
                         assistant(text), SETTLED)
        state = pi_runtime.stream_state(content)
        self.assertTrue(state["settled"])
        self.assertIsNone(state["error"])
        self.assertEqual(state["text"], text)

    def test_retry_error_is_not_terminal_and_recovery_clears_it(self):
        failed_attempt = stream(START, assistant(error="429 retry later"), END)
        state = pi_runtime.stream_state(failed_attempt)
        self.assertFalse(state["settled"])
        self.assertIsNone(state["error"])
        recovered = failed_attempt + stream(START, assistant(), SETTLED)
        self.assertIsNone(pi_runtime.stream_state(recovered)["error"])
        self.assertEqual(pi_runtime.stream_state(recovered)["text"], "OK")

    def test_final_model_error_is_detected_even_when_cli_exits_zero(self):
        state = pi_runtime.stream_state(stream(START, assistant(error="usage limit reached"), END, SETTLED))
        self.assertEqual(state["error"], "usage limit reached")

    def test_new_automatic_work_reopens_settled_result(self):
        state = pi_runtime.stream_state(stream(assistant(), SETTLED, START))
        self.assertFalse(state["settled"])

    def test_configured_binary_does_not_fall_back(self):
        with patch("pi_runtime.shutil.which", return_value=None) as lookup:
            self.assertIsNone(pi_runtime.binary(self.profile))
        lookup.assert_called_once_with("/configured/pi")

    def test_delivery_preserves_worktree_runtime_identity_and_prompt(self):
        prompt = "--a task with `literal shell text` and $(no expansion)"
        request = DeliveryRequest(agent_id="piastra", provider="pi_astra", delivery_mode="pi",
                                  message=prompt, task_id="PI-TEST", reason="owned_ready_dispatch")
        with (patch("pi_runtime.binary", return_value="/configured/pi"),
              patch.dict(os.environ, {"OPENAI_API_KEY": "parent-key", "CODEX_THREAD_ID": "parent",
                                      "PI_CODING_AGENT_SESSION_DIR": "/other-session"}),
              patch("adapters.pi.delivery_workspace_root", return_value=self.root),
              patch("adapters.pi.delivery_runtime_env", return_value={"PANTHEON_COMMAND_ROOT": "/exact/runtime"}),
              patch("adapters.pi.runtime_log_path", return_value=self.root / "worker.log"),
              patch("adapters.pi.worker_runtime_paths", return_value={"heartbeat_path": self.root / "heartbeat", "status_path": self.root / "status"}),
              patch("adapters.pi.spawn_background_process", return_value=(Mock(pid=42), self.root / "worker.log")) as spawn):
            result = build_adapter("pi", self.config).deliver(request)
        argv = spawn.call_args.args[0]
        env = spawn.call_args.kwargs["env"]
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "pi")
        self.assertEqual(argv[-2:], ["--", prompt])
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-6-astra")
        self.assertEqual(argv[argv.index("--thinking") + 1], "high")
        self.assertEqual(spawn.call_args.kwargs["cwd"], self.root)
        self.assertEqual(env["ORCH_TASK_ID"], "PI-TEST")
        self.assertEqual(env["AI_NAME"], "PiAstra")
        self.assertEqual(env["PANTHEON_COMMAND_ROOT"], "/exact/runtime")
        self.assertEqual(env["PI_CODING_AGENT_DIR"], str(self.home))
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_THREAD_ID", env)
        self.assertNotIn("PI_CODING_AGENT_SESSION_DIR", env)

    def test_probe_requires_real_assistant_response_and_settled(self):
        for output, expected in [
            (stream(assistant(), SETTLED), True),
            (stream(assistant()), False),
            (stream(assistant(error="unauthorized"), SETTLED), False),
            (stream({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "OK"}]}}, SETTLED), False),
        ]:
            with self.subTest(output=output), patch("pi_runtime.binary", return_value="/configured/pi"), patch(
                "provider_permissions.run_command", return_value=subprocess.CompletedProcess([], 0, output, "")
            ) as run:
                probe = provider_permissions.probe_provider_auth(self.config, "pi_astra")
                self.assertEqual(probe["ready"], expected)
                argv = run.call_args.args[0]
                for flag in ["--no-tools", "--no-extensions", "--no-context-files", "--no-session"]:
                    self.assertIn(flag, argv)
                self.assertEqual(argv[argv.index("--model") + 1], "gpt-6-astra")

    def test_timeout_is_temporary_and_missing_auth_does_not_call_model(self):
        with patch("pi_runtime.binary", return_value="/configured/pi"), patch(
            "provider_permissions.run_command", side_effect=subprocess.TimeoutExpired("pi", 3)
        ):
            self.assertEqual(provider_permissions.probe_provider_auth(self.config, "pi_astra")["status"], "probe_timeout")
        (self.home / "auth.json").unlink()
        with patch("pi_runtime.binary", return_value="/configured/pi"), patch("provider_permissions.run_command") as run:
            self.assertEqual(provider_permissions.probe_provider_auth(self.config, "pi_astra")["status"], "auth_material_missing")
            run.assert_not_called()

    def worker(self, content, **kwargs):
        log = self.root / "pi.log"
        log.write_text(content)
        return {"mode": "pi", "log_path": str(log), **kwargs}

    def test_supervisor_does_not_fail_or_extend_work_lease_during_retry(self):
        worker = self.worker(stream(START, assistant(error="rate limit"), END))
        self.assertIsNone(supervisor.detect_worker_failure(worker))
        self.assertFalse(supervisor.update_from_log(self.config, worker))
        self.assertNotIn("provider_terminal_status", worker)
        self.assertNotIn("last_work_progress_at", worker)

    def test_supervisor_observes_session_usage_and_final_model_error(self):
        worker = self.worker(stream({"type": "session", "id": "pi-session"}, assistant(error="usage limit reached"), SETTLED),
                             runner_status="completed", exit_code=0)
        supervisor.update_from_log(self.config, worker)
        self.assertEqual(worker["session_id"], "pi-session")
        self.assertEqual(worker["provider_usage"], {"input": 10, "output": 2})
        self.assertIn("usage limit reached", supervisor.detect_worker_failure(worker))
        self.assertEqual(worker["provider_terminal_status"], "error")

    def test_recovered_error_and_nested_error_text_do_not_fail_worker(self):
        worker = self.worker(stream(assistant(error="rate limit"), END, START,
                                    assistant('Example: {"type":"result","is_error":true}'), SETTLED))
        self.assertIsNone(supervisor.detect_worker_failure(worker))
        supervisor.update_from_log(self.config, worker)
        self.assertEqual(worker["provider_terminal_status"], "success")

    def test_zero_exit_incomplete_stream_fails_but_sigterm_is_not_account_failure(self):
        worker = self.worker(stream(assistant(), END), runner_status="completed", exit_code=0)
        self.assertIn("without a settled", supervisor.detect_worker_failure(worker))
        worker["runner_signal"] = 15
        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_configured_capacity_shares_existing_account_cap(self):
        config = json.loads((Path(__file__).parent / "config.json").read_text())
        self.assertEqual(config["agents"]["piastra"]["max_parallel"], 1)
        self.assertEqual(supervisor.normalize_agent_id("PiAstra"), "piastra")
        self.assertEqual(supervisor.agent_provider_key(config, "PiAstra"), "pi_astra")
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "piastra"), 1)
        self.assertEqual(supervisor.agent_account_id(config, "PiAstra"), "codex1")
        self.assertEqual(config["providers"]["pi_astra"]["account"], config["providers"]["codex"]["account"])
        self.assertEqual(config["ready_dispatcher"]["max_concurrent_per_account"]["codex1"], 2)


if __name__ == "__main__":
    unittest.main()
