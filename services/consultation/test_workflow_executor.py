"""L12-CONS-001 acceptance tests for the durable Consultation executor."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest
import uvicorn

from services.consultation import main as consultation_main
from services.consultation.provider import HttpContributionProvider
from services.consultation.store import ConsultationStore
from services.consultation.workflow_executor import (
    ExecutorConfig,
    execute_claim,
    run_tick,
)
from services.consultation.workflow_state import WorkflowStateStore


TENANT = "tenant-consult-a"
OTHER_TENANT = "tenant-consult-b"
SERVICE_TOKEN = "consultation-service-token"
OPERATOR_TOKEN = "consultation-operator-token"
PROVIDER_TOKEN = "provider-service-token"
HANDOFF_TOKEN = "handoff-service-token"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request_json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = SERVICE_TOKEN,
    tenant_id: str = TENANT,
    payload: dict[str, Any] | None = None,
) -> Any:
    headers = {
        "Accept": "application/json",
        "X-Pantheon-Tenant-Id": tenant_id,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
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


@dataclass
class BoundaryControl:
    mode: str = "complete"
    provider_calls: list[dict[str, Any]] | None = None
    handoff_calls: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.provider_calls = []
        self.handoff_calls = []
        self.lock = threading.Lock()


class _BoundaryServer(ThreadingHTTPServer):
    control: BoundaryControl


class _BoundaryHandler(BaseHTTPRequestHandler):
    server: _BoundaryServer

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _read(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        parsed = json.loads(raw.decode("utf-8"))
        assert isinstance(parsed, dict)
        return parsed

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        payload = self._read()
        if self.path == "/contribute":
            if self.headers.get("Authorization") != f"Bearer {PROVIDER_TOKEN}":
                self._write(403, {"detail": "provider auth failed"})
                return
            with self.server.control.lock:
                self.server.control.provider_calls.append(
                    {
                        "payload": payload,
                        "tenant": self.headers.get("X-Pantheon-Tenant-Id"),
                        "actor": self.headers.get("X-Pantheon-Service-Actor"),
                        "idempotency_key": self.headers.get("Idempotency-Key"),
                    }
                )
                mode = self.server.control.mode
            if mode == "blocked":
                self._write(
                    200,
                    {
                        "status": "blocked",
                        "reason": "qualified committee provider unavailable",
                        "retryable": True,
                    },
                )
                return
            request_id = str(payload["request_id"])
            tenant_id = str(payload["tenant_id"])
            self._write(
                200,
                {
                    "status": "completed",
                    "contribution": {
                        "contribution_id": f"contrib-{request_id}",
                        "tenant_id": tenant_id,
                        "request_id": request_id,
                        "participant_type": "committee",
                        "participant_ref": "risk-committee-provider",
                        "summary": "Qualified provider reviewed the consultation evidence.",
                        "recommendation": "approve_with_conditions",
                        "confidence": 0.84,
                        "event_type": "committee_provider_contribution",
                        "event_content": {
                            "claim": "Risk remains bounded under paper-only constraints.",
                            "provider_run_id": f"provider-run-{request_id}",
                        },
                        "evidence": [
                            {
                                "id": f"provider-evidence-{request_id}",
                                "evidence_type": "committee_review",
                                "artifact_ref": f"artifact://{request_id}/review",
                                "description": "Provider-produced consultation review.",
                                "link": f"artifact://{request_id}/review",
                            }
                        ],
                        "findings": [
                            {
                                "severity": "medium",
                                "category": "risk",
                                "claim": "Paper-only risk controls are present.",
                                "evidence_refs": [
                                    f"provider-evidence-{request_id}"
                                ],
                                "recommendation": "Keep live authority disabled.",
                            }
                        ],
                    },
                },
            )
            return
        if self.path == "/handoff":
            if self.headers.get("Authorization") != f"Bearer {HANDOFF_TOKEN}":
                self._write(403, {"detail": "handoff auth failed"})
                return
            handoff = payload.get("handoff") or {}
            with self.server.control.lock:
                self.server.control.handoff_calls.append(
                    {
                        "payload": payload,
                        "tenant": self.headers.get("X-Pantheon-Tenant-Id"),
                        "idempotency_key": self.headers.get("Idempotency-Key"),
                    }
                )
            self._write(
                200,
                {
                    "acknowledged": True,
                    "handoff_id": handoff.get("handoff_id"),
                    "consumer_ref": "governance-handoff-sink",
                },
            )
            return
        self._write(404, {"detail": "not found"})


@contextmanager
def _boundary_server() -> Iterator[tuple[str, BoundaryControl]]:
    control = BoundaryControl()
    server = _BoundaryServer(("127.0.0.1", 0), _BoundaryHandler)
    server.control = control
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", control
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def _consultation_server() -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            consultation_main.app,
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
            raise RuntimeError("consultation test server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@dataclass
class LiveStack:
    api_url: str
    boundary_url: str
    control: BoundaryControl
    root: Path

    def config(
        self,
        *,
        worker_id: str = "worker-a",
        state_path: Path | None = None,
        lease_seconds: int = 30,
        max_blocked_attempts: int = 3,
        batch_size: int = 10,
    ) -> ExecutorConfig:
        return ExecutorConfig(
            api_url=self.api_url,
            tenant_id=TENANT,
            api_token=SERVICE_TOKEN,
            provider_url=self.boundary_url + "/contribute",
            provider_token=PROVIDER_TOKEN,
            provider_service_actor="qualified-provider-adapter",
            handoff_sink_url=self.boundary_url + "/handoff",
            handoff_token=HANDOFF_TOKEN,
            worker_id=worker_id,
            state_path=str(state_path or self.root / "workflow.sqlite3"),
            lease_seconds=lease_seconds,
            retry_after_seconds=0,
            max_blocked_attempts=max_blocked_attempts,
            batch_size=batch_size,
            timeout_seconds=10.0,
        )

    def create_request(
        self,
        request_id: str,
        *,
        request_type: str = "strategy_review",
    ) -> None:
        payload = {
            "request_id": request_id,
            "tenant_id": TENANT,
            "request_type": request_type,
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
        }
        _request_json(
            self.api_url + "/api/consult/requests",
            method="POST",
            token=OPERATOR_TOKEN,
            payload=payload,
        )
        _request_json(
            self.api_url + f"/api/consult/requests/{request_id}/submit",
            method="POST",
            token=OPERATOR_TOKEN,
            payload={},
        )


@pytest.fixture
def live_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[LiveStack]:
    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("CONSULTATION_OPERATOR_TOKEN", OPERATOR_TOKEN)
    consultation_main.store = ConsultationStore(str(tmp_path / "domain"))
    consultation_main.workflow_state = WorkflowStateStore(
        tmp_path / "workflow.sqlite3"
    )
    with _boundary_server() as (boundary_url, control):
        with _consultation_server() as api_url:
            yield LiveStack(
                api_url=api_url,
                boundary_url=boundary_url,
                control=control,
                root=tmp_path,
            )


def _provider(config: ExecutorConfig) -> HttpContributionProvider:
    return HttpContributionProvider(
        endpoint=config.provider_url,
        bearer_token=config.provider_token,
        service_actor=config.provider_service_actor,
        timeout_seconds=config.timeout_seconds,
    )


def test_real_provider_auth_tenant_and_acknowledged_handoff(
    live_stack: LiveStack,
) -> None:
    request_id = "cr-real-boundary"
    live_stack.create_request(request_id)
    config = live_stack.config()
    state = WorkflowStateStore(config.state_path)

    result = run_tick(config=config, state=state)

    assert result["completed"] == 1
    assert result["dead_lettered"] == 0
    assert state.counts(tenant_id=TENANT)["completed"] == 1
    assert len(live_stack.control.provider_calls) == 1
    provider_call = live_stack.control.provider_calls[0]
    assert provider_call["tenant"] == TENANT
    assert provider_call["actor"] == "qualified-provider-adapter"
    assert provider_call["idempotency_key"].startswith("consultation-provider:")
    assert len(live_stack.control.handoff_calls) == 1

    participants = _request_json(
        live_stack.api_url
        + f"/api/consult/requests/{request_id}/participants"
    )
    memos = _request_json(
        live_stack.api_url
        + f"/api/consult/memos?request_id={request_id}"
    )
    handoffs = _request_json(
        live_stack.api_url
        + f"/api/consult/handoffs?request_id={request_id}"
    )
    assert [item["participant_ref"] for item in participants] == [
        "risk-committee-provider"
    ]
    assert len(memos) == 1
    assert memos[0]["author_type"] == "committee"
    assert memos[0]["status"] == "published"
    assert len(handoffs) == 1
    assert handoffs[0]["status"] == "acknowledged"

    with pytest.raises(urllib.error.HTTPError) as unauthenticated:
        _request_json(
            live_stack.api_url + f"/api/consult/requests/{request_id}",
            token=None,
        )
    assert unauthenticated.value.code == 401

    with pytest.raises(urllib.error.HTTPError) as cross_tenant:
        _request_json(
            live_stack.api_url + f"/api/consult/requests/{request_id}",
            tenant_id=OTHER_TENANT,
        )
    assert cross_tenant.value.code == 404


def test_two_executors_claim_once_and_create_one_memo_handoff(
    live_stack: LiveStack,
) -> None:
    request_id = "cr-race"
    live_stack.create_request(request_id, request_type="redteam")
    path = live_stack.root / "race.sqlite3"
    configs = [
        live_stack.config(worker_id="worker-a", state_path=path),
        live_stack.config(worker_id="worker-b", state_path=path),
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda config: run_tick(
                    config=config,
                    state=WorkflowStateStore(path),
                ),
                configs,
            )
        )

    assert sum(result["completed"] for result in results) == 1
    assert len(live_stack.control.provider_calls) == 1
    participants = _request_json(
        live_stack.api_url
        + f"/api/consult/requests/{request_id}/participants"
    )
    memos = _request_json(
        live_stack.api_url
        + f"/api/consult/memos?request_id={request_id}"
    )
    handoffs = _request_json(
        live_stack.api_url
        + f"/api/consult/handoffs?request_id={request_id}"
    )
    assert len(participants) == len(memos) == len(handoffs) == 1


@pytest.mark.parametrize(
    "crash_phase",
    [
        "contribution_received",
        "participant_assigned",
        "transcript_recorded",
        "evidence_recorded",
        "memo_submitted",
        "memo_published",
        "handoff_persisted",
        "handoff_acknowledged",
    ],
)
def test_phase_crash_recovers_without_duplicate_provider_or_handoff(
    live_stack: LiveStack,
    crash_phase: str,
) -> None:
    request_id = "cr-crash-" + crash_phase.replace("_", "-")
    live_stack.create_request(request_id)
    path = live_stack.root / f"{request_id}.sqlite3"
    clock = [100.0]
    state = WorkflowStateStore(path, now=lambda: clock[0])
    config = live_stack.config(
        state_path=path,
        lease_seconds=1,
        batch_size=1,
    )
    state.ensure_request(tenant_id=TENANT, request_id=request_id)
    claim = state.claim_next(
        tenant_id=TENANT,
        lease_owner="crashing-worker",
        lease_seconds=1,
    )
    assert claim is not None

    class SimulatedCrash(RuntimeError):
        pass

    def crash(phase: str) -> None:
        if phase == crash_phase:
            raise SimulatedCrash(phase)

    before_provider_calls = len(live_stack.control.provider_calls)
    with pytest.raises(SimulatedCrash, match=crash_phase):
        execute_claim(
            config=config,
            state=state,
            provider=_provider(config),
            claim=claim,
            phase_hook=crash,
        )

    clock[0] = 102.0
    recovered_state = WorkflowStateStore(path, now=lambda: clock[0])
    recovered_claim = recovered_state.claim_next(
        tenant_id=TENANT,
        lease_owner="recovery-worker",
        lease_seconds=30,
    )
    assert recovered_claim is not None
    outcome = execute_claim(
        config=replace(config, lease_seconds=30),
        state=recovered_state,
        provider=_provider(config),
        claim=recovered_claim,
    )

    assert outcome["outcome"] == "completed"
    assert (
        len(live_stack.control.provider_calls) - before_provider_calls
    ) == 1
    memos = _request_json(
        live_stack.api_url
        + f"/api/consult/memos?request_id={request_id}"
    )
    handoffs = _request_json(
        live_stack.api_url
        + f"/api/consult/handoffs?request_id={request_id}"
    )
    assert len(memos) == len(handoffs) == 1
    assert handoffs[0]["status"] == "acknowledged"


def test_bounded_blocking_dead_letters_and_operator_replay(
    live_stack: LiveStack,
) -> None:
    request_id = "cr-dlq"
    live_stack.create_request(request_id)
    path = live_stack.root / "dlq.sqlite3"
    state = WorkflowStateStore(path)
    consultation_main.workflow_state = state
    config = live_stack.config(
        state_path=path,
        max_blocked_attempts=2,
        batch_size=1,
    )
    live_stack.control.mode = "blocked"

    first = run_tick(config=config, state=state)
    second = run_tick(config=config, state=state)

    assert first["blocked"] == 1
    assert second["dead_lettered"] == 1
    item = state.get(tenant_id=TENANT, request_id=request_id)
    assert item is not None
    assert item["status"] == "dead_letter"
    assert item["blocked_count"] == 2
    assert "qualified committee provider unavailable" in item["last_error"]

    with pytest.raises(urllib.error.HTTPError) as service_replay:
        _request_json(
            live_stack.api_url
            + f"/api/consult/workflows/dead-letters/{request_id}/replay",
            method="POST",
            token=SERVICE_TOKEN,
            payload={},
        )
    assert service_replay.value.code == 403

    replayed = _request_json(
        live_stack.api_url
        + f"/api/consult/workflows/dead-letters/{request_id}/replay",
        method="POST",
        token=OPERATOR_TOKEN,
        payload={},
    )
    assert replayed["replayed"] is True
    live_stack.control.mode = "complete"

    completed = run_tick(config=config, state=state)

    assert completed["completed"] == 1
    final = state.get(tenant_id=TENANT, request_id=request_id)
    assert final is not None
    assert final["status"] == "completed"
    assert final["replay_count"] == 1
