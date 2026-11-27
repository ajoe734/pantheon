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


class _RecordingWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def record_success(self, **kwargs):
        self.calls.append(("record_success", kwargs))

    async def record_failure(self, **kwargs):
        self.calls.append(("record_failure", kwargs))


def test_idle_tick_publishes_admissible_controller_truth() -> None:
    import importlib
    from datetime import datetime, timedelta, timezone

    from services.consultation.workflow_executor import publish_loop_truth

    writer = _RecordingWriter()
    result = {
        "requests_discovered": 0,
        "completed": 0,
        "blocked": 0,
        "outcomes": [],
        "state_counts": {},
    }
    publish_loop_truth(
        writer,
        loop_id="consultation",
        result=result,
        health={"status": "ok"},
        checked_at="2026-10-08T00:00:00Z",
    )
    kind, kwargs = writer.calls[0]
    assert kind == "record_success"
    now = datetime.now(timezone.utc)
    projector = importlib.import_module("services.loop-control").project_controller_record_to_bff
    projected = projector(
        {
            "loop_id": "consultation",
            "controller_id": "c1",
            "controller_name": "consultation-workflow-executor",
            "last_heartbeat_at": now,
            "last_success_at": now,
            "lease_token": "tok",
            "lease_expires_at": now + timedelta(seconds=61),
            "desired_state": kwargs["desired_state"],
            "downstream_actual_state": kwargs["downstream_actual_state"],
            "evidence_refs": kwargs["evidence_refs"],
        },
        now=now,
    )
    assert projected["desired_state_presence"]["authoritative"] is True
    assert projected["downstream_actual_state"]["authoritative"] is True
    assert projected["evidence_refs"]
    assert projected["controller_health"]["status"] == "healthy"


# ---------------------------------------------------------------------------
# CONSULT-DEAD-LETTER-RECOVERY-AFTER-FIX-20261120: bounded automatic redrive
# ---------------------------------------------------------------------------
from services.consultation import workflow_executor as wf  # noqa: E402
from services.consultation.workflow_state import (  # noqa: E402
    AUTO_REDRIVE_MAX_PER_ITEM,
    AUTO_REDRIVE_PER_TICK,
    recovery_exhausted,
)

_AUTH = "panel_case_file: http_401:missing Bearer token"
_ENSURE_T = "consultation agent ensure transport failed for persona-x"
_ENSURE_F = "consultation agent ensure failed: persona owner or gateway unavailable/unsafe"


def _dead(state: WorkflowStateStore, request_id: str, reason: str, *, replays: int = 0) -> None:
    state.ensure_request(tenant_id=TENANT, request_id=request_id)
    claim = state.claim_next(tenant_id=TENANT, lease_owner="w", lease_seconds=30)
    assert claim is not None and claim.request_id == request_id
    state.block(claim, reason=reason, max_blocked_attempts=1, retry_after_seconds=0)
    with state._connect() as c:
        c.execute("UPDATE consultation_work_items SET replay_count=? WHERE request_id=?", (replays, request_id))


def _recovery_env(monkeypatch, tmp_path, *, auth_ok, ensure_ok):
    calls = {"auth": 0, "ensure": 0, "posts": [], "status": "in_progress"}

    def auth(_config):
        calls["auth"] += 1
        if not auth_ok["v"]:
            raise RuntimeError("http_401")

    def ensure(_config, _provider, _rid):
        calls["ensure"] += 1
        if not ensure_ok["v"]:
            raise RuntimeError("adapter down")

    monkeypatch.setattr(wf, "_probe_case_file_credentials", auth)
    monkeypatch.setattr(wf, "_probe_ensure_route", ensure)
    monkeypatch.setattr(wf, "_api_get", lambda _c, path: {"status": calls["status"]})
    monkeypatch.setattr(wf, "_api_post", lambda _c, path, body: calls["posts"].append((path, body)) or {})
    wf._EXHAUSTION_RECORDED.clear()
    config = ExecutorConfig(
        api_url="http://x", tenant_id=TENANT, api_token="t", provider_url="http://p", provider_token="t",
        provider_service_actor="a", handoff_sink_url="", handoff_token="", worker_id="w",
        state_path=str(tmp_path / "s.sqlite3"))
    return config, calls


def test_hosted_shape_recovers_only_after_probes_pass_and_converges(tmp_path, monkeypatch):
    auth_ok, ensure_ok = {"v": False}, {"v": False}
    config, calls = _recovery_env(monkeypatch, tmp_path, auth_ok=auth_ok, ensure_ok=ensure_ok)
    state = WorkflowStateStore(config.state_path)
    ids = {
        "pub": [f"consult-envelope-publication-{i}" for i in "abc"],
        "mtg": ["mtg-1f38", "mtg-6edaf", "mtg-16d4"],
        "old": ["mtg-0f7f"],
    }
    for rid in ids["pub"]:
        _dead(state, rid, _AUTH)
        with state._connect() as c:
            c.execute("UPDATE consultation_work_items SET attempt_count=56 WHERE request_id=?", (rid,))
    for rid in ids["mtg"]:
        _dead(state, rid, _ENSURE_T)
    _dead(state, ids["old"][0], _ENSURE_F)

    # probes fail: nothing is redriven, every item stays dead_letter
    out = wf.recover_dead_letters(config=config, state=state, provider=None)
    assert out["redriven"] == [] and len(out["probe_failed"]) == 2
    assert state.counts(tenant_id=TENANT)["dead_letter"] == 7

    auth_ok["v"] = ensure_ok["v"] = True
    ticks, completed = 0, []
    while state.counts(tenant_id=TENANT)["dead_letter"] and ticks < 10:
        ticks += 1
        before = dict(calls)
        out = wf.recover_dead_letters(config=config, state=state, provider=None)
        assert len(out["redriven"]) <= AUTO_REDRIVE_PER_TICK
        assert calls["auth"] - before["auth"] <= 1 and calls["ensure"] - before["ensure"] <= 1
        while (claim := state.claim_next(tenant_id=TENANT, lease_owner="w", lease_seconds=30)):
            state.complete(claim, memo_id="m-" + claim.request_id, handoff_id="h")
            completed.append(claim.request_id)
    assert ticks == 3  # 7 items, cap 3 per tick
    assert sorted(completed) == sorted(sum(ids.values(), []))
    assert state.counts(tenant_id=TENANT) == {"pending": 0, "leased": 0, "blocked": 0, "dead_letter": 0, "completed": 7}
    # a completed item is never redriven again and no request was failed
    assert wf.recover_dead_letters(config=config, state=state, provider=None)["redriven"] == []
    assert calls["posts"] == []


def test_budget_exhausted_item_stays_dead_letter_and_is_named_failed(tmp_path, monkeypatch):
    config, calls = _recovery_env(monkeypatch, tmp_path, auth_ok={"v": True}, ensure_ok={"v": True})
    state = WorkflowStateStore(config.state_path)
    _dead(state, "r-spent", _AUTH, replays=AUTO_REDRIVE_MAX_PER_ITEM)
    _dead(state, "r-last", _AUTH, replays=AUTO_REDRIVE_MAX_PER_ITEM - 1)
    out = wf.recover_dead_letters(config=config, state=state, provider=None)
    assert out["redriven"] == ["r-last"] and out["exhausted"] == ["r-spent"]
    row = state.get(tenant_id=TENANT, request_id="r-spent")
    assert row["status"] == "dead_letter" and "http_401" in row["last_error"] and recovery_exhausted(row)
    assert ("/api/consult/meetings/r-spent/sync" in [p for p, _ in calls["posts"]])
    # recorded once per process even over many ticks
    n = len(calls["posts"])
    wf.recover_dead_letters(config=config, state=state, provider=None)
    assert len(calls["posts"]) == n
    # the last item redriven once more and failing again is exhausted too
    claim = state.claim_next(tenant_id=TENANT, lease_owner="w", lease_seconds=30)
    state.block(claim, reason=_AUTH, max_blocked_attempts=1, retry_after_seconds=0)
    assert recovery_exhausted(state.get(tenant_id=TENANT, request_id="r-last"))


def test_non_recoverable_dead_letter_is_named_failed_within_one_tick(tmp_path, monkeypatch):
    config, calls = _recovery_env(monkeypatch, tmp_path, auth_ok={"v": True}, ensure_ok={"v": True})
    state = WorkflowStateStore(config.state_path)
    for rid, reason in (("a", "provider schema invalid"), ("b", "panel_case_file: http_500 boom"),
                        ("c", "panel_case_file: http_404 not found")):
        _dead(state, rid, reason)
    out = wf.recover_dead_letters(config=config, state=state, provider=None)
    assert out["redriven"] == [] and sorted(out["exhausted"]) == ["a", "b", "c"]
    assert calls["auth"] == 0 and calls["ensure"] == 0
    synced = [p for p, b in calls["posts"] if p.endswith("/sync") and b["request_status"] == "failed"]
    assert len(synced) == 3
    assert all(recovery_exhausted(state.get(tenant_id=TENANT, request_id=r)) for r in "abc")


_WB = "http_error=403 Forbidden url=http://memory/api/memory/writebacks/learn-feedback"
_MEMO = "http_error=409 Conflict url=http://api/api/consult/memos"


def test_hosted_1431_shape_ends_redriven_or_named_failed(tmp_path, monkeypatch):
    config, calls = _recovery_env(monkeypatch, tmp_path, auth_ok={"v": True}, ensure_ok={"v": True})
    wb_ok = {"v": True}
    probes = {"wb": 0}

    def wb(_config):
        probes["wb"] += 1
        if not wb_ok["v"]:
            raise RuntimeError("http_403")

    monkeypatch.setattr(wf, "_probe_writeback_identity", wb)
    state = WorkflowStateStore(config.state_path)
    for i in "abc":
        _dead(state, f"consult-envelope-publication-{i}", "caller_context_missing", replays=1)
    _dead(state, "mtg-wb1", _WB, replays=1)
    _dead(state, "mtg-wb2", _WB, replays=1)
    _dead(state, "mtg-wb3", _WB, replays=0)
    _dead(state, "mtg-memo1", _MEMO, replays=1)
    _dead(state, "mtg-memo2", _MEMO, replays=0)
    _dead(state, "mtg-roster", "roster identity refused", replays=0)

    wb_ok["v"] = False
    out = wf.recover_dead_letters(config=config, state=state, provider=None)
    assert sorted(out["exhausted"]) == sorted(
        [f"consult-envelope-publication-{i}" for i in "abc"] + ["mtg-roster"])
    assert out["probe_failed"] and probes["wb"] == 1
    assert sorted(out["redriven"]) == ["mtg-memo1", "mtg-memo2"]  # idempotent kind needs no probe

    wb_ok["v"] = True
    for _ in range(6):
        wf.recover_dead_letters(config=config, state=state, provider=None)
        while (claim := state.claim_next(tenant_id=TENANT, lease_owner="w", lease_seconds=30)):
            state.complete(claim, memo_id="m", handoff_id="h")
    failed_ids = {b["request_status"] and p.split("/")[-2] for p, b in calls["posts"] if p.endswith("/sync")}
    for row in state.list_items(tenant_id=TENANT, status="dead_letter"):
        assert row["request_id"] in failed_ids  # no dead_letter without a failed request
    assert state.counts(tenant_id=TENANT)["dead_letter"] == 4
    assert state.counts(tenant_id=TENANT)["completed"] == 5


def test_budget_survives_reason_change_between_redrives(tmp_path, monkeypatch):
    config, calls = _recovery_env(monkeypatch, tmp_path, auth_ok={"v": True}, ensure_ok={"v": True})
    monkeypatch.setattr(wf, "_probe_writeback_identity", lambda _c: None)
    state = WorkflowStateStore(config.state_path)
    _dead(state, "r", _AUTH)
    for reason in (_WB, _MEMO, _AUTH):
        out = wf.recover_dead_letters(config=config, state=state, provider=None)
        assert out["redriven"] == ["r"]
        claim = state.claim_next(tenant_id=TENANT, lease_owner="w", lease_seconds=30)
        state.block(claim, reason=reason, max_blocked_attempts=1, retry_after_seconds=0)
    out = wf.recover_dead_letters(config=config, state=state, provider=None)
    assert out["redriven"] == [] and out["exhausted"] == ["r"]


def test_completed_item_is_never_redriven_and_request_id_is_reused(tmp_path, monkeypatch):
    config, _ = _recovery_env(monkeypatch, tmp_path, auth_ok={"v": True}, ensure_ok={"v": True})
    state = WorkflowStateStore(config.state_path)
    _dead(state, "r1", _ENSURE_T)
    assert state.redrive_dead_letter(tenant_id=TENANT, request_id="r1")
    claim = state.claim_next(tenant_id=TENANT, lease_owner="w", lease_seconds=30)
    assert claim.request_id == "r1"
    state.complete(claim, memo_id="m", handoff_id="h")
    assert not state.redrive_dead_letter(tenant_id=TENANT, request_id="r1")
    assert [i["request_id"] for i in state.list_items(tenant_id=TENANT)] == ["r1"]


def test_old_release_reads_the_table_unchanged(tmp_path):
    import sqlite3
    state = WorkflowStateStore(tmp_path / "s.sqlite3")
    cols = [r[1] for r in sqlite3.connect(state.path).execute("PRAGMA table_info(consultation_work_items)")]
    assert "replay_count" in cols and "recovery" not in " ".join(cols)  # budget rides the existing column
    _dead(state, "r1", _AUTH)
    assert state.redrive_dead_letter(tenant_id=TENANT, request_id="r1")
    # the previous release's own replay path still reads and moves the same row
    assert state.get(tenant_id=TENANT, request_id="r1")["status"] == "pending"


# ---- CONSULTATION-MEMORY-READ-IDENTITY-20261120 ----------------------------
def _read_double(tokens: dict[str, tuple[str, set[str]]]):
    """Memory double enforcing services/memory/main.py _authorize_memory_read."""
    read_roles = {"researcher", "operator", "admin", "system", "analyst", "consultation_session",
                  "persona_memory_reader", "persona_session"}
    seen: list[dict[str, Any]] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            import urllib.parse as up
            q = up.parse_qs(up.urlparse(self.path).query)
            tok = self.headers.get("Authorization", "").removeprefix("Bearer ")
            ident = tokens.get(tok)
            roles = [r for r in q.get("actor_roles", []) for r in r.split(",") if r]
            actor = (q.get("actor_id") or [""])[0]
            code = 200
            if ident is None or not (ident[1] & read_roles):
                code = 403
            elif actor != ident[0] or not roles or not set(roles) <= ident[1]:
                code = 403
            seen.append({"code": code, "actor": actor, "roles": roles})
            body = json.dumps({"hits": []} if code == 200 else {"detail": {"error": "unauthorized"}}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def test_panel_case_file_composes_with_issued_read_identity(tmp_path, monkeypatch):
    import time
    from scripts.issue_dev_paper_principals import issue_environment
    from services.knowledge import case_file as cf
    from services.runtime_auth_inbound import _verify_jwt_hs256

    key = "synthetic-dev-principal-unit-key-" * 2
    now = int(time.time())
    env = {"PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
           "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true"}
    import scripts.test_issue_dev_paper_principals as t
    values = issue_environment(t.configured(), now=now)
    claims = {n: _verify_jwt_hs256(values[n], secret=key, issuer="isolated-issuer",
                                   audience="isolated-audience", now=now + 1)
              for n in ("CONSULTATION_MEMORY_READ_TOKEN", "CONSULTATION_MEMORY_WRITE_TOKEN")}
    tokens = {values[n]: (c["sub"], set(c["roles"])) for n, c in claims.items()}
    srv, seen = _read_double(tokens)
    try:
        read_file = tmp_path / "read"
        read_file.write_text(values["CONSULTATION_MEMORY_READ_TOKEN"])
        monkeypatch.setenv("CONSULTATION_MEMORY_READ_TOKEN_FILE", str(read_file))
        monkeypatch.setenv("PANTHEON_MEMORY_READ_ACTOR_ID", "pantheon-dev-consultation-memory-reader")
        monkeypatch.setenv("PANTHEON_MEMORY_READ_ACTOR_ROLES", "consultation_session")
        for k in ("PANTHEON_MEMORY_ACTOR_ID", "PANTHEON_MEMORY_ACTOR_ROLES"):
            monkeypatch.delenv(k, raising=False)  # hosted-shaped: legacy vars empty
        token, actor, roles = wf.memory_read_identity()
        assert actor and roles == ("consultation_session",)
        owners = cf.Owners(memory=cf.OwnerEndpoint(f"http://127.0.0.1:{srv.server_port}", token))
        subject = cf.Subject(decision_type="consult", persona_id="persona-a", tenant_id="tenant-dev",
                             strategy_family="fam-1", member_artifact_refs=("r1",))
        caller = cf.Caller(actor_id=actor, actor_roles=roles, session_id="consult-x", persona_id="")
        case = cf.compose_case_file(subject, caller, owners)
        assert seen and all(s["code"] == 200 for s in seen)
        assert not any("http_" in str(u) for g in case["groups"].values() for u in g["unavailable"]), case["groups"]
        # The write credential is refused as a reader (no 403-free path through it).
        bad = cf.Owners(memory=cf.OwnerEndpoint(f"http://127.0.0.1:{srv.server_port}",
                                                values["CONSULTATION_MEMORY_WRITE_TOKEN"]))
        seen.clear()
        cf.compose_case_file(subject, caller, bad)
        assert seen and all(s["code"] == 403 for s in seen)
        assert values["CONSULTATION_MEMORY_READ_TOKEN"] not in json.dumps(case)
    finally:
        srv.shutdown()


def test_memory_read_identity_never_empty_roles(monkeypatch):
    monkeypatch.delenv("CONSULTATION_MEMORY_READ_TOKEN_FILE", raising=False)
    monkeypatch.setenv("PANTHEON_MEMORY_ACTOR_ID", "")
    monkeypatch.setenv("PANTHEON_MEMORY_ACTOR_ROLES", "")
    monkeypatch.delenv("PANTHEON_MEMORY_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("PANTHEON_MEMORY_AUTH_TOKEN_FILE", raising=False)
    _t, actor, roles = wf.memory_read_identity()
    assert actor and roles


# --- CONSULTATION-WRITEBACK-PROBE-AND-PERSONA-DIRECTORY-20261120 -------------

def _memory_double(monkeypatch, *, roles, verified_tenant=TENANT):
    """Mirror of memory-svc writeback_learn_feedback: authority/tenant check, then validation."""
    seen = []

    def request(url, *, method, timeout_seconds, headers, payload=None):
        assert url.endswith("/api/memory/writebacks/learn-feedback") and method == "POST"
        seen.append(dict(payload or {}))
        authority = str((payload or {}).get("write_authority") or "")
        if not ({authority, "operator", "admin", "system", "memory-writer"} & set(roles)):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        if (payload or {}).get("tenant_id") and payload["tenant_id"] != verified_tenant:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        if not payload.get("source_event_type"):  # validation: nothing is written
            raise urllib.error.HTTPError(url, 422, "Unprocessable", {}, None)
        raise AssertionError("probe must never reach a write")

    monkeypatch.setattr(wf, "_json_request", request)
    monkeypatch.setattr(wf, "_memory_headers", lambda: {"Authorization": "Bearer x"})
    wf._WRITEBACK_PROBE_CACHE.clear()
    return seen


def _wb_config(tmp_path):
    return ExecutorConfig(
        api_url="http://x", tenant_id=TENANT, api_token="t", provider_url="http://p", provider_token="t",
        provider_service_actor="a", handoff_sink_url="", handoff_token="", worker_id="w",
        state_path=str(tmp_path / "s.sqlite3"), memory_url="http://memory")


def test_writeback_probe_passes_authority_check_and_is_rejected_only_by_validation(tmp_path, monkeypatch):
    seen = _memory_double(monkeypatch, roles=["consultation-svc"])
    wf._probe_writeback_identity(_wb_config(tmp_path))
    assert seen == [{"write_authority": "consultation-svc", "tenant_id": TENANT}]

    # The previous empty payload is refused by the authority check, as hosted.
    monkeypatch.setattr(wf, "_json_request", lambda *a, **k: (_ for _ in ()).throw(
        urllib.error.HTTPError("u", 403, "Forbidden", {}, None)))
    wf._WRITEBACK_PROBE_CACHE.clear()
    with pytest.raises(urllib.error.HTTPError):
        wf._probe_writeback_identity(_wb_config(tmp_path))


def test_writeback_probe_refuses_when_identity_lacks_authority(tmp_path, monkeypatch):
    _memory_double(monkeypatch, roles=["persona-svc"])
    with pytest.raises(urllib.error.HTTPError):
        wf._probe_writeback_identity(_wb_config(tmp_path))


def test_writeback_probe_runs_at_most_once_per_interval_while_candidates_exist(tmp_path, monkeypatch):
    seen = _memory_double(monkeypatch, roles=["persona-svc"])  # refused: the worst case
    clock = {"t": 1000.0}
    monkeypatch.setattr(wf.time, "monotonic", lambda: clock["t"])
    config = _wb_config(tmp_path)
    for _ in range(60):  # 60 executor ticks of 15 s = 15 minutes
        with pytest.raises(Exception):
            wf._probe_writeback_identity(config)
        clock["t"] += 15
    assert len(seen) == 1
    clock["t"] += wf.WRITEBACK_PROBE_INTERVAL_SECONDS
    with pytest.raises(Exception):
        wf._probe_writeback_identity(config)
    assert len(seen) == 2
    per_day = 86400 / wf.WRITEBACK_PROBE_INTERVAL_SECONDS
    assert per_day == 96 < 5760


def test_hosted_writeback_dead_letters_are_redriven_once_probe_passes(tmp_path, monkeypatch):
    config, _ = _recovery_env(monkeypatch, tmp_path, auth_ok={"v": True}, ensure_ok={"v": True})
    config = replace(config, memory_url="http://memory")
    roles = ["persona-svc"]
    seen = _memory_double(monkeypatch, roles=roles)
    clock = {"t": 0.0}
    monkeypatch.setattr(wf.time, "monotonic", lambda: clock["t"])
    state = WorkflowStateStore(config.state_path)
    ids = ["mtg-0f7fa4993625ca47", "mtg-1f38c1a87b17344a", "mtg-16d42d0b44dfeab5"]
    for rid in ids:
        _dead(state, rid, _WB, replays=1)
    out = wf.recover_dead_letters(config=config, state=state, provider=None)
    assert out["redriven"] == [] and out["probe_failed"]
    roles[:] = ["consultation-svc"]  # the issued principal is now in place
    out = wf.recover_dead_letters(config=config, state=state, provider=None)
    assert out["redriven"] == []  # still inside the interval: no new request
    clock["t"] += wf.WRITEBACK_PROBE_INTERVAL_SECONDS
    out = wf.recover_dead_letters(config=config, state=state, provider=None)
    assert sorted(out["redriven"]) == sorted(ids)
    assert len(seen) == 2
    while (claim := state.claim_next(tenant_id=TENANT, lease_owner="w", lease_seconds=30)):
        state.complete(claim, memo_id="m", handoff_id="h")
    assert state.counts(tenant_id=TENANT)["dead_letter"] == 0


# ---- MEETING-WORKFLOW-TIMEOUT-AND-MEMORY-20261122 -------------------------
def test_meeting_executor_defaults_and_overrides(monkeypatch):
    for key in ("CONSULTATION_WORKFLOW_EXECUTOR_TIMEOUT_SECONDS", "CONSULTATION_WORKFLOW_LEASE_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    config = ExecutorConfig.from_env()
    assert (config.timeout_seconds, config.lease_seconds) == (180.0, 120)
    assert ExecutorConfig.__dataclass_fields__["timeout_seconds"].default == 180.0
    assert ExecutorConfig.__dataclass_fields__["lease_seconds"].default == 120
    monkeypatch.setenv("CONSULTATION_WORKFLOW_EXECUTOR_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("CONSULTATION_WORKFLOW_LEASE_SECONDS", "90")
    config = ExecutorConfig.from_env()
    assert (config.timeout_seconds, config.lease_seconds) == (240.0, 90)


def _meeting_claim_setup(tmp_path, monkeypatch, *, lease_seconds=3):
    from services.consultation import meetings

    config = replace(_wb_config(tmp_path), lease_seconds=lease_seconds)
    state = WorkflowStateStore(config.state_path)
    state.ensure_request(tenant_id=TENANT, request_id="mtg-slow")
    claim = state.claim_next(tenant_id=TENANT, lease_owner="owner", lease_seconds=lease_seconds)
    posts = []
    monkeypatch.setattr(wf, "_api_post", lambda _c, path, body: posts.append((path, body)) or {})
    return meetings, config, state, claim, posts


def test_meeting_35_second_turn_and_slow_ensure_keep_durable_lease(tmp_path, monkeypatch):
    """Real elapsed time + SQLite fencing: another worker cannot steal either call."""
    meetings, config, state, claim, posts = _meeting_claim_setup(tmp_path, monkeypatch)
    phase = {"name": ""}
    renewals = []
    durations = {}
    renew = state.renew

    def observe_renew(claim, *, lease_seconds):
        renewed = renew(claim, lease_seconds=lease_seconds)
        renewals.append((phase["name"], renewed.lease_epoch, renewed.lease_expires_at))
        return renewed

    monkeypatch.setattr(state, "renew", observe_renew)

    class SlowProvider:
        def wait(self, name, seconds):
            phase["name"] = name
            start = time.monotonic()
            while time.monotonic() - start < seconds:
                assert state.claim_next(tenant_id=TENANT, lease_owner="rival", lease_seconds=3) is None
                time.sleep(0.05)
            durations[name] = time.monotonic() - start
            return {"phase": name}

        def ensure_agents(self, **kwargs):
            assert kwargs == {"tenant_id": TENANT, "persona_ids": ["persona-a"]}
            return self.wait("ensure", 3.2)

        def obtain(self, **kwargs):
            assert kwargs == {"tenant_id": TENANT, "request": {"request_id": claim.request_id}}
            return self.wait("obtain", 35.0)

    def execute(**kwargs):
        kwargs["renew_lease"]()
        kwargs["provider"].ensure_agents(tenant_id=TENANT, persona_ids=["persona-a"])
        kwargs["renew_lease"]()
        assert kwargs["provider"].obtain(tenant_id=TENANT, request={"request_id": claim.request_id}) == {"phase": "obtain"}
        return {"status": "closed", "outcome": "proposals_issued", "minutes": {"memo_id": "mem-slow"}}

    monkeypatch.setattr(meetings, "execute_persona_meeting", execute)
    out = wf._execute_meeting_claim(config=config, state=state, provider=SlowProvider(),
                                   claim=claim, request={}, phase_hook=None)
    assert out["outcome"] == "completed"
    assert durations["obtain"] >= 35.0
    assert len([r for r in renewals if r[0] == "ensure"]) >= 3
    assert len([r for r in renewals if r[0] == "obtain"]) >= 34
    assert all(epoch == claim.lease_epoch for _, epoch, _ in renewals)
    row = state.get(tenant_id=TENANT, request_id=claim.request_id)
    assert row["status"] == "completed" and row["memo_id"] == "mem-slow"
    assert len(posts) == 1 and posts[0][1]["request_status"] == "published"


@pytest.mark.parametrize("method", ["ensure_agents", "obtain"])
def test_meeting_provider_timeout_propagates_without_publication(tmp_path, monkeypatch, method):
    from types import SimpleNamespace

    meetings, config, state, claim, posts = _meeting_claim_setup(tmp_path, monkeypatch)
    error = TimeoutError("provider transport timed out")

    def fail(**kwargs):
        raise error

    def execute(**kwargs):
        return getattr(kwargs["provider"], method)(tenant_id=TENANT)

    monkeypatch.setattr(meetings, "execute_persona_meeting", execute)
    with pytest.raises(TimeoutError) as caught:
        wf._execute_meeting_claim(config=config, state=state,
                                  provider=SimpleNamespace(**{method: fail}), claim=claim,
                                  request={}, phase_hook=None)
    assert caught.value is error
    assert not posts
    assert state.get(tenant_id=TENANT, request_id=claim.request_id)["status"] == "leased"


def test_meeting_lost_lease_joins_provider_and_prevents_publication(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from services.consultation.workflow_state import StaleWorkflowClaim

    meetings, config, state, claim, posts = _meeting_claim_setup(tmp_path, monkeypatch, lease_seconds=1)
    joined = threading.Event()
    renewal_failed = threading.Event()

    def renew(*args):
        renewal_failed.set()
        raise StaleWorkflowClaim("claim fenced")

    def obtain(**kwargs):
        assert renewal_failed.wait(timeout=5)
        joined.set()
        return {}

    monkeypatch.setattr(wf, "_renew", renew)
    monkeypatch.setattr(meetings, "execute_persona_meeting",
                        lambda **kw: kw["provider"].obtain(tenant_id=TENANT))
    with pytest.raises(StaleWorkflowClaim, match="claim fenced"):
        wf._execute_meeting_claim(config=config, state=state,
                                  provider=SimpleNamespace(obtain=obtain), claim=claim,
                                  request={}, phase_hook=None)
    assert joined.is_set() and not posts


# ---- PUBLICATION-PANEL-CASE-FILE-SOURCES-AND-CLAIM-SUBJECT-20261127 -------------------------------

from services.consultation import workflow_executor as wf  # noqa: E402
from services.consultation.provider import QualifiedContribution  # noqa: E402
from services.consultation.tests.test_persona_panel_integration import stack  # noqa: E402,F401 (fixture)

# A pool envelope publication case file as _compose_panel_case_file merges it for a two-family pool:
# one display label in strategy_family, owner-resolved families/underlyings/members as lists, a pool
# item whose subject_fields carry no strategy_family, registry items that do, a backtest verdict item
# with no subject_fields at all.
POOL_CASE = {
    "subject": {"decision_type": "consult", "persona_id": "persona-hengyue", "tenant_id": "tenant-dev",
                "strategy_family": "mean_reversion momentum", "strategy_families": ["mean_reversion", "momentum"],
                "underlyings": ["2330", "0050"], "instrument_class": "equity",
                "member_artifact_refs": ["reg-a", "reg-b"], "backtest_run_refs": ["bt-a"], "regime_tag": ""},
    "groups": {
        "facts": {"items": [
            {"item_id": "facts:registry_entry:reg-a", "subject_fields": {
                "strategy_id": "mean_reversion", "strategy_family": "mean_reversion",
                "parameters": {"lookback": 20, "z_entry": 2.0, "allow_short": False}}},
            {"item_id": "facts:registry_entry:reg-b", "subject_fields": {
                "strategy_id": "momentum", "strategy_family": "momentum"}},
            {"item_id": "facts:strategy_pool:persona-hengyue@7", "subject_fields": {
                "pool_version": 7, "members": [{"artifact_ref": "reg-a", "weight": 0.5},
                                               {"artifact_ref": "reg-b", "weight": 0.5}]}},
            {"item_id": "facts:backtest_gate_verdict:bt-a", "subject_fields": {}},
        ]},
        "similar_history": {"items": []}, "lessons": {"items": []}, "evidence": {"items": []},
    },
}
POOL_ITEMS = [i["item_id"] for i in POOL_CASE["groups"]["facts"]["items"]]


def _pre_fix_subject_rejection(fields, refs, case):
    """The comparison as shipped before this task (whole-value equality only), for contrast."""
    items = {i["item_id"]: i for g in case["groups"].values() for i in g["items"]}
    for field, value in fields.items():
        if value is None or not any(
            (field in items[ref].get("subject_fields", {}) and value == items[ref]["subject_fields"][field])
            or (field in case["subject"] and value == case["subject"][field]) for ref in refs):
            return "claim_subject_mismatch"
    return None


def _pool_contribution(subject_fields, refs):
    return QualifiedContribution.from_payload({
        "contribution_id": "c-hy", "tenant_id": "tenant-dev", "request_id": "consult-envelope-publication-68ffaa6c8e39",
        "participant_type": "persona", "participant_ref": "persona-ee7112a829d38b9135ac", "summary": "summary",
        "recommendation": "approve", "confidence": .6,
        "event_content": {"claims": [{"claim": "pool claim", "evidence_refs": refs,
                                      "subject_fields": subject_fields}],
                          "summary_evidence_refs": refs[:1],
                          "interpretation": {"mode": "fallback", "reason": "broker_disabled", "citations": []}},
        "evidence": [{"id": r, "evidence_type": "case_file_item", "link": f"casefile://{r}"} for r in POOL_ITEMS],
        "findings": []}, expected_tenant_id="tenant-dev",
        expected_request_id="consult-envelope-publication-68ffaa6c8e39")


@pytest.mark.parametrize("fields,refs", [
    # One family of a multi-family pool, citing the pool item (which carries no strategy_family).
    ({"strategy_family": "momentum"}, ["facts:strategy_pool:persona-hengyue@7"]),
    ({"strategy_family": "mean_reversion"}, ["facts:backtest_gate_verdict:bt-a"]),
    # One member of a subject collection.
    ({"underlyings": "2330"}, ["facts:strategy_pool:persona-hengyue@7"]),
    ({"member_artifact_refs": "reg-b"}, ["facts:strategy_pool:persona-hengyue@7"]),
    ({"underlyings": ["0050"]}, ["facts:backtest_gate_verdict:bt-a"]),
    # A subset of an owner object / list of objects.
    ({"parameters": {"lookback": 20}}, ["facts:registry_entry:reg-a"]),
    ({"members": [{"artifact_ref": "reg-b"}]}, ["facts:strategy_pool:persona-hengyue@7"]),
])
def test_pool_publication_subject_assertions_the_owner_states_are_accepted(fields, refs):
    # Before the fix every one of these attributable assertions was excluded as claim_subject_mismatch.
    assert _pre_fix_subject_rejection(fields, refs, POOL_CASE) == "claim_subject_mismatch"
    assert wf._case_file_rejection(_pool_contribution(fields, refs), POOL_CASE) is None


@pytest.mark.parametrize("fields,refs", [
    ({"strategy_family": "carry"}, ["facts:strategy_pool:persona-hengyue@7"]),       # not in the pool
    ({"strategy_family": "mean_reversion momentum x"}, ["facts:registry_entry:reg-a"]),
    ({"underlyings": "2454"}, ["facts:strategy_pool:persona-hengyue@7"]),
    ({"underlyings": []}, ["facts:strategy_pool:persona-hengyue@7"]),
    ({"parameters": {"lookback": "20"}}, ["facts:registry_entry:reg-a"]),             # wrong JSON type
    ({"parameters": {"allow_short": 0}}, ["facts:registry_entry:reg-a"]),             # 0 is not false
    ({"parameters": {}}, ["facts:registry_entry:reg-a"]),
    ({"parameters": {"lookback": 20}}, ["facts:registry_entry:reg-b"]),               # not the cited owner's
    ({"persona_id": "persona-other"}, ["facts:registry_entry:reg-a"]),
    ({"pool_version": 7}, ["facts:registry_entry:reg-a"]),                            # uncited item's field
    ({"strategy_id": None}, ["facts:registry_entry:reg-a"]),
])
def test_unattributable_subject_assertions_stay_claim_subject_mismatch(fields, refs):
    contribution = _pool_contribution(fields, refs)
    assert wf._case_file_rejection(contribution, POOL_CASE) == "claim_subject_mismatch"
    reason, detail = wf._case_file_rejection_detail(contribution, POOL_CASE)
    field = next(iter(fields))
    assert reason == "claim_subject_mismatch"
    assert f'field="{field}"' in detail and refs[0] in detail and detail.startswith("node=0 ")


def test_subject_field_contract_names_assertable_fields_per_cited_item():
    contract = wf.subject_field_contract(POOL_CASE)
    assert contract["optional"] is True
    assert contract["case_subject_fields"] == sorted(POOL_CASE["subject"])
    assert contract["cited_item_subject_fields"] == {
        "facts:registry_entry:reg-a": ["parameters", "strategy_family", "strategy_id"],
        "facts:registry_entry:reg-b": ["strategy_family", "strategy_id"],
        "facts:strategy_pool:persona-hengyue@7": ["members", "pool_version"]}
    assert "claim_subject_mismatch" in contract["rule"]


def test_panel_records_rejection_detail_on_memo_sends_contract_and_issued_research_reader(stack, monkeypatch, tmp_path):
    from services.consultation.client import ConsultationServiceClient
    from services.consultation.tests.test_persona_panel_integration import (
        INITIATOR, SERVICE_TOKEN, TENANT, _bootstrap, _config, _get, _provider, _provider_fake, triggers)

    # Hosted shape: the shared research token is the persona viewer; consultation has its own reader file.
    issued = "synthetic.case-file-research.reader"
    token_file = tmp_path / wf.CASE_FILE_RESEARCH_TOKEN_ENV
    token_file.write_text(issued)
    token_file.chmod(0o600)
    monkeypatch.setenv("PANTHEON_RESEARCH_API_URL", "http://research.invalid")
    monkeypatch.setenv("PANTHEON_RESEARCH_SERVICE_TOKEN", "synthetic.persona-pool-cycle.viewer")
    monkeypatch.setenv(wf.CASE_FILE_RESEARCH_TOKEN_ENV + "_FILE", str(token_file))
    research_tokens = []
    real_compose = wf.decision_case.compose_case_file

    def spy(subject, caller, owners, **kw):
        research_tokens.append(owners.research.token)
        return real_compose(subject, caller, owners, **kw)

    monkeypatch.setattr(wf.decision_case, "compose_case_file", spy)

    _bootstrap(stack)
    risk = triggers.RISK_PERSONA_ID
    requirement = triggers.required_consultation(
        "trial_admission", "reg-trial", initiator_persona_id=INITIATOR, target_version="1")
    answers = {p.persona_id: "approve" for p in requirement.participants}
    with _provider_fake(answers) as fake:
        config = _config(stack, f"http://127.0.0.1:{fake.server_address[1]}/contribute")
        provider = _provider(config)
        real_obtain = provider.obtain

        def obtain(**kw):  # the risk persona's agent asserts a strategy_id no case item states
            got = real_obtain(**kw)
            if got.participant_ref != risk:
                return got
            claims = [{**c, "subject_fields": {"strategy_id": "carry"}} for c in got.event_content["claims"]]
            return replace(got, event_content={**got.event_content, "claims": claims})

        monkeypatch.setattr(provider, "obtain", obtain)
        created = ConsultationServiceClient(
            base_url=stack["api"], token=SERVICE_TOKEN, tenant_id=TENANT
        ).open_persona_panel(requirement, persona_id=INITIATOR, trace_id="trace-subject")
        state = WorkflowStateStore(config.state_path)
        result = wf.run_tick(config=config, state=state, provider=provider)
    assert result["completed"] == 1, result
    assert research_tokens and set(research_tokens) == {issued}

    case = fake.calls[0]["payload"]["request"]["case_file"]
    for call in fake.calls:
        assert call["payload"]["request"]["subject_field_contract"] == wf.subject_field_contract(case)
        assert "subject_field_contract" not in call["payload"]["request"]["case_file"]

    rid = created["request_id"]
    transcript = _get(stack["api"], f"/api/consult/requests/{rid}/transcript")
    rejected = [e["content"] for e in transcript["events"] if e["event_type"] == "panel_contribution_rejected"]
    assert [(r["persona_id"], r["reason"]) for r in rejected] == [(risk, "claim_subject_mismatch")]
    assert rejected[0]["detail"].startswith('node=0 field="strategy_id" asserted="carry" cited=')
    memo = _get(stack["api"], f"/api/consult/memos?request_id={rid}")[0]
    assert f'{risk}: claim_subject_mismatch (node=0 field="strategy_id" asserted="carry"' in memo["summary"]
    assert issued not in json.dumps(transcript) and issued not in memo["summary"]
