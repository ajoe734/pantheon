"""Current Consultation provider, handoff, replay, and health acceptance."""

from __future__ import annotations

import json
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
import uvicorn

from services.consultation import main as consultation_main
from services.consultation.provider import (
    ContributionBlocked,
    HttpContributionProvider,
)
from services.consultation.store import ConsultationStore
from services.consultation.workflow_executor import (
    ExecutorConfig,
    execute_claim,
    initial_health_state,
    run_tick,
    update_functional_health,
)
from services.consultation.workflow_state import WorkflowStateStore


_ADAPTER_DIR = Path(__file__).resolve().parents[2] / "openclaw-gateway-adapter"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from assistant_openclaw_provider import (  # noqa: E402
    AssistantOpenClawProvider,
    STRUCTURED_AGENT_ID,
)
from consultation_provider import (  # noqa: E402
    CONSULTATION_CONTRIBUTION_PATH,
    QUALIFIED_CONTRIBUTION_SCHEMA,
    app as integrated_adapter_app,
    create_app,
)


TENANT = "tenant-current-consultation"
SERVICE_TOKEN = "current-consultation-service-token"
OPERATOR_TOKEN = "current-consultation-operator-token"
PROVIDER_TOKEN = "current-openclaw-provider-token"
GATEWAY_TOKEN = "local-openclaw-gateway-token"
HANDOFF_TOKEN = "current-governance-handoff-token"
SERVICE_ACTOR = "consultation-workflow-executor"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def _serve_asgi(application: Any) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() >= deadline:
            raise RuntimeError("acceptance ASGI server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _request_json(
    url: str,
    *,
    method: str = "GET",
    token: str = SERVICE_TOKEN,
    payload: dict[str, Any] | None = None,
) -> Any:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Pantheon-Tenant-Id": TENANT,
    }
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=10.0) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _default_contribution(tenant_id: str, request_id: str) -> dict[str, Any]:
    return {
        "contribution_id": f"openclaw-contribution-{request_id}",
        "tenant_id": tenant_id,
        "request_id": request_id,
        "participant_type": "committee",
        "participant_ref": "openclaw-risk-committee",
        "summary": "OpenClaw produced a qualified advisory review.",
        "recommendation": "approve_with_conditions",
        "confidence": 0.82,
        "event_type": "openclaw_committee_contribution",
        "event_content": {
            "claim": "Paper-only controls bound the reviewed risk.",
            "provider_run_id": f"openclaw-run-{request_id}",
        },
        "evidence": [
            {
                "id": f"openclaw-evidence-{request_id}",
                "evidence_type": "committee_review",
                "link": f"artifact://consultation/{request_id}",
            }
        ],
        "findings": [
            {
                "severity": "medium",
                "category": "risk",
                "claim": "Live authority remains disabled.",
                "evidence_refs": [f"openclaw-evidence-{request_id}"],
                "recommendation": "Retain paper-only rollout.",
            }
        ],
    }


def _extract_schema_and_envelope(input_text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    schema_marker = "that satisfies this JSON schema:\n"
    prompt_marker = "\n\nYou are producing an advisory Pantheon Consultation contribution."
    envelope_marker = "Consultation envelope JSON: "
    schema_obj: dict[str, Any] = {}
    envelope_obj: dict[str, Any] = {}
    if schema_marker in input_text and prompt_marker in input_text:
        raw_schema = input_text.split(schema_marker, 1)[1].split(prompt_marker, 1)[0]
        schema_obj = json.loads(raw_schema)
    if envelope_marker in input_text:
        raw_envelope = input_text.split(envelope_marker, 1)[1].strip()
        envelope_obj = json.loads(raw_envelope)
    return schema_obj, envelope_obj


@dataclass
class GatewayResponsesControl:
    calls: list[dict[str, Any]]
    findings_override: list[Any] | None = None
    contribution_override: dict[str, Any] | None = None
    raw_text_override: str | None = None
    function_calls_override: list[dict[str, Any]] | None = None
    http_error: tuple[int, dict[str, Any]] | None = None


class _GatewayResponsesServer(ThreadingHTTPServer):
    control: GatewayResponsesControl


class _GatewayResponsesHandler(BaseHTTPRequestHandler):
    server: _GatewayResponsesServer

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if (
            self.path != "/v1/responses"
            or self.headers.get("Authorization") != f"Bearer {GATEWAY_TOKEN}"
        ):
            self._write_json(403, {"error": "gateway auth failed"})
            return
        input_text = str(body.get("input") or "")
        schema_obj, envelope_obj = _extract_schema_and_envelope(input_text)
        control = self.server.control
        control.calls.append(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
                "prompt": input_text,
                "agent_id": self.headers.get("X-OpenClaw-Agent-Id"),
                "extraction_schema": schema_obj,
                "envelope": envelope_obj,
            }
        )
        if control.http_error is not None:
            status_code, err_payload = control.http_error
            self._write_json(status_code, err_payload)
            return

        tenant_id = str(envelope_obj.get("tenant_id") or TENANT)
        request_id = str(envelope_obj.get("request_id") or "unknown")
        contribution = _default_contribution(tenant_id, request_id)
        if control.findings_override is not None:
            contribution["findings"] = control.findings_override
        if control.contribution_override is not None:
            contribution = dict(control.contribution_override)

        if control.function_calls_override is not None:
            events = [
                {
                    "type": "response.completed",
                    "response": {
                        "id": f"resp-{request_id}",
                        "status": "incomplete",
                        "output": [
                            {
                                "type": "function_call",
                                "name": item.get("name", "emit_extraction"),
                                "arguments": item.get("arguments", "{}"),
                                "call_id": item.get("call_id", "call_1"),
                            }
                            for item in control.function_calls_override
                        ],
                    },
                }
            ]
        else:
            text = (
                control.raw_text_override
                if control.raw_text_override is not None
                else json.dumps(contribution)
            )
            events = [
                {"type": "response.output_text.done", "text": text},
                {
                    "type": "response.completed",
                    "response": {
                        "id": f"resp-{request_id}",
                        "status": "completed",
                        "usage": {"input_tokens": 32, "output_tokens": 48},
                    },
                },
            ]
        self._write_sse(events)

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _write_sse(self, events: list[dict[str, Any]]) -> None:
        chunks = [f"data: {json.dumps(evt)}\n\n" for evt in events]
        chunks.append("data: [DONE]\n\n")
        raw = "".join(chunks).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@contextmanager
def _serve_gateway_responses() -> Iterator[tuple[str, GatewayResponsesControl]]:
    control = GatewayResponsesControl(calls=[])
    server = _GatewayResponsesServer(("127.0.0.1", 0), _GatewayResponsesHandler)
    server.control = control
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"ws://{host}:{port}", control
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@dataclass
class HandoffControl:
    calls: list[dict[str, Any]]


class _HandoffServer(ThreadingHTTPServer):
    control: HandoffControl


class _HandoffHandler(BaseHTTPRequestHandler):
    server: _HandoffServer

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if (
            self.path != "/handoff"
            or self.headers.get("Authorization") != f"Bearer {HANDOFF_TOKEN}"
        ):
            self._write(403, {"status": "blocked"})
            return
        handoff = dict(payload["handoff"])
        self.server.control.calls.append(
            {
                "payload": payload,
                "idempotency_key": self.headers.get("Idempotency-Key"),
            }
        )
        self._write(
            200,
            {
                "acknowledged": True,
                "handoff_id": handoff["handoff_id"],
                "consumer_ref": "governance-consultation-owner",
            },
        )

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def _serve_handoff() -> Iterator[tuple[str, HandoffControl]]:
    control = HandoffControl(calls=[])
    server = _HandoffServer(("127.0.0.1", 0), _HandoffHandler)
    server.control = control
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/handoff", control
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@dataclass
class CurrentStack:
    api_url: str
    provider_url: str
    handoff_url: str
    provider: GatewayResponsesControl
    real_provider: AssistantOpenClawProvider
    handoff: HandoffControl
    root: Path

    def config(
        self,
        *,
        state_name: str = "workflow.sqlite3",
        handoff_url: str | None = None,
        max_blocked_attempts: int = 2,
    ) -> ExecutorConfig:
        return ExecutorConfig(
            api_url=self.api_url,
            tenant_id=TENANT,
            api_token=SERVICE_TOKEN,
            provider_url=self.provider_url,
            provider_token=PROVIDER_TOKEN,
            provider_service_actor=SERVICE_ACTOR,
            handoff_sink_url=(
                self.handoff_url if handoff_url is None else handoff_url
            ),
            handoff_token=HANDOFF_TOKEN,
            worker_id="current-consultation-worker",
            state_path=str(self.root / state_name),
            lease_seconds=30,
            retry_after_seconds=0,
            max_blocked_attempts=max_blocked_attempts,
            batch_size=1,
            timeout_seconds=10.0,
        )

    def create_request(self, request_id: str) -> dict[str, Any]:
        request = _request_json(
            self.api_url + "/api/consult/requests",
            method="POST",
            token=OPERATOR_TOKEN,
            payload={
                "request_id": request_id,
                "tenant_id": TENANT,
                "request_type": "strategy_review",
                "requested_by": {
                    "actor_type": "operator",
                    "actor_id": "risk-operator",
                },
                "target_type": "allocation_policy",
                "target_id": f"allocation-{request_id}",
                "task": "Review paper-only allocation risk.",
                "context_refs": [],
                "evidence_refs": [],
                "priority": "high",
                "metadata": {"paper_only": True},
                "trace_id": f"trace-{request_id}",
            },
        )
        _request_json(
            self.api_url + f"/api/consult/requests/{request_id}/submit",
            method="POST",
            token=OPERATOR_TOKEN,
            payload={},
        )
        request["status"] = "submitted"
        return request


def _forbidden_subprocess_run(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("structured consultation turn must not spawn CLI subprocess")


@pytest.fixture
def current_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[CurrentStack]:
    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("CONSULTATION_OPERATOR_TOKEN", OPERATOR_TOKEN)
    consultation_main.store = ConsultationStore(str(tmp_path / "domain"))
    consultation_main.workflow_state = WorkflowStateStore(
        tmp_path / "api-workflow.sqlite3"
    )
    with _serve_gateway_responses() as (gateway_url, gateway_control):
        real_provider = AssistantOpenClawProvider(
            gateway_url=gateway_url,
            agent_id="main",
            token=GATEWAY_TOKEN,
            _which_func=lambda _: None,
            _run_func=_forbidden_subprocess_run,
        )
        provider_app = create_app(
            provider=real_provider,
            provider_token=PROVIDER_TOKEN,
            expected_service_actor=SERVICE_ACTOR,
            replay_path=tmp_path / "provider-replay.sqlite3",
        )
        with _serve_handoff() as (handoff_url, handoff):
            with _serve_asgi(provider_app) as provider_base:
                with _serve_asgi(consultation_main.app) as api_url:
                    yield CurrentStack(
                        api_url=api_url,
                        provider_url=provider_base + CONSULTATION_CONTRIBUTION_PATH,
                        handoff_url=handoff_url,
                        provider=gateway_control,
                        real_provider=real_provider,
                        handoff=handoff,
                        root=tmp_path,
                    )


def _http_provider(config: ExecutorConfig) -> HttpContributionProvider:
    return HttpContributionProvider(
        endpoint=config.provider_url,
        bearer_token=config.provider_token,
        service_actor=config.provider_service_actor,
        timeout_seconds=config.timeout_seconds,
    )


def test_container_entrypoint_mounts_consultation_route() -> None:
    # Import in a fresh interpreter: importing consultation_provider in this
    # test module has already attached the route to main.app in this process.
    dockerfile = (_ADAPTER_DIR / "Dockerfile").read_text(encoding="utf-8")
    command = json.loads(next(line[4:] for line in dockerfile.splitlines() if line.startswith("CMD ")))
    argv = shlex.split(command[2])
    assert argv[0] == "uvicorn"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; "
            "from uvicorn.importer import import_from_string; "
            "sys.path.insert(0, sys.argv[2]); "
            "app = import_from_string(sys.argv[1]); "
            "print(json.dumps([getattr(route, 'path', None) for route in app.routes]))",
            argv[1],
            argv[argv.index("--app-dir") + 1],
        ],
        cwd=_ADAPTER_DIR.parents[1],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    paths = json.loads(result.stdout.strip().splitlines()[-1])
    assert CONSULTATION_CONTRIBUTION_PATH in paths
    assert "/livez" in paths
    assert "/api/openclaw-adapter/assistant/providers/openclaw/invoke" in paths


def test_consultation_entrypoint_preserves_existing_adapter_routes() -> None:
    paths = {getattr(route, "path", None) for route in integrated_adapter_app.routes}

    assert "/healthz" in paths
    assert "/api/openclaw-adapter/assistant/providers/openclaw/invoke" in paths
    assert "/api/openclaw-adapter/assistant/providers/openclaw/structured" in paths
    assert CONSULTATION_CONTRIBUTION_PATH in paths


def test_current_provider_publishes_one_memo_and_replays_acknowledgement(
    current_stack: CurrentStack,
) -> None:
    request_id = "current-provider-handoff"
    request = current_stack.create_request(request_id)
    config = current_stack.config()

    invalid_provider = HttpContributionProvider(
        endpoint=config.provider_url,
        bearer_token="wrong-token",
        service_actor=config.provider_service_actor,
        timeout_seconds=config.timeout_seconds,
    )
    with pytest.raises(ContributionBlocked, match="authorization is invalid") as denied:
        invalid_provider.obtain(tenant_id=TENANT, request=request)
    assert denied.value.retryable is False
    assert current_stack.provider.calls == []

    result = run_tick(config=config, state=WorkflowStateStore(config.state_path))

    assert result["completed"] == 1
    replayed_contribution = _http_provider(config).obtain(
        tenant_id=TENANT,
        request=request,
    )
    assert replayed_contribution.request_id == request_id

    recovery_config = current_stack.config(state_name="recovery.sqlite3")
    recovery_state = WorkflowStateStore(recovery_config.state_path)
    recovery_state.ensure_request(tenant_id=TENANT, request_id=request_id)
    claim = recovery_state.claim_next(
        tenant_id=TENANT,
        lease_owner="recovery-worker",
        lease_seconds=30,
    )
    assert claim is not None
    recovered = execute_claim(
        config=recovery_config,
        state=recovery_state,
        provider=_http_provider(recovery_config),
        claim=claim,
    )

    assert recovered["outcome"] == "completed"
    assert "recovered acknowledged handoff" in recovered["detail"]
    assert len(current_stack.provider.calls) == 1
    captured = current_stack.provider.calls[0]
    assert captured["path"] == "/v1/responses"
    assert captured["agent_id"] == STRUCTURED_AGENT_ID
    assert captured["agent_id"] == "structured-extraction"
    assert captured["body"]["model"] == "openclaw/structured-extraction"
    assert captured["body"]["stream"] is True
    assert "tools" not in captured["body"]
    assert "tool_choice" not in captured["body"]
    assert captured["extraction_schema"] == QUALIFIED_CONTRIBUTION_SCHEMA
    assert captured["body"]["metadata"] == {"trace_id": f"trace-{request_id}"}
    assert len(current_stack.handoff.calls) == 1

    memos = _request_json(
        current_stack.api_url + f"/api/consult/memos?request_id={request_id}"
    )
    handoffs = _request_json(
        current_stack.api_url + f"/api/consult/handoffs?request_id={request_id}"
    )
    assert len(memos) == 1
    assert memos[0]["status"] == "published"
    assert memos[0]["author_type"] == "committee"
    assert memos[0]["author_ref"] == "openclaw-risk-committee"
    assert memos[0]["findings"] == [
        {
            "severity": "medium",
            "category": "risk",
            "claim": "Live authority remains disabled.",
            "evidence_refs": [f"openclaw-evidence-{request_id}"],
            "recommendation": "Retain paper-only rollout.",
        }
    ]
    assert len(handoffs) == 1
    assert handoffs[0]["status"] == "acknowledged"


def test_missing_handoff_owner_degrades_health_until_dlq_replay(
    current_stack: CurrentStack,
) -> None:
    request_id = "current-handoff-required"
    current_stack.create_request(request_id)
    config = current_stack.config(handoff_url="", max_blocked_attempts=2)
    state = WorkflowStateStore(config.state_path)
    health = initial_health_state(config)

    blocked = run_tick(config=config, state=state)
    update_functional_health(
        health,
        blocked,
        observed_at="2026-08-14T08:00:00Z",
    )

    assert blocked["blocked"] == 1
    assert health["status"] == "degraded"
    assert health["last_success_at"] is None
    assert health["functional_health"] == {
        "ready": False,
        "blocked_count": 1,
        "dead_letter_count": 0,
        "last_success_at": None,
    }

    dead_lettered = run_tick(config=config, state=state)
    update_functional_health(
        health,
        dead_lettered,
        observed_at="2026-08-14T08:00:15Z",
    )

    assert dead_lettered["dead_lettered"] == 1
    assert health["functional_health"]["dead_letter_count"] == 1
    assert "dead-letter item(s)" in health["last_failure_reason"]
    assert len(current_stack.provider.calls) == 1
    assert current_stack.handoff.calls == []

    assert state.replay_dead_letter(tenant_id=TENANT, request_id=request_id)
    recovered_config = replace(config, handoff_sink_url=current_stack.handoff_url)
    recovered = run_tick(config=recovered_config, state=state)
    update_functional_health(
        health,
        recovered,
        observed_at="2026-08-14T08:00:30Z",
    )

    assert recovered["completed"] == 1
    assert recovered["state_counts"]["dead_letter"] == 0
    assert health["status"] == "ok"
    assert health["last_success_at"] == "2026-08-14T08:00:30Z"
    assert health["functional_health"]["ready"] is True
    assert len(current_stack.provider.calls) == 1
    assert len(current_stack.handoff.calls) == 1


def test_workflow_executor_loop_control_observation(
    current_stack: CurrentStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_id = "observation-test"
    current_stack.create_request(request_id)
    config = current_stack.config(max_blocked_attempts=1)

    monkeypatch.setenv("CONSULTATION_API_URL", config.api_url)
    monkeypatch.setenv("PANTHEON_TENANT_ID", config.tenant_id)
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", config.api_token)
    monkeypatch.setenv("CONSULTATION_PROVIDER_URL", config.provider_url)
    monkeypatch.setenv("CONSULTATION_PROVIDER_TOKEN", config.provider_token)
    monkeypatch.setenv("CONSULTATION_HANDOFF_SINK_URL", config.handoff_sink_url)
    monkeypatch.setenv("CONSULTATION_HANDOFF_TOKEN", config.handoff_token)
    monkeypatch.setenv("CONSULTATION_WORKFLOW_STATE_PATH", config.state_path)
    monkeypatch.setenv("CONSULTATION_WORKFLOW_EXECUTOR_MAX_TICKS", "1")
    monkeypatch.setenv("CONSULTATION_WORKFLOW_EXECUTOR_INTERVAL_SECONDS", "1")

    written_records: list[dict[str, Any]] = []

    class FakeLoopWriter:
        def __init__(self, dsn: str, **kwargs: Any) -> None:
            self.dsn = dsn
            self.kwargs = kwargs

        async def record_success(self, loop_id: str, **kwargs: Any) -> None:
            written_records.append({"type": "success", "loop_id": loop_id, "kwargs": kwargs})

        async def record_failure(self, loop_id: str, **kwargs: Any) -> None:
            written_records.append({"type": "failure", "loop_id": loop_id, "kwargs": kwargs})

    import services.consultation.workflow_executor as executor_module

    fake_module = SimpleNamespace(LoopControllerWriter=FakeLoopWriter)
    monkeypatch.setattr(executor_module, "importlib", SimpleNamespace(import_module=lambda name: fake_module if name == "services.loop-control" else __import__(name)))

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost:5432/fake")

    ret = executor_module.main()
    assert ret == 0
    assert len(written_records) == 1
    record = written_records[0]
    assert record["type"] == "success"
    assert record["loop_id"] == "consultation"
    assert any(
        "consultation://memos/" in ref
        for ref in record["kwargs"]["evidence_refs"]
    )


@pytest.mark.parametrize(
    "findings",
    [
        [{}],
        [
            {
                "severity": "catastrophic",
                "category": "risk",
                "claim": "c",
                "evidence_refs": ["ev-1"],
                "recommendation": "r",
            }
        ],
    ],
)
def test_malformed_findings_are_never_qualified_cached_or_submitted(
    current_stack: CurrentStack,
    findings: list[Any],
) -> None:
    request_id = "malformed-findings-handoff"
    request = current_stack.create_request(request_id)
    config = current_stack.config()
    current_stack.provider.findings_override = findings

    with pytest.raises(ContributionBlocked, match="findings\\[0\\]"):
        _http_provider(config).obtain(tenant_id=TENANT, request=request)
    result = run_tick(config=config, state=WorkflowStateStore(config.state_path))

    assert result["completed"] == 0
    assert current_stack.handoff.calls == []
    memos = _request_json(
        current_stack.api_url + f"/api/consult/memos?request_id={request_id}"
    )
    assert memos == []

    # The failed turn was released, not cached: a corrected provider turn succeeds.
    calls_before = len(current_stack.provider.calls)
    current_stack.provider.findings_override = None
    ok = _http_provider(config).obtain(tenant_id=TENANT, request=request)
    assert len(current_stack.provider.calls) == calls_before + 1
    assert ok.findings[0]["claim"] == "Live authority remains disabled."


def test_provider_prompt_describes_canonical_finding_contract(
    current_stack: CurrentStack,
) -> None:
    request = current_stack.create_request("prompt-finding-contract")
    _http_provider(current_stack.config()).obtain(tenant_id=TENANT, request=request)
    call = current_stack.provider.calls[0]
    prompt = call["prompt"]
    assert prompt.startswith("No tool is available in this turn.")
    for token in (
        "severity",
        "category",
        "claim",
        "evidence_refs",
        "recommendation",
        "critical",
        "execution_authority=none",
        "allowed_tools=[]",
    ):
        assert token in prompt
    assert "no approval, deployment" in prompt
    assert call["extraction_schema"] == QUALIFIED_CONTRIBUTION_SCHEMA


@pytest.mark.parametrize(
    "raw_text",
    [
        "Advisory review complete: recommend approve_with_conditions.",
        "```json\n{\"summary\": \"markdown fenced\"}\n```",
    ],
)
def test_non_json_provider_output_fails_closed_and_dead_letters(
    current_stack: CurrentStack,
    raw_text: str,
) -> None:
    request_id = "non-json-provider-output"
    request = current_stack.create_request(request_id)
    config = current_stack.config(max_blocked_attempts=2)
    state = WorkflowStateStore(config.state_path)
    current_stack.provider.raw_text_override = raw_text

    with pytest.raises(ContributionBlocked, match="model answer is not valid JSON") as exc_info:
        _http_provider(config).obtain(tenant_id=TENANT, request=request)
    assert exc_info.value.retryable is False

    first_tick = run_tick(config=config, state=state)
    assert first_tick["blocked"] == 1
    second_tick = run_tick(config=config, state=state)
    assert second_tick["dead_lettered"] == 1
    assert current_stack.handoff.calls == []
    memos = _request_json(
        current_stack.api_url + f"/api/consult/memos?request_id={request_id}"
    )
    assert memos == []


def test_tool_call_output_is_rejected_and_not_cached(
    current_stack: CurrentStack,
) -> None:
    request_id = "tool-call-rejected"
    request = current_stack.create_request(request_id)
    config = current_stack.config()
    current_stack.provider.function_calls_override = [
        {"name": "emit_extraction", "arguments": "{}", "call_id": "call_1"}
    ]

    with pytest.raises(
        ContributionBlocked,
        match="structured extraction must not emit tool calls",
    ) as exc_info:
        _http_provider(config).obtain(tenant_id=TENANT, request=request)
    assert exc_info.value.retryable is True
    assert len(current_stack.provider.calls) == 1

    # Replay claim was released; a clean structured JSON answer now succeeds.
    current_stack.provider.function_calls_override = None
    contribution = _http_provider(config).obtain(tenant_id=TENANT, request=request)
    assert contribution.request_id == request_id
    assert len(current_stack.provider.calls) == 2


def test_tenant_and_request_identity_mismatch_fail_closed(
    current_stack: CurrentStack,
) -> None:
    request_id = "identity-mismatch-check"
    request = current_stack.create_request(request_id)
    config = current_stack.config()
    provider_client = _http_provider(config)

    # 1. Envelope vs nested request mismatch is rejected before invoking OpenClaw.
    mismatched_nested = dict(request, tenant_id="other-tenant-id")
    with pytest.raises(
        ContributionBlocked,
        match="ConsultRequest identity does not match provider envelope",
    ):
        provider_client.obtain(tenant_id=TENANT, request=mismatched_nested)
    assert len(current_stack.provider.calls) == 0

    # 2. Provider structured output with wrong tenant_id is rejected and not cached.
    bad_tenant = _default_contribution("wrong-tenant", request_id)
    current_stack.provider.contribution_override = bad_tenant
    with pytest.raises(
        ContributionBlocked,
        match="provider contribution tenant mismatch",
    ):
        provider_client.obtain(tenant_id=TENANT, request=request)
    assert len(current_stack.provider.calls) == 1

    # 3. Provider structured output with wrong request_id is rejected and not cached.
    bad_request = _default_contribution(TENANT, "wrong-request-id")
    current_stack.provider.contribution_override = bad_request
    with pytest.raises(
        ContributionBlocked,
        match="provider contribution request mismatch",
    ):
        provider_client.obtain(tenant_id=TENANT, request=request)
    assert len(current_stack.provider.calls) == 2

    # 4. Restoring matching identity succeeds on retry.
    current_stack.provider.contribution_override = None
    valid = provider_client.obtain(tenant_id=TENANT, request=request)
    assert valid.tenant_id == TENANT
    assert valid.request_id == request_id
    assert len(current_stack.provider.calls) == 3


def test_consultation_provider_surfaces_restricted_agent_and_gateway_failures(
    current_stack: CurrentStack,
) -> None:
    request_id = "req-gateway-failure"
    request = current_stack.create_request(request_id)
    config = current_stack.config(max_blocked_attempts=2)
    state = WorkflowStateStore(config.state_path)
    health = initial_health_state(config)

    # Non-retryable 404 when structured-extraction agent is rejected/missing.
    current_stack.provider.http_error = (
        404,
        {"error": {"message": "agent 'structured-extraction' not found"}},
    )
    with pytest.raises(ContributionBlocked) as exc_404:
        _http_provider(config).obtain(tenant_id=TENANT, request=request)
    assert "structured-extraction" in exc_404.value.reason
    assert exc_404.value.retryable is False
    assert current_stack.provider.calls[0]["agent_id"] == "structured-extraction"

    # Retryable 503 upstream failure is surfaced in workflow state and health.
    current_stack.provider.http_error = (
        503,
        {"error": {"message": "openclaw gateway temporarily unavailable"}},
    )
    with pytest.raises(ContributionBlocked) as exc_503:
        _http_provider(config).obtain(tenant_id=TENANT, request=request)
    assert exc_503.value.retryable is True

    first_tick = run_tick(config=config, state=state)
    update_functional_health(health, first_tick, observed_at="2026-10-18T17:20:00Z")
    assert first_tick["blocked"] == 1
    assert "503" in first_tick["outcomes"][0]["detail"]
    assert health["status"] == "degraded"
    assert health["functional_health"]["ready"] is False
    assert health["functional_health"]["blocked_count"] == 1
    assert "blocked item(s)" in health["last_failure_reason"]

