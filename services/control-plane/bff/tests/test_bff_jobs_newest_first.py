"""Regression tests for BFF-JOBS-LIST-NEWEST-FIRST-20261106.

Verifies:
1. GET /bff/jobs returns jobs across all sources ordered newest first by
   started_at falling back to created_at with a stable tie break by id and a
   job with no time sorting last.
2. Paging stays stable: walking every page returns each job exactly once and
   page_info.total is unchanged.
3. status and job_type filters and the source diagnostics in meta behave as
   before.
4. Real row shapes from at least two sources in an order where concatenation
   would put an older source first (e.g. research_worker_gateway and
   research_orchestrator on 2026-11-01 before source_ingestion IngestRun rows
   on 2026-11-06).
"""
from __future__ import annotations

import functools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterator, List, Tuple
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.jobs.router import create_jobs_router
from services.control_plane.bff.personas import service as persona_service
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.job_read import JobReadPort, sort_jobs_newest_first
from services.runtime_auth_inbound import encode_jwt_hs256
from services.source_ingestion.connectors.base import IngestRun, IngestRunStatus, SourceType

_SECRET = "bff-jobs-newest-first-secret-0123456789abcdef"
_ISS = "pantheon-test"
_AUD = "pantheon-bff-test"

_SOURCE_ENVS: Dict[str, Tuple[str, str]] = {
    "research_worker_gateway": (
        "PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL",
        "/api/research-worker-gateway/jobs",
    ),
    "research_orchestrator": (
        "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL",
        "/api/research-orchestrator/runs",
    ),
    "training_session": (
        "PANTHEON_TRAINING_SESSION_API_URL",
        "/api/training/preview-jobs",
    ),
    "source_ingestion": (
        "PANTHEON_SOURCE_INGEST_API_URL",
        "/api/source-ingest/jobs",
    ),
    "policy_learning": (
        "PANTHEON_POLICY_LEARNING_API_URL",
        "/api/policy-learning/jobs",
    ),
}
_ALL_ENVS = [env_var for env_var, _ in _SOURCE_ENVS.values()] + [
    "PANTHEON_SOURCE_INGEST_URL",
    "SOURCE_INGEST_URL",
    "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL",
    "PANTHEON_OPENCLAW_ADAPTER_URL",
    "OPENCLAW_GATEWAY_ADAPTER_URL",
]


class _LocalJobSourceServer:
    """Real local HTTP server for a job source owner endpoint."""

    def __init__(self, path: str, responder: Callable[[Dict[str, List[str]]], Tuple[int, Any]]) -> None:
        self.requests: List[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append(self.path)
                parsed = urlparse(self.path)
                if parsed.path != path:
                    self.send_response(404)
                    self.end_headers()
                    return
                status_code, body = responder(parse_qs(parsed.query))
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _operator_headers() -> Dict[str, str]:
    token = encode_jwt_hs256(
        {
            "sub": "op-jobs-order",
            "roles": ["operator"],
            "tenant_id": "tenant-dev",
            "exp": 4102444800,
            "iss": _ISS,
            "aud": _AUD,
        },
        secret=_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


def _real_worker_gateway_row(
    job_id: str,
    *,
    created_at: str | None,
    status: str = "completed",
) -> Dict[str, Any]:
    """Real row shape emitted by services/research-worker-gateway/main.py (no started_at)."""
    return {
        "id": job_id,
        "job_id": job_id,
        "worker": "factor-backtest-worker",
        "task_id": "rtask-20261101-01",
        "run_id": f"rrun-for-{job_id}",
        "objective": "evaluate candidate alpha decay",
        "requested_mode": "paper",
        "dispatch_mode": "paper_sandbox",
        "status": status,
        "production_activation": "disabled",
        "input_refs": ["dataset:equity-daily-v1"],
        "parameters": {"horizon_days": 5},
        "output_refs": [f"artifact:{job_id}"],
        "stdout": "ok",
        "stderr": None,
        "exit_code": 0 if status == "completed" else None,
        "created_by": "op-jobs-order",
        "created_at": created_at,
        "updated_at": created_at,
        "idempotency_key": f"idem-{job_id}",
        "rejection": None,
        "cancel_reason": None,
        "events": [],
    }


def _real_orchestrator_run_row(
    run_id: str,
    *,
    created_at: str | None,
    status: str = "running",
) -> Dict[str, Any]:
    """Real row shape emitted by services/research/main.py /api/research-orchestrator/runs (no started_at)."""
    return {
        "id": run_id,
        "run_id": run_id,
        "task_id": "rtask-20261101-02",
        "stage_id": "stage-hypothesis-eval",
        "attempt_number": 1,
        "parent_run_id": None,
        "root_run_id": run_id,
        "adapter": "qlib_factor_eval",
        "requested_mode": "paper",
        "dispatch_mode": "paper_sandbox",
        "status": status,
        "production_activation": "disabled",
        "input_refs": ["dataset:macro-rates-v1"],
        "parameters": {"window": 20},
        "created_by": "op-jobs-order",
        "tenant_id": "tenant-dev",
        "user_id": "op-jobs-order",
        "created_at": created_at,
        "updated_at": created_at,
        "idempotency_key": f"idem-{run_id}",
        "rejection": None,
        "events": [],
        "artifact_refs": [],
        "proposal_refs": [],
        "registry_writebacks": [],
    }


def _real_source_ingest_run_row(
    ingest_run_id: str,
    *,
    started_at: str,
    finished_at: str | None = None,
    status: IngestRunStatus = IngestRunStatus.COMPLETED,
) -> Dict[str, Any]:
    """Real row shape emitted by IngestRun.to_dict() in services/source_ingestion/connectors/base.py (started_at only, no created_at)."""
    run = IngestRun(
        ingest_run_id=ingest_run_id,
        connector_id="conn-market-ohlcv-daily",
        source_type=SourceType.MARKET,
        trigger_type="scheduled",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        raw_count=42,
        normalized_count=42,
        rejected_count=0,
        trace_id=f"trace-{ingest_run_id}",
        events=[],
    )
    row = run.to_dict()
    assert "started_at" in row and "created_at" not in row
    return row


def _real_policy_learning_row(
    job_id: str,
    *,
    created_at: str | None,
    status: str = "queued",
) -> Dict[str, Any]:
    """Real row shape emitted by services/policy-learning/main.py /api/policy-learning/jobs."""
    return {
        "id": job_id,
        "job_id": job_id,
        "policy_id": "pol-alloc-v1",
        "objective": "offline bandit policy evaluation",
        "adapter": "contextual_bandit",
        "requested_mode": "paper",
        "status": status,
        "production_activation": "disabled",
        "source_refs": ["dataset:alloc-episodes-v1"],
        "constraints": {"max_turnover": 0.15},
        "created_at": created_at,
        "updated_at": created_at,
        "created_by": "op-jobs-order",
        "rejection": None,
        "events": [],
    }


def _bounded_source_ingest_responder(
    runs: List[Dict[str, Any]],
) -> Callable[[Dict[str, List[str]]], Tuple[int, Any]]:
    """Simulate GET /api/source-ingest/jobs bounded pagination and status filtering."""

    def _respond(query: Dict[str, List[str]]) -> Tuple[int, Any]:
        status_raw = (query.get("status") or [""])[0]
        wanted = {part.strip().lower() for part in status_raw.split(",") if part.strip()}
        filtered = [
            r for r in runs if not wanted or str(r.get("status") or "").lower() in wanted
        ]
        page_size = int((query.get("page_size") or ["100"])[0])
        offset = int((query.get("page_token") or ["0"])[0] or "0")
        page = filtered[offset : offset + page_size]
        next_offset = offset + len(page)
        return 200, {
            "runs": page,
            "receipts": [],
            "page_info": {
                "total": len(filtered),
                "page_size": page_size,
                "next_page_token": str(next_offset) if next_offset < len(filtered) else None,
            },
        }

    return _respond


@pytest.fixture()
def job_test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Dict[str, Any]]:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", _ISS)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", _AUD)
    monkeypatch.setenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0")
    for env_name in _ALL_ENVS:
        monkeypatch.delenv(env_name, raising=False)

    closers: List[Callable[[], None]] = []

    def serve(
        source_name: str,
        rows_or_responder: Any,
    ) -> _LocalJobSourceServer:
        env_var, path = _SOURCE_ENVS[source_name]
        if callable(rows_or_responder):
            responder = rows_or_responder
        else:
            responder = lambda _q, data=rows_or_responder: (200, data)
        srv = _LocalJobSourceServer(path, responder)
        closers.append(srv.close)
        monkeypatch.setenv(env_var, srv.url)
        return srv

    try:
        yield {"monkeypatch": monkeypatch, "serve": serve}
    finally:
        for close in closers:
            close()


def _build_jobs_client() -> TestClient:
    store = create_read_surface_ports()
    dataset_surface_status = functools.partial(
        persona_service._dataset_surface_status,
        read_store=store,
    )

    def read_surface_meta(dataset: str, surface_key: str, **kwargs: Any) -> Dict[str, Any]:
        if kwargs.get("surface") is None:
            kwargs["surface"] = dataset_surface_status(
                dataset,
                snapshot_at=kwargs.get("snapshot_at"),
                has_data=kwargs.get("has_data"),
            )
        return persona_service._read_surface_meta(dataset, surface_key, **kwargs)

    app = build_bff_app()
    app.include_router(
        create_jobs_router(
            read_surface=lambda: store,
            extract_identity=persona_service._extract_identity,
            require_read_role=auth_policy.require_read_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-11-06T12:00:00Z",
            page_slice=persona_service._page_slice,
            read_surface_meta=read_surface_meta,
            dataset_surface_status=dataset_surface_status,
            raise_if_read_surface_unavailable=persona_service._raise_if_read_surface_unavailable,
            reject_body_idempotency_key=lambda _b: None,
            resolve_final_idempotency_key=lambda k1: k1 or "",
            submit_job_action=lambda *_a, **_kw: {},
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_jobs_list_orders_newest_first_by_started_at_then_created_at_tie_break_id_and_no_time_last(
    job_test_env: Dict[str, Any],
) -> None:
    """AC1 + AC4: Real row shapes across sources where concatenation puts 11/01 before 11/06."""
    serve = job_test_env["serve"]

    # Source #1 in _JOB_SOURCES: research_worker_gateway (older 11/01 row, equal-timestamp tie row, and no-time rows)
    serve(
        "research_worker_gateway",
        {
            "items": [
                _real_worker_gateway_row("wjob-20261101-001", created_at="2026-11-01T08:00:00Z"),
                _real_worker_gateway_row("wjob-20261103-tie", created_at="2026-11-03T10:00:00Z"),
                _real_worker_gateway_row("wjob-notime-a", created_at=None),
                _real_worker_gateway_row("wjob-notime-b", created_at=None),
            ]
        },
    )
    # Source #2 in _JOB_SOURCES: research_orchestrator (older 11/01 row and equal-timestamp tie row)
    serve(
        "research_orchestrator",
        {
            "items": [
                _real_orchestrator_run_row("rrun-20261101-002", created_at="2026-11-01T12:00:00Z"),
                _real_orchestrator_run_row("rrun-20261103-tie", created_at="2026-11-03T10:00:00Z"),
            ]
        },
    )
    # Source #3 in _JOB_SOURCES: training_session (row with BOTH old created_at and newer started_at)
    serve(
        "training_session",
        {
            "items": [
                {
                    "job_id": "pvjob-20261105-001",
                    "status": "running",
                    "created_at": "2026-11-01T01:00:00Z",
                    "started_at": "2026-11-05T16:30:00Z",
                }
            ]
        },
    )
    # Source #4 in _JOB_SOURCES: source_ingestion (real IngestRun.to_dict() rows with started_at on 11/06 and NO created_at)
    serve(
        "source_ingestion",
        _bounded_source_ingest_responder(
            [
                _real_source_ingest_run_row(
                    "ingest-20261106-002",
                    started_at="2026-11-06T11:45:00Z",
                    finished_at="2026-11-06T11:45:05Z",
                ),
                _real_source_ingest_run_row(
                    "ingest-20261106-001",
                    started_at="2026-11-06T09:15:00Z",
                    finished_at="2026-11-06T09:15:04Z",
                ),
            ]
        ),
    )
    # Source #5 in _JOB_SOURCES: policy_learning (equal-timestamp tie row)
    serve(
        "policy_learning",
        {
            "items": [
                _real_policy_learning_row("plj-20261103-tie", created_at="2026-11-03T10:00:00Z"),
            ]
        },
    )

    client = _build_jobs_client()

    # First page of size 2 must contain the 2026-11-06 source_ingestion jobs, NOT the 2026-11-01 jobs
    first_page = client.get("/bff/jobs?page_size=2", headers=_operator_headers())
    assert first_page.status_code == 200, first_page.text
    first_payload = first_page.json()
    assert [item["id"] for item in first_payload["items"]] == [
        "job-ingest-ingest-20261106-002",
        "job-ingest-ingest-20261106-001",
    ]
    assert first_payload["items"][0]["created_at"] is None
    assert first_payload["items"][0]["started_at"] == "2026-11-06T11:45:00Z"

    # Full list order across all 10 jobs
    full_resp = client.get("/bff/jobs?page_size=50", headers=_operator_headers())
    assert full_resp.status_code == 200, full_resp.text
    full_items = full_resp.json()["items"]
    assert [item["id"] for item in full_items] == [
        # 1. 2026-11-06 source_ingestion jobs (by started_at)
        "job-ingest-ingest-20261106-002",
        "job-ingest-ingest-20261106-001",
        # 2. 2026-11-05 training_session job (started_at=2026-11-05 overrides created_at=2026-11-01)
        "job-trainer-pvjob-20261105-001",
        # 3. 2026-11-03T10:00:00Z equal-timestamp jobs tie-broken deterministically by id
        "job-worker-wjob-20261103-tie",
        "job-policy-plj-20261103-tie",
        "job-orchestrator-rrun-20261103-tie",
        # 4. 2026-11-01 jobs (by created_at fallback)
        "job-orchestrator-rrun-20261101-002",
        "job-worker-wjob-20261101-001",
        # 5. Jobs with no started_at and no created_at sort last, tie-broken by id
        "job-worker-wjob-notime-b",
        "job-worker-wjob-notime-a",
    ]


def test_jobs_list_tie_break_by_id_is_independent_of_input_order() -> None:
    """AC1: Equal timestamps (including timezone offset equivalents) and no-time jobs sort deterministically regardless of input order."""
    candidates = [
        {"id": "job-orchestrator-rrun-100", "job_id": "job-orchestrator-rrun-100", "created_at": "2026-11-06T10:00:00Z", "started_at": None},
        {"id": "job-worker-wjob-100", "job_id": "job-worker-wjob-100", "created_at": "2026-11-06T12:00:00+02:00", "started_at": None},
        {"id": "job-ingest-ingest-100", "job_id": "job-ingest-ingest-100", "created_at": None, "started_at": "2026-11-06T10:00:00.000000Z"},
        {"id": "job-worker-wjob-no-time-1", "job_id": "job-worker-wjob-no-time-1", "created_at": None, "started_at": None},
        {"id": "job-worker-wjob-no-time-2", "job_id": "job-worker-wjob-no-time-2", "created_at": "", "started_at": ""},
    ]
    forward_ids = [item["id"] for item in sort_jobs_newest_first(candidates)]
    reverse_ids = [item["id"] for item in sort_jobs_newest_first(list(reversed(candidates)))]
    assert forward_ids == reverse_ids == [
        "job-worker-wjob-100",
        "job-orchestrator-rrun-100",
        "job-ingest-ingest-100",
        "job-worker-wjob-no-time-2",
        "job-worker-wjob-no-time-1",
    ]


def test_jobs_list_pagination_walks_every_page_once_with_unchanged_total(
    job_test_env: Dict[str, Any],
) -> None:
    """AC2: Walking every page returns each job exactly once and page_info.total is unchanged."""
    serve = job_test_env["serve"]

    serve(
        "research_worker_gateway",
        {
            "items": [
                _real_worker_gateway_row("wjob-20261101-a", created_at="2026-11-01T09:00:00Z"),
                _real_worker_gateway_row("wjob-20261104-a", created_at="2026-11-04T09:00:00Z"),
                _real_worker_gateway_row("wjob-notime", created_at=None),
            ]
        },
    )
    serve(
        "research_orchestrator",
        {
            "items": [
                _real_orchestrator_run_row("rrun-20261102-a", created_at="2026-11-02T09:00:00Z"),
                _real_orchestrator_run_row("rrun-20261104-tie", created_at="2026-11-04T09:00:00Z"),
            ]
        },
    )
    serve(
        "training_session",
        {"items": [{"job_id": "pvjob-20261105-a", "status": "completed", "started_at": "2026-11-05T09:00:00Z"}]},
    )
    serve(
        "source_ingestion",
        _bounded_source_ingest_responder(
            [
                _real_source_ingest_run_row("ingest-20261106-b", started_at="2026-11-06T10:00:00Z"),
                _real_source_ingest_run_row("ingest-20261106-a", started_at="2026-11-06T08:00:00Z"),
                _real_source_ingest_run_row("ingest-20261103-a", started_at="2026-11-03T09:00:00Z"),
            ]
        ),
    )
    serve(
        "policy_learning",
        {"items": [_real_policy_learning_row("plj-20261101-b", created_at="2026-11-01T15:00:00Z")]},
    )

    client = _build_jobs_client()

    all_resp = client.get("/bff/jobs?page_size=50", headers=_operator_headers())
    assert all_resp.status_code == 200, all_resp.text
    all_payload = all_resp.json()
    expected_ids = [item["id"] for item in all_payload["items"]]
    expected_total = len(expected_ids)
    assert expected_total == 10
    assert all_payload["page_info"]["total"] == expected_total

    walked_ids: List[str] = []
    page_token: str | None = None
    pages_seen = 0
    while True:
        params: Dict[str, Any] = {"page_size": 3}
        if page_token is not None:
            params["page_token"] = page_token
        resp = client.get("/bff/jobs", params=params, headers=_operator_headers())
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["page_info"]["total"] == expected_total
        assert payload["meta"]["total"] == expected_total
        page_ids = [item["id"] for item in payload["items"]]
        assert page_ids, "non-terminal page must not be empty"
        walked_ids.extend(page_ids)
        pages_seen += 1
        page_token = payload["page_info"]["next_page_token"]
        if page_token is None:
            break

    assert pages_seen == 4  # 3 + 3 + 3 + 1 = 10
    assert len(walked_ids) == len(set(walked_ids)) == expected_total
    assert walked_ids == expected_ids


def test_jobs_list_status_and_job_type_filters_and_meta_diagnostics_preserved(
    job_test_env: Dict[str, Any],
) -> None:
    """AC3: status and job_type filters and source diagnostics in meta behave as before."""
    serve = job_test_env["serve"]

    serve(
        "research_worker_gateway",
        {
            "items": [
                _real_worker_gateway_row("wjob-20261101-run", created_at="2026-11-01T10:00:00Z", status="running"),
                _real_worker_gateway_row("wjob-20261102-done", created_at="2026-11-02T10:00:00Z", status="completed"),
            ]
        },
    )
    serve(
        "research_orchestrator",
        {
            "items": [
                _real_orchestrator_run_row("rrun-20261105-run", created_at="2026-11-05T10:00:00Z", status="running"),
            ]
        },
    )
    serve(
        "source_ingestion",
        _bounded_source_ingest_responder(
            [
                _real_source_ingest_run_row(
                    "ingest-20261106-queued",
                    started_at="2026-11-06T11:00:00Z",
                    status=IngestRunStatus.QUEUED,
                ),
                _real_source_ingest_run_row(
                    "ingest-20261106-done",
                    started_at="2026-11-06T12:00:00Z",
                    finished_at="2026-11-06T12:00:02Z",
                    status=IngestRunStatus.COMPLETED,
                ),
            ]
        ),
    )
    # Leave training_session and policy_learning unconfigured so meta reports partial degradation.

    client = _build_jobs_client()

    # 1. status=completed filter preserves newest-first order and degraded_sources diagnostics
    completed_resp = client.get("/bff/jobs", params={"status": "completed"}, headers=_operator_headers())
    assert completed_resp.status_code == 200, completed_resp.text
    completed_payload = completed_resp.json()
    assert [item["id"] for item in completed_payload["items"]] == [
        "job-ingest-ingest-20261106-done",
        "job-worker-wjob-20261102-done",
    ]
    assert completed_payload["page_info"]["total"] == 2
    surface = completed_payload["meta"]["surfaces"]["job_list"]
    assert surface["source"] == "bff_composed"
    assert surface["status"] == "degraded"
    assert surface["degraded_sources"] == {
        "training_session": "unconfigured",
        "policy_learning": "unconfigured",
    }

    # 2. job_type=source_ingestion filter queries only source_ingestion and preserves newest-first order
    ingest_resp = client.get(
        "/bff/jobs",
        params={"job_type": "source_ingestion"},
        headers=_operator_headers(),
    )
    assert ingest_resp.status_code == 200, ingest_resp.text
    ingest_payload = ingest_resp.json()
    assert [item["id"] for item in ingest_payload["items"]] == [
        "job-ingest-ingest-20261106-done",
        "job-ingest-ingest-20261106-queued",
    ]
    assert ingest_payload["page_info"]["total"] == 2
    ingest_surface = ingest_payload["meta"]["surfaces"]["job_list"]
    assert ingest_surface["source"] == "source_ingestion"
    assert ingest_surface["status"] == "ok"
    assert "degraded_sources" not in ingest_surface
