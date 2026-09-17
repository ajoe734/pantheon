"""Typed Job read composition port (JobReadPort).

Implements the accepted contract in
``docs/operations/bff-upstream-v2-20260911/decisions/research-jobs.md``: the
Management "Job" is a read-only projection composed across six qualified
domain-owned sources, dispatched by the ``job-<source>-<native_id>`` prefix.
``ResearchTicket``, ``Experiment``, and ``OrchestratorRun`` are distinct
aggregates from ``Job`` and are never returned by this port — the historical
``ReadSurfacePorts.get_job_bff``/``list_jobs_bff`` calling
``get_research_ticket``/``list_research_tickets`` (ticket-as-job masquerade)
is retired by this module.

Each source is called over HTTP using the same calling convention already
used elsewhere in the BFF for talking to these services (plain ``urllib``
JSON requests, base URL resolved from environment variables, see
``services/control-plane/bff/downstream_health_monitor.py`` for the exact env
var names and ``services/control-plane/bff/openclaw_ops_client.py`` /
``services/control-plane/bff/ports/research_knowledge_source.py`` for the
calling pattern). No new HTTP client library or framework is introduced.

Failure handling: a source that is unreachable or unconfigured never
fabricates an empty/fake success. ``list_jobs_bff`` drops that source's
contribution and records it as degraded/unavailable (surfaced via
``get_degraded_sources``); ``get_job_bff``/``get_job_logs_bff`` raise
``JobSourceUnavailableError`` for a job_id whose owning source cannot be
reached so the caller can return HTTP 503, never a silent 404.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from services.control_plane.bff.jobs.projection import (
        calculate_job_allowed_actions,
        project_job as _project_job_canonical,
    )
except (ImportError, ValueError):
    from ..jobs.projection import (
        calculate_job_allowed_actions,
        project_job as _project_job_canonical,
    )

log = logging.getLogger(__name__)


class JobSourceUnavailableError(RuntimeError):
    """Raised when the domain owner for a specific job_id cannot be reached.

    Distinguishes "the source is down/unconfigured" (503, retryable) from
    "the source is up and genuinely has no such job" (404).
    """

    def __init__(self, source: str, message: str) -> None:
        super().__init__(message)
        self.source = source
        self.message = message


@dataclass(frozen=True)
class JobSourceSpec:
    """Static configuration for one of the six qualified job sources."""

    prefix: str  # e.g. "job-worker-"
    name: str  # e.g. "research_worker_gateway"
    base_url_envs: Tuple[str, ...]
    list_path: Optional[str]
    get_path_template: Optional[str]  # "{native_id}" substituted
    native_id_field: str  # response field carrying the native id
    supports_list: bool = True


# Native ID prefixes and env vars mirror downstream_health_monitor.py's
# `_SERVICE_HEALTH_TARGETS` naming exactly, so BFF operators only ever set one
# set of env vars per backend service.
_JOB_SOURCES: Tuple[JobSourceSpec, ...] = (
    JobSourceSpec(
        prefix="job-worker-",
        name="research_worker_gateway",
        base_url_envs=("PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL",),
        list_path="/api/research-worker-gateway/jobs",
        get_path_template="/api/research-worker-gateway/jobs/{native_id}",
        native_id_field="job_id",
    ),
    JobSourceSpec(
        prefix="job-orchestrator-",
        name="research_orchestrator",
        base_url_envs=("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL",),
        list_path="/api/research-orchestrator/runs",
        get_path_template="/api/research-orchestrator/runs/{native_id}",
        native_id_field="run_id",
    ),
    JobSourceSpec(
        prefix="job-trainer-",
        name="training_session",
        base_url_envs=("PANTHEON_TRAINING_SESSION_API_URL",),
        list_path="/api/training/preview-jobs",
        get_path_template="/api/training/preview-jobs/{native_id}",
        native_id_field="job_id",
    ),
    JobSourceSpec(
        prefix="job-ingest-",
        name="source_ingestion",
        base_url_envs=("PANTHEON_SOURCE_INGEST_API_URL", "PANTHEON_SOURCE_INGEST_URL", "SOURCE_INGEST_URL"),
        list_path="/api/source-ingest/jobs",
        get_path_template="/api/source-ingest/jobs/{native_id}",
        native_id_field="ingest_run_id",
    ),
    JobSourceSpec(
        prefix="job-policy-",
        name="policy_learning",
        base_url_envs=("PANTHEON_POLICY_LEARNING_API_URL",),
        list_path="/api/policy-learning/jobs",
        get_path_template="/api/policy-learning/jobs/{native_id}",
        native_id_field="job_id",
    ),
    JobSourceSpec(
        prefix="job-openclaw-",
        name="openclaw_gateway_adapter",
        base_url_envs=("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "PANTHEON_OPENCLAW_ADAPTER_URL", "OPENCLAW_GATEWAY_ADAPTER_URL"),
        list_path=None,  # read-only detail-only source: no list_jobs endpoint (§2.6 of research-jobs.md)
        get_path_template="/api/openclaw-adapter/workflows/jobs/{native_id}",
        native_id_field="job_id",
        supports_list=False,
    ),
)


def _base_url(spec: JobSourceSpec) -> Optional[str]:
    for env_name in spec.base_url_envs:
        raw = os.getenv(env_name, "").strip()
        if raw:
            return raw.rstrip("/")
    return None


def _timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0").strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 2.0


HttpGetFn = Callable[[str], Tuple[bool, Any]]


def _default_http_get(url: str) -> Tuple[bool, Any]:
    """GET ``url`` and return ``(ok, parsed_json_or_none)``. Never raises."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_timeout_seconds()) as resp:
            raw = resp.read()
            body = json.loads(raw.decode("utf-8")) if raw else None
            return True, body
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True, None
        log.warning("job source GET %s failed: HTTP %s", url, exc.code)
        return False, None
    except Exception as exc:  # noqa: BLE001 - any transport failure means "unavailable"
        log.warning("job source GET %s failed: %s", url, exc)
        return False, None


def _extract_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "data", "jobs", "runs", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_detail(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload
    return None


def _normalize_status(raw: Any) -> str:
    return str(raw or "pending").strip().lower()


def _project_job(spec: JobSourceSpec, native_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    return _project_job_canonical(spec, native_id, record)


@dataclass
class JobReadPort:
    """Composes job reads across the six qualified sources by ID prefix."""

    http_get: HttpGetFn = field(default=_default_http_get)
    sources: Tuple[JobSourceSpec, ...] = field(default=_JOB_SOURCES)

    def __post_init__(self) -> None:
        self._degraded_sources: Dict[str, str] = {}

    def get_degraded_sources(self) -> Dict[str, str]:
        """Sources that were unreachable/unconfigured during the last call."""
        return dict(self._degraded_sources)

    def _spec_for_job_id(self, job_id: str) -> Optional[JobSourceSpec]:
        for spec in self.sources:
            if job_id.startswith(spec.prefix):
                return spec
        return None

    def list_jobs_bff(
        self,
        *,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        **_kwargs: Any,
    ) -> List[Dict[str, Any]]:
        self._degraded_sources = {}
        jobs: List[Dict[str, Any]] = []
        for spec in self.sources:
            if not spec.supports_list or spec.list_path is None:
                continue
            if job_type and job_type not in (spec.name, spec.prefix.rstrip("-")):
                continue
            base = _base_url(spec)
            if not base:
                self._degraded_sources[spec.name] = "unconfigured"
                continue
            ok, payload = self.http_get(f"{base}{spec.list_path}")
            if not ok:
                self._degraded_sources[spec.name] = "unreachable"
                continue
            for record in _extract_list(payload):
                native_id = str(record.get(spec.native_id_field) or record.get("id") or "").strip()
                if not native_id:
                    continue
                jobs.append(_project_job(spec, native_id, record))
        if status:
            jobs = [j for j in jobs if j.get("status") == status]
        jobs.sort(key=lambda j: str(j.get("created_at") or ""), reverse=True)
        return jobs

    def get_job_bff(self, job_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not job_id:
            return None
        clean_id = str(job_id).strip()
        spec = self._spec_for_job_id(clean_id)
        if spec is None:
            # Unrecognized prefix: genuinely not a Job this composition owns.
            return None
        native_id = clean_id[len(spec.prefix):]
        if not native_id or spec.get_path_template is None:
            return None
        base = _base_url(spec)
        if not base:
            raise JobSourceUnavailableError(
                spec.name,
                f"{spec.name} is not configured (set one of {spec.base_url_envs}); "
                f"cannot resolve job {clean_id!r}.",
            )
        path = spec.get_path_template.format(native_id=native_id)
        ok, payload = self.http_get(f"{base}{path}")
        if not ok:
            raise JobSourceUnavailableError(spec.name, f"{spec.name} is unreachable; cannot resolve job {clean_id!r}.")
        record = _extract_detail(payload)
        if record is None:
            return None
        return _project_job(spec, native_id, record)

    def get_job_logs_bff(self, job_id: Optional[str]) -> List[Dict[str, Any]]:
        job = self.get_job_bff(job_id)
        if not job:
            return []
        logs = job.get("logs")
        return logs if isinstance(logs, list) else []


def create_job_read_port(*, http_get: Optional[HttpGetFn] = None) -> JobReadPort:
    """Factory mirroring the other ``create_*_port`` factories in this package."""
    return JobReadPort(http_get=http_get or _default_http_get)
