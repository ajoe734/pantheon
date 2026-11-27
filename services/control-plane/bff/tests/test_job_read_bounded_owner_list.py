"""BFF-JOBS-LIST-BOUNDED-OWNER-READ-20261127.

GET /bff/jobs reads only the rows the requested BFF page needs from bounded
owners (source-ingest), reports the owner total, keeps rows already read when
a later owner page fails (``partial``) and caps the whole read with a deadline.

The paging logic under test is the real ``JobReadPort``; the owners are HTTP
doubles (a real local HTTP server for the router tests, a recording
``http_get`` for the port tests).
"""
from __future__ import annotations

import functools
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.jobs import router as jobs_router_module
from services.control_plane.bff.jobs.router import create_jobs_router
from services.control_plane.bff.personas import service as persona_service
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.job_read import JobReadPort, JobSourceSpec
from services.runtime_auth_inbound import encode_jwt_hs256

_SECRET = "bff-jobs-bounded-owner-secret-0123456789abcdef"
_ISS = "pantheon-test"
_AUD = "pantheon-bff-test"
_INGEST_PATH = "/api/source-ingest/jobs"
_POLICY_PATH = "/api/policy-learning/jobs"
_OWNER_MAX_PAGE_SIZE = 250  # services/source_ingestion/routers/ingest_operations.py
_ALL_ENVS = (
    "PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL",
    "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL",
    "PANTHEON_TRAINING_SESSION_API_URL",
    "PANTHEON_SOURCE_INGEST_API_URL",
    "PANTHEON_SOURCE_INGEST_URL",
    "SOURCE_INGEST_URL",
    "PANTHEON_POLICY_LEARNING_API_URL",
    "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL",
    "PANTHEON_OPENCLAW_ADAPTER_URL",
    "OPENCLAW_GATEWAY_ADAPTER_URL",
)


def _ts(minutes_ago: int) -> str:
    base = 1_795_000_000 - minutes_ago * 60  # 2026-11-18-ish, fixed
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base))


def _ingest_runs(count: int, *, prefix: str = "ingest", step: int = 1, start: int = 0) -> List[Dict[str, Any]]:
    """Owner rows newest first, as list_runs_page answers them."""
    return [
        {
            "ingest_run_id": f"{prefix}-{i:05d}",
            "connector_id": "conn-market-ohlcv-daily",
            "status": "failed" if i % 7 == 0 else "completed",
            "trigger_type": "scheduled",
            "started_at": _ts(start + i * step),
        }
        for i in range(count)
    ]


class _BoundedOwner:
    """Bounded owner double: newest-first rows, status filter, offset tokens."""

    def __init__(self, runs: List[Dict[str, Any]], *, fail_offsets: Tuple[int, ...] = ()) -> None:
        self.runs = runs
        self.fail_offsets = set(fail_offsets)
        self.requests: List[Dict[str, List[str]]] = []

    def respond(self, query: Dict[str, List[str]]) -> Tuple[int, Any]:
        self.requests.append(query)
        wanted = {p for p in (query.get("status") or [""])[0].split(",") if p}
        rows = [r for r in self.runs if not wanted or r["status"] in wanted]
        size = min(int((query.get("page_size") or ["100"])[0]), _OWNER_MAX_PAGE_SIZE)
        offset = int((query.get("page_token") or ["0"])[0] or "0")
        if offset in self.fail_offsets:
            return 503, {"detail": "owner unavailable"}
        page = rows[offset: offset + size]
        nxt = offset + len(page)
        return 200, {
            "runs": page,
            "receipts": [],
            "page_info": {"total": len(rows), "page_size": size,
                          "next_page_token": str(nxt) if nxt < len(rows) else None},
        }

    @property
    def rows_served(self) -> int:
        total = 0
        for q in self.requests:
            offset = int((q.get("page_token") or ["0"])[0] or "0")
            if offset in self.fail_offsets:
                continue
            total += min(int((q.get("page_size") or ["100"])[0]), _OWNER_MAX_PAGE_SIZE)
        return total


class _LocalOwnerServer:
    def __init__(self, path: Optional[str], responder: Callable[[Dict[str, List[str]]], Tuple[int, Any]]) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if path is not None and parsed.path != path:
                    self.send_response(404)
                    self.end_headers()
                    return
                code, body = responder(parse_qs(parsed.query))
                payload = json.dumps(body).encode("utf-8")
                self.send_response(code)
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


def _headers() -> Dict[str, str]:
    token = encode_jwt_hs256(
        {"sub": "viewer-jobs-bounded", "roles": ["viewer"], "tenant_id": "tenant-dev",
         "exp": 4102444800, "iss": _ISS, "aud": _AUD},
        secret=_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def owners(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str, str, Any], _LocalOwnerServer]]:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", _ISS)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", _AUD)
    monkeypatch.setenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0")
    for name in _ALL_ENVS:
        monkeypatch.delenv(name, raising=False)
    empty_srv = _LocalOwnerServer(None, lambda _q: (200, {"items": []}))
    servers: List[_LocalOwnerServer] = [empty_srv]
    for default_env in (
        "PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL",
        "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL",
        "PANTHEON_TRAINING_SESSION_API_URL",
        "PANTHEON_POLICY_LEARNING_API_URL",
    ):
        monkeypatch.setenv(default_env, empty_srv.url)

    def serve(env: str, path: str, responder: Any) -> _LocalOwnerServer:
        srv = _LocalOwnerServer(path, responder if callable(responder) else (lambda _q, b=responder: (200, b)))
        servers.append(srv)
        monkeypatch.setenv(env, srv.url)
        return srv

    try:
        yield serve
    finally:
        for srv in servers:
            srv.close()


def _client() -> TestClient:
    store = create_read_surface_ports()
    dataset_surface_status = functools.partial(persona_service._dataset_surface_status, read_store=store)

    def read_surface_meta(dataset: str, surface_key: str, **kwargs: Any) -> Dict[str, Any]:
        if kwargs.get("surface") is None:
            kwargs["surface"] = dataset_surface_status(
                dataset, snapshot_at=kwargs.get("snapshot_at"), has_data=kwargs.get("has_data"))
        return persona_service._read_surface_meta(dataset, surface_key, **kwargs)

    app = build_bff_app()
    app.include_router(create_jobs_router(
        read_surface=lambda: store,
        extract_identity=persona_service._extract_identity,
        require_read_role=auth_policy.require_read_role,
        bff_error=auth_policy.bff_error,
        utc_now=lambda: "2026-11-27T04:00:00Z",
        page_slice=persona_service._page_slice,
        read_surface_meta=read_surface_meta,
        dataset_surface_status=dataset_surface_status,
        raise_if_read_surface_unavailable=persona_service._raise_if_read_surface_unavailable,
        reject_body_idempotency_key=lambda _b: None,
        resolve_final_idempotency_key=lambda k: k or "",
        submit_job_action=lambda *_a, **_kw: {},
    ))
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------
# Router level: real HTTP owners, real port, real router.
# --------------------------------------------------------------------------

def test_page_one_reads_one_owner_page_and_reports_owner_total(owners: Any) -> None:
    runs = _ingest_runs(15_000)  # 60 owner pages of 250
    owner = _BoundedOwner(runs)
    owners("PANTHEON_SOURCE_INGEST_API_URL", _INGEST_PATH, owner.respond)

    resp = _client().get("/bff/jobs?page_size=20", headers=_headers())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(owner.requests) == 1
    assert owner.requests[0]["page_size"] == ["20"]
    assert "page_token" not in owner.requests[0]
    assert [j["id"] for j in body["items"]] == [f"job-ingest-{r['ingest_run_id']}" for r in runs[:20]]
    assert body["page_info"] == {"next_page_token": "20", "total": 15_000}
    assert body["meta"]["total"] == 15_000
    assert not body["meta"]["surfaces"]["job_list"].get("degraded_sources")


def test_page_three_reads_only_what_it_needs(owners: Any) -> None:
    runs = _ingest_runs(15_000)
    owner = _BoundedOwner(runs)
    owners("PANTHEON_SOURCE_INGEST_API_URL", _INGEST_PATH, owner.respond)

    body = _client().get("/bff/jobs?page_size=20&page_token=40", headers=_headers()).json()

    assert len(owner.requests) == 1
    assert owner.requests[0]["page_size"] == ["60"]
    assert owner.rows_served == 60
    assert [j["id"] for j in body["items"]] == [f"job-ingest-{r['ingest_run_id']}" for r in runs[40:60]]
    assert body["page_info"] == {"next_page_token": "60", "total": 15_000}


def test_deep_page_reads_offset_plus_page_size_across_owner_pages(owners: Any) -> None:
    runs = _ingest_runs(15_000)
    owner = _BoundedOwner(runs)
    owners("PANTHEON_SOURCE_INGEST_API_URL", _INGEST_PATH, owner.respond)

    body = _client().get("/bff/jobs?page_size=50&page_token=500", headers=_headers()).json()

    # 550 rows: 250 + 250 + 50, never the remaining 14,450.
    assert [q["page_size"] for q in owner.requests] == [["250"], ["250"], ["50"]]
    assert owner.rows_served == 550
    assert [j["id"] for j in body["items"]] == [f"job-ingest-{r['ingest_run_id']}" for r in runs[500:550]]
    assert body["page_info"]["total"] == 15_000


def test_status_filter_is_passed_to_the_owner_and_total_is_filtered(owners: Any) -> None:
    runs = _ingest_runs(15_000)
    owner = _BoundedOwner(runs)
    owners("PANTHEON_SOURCE_INGEST_API_URL", _INGEST_PATH, owner.respond)

    body = _client().get("/bff/jobs?page_size=20&status=failed", headers=_headers()).json()

    failed = [r for r in runs if r["status"] == "failed"]
    assert owner.requests[0]["status"] == ["failed"]
    assert len(owner.requests) == 1
    assert [j["id"] for j in body["items"]] == [f"job-ingest-{r['ingest_run_id']}" for r in failed[:20]]
    assert body["page_info"]["total"] == len(failed)


def test_total_adds_counted_rows_of_unbounded_sources_and_merge_is_newest_first(owners: Any) -> None:
    runs = _ingest_runs(15_000, step=2)  # minutes 0, 2, 4, ...
    owner = _BoundedOwner(runs)
    owners("PANTHEON_SOURCE_INGEST_API_URL", _INGEST_PATH, owner.respond)
    policy_rows = [
        {"job_id": f"pol-{i:03d}", "status": "queued", "created_at": _ts(2 * i + 1)}  # odd minutes
        for i in range(30)
    ]
    owners("PANTHEON_POLICY_LEARNING_API_URL", _POLICY_PATH, {"items": policy_rows})

    body = _client().get("/bff/jobs?page_size=20", headers=_headers()).json()

    ids = [j["id"] for j in body["items"]]
    expected: List[str] = []
    for i in range(10):
        expected.append(f"job-ingest-{runs[i]['ingest_run_id']}")
        expected.append(f"job-policy-pol-{i:03d}")
    assert ids == expected
    assert body["page_info"]["total"] == 15_000 + 30
    assert len(owner.requests) == 1

    filtered = _client().get("/bff/jobs?page_size=20&job_type=source_ingestion", headers=_headers()).json()
    assert [j["id"] for j in filtered["items"]] == [f"job-ingest-{r['ingest_run_id']}" for r in runs[:20]]
    assert filtered["page_info"]["total"] == 15_000


def test_later_owner_page_failure_keeps_rows_and_reports_partial(owners: Any) -> None:
    runs = _ingest_runs(15_000)
    owner = _BoundedOwner(runs, fail_offsets=(250,))
    owners("PANTHEON_SOURCE_INGEST_API_URL", _INGEST_PATH, owner.respond)

    body = _client().get("/bff/jobs?page_size=50&page_token=250", headers=_headers()).json()

    # Owner page 1 (rows 0-249) is kept; owner page 2 failed, so this BFF page is past what was read.
    assert body["items"] == []
    assert body["page_info"]["total"] == 15_000
    surface = body["meta"]["surfaces"]["job_list"]
    assert surface["degraded_sources"] == {"source_ingestion": "partial"}
    assert surface["status"] == "degraded"

    first = _client().get("/bff/jobs?page_size=50&page_token=220", headers=_headers()).json()
    assert [j["id"] for j in first["items"]] == [f"job-ingest-{r['ingest_run_id']}" for r in runs[220:250]]
    assert first["meta"]["surfaces"]["job_list"]["degraded_sources"] == {"source_ingestion": "partial"}


def test_first_owner_page_failure_reports_unreachable(owners: Any) -> None:
    owner = _BoundedOwner(_ingest_runs(15_000), fail_offsets=(0,))
    owners("PANTHEON_SOURCE_INGEST_API_URL", _INGEST_PATH, owner.respond)

    body = _client().get("/bff/jobs?page_size=20", headers=_headers()).json()

    assert body["items"] == []
    assert body["page_info"] == {"next_page_token": None, "total": 0}
    assert body["meta"]["surfaces"]["job_list"]["degraded_sources"] == {"source_ingestion": "unreachable"}


def test_stalled_source_is_reported_as_timeout_within_the_deadline(owners: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()

    def stalled(_q: Dict[str, List[str]]) -> Tuple[int, Any]:
        entered.set()
        release.wait(30)
        return 200, {"items": []}

    owner = _BoundedOwner(_ingest_runs(100))
    owners("PANTHEON_SOURCE_INGEST_API_URL", _INGEST_PATH, owner.respond)
    owners("PANTHEON_POLICY_LEARNING_API_URL", _POLICY_PATH, stalled)
    monkeypatch.setenv("PANTHEON_BFF_JOBS_LIST_DEADLINE_SECONDS", "0.2")
    try:
        body = _client().get("/bff/jobs?page_size=20", headers=_headers()).json()
        assert entered.is_set()
        assert not release.is_set()
    finally:
        release.set()

    assert body["meta"]["surfaces"]["job_list"]["degraded_sources"] == {"policy_learning": "timeout"}
    assert len(body["items"]) == 20
    assert body["page_info"]["total"] == 100


def test_deadline_default_and_env() -> None:
    assert jobs_router_module._JOBS_LIST_DEADLINE_DEFAULT_SECONDS == 5.0


# --------------------------------------------------------------------------
# Port level: real JobReadPort paging, recording http_get owner double.
# --------------------------------------------------------------------------

def _bounded_spec(name: str, prefix: str, env: str) -> JobSourceSpec:
    return JobSourceSpec(prefix=prefix, name=name, base_url_envs=(env,), list_path=_INGEST_PATH,
                         get_path_template=_INGEST_PATH + "/{native_id}", native_id_field="ingest_run_id",
                         bounded_list=True)


def _http_get_for(owners_by_base: Dict[str, _BoundedOwner], calls: List[str]) -> Callable[[str], Tuple[bool, Any]]:
    def http_get(url: str) -> Tuple[bool, Any]:
        calls.append(url)
        parsed = urlparse(url)
        owner = owners_by_base[f"{parsed.scheme}://{parsed.netloc}"]
        code, body = owner.respond(parse_qs(parsed.query))
        return (True, body) if code == 200 else (False, None)
    return http_get


def test_port_two_bounded_sources_merge_newest_first_with_owner_totals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_A_URL", "http://owner-a")
    monkeypatch.setenv("JOBS_B_URL", "http://owner-b")
    a = _BoundedOwner(_ingest_runs(15_000, prefix="a", step=3))  # minutes 0,3,6,...
    b = _BoundedOwner(_ingest_runs(9_000, prefix="b", step=3, start=1))  # minutes 1,4,7,...
    calls: List[str] = []
    port = JobReadPort(
        http_get=_http_get_for({"http://owner-a": a, "http://owner-b": b}, calls),
        sources=(_bounded_spec("src_a", "job-a-", "JOBS_A_URL"), _bounded_spec("src_b", "job-b-", "JOBS_B_URL")),
    )
    diagnostics: Dict[str, Any] = {}

    jobs = port.list_jobs_bff(diagnostics=diagnostics, limit=20, deadline_seconds=5.0)

    assert len(a.requests) == 1 and len(b.requests) == 1
    assert diagnostics["total"] == 24_000
    assert diagnostics["degraded_sources"] == {}
    merged = sorted(
        [(r["started_at"], f"job-a-{r['ingest_run_id']}") for r in a.runs[:20]]
        + [(r["started_at"], f"job-b-{r['ingest_run_id']}") for r in b.runs[:20]],
        reverse=True,
    )
    assert [j["id"] for j in jobs][:20] == [jid for _ts_, jid in merged[:20]]
    assert [j["id"] for j in jobs][:4] == ["job-a-a-00000", "job-b-b-00000", "job-a-a-00001", "job-b-b-00001"]


def test_port_partial_keeps_rows_and_owner_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_A_URL", "http://owner-a")
    a = _BoundedOwner(_ingest_runs(15_000, prefix="a"), fail_offsets=(250,))
    port = JobReadPort(http_get=_http_get_for({"http://owner-a": a}, []),
                       sources=(_bounded_spec("src_a", "job-a-", "JOBS_A_URL"),))
    diagnostics: Dict[str, Any] = {}

    jobs = port.list_jobs_bff(diagnostics=diagnostics, limit=400)

    assert len(jobs) == 250
    assert diagnostics["degraded_sources"] == {"src_a": "partial"}
    assert diagnostics["healthy_sources"] == ["src_a"]
    assert diagnostics["total"] == 15_000


def test_port_first_page_failure_is_unreachable_without_rows_or_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_A_URL", "http://owner-a")
    a = _BoundedOwner(_ingest_runs(15_000, prefix="a"), fail_offsets=(0,))
    port = JobReadPort(http_get=_http_get_for({"http://owner-a": a}, []),
                       sources=(_bounded_spec("src_a", "job-a-", "JOBS_A_URL"),))
    diagnostics: Dict[str, Any] = {}

    assert port.list_jobs_bff(diagnostics=diagnostics, limit=20) == []
    assert diagnostics["degraded_sources"] == {"src_a": "unreachable"}
    assert diagnostics["healthy_sources"] == []
    assert diagnostics["total"] == 0


def test_port_without_limit_keeps_single_page_and_crawl_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_A_URL", "http://owner-a")
    a = _BoundedOwner(_ingest_runs(600, prefix="a"))
    port = JobReadPort(http_get=_http_get_for({"http://owner-a": a}, []),
                       sources=(_bounded_spec("src_a", "job-a-", "JOBS_A_URL"),))
    diagnostics: Dict[str, Any] = {}

    single = port.list_jobs_bff(diagnostics=diagnostics, page_size=100)
    assert len(single) == 100 and len(a.requests) == 1
    assert diagnostics["degraded_sources"] == {"src_a": "truncated"}

    a.requests.clear()
    full = port.list_jobs_bff(diagnostics=diagnostics)
    assert len(full) == 600 and len(a.requests) == 3
    assert diagnostics["degraded_sources"] == {}
    assert diagnostics["total"] == 600


def _unused(_: Optional[int] = None) -> None:  # pragma: no cover - keeps Optional import meaningful
    return None
