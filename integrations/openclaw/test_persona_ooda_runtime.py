"""Unit tests for real persona OODA turn execution (injectable HTTP)."""
from __future__ import annotations

import json
from typing import Dict

import persona_ooda_runtime as rt


PERSONA = {"id": "persona-crypto", "name": "Crypto Persona"}
CTX = {"persona_id": "persona-crypto", "total_trades": 5992, "alerts": 19}


def test_prompt_includes_state_and_ooda_structure():
    p = rt.build_ooda_prompt(PERSONA, CTX)
    assert "Crypto Persona" in p
    assert "Observe" in p and "Orient" in p and "Decide" in p
    assert "5992" in p  # live state injected
    assert "在，老闆" in p  # explicitly forbids the filler reply


def test_run_turn_posts_to_persona_agent_and_parses_decision():
    captured: Dict[str, object] = {}

    def fake_post(url, body, headers, timeout):
        captured["url"] = url
        captured["body"] = json.loads(body.decode("utf-8"))
        captured["auth"] = headers.get("Authorization")
        return json.dumps({
            "status": "completed",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": "Observe：趨勢轉強。Decide：BTC 加碼，停損 -3%。"}
            ]}],
        }).encode("utf-8")

    turn = rt.run_persona_ooda_turn(
        "persona-crypto", rt.build_ooda_prompt(PERSONA, CTX),
        gateway_url="ws://openclaw-gateway:18789", token="tok", post=fake_post,
    )
    assert turn.status == "completed"
    assert "BTC" in turn.decision and "Decide" in turn.decision
    assert turn.model == "openclaw/persona-crypto"
    # ws -> http base, routed to the persona's own agent
    assert captured["url"] == "http://openclaw-gateway:18789/v1/responses"
    assert captured["body"]["model"] == "openclaw/persona-crypto"
    assert captured["auth"] == "Bearer tok"


def test_empty_decision_is_error_not_silent_success():
    def fake_post(url, body, headers, timeout):
        return json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": ""}]}]}).encode()

    turn = rt.run_persona_ooda_turn("persona-crypto", "p", token="t", post=fake_post)
    assert turn.status == "error" and turn.error == "empty decision text"


def test_transport_failure_is_captured():
    def fake_post(url, body, headers, timeout):
        raise OSError("connection refused")

    turn = rt.run_persona_ooda_turn("persona-crypto", "p", token="t", post=fake_post)
    assert turn.status == "error" and "connection refused" in (turn.error or "")


def test_brokered_prompt_names_the_tools_and_has_no_json_reply_contract():
    p = rt.build_brokered_cycle_prompt({"persona_id": "persona-crypto", "mandate": "m"}, {"pool": {}})
    for tool in ("persona_memory_recall", "persona_mental_model_read", "persona_proposal_submit"):
        assert tool in p
    assert "Return only JSON" not in p and "ignored" in p


def test_session_user_is_sent_as_the_openresponses_user():
    seen = {}

    def fake_post(url, body, headers, timeout):
        seen.update(json.loads(body))
        return json.dumps({"output": [{"content": [{"type": "output_text", "text": "done"}]}]}).encode()

    rt.run_persona_ooda_turn("persona-crypto", "p", token="t", post=fake_post, session_user="daily-cycle-1")
    assert seen["user"] == "daily-cycle-1"
    seen.clear()
    rt.run_persona_ooda_turn("persona-crypto", "p", token="t", post=fake_post)
    assert "user" not in seen


def test_broker_client_registers_and_reads_the_run_record_with_the_service_token():
    calls = []

    def post(url, body, headers, timeout):
        calls.append((url, json.loads(body), headers["X-Pantheon-Service-Token"]))
        return json.dumps({"status": "ok", "run": {"run_ref": "ptr-1"}}).encode()

    client = rt.AdapterToolBrokerClient("http://adapter:1/", "svc", post=post)
    assert client.register_run("persona-crypto", "daily_cycle", "u-1") == {"run_ref": "ptr-1"}
    assert calls == [("http://adapter:1/api/openclaw-adapter/agents/persona-tools/runs",
                      {"persona_id": "persona-crypto", "purpose": "daily_cycle", "session_user": "u-1"}, "svc")]
    with __import__("pytest").raises(rt.ToolBrokerUnavailable):
        rt.AdapterToolBrokerClient("http://adapter:1", "", post=post).register_run("p", "daily_cycle", "u")


def test_blocked_tools_report_and_backend_actually_used_are_named():
    def fake_post(url, body, headers, timeout):
        return json.dumps({"model": "anthropic/claude-sonnet-4-6", "provider": "claude-cli", "output": [
            {"type": "message", "content": [{"type": "output_text",
             "text": "Both tool calls are returning missing host identity"}]}]}).encode()

    turn = rt.run_persona_ooda_turn("persona-crypto", "p", token="t", post=fake_post)
    assert turn.blocked_reason == "persona_tools_blocked:missing_host_identity"
    assert (turn.model_used, turn.provider) == ("anthropic/claude-sonnet-4-6", "claude-cli")


def test_plain_reply_has_no_blocked_reason_and_auth_failure_is_named():
    import urllib.error

    def ok(url, body, headers, timeout):
        return json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]}).encode()

    assert rt.run_persona_ooda_turn("persona-crypto", "p", token="t", post=ok).blocked_reason == ""

    def denied(url, body, headers, timeout):
        raise urllib.error.HTTPError(url, 401, "unauthorized", {}, None)

    turn = rt.run_persona_ooda_turn("persona-crypto", "p", token="t", post=denied)
    assert turn.status == "error" and turn.error == "model_auth_failed"
