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
import socket
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_gateway_cron_call_shells_out_and_parses(self) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd, **_kw):
            captured.append(list(cmd))
            class R:
                returncode = 0
                stdout = 'banner line\n{"id": "job-1", "name": "n"}'
                stderr = ""
            return R()

        provider = self._make_provider(run_func=fake_run)
        out = provider.gateway_cron_call("cron.add", {"name": "n"})
        self.assertEqual(out, {"id": "job-1", "name": "n"})
        cmd = captured[0]
        self.assertEqual(cmd[1:4], ["gateway", "call", "cron.add"])
        self.assertIn("--url", cmd)
        self.assertIn("--token", cmd)
        self.assertIn("--params", cmd)

    def test_gateway_agents_list_reads_only_the_live_registry(self) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd, **_kw):
            captured.append(list(cmd))

            class R:
                returncode = 0
                stdout = '{"agents":[{"id":"main"},{"id":"persona-opinion-a"}]}'
                stderr = ""

            return R()

        provider = self._make_provider(run_func=fake_run)

        self.assertEqual(
            provider.gateway_agents_list(),
            [{"id": "main"}, {"id": "persona-opinion-a"}],
        )
        self.assertEqual(captured[0][1:4], ["gateway", "call", "agents.list"])
        self.assertNotIn("--params", captured[0])

    def test_gateway_agents_list_rejects_invalid_live_payload(self) -> None:
        def fake_run(_cmd, **_kw):
            class R:
                returncode = 0
                stdout = '{"agents":"not-a-list"}'
                stderr = ""

            return R()

        provider = self._make_provider(run_func=fake_run)
        with self.assertRaises(OpenClawProviderError) as ctx:
            provider.gateway_agents_list()
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_GATEWAY_SERIALIZATION_FAILURE")

    def test_gateway_agents_list_caps_subprocess_to_remaining_budget(self) -> None:
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["timeout"] = kwargs["timeout"]

            class R:
                returncode = 0
                stdout = '{"agents":[]}'
                stderr = ""

            return R()

        provider = self._make_provider(run_func=fake_run)
        provider.gateway_agents_list(timeout_seconds=0.75)

        self.assertEqual(captured["timeout"], 0.75)
        timeout_index = captured["cmd"].index("--timeout") + 1
        self.assertGreater(int(captured["cmd"][timeout_index]), 750)

    def test_gateway_agents_list_maps_blocked_probe_timeout(self) -> None:
        def blocked(_cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd="openclaw gateway call agents.list", timeout=kwargs["timeout"])

        provider = self._make_provider(run_func=blocked)
        with self.assertRaises(OpenClawProviderError) as ctx:
            provider.gateway_agents_list(timeout_seconds=0.25)

        self.assertEqual(ctx.exception.status_code, 504)
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_GATEWAY_TIMEOUT")

    def test_gateway_cron_update_forwards_command_and_params_unchanged(self) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd, **_kw):
            captured.append(list(cmd))

            class R:
                returncode = 0
                stdout = '{"id":"job-1","updated":true}'
                stderr = ""

            return R()

        params = {
            "id": "job-1",
            "patch": {
                "enabled": False,
                "payload": {
                    "kind": "systemEvent",
                    "text": '{"persona_id":"persona-1","workflow_id":"pantheon.review"}',
                },
                "schedule": {"kind": "cron", "expr": "*/30 * * * *"},
            },
        }
        original_params = json.loads(json.dumps(params))
        provider = self._make_provider(run_func=fake_run)

        out = provider.gateway_cron_call("cron.update", params)

        self.assertEqual(out, {"id": "job-1", "updated": True})
        self.assertEqual(params, original_params, "provider must not mutate caller params")
        cmd = captured[0]
        self.assertEqual(cmd[1:4], ["gateway", "call", "cron.update"])
        params_index = cmd.index("--params") + 1
        self.assertEqual(json.loads(cmd[params_index]), original_params)

    def test_gateway_cron_call_parses_banner_prefixed_pretty_json(self) -> None:
        """Banner/doctor noise + pretty-printed multi-line JSON must still parse."""
        noisy = (
            "[state-migrations] Legacy state migration warnings:\n"
            "- Left migrated task registry sidecar in place\n"
            "│  Doctor warnings box  │\n"
            '{\n  "jobs": [],\n  "total": 0,\n  "hasMore": false\n}\n'
        )

        def fake_run(cmd, **_kw):
            class R:
                returncode = 0
                stdout = noisy
                stderr = ""
            return R()

        provider = self._make_provider(run_func=fake_run)
        out = provider.gateway_cron_call("cron.list", {"limit": 5})
        self.assertEqual(out.get("total"), 0)
        self.assertEqual(out.get("jobs"), [])

    def test_extract_gateway_json_wraps_top_level_array(self) -> None:
        out = AssistantOpenClawProvider._extract_gateway_json('noise\n[{"id": "main"}]')
        self.assertEqual(out, {"result": [{"id": "main"}]})

    def test_gateway_cron_call_rejects_non_cron_method(self) -> None:
        provider = self._make_provider(run_func=lambda *a, **k: None)
        with self.assertRaises(OpenClawProviderError) as ctx:
            provider.gateway_cron_call("agent.invoke", {})
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_GATEWAY_METHOD_FORBIDDEN")

    def test_gateway_cron_call_rejects_unknown_cron_method(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **_kw):
            calls.append(list(cmd))
            raise AssertionError("forbidden methods must not reach the subprocess")

        provider = self._make_provider(run_func=fake_run)
        with self.assertRaises(OpenClawProviderError) as ctx:
            provider.gateway_cron_call("cron.unknown", {"id": "job-1"})
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_GATEWAY_METHOD_FORBIDDEN")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(calls, [])

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
        self.assertFalse(info["ready"])
        self.assertEqual(info["status"], "not_checked")
        self.assertEqual(info["reason"], "answer_probe_not_run")

    def test_readiness_auth_probe_requires_bounded_agent_answer(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["timeout"] = kwargs["timeout"]

            class R:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json(
                    "PANTHEON_PROVIDER_READY"
                )
                stderr = ""

            return R()

        provider = self._make_provider(run_func=fake_run)
        info = provider.readiness(auth_probe=True)

        self.assertTrue(info["ready"])
        self.assertEqual(info["status"], "ready")
        self.assertEqual(info["answer_probe"]["status"], "completed")
        self.assertEqual(info["answer_probe"]["deadline_seconds"], 20.0)
        self.assertLessEqual(captured["timeout"], 20.0)
        self.assertGreaterEqual(captured["timeout"], 5.0)
        command = captured["cmd"]
        self.assertEqual(command[1], "agent")
        self.assertIn("PANTHEON_PROVIDER_READY", command[command.index("--message") + 1])

    def test_readiness_auth_probe_converges_via_fallback_when_primary_claude_fails(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            # First call is primary (anthropic/claude-opus-4-8) which fails with timeout
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(cmd="openclaw agent", timeout=kwargs["timeout"])
            # Second call is fallback (openai/gpt-5.6-sol) which succeeds
            class R:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json("PANTHEON_PROVIDER_READY")
                stderr = ""
            return R()

        provider = self._make_provider(run_func=fake_run)
        info = provider.readiness(auth_probe=True)

        self.assertTrue(info["ready"])
        self.assertEqual(info["status"], "ready")
        self.assertEqual(info["active_model"], "openai/gpt-5.6-sol")
        self.assertEqual(info["primary_model"], "anthropic/claude-opus-4-8")
        self.assertTrue(info["fallback_used"])
        self.assertEqual(info["primary_unavailable"]["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(info["primary_unavailable"]["status"], "unavailable")
        self.assertEqual(info["primary_unavailable"]["reason"], "OPENCLAW_GATEWAY_TIMEOUT")
        self.assertEqual(info["answer_probe"]["status"], "completed")
        self.assertEqual(info["answer_probe"]["active_model"], "openai/gpt-5.6-sol")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][calls[0].index("--model") + 1], "anthropic/claude-opus-4-8")
        self.assertEqual(calls[1][calls[1].index("--model") + 1], "openai/gpt-5.6-sol")

    def test_readiness_auth_probe_fails_closed_when_all_models_fail(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 1
                stdout = ""
                stderr = "auth session expired"
            return R()

        provider = self._make_provider(run_func=fake_run)
        info = provider.readiness(auth_probe=True)

        self.assertFalse(info["ready"])
        self.assertEqual(info["status"], "degraded")
        self.assertEqual(info["reason"], "OPENCLAW_AUTH_UNAVAILABLE")
        self.assertEqual(info["primary_unavailable"]["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(info["primary_unavailable"]["status"], "unavailable")
        self.assertEqual(info["primary_unavailable"]["reason"], "OPENCLAW_AUTH_UNAVAILABLE")
        self.assertEqual(info["answer_probe"]["status"], "failed")
        self.assertEqual(len(calls), 3)  # primary + 2 fallbacks

    def test_readiness_primary_unavailable_evidence_is_sanitized(self) -> None:
        def fake_run(cmd, **kwargs):
            class R:
                returncode = 1
                stdout = ""
                stderr = "error with token=sk-ant-secret-key-12345 login expired"
            return R()

        provider = self._make_provider(run_func=fake_run)
        info = provider.readiness(auth_probe=True)

        sanitized_reason = info["primary_unavailable"]["reason"]
        self.assertNotIn("secret", sanitized_reason)
        self.assertNotIn("12345", sanitized_reason)
        self.assertEqual(sanitized_reason, "OPENCLAW_AUTH_UNAVAILABLE")

    def test_readiness_auth_probe_fails_when_reply_has_wrong_sentinel(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json("WRONG_SENTINEL")
                stderr = ""
            return R()

        provider = self._make_provider(run_func=fake_run)
        info = provider.readiness(auth_probe=True)

        self.assertFalse(info["ready"])
        self.assertEqual(info["status"], "degraded")
        self.assertEqual(info["reason"], "openclaw_answer_probe_sentinel_mismatch")
        self.assertEqual(info["primary_unavailable"]["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(info["primary_unavailable"]["status"], "unavailable")
        self.assertEqual(info["primary_unavailable"]["reason"], "openclaw_answer_probe_sentinel_mismatch")
        self.assertEqual(info["answer_probe"]["status"], "failed")
        self.assertEqual(len(calls), 3)  # primary + 2 fallbacks

    def test_readiness_auth_probe_converges_when_primary_returns_wrong_sentinel_and_fallback_succeeds(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if len(calls) == 1:
                class R1:
                    returncode = 0
                    stdout = TestAssistantOpenClawProviderUnit._agent_json("WRONG_SENTINEL")
                    stderr = ""
                return R1()
            class R2:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json("PANTHEON_PROVIDER_READY")
                stderr = ""
            return R2()

        provider = self._make_provider(run_func=fake_run)
        info = provider.readiness(auth_probe=True)

        self.assertTrue(info["ready"])
        self.assertEqual(info["status"], "ready")
        self.assertEqual(info["active_model"], "openai/gpt-5.6-sol")
        self.assertEqual(info["primary_model"], "anthropic/claude-opus-4-8")
        self.assertTrue(info["fallback_used"])
        self.assertEqual(info["primary_unavailable"]["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(info["primary_unavailable"]["status"], "unavailable")
        self.assertEqual(info["primary_unavailable"]["reason"], "openclaw_answer_probe_sentinel_mismatch")
        self.assertEqual(info["answer_probe"]["status"], "completed")
        self.assertEqual(info["answer_probe"]["active_model"], "openai/gpt-5.6-sol")
        self.assertEqual(len(calls), 2)

    def test_readiness_auth_probe_fails_when_reply_is_empty(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json("")
                stderr = ""
            return R()

        provider = self._make_provider(run_func=fake_run)
        info = provider.readiness(auth_probe=True)

        self.assertFalse(info["ready"])
        self.assertEqual(info["status"], "degraded")
        self.assertEqual(info["reason"], "openclaw_answer_probe_empty")
        self.assertEqual(info["primary_unavailable"]["reason"], "openclaw_answer_probe_empty")
        self.assertEqual(info["answer_probe"]["status"], "failed")
        self.assertEqual(len(calls), 3)

    def test_readiness_auth_probe_rejects_substring_sentinel(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json("prefix PANTHEON_PROVIDER_READY suffix")
                stderr = ""
            return R()

        provider = self._make_provider(run_func=fake_run)
        info = provider.readiness(auth_probe=True)

        self.assertFalse(info["ready"])
        self.assertEqual(info["status"], "degraded")
        self.assertEqual(info["reason"], "openclaw_answer_probe_sentinel_mismatch")
        self.assertEqual(info["primary_unavailable"]["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(info["primary_unavailable"]["status"], "unavailable")
        self.assertEqual(info["primary_unavailable"]["reason"], "openclaw_answer_probe_sentinel_mismatch")
        self.assertEqual(info["answer_probe"]["status"], "failed")
        self.assertEqual(len(calls), 3)

    def test_invoke_converges_via_fallback_when_primary_fails_with_auth_unavailable(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if len(calls) == 1:
                class R1:
                    returncode = 1
                    stdout = ""
                    stderr = "Claude OAuth login session expired (401 unauthorized)"
                return R1()
            class R2:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json("fallback reply")
                stderr = ""
            return R2()

        provider = self._make_provider(run_func=fake_run)
        result = provider.invoke("test", mode="user", operator_id="op-1")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["transport"], "cli")
        self.assertEqual(result.output["active_model"], "openai/gpt-5.6-sol")
        self.assertTrue(result.output["fallback_used"])
        self.assertEqual(len(calls), 2)

    def test_invoke_does_not_retry_after_generic_invocation_failure(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 1
                stdout = ""
                stderr = "generic post-execution error in tool call"
            return R()

        provider = self._make_provider(run_func=fake_run)
        with self.assertRaises(OpenClawProviderError) as ctx:
            provider.invoke("test ambiguous generic failure", mode="user", operator_id="op-1")

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_GATEWAY_INVOCATION_FAILED")
        # Must NOT attempt fallback on generic post-execution failure to satisfy unambiguous-pre-execution-only contract
        self.assertEqual(len(calls), 1)

    def test_invoke_does_not_retry_after_timeout(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            raise subprocess.TimeoutExpired(cmd="openclaw agent", timeout=kwargs["timeout"])

        provider = self._make_provider(run_func=fake_run)
        with self.assertRaises(OpenClawProviderError) as ctx:
            provider.invoke("test ambiguous turn", mode="user", operator_id="op-1")

        self.assertEqual(ctx.exception.status_code, 504)
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_GATEWAY_TIMEOUT")
        # Must NOT attempt fallback on ambiguous timeout to prevent running side-effects twice
        self.assertEqual(len(calls), 1)

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

    def test_invoke_selects_validated_per_request_agent_and_reports_it(self) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd, **_kw):
            captured.append(list(cmd))
            class R:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json("persona result")
                stderr = ""
            return R()

        provider = self._make_provider(run_func=fake_run)
        result = provider.invoke(
            "Return opinion JSON",
            agent_id="persona-opinion-0123456789abcdef01234567",
            session_id="fresh-persona-session-1",
            operator_id="op-1",
        )

        cmd = captured[0]
        self.assertEqual(
            cmd[cmd.index("--agent") + 1],
            "persona-opinion-0123456789abcdef01234567",
        )
        self.assertEqual(cmd[cmd.index("--session-id") + 1], "fresh-persona-session-1")
        self.assertEqual(
            result.output["agent_id"],
            "persona-opinion-0123456789abcdef01234567",
        )

    def test_invoke_cli_args_and_env(self) -> None:
        """CLI gets `agent --agent --message <prompt> --json` (no --url/--token);
        the prompt travels as the argv `--message` VALUE (the CLI has no stdin
        mode — `--message -` is taken literally and yields HEARTBEAT_OK), and the
        ws URL + token are supplied via the subprocess env the CLI reads."""
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

        prompt = "Reply with exactly: OPENCLAW_LIVE"
        provider = self._make_provider(run_func=fake_run)
        with patch.dict(
            os.environ,
            {"PANTHEON_OPENCLAW_GATEWAY_STATE_DIR": "/home/node/.openclaw"},
            clear=False,
        ):
            provider.invoke(prompt, operator_id="op-1")
        self.assertTrue(captured, "subprocess.run was never called")
        cmd = captured[0]
        self.assertIn("agent", cmd)
        self.assertIn("--agent", cmd)
        self.assertIn("main", cmd)
        self.assertIn("--json", cmd)
        # The prompt is the argv value immediately after --message, NOT stdin
        # and NOT a literal "-".
        self.assertIn("--message", cmd)
        self.assertEqual(cmd[cmd.index("--message") + 1], prompt)
        self.assertNotEqual(cmd[cmd.index("--message") + 1], "-")
        # No stdin is fed (the CLI does not read it).
        self.assertIsNone(captured_input[0])
        # The agent subcommand does NOT accept --url/--token.
        self.assertNotIn("--url", cmd)
        self.assertNotIn("--token", cmd)
        # URL + token travel via the environment instead.
        env = captured_env[0]
        self.assertEqual(env.get("OPENCLAW_GATEWAY_URL"), "ws://openclaw-gateway:18789")
        self.assertEqual(env.get("OPENCLAW_GATEWAY_TOKEN"), "test-token")
        self.assertEqual(env.get("OPENCLAW_STATE_DIR"), "/home/node/.openclaw")
        self.assertEqual(env.get("HOME"), "/home/node")

    def test_invoke_oversized_prompt_uses_responses_http_transport(self) -> None:
        """Large Management AI context uses the body-based Responses transport.

        The complete prompt must be preserved; the CLI cannot safely accept it
        as one argv value.  This prevents a BFF deterministic fallback caused
        solely by the adapter's argv transport limit.
        """
        calls: list = []
        captured: dict = {}

        def fake_run(cmd, **kw):
            calls.append(cmd)
            class R:
                returncode = 0
                stdout = TestAssistantOpenClawProviderUnit._agent_json("response")
                stderr = ""
            return R()

        class FakeResp:
            def __iter__(self):
                return iter([
                    b'data: {"type":"response.output_text.done","text":"large provider answer"}\n',
                    b'data: {"type":"response.completed"}\n',
                    b"data: [DONE]\n",
                ])

            def close(self):
                pass

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = req.data
            return FakeResp()

        big_prompt = "x" * (96 * 1024 + 1)
        provider = self._make_provider(run_func=fake_run)
        with patch("urllib.request.urlopen", fake_urlopen):
            result = provider.invoke(
                big_prompt,
                operator_id="op-1",
                session_id="session-large-prompt",
            )

        self.assertFalse(calls, "CLI must not receive an oversized argv prompt")
        self.assertEqual(captured["url"], "http://openclaw-gateway:18789/v1/responses")
        body = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(body["input"], big_prompt)
        self.assertTrue(body["stream"])
        self.assertEqual(body["user"], "session-large-prompt")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["transport"], "responses_http")
        self.assertEqual(result.output["transport_reason"], "argv_prompt_exceeds_safe_limit")
        self.assertEqual(
            result.output["json_events"][0]["item"]["text"],
            "large provider answer",
        )

    def test_oversized_prompt_unreachable_responses_is_typed(self) -> None:
        """The normal invoke fallback keeps the Responses unreachable contract."""
        import urllib.error

        provider = self._make_provider(run_func=lambda *_args, **_kwargs: None)
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError("refused")),
        ), self.assertRaises(OpenClawProviderError) as ctx:
            provider.invoke("x" * (96 * 1024 + 1), operator_id="op-1")

        self.assertEqual(ctx.exception.error_code, "OPENCLAW_RESPONSES_UNREACHABLE")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_invoke_non_zero_exit_raises(self) -> None:
        def fake_run(cmd, **_kw):
            class R:
                returncode = 1
                stdout = ""
                stderr = "generic failure"
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


class TestAssistantOpenClawProviderStream(unittest.TestCase):
    """Unit tests for the /v1/responses SSE streaming path (mock urllib)."""

    def _provider(self):
        return AssistantOpenClawProvider(
            gateway_url="ws://openclaw-gateway:18789",
            agent_id="main",
            token="test-token",
            _which_func=lambda _: "/usr/local/bin/openclaw",
        )

    def test_http_base_derives_http_from_ws(self) -> None:
        self.assertEqual(self._provider()._http_base(), "http://openclaw-gateway:18789")

    def test_stream_yields_delta_then_done(self) -> None:
        from unittest import mock

        sse = [
            b'event: response.output_text.delta\n',
            b'data: {"type":"response.output_text.delta","delta":"\xe4\xbd\xa0\xe5\xa5\xbd"}\n',
            b"\n",
            b'data: {"type":"response.output_text.delta","delta":"!"}\n',
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b"data: [DONE]\n",
        ]

        class FakeResp:
            def __iter__(self):
                return iter(sse)

            def close(self):
                pass

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = req.data
            captured["auth"] = req.headers.get("Authorization")
            captured["agent_id"] = dict(req.header_items()).get("X-openclaw-agent-id")
            return FakeResp()

        provider = self._provider()
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            events = list(provider.stream("hi", operator_id="op-1", session_user="sess-1"))

        # URL + auth + payload shape
        self.assertEqual(captured["url"], "http://openclaw-gateway:18789/v1/responses")
        self.assertEqual(captured["auth"], "Bearer test-token")
        body = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(body["model"], "openclaw")
        self.assertTrue(body["stream"])
        self.assertEqual(body["user"], "sess-1")
        self.assertEqual(captured["agent_id"], "main")

        deltas = [e["text"] for e in events if e["type"] == "delta"]
        done = [e for e in events if e["type"] == "done"]
        self.assertEqual("".join(deltas), "你好!")
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["text"], "你好!")
        self.assertEqual(done[0]["transport"], "responses_http")

    def test_stream_404_reports_disabled(self) -> None:
        import urllib.error
        from unittest import mock

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        provider = self._provider()
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            events = list(provider.stream("hi", operator_id="op-1"))
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["error_code"], "OPENCLAW_RESPONSES_DISABLED")

    def test_stream_completed_without_text_reports_empty_response(self) -> None:
        from unittest import mock

        class FakeResp:
            def __iter__(self):
                return iter([b'data: {"type":"response.completed"}\n'])

            def close(self):
                pass

        provider = self._provider()
        with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            events = list(provider.stream("hi", operator_id="op-1"))

        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["error_code"], "OPENCLAW_RESPONSES_EMPTY")

    def test_stream_uses_output_text_done_when_no_deltas_arrive(self) -> None:
        from unittest import mock

        class FakeResp:
            def __iter__(self):
                return iter([
                    b'data: {"type":"response.output_text.done","text":"provider answer"}\n',
                    b'data: {"type":"response.completed"}\n',
                    b"data: [DONE]\n",
                ])

            def close(self):
                pass

        provider = self._provider()
        with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            events = list(provider.stream("hi", operator_id="op-1"))

        done = [event for event in events if event["type"] == "done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["text"], "provider answer")
        self.assertEqual(done[0]["transport"], "responses_http")

    def test_stream_unreachable_transport_has_typed_failure(self) -> None:
        import urllib.error
        from unittest import mock

        provider = self._provider()
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError("refused")),
        ):
            events = list(provider.stream("hi", operator_id="op-1"))

        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["error_code"], "OPENCLAW_RESPONSES_UNREACHABLE")
        self.assertEqual(events[-1]["status_code"], 503)

    def test_stream_timeout_transport_has_typed_failure(self) -> None:
        import urllib.error
        from unittest import mock

        provider = self._provider()
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(socket.timeout("timed out")),
        ):
            events = list(provider.stream("hi", operator_id="op-1"))

        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["error_code"], "OPENCLAW_RESPONSES_TIMEOUT")
        self.assertEqual(events[-1]["status_code"], 504)

    def test_stream_done_marker_without_text_reports_empty_response(self) -> None:
        from unittest import mock

        class FakeResp:
            def __iter__(self):
                return iter([b"data: [DONE]\\n"])

            def close(self):
                pass

        provider = self._provider()
        with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            events = list(provider.stream("hi", operator_id="op-1"))

        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["error_code"], "OPENCLAW_RESPONSES_EMPTY")

    def test_stream_requires_token(self) -> None:
        provider = AssistantOpenClawProvider(
            gateway_url="ws://openclaw-gateway:18789", token="",
            _which_func=lambda _: "/usr/local/bin/openclaw",
        )
        events = list(provider.stream("hi", operator_id="op-1"))
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["error_code"], "OPENCLAW_TOKEN_NOT_CONFIGURED")

    def test_stream_respects_upstream_contract_model(self) -> None:
        from unittest import mock

        calls: list[dict] = []

        class FakeResp:
            def __iter__(self):
                return iter([
                    b'data: {"type":"response.output_text.delta","delta":"streamed answer"}\n',
                    b'data: {"type":"response.completed"}\n',
                    b"data: [DONE]\n",
                ])

            def close(self):
                pass

        def fake_urlopen(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            calls.append(body)
            return FakeResp()

        provider = self._provider()
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            events = list(provider.stream("hi", operator_id="op-1"))

        done = [event for event in events if event["type"] == "done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["text"], "streamed answer")
        self.assertEqual(done[0]["transport"], "responses_http")
        self.assertEqual(len(calls), 1)
        # Upstream OpenClaw v2026.7.1 contract requires model="openclaw"
        self.assertEqual(calls[0]["model"], "openclaw")

    def test_stream_surfaces_http_400_as_typed_error(self) -> None:
        import urllib.error
        from unittest import mock

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)

        provider = self._provider()
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            events = list(provider.stream("hi", operator_id="op-1"))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["error_code"], "OPENCLAW_RESPONSES_HTTP_ERROR")
        self.assertEqual(events[0]["status_code"], 400)
