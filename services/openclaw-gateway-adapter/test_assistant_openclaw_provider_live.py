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
import urllib.error
from pathlib import Path
from unittest.mock import patch

_ADAPTER_DIR = str(Path(__file__).resolve().parent)
if _ADAPTER_DIR not in sys.path:
    sys.path.insert(0, _ADAPTER_DIR)

from assistant_openclaw_provider import AssistantOpenClawProvider, OpenClawProviderError


def _sse_bytes(events: list) -> list:
    lines = [("data: " + json.dumps(evt) + "\n").encode("utf-8") for evt in events]
    lines.append(b"data: [DONE]\n")
    return lines


class _FakeSSEResponse:
    """Minimal fake of the `urlopen()` response object, iterable over SSE lines."""

    def __init__(self, events: list) -> None:
        self._lines = _sse_bytes(events)

    def __iter__(self):
        return iter(self._lines)

    def close(self) -> None:
        pass


def _answer_events(text: str) -> list:
    """SSE events for a plain-text `/v1/responses` reply (no tool calls)."""
    return [
        {"type": "response.output_text.done", "text": text},
        {"type": "response.completed", "response": {"status": "completed"}},
    ]


def _http_error_urlopen(status_code: int, body: str = ""):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, status_code, body, {}, None)

    return fake_urlopen


def _model_dispatch_urlopen(handlers: dict, calls: list):
    """Return a fake `urlopen` that dispatches on the request's requested model.

    The JSON `model` field is always the fixed `openclaw/<agentId>` alias (the
    pinned Gateway's model resolver rejects a raw provider id there); the
    actual requested provider/model candidate travels in the `x-openclaw-model`
    header instead. `handlers[model]` is a callable `(timeout) ->
    _FakeSSEResponse` (or one that raises). Every call is recorded into
    `calls` as `{"model", "timeout", "body"}` so tests can assert
    per-candidate timeout budgets and payload shape without any subprocess
    involved.
    """

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        headers = {k.lower(): v for k, v in req.header_items()}
        model = headers.get("x-openclaw-model")
        calls.append({"model": model, "timeout": timeout, "body": body})
        handler = handlers.get(model)
        if handler is None:
            raise AssertionError(f"unexpected model requested: {model!r}")
        return handler(timeout)

    return fake_urlopen


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
        self.assertEqual(
            result.output.get("transport"),
            "responses_http",
            "Ordinary turns must go through the unified HTTP /v1/responses transport",
        )

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

    def test_readiness_ignores_missing_cli_binary(self) -> None:
        """The HTTP answer-probe never needs the `openclaw` CLI binary.

        Ordinary turns (invoke/stream/readiness) are unified on HTTP; a
        missing/unresolvable CLI binary must not block the answer probe.
        """
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {"anthropic/claude-opus-4-8": lambda _t: _FakeSSEResponse(_answer_events("PANTHEON_PROVIDER_READY"))},
            calls,
        )
        provider = AssistantOpenClawProvider(
            gateway_url="ws://openclaw-gateway:18789",
            token="tok",
            _which_func=lambda _: None,
        )
        with patch("urllib.request.urlopen", fake_urlopen):
            info = provider.readiness(auth_probe=True)
        self.assertTrue(info["ready"])
        self.assertNotIn("binary_path", info)

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
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {"anthropic/claude-opus-4-8": lambda _t: _FakeSSEResponse(_answer_events("PANTHEON_PROVIDER_READY"))},
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            info = provider.readiness(auth_probe=True)

        self.assertTrue(info["ready"])
        self.assertEqual(info["status"], "ready")
        self.assertEqual(info["answer_probe"]["status"], "completed")
        self.assertEqual(info["answer_probe"]["deadline_seconds"], 20.0)
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(calls[0]["timeout"], 20.0)
        self.assertGreaterEqual(calls[0]["timeout"], 1.0)
        self.assertIn("PANTHEON_PROVIDER_READY", json.dumps(calls[0]["body"]))

    def test_readiness_auth_probe_converges_via_fallback_when_primary_claude_fails(self) -> None:
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {
                "anthropic/claude-opus-4-8": lambda _t: (_ for _ in ()).throw(
                    urllib.error.URLError(TimeoutError("timed out"))
                ),
                "openai/gpt-5.6-sol": lambda _t: _FakeSSEResponse(_answer_events("PANTHEON_PROVIDER_READY")),
            },
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
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
        self.assertEqual(calls[0]["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(calls[1]["model"], "openai/gpt-5.6-sol")
        self.assertGreaterEqual(calls[0]["timeout"], 4.0)
        self.assertLessEqual(calls[0]["timeout"], 5.0)
        self.assertGreaterEqual(calls[1]["timeout"], 8.0)

    def test_readiness_auth_probe_converges_via_second_fallback_when_primary_and_fallback_one_fail(self) -> None:
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {
                "anthropic/claude-opus-4-8": lambda _t: (_ for _ in ()).throw(
                    urllib.error.URLError(TimeoutError("timed out"))
                ),
                "openai/gpt-5.6-sol": lambda _t: (_ for _ in ()).throw(
                    urllib.error.URLError(TimeoutError("timed out"))
                ),
                "openai/gpt-5.5": lambda _t: _FakeSSEResponse(_answer_events("PANTHEON_PROVIDER_READY")),
            },
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            info = provider.readiness(auth_probe=True)

        self.assertTrue(info["ready"])
        self.assertEqual(info["status"], "ready")
        self.assertEqual(info["active_model"], "openai/gpt-5.5")
        self.assertEqual(info["primary_model"], "anthropic/claude-opus-4-8")
        self.assertTrue(info["fallback_used"])
        self.assertEqual(info["fallback_model"], "openai/gpt-5.5")
        self.assertEqual(info["primary_unavailable"]["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(info["primary_unavailable"]["status"], "unavailable")
        self.assertEqual(info["primary_unavailable"]["reason"], "OPENCLAW_GATEWAY_TIMEOUT")
        self.assertEqual(info["answer_probe"]["status"], "completed")
        self.assertEqual(info["answer_probe"]["active_model"], "openai/gpt-5.5")
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(calls[1]["model"], "openai/gpt-5.6-sol")
        self.assertEqual(calls[2]["model"], "openai/gpt-5.5")
        self.assertGreaterEqual(calls[0]["timeout"], 4.0)
        self.assertLessEqual(calls[0]["timeout"], 5.0)
        self.assertGreaterEqual(calls[1]["timeout"], 8.0)
        self.assertGreaterEqual(calls[2]["timeout"], 8.0)

    def test_readiness_primary_candidate_gets_more_than_gateway_queue_wait_budget(self) -> None:
        """OPGAP-OPENCLAW-READINESS-QUEUE-BUDGET-20260901 regression.

        The gateway's own internal request-lane queueing has been observed to
        add several real seconds of wait before an HTTP turn even starts
        (independent of model latency). A primary-candidate budget that is
        capped near or below that queueing delay means the primary is killed
        by "no output" before it ever gets a chance to run, on every probe,
        not just under exceptional load. This asserts the primary candidate's
        requested timeout has real headroom above a 1.5s-scale queueing delay.
        """
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {"anthropic/claude-opus-4-8": lambda _t: _FakeSSEResponse(_answer_events("PANTHEON_PROVIDER_READY"))},
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            info = provider.readiness(auth_probe=True)

        self.assertTrue(info["ready"])
        self.assertEqual(info["active_model"], "anthropic/claude-opus-4-8")
        self.assertGreater(
            calls[0]["timeout"],
            1.5,
            "primary candidate must get more than the old 1.5s cap so it survives "
            "realistic gateway queueing delay instead of being killed before it starts",
        )

    def test_readiness_auth_probe_fails_closed_when_all_models_fail(self) -> None:
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {
                "anthropic/claude-opus-4-8": lambda _t: (_ for _ in ()).throw(
                    urllib.error.HTTPError("url", 401, "auth session expired", {}, None)
                ),
                "openai/gpt-5.6-sol": lambda _t: (_ for _ in ()).throw(
                    urllib.error.HTTPError("url", 401, "auth session expired", {}, None)
                ),
                "openai/gpt-5.5": lambda _t: (_ for _ in ()).throw(
                    urllib.error.HTTPError("url", 401, "auth session expired", {}, None)
                ),
            },
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            info = provider.readiness(auth_probe=True)

        self.assertFalse(info["ready"])
        self.assertEqual(info["status"], "degraded")
        self.assertEqual(info["reason"], "OPENCLAW_RESPONSES_HTTP_ERROR")
        self.assertEqual(info["primary_unavailable"]["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(info["primary_unavailable"]["status"], "unavailable")
        self.assertEqual(info["primary_unavailable"]["reason"], "OPENCLAW_RESPONSES_HTTP_ERROR")
        self.assertEqual(info["answer_probe"]["status"], "failed")
        self.assertEqual(len(calls), 3)  # primary + 2 fallbacks

    def test_readiness_primary_unavailable_evidence_is_sanitized(self) -> None:
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {
                model: (lambda _t: (_ for _ in ()).throw(
                    urllib.error.HTTPError(
                        "url", 401, "error with token=sk-ant-secret-key-12345 login expired", {}, None
                    )
                ))
                for model in ("anthropic/claude-opus-4-8", "openai/gpt-5.6-sol", "openai/gpt-5.5")
            },
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            info = provider.readiness(auth_probe=True)

        sanitized_reason = info["primary_unavailable"]["reason"]
        self.assertNotIn("secret", sanitized_reason)
        self.assertNotIn("12345", sanitized_reason)
        self.assertEqual(sanitized_reason, "OPENCLAW_RESPONSES_HTTP_ERROR")

    def test_readiness_auth_probe_fails_when_reply_has_wrong_sentinel(self) -> None:
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {
                model: (lambda _t: _FakeSSEResponse(_answer_events("WRONG_SENTINEL")))
                for model in ("anthropic/claude-opus-4-8", "openai/gpt-5.6-sol", "openai/gpt-5.5")
            },
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
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
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {
                "anthropic/claude-opus-4-8": lambda _t: _FakeSSEResponse(_answer_events("WRONG_SENTINEL")),
                "openai/gpt-5.6-sol": lambda _t: _FakeSSEResponse(_answer_events("PANTHEON_PROVIDER_READY")),
            },
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
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
        """An upstream reply with no text (and no function calls) raises
        OPENCLAW_RESPONSES_EMPTY from `_invoke_via_http`, which readiness
        treats as a per-candidate failure (not the empty-but-successful path
        the old CLI transport used, since HTTP raises before returning)."""
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {
                model: (lambda _t: _FakeSSEResponse([{"type": "response.completed", "response": {"status": "completed"}}]))
                for model in ("anthropic/claude-opus-4-8", "openai/gpt-5.6-sol", "openai/gpt-5.5")
            },
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            info = provider.readiness(auth_probe=True)

        self.assertFalse(info["ready"])
        self.assertEqual(info["status"], "degraded")
        self.assertEqual(info["reason"], "OPENCLAW_RESPONSES_EMPTY")
        self.assertEqual(info["primary_unavailable"]["reason"], "OPENCLAW_RESPONSES_EMPTY")
        self.assertEqual(info["answer_probe"]["status"], "failed")
        self.assertEqual(len(calls), 3)

    def test_readiness_auth_probe_rejects_substring_sentinel(self) -> None:
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {
                model: (lambda _t: _FakeSSEResponse(_answer_events("prefix PANTHEON_PROVIDER_READY suffix")))
                for model in ("anthropic/claude-opus-4-8", "openai/gpt-5.6-sol", "openai/gpt-5.5")
            },
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            info = provider.readiness(auth_probe=True)

        self.assertFalse(info["ready"])
        self.assertEqual(info["status"], "degraded")
        self.assertEqual(info["reason"], "openclaw_answer_probe_sentinel_mismatch")
        self.assertEqual(info["primary_unavailable"]["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(info["primary_unavailable"]["status"], "unavailable")
        self.assertEqual(info["primary_unavailable"]["reason"], "openclaw_answer_probe_sentinel_mismatch")
        self.assertEqual(info["answer_probe"]["status"], "failed")
        self.assertEqual(len(calls), 3)

    def test_invoke_does_not_retry_on_http_401_making_exactly_one_call(self) -> None:
        calls: list = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(OpenClawProviderError) as ctx:
                provider.invoke("test", mode="user", operator_id="op-1")

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_RESPONSES_HTTP_ERROR")
        self.assertEqual(len(calls), 1)

    def test_invoke_does_not_retry_after_generic_connection_failure(self) -> None:
        calls: list = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            raise ConnectionResetError("connection reset")

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(OpenClawProviderError) as ctx:
                provider.invoke("test ambiguous generic failure", mode="user", operator_id="op-1")

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_RESPONSES_UNREACHABLE")
        # Must NOT attempt any retry/fallback on an ambiguous connection failure.
        self.assertEqual(len(calls), 1)

    def test_invoke_does_not_retry_on_http_5xx_error(self) -> None:
        calls: list = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            raise urllib.error.HTTPError(req.full_url, 503, "service unavailable", {}, None)

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(OpenClawProviderError) as ctx:
                provider.invoke("test post-execution auth failure", mode="user", operator_id="op-1")

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_RESPONSES_HTTP_ERROR")
        # Must fail closed with exactly one HTTP call, no automatic model swap/CLI fallback.
        self.assertEqual(len(calls), 1)

    def test_invoke_does_not_retry_after_timeout(self) -> None:
        calls: list = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            raise urllib.error.URLError(TimeoutError("timed out"))

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(OpenClawProviderError) as ctx:
                provider.invoke("test ambiguous turn", mode="user", operator_id="op-1")

        self.assertEqual(ctx.exception.status_code, 504)
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_RESPONSES_TIMEOUT")
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
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {"anthropic/claude-opus-4-8": lambda _t: _FakeSSEResponse(_answer_events(reply))},
            calls,
        )

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            result = provider.invoke("Say hello", mode="user", operator_id="op-1")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.provider, "openclaw")
        events = result.output.get("json_events", [])
        self.assertTrue(events)
        self.assertEqual(events[0]["item"]["text"], reply)
        self.assertEqual(result.output["transport"], "responses_http")

    def test_invoke_selects_validated_per_request_agent_and_reports_it(self) -> None:
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            headers = {k.lower(): v for k, v in req.header_items()}
            captured["agent_id"] = headers.get("x-openclaw-agent-id")
            captured["model_header"] = headers.get("x-openclaw-model")
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("persona result"))

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            result = provider.invoke(
                "Return opinion JSON",
                agent_id="persona-opinion-0123456789abcdef01234567",
                session_id="fresh-persona-session-1",
                operator_id="op-1",
            )

        self.assertEqual(captured["agent_id"], "persona-opinion-0123456789abcdef01234567")
        # `user` is derived from authenticated actor + conversation, not the
        # raw caller-supplied session_id verbatim (tenant isolation).
        self.assertEqual(captured["body"]["user"], "op-1|fresh-persona-session-1")
        # A non-default agent with no explicit model override must NOT request
        # one of the primary/fallback models; it uses the agent's own config.
        self.assertEqual(
            captured["body"]["model"], "openclaw/persona-opinion-0123456789abcdef01234567"
        )
        self.assertIsNone(captured["model_header"])
        self.assertEqual(
            result.output["agent_id"],
            "persona-opinion-0123456789abcdef01234567",
        )

    def test_invoke_persona_agent_does_not_override_model(self) -> None:
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            captured["agent_id"] = headers.get("x-openclaw-agent-id")
            captured["model_header"] = headers.get("x-openclaw-model")
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("persona opinion text"))

        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            result = provider.invoke(
                "Return persona opinion",
                agent_id="persona-opinion-a",
                mode="user",
                operator_id="op-1",
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(captured["agent_id"], "persona-opinion-a")
        self.assertEqual(captured["body"]["model"], "openclaw/persona-opinion-a")
        self.assertIsNone(captured["model_header"])
        self.assertEqual(result.output["agent_id"], "persona-opinion-a")
        self.assertNotIn("active_model", result.output)

    def test_readiness_to_single_invoke_end_to_end_uses_probed_fallback(self) -> None:
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {
                "anthropic/claude-opus-4-8": lambda _t: (_ for _ in ()).throw(
                    urllib.error.URLError(TimeoutError("timed out"))
                ),
                "openai/gpt-5.6-sol": lambda _t: _FakeSSEResponse(
                    _answer_events("PANTHEON_PROVIDER_READY")
                ),
            },
            calls,
        )

        provider = self._make_provider()

        # 1. Readiness probe runs: primary hangs/times out, fallback openai/gpt-5.6-sol succeeds
        with patch("urllib.request.urlopen", fake_urlopen):
            info = provider.readiness(auth_probe=True)
        self.assertTrue(info["ready"])
        self.assertEqual(info["status"], "ready")
        self.assertEqual(info["active_model"], "openai/gpt-5.6-sol")
        self.assertTrue(info["fallback_used"])
        self.assertEqual(len(calls), 2)

        # 2. Single invoke runs next: MUST select the already-probed eligible model directly
        # and not attempt the timed-out primary again.
        fake_urlopen_2 = _model_dispatch_urlopen(
            {"openai/gpt-5.6-sol": lambda _t: _FakeSSEResponse(_answer_events("live answer"))},
            calls,
        )
        with patch("urllib.request.urlopen", fake_urlopen_2):
            result = provider.invoke("Reply with exactly: OPENCLAW_LIVE", mode="user", operator_id="smoke-test")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["active_model"], "openai/gpt-5.6-sol")
        self.assertTrue(result.output["fallback_used"])
        self.assertEqual(result.output["transport"], "responses_http")
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[2]["model"], "openai/gpt-5.6-sol")

    def test_invoke_never_spawns_a_subprocess(self) -> None:
        """Ordinary invoke() must go through HTTP only — no CLI subprocess.

        `_run_func` is set to raise if ever called, and the HTTP layer is
        mocked to succeed; the assertion is that invoke() succeeds without
        ever touching the subprocess spy.
        """

        def forbidden_run(*_args, **_kwargs):
            raise AssertionError("must not spawn subprocess for ordinary turn")

        provider = AssistantOpenClawProvider(
            gateway_url="ws://openclaw-gateway:18789",
            agent_id="main",
            token="test-token",
            _which_func=lambda _: "/usr/local/bin/openclaw",
            _run_func=forbidden_run,
        )
        prompt = "Reply with exactly: OPENCLAW_LIVE"
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {"anthropic/claude-opus-4-8": lambda _t: _FakeSSEResponse(_answer_events("response"))},
            calls,
        )
        with patch("urllib.request.urlopen", fake_urlopen):
            result = provider.invoke(prompt, operator_id="op-1")
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["body"]["input"], prompt)
        self.assertEqual(calls[0]["model"], "anthropic/claude-opus-4-8")

    def test_invoke_oversized_prompt_behaves_identically_to_short_prompt(self) -> None:
        """A >96KiB prompt must use the exact same HTTP transport as a short one.

        Ordinary-turn transport selection never depends on prompt length —
        there is no separate "oversized" code path anymore.
        """
        calls: list = []
        fake_urlopen = _model_dispatch_urlopen(
            {"anthropic/claude-opus-4-8": lambda _t: _FakeSSEResponse(_answer_events("large provider answer"))},
            calls,
        )

        big_prompt = "x" * (96 * 1024 + 1)
        provider = self._make_provider()
        with patch("urllib.request.urlopen", fake_urlopen):
            result = provider.invoke(
                big_prompt,
                operator_id="op-1",
                session_id="session-large-prompt",
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["body"]["input"], big_prompt)
        self.assertTrue(calls[0]["body"]["stream"])
        self.assertEqual(calls[0]["body"]["user"], "op-1|session-large-prompt")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["transport"], "responses_http")
        self.assertEqual(
            result.output["json_events"][0]["item"]["text"],
            "large provider answer",
        )

    def test_oversized_prompt_unreachable_responses_is_typed(self) -> None:
        """The normal invoke path keeps the Responses unreachable contract
        regardless of prompt size."""
        provider = self._make_provider()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError("refused")),
        ), self.assertRaises(OpenClawProviderError) as ctx:
            provider.invoke("x" * (96 * 1024 + 1), operator_id="op-1")

        self.assertEqual(ctx.exception.error_code, "OPENCLAW_RESPONSES_UNREACHABLE")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_invoke_non_zero_exit_raises(self) -> None:
        provider = self._make_provider()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("url", 500, "generic failure", {}, None),
        ):
            with self.assertRaises(OpenClawProviderError) as ctx:
                provider.invoke("test", operator_id="op-1")
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_RESPONSES_HTTP_ERROR")

    def test_http_url_converted_to_ws(self) -> None:
        """If an http:// URL was set (old broken config), normalize it to ws://."""
        provider = AssistantOpenClawProvider(
            gateway_url="http://openclaw-gateway:18789",
            token="tok",
            _which_func=lambda _: "/usr/local/bin/openclaw",
        )
        self.assertTrue(provider._gateway_url.startswith("ws://"))

    def test_no_gateway_url_uses_default_http_base(self) -> None:
        """When no URL is configured, the derived HTTP base for `/v1/responses`
        still resolves to the canonical default WS address's HTTP equivalent.

        `stream()`/`invoke()` still require an explicitly configured gateway
        URL to actually make a request (see `test_stream_requires_gateway_url`
        below); this only pins the fallback base-URL derivation used once a
        URL is configured.
        """
        provider = AssistantOpenClawProvider(
            gateway_url="",
            token="tok",
            _which_func=lambda _: "/usr/local/bin/openclaw",
        )
        self.assertFalse(provider.configured)
        self.assertEqual(provider._http_base(), "http://openclaw-gateway:18789")

    def test_stream_requires_gateway_url(self) -> None:
        """Ordinary turns must not silently proceed without a configured
        gateway URL — stream()/invoke() surface a typed error instead."""
        provider = AssistantOpenClawProvider(
            gateway_url="",
            token="tok",
            _which_func=lambda _: "/usr/local/bin/openclaw",
        )
        with self.assertRaises(OpenClawProviderError) as ctx:
            provider.invoke("test", operator_id="op-1")
        self.assertEqual(ctx.exception.error_code, "OPENCLAW_GATEWAY_URL_NOT_SET")


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
        self.assertEqual(body["model"], "openclaw/main")
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
        # Upstream OpenClaw v2026.7.1 contract requires an `openclaw`/
        # `openclaw/<agentId>` alias, never a raw provider id, in `model`.
        self.assertEqual(calls[0]["model"], "openclaw/main")

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
