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


def test_other_dead_letter_reasons_keep_today_behaviour(tmp_path, monkeypatch):
    config, calls = _recovery_env(monkeypatch, tmp_path, auth_ok={"v": True}, ensure_ok={"v": True})
    state = WorkflowStateStore(config.state_path)
    for rid, reason in (("a", "provider schema invalid"), ("b", "panel_case_file: http_500 boom"),
                        ("c", "panel_case_file: http_404 not found")):
        _dead(state, rid, reason)
    out = wf.recover_dead_letters(config=config, state=state, provider=None)
    assert out["redriven"] == [] and out["exhausted"] == [] and calls["auth"] == 0 and calls["ensure"] == 0
    assert state.counts(tenant_id=TENANT)["dead_letter"] == 3 and calls["posts"] == []
    assert not recovery_exhausted(state.get(tenant_id=TENANT, request_id="a"))


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
