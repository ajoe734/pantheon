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
from adapters.claude_cli import ClaudeCLIAdapter
from adapters.copilot_local import CopilotLocalAdapter
from adapters.gemini import GeminiAdapter
from adapters.qwen import QwenAdapter


class AdapterFallbackPolicyTests(unittest.TestCase):
    def test_claude_can_disable_inbox_fallback(self) -> None:
        config = {
            "providers": {
                "claude": {
                    "allow_inbox_fallback": False,
                    "runtime": {"cli": "claude"},
                }
            }
        }
        request = DeliveryRequest(agent_id="claude", provider="claude", delivery_mode="claude_cli", message="wake")
        adapter = ClaudeCLIAdapter(config=config, provider_capabilities={})
        with (
            mock.patch("adapters.claude_cli._configured_claude_cli", return_value=None),
            mock.patch("adapters.claude_cli._claude_auth_ready", return_value=False),
        ):
            result = adapter.deliver(request)
        self.assertFalse(result.ok)
        self.assertFalse(result.manual_confirmation_required)
        self.assertEqual(result.mode, "claude_cli")

    def test_claude_alias_uses_provider_specific_home_env(self) -> None:
        config = {
            "agents": {
                "claude2": {
                    "id": "claude2",
                    "display_name": "Claude2",
                    "provider": "claude2",
                    "adapter": "claude_cli",
                }
            },
            "paths": {"status_file": "ai-status.json", "claude_mcp_config": ".orchestrator/claude-approval-broker.mcp.json"},
            "providers": {
                "claude2": {
                    "allow_inbox_fallback": False,
                    "runtime": {
                        "cli": ".orchestrator/bin/claude",
                        "home": "~/.claude2",
                        "output_format": "stream-json",
                        "include_hook_events": True,
                    },
                }
            },
        }
        request = DeliveryRequest(agent_id="claude2", provider="claude2", delivery_mode="claude_cli", message="wake")
        adapter = ClaudeCLIAdapter(config=config, provider_capabilities={"providers": {"claude": {"supports_auto_approve": True}}})
        fake_process = mock.Mock(pid=1234)
        with (
            mock.patch("adapters.claude_cli._configured_claude_cli", return_value=".orchestrator/bin/claude"),
            mock.patch("adapters.claude_cli._claude_auth_ready", return_value=True),
            mock.patch("adapters.claude_cli.spawn_background_process", return_value=(fake_process, Path("/tmp/claude2.log"))) as spawn,
        ):
            result = adapter.deliver(request)

        self.assertTrue(result.ok)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["HOME"], os.path.expanduser("~/.claude2"))
        self.assertEqual(env["ORCH_PROVIDER"], "claude2")
        self.assertIn("--permission-mode", result.command)

    def test_gemini_can_disable_inbox_fallback(self) -> None:
        config = {
            "agents": {"gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"}},
            "providers": {
                "gemini": {
                    "allow_inbox_fallback": False,
                    "gemini": {"cli": "gemini"},
                }
            },
        }
        request = DeliveryRequest(agent_id="gemini", provider="gemini", delivery_mode="gemini", message="wake")
        adapter = GeminiAdapter(config=config, provider_capabilities={})
        with mock.patch("adapters.gemini.command_exists", return_value=None):
            result = adapter.deliver(request)
        self.assertFalse(result.ok)
        self.assertFalse(result.manual_confirmation_required)
        self.assertEqual(result.mode, "gemini")

    def test_copilot_can_disable_inbox_fallback(self) -> None:
        config = {
            "providers": {
                "copilot": {
                    "allow_inbox_fallback": False,
                    "local": {"cli": "copilot"},
                    "cloud": {"cli": "gh"},
                }
            }
        }
        request = DeliveryRequest(agent_id="copilot", provider="copilot", delivery_mode="copilot_local", message="wake")
        adapter = CopilotLocalAdapter(config=config, provider_capabilities={})
        with (
            mock.patch("adapters.copilot_local._configured_copilot_cli", return_value=None),
            mock.patch("adapters.copilot_local._copilot_auth_ready", return_value=False),
        ):
            result = adapter.deliver(request)
        self.assertFalse(result.ok)
        self.assertFalse(result.manual_confirmation_required)
        self.assertEqual(result.mode, "copilot_local")

    def test_qwen_can_disable_inbox_fallback(self) -> None:
        config = {
            "agents": {"qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"}},
            "providers": {
                "qwen": {
                    "allow_inbox_fallback": False,
                    "qwen": {"cli": "qwen"},
                }
            },
        }
        request = DeliveryRequest(agent_id="qwen", provider="qwen", delivery_mode="qwen", message="wake")
        adapter = QwenAdapter(config=config, provider_capabilities={})
        with mock.patch("adapters.qwen.command_exists", return_value=None):
            result = adapter.deliver(request)
        self.assertFalse(result.ok)
        self.assertFalse(result.manual_confirmation_required)
        self.assertEqual(result.mode, "qwen")


if __name__ == "__main__":
    unittest.main()
