"""BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001: Jobs source/owner contract.

Proves, with real business-logic assertions (not fixture-fake shortcuts):
  (a) each of the six sources' ``job-<source>-*`` ids dispatch to the correct
      domain owner via ``JobReadPort``, and the projected Job never carries
      ``ResearchTicket`` shape (no ``ticket_id``/``linked_experiments``).
  (b) an unrecognized job_id returns 404 (never masquerades as a ticket).
  (c) an unauthenticated request to ``GET /bff/jobs`` returns 401.
  (d) a JobAction/ExperimentAction against a source/action with no verified
      backend mutation raises ``ActionUnavailableError`` carrying
      ``action_id``/``job_id`` (or ``experiment_id``)/``reason`` — never a
      fake 200/202 "executed" receipt.
  (e) the command adapter registry has exactly one handler match per
      (command, entity, action) tuple after registering ExperimentCommandAdapter
      and JobCommandAdapter — no duplicate/shadowed first-match routing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.jobs.router import create_jobs_router
from services.control_plane.bff.ports.job_read import (
    JobReadPort,
    JobSourceUnavailableError,
    _JOB_SOURCES,
)
from services.control_plane.bff.command_adapters.base import ActionUnavailableError
from services.control_plane.bff.command_adapters.evolution_adapter import (
    EvolutionCommandAdapter,
)
from services.control_plane.bff.command_adapters.experiment_adapter import (
    ExperimentCommandAdapter,
)
from services.control_plane.bff.command_adapters.job_adapter import JobCommandAdapter
from services.control_plane.bff.command_adapters.registry import (
    _DEFAULT_ADAPTERS,
    find_adapter,
)

OPERATOR_HEADERS = {"Authorization": "Bearer op-jobs-owner-001:operator"}


# --------------------------------------------------------------------------- #
# (a) Six-source dispatch by job_id prefix, never a ResearchTicket
# --------------------------------------------------------------------------- #

_ENV_VAR_BY_PREFIX = {spec.prefix: spec.base_url_envs[0] for spec in _JOB_SOURCES}


def _fake_http_get_factory(responses: Dict[str, Dict[str, Any]]):
    calls: List[str] = []

    def _fake_http_get(url: str) -> Tuple[bool, Any]:
        calls.append(url)
        for path_suffix, payload in responses.items():
            if url.endswith(path_suffix):
                return True, payload
        return True, None

    return _fake_http_get, calls


@pytest.mark.parametrize(
    "prefix,native_id,detail_path,record",
    [
        ("job-worker-", "wjob-20260913-001", "/api/research-worker-gateway/jobs/wjob-20260913-001", {"status": "running", "job_id": "wjob-20260913-001"}),
        ("job-orchestrator-", "rrun-20260913-001", "/api/research-orchestrator/runs/rrun-20260913-001", {"status": "running", "run_id": "rrun-20260913-001"}),
        ("job-trainer-", "pvjob-abc123", "/api/training/preview-jobs/pvjob-abc123", {"status": "pending", "job_id": "pvjob-abc123"}),
        ("job-ingest-", "ingest-abc123", "/api/source-ingest/jobs/ingest-abc123", {"status": "completed", "ingest_run_id": "ingest-abc123"}),
        ("job-policy-", "plj-20260913-001", "/api/policy-learning/jobs/plj-20260913-001", {"status": "queued", "job_id": "plj-20260913-001"}),
        ("job-openclaw-", "wf-abc123", "/api/openclaw-adapter/workflows/jobs/wf-abc123", {"status": "running", "job_id": "wf-abc123"}),
    ],
)
def test_job_read_port_dispatches_each_source_by_prefix(monkeypatch, prefix, native_id, detail_path, record) -> None:
    env_var = _ENV_VAR_BY_PREFIX[prefix]
    monkeypatch.setenv(env_var, "http://fake-source.local")
    fake_get, calls = _fake_http_get_factory({detail_path: record})
    port = JobReadPort(http_get=fake_get)

    job_id = f"{prefix}{native_id}"
    job = port.get_job_bff(job_id)

    assert job is not None
    assert job["job_id"] == job_id
    assert job["native_id"] == native_id
    assert calls and calls[0] == f"http://fake-source.local{detail_path}"
    # Never a ResearchTicket in disguise.
    assert "ticket_id" not in job
    assert "linked_experiments" not in job
    assert "priority" not in job  # ticket-only field


def test_job_read_port_list_composes_across_listable_sources(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL", "http://worker.local")
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://orchestrator.local")
    monkeypatch.delenv("PANTHEON_TRAINING_SESSION_API_URL", raising=False)
    monkeypatch.delenv("PANTHEON_SOURCE_INGEST_API_URL", raising=False)
    monkeypatch.delenv("PANTHEON_SOURCE_INGEST_URL", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_URL", raising=False)
    monkeypatch.delenv("PANTHEON_POLICY_LEARNING_API_URL", raising=False)

    def fake_get(url: str) -> Tuple[bool, Any]:
        if url == "http://worker.local/api/research-worker-gateway/jobs":
            return True, {"items": [{"job_id": "wjob-20260913-001", "status": "running", "created_at": "2026-09-13T00:00:00Z"}]}
        if url == "http://orchestrator.local/api/research-orchestrator/runs":
            return True, {"items": [{"run_id": "rrun-20260913-001", "status": "completed", "created_at": "2026-09-12T00:00:00Z"}]}
        return True, None

    port = JobReadPort(http_get=fake_get)
    jobs = port.list_jobs_bff()
    job_ids = {j["job_id"] for j in jobs}
    assert job_ids == {"job-worker-wjob-20260913-001", "job-orchestrator-rrun-20260913-001"}
    # Unconfigured sources are recorded as degraded, not fabricated as empty success.
    degraded = port.get_degraded_sources()
    assert degraded.get("training_session") == "unconfigured"
    assert degraded.get("policy_learning") == "unconfigured"
    assert degraded.get("source_ingestion") == "unconfigured"
    # OpenClaw never appears in list composition (read-only detail-only source).
    assert "openclaw_gateway_adapter" not in {j["source"] for j in jobs}


def test_job_read_port_unreachable_source_raises_for_get(monkeypatch) -> None:
    monkeypatch.delenv("PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL", raising=False)
    port = JobReadPort(http_get=lambda url: (True, None))
    with pytest.raises(JobSourceUnavailableError):
        port.get_job_bff("job-worker-wjob-20260913-999")


# --------------------------------------------------------------------------- #
# (b) Unknown job_id -> 404, (c) unauthenticated -> 401
# --------------------------------------------------------------------------- #


class _JobsOnlyReadStore:
    """Minimal canonical-store double: delegates strictly to a real JobReadPort.

    Mirrors ``ReadSurfacePorts.dataset_source`` where ``"jobs"`` is not a legacy
    canonical-store dataset and therefore returns ``"missing"`` unless surface
    metadata is derived from the canonical owner job result.
    """

    def __init__(self, job_read: JobReadPort) -> None:
        self._job_read = job_read

    def get_job_bff(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._job_read.get_job_bff(job_id)

    def list_jobs_bff(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self._job_read.list_jobs_bff(**kwargs)

    def get_job_logs_bff(self, job_id: str) -> List[Dict[str, Any]]:
        return self._job_read.get_job_logs_bff(job_id)

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "missing"


import functools

from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.personas import service as persona_service


def _bind_read_surface_helpers(store: _JobsOnlyReadStore):
    dataset_surface_status = functools.partial(
        persona_service._dataset_surface_status, read_store=store
    )

    def _read_surface_meta(dataset: str, surface_key: str, **kwargs: Any) -> Dict[str, Any]:
        if kwargs.get("surface") is None:
            kwargs["surface"] = dataset_surface_status(
                dataset,
                snapshot_at=kwargs.get("snapshot_at"),
                has_data=kwargs.get("has_data"),
            )
        return persona_service._read_surface_meta(dataset, surface_key, **kwargs)

    return (
        _read_surface_meta,
        dataset_surface_status,
        persona_service._raise_if_read_surface_unavailable,
    )


def _fresh_client() -> TestClient:
    store = _JobsOnlyReadStore(JobReadPort(http_get=lambda url: (True, None)))
    read_surface_meta, dataset_surface_status, raise_if_read_surface_unavailable = (
        _bind_read_surface_helpers(store)
    )
    app = build_bff_app()
    app.include_router(
        create_jobs_router(
            read_surface=lambda: store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-09-13T00:00:00Z",
            page_slice=lambda items, token=None, size=20: (list(items[:size]), None),
            read_surface_meta=read_surface_meta,
            dataset_surface_status=dataset_surface_status,
            raise_if_read_surface_unavailable=raise_if_read_surface_unavailable,
            reject_body_idempotency_key=lambda b: None,
            resolve_final_idempotency_key=lambda k1, k2: k1 or k2 or "",
            submit_job_action=lambda *a, **kw: {},
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_unknown_job_id_returns_404() -> None:
    client = _fresh_client()
    resp = client.get("/bff/jobs/not-a-recognized-job-id-prefix", headers=OPERATOR_HEADERS)
    assert resp.status_code == 404


def test_jobs_list_unauthenticated_returns_401() -> None:
    client = _fresh_client()
    resp = client.get("/bff/jobs")
    assert resp.status_code == 401


def test_job_detail_unauthenticated_returns_401() -> None:
    client = _fresh_client()
    resp = client.get("/bff/jobs/job-worker-wjob-20260913-001")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# (d) Actions without a verified backend fail closed, never fake 200/202
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "job_id",
    [
        "job-worker-wjob-20260913-001",
        "job-orchestrator-rrun-20260913-001",
        "job-trainer-pvjob-abc123",
        "job-ingest-ingest-abc123",
        "job-policy-plj-20260913-001",
        "job-openclaw-wf-abc123",
    ],
)
def test_job_action_fails_closed_for_every_source(job_id: str) -> None:
    adapter = JobCommandAdapter()
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            command_id="cmd-1",
            command_type="JobAction",
            params={"action_id": "cancel", "job_id": job_id},
        )
    exc = excinfo.value
    assert exc.action_id == "cancel"
    assert getattr(exc, "job_id", None) == job_id
    assert getattr(exc, "reason", None) in {"owner_operation_unsupported", "owner_operation_not_in_scope"}
    assert exc.downstream_status in (400, 503)


def test_job_action_unknown_prefix_is_not_in_scope() -> None:
    adapter = JobCommandAdapter()
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            command_id="cmd-2",
            command_type="JobAction",
            params={"action_id": "cancel", "job_id": "totally-unrecognized-id"},
        )
    assert excinfo.value.reason == "owner_operation_not_in_scope"
    assert excinfo.value.downstream_status == 400


def test_experiment_action_unimplemented_obligations_fail_closed() -> None:
    adapter = ExperimentCommandAdapter(research_write_owner_factory=lambda: None)
    for action_id in ("invalidated", "attached_to_review", "archived", "retry", "some_unknown_action"):
        with pytest.raises(ActionUnavailableError):
            adapter.execute(
                command_id="cmd-3",
                command_type="ExperimentAction",
                params={"action_id": action_id, "experiment_id": "exp-20260913-001"},
            )


def test_experiment_cancel_fails_closed_when_owner_unconfigured() -> None:
    adapter = ExperimentCommandAdapter(research_write_owner_factory=lambda: None)
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            command_id="cmd-4",
            command_type="ExperimentAction",
            params={"action_id": "cancel", "experiment_id": "exp-20260913-001"},
        )
    assert excinfo.value.downstream_status == 503


def test_experiment_cancel_real_success_via_injected_owner() -> None:
    class _Owner:
        def cancel_research_experiment(self, experiment_id, *, completed_at=None):
            assert experiment_id == "exp-20260913-001"
            return {"experiment_id": experiment_id, "status": "canceled", "completed_at": completed_at}

    adapter = ExperimentCommandAdapter(research_write_owner_factory=lambda: _Owner())
    receipt = adapter.execute(
        command_id="cmd-5",
        command_type="ExperimentAction",
        params={"action_id": "cancel", "experiment_id": "exp-20260913-001"},
    )
    assert receipt["status"] == "canceled"
    assert receipt["entity_id"] == "exp-20260913-001"
    assert receipt["domain_receipt"]["status"] == "canceled"


def test_evolution_adapter_no_longer_handles_experiment_or_job_actions() -> None:
    adapter = EvolutionCommandAdapter()
    assert adapter.can_handle("ExperimentAction", "experiment", "cancel") is False
    assert adapter.can_handle("JobAction", "job", "cancel") is False
    assert not hasattr(adapter, "_execute_experiment_or_job")


# --------------------------------------------------------------------------- #
# (e) Registry: exactly one handler match per (command, entity, action)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "command_type,entity_type,action_id",
    [
        ("ExperimentAction", "experiment", "cancel"),
        ("ExperimentAction", "researchexperiment", "cancel"),
        ("JobAction", "job", "cancel"),
        ("EvolutionProgramAction", "evolutionprogram", "pause"),
        ("ApproveEvolutionDecision", "evolutiondecision", "approve"),
    ],
)
def test_registry_has_exactly_one_matching_adapter(command_type, entity_type, action_id) -> None:
    matches = [a for a in _DEFAULT_ADAPTERS if a.can_handle(command_type, entity_type, action_id)]
    assert len(matches) == 1, (
        f"Expected exactly one adapter to handle ({command_type!r}, {entity_type!r}, {action_id!r}), "
        f"got {[type(a).__name__ for a in matches]}"
    )


def test_registry_dispatches_experiment_and_job_action_to_dedicated_adapters() -> None:
    assert isinstance(find_adapter("ExperimentAction", "experiment", "cancel"), ExperimentCommandAdapter)
    assert isinstance(find_adapter("JobAction", "job", "cancel"), JobCommandAdapter)


def test_registry_experiment_and_job_adapters_registered_ahead_of_evolution() -> None:
    order = [type(a).__name__ for a in _DEFAULT_ADAPTERS]
    assert order.index("ExperimentCommandAdapter") < order.index("EvolutionCommandAdapter")
    assert order.index("JobCommandAdapter") < order.index("EvolutionCommandAdapter")


# --------------------------------------------------------------------------- #
# (f) Default JobReadPort HTTP GET forwards request-scoped caller auth/tenant
# --------------------------------------------------------------------------- #

import concurrent.futures
import io
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from services.control_plane.bff.core.owner_reads import (
    OwnerReadContextMiddleware,
    authorization as owner_read_authorization,
    selected_tenant as owner_read_selected_tenant,
)
from services.control_plane.bff.ports.job_read import create_job_read_port
from services.runtime_auth_inbound import encode_jwt_hs256

_TEST_JWT_SECRET = "bff-job-owner-read-context-test-secret-20261018"
_TEST_JWT_ISSUER = "pantheon-bff-job-tests"
_TEST_JWT_AUDIENCE = "pantheon-bff-operators"


def _make_jwt(
    *,
    sub: str = "op-trainer-1",
    roles: Tuple[str, ...] = ("operator",),
    tenant_id: Optional[str] = "tenant-dev",
    allowed_tenants: Optional[Tuple[str, ...]] = None,
    secret: str = _TEST_JWT_SECRET,
) -> str:
    claims: Dict[str, Any] = {
        "sub": sub,
        "roles": list(roles),
        "exp": 4102444800,
        "iss": _TEST_JWT_ISSUER,
        "aud": _TEST_JWT_AUDIENCE,
    }
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    if allowed_tenants is not None:
        claims["allowed_tenants"] = list(allowed_tenants)
    return encode_jwt_hs256(claims, secret=secret)


class _SyntheticHttpResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.headers: Dict[str, str] = {"Content-Type": "application/json"}
        raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self._buf = io.BytesIO(raw)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_SyntheticHttpResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _setup_synthetic_training_owner(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", _TEST_JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", _TEST_JWT_AUDIENCE)

    monkeypatch.setenv("PANTHEON_TRAINING_SESSION_API_URL", "http://training-session.local")
    for env_name in (
        "PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL",
        "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL",
        "PANTHEON_SOURCE_INGEST_API_URL",
        "PANTHEON_SOURCE_INGEST_URL",
        "SOURCE_INGEST_URL",
        "PANTHEON_POLICY_LEARNING_API_URL",
        "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL",
        "PANTHEON_OPENCLAW_ADAPTER_URL",
        "OPENCLAW_GATEWAY_ADAPTER_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)

    jobs_by_id: Dict[str, Dict[str, Any]] = {
        "pvjob-dev-001": {
            "job_id": "pvjob-dev-001",
            "tenant_id": "tenant-dev",
            "status": "completed",
            "created_at": "2026-10-18T10:00:00Z",
            "logs": [
                {"ts": "2026-10-18T10:00:01Z", "level": "INFO", "message": "preview evaluation started"},
                {"ts": "2026-10-18T10:00:05Z", "level": "INFO", "message": "preview evaluation completed"},
            ],
        },
        "pvjob-b-002": {
            "job_id": "pvjob-b-002",
            "tenant_id": "tenant-b",
            "status": "running",
            "created_at": "2026-10-18T11:00:00Z",
            "logs": [
                {"ts": "2026-10-18T11:00:01Z", "level": "INFO", "message": "tenant-b preview running"},
            ],
        },
    }
    recorded_calls: List[Dict[str, str]] = []
    owner_state = {"mode": "ok"}

    def _urlopen(req: urllib.request.Request, timeout: float = 2.0):
        url = req.full_url
        headers = {k.lower(): v for k, v in req.header_items()}
        auth_header = headers.get("authorization", "")
        tenant_header = headers.get("x-tenant-id", "")
        service_header = headers.get("x-pantheon-service", "")
        recorded_calls.append(
            {
                "url": url,
                "path": urlsplit(url).path,
                "authorization": auth_header,
                "tenant_id": tenant_header,
                "service": service_header,
            }
        )
        if owner_state["mode"] == "503":
            raise urllib.error.HTTPError(
                url,
                503,
                "Service Unavailable",
                {},
                io.BytesIO(b'{"error":{"code":"DEPENDENCY_UNAVAILABLE"}}'),
            )
        # Reproduce Training Session inbound_authority contract: bare GET without
        # X-Tenant-Id, X-Pantheon-Service, or Bearer token fails with HTTP 400/401.
        if not tenant_header or service_header != "control-plane-bff" or not auth_header.startswith("Bearer "):
            raise urllib.error.HTTPError(
                url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"error":{"code":"TENANT_REQUIRED","message":"X-Tenant-Id and caller auth required"}}'),
            )
        path = urlsplit(url).path
        if path == "/api/training/preview-jobs":
            items = [dict(j) for j in jobs_by_id.values() if j["tenant_id"] == tenant_header]
            return _SyntheticHttpResponse(200, items)
        prefix = "/api/training/preview-jobs/"
        if path.startswith(prefix):
            native_id = path[len(prefix):]
            record = jobs_by_id.get(native_id)
            if not record or record["tenant_id"] != tenant_header:
                raise urllib.error.HTTPError(
                    url,
                    404,
                    "Not Found",
                    {},
                    io.BytesIO(b'{"detail":"preview job not found"}'),
                )
            return _SyntheticHttpResponse(200, dict(record))
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    port = create_job_read_port()
    store = _JobsOnlyReadStore(port)
    assert store.dataset_source("jobs") == "missing"
    monkeypatch.setattr(
        persona_service,
        "_composed_persona_service",
        type("_ComposedPersonaService", (), {"get_read_store": staticmethod(lambda: store)})(),
    )
    app = build_bff_app()
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(
        create_jobs_router(
            read_surface=lambda: store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-10-18T12:00:00Z",
            page_slice=lambda items, token=None, size=20: (list(items[:size]), None),
            read_surface_meta=persona_service._read_surface_meta,
            dataset_surface_status=persona_service._dataset_surface_status,
            raise_if_read_surface_unavailable=persona_service._raise_if_read_surface_unavailable,
            reject_body_idempotency_key=lambda b: None,
            resolve_final_idempotency_key=lambda k1, k2: k1 or k2 or "",
            submit_job_action=lambda *a, **kw: {},
        )
    )
    client = TestClient(app, raise_server_exceptions=False)
    return client, port, recorded_calls, owner_state


def test_default_job_read_port_forwards_caller_auth_and_verified_tenant_for_list_detail_and_logs(monkeypatch) -> None:
    client, port, calls, _ = _setup_synthetic_training_owner(monkeypatch)
    token_dev = _make_jwt(sub="op-dev-1", tenant_id="tenant-dev")
    headers_dev = {"Authorization": f"Bearer {token_dev}"}

    # 1. List jobs forwards caller Authorization and verified primary tenant
    list_resp = client.get("/bff/jobs", headers=headers_dev)
    assert list_resp.status_code == 200, list_resp.text
    list_payload = list_resp.json()
    items = list_payload["items"]
    assert [item["job_id"] for item in items] == ["job-trainer-pvjob-dev-001"]
    assert list_payload["meta"]["surfaces"]["job_list"]["source"] == "training_session"
    assert list_payload["meta"]["surfaces"]["job_list"]["status"] == "degraded"
    assert list_payload["meta"]["surfaces"]["job_list"]["degraded_sources"] == {
        "research_worker_gateway": "unconfigured",
        "research_orchestrator": "unconfigured",
        "source_ingestion": "unconfigured",
        "policy_learning": "unconfigured",
    }
    assert calls[-1]["path"] == "/api/training/preview-jobs"
    assert calls[-1]["authorization"] == f"Bearer {token_dev}"
    assert calls[-1]["tenant_id"] == "tenant-dev"
    assert calls[-1]["service"] == "control-plane-bff"

    filtered_list_resp = client.get("/bff/jobs?job_type=training_session", headers=headers_dev)
    assert filtered_list_resp.status_code == 200, filtered_list_resp.text
    filtered_list_payload = filtered_list_resp.json()
    assert [item["job_id"] for item in filtered_list_payload["items"]] == ["job-trainer-pvjob-dev-001"]
    assert filtered_list_payload["meta"]["surfaces"]["job_list"] == {
        "status": "ok",
        "source": "training_session",
    }
    assert "degradation" not in filtered_list_payload["meta"]

    # 2. Detail read forwards caller Authorization and verified tenant (200 instead of bare-GET 400->503)
    # and derives meta.surfaces.job_detail from the canonical owner result (never missing/unavailable)
    detail_resp = client.get("/bff/jobs/job-trainer-pvjob-dev-001", headers=headers_dev)
    assert detail_resp.status_code == 200, detail_resp.text
    detail_payload = detail_resp.json()
    detail_data = detail_payload["data"]
    assert detail_data["job_id"] == "job-trainer-pvjob-dev-001"
    assert detail_data["native_id"] == "pvjob-dev-001"
    assert detail_data["source"] == "training_session"
    detail_meta = detail_payload["meta"]
    assert detail_meta["surfaces"]["job_detail"] == {
        "status": "ok",
        "source": "training_session",
    }
    assert "degradation" not in detail_meta
    assert calls[-1]["path"] == "/api/training/preview-jobs/pvjob-dev-001"
    assert calls[-1]["authorization"] == f"Bearer {token_dev}"
    assert calls[-1]["tenant_id"] == "tenant-dev"

    # 3. Logs route and direct port get_job_logs_bff forward caller Authorization and verified tenant
    # and derive meta.surfaces.job_logs from the canonical owner result (never missing/unavailable)
    logs_resp = client.get("/bff/jobs/job-trainer-pvjob-dev-001/logs", headers=headers_dev)
    assert logs_resp.status_code == 200, logs_resp.text
    logs_payload = logs_resp.json()
    assert logs_payload["job_id"] == "job-trainer-pvjob-dev-001"
    assert len(logs_payload["logs"]) == 2
    assert logs_payload["logs"][0]["message"] == "preview evaluation started"
    logs_meta = logs_payload["meta"]
    assert logs_meta["surfaces"]["job_logs"] == {
        "status": "ok",
        "source": "training_session",
    }
    assert "degradation" not in logs_meta
    assert calls[-1]["path"] == "/api/training/preview-jobs/pvjob-dev-001"
    assert calls[-1]["authorization"] == f"Bearer {token_dev}"
    assert calls[-1]["tenant_id"] == "tenant-dev"

    auth_tok = owner_read_authorization.set(f"Bearer {token_dev}")
    tenant_tok = owner_read_selected_tenant.set("tenant-dev")
    try:
        direct_logs = port.get_job_logs_bff("job-trainer-pvjob-dev-001")
        assert len(direct_logs) == 2
    finally:
        owner_read_selected_tenant.reset(tenant_tok)
        owner_read_authorization.reset(auth_tok)


def test_default_job_read_port_stateless_across_sequential_and_concurrent_callers(monkeypatch) -> None:
    client, _port, calls, _ = _setup_synthetic_training_owner(monkeypatch)
    token_multi = _make_jwt(
        sub="op-multi",
        tenant_id="tenant-dev",
        allowed_tenants=("tenant-dev", "tenant-b"),
    )
    token_b_only = _make_jwt(
        sub="op-b-only",
        tenant_id="tenant-b",
        allowed_tenants=("tenant-b",),
    )

    # Sequential switching between default tenant and allowed X-Tenant-Id
    r_dev = client.get("/bff/jobs", headers={"Authorization": f"Bearer {token_multi}"})
    assert r_dev.status_code == 200
    assert [j["job_id"] for j in r_dev.json()["items"]] == ["job-trainer-pvjob-dev-001"]

    r_b = client.get(
        "/bff/jobs",
        headers={"Authorization": f"Bearer {token_multi}", "X-Tenant-Id": "tenant-b"},
    )
    assert r_b.status_code == 200
    assert [j["job_id"] for j in r_b.json()["items"]] == ["job-trainer-pvjob-b-002"]

    # Subsequent call without X-Tenant-Id does not retain tenant-b
    r_dev_again = client.get("/bff/jobs/job-trainer-pvjob-dev-001", headers={"Authorization": f"Bearer {token_multi}"})
    assert r_dev_again.status_code == 200
    assert r_dev_again.json()["data"]["job_id"] == "job-trainer-pvjob-dev-001"
    assert owner_read_authorization.get() is None
    assert owner_read_selected_tenant.get() is None

    # Concurrent callers never bleed auth or tenant context across requests
    def _exercise_caller(index: int) -> Tuple[str, int, str]:
        if index % 2 == 0:
            resp = client.get(
                "/bff/jobs/job-trainer-pvjob-dev-001",
                headers={"Authorization": f"Bearer {token_multi}", "X-Tenant-Id": "tenant-dev"},
            )
            return ("tenant-dev", resp.status_code, resp.json().get("data", {}).get("job_id", ""))
        resp = client.get(
            "/bff/jobs/job-trainer-pvjob-b-002",
            headers={"Authorization": f"Bearer {token_b_only}", "X-Tenant-Id": "tenant-b"},
        )
        return ("tenant-b", resp.status_code, resp.json().get("data", {}).get("job_id", ""))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_exercise_caller, range(12)))

    for expected_tenant, status_code, returned_job_id in results:
        assert status_code == 200
        if expected_tenant == "tenant-dev":
            assert returned_job_id == "job-trainer-pvjob-dev-001"
        else:
            assert returned_job_id == "job-trainer-pvjob-b-002"

    for call in calls:
        if call["tenant_id"] == "tenant-b" and call["path"].endswith("pvjob-b-002"):
            assert call["authorization"] == f"Bearer {token_b_only}"
        if call["tenant_id"] == "tenant-dev":
            assert call["authorization"] == f"Bearer {token_multi}"


def test_default_job_read_port_rejects_missing_or_invalid_auth_and_foreign_tenant_without_owner_io(monkeypatch) -> None:
    client, port, calls, _ = _setup_synthetic_training_owner(monkeypatch)
    token_dev = _make_jwt(sub="op-dev-only", tenant_id="tenant-dev", allowed_tenants=("tenant-dev",))
    token_wrong_sig = _make_jwt(sub="op-forged", tenant_id="tenant-dev", secret="wrong-signing-secret-0000")
    token_no_tenant = _make_jwt(sub="op-no-tenant", tenant_id=None, allowed_tenants=None)

    # 1. Missing auth -> 401
    for path in ("/bff/jobs", "/bff/jobs/job-trainer-pvjob-dev-001", "/bff/jobs/job-trainer-pvjob-dev-001/logs"):
        assert client.get(path).status_code == 401

    # 2. Invalid JWT signature -> 401
    bad_headers = {"Authorization": f"Bearer {token_wrong_sig}", "X-Tenant-Id": "tenant-dev"}
    for path in ("/bff/jobs", "/bff/jobs/job-trainer-pvjob-dev-001", "/bff/jobs/job-trainer-pvjob-dev-001/logs"):
        assert client.get(path, headers=bad_headers).status_code == 401

    # 3. Token without tenant authority -> 403 TENANT_MISMATCH (no invented default tenant)
    no_tenant_headers = {"Authorization": f"Bearer {token_no_tenant}"}
    for path in ("/bff/jobs", "/bff/jobs/job-trainer-pvjob-dev-001", "/bff/jobs/job-trainer-pvjob-dev-001/logs"):
        resp = client.get(path, headers=no_tenant_headers)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] in {"TENANT_MISMATCH", "FORBIDDEN"}

    # 4. Foreign tenant header -> 403 TENANT_MISMATCH before any network call
    foreign_headers = {"Authorization": f"Bearer {token_dev}", "X-Tenant-Id": "tenant-b"}
    for path in ("/bff/jobs", "/bff/jobs/job-trainer-pvjob-dev-001", "/bff/jobs/job-trainer-pvjob-dev-001/logs"):
        resp = client.get(path, headers=foreign_headers)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] in {"TENANT_MISMATCH", "FORBIDDEN"}

    # 5. Direct port invocation without caller authorization context fails closed
    assert owner_read_authorization.get() is None
    assert port.list_jobs_bff() == []
    assert port.get_degraded_sources().get("training_session") == "unreachable"
    with pytest.raises(JobSourceUnavailableError):
        port.get_job_bff("job-trainer-pvjob-dev-001")

    # Zero network packets sent to the synthetic owner across all rejected cases
    assert calls == []


def test_default_job_read_port_preserves_404_missing_and_503_unavailable_visibility(monkeypatch) -> None:
    client, port, _calls, owner_state = _setup_synthetic_training_owner(monkeypatch)
    token_dev = _make_jwt(sub="op-dev-1", tenant_id="tenant-dev")
    headers_dev = {"Authorization": f"Bearer {token_dev}"}

    # 1. Missing native job returns 404
    missing_resp = client.get("/bff/jobs/job-trainer-pvjob-nonexistent", headers=headers_dev)
    assert missing_resp.status_code == 404
    assert missing_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    missing_logs_resp = client.get("/bff/jobs/job-trainer-pvjob-nonexistent/logs", headers=headers_dev)
    assert missing_logs_resp.status_code == 404
    assert missing_logs_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    # 2. Another tenant's job ID queried under caller's own tenant returns 404 from owner
    other_tenant_job_resp = client.get("/bff/jobs/job-trainer-pvjob-b-002", headers=headers_dev)
    assert other_tenant_job_resp.status_code == 404
    assert other_tenant_job_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    # 3. Genuine owner unavailability surfaces as 503 for detail/logs and degraded for list
    owner_state["mode"] = "503"
    unavail_detail = client.get("/bff/jobs/job-trainer-pvjob-dev-001", headers=headers_dev)
    assert unavail_detail.status_code == 503
    assert unavail_detail.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    unavail_logs = client.get("/bff/jobs/job-trainer-pvjob-dev-001/logs", headers=headers_dev)
    assert unavail_logs.status_code == 503
    assert unavail_logs.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    unavail_list = client.get("/bff/jobs", headers=headers_dev)
    assert unavail_list.status_code == 200
    unavail_payload = unavail_list.json()
    assert unavail_payload["items"] == []
    assert port.get_degraded_sources().get("training_session") == "unreachable"
    assert unavail_payload["meta"]["surfaces"]["job_list"]["status"] == "unavailable"
    assert unavail_payload["meta"]["surfaces"]["job_list"]["source"] == "unavailable"
    assert unavail_payload["meta"]["surfaces"]["job_list"]["degraded_sources"]["training_session"] == "unreachable"
    assert "training_session=unreachable" in unavail_payload["meta"]["degradation"]["reason"]


@pytest.mark.parametrize(
    "prefix,native_id,detail_path,expected_source,record",
    [
        (
            "job-worker-",
            "wjob-20260913-001",
            "/api/research-worker-gateway/jobs/wjob-20260913-001",
            "research_worker_gateway",
            {"status": "running", "job_id": "wjob-20260913-001", "logs": [{"message": "worker running"}]},
        ),
        (
            "job-orchestrator-",
            "rrun-20260913-001",
            "/api/research-orchestrator/runs/rrun-20260913-001",
            "research_orchestrator",
            {"status": "running", "run_id": "rrun-20260913-001", "logs": [{"message": "orchestrator running"}]},
        ),
        (
            "job-trainer-",
            "pvjob-abc123",
            "/api/training/preview-jobs/pvjob-abc123",
            "training_session",
            {"status": "pending", "job_id": "pvjob-abc123", "logs": [{"message": "trainer pending"}]},
        ),
        (
            "job-ingest-",
            "ingest-abc123",
            "/api/source-ingest/jobs/ingest-abc123",
            "source_ingestion",
            {"status": "completed", "ingest_run_id": "ingest-abc123", "logs": [{"message": "ingest completed"}]},
        ),
        (
            "job-policy-",
            "plj-20260913-001",
            "/api/policy-learning/jobs/plj-20260913-001",
            "policy_learning",
            {"status": "queued", "job_id": "plj-20260913-001", "logs": [{"message": "policy queued"}]},
        ),
        (
            "job-openclaw-",
            "wf-abc123",
            "/api/openclaw-adapter/workflows/jobs/wf-abc123",
            "openclaw_gateway_adapter",
            {"status": "running", "job_id": "wf-abc123", "logs": [{"message": "openclaw running"}]},
        ),
    ],
)
def test_job_detail_and_logs_surface_metadata_derived_from_canonical_owner_across_all_sources(
    monkeypatch, prefix, native_id, detail_path, expected_source, record
) -> None:
    env_var = _ENV_VAR_BY_PREFIX[prefix]
    monkeypatch.setenv(env_var, "http://fake-source.local")
    fake_get, _ = _fake_http_get_factory({detail_path: record})
    store = _JobsOnlyReadStore(JobReadPort(http_get=fake_get))
    assert store.dataset_source("jobs") == "missing"
    read_surface_meta, dataset_surface_status, raise_if_read_surface_unavailable = (
        _bind_read_surface_helpers(store)
    )
    app = build_bff_app()
    app.include_router(
        create_jobs_router(
            read_surface=lambda: store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-10-18T12:00:00Z",
            page_slice=lambda items, token=None, size=20: (list(items[:size]), None),
            read_surface_meta=read_surface_meta,
            dataset_surface_status=dataset_surface_status,
            raise_if_read_surface_unavailable=raise_if_read_surface_unavailable,
            reject_body_idempotency_key=lambda b: None,
            resolve_final_idempotency_key=lambda k1, k2: k1 or k2 or "",
            submit_job_action=lambda *a, **kw: {},
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    job_id = f"{prefix}{native_id}"
    detail_resp = client.get(f"/bff/jobs/{job_id}", headers=OPERATOR_HEADERS)
    assert detail_resp.status_code == 200, detail_resp.text
    detail_payload = detail_resp.json()
    assert detail_payload["data"]["job_id"] == job_id
    assert detail_payload["data"]["source"] == expected_source
    assert detail_payload["meta"]["surfaces"]["job_detail"] == {
        "status": "ok",
        "source": expected_source,
    }
    assert "degradation" not in detail_payload["meta"]

    logs_resp = client.get(f"/bff/jobs/{job_id}/logs", headers=OPERATOR_HEADERS)
    assert logs_resp.status_code == 200, logs_resp.text
    logs_payload = logs_resp.json()
    assert logs_payload["job_id"] == job_id
    assert logs_payload["meta"]["surfaces"]["job_logs"] == {
        "status": "ok",
        "source": expected_source,
    }
    assert "degradation" not in logs_payload["meta"]

    missing_resp = client.get(f"/bff/jobs/{prefix}nonexistent-id", headers=OPERATOR_HEADERS)
    assert missing_resp.status_code == 404
    assert missing_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_job_detail_and_logs_do_not_swallow_helper_type_error_into_legacy_fallback(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_TRAINING_SESSION_API_URL", "http://fake-source.local")
    fake_get, _ = _fake_http_get_factory(
        {
            "/api/training/preview-jobs": [
                {"status": "running", "job_id": "pvjob-abc123"},
            ],
            "/api/training/preview-jobs/pvjob-abc123": {
                "status": "running",
                "job_id": "pvjob-abc123",
                "logs": [{"message": "running"}],
            },
        }
    )
    store = _JobsOnlyReadStore(JobReadPort(http_get=fake_get))

    def broken_dataset_surface_status(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise TypeError("internal dataset_surface_status TypeError must not fall back")

    def broken_read_surface_meta(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise TypeError("internal read_surface_meta TypeError must not fall back")

    for ds_fn, meta_fn in (
        (broken_dataset_surface_status, persona_service._read_surface_meta),
        (persona_service._dataset_surface_status, broken_read_surface_meta),
    ):
        app = build_bff_app()
        app.include_router(
            create_jobs_router(
                read_surface=lambda: store,
                extract_identity=auth_policy.extract_identity,
                require_read_role=auth_policy.require_read_role,
                bff_error=auth_policy.bff_error,
                utc_now=lambda: "2026-10-18T12:00:00Z",
                page_slice=lambda items, token=None, size=20: (list(items[:size]), None),
                read_surface_meta=meta_fn,
                dataset_surface_status=ds_fn,
                raise_if_read_surface_unavailable=persona_service._raise_if_read_surface_unavailable,
                reject_body_idempotency_key=lambda b: None,
                resolve_final_idempotency_key=lambda k1, k2: k1 or k2 or "",
                submit_job_action=lambda *a, **kw: {},
            )
        )
        client = TestClient(app, raise_server_exceptions=False)
        list_resp = client.get("/bff/jobs?job_type=training_session", headers=OPERATOR_HEADERS)
        assert list_resp.status_code == 500
        detail_resp = client.get("/bff/jobs/job-trainer-pvjob-abc123", headers=OPERATOR_HEADERS)
        assert detail_resp.status_code == 500
        logs_resp = client.get("/bff/jobs/job-trainer-pvjob-abc123/logs", headers=OPERATOR_HEADERS)
        assert logs_resp.status_code == 500


# --------------------------------------------------------------------------- #
# (g) GET /bff/jobs list metadata derived from same-call canonical owner read
# --------------------------------------------------------------------------- #

import threading
from services.control_plane.bff.ports.read_surface_ports import create_in_memory_read_surface_ports
from services.control_plane.bff.research.routes.common import _default_page_slice


def _configure_all_listable_job_sources(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL", "http://worker.local")
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://orchestrator.local")
    monkeypatch.setenv("PANTHEON_TRAINING_SESSION_API_URL", "http://trainer.local")
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", "http://ingest.local")
    monkeypatch.setenv("PANTHEON_POLICY_LEARNING_API_URL", "http://policy.local")


def _build_jobs_list_client(store: Any) -> TestClient:
    read_surface_meta, dataset_surface_status, raise_if_read_surface_unavailable = (
        _bind_read_surface_helpers(store)
    )
    app = build_bff_app()
    app.include_router(
        create_jobs_router(
            read_surface=lambda: store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-10-18T12:00:00Z",
            page_slice=_default_page_slice,
            read_surface_meta=read_surface_meta,
            dataset_surface_status=dataset_surface_status,
            raise_if_read_surface_unavailable=raise_if_read_surface_unavailable,
            reject_body_idempotency_key=lambda b: None,
            resolve_final_idempotency_key=lambda k1, k2: k1 or k2 or "",
            submit_job_action=lambda *a, **kw: {},
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_jobs_list_surface_metadata_healthy_rows_and_healthy_empty_with_legacy_dataset_missing(
    monkeypatch,
) -> None:
    _configure_all_listable_job_sources(monkeypatch)
    state: Dict[str, Any] = {
        "orchestrator": [
            {"run_id": "rrun-20261018-002", "status": "running", "created_at": "2026-10-18T11:00:00Z"},
            {"run_id": "rrun-20261018-001", "status": "completed", "created_at": "2026-10-18T10:00:00Z"},
        ],
        "trainer": [
            {"job_id": "pvjob-20261018-001", "status": "completed", "created_at": "2026-10-18T09:00:00Z"},
        ],
    }

    def fake_get(url: str) -> Tuple[bool, Any]:
        if url.endswith("/api/research-orchestrator/runs"):
            return True, {"items": list(state["orchestrator"])}
        if url.endswith("/api/training/preview-jobs"):
            return True, {"items": list(state["trainer"])}
        return True, {"items": []}

    ports = create_in_memory_read_surface_ports(job_read=JobReadPort(http_get=fake_get))
    assert ports.dataset_source("jobs") == "missing"
    client = _build_jobs_list_client(ports)

    # 1. Healthy rows across configured owners never become missing/unavailable
    resp = client.get("/bff/jobs?page_size=2", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [j["job_id"] for j in body["items"]] == [
        "job-orchestrator-rrun-20261018-002",
        "job-orchestrator-rrun-20261018-001",
    ]
    assert body["data"] == body["items"]
    assert body["page_info"] == {"next_page_token": "2", "total": 3}
    assert body["meta"]["total"] == 3
    assert body["meta"]["surfaces"]["job_list"] == {
        "status": "ok",
        "source": "research_orchestrator",
    }
    assert "degradation" not in body["meta"]

    # 2. Second page via page_token preserves envelope and derives source from page items
    page2 = client.get("/bff/jobs?page_size=2&page_token=2", headers=OPERATOR_HEADERS)
    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    assert [j["job_id"] for j in body2["items"]] == ["job-trainer-pvjob-20261018-001"]
    assert body2["page_info"] == {"next_page_token": None, "total": 3}
    assert body2["meta"]["surfaces"]["job_list"] == {
        "status": "ok",
        "source": "training_session",
    }
    assert "degradation" not in body2["meta"]

    # 3. Healthy empty result via status + job_type filter remains status=ok, never missing/unavailable
    filtered_empty = client.get(
        "/bff/jobs?status=failed&job_type=research_orchestrator", headers=OPERATOR_HEADERS
    )
    assert filtered_empty.status_code == 200, filtered_empty.text
    fe_body = filtered_empty.json()
    assert fe_body["items"] == []
    assert fe_body["data"] == []
    assert fe_body["page_info"] == {"next_page_token": None, "total": 0}
    assert fe_body["meta"]["total"] == 0
    assert fe_body["meta"]["surfaces"]["job_list"]["status"] == "ok"
    assert fe_body["meta"]["surfaces"]["job_list"]["source"] == "research_orchestrator"
    assert "degradation" not in fe_body["meta"]

    # 4. Healthy empty result when all configured owners return [] remains status=ok
    state["orchestrator"] = []
    state["trainer"] = []
    empty_resp = client.get("/bff/jobs", headers=OPERATOR_HEADERS)
    assert empty_resp.status_code == 200, empty_resp.text
    empty_body = empty_resp.json()
    assert empty_body["items"] == []
    assert empty_body["page_info"] == {"next_page_token": None, "total": 0}
    assert empty_body["meta"]["total"] == 0
    assert empty_body["meta"]["surfaces"]["job_list"] == {
        "status": "ok",
        "source": "research_worker_gateway",
    }
    assert "degradation" not in empty_body["meta"]


def test_jobs_list_surface_metadata_partial_failure_and_all_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://orchestrator.local")
    monkeypatch.setenv("PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL", "http://worker.local")
    monkeypatch.setenv("PANTHEON_TRAINING_SESSION_API_URL", "http://trainer.local")
    monkeypatch.delenv("PANTHEON_SOURCE_INGEST_API_URL", raising=False)
    monkeypatch.delenv("PANTHEON_SOURCE_INGEST_URL", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_URL", raising=False)
    monkeypatch.delenv("PANTHEON_POLICY_LEARNING_API_URL", raising=False)

    mode = {"orchestrator_ok": True, "trainer_ok": True}

    def fake_get(url: str) -> Tuple[bool, Any]:
        if url.endswith("/api/research-worker-gateway/jobs"):
            return False, None
        if url.endswith("/api/research-orchestrator/runs"):
            if not mode["orchestrator_ok"]:
                return False, None
            return True, {
                "items": [
                    {"run_id": "rrun-20261018-010", "status": "running", "created_at": "2026-10-18T12:00:00Z"}
                ]
            }
        if url.endswith("/api/training/preview-jobs"):
            if not mode["trainer_ok"]:
                return False, None
            return True, {
                "items": [
                    {"job_id": "pvjob-20261018-010", "status": "completed", "created_at": "2026-10-18T11:00:00Z"}
                ]
            }
        return False, None

    store = _JobsOnlyReadStore(JobReadPort(http_get=fake_get))
    client = _build_jobs_list_client(store)

    # 1. Partial failure: total > 0 must NOT be inferred as full health ("ok")
    partial_resp = client.get("/bff/jobs", headers=OPERATOR_HEADERS)
    assert partial_resp.status_code == 200, partial_resp.text
    partial_body = partial_resp.json()
    assert [j["job_id"] for j in partial_body["items"]] == [
        "job-orchestrator-rrun-20261018-010",
        "job-trainer-pvjob-20261018-010",
    ]
    assert partial_body["page_info"]["total"] == 2
    surface = partial_body["meta"]["surfaces"]["job_list"]
    assert surface["status"] == "degraded"
    assert surface["source"] == "research_orchestrator"
    assert surface["degraded_sources"] == {
        "research_worker_gateway": "unreachable",
        "source_ingestion": "unconfigured",
        "policy_learning": "unconfigured",
    }
    assert "research_worker_gateway=unreachable" in partial_body["meta"]["degradation"]["reason"]
    assert "policy_learning=unconfigured" in partial_body["meta"]["degradation"]["reason"]

    # 2. Filtering to a healthy job_type scopes applicable owners to that owner -> status=ok
    healthy_type_resp = client.get("/bff/jobs?job_type=research_orchestrator", headers=OPERATOR_HEADERS)
    assert healthy_type_resp.status_code == 200, healthy_type_resp.text
    healthy_type_body = healthy_type_resp.json()
    assert [j["job_id"] for j in healthy_type_body["items"]] == ["job-orchestrator-rrun-20261018-010"]
    assert healthy_type_body["meta"]["surfaces"]["job_list"] == {
        "status": "ok",
        "source": "research_orchestrator",
    }
    assert "degradation" not in healthy_type_body["meta"]

    # 3. Filtering to an unreachable job_type -> all applicable owners unavailable
    unavail_type_resp = client.get("/bff/jobs?job_type=research_worker_gateway", headers=OPERATOR_HEADERS)
    assert unavail_type_resp.status_code == 200, unavail_type_resp.text
    unavail_type_body = unavail_type_resp.json()
    assert unavail_type_body["items"] == []
    assert unavail_type_body["meta"]["surfaces"]["job_list"]["status"] == "unavailable"
    assert unavail_type_body["meta"]["surfaces"]["job_list"]["source"] == "unavailable"
    assert unavail_type_body["meta"]["surfaces"]["job_list"]["degraded_sources"] == {
        "research_worker_gateway": "unreachable",
    }
    assert "research_worker_gateway=unreachable" in unavail_type_body["meta"]["degradation"]["reason"]

    # 4. All applicable owners unavailable across unfiltered list -> status=unavailable, never healthy
    mode["orchestrator_ok"] = False
    mode["trainer_ok"] = False
    all_down_resp = client.get("/bff/jobs", headers=OPERATOR_HEADERS)
    assert all_down_resp.status_code == 200, all_down_resp.text
    all_down_body = all_down_resp.json()
    assert all_down_body["items"] == []
    assert all_down_body["page_info"] == {"next_page_token": None, "total": 0}
    all_down_surface = all_down_body["meta"]["surfaces"]["job_list"]
    assert all_down_surface["status"] == "unavailable"
    assert all_down_surface["source"] == "unavailable"
    assert all_down_surface["degraded_sources"] == {
        "research_worker_gateway": "unreachable",
        "research_orchestrator": "unreachable",
        "training_session": "unreachable",
        "source_ingestion": "unconfigured",
        "policy_learning": "unconfigured",
    }
    assert "All job sources unavailable" in all_down_body["meta"]["degradation"]["reason"]


def test_jobs_list_diagnostics_isolated_across_concurrent_requests(monkeypatch) -> None:
    _configure_all_listable_job_sources(monkeypatch)
    barrier = threading.Barrier(2, timeout=5.0)

    def interleaved_http_get(url: str) -> Tuple[bool, Any]:
        if url.endswith("/api/training/preview-jobs"):
            barrier.wait()
            return True, {
                "items": [
                    {"job_id": "pvjob-iso-001", "status": "running", "created_at": "2026-10-18T12:00:00Z"}
                ]
            }
        if url.endswith("/api/research-worker-gateway/jobs"):
            barrier.wait()
            return False, None
        return True, {"items": []}

    ports = create_in_memory_read_surface_ports(job_read=JobReadPort(http_get=interleaved_http_get))
    client = _build_jobs_list_client(ports)

    def call_healthy() -> Dict[str, Any]:
        r = client.get("/bff/jobs?job_type=training_session", headers=OPERATOR_HEADERS)
        assert r.status_code == 200
        return r.json()

    def call_unavailable() -> Dict[str, Any]:
        r = client.get("/bff/jobs?job_type=research_worker_gateway", headers=OPERATOR_HEADERS)
        assert r.status_code == 200
        return r.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fut_ok = pool.submit(call_healthy)
        fut_unavail = pool.submit(call_unavailable)
        res_ok = fut_ok.result()
        res_unavail = fut_unavail.result()

    assert [j["job_id"] for j in res_ok["items"]] == ["job-trainer-pvjob-iso-001"]
    assert res_ok["meta"]["surfaces"]["job_list"] == {
        "status": "ok",
        "source": "training_session",
    }
    assert "degradation" not in res_ok["meta"]

    assert res_unavail["items"] == []
    assert res_unavail["meta"]["surfaces"]["job_list"]["status"] == "unavailable"
    assert res_unavail["meta"]["surfaces"]["job_list"]["source"] == "unavailable"
    assert res_unavail["meta"]["surfaces"]["job_list"]["degraded_sources"] == {
        "research_worker_gateway": "unreachable",
    }
    assert "research_worker_gateway=unreachable" in res_unavail["meta"]["degradation"]["reason"]



