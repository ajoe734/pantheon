"""BFF-SHELL-SUMMARY-JOBS-SOURCE-20261105: shell summary decides running_jobs from the jobs read.

Real local HTTP job sources (stdlib servers, real sockets), the real
ReadSurfacePorts composition and the real ManagementService; no synthetic
dataset_source patching.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import parse_qs, urlparse

import pytest

from services.control_plane.bff.core.owner_reads import (
    authorization as owner_read_authorization,
    selected_tenant as owner_read_selected_tenant,
)
from services.control_plane.bff.management_read_models import service as service_module
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.ports import create_read_surface_ports
from services.runtime_auth_inbound import encode_jwt_hs256

_SECRET = "shell-summary-jobs-secret-0123456789abcdef"
_ISS, _AUD = "pantheon-test", "pantheon-bff-test"
_LIVE = {"bff_composed", "service_client", "service_store", "bff_cheap_count"}

_SOURCE_ENVS = {
    "research_worker_gateway": ("PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL", "/api/research-worker-gateway/jobs"),
    "research_orchestrator": ("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "/api/research-orchestrator/runs"),
    "training_session": ("PANTHEON_TRAINING_SESSION_API_URL", "/api/training/preview-jobs"),
    "source_ingestion": ("PANTHEON_SOURCE_INGEST_API_URL", "/api/source-ingest/jobs"),
    "policy_learning": ("PANTHEON_POLICY_LEARNING_API_URL", "/api/policy-learning/jobs"),
}
_ALL_ENVS = [e for e, _ in _SOURCE_ENVS.values()] + [
    "PANTHEON_SOURCE_INGEST_URL",
    "SOURCE_INGEST_URL",
]


class _Source:
    def __init__(self, path: str, body_for: Callable[[Dict[str, List[str]]], Any]) -> None:
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
                payload = json.dumps(body_for(parse_qs(parsed.query))).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_a: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class _StalledSocket:
    """Accepts connections and never answers (a real stalled socket)."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.conns: List[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(conn)

    def close(self) -> None:
        for c in self.conns:
            c.close()
        self.sock.close()


def _token() -> str:
    return encode_jwt_hs256(
        {"sub": "op-1", "roles": ["operator"], "tenant_id": "tenant-dev", "exp": 4102444800, "iss": _ISS, "aud": _AUD},
        secret=_SECRET,
    )


@pytest.fixture()
def env(monkeypatch) -> Iterator[Dict[str, Any]]:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", _ISS)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", _AUD)
    monkeypatch.setenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0")
    for name in _ALL_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("BFF_READ_SURFACE_STATE", raising=False)
    service_module._SHELL_SUMMARY_COUNT_CACHE.clear()
    closers: List[Callable[[], None]] = []
    a = owner_read_authorization.set(f"Bearer {_token()}")
    t = owner_read_selected_tenant.set("tenant-dev")
    try:
        yield {"monkeypatch": monkeypatch, "closers": closers}
    finally:
        owner_read_selected_tenant.reset(t)
        owner_read_authorization.reset(a)
        for close in closers:
            close()
        service_module._SHELL_SUMMARY_COUNT_CACHE.clear()


def _serve(env: Dict[str, Any], name: str, rows: Any) -> _Source:
    envvar, path = _SOURCE_ENVS[name]
    src = _Source(path, rows if callable(rows) else (lambda _q: rows))
    env["closers"].append(src.close)
    env["monkeypatch"].setenv(envvar, src.url)
    return src


def _stall(env: Dict[str, Any], name: str) -> _StalledSocket:
    st = _StalledSocket()
    env["closers"].append(st.close)
    env["monkeypatch"].setenv(_SOURCE_ENVS[name][0], st.url)
    return st


def _summary() -> Dict[str, Any]:
    svc = ManagementService(get_read_store=lambda: _summary.store, utc_now=lambda: "2026-11-05T00:00:00Z")
    return svc.build_shell_summary_counts(ttl_seconds=0.0)


def _healthy_all(env: Dict[str, Any]) -> None:
    _serve(env, "research_worker_gateway", {"jobs": [{"job_id": "w1", "status": "running", "created_at": "2026-11-05T01"}]})
    _serve(env, "research_orchestrator", {"runs": [{"run_id": "o1", "status": "queued"}, {"run_id": "o2", "status": "completed"}]})
    _serve(env, "training_session", [{"job_id": "t1", "status": "in_progress"}])
    _serve(env, "policy_learning", {"jobs": [{"job_id": "p1", "status": "failed"}]})

    def ingest(q: Dict[str, List[str]]) -> Dict[str, Any]:
        return {"runs": [{"ingest_run_id": "i1", "status": "queued"}], "receipts": [],
                "page_info": {"total": 1, "page_size": 200, "next_page_token": None}}

    _serve(env, "source_ingestion", ingest)


@pytest.fixture(autouse=True)
def _store():
    _summary.store = create_read_surface_ports()
    yield


def test_healthy_sources_give_ok_live_surface_and_nonzero_count(env) -> None:
    _healthy_all(env)
    payload = _summary()
    assert payload["counts"]["running_jobs"] == 4  # w1 running, o1 queued, t1 in_progress, i1 queued
    surface = payload["surfaces"]["running_jobs"]
    assert surface["status"] == "ok" and surface["source"] in _LIVE
    assert payload["surfaces"]["shell_summary"]["source"] in _LIVE
    for name, s in payload["surfaces"].items():
        assert s["source"] in _LIVE or s["status"] == "unavailable", name


def test_no_job_source_configured_is_unavailable_missing(env) -> None:
    payload = _summary()
    surface = payload["surfaces"]["running_jobs"]
    assert surface["status"] == "unavailable" and surface["source"] == "missing"


def test_partial_failure_is_degraded_live_and_names_failed_sources(env) -> None:
    _healthy_all(env)
    env["monkeypatch"].setenv("PANTHEON_POLICY_LEARNING_API_URL", "http://127.0.0.1:1")  # refused
    payload = _summary()
    surface = payload["surfaces"]["running_jobs"]
    assert surface["status"] == "degraded" and surface["source"] in _LIVE
    assert surface["degraded_sources"] == {"policy_learning": "unreachable"}
    assert payload["counts"]["running_jobs"] == 4  # healthy sources still counted


def test_all_sources_failing_is_unavailable_and_not_zero_ok(env) -> None:
    for envvar, _ in _SOURCE_ENVS.values():
        env["monkeypatch"].setenv(envvar, "http://127.0.0.1:1")
    payload = _summary()
    surface = payload["surfaces"]["running_jobs"]
    assert surface["status"] == "unavailable"
    assert set(surface["degraded_sources"]) == set(_SOURCE_ENVS)
    assert surface["source"] != "missing"  # configured but failing, not "no source"


def test_failed_read_is_not_served_healthy_to_another_caller(env) -> None:
    _healthy_all(env)
    svc = ManagementService(get_read_store=lambda: _summary.store, utc_now=lambda: "t")
    first = svc.build_shell_summary_counts(ttl_seconds=60.0)
    assert first["surfaces"]["running_jobs"]["status"] == "ok"
    other = owner_read_authorization.set("Bearer someone-else")
    try:
        second = svc.build_shell_summary_counts(ttl_seconds=60.0)
    finally:
        owner_read_authorization.reset(other)
    assert second is not first  # per-caller cache key; unauthenticated caller re-reads
    assert second["surfaces"]["running_jobs"]["status"] == "unavailable"
    assert second["counts"]["running_jobs"] == 0


def test_stalled_source_does_not_delay_beyond_budget_and_other_counts_hold(env) -> None:
    _healthy_all(env)
    _stall(env, "training_session")
    env["monkeypatch"].setenv("PANTHEON_BFF_SHELL_SUMMARY_JOBS_BUDGET_SECONDS", "0.6")
    started = time.monotonic()
    payload = _summary()
    elapsed = time.monotonic() - started
    assert elapsed < 1.2, elapsed
    surface = payload["surfaces"]["running_jobs"]
    assert surface["status"] == "degraded"
    assert surface["degraded_sources"] == {"training_session": "timeout"}
    assert payload["counts"]["running_jobs"] == 3
    print(f"STALL_EVIDENCE elapsed_s={elapsed:.3f} budget_s=0.6")


def test_shell_summary_requests_only_bounded_active_ingest_pages(env) -> None:
    _healthy_all(env)
    ingest_paths: List[str] = []
    src = _serve(env, "source_ingestion", lambda q: {"runs": [], "receipts": [], "page_info": {"total": 0, "next_page_token": None}})
    _summary()
    ingest_paths = src.requests
    assert len(ingest_paths) == 1
    q = parse_qs(urlparse(ingest_paths[0]).query)
    assert q["page_size"] == ["200"]
    assert set(q["status"][0].split(",")) == {"running", "in_progress", "queued"}


def _serve_real_source_ingestion(env: Dict[str, Any], runs: int):
    """Run the real source_ingestion app on a real socket with a hosted-size journal."""
    import importlib
    import os
    import sys
    import tempfile

    import uvicorn

    from services.source_ingestion.test_service import _seed_hosted_scale_journal

    mp = env["monkeypatch"]
    mp.setenv("SOURCE_INGEST_DATA_DIR", tempfile.mkdtemp(prefix="shell_summary_ingest_"))
    mp.setenv("SEARCH_INGEST_NOTIFY_URL", "")
    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.reload(importlib.import_module("services.source_ingestion.main"))
    _seed_hosted_scale_journal(module.runtime.store, runs)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(module.app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    env["closers"].append(lambda: setattr(server, "should_exit", True))
    mp.setenv(_SOURCE_ENVS["source_ingestion"][0], f"http://127.0.0.1:{sock.getsockname()[1]}")
    return module


def test_real_source_ingestion_at_hosted_size_is_reachable_and_totals_span_owner_pages(env) -> None:
    runs = 9000  # ~19.8 MB unbounded body, the hosted measured size (19.6 MB)
    _serve_real_source_ingestion(env, runs)
    store = create_read_surface_ports()

    diagnostics: Dict[str, Any] = {}
    started = time.monotonic()
    jobs = store.list_jobs_bff(diagnostics=diagnostics)
    full_elapsed = time.monotonic() - started
    assert diagnostics["degraded_sources"] == {"research_worker_gateway": "unconfigured",
                                               "research_orchestrator": "unconfigured",
                                               "training_session": "unconfigured",
                                               "policy_learning": "unconfigured"}
    assert diagnostics["healthy_sources"] == ["source_ingestion"]
    assert len(jobs) == len({j["job_id"] for j in jobs}) == runs  # every owner page, no loss, no dupes

    started = time.monotonic()
    payload = _summary()
    shell_elapsed = time.monotonic() - started
    surface = payload["surfaces"]["running_jobs"]
    assert surface["source"] in _LIVE and surface["status"] == "degraded"  # only the four unconfigured sources
    assert "source_ingestion" not in surface["degraded_sources"]
    assert payload["counts"]["running_jobs"] == 18  # the seeded queued runs, found without paging the 9000
    assert shell_elapsed < 2.0
    print(f"SHELL_EVIDENCE full_list_s={full_elapsed:.3f} jobs={len(jobs)} shell_s={shell_elapsed:.3f}")
