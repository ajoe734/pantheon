"""Authoritative ExperimentTask/ExperimentRun persistence boundary.

Alpha replication evaluates work in the research plane, but the durable
authority is the research-orchestrator service. This adapter records the
schema-backed domain payloads through that service's task/run APIs and verifies
them with independent GET readback before returning an acknowledgement.

The outer ``manual`` dispatch mode is a persistence transport for a result that
was already produced by the named research backend. It is never projected as
the producer mode, and production activation remains disabled.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from services.research.experiments.models import ExperimentRun, ExperimentTask


class ResearchAuthorityError(RuntimeError):
    """Raised when the research authority rejects or misreads a record."""


JsonTransport = Callable[[str, str, Mapping[str, Any] | None], Any]


@dataclass(frozen=True)
class AuthoritativeTaskReceipt:
    authority_task_id: str
    task: ExperimentTask
    record: Mapping[str, Any]


@dataclass(frozen=True)
class AuthoritativeRunReceipt:
    authority_run_id: str
    run: ExperimentRun
    record: Mapping[str, Any]


class ExperimentAuthority(Protocol):
    """Minimal authority contract consumed by the alpha worker."""

    def ensure_task(
        self,
        task: ExperimentTask,
        *,
        approval_decision_id: str,
        approver: str,
        approved_at: str,
        checksum: str,
    ) -> AuthoritativeTaskReceipt:
        """Persist and read back one immutable ExperimentTask intent."""

    def ensure_run(
        self,
        authority_task_id: str,
        run: ExperimentRun,
        *,
        approval_decision_id: str,
    ) -> AuthoritativeRunReceipt:
        """Persist and read back one non-stub ExperimentRun result."""

    def list_runs(
        self,
        *,
        tenant_id: str | None = None,
        strategy_spec_id: str | None = None,
    ) -> list[ExperimentRun]:
        """Read schema-backed ExperimentRuns from authority."""


class ResearchAuthorityHttpClient:
    """HTTP client for the canonical research-orchestrator service."""

    def __init__(
        self,
        base_url: str,
        *,
        actor_id: str = "alpha-replication-worker",
        timeout_seconds: float = 10.0,
        transport: JsonTransport | None = None,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        if not self._base_url:
            raise ValueError("research authority base_url is required")
        self._actor_id = _required_text(actor_id, "actor_id")
        self._timeout_seconds = float(timeout_seconds)
        self._transport = transport or self._urlopen_transport

    def ensure_task(
        self,
        task: ExperimentTask,
        *,
        approval_decision_id: str,
        approver: str,
        approved_at: str,
        checksum: str,
    ) -> AuthoritativeTaskReceipt:
        tenant_id, strategy_spec_id = _required_scope(task)
        approval_id = _required_text(approval_decision_id, "approval_decision_id")
        body = {
            "title": f"Alpha replication for {strategy_spec_id}",
            "objective": "Revalidate one approved immutable StrategySpec in the research plane.",
            "source_refs": [
                {"type": "strategy_spec", "id": strategy_spec_id},
                {"type": "approval_decision", "id": approval_id},
            ],
            "constraints": {
                "record_type": "ExperimentTask",
                "tenant_id": tenant_id,
                "strategy_spec_id": strategy_spec_id,
                "approval_decision_id": approval_id,
                "approver": _required_text(approver, "approver"),
                "approved_at": _required_text(approved_at, "approved_at"),
                "strategy_spec_checksum": _required_text(checksum, "checksum"),
                "production_activation": "disabled",
                "experiment_task": task.to_dict(),
            },
            "actor_id": self._actor_id,
            "idempotency_key": task.idempotency_key,
            "created_at": task.created_at,
        }
        created = _mapping(
            self._request("POST", "/api/research-orchestrator/tasks", body),
            "create ExperimentTask response",
        )
        authority_task_id = _required_text(created.get("task_id"), "authority task_id")
        readback = _mapping(
            self._request(
                "GET",
                f"/api/research-orchestrator/tasks/{urllib.parse.quote(authority_task_id, safe='')}",
                None,
            ),
            "ExperimentTask readback",
        )
        readback_task = self._validate_task_record(
            readback,
            expected_task=task,
            approval_decision_id=approval_id,
        )
        return AuthoritativeTaskReceipt(
            authority_task_id=authority_task_id,
            task=readback_task,
            record=readback,
        )

    def ensure_run(
        self,
        authority_task_id: str,
        run: ExperimentRun,
        *,
        approval_decision_id: str,
    ) -> AuthoritativeRunReceipt:
        tenant_id, strategy_spec_id = _required_scope(run)
        task_id = _required_text(authority_task_id, "authority_task_id")
        approval_id = _required_text(approval_decision_id, "approval_decision_id")
        body = {
            "adapter": "manual",
            "requested_mode": "manual",
            "dispatch_mode": "manual",
            "input_refs": [
                {"type": "strategy_spec", "id": strategy_spec_id},
                {"type": "dataset", "id": run.dataset_version_id},
            ],
            "parameters": {
                "record_type": "ExperimentRun",
                "tenant_id": tenant_id,
                "strategy_spec_id": strategy_spec_id,
                "approval_decision_id": approval_id,
                "producer_backend": run.backend_id,
                "authority_transport": "manual_record",
                "experiment_run": run.to_dict(),
            },
            "actor_id": self._actor_id,
            "idempotency_key": _run_idempotency_key(run),
            "requested_at": run.created_at,
        }
        created = _mapping(
            self._request(
                "POST",
                f"/api/research-orchestrator/tasks/{urllib.parse.quote(task_id, safe='')}/runs",
                body,
            ),
            "create ExperimentRun response",
        )
        authority_run_id = _required_text(created.get("run_id"), "authority run_id")
        if str(created.get("status") or "").lower() not in {"completed", "failed"}:
            self._request(
                "POST",
                f"/api/research-orchestrator/runs/{urllib.parse.quote(authority_run_id, safe='')}/complete",
                {
                    "status": run.status,
                    "summary": f"Persisted authoritative {run.backend_id} ExperimentRun {run.run_id}.",
                    "actor_id": self._actor_id,
                    "completed_at": run.finished_at or run.updated_at or run.created_at,
                },
            )
        readback = _mapping(
            self._request(
                "GET",
                f"/api/research-orchestrator/runs/{urllib.parse.quote(authority_run_id, safe='')}",
                None,
            ),
            "ExperimentRun readback",
        )
        readback_run = self._validate_run_record(
            readback,
            expected_run=run,
            approval_decision_id=approval_id,
        )
        return AuthoritativeRunReceipt(
            authority_run_id=authority_run_id,
            run=readback_run,
            record=readback,
        )

    def list_runs(
        self,
        *,
        tenant_id: str | None = None,
        strategy_spec_id: str | None = None,
    ) -> list[ExperimentRun]:
        payload = self._request("GET", "/api/research-orchestrator/runs", None)
        if not isinstance(payload, list):
            raise ResearchAuthorityError("ExperimentRun list readback must be an array")
        results: list[ExperimentRun] = []
        for raw in payload:
            if not isinstance(raw, Mapping):
                continue
            parameters = raw.get("parameters")
            if not isinstance(parameters, Mapping) or parameters.get("record_type") != "ExperimentRun":
                continue
            if tenant_id is not None and parameters.get("tenant_id") != tenant_id:
                continue
            if strategy_spec_id is not None and parameters.get("strategy_spec_id") != strategy_spec_id:
                continue
            domain_payload = parameters.get("experiment_run")
            if isinstance(domain_payload, Mapping):
                results.append(ExperimentRun.from_dict(domain_payload))
        return results

    def _validate_task_record(
        self,
        record: Mapping[str, Any],
        *,
        expected_task: ExperimentTask,
        approval_decision_id: str,
    ) -> ExperimentTask:
        constraints = record.get("constraints")
        if not isinstance(constraints, Mapping):
            raise ResearchAuthorityError("authority task is missing constraints")
        if constraints.get("record_type") != "ExperimentTask":
            raise ResearchAuthorityError("authority task is not an ExperimentTask record")
        if constraints.get("production_activation") != "disabled":
            raise ResearchAuthorityError("authority task must keep production activation disabled")
        if constraints.get("approval_decision_id") != approval_decision_id:
            raise ResearchAuthorityError("authority task approval decision changed during readback")
        payload = constraints.get("experiment_task")
        if not isinstance(payload, Mapping):
            raise ResearchAuthorityError("authority task is missing experiment_task payload")
        actual = ExperimentTask.from_dict(payload)
        _assert_same_identity(actual, expected_task)
        if actual.to_dict() != expected_task.to_dict():
            raise ResearchAuthorityError("authority ExperimentTask payload differs from submitted immutable intent")
        return actual

    def _validate_run_record(
        self,
        record: Mapping[str, Any],
        *,
        expected_run: ExperimentRun,
        approval_decision_id: str,
    ) -> ExperimentRun:
        if str(record.get("production_activation") or "") != "disabled":
            raise ResearchAuthorityError("authority run must keep production activation disabled")
        if str(record.get("adapter") or "") == "stub":
            raise ResearchAuthorityError("stub authority runs are not accepted")
        parameters = record.get("parameters")
        if not isinstance(parameters, Mapping):
            raise ResearchAuthorityError("authority run is missing parameters")
        if parameters.get("record_type") != "ExperimentRun":
            raise ResearchAuthorityError("authority run is not an ExperimentRun record")
        if parameters.get("approval_decision_id") != approval_decision_id:
            raise ResearchAuthorityError("authority run approval decision changed during readback")
        if parameters.get("producer_backend") in (None, "", "stub"):
            raise ResearchAuthorityError("authority run must name a non-stub producer backend")
        payload = parameters.get("experiment_run")
        if not isinstance(payload, Mapping):
            raise ResearchAuthorityError("authority run is missing experiment_run payload")
        actual = ExperimentRun.from_dict(payload)
        _assert_same_identity(actual, expected_run)
        if str(record.get("status") or "").lower() != actual.status:
            raise ResearchAuthorityError("authority run status differs from ExperimentRun status")
        return actual

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None,
    ) -> Any:
        try:
            return self._transport(method, path, body)
        except ResearchAuthorityError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport errors are boundary failures.
            raise ResearchAuthorityError(f"research authority {method} {path} failed: {exc}") from exc

    def _urlopen_transport(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None,
    ) -> Any:
        data = None if body is None else json.dumps(body, sort_keys=True).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ResearchAuthorityError(
                f"research authority {method} {path} returned HTTP {exc.code}: {detail}"
            ) from exc


def _run_idempotency_key(run: ExperimentRun) -> str:
    metadata = dict(run.metadata)
    value = metadata.get("idempotency_key")
    return _required_text(value or f"authority:{run.tenant_id}:{run.run_id}", "run idempotency key")


def _required_scope(value: ExperimentTask | ExperimentRun) -> tuple[str, str]:
    return (
        _required_text(value.tenant_id, "tenant_id"),
        _required_text(value.strategy_spec_id, "strategy_spec_id"),
    )


def _assert_same_identity(
    actual: ExperimentTask | ExperimentRun,
    expected: ExperimentTask | ExperimentRun,
) -> None:
    for field_name in (
        "tenant_id",
        "strategy_spec_id",
        "strategy_id",
        "strategy_spec_version",
        "task_id",
    ):
        if getattr(actual, field_name) != getattr(expected, field_name):
            raise ResearchAuthorityError(f"authority {field_name} changed during readback")
    if isinstance(actual, ExperimentRun) and isinstance(expected, ExperimentRun):
        if actual.run_id != expected.run_id:
            raise ResearchAuthorityError("authority run_id changed during readback")


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ResearchAuthorityError(f"{field_name} is required")
    return text


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchAuthorityError(f"{label} must be an object")
    return value


__all__ = [
    "AuthoritativeRunReceipt",
    "AuthoritativeTaskReceipt",
    "ExperimentAuthority",
    "ResearchAuthorityError",
    "ResearchAuthorityHttpClient",
]
