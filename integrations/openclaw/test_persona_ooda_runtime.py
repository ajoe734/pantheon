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
