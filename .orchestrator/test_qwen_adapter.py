from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from adapters.base import DeliveryRequest
from adapters.qwen import QwenAdapter


class QwenAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "paths": {
                "status_file": "ai-status.json",
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
                    "qwen": {
                        "cli": "qwen",
                        "approval_mode": "yolo",
                        "include_directories": True,
                        "output_format": "stream-json",
                        "include_partial_messages": False,
                        "channel": "CI",
                        "auth_type": "openai",
                        "model": "qwen3-coder-plus",
                        "openai_api_key_env": "OPENAI_API_KEY",
                        "openai_base_url_env": "OPENAI_BASE_URL",
                        "extra_args": ["--debug"],
                    }
                }
            },
        }
        self.request = DeliveryRequest(
            agent_id="qwen",
            provider="qwen",
            delivery_mode="qwen",
            message="wake up",
            task_id="OPS-999",
        )

    @mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "http://127.0.0.1:8000/v1"}, clear=False)
    @mock.patch("adapters.qwen.spawn_background_process")
    @mock.patch("adapters.qwen.runtime_log_path", return_value=Path("/tmp/qwen.log"))
    @mock.patch("adapters.qwen.new_runtime_id", return_value="qwen-20260409T000000Z-test")
    @mock.patch("adapters.qwen.command_exists", return_value="/usr/bin/qwen")
    @mock.patch("adapters.qwen.run_command")
    def test_qwen_command_uses_standalone_cli(
        self,
        run_command: mock.Mock,
        _command_exists: mock.Mock,
        _new_runtime_id: mock.Mock,
        _runtime_log_path: mock.Mock,
        spawn_background_process: mock.Mock,
    ) -> None:
        run_command.return_value = mock.Mock(stdout="", stderr="No authentication method configured", returncode=0)
        process = mock.Mock()
        process.pid = 43210
        spawn_background_process.return_value = (process, Path("/tmp/qwen.log"))

        adapter = QwenAdapter(config=self.config, provider_capabilities={})
        result = adapter.deliver(self.request)

        self.assertTrue(result.ok)
        command = spawn_background_process.call_args.args[0]
        env = spawn_background_process.call_args.kwargs["env"]
        self.assertEqual(command[0], "qwen")
        self.assertIn("--approval-mode", command)
        self.assertIn("yolo", command)
        self.assertIn("--output-format", command)
        self.assertIn("stream-json", command)
        self.assertIn("--model", command)
        self.assertIn("qwen3-coder-plus", command)
        self.assertIn("--debug", command)
        self.assertEqual(env["OPENAI_API_KEY"], "test-key")
        self.assertEqual(env["OPENAI_BASE_URL"], "http://127.0.0.1:8000/v1")


if __name__ == "__main__":
    unittest.main()
