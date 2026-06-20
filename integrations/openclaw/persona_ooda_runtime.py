"""Real persona OODA turn execution through OpenClaw `/v1/responses`.

This is the REAL replacement for the `FakeOpenClawUpstream` stub that the persona
OODA harness used (`services/persona/oss_runtime.py::_run_openclaw`): instead of
fabricating a session record, this drives the persona's OWN OpenClaw agent
(`model: openclaw/<persona_id>`) through the gateway OpenResponses endpoint and
returns the persona's actual observe/orient/decide output.

`post` is injectable so prompt-building + parsing are unit-testable; the live
path uses urllib against the real gateway. There is no skip-as-green: a live test
exercises the real gateway and asserts a non-empty, persona-grounded decision.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

DEFAULT_GATEWAY_HTTP_URL = "http://openclaw-gateway:18789"


@dataclass(frozen=True)
class PersonaOodaTurn:
    persona_id: str
    status: str  # "completed" | "error"
    decision: str
    elapsed_ms: int
    model: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "persona_id": self.persona_id,
            "status": self.status,
            "decision": self.decision,
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "transport": "openclaw_responses_http",
        }
        if self.error:
            d["error"] = self.error
        return d


def build_ooda_prompt(persona: Mapping[str, Any], context_bundle: Mapping[str, Any]) -> str:
    """Build the per-turn OODA instruction the persona agent runs.

    The persona's identity/mandate already lives in its agent SOUL.md (synced by
    persona_agent_sync); here we only hand it the live state + the OODA task.
    """
    name = str(persona.get("name") or persona.get("persona_id") or persona.get("id") or "persona")
    ctx = json.dumps(dict(context_bundle), ensure_ascii=False, default=str)[:4000]
    return (
        f"你是 {name}。跑一輪 OODA。以下是本輪的即時狀態快照（context bundle）：\n"
        f"{ctx}\n\n"
        "請依你的 mandate 與策略族，用以下結構回覆（繁體中文，具體、可引用數字）：\n"
        "Observe：你從快照看到什麼（引用數字）。\n"
        "Orient：對你這個策略族代表什麼（趨勢/波動/回撤/風險）。\n"
        "Decide：一個具體決策——標的、方向、進出/加減/持有/轉平、理由與風險（停損/部位）。"
        "若資料不足以決策，明確說缺哪個訊號/行情輸入。不要用空話或『在，老闆』敷衍。"
    )


HttpPost = Callable[[str, bytes, Dict[str, str], float], bytes]


def _default_post(url: str, body: bytes, headers: Dict[str, str], timeout: float) -> bytes:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _extract_text(responses_json: Mapping[str, Any]) -> str:
    """Pull the assistant text out of an OpenResponses non-stream payload."""
    for item in responses_json.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for c in item.get("content", []) or []:
            if isinstance(c, dict) and c.get("type") in ("output_text", "text") and c.get("text"):
                return str(c["text"]).strip()
    meta = (responses_json.get("result") or {}).get("meta") if isinstance(responses_json.get("result"), dict) else None
    if isinstance(meta, dict):
        for k in ("finalAssistantVisibleText", "finalAssistantRawText"):
            if isinstance(meta.get(k), str) and meta[k].strip():
                return meta[k].strip()
    return ""


def run_persona_ooda_turn(
    persona_id: str,
    prompt: str,
    *,
    gateway_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout_seconds: float = 120.0,
    post: Optional[HttpPost] = None,
) -> PersonaOodaTurn:
    """Run ONE real OODA turn on the persona's OpenClaw agent and return its
    decision. Never raises — failures come back as status="error".
    """
    base = (gateway_url or os.getenv("OPENCLAW_GATEWAY_URL", "") or DEFAULT_GATEWAY_HTTP_URL).strip()
    if base.startswith("ws://"):
        base = "http://" + base[len("ws://"):]
    elif base.startswith("wss://"):
        base = "https://" + base[len("wss://"):]
    base = base.rstrip("/")
    tok = token or os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
    model = f"openclaw/{persona_id}"
    body = json.dumps({"model": model, "input": prompt, "stream": False}).encode("utf-8")
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    do_post = post or _default_post

    started = time.monotonic()
    try:
        raw = do_post(f"{base}/v1/responses", body, headers, timeout_seconds)
    except urllib.error.HTTPError as exc:
        return PersonaOodaTurn(persona_id, "error", "", int((time.monotonic() - started) * 1000), model,
                               error=f"/v1/responses HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        return PersonaOodaTurn(persona_id, "error", "", int((time.monotonic() - started) * 1000), model,
                               error=f"/v1/responses failed: {exc}")
    elapsed = int((time.monotonic() - started) * 1000)
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, TypeError) as exc:
        return PersonaOodaTurn(persona_id, "error", "", elapsed, model, error=f"bad json: {exc}")
    text = _extract_text(data)
    if not text:
        return PersonaOodaTurn(persona_id, "error", "", elapsed, model, error="empty decision text")
    return PersonaOodaTurn(persona_id, "completed", text, elapsed, model)
