from __future__ import annotations

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
