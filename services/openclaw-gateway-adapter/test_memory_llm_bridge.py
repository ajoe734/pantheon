from __future__ import annotations

import json
import threading

import pytest

from memory_llm_bridge import BridgeConfig, MemoryLlmBridge

TOKEN = "bridge-token"
SCHEMA = {"name": "facts", "schema": {"type": "object", "required": ["facts"],
                                       "properties": {"facts": {"type": "array"}}}}


def _bridge(**over):
    cfg = dict(enabled=True, token=TOKEN, daily_budget=100, max_concurrency=2)
    cfg.update(over)
    return MemoryLlmBridge(BridgeConfig(**cfg))


def _body(**over):
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "SECRET-PROMPT"}],
            "response_format": {"type": "json_schema", "json_schema": SCHEMA}}
    body.update(over)
    return body


def _call(bridge, body=None, *, auth=f"Bearer {TOKEN}", invoke=None, verify=None):
    calls = []

    def default_invoke(messages, instruction, remaining):
        calls.append((messages, instruction, remaining))
        return {"text": '```json\n{"facts": []}\n```', "usage": {"prompt_tokens": 7, "completion_tokens": 3}}

    resp = bridge.handle(
        authorization=auth, body=_body() if body is None else body, operation_header=None,
        invoke=invoke or default_invoke, verify_policy=verify or (lambda agent, deadline: None),
    )
    return resp, calls


def test_success_returns_openai_shape_with_usage_and_ignores_model_name():
    resp, calls = _call(_bridge())
    assert resp.status_code == 200
    assert resp.body["object"] == "chat.completion"
    assert json.loads(resp.body["choices"][0]["message"]["content"]) == {"facts": []}
    assert resp.body["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert resp.body["model"] == "memory-index"
    assert len(calls) == 1 and "facts" in calls[0][1]


def test_disabled_by_default_and_requires_dedicated_token():
    assert _call(_bridge(enabled=False))[0].status_code == 404
    assert _call(_bridge(token=""))[0].status_code == 404
    for auth in (None, "Bearer wrong", "Basic " + TOKEN, "Bearer "):
        assert _call(_bridge(), auth=auth)[0].status_code == 401


@pytest.mark.parametrize("field,value", [
    ("tools", [{"type": "function"}]), ("tool_choice", "auto"), ("stream", True),
    ("functions", [{"name": "x"}]),
])
def test_rejects_tools_and_streaming_with_recorded_reason(field, value):
    bridge = _bridge()
    resp, calls = _call(bridge, _body(**{field: value}))
    assert resp.status_code == 422 and not calls
    assert bridge.telemetry[-1]["outcome"] == "rejected"
    assert bridge.telemetry[-1]["reason"].startswith(field)


def test_requires_json_response_format_and_json_object_allowed():
    assert _call(_bridge(), _body(response_format=None))[0].status_code == 422
    resp, _ = _call(_bridge(), _body(response_format={"type": "json_object"}))
    assert resp.status_code == 200


def test_policy_denial_blocks_dispatch():
    def deny(agent, deadline):
        assert agent == "memory-index"
        raise RuntimeError("policy mismatch")

    resp, calls = _call(_bridge(), verify=deny)
    assert resp.status_code == 503 and not calls


def test_daily_budget_returns_rate_limit_with_retry_after():
    bridge = _bridge(daily_budget=1, retry_after_seconds=9)
    assert _call(bridge)[0].status_code == 200
    resp, calls = _call(bridge)
    assert resp.status_code == 429 and resp.headers["Retry-After"] == "9" and not calls
    assert bridge.telemetry[-1]["reason"] == "daily_budget_exhausted"


def test_budget_persists_in_state_file(tmp_path):
    path = str(tmp_path / "nested" / "budget.sqlite3")
    assert _call(_bridge(daily_budget=1, state_path=path))[0].status_code == 200
    assert _call(_bridge(daily_budget=1, state_path=path))[0].status_code == 429


def test_concurrency_limit_refuses_with_429():
    bridge = _bridge(max_concurrency=1)
    entered, release = threading.Event(), threading.Event()

    def slow(messages, instruction, remaining):
        entered.set()
        release.wait(5)
        return {"text": "{\"facts\": []}", "usage": None}

    t = threading.Thread(target=lambda: _call(bridge, invoke=slow))
    t.start()
    assert entered.wait(5)
    resp, calls = _call(bridge)
    release.set()
    t.join()
    assert resp.status_code == 429 and not calls


def test_upstream_quota_and_timeout_and_bad_answers():
    class Err(Exception):
        def __init__(self, status, code):
            self.status_code, self.error_code = status, code

    def raising(status, code):
        def fn(*_a):
            raise Err(status, code)
        return fn

    assert _call(_bridge(), invoke=raising(502, "OPENCLAW_QUOTA_EXCEEDED"))[0].status_code == 429
    assert _call(_bridge(), invoke=raising(429, "X"))[0].headers["Retry-After"]
    assert _call(_bridge(), invoke=raising(504, "OPENCLAW_GATEWAY_TIMEOUT"))[0].status_code == 504
    assert _call(_bridge(), invoke=raising(500, "BOOM"))[0].status_code == 502
    assert _call(_bridge(), invoke=lambda *_a: {"text": "not json"})[0].status_code == 502
    assert _call(_bridge(), invoke=lambda *_a: {"text": '{"other": 1}'})[0].status_code == 502


def test_telemetry_never_contains_prompt_or_response_content(caplog):
    bridge = _bridge()
    with caplog.at_level("INFO", logger="pantheon.memory_llm_bridge"):
        _call(bridge, invoke=lambda *_a: {"text": '{"facts": ["RESPONSE-SECRET"]}', "usage": {"prompt_tokens": 1}})
    blob = json.dumps(bridge.telemetry) + caplog.text
    assert "SECRET-PROMPT" not in blob and "RESPONSE-SECRET" not in blob
    event = bridge.telemetry[-1]
    assert event["operation"] == "facts" and event["outcome"] == "ok" and "latency_ms" in event


def test_route_runs_only_memory_index_agent_and_checks_policy(monkeypatch):
    import main as adapter
    from fastapi.testclient import TestClient

    bridge = _bridge()
    monkeypatch.setattr(adapter, "_MEMORY_LLM_BRIDGE", bridge)
    seen = {}

    class Result:
        output = {"usage": {"prompt_tokens": 2, "completion_tokens": 1},
                  "json_events": [{"item": {"text": '{"facts": []}'}}]}

    class Provider:
        _result_text = staticmethod(adapter.AssistantOpenClawProvider._result_text)

        def _invoke_via_http(self, prompt, **kwargs):
            seen["kwargs"] = kwargs
            return Result()

    monkeypatch.setattr(adapter, "_OPENCLAW_AGENT_PROVIDER", Provider())
    monkeypatch.setattr(adapter, "_assert_structured_gateway_policy",
                        lambda agent, deadline: seen.setdefault("policy", agent))
    client = TestClient(adapter.app)
    resp = client.post("/internal/memory-llm/v1/chat/completions", json=_body(model="main"),
                       headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200, resp.text
    assert seen["policy"] == "memory-index"
    assert seen["kwargs"]["agent_id"] == "memory-index"
    assert "model" not in seen["kwargs"]
    assert client.post("/internal/memory-llm/v1/chat/completions", json=_body()).status_code == 401


# --- Opt-in dev-host probe against the real hindsight container + adapter -----
# Runs inside the real hindsight image of an isolated Compose project whose
# adapter has MEMORY_LLM_BRIDGE_ENABLED=true and a real gateway/pooled model.
# Test data is a unique smoke bank; it is never a product seed. Nothing is
# asserted that the run did not observe, and no result is synthesized.
PROBE = r'''
import json, os, time, urllib.request, urllib.error, uuid
bank = 'pantheon-bridge-smoke-' + uuid.uuid4().hex
base = 'http://127.0.0.1:8888'
headers = {'Authorization': 'Bearer ' + os.environ['HINDSIGHT_API_TENANT_API_KEY'], 'Content-Type': 'application/json'}
def call(path, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), time.monotonic() - started
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300], time.monotonic() - started
out = {}
try:
    status, body, took = call('/v1/default/banks/' + bank + '/memories', {
        'items': [{'content': 'The risk committee decided to reduce Taiwan equity exposure because volatility rose.',
                   'document_id': 'bridge-smoke-1'}], 'async': False})
    out['retain'] = {'status': status, 'latency_s': round(took, 2), 'json_valid': status == 200}
    status, body, took = call('/v1/default/banks/' + bank + '/consolidate', {})
    out['consolidation'] = {'status': status, 'latency_s': round(took, 2), 'json_valid': status == 200}
    print(json.dumps(out))
finally:
    call('/v1/default/banks/' + bank, method='DELETE')
'''


def test_real_container_bridge_probe(monkeypatch):
    import os
    import secrets
    import socket
    import subprocess
    import time
    from pathlib import Path

    raw = os.getenv("PANTHEON_MEMORY_BRIDGE_PROBE_COMMAND")
    if raw:
        result = subprocess.run(json.loads(raw), input=PROBE, text=True, capture_output=True, timeout=900)
        assert result.returncode == 0, result.stdout + result.stderr
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        assert evidence["retain"]["status"] == 200, evidence
        output = os.getenv("PANTHEON_MEMORY_BRIDGE_PROBE_OUTPUT")
        if output:
            Path(output).write_text(json.dumps(evidence, indent=2) + "\n")
        return

    if os.getenv("PANTHEON_RUN_HINDSIGHT_CONTAINER_PROBE") != "1":
        pytest.skip(
            "Set PANTHEON_RUN_HINDSIGHT_CONTAINER_PROBE=1 (starts real hindsight + pg containers against "
            "adapter + GatewayDouble) or PANTHEON_MEMORY_BRIDGE_PROBE_COMMAND JSON argv"
        )

    import uvicorn
    import main as adapter
    from assistant_openclaw_provider import AssistantOpenClawProvider

    hs_image = os.getenv("HINDSIGHT_IMAGE", "pantheon-case-file-hindsight:local")
    pg_image = os.getenv("PG_IMAGE", "pantheon-hindsight-eval-pg:pgroonga-pgvector")
    tag = "bridge-probe-" + secrets.token_hex(4)
    net, pg, hs = f"{tag}-net", f"{tag}-pg", f"{tag}-hs"
    api_key = secrets.token_hex(8)
    bridge_token = secrets.token_hex(16)

    def _sh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(args, text=True, capture_output=True, check=check)

    def _reply(agent_id: str, request: Dict[str, Any]) -> str:
        text = json.dumps(request)
        if "Extract SIGNIFICANT facts" in text or "Extract" in text:
            return json.dumps({"facts": [{
                "what": "The risk committee decided to reduce Taiwan equity exposure because volatility rose.",
                "when": "N/A", "where": "N/A", "who": "risk committee", "why": "volatility rose",
                "fact_type": "world", "entities": [{"text": "risk committee"}, {"text": "Taiwan equity"}],
            }]})
        return json.dumps({"creates": [], "updates": [], "deletes": []})

    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        port = s.getsockname()[1]

    with GatewayDouble(_reply) as double:
        provider = AssistantOpenClawProvider(gateway_url=double.url, token=double.token)
        monkeypatch.setattr(provider, "_gateway_call", lambda method, *a, **k: deny_all_policy_snapshot(
            "memory-index", toolsearch_args=adapter._CLAUDE_CLI_TOOLSEARCH_ARGS,
            toolsearch_resume_args=adapter._CLAUDE_CLI_TOOLSEARCH_RESUME_ARGS))
        monkeypatch.setattr(adapter, "_OPENCLAW_AGENT_PROVIDER", provider)
        monkeypatch.setattr(adapter, "_MEMORY_LLM_BRIDGE", MemoryLlmBridge(BridgeConfig(
            enabled=True, token=bridge_token, daily_budget=100, max_concurrency=2)))
        server = uvicorn.Server(uvicorn.Config(adapter.app, host="0.0.0.0", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        try:
            _sh("docker", "network", "create", net)
            _sh("docker", "run", "-d", "--name", pg, "--network", net, "-e", "POSTGRES_USER=u",
                "-e", "POSTGRES_PASSWORD=p", "-e", "POSTGRES_DB=mem", pg_image)
            for _ in range(60):
                if _sh("docker", "exec", pg, "pg_isready", "-U", "u", "-d", "mem", check=False).returncode == 0:
                    break
                time.sleep(1)
            time.sleep(2)
            base = f"http://host.docker.internal:{port}/internal/memory-llm/v1"
            env = {
                "HINDSIGHT_API_DATABASE_URL": f"postgresql://u:p@{pg}:5432/mem",
                "HINDSIGHT_API_TEXT_SEARCH_EXTENSION": "pgroonga",
                "HINDSIGHT_API_VECTOR_EXTENSION": "pgvector",
                "HINDSIGHT_API_RUN_MIGRATIONS_ON_STARTUP": "true",
                "HINDSIGHT_API_MCP_ENABLED": "false",
                "HINDSIGHT_API_TENANT_EXTENSION": "hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension",
                "HINDSIGHT_API_TENANT_API_KEY": api_key,
                "HINDSIGHT_API_LLM_PROVIDER": "openai",
                "HINDSIGHT_API_LLM_MODEL": "memory-index",
                "HINDSIGHT_API_LLM_API_KEY": bridge_token,
                "HINDSIGHT_API_LLM_BASE_URL": base,
                "HINDSIGHT_API_RETAIN_EXTRACTION_MODE": "verbatim",
                "HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION": "false",
                "HINDSIGHT_API_WORKER_ENABLED": "true",
                "OMP_NUM_THREADS": "2",
            }
            for op in ("RETAIN", "CONSOLIDATION"):
                env[f"HINDSIGHT_API_{op}_LLM_PROVIDER"] = "openai"
                env[f"HINDSIGHT_API_{op}_LLM_MODEL"] = "memory-index"
                env[f"HINDSIGHT_API_{op}_LLM_API_KEY"] = bridge_token
                env[f"HINDSIGHT_API_{op}_LLM_BASE_URL"] = base
            args = ["docker", "run", "-d", "--name", hs, "--network", net,
                    "--add-host", "host.docker.internal:host-gateway"]
            for k, v in env.items():
                args += ["-e", f"{k}={v}"]
            _sh(*args, hs_image)
            ready = False
            for _ in range(180):
                if _sh("docker", "exec", hs, "python", "-m", "hindsight_api.http_probe",
                       "http://127.0.0.1:8888/health", "5", check=False).returncode == 0:
                    ready = True
                    break
                time.sleep(2)
            assert ready, _sh("docker", "logs", "--tail", "40", hs, check=False).stderr
            probe_src = PROBE.replace(
                "    print(json.dumps(out))",
                "    time.sleep(int(os.environ.get('PROBE_WAIT_S', '20')))\n    print(json.dumps(out))",
            )
            res = subprocess.run(
                ["docker", "exec", "-i", "-e", f"HINDSIGHT_API_TENANT_API_KEY={api_key}", hs, "python", "-"],
                input=probe_src, text=True, capture_output=True, timeout=900,
            )
            assert res.returncode == 0, res.stdout + res.stderr
            evidence = json.loads(res.stdout.strip().splitlines()[-1])
            assert evidence["retain"]["status"] == 200, evidence
            assert evidence["consolidation"]["status"] == 200, evidence
            assert len(double.requests) >= 2, double.requests
            assert {r["agent_id"] for r in double.requests} == {"memory-index"}
        finally:
            for c in (hs, pg):
                _sh("docker", "rm", "-f", c, check=False)
            _sh("docker", "network", "rm", net, check=False)
            server.should_exit = True
            thread.join(timeout=5)


# --- Gateway double (test support only) ----------------------------------------
# Speaks the pinned Gateway `POST /v1/responses` SSE shape consumed by
# AssistantOpenClawProvider._invoke_via_http and serves the `config.get` policy
# snapshot. NOT a claim about real-gateway latency or JSON validity; hosted
# acceptance (MEMORY-HINDSIGHT-HOSTED-ACCEPTANCE-20261113) measures those.
import json as _json  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
from typing import Any, Callable, Dict, List, Optional  # noqa: E402

Reply = Callable[[str, Dict[str, Any]], Any]  # (agent_id, request) -> text | (text, usage)


class GatewayDouble:
    def __init__(self, reply: Reply, *, token: str = "double-gateway-token") -> None:
        self.reply = reply
        self.token = token
        self.requests: List[Dict[str, Any]] = []  # agent_id + body, never the token
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "GatewayDouble":
        double = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/v1/responses":
                    return self._plain(404, b"{}")
                if self.headers.get("Authorization") != f"Bearer {double.token}":
                    return self._plain(401, b"{}")
                length = int(self.headers.get("Content-Length") or 0)
                body = _json.loads(self.rfile.read(length) or b"{}")
                agent_id = self.headers.get("X-OpenClaw-Agent-Id", "")
                double.requests.append({"agent_id": agent_id, "body": body})
                out = double.reply(agent_id, body)
                text, usage = out if isinstance(out, tuple) else (out, {"input_tokens": 11, "output_tokens": 5})
                events = [
                    {"type": "response.output_text.done", "text": text},
                    {"type": "response.completed",
                     "response": {"id": "resp_double", "status": "completed", "usage": usage}},
                ]
                payload = "".join(f"event: {e['type']}\ndata: {_json.dumps(e)}\n\n" for e in events)
                payload += "data: [DONE]\n\n"
                data = payload.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _plain(self, code: int, data: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def __enter__(self) -> "GatewayDouble":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()


def deny_all_policy_snapshot(agent_id: str, *, toolsearch_args: List[str], toolsearch_resume_args: List[str]) -> Dict[str, Any]:
    """The `config.get` snapshot of a correctly provisioned deny-all agent."""
    return {
        "valid": True,
        "config": {"agents": {
            "list": [{"id": agent_id, "tools": {"deny": ["*"], "exec": {"security": "deny", "ask": "always"}}}],
            "defaults": {"cliBackends": {"claude-cli": {"args": toolsearch_args, "resumeArgs": toolsearch_resume_args}}},
        }},
    }


# --- AC6: real adapter app + reusable gateway double (no real gateway) --------
# The double follows the pinned /v1/responses SSE shape. This proves the
# bridge's wire path and schema handling end to end; it makes NO claim about
# real-gateway latency or JSON validity (hosted_behavior_claimed=false), which
# MEMORY-HINDSIGHT-HOSTED-ACCEPTANCE-20261113 measures on hosted dev.
RETAIN_SCHEMA = {"name": "retain_extraction", "schema": {
    "type": "object", "required": ["facts"],
    "properties": {"facts": {"type": "array", "items": {
        "type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}}}}}
CONSOLIDATION_SCHEMA = {"name": "consolidation", "schema": {
    "type": "object", "required": ["observations"],
    "properties": {"observations": {"type": "array", "items": {"type": "string"}}}}}


def _double_reply(agent_id, request):
    text = json.dumps(request)
    if "facts" in text:
        return '```json\n{"facts": [{"text": "risk committee reduced Taiwan equity exposure"}]}\n```'
    return '{"observations": ["exposure reduced after volatility rose"]}'


@pytest.fixture()
def live_adapter(monkeypatch):
    import socket
    import time
    import urllib.request

    import uvicorn

    import main as adapter
    from assistant_openclaw_provider import AssistantOpenClawProvider

    with GatewayDouble(_double_reply) as double:
        provider = AssistantOpenClawProvider(gateway_url=double.url, token=double.token)
        monkeypatch.setattr(provider, "_gateway_call", lambda method, *a, **k: deny_all_policy_snapshot(
            "memory-index", toolsearch_args=adapter._CLAUDE_CLI_TOOLSEARCH_ARGS,
            toolsearch_resume_args=adapter._CLAUDE_CLI_TOOLSEARCH_RESUME_ARGS))
        monkeypatch.setattr(adapter, "_OPENCLAW_AGENT_PROVIDER", provider)
        monkeypatch.setattr(adapter, "_MEMORY_LLM_BRIDGE", _bridge())
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(adapter.app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        yield f"http://127.0.0.1:{port}/internal/memory-llm/v1/chat/completions", double
        server.should_exit = True
        thread.join(timeout=5)


def _post(url, body):
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    import urllib.error

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:  # keep the body: it carries the recorded reason
        return exc.code, json.loads(exc.read() or b"{}")


@pytest.mark.parametrize("schema,key", [(RETAIN_SCHEMA, "facts"), (CONSOLIDATION_SCHEMA, "observations")])
def test_retain_and_consolidation_requests_reach_deny_all_agent_with_valid_json(live_adapter, schema, key):
    url, double = live_adapter
    body = _body(model="gpt-4o-mini", response_format={"type": "json_schema", "json_schema": schema})
    status, payload = _post(url, body)
    assert status == 200, payload
    content = json.loads(payload["choices"][0]["message"]["content"])
    assert key in content and isinstance(content[key], list) and content[key]
    assert payload["usage"]["total_tokens"] == 16
    assert [r["agent_id"] for r in double.requests] == ["memory-index"]
    assert "gpt-4o-mini" not in json.dumps(double.requests)  # caller model name is never forwarded
