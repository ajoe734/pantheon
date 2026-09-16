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

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fastapi.testclient import TestClient

import main as bff_main
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

    Mirrors the pattern in tests/test_overlay_retirement.py's
    ``FakeCanonicalReadStore`` (``dataset_source`` -> a non-"missing" label so
    unrelated read-surface-unavailable machinery does not interfere with this
    Jobs-only contract test).
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
        return "canonical_store"


def _fresh_client() -> TestClient:
    bff_main.read_store = _JobsOnlyReadStore(JobReadPort(http_get=lambda url: (True, None)))
    return TestClient(bff_main.app)


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
