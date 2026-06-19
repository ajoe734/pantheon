"""Live smoke test for the OpenClaw assistant provider.

Gate: only runs when OPENCLAW_GATEWAY_URL and OPENCLAW_GATEWAY_TOKEN are set
and the `openclaw` binary is available.  Skips cleanly otherwise so that CI
without a gateway deployment stays green.

This test is the live evidence required by OPENCLAW-AGENT-TURN-LIVE-FIX
acceptance criterion 1: "adapter sends a real prompt and receives a real model
reply (non-mock, non-dry-run)".

Run locally:
  OPENCLAW_GATEWAY_URL=ws://openclaw-gateway:18789 \\
  OPENCLAW_GATEWAY_TOKEN=pantheon-local-token \\
  python -m pytest services/openclaw-gateway-adapter/test_assistant_openclaw_provider_live.py -v

The sentinel phrase "OPENCLAW_LIVE" confirms the model processed the prompt
through the actual gateway agent, not a mock.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

_ADAPTER_DIR = str(Path(__file__).resolve().parent)
if _ADAPTER_DIR not in sys.path:
    sys.path.insert(0, _ADAPTER_DIR)

from assistant_openclaw_provider import AssistantOpenClawProvider, OpenClawProviderError


def _gateway_available() -> bool:
    url = os.getenv("OPENCLAW_GATEWAY_URL", "").strip()
    token = os.getenv("OPENCLAW_GATEWAY_TOKEN", "").strip()
    bin_path = os.getenv("OPENCLAW_BIN", "").strip() or shutil.which("openclaw")
    return bool(url and token and bin_path)


@unittest.skipUnless(
    _gateway_available(),
    "Skipping live gateway smoke: OPENCLAW_GATEWAY_URL, OPENCLAW_GATEWAY_TOKEN, "
    "and openclaw binary must all be present",
)
class TestAssistantOpenClawProviderLive(unittest.TestCase):
    """Live smoke tests that require a deployed gateway."""

    def _provider(self) -> AssistantOpenClawProvider:
        return AssistantOpenClawProvider()

    def test_provider_configured(self) -> None:
        provider = self._provider()
        self.assertTrue(provider.configured, "Provider must be configured when env vars are set")

    def test_readiness_live(self) -> None:
        provider = self._provider()
        info = provider.readiness(auth_probe=True)
        self.assertTrue(info.get("ready"), f"readiness must be ready: {info}")
        self.assertEqual(info["status"], "ready")

    def test_live_agent_turn(self) -> None:
        """Send a real prompt and verify the gateway returns a non-empty reply.

        The prompt asks the agent to echo a sentinel string so the test can
        assert the reply is live (not mocked or synthesized locally).
        """
        provider = self._provider()
        result = provider.invoke(
            "Reply with exactly: OPENCLAW_LIVE",
            mode="user",
            operator_id="smoke-test",
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.provider, "openclaw")
        reply_text: str = ""
        for event in result.output.get("json_events", []):
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                reply_text = item.get("text", "")
                break
        self.assertIn(
            "OPENCLAW_LIVE",
            reply_text,
            f"Expected sentinel 'OPENCLAW_LIVE' in agent reply but got: {reply_text!r}",
        )
        self.assertEqual(result.output.get("transport"), "cli", "Transport must be 'cli' (not mock/REST)")

    def test_live_turn_redaction_envelope(self) -> None:
        """Verify the result envelope structure expected by the BFF."""
        provider = self._provider()
        result = provider.invoke("Say hello briefly.", mode="user", operator_id="smoke-test")
        d = result.to_dict()
        self.assertEqual(d["provider"], "openclaw")
        self.assertIn("output", d)
        self.assertIn("json_events", d["output"])
        self.assertIn("transport", d["output"])


class TestAssistantOpenClawProviderUnit(unittest.TestCase):
    """Unit tests that run without a live gateway (mock subprocess)."""

    def _make_provider(self, *, run_func=None, which_func=None) -> AssistantOpenClawProvider:
        return AssistantOpenClawProvider(
            gateway_url="ws://openclaw-gateway:18789",
            agent_id="main",
            token="test-token",
            _which_func=which_func or (lambda _: "/usr/local/bin/openclaw"),
            _run_func=run_func,
        )

    def test_readiness_no_binary_auth_probe(self) -> None:
        """auth_probe=True checks binary existence and returns degraded if missing."""
        provider = AssistantOpenClawProvider(
            gateway_url="ws://openclaw-gateway:18789",
            token="tok",
            _which_func=lambda _: None,
        )
        info = provider.readiness(auth_probe=True)
        self.assertFalse(info["ready"])
        self.assertIn("binary", info["reason"])

    def test_readiness_no_token_auth_probe(self) -> None:
        """auth_probe=True checks token and returns degraded if missing."""
        provider = AssistantOpenClawProvider(
            gateway_url="ws://openclaw-gateway:18789",
            _which_func=lambda _: "/usr/local/bin/openclaw",
        )
        info = provider.readiness(auth_probe=True)
        self.assertFalse(info["ready"])
        self.assertIn("TOKEN", info["reason"])

    def test_readiness_configured(self) -> None:
        provider = self._make_provider()
        info = provider.readiness()
        self.assertTrue(info["ready"])
        self.assertEqual(info["status"], "ready")

    @staticmethod
    def _agent_json(text: str) -> str:
        """Mimic `openclaw agent --json` (2026.6.8) stdout for a reply."""
        return json.dumps(
            {
                "runId": "run_test",
                "status": "ok",
                "result": {
                    "payloads": [{"type": "text", "text": text}],
                    "meta": {"finalAssistantVisibleText": text, "finalAssistantRawText": text},
                },
            }
        )

    def test_invoke_success(self) -> None:
        reply = "Hello from mock OpenClaw agent"

        def fake_run(cmd, **_kw):
            class R:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json(reply)
                stderr = ""
            return R()

        provider = self._make_provider(run_func=fake_run)
        result = provider.invoke("Say hello", mode="user", operator_id="op-1")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.provider, "openclaw")
        events = result.output.get("json_events", [])
        self.assertTrue(events)
        self.assertEqual(events[0]["item"]["text"], reply)
        self.assertEqual(result.output["transport"], "cli")

    def test_invoke_cli_args_and_env(self) -> None:
        """CLI gets `agent --agent --message --json` (no --url/--token); the ws
        URL + token are supplied via the subprocess env the CLI reads."""
        captured: list[list[str]] = []
        captured_env: list[dict] = []
        captured_input: list = []

        def fake_run(cmd, **kw):
            captured.append(list(cmd))
            captured_env.append(dict(kw.get("env") or {}))
            captured_input.append(kw.get("input"))
            class R:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json("response")
                stderr = ""
            return R()

        big_prompt = "test " + ("x" * 200_000)  # would overflow a single argv
        provider = self._make_provider(run_func=fake_run)
        provider.invoke(big_prompt, operator_id="op-1")
        self.assertTrue(captured, "subprocess.run was never called")
        cmd = captured[0]
        self.assertIn("agent", cmd)
        self.assertIn("--agent", cmd)
        self.assertIn("main", cmd)
        self.assertIn("--message", cmd)
        self.assertIn("--json", cmd)
        # The prompt is fed via stdin (`--message -`), never as an argv string
        # (a large prompt as argv overflows MAX_ARG_STRLEN -> "Argument list too long").
        self.assertIn("-", cmd)
        self.assertNotIn(big_prompt, cmd)
        self.assertEqual(captured_input[0], big_prompt)
        # The agent subcommand does NOT accept --url/--token.
        self.assertNotIn("--url", cmd)
        self.assertNotIn("--token", cmd)
        # URL + token travel via the environment instead.
        env = captured_env[0]
        self.assertEqual(env.get("OPENCLAW_GATEWAY_URL"), "ws://openclaw-gateway:18789")
        self.assertEqual(env.get("OPENCLAW_GATEWAY_TOKEN"), "test-token")

    def test_invoke_non_zero_exit_raises(self) -> None:
        def fake_run(cmd, **_kw):
            class R:
                returncode = 1
                stdout = ""
                stderr = "Connection refused"
            return R()

        provider = self._make_provider(run_func=fake_run)
        with self.assertRaises(OpenClawProviderError) as ctx:
            provider.invoke("test", operator_id="op-1")
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_GATEWAY_INVOCATION_FAILED")

    def test_http_url_converted_to_ws(self) -> None:
        """If an http:// URL was set (old broken config), normalize it to ws://."""
        provider = AssistantOpenClawProvider(
            gateway_url="http://openclaw-gateway:18789",
            token="tok",
            _which_func=lambda _: "/usr/local/bin/openclaw",
        )
        self.assertTrue(provider._gateway_url.startswith("ws://"))

    def test_no_gateway_url_invoke_uses_default(self) -> None:
        """When no URL is set explicitly, invoke falls back to the canonical WS address."""
        captured: list[list[str]] = []
        captured_env: list[dict] = []

        def fake_run(cmd, **kw):
            captured.append(list(cmd))
            captured_env.append(dict(kw.get("env") or {}))
            class R:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json("ok")
                stderr = ""
            return R()

        provider = AssistantOpenClawProvider(
            gateway_url="",
            token="tok",
            _which_func=lambda _: "/usr/local/bin/openclaw",
            _run_func=fake_run,
        )
        self.assertFalse(provider.configured)
        provider.invoke("test", operator_id="op-1")
        # URL is not a CLI arg; the default WS address is exported via env.
        self.assertEqual(
            captured_env[0].get("OPENCLAW_GATEWAY_URL"),
            "ws://openclaw-gateway:18789",
            "Default WS URL must be exported to the CLI env",
        )


if __name__ == "__main__":
    unittest.main()
