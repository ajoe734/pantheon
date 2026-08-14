"""Current Consultation provider, handoff, replay, and health acceptance."""

from __future__ import annotations

import json
import socket
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

from consultation_provider import (  # noqa: E402
    CONSULTATION_CONTRIBUTION_PATH,
    create_app,
)


TENANT = "tenant-current-consultation"
SERVICE_TOKEN = "current-consultation-service-token"
OPERATOR_TOKEN = "current-consultation-operator-token"
PROVIDER_TOKEN = "current-openclaw-provider-token"
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


class FakeOpenClawProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, prompt: str, **kwargs: Any) -> Any:
        context = dict(kwargs["context_pack"])
        request_id = str(context["request_id"])
        contribution = {
            "contribution_id": f"openclaw-contribution-{request_id}",
            "tenant_id": str(context["tenant_id"]),
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
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return SimpleNamespace(
            status="completed",
            output={
                "json_events": [
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps(contribution),
                        },
                    }
                ]
            },
        )


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
    provider: FakeOpenClawProvider
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
    fake_provider = FakeOpenClawProvider()
    provider_app = create_app(
        provider=fake_provider,
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
                    provider=fake_provider,
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
    assert len(current_stack.handoff.calls) == 1

    memos = _request_json(
        current_stack.api_url + f"/api/consult/memos?request_id={request_id}"
    )
    handoffs = _request_json(
        current_stack.api_url + f"/api/consult/handoffs?request_id={request_id}"
    )
    assert len(memos) == 1
    assert memos[0]["status"] == "published"
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
