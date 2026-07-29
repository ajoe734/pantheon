"""Real downstream dispatch and terminal-receipt readback for Evolution.

L12-EVO-001.

An approved EvolutionDecision may only reach ``executed`` on a *terminal
receipt read back from the plane that actually did the work*.  This module owns
that boundary:

* an **adapter** per execution plane knows how to submit the approved action to
  the real downstream service, idempotently, keyed by the decision id;
* the same adapter knows how to **read the downstream back** and classify the
  result as pending, terminal-success, terminal-failure, or a retryable /
  permanent transport problem;
* nothing here fabricates a receipt.  A plane with no adapter is refused with a
  reason, not stubbed, so an unsupported action can never be marked executed.

Only the ``research`` plane is auto-dispatchable today.  The governance,
deployment, and runtime planes require an authoritative owner and inputs
(active binding, replacement plan, MFA'd operator) that cannot be inferred from
an EvolutionDecision, so they are declared unsupported rather than approximated.
That is the same posture the dispatch worker has always taken; what changes is
that the refusal is now explicit and recorded instead of implied.
"""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

# Downstream outcome classes.  ``PENDING`` is deliberately distinct from
# ``RETRYABLE``: a run that is still executing is a healthy in-flight dispatch,
# not a delivery failure, and must not consume the outbox's retry budget in the
# same way a broken transport does.
OUTCOME_PENDING = "pending"
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
OUTCOME_RETRYABLE = "retryable_error"
OUTCOME_TERMINAL_ERROR = "terminal_error"
OUTCOME_UNSUPPORTED = "unsupported"

_RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Research-orchestrator run statuses, split into the three classes the outbox
# needs.  Anything outside these sets is treated as still-running: a status we
# do not recognise must never be read as a terminal success.
_RESEARCH_SUCCESS_STATUSES = frozenset({"completed", "succeeded"})
_RESEARCH_FAILURE_STATUSES = frozenset({"failed", "rejected", "canceled", "cancelled", "error"})


class DispatchReceiptError(RuntimeError):
    """Raised when a receipt cannot be obtained or is internally inconsistent."""


@dataclass(frozen=True)
class DispatchReceipt:
    """What a downstream plane reported for one dispatched decision."""

    outcome: str
    downstream_kind: str
    downstream_ref_id: str | None = None
    downstream_status: str | None = None
    detail: str | None = None
    evidence: Mapping[str, Any] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.outcome in {OUTCOME_SUCCEEDED, OUTCOME_FAILED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "downstream_kind": self.downstream_kind,
            "downstream_ref_id": self.downstream_ref_id,
            "downstream_status": self.downstream_status,
            "detail": self.detail,
            "evidence": dict(self.evidence or {}),
        }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_json(
    method: str,
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout: float,
) -> tuple[int, Any]:
    data = json.dumps(dict(payload)).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = response.status
        body = response.read().decode("utf-8")
    return status, (json.loads(body) if body.strip() else None)


def _classify_http_error(exc: urllib.error.HTTPError) -> str:
    if exc.code in _RETRYABLE_HTTP_STATUS:
        return OUTCOME_RETRYABLE
    return OUTCOME_TERMINAL_ERROR


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

class ResearchPlaneAdapter:
    """Dispatch research-plane actions to the real research orchestrator.

    The orchestrator's own idempotency keys are derived from the decision id, so
    a redelivered outbox record re-attaches to the existing task/run rather than
    starting a second one.  The terminal receipt is the run's status read back
    from ``GET /api/research-orchestrator/runs/{run_id}/status`` — never the
    202/201 that acknowledged the submission.
    """

    kind = "research_orchestrator_run"
    plane = "research"

    def __init__(
        self,
        *,
        api_url: str,
        timeout: float = 20.0,
        http_json: Callable[..., tuple[int, Any]] = _http_json,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._http_json = http_json

    # -- submission ---------------------------------------------------------

    def submit(self, intent: Mapping[str, Any]) -> DispatchReceipt:
        """Ensure a research run exists for this decision; return its ref.

        Never terminal on its own: submission only proves the orchestrator
        accepted the work.  The caller must poll :meth:`read_receipt`.
        """
        decision_id = str(intent["decision_id"])
        try:
            task_id = self._ensure_task(intent, decision_id)
            run_id = self._ensure_run(intent, decision_id, task_id)
        except urllib.error.HTTPError as exc:
            return DispatchReceipt(
                outcome=_classify_http_error(exc),
                downstream_kind=self.kind,
                detail=f"research orchestrator rejected submission: HTTP {exc.code} {exc.reason}",
            )
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            return DispatchReceipt(
                outcome=OUTCOME_RETRYABLE,
                downstream_kind=self.kind,
                detail=f"research orchestrator transport error: {exc}",
            )
        except (ValueError, KeyError, TypeError) as exc:
            return DispatchReceipt(
                outcome=OUTCOME_TERMINAL_ERROR,
                downstream_kind=self.kind,
                detail=f"research orchestrator returned an unusable submission response: {exc}",
            )

        return DispatchReceipt(
            outcome=OUTCOME_PENDING,
            downstream_kind=self.kind,
            downstream_ref_id=run_id,
            detail=f"research run {run_id} submitted for task {task_id}",
            evidence={"task_id": task_id, "run_id": run_id},
        )

    def _ensure_task(self, intent: Mapping[str, Any], decision_id: str) -> str:
        tenant_id = str(intent.get("tenant_id") or "").strip()
        if not tenant_id:
            raise ValueError("dispatch intent carries no tenant_id")
        status, body = self._http_json(
            "POST",
            f"{self.api_url}/api/research-orchestrator/tasks",
            payload={
                "title": f"Evolution {intent.get('action_type')} for {intent.get('target_id')}",
                "objective": (
                    f"Approved EvolutionDecision {decision_id} "
                    f"({intent.get('action_type')}) on {intent.get('target_type')} "
                    f"{intent.get('target_id')}@{intent.get('target_version')}"
                ),
                "source_refs": [{"type": "evolution_decision", "id": decision_id}],
                "constraints": {
                    "tenant_id": tenant_id,
                    "evolution_decision_id": decision_id,
                },
                "actor_id": "evolution-dispatch-worker",
                "idempotency_key": f"evolution-task:{tenant_id}:{decision_id}",
            },
            timeout=self.timeout,
        )
        if status not in {200, 201}:
            raise ValueError(f"unexpected task-create status {status}")
        task_id = (body or {}).get("task_id")
        if not task_id:
            raise ValueError("task-create response carried no task_id")
        return str(task_id)

    def _ensure_run(self, intent: Mapping[str, Any], decision_id: str, task_id: str) -> str:
        tenant_id = str(intent.get("tenant_id") or "").strip()
        if not tenant_id:
            raise ValueError("dispatch intent carries no tenant_id")
        status, body = self._http_json(
            "POST",
            f"{self.api_url}/api/research-orchestrator/tasks/{task_id}/runs",
            payload={
                "input_refs": [
                    {"type": "strategy_artifact", "id": str(intent.get("target_id") or "")}
                ],
                "parameters": {
                    "tenant_id": tenant_id,
                    "decision_id": decision_id,
                    "action_type": intent.get("action_type"),
                    "target_artifact_id": intent.get("target_id"),
                    "target_version": intent.get("target_version"),
                    "work_item_id": task_id,
                },
                "actor_id": "evolution-dispatch-worker",
                "idempotency_key": f"evolution-run:{tenant_id}:{decision_id}",
            },
            timeout=self.timeout,
        )
        if status not in {200, 201}:
            raise ValueError(f"unexpected run-create status {status}")
        run_id = (body or {}).get("run_id")
        if not run_id:
            raise ValueError("run-create response carried no run_id")
        return str(run_id)

    # -- readback -----------------------------------------------------------

    def read_receipt(
        self,
        downstream_ref_id: str,
        *,
        expected_intent: Mapping[str, Any] | None = None,
    ) -> DispatchReceipt:
        """Read the full run record back, validate its tenant/decision, classify it."""
        try:
            status, body = self._http_json(
                "GET",
                f"{self.api_url}/api/research-orchestrator/runs/{downstream_ref_id}",
                timeout=self.timeout,
            )
        except urllib.error.HTTPError as exc:
            outcome = _classify_http_error(exc)
            if exc.code == 404:
                # The run we were told to observe does not exist.  That is a
                # permanent inconsistency, not something to retry forever.
                outcome = OUTCOME_TERMINAL_ERROR
            return DispatchReceipt(
                outcome=outcome,
                downstream_kind=self.kind,
                downstream_ref_id=downstream_ref_id,
                detail=f"research run readback failed: HTTP {exc.code} {exc.reason}",
            )
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            return DispatchReceipt(
                outcome=OUTCOME_RETRYABLE,
                downstream_kind=self.kind,
                downstream_ref_id=downstream_ref_id,
                detail=f"research run readback transport error: {exc}",
            )
        except ValueError as exc:
            return DispatchReceipt(
                outcome=OUTCOME_TERMINAL_ERROR,
                downstream_kind=self.kind,
                downstream_ref_id=downstream_ref_id,
                detail=f"research run readback returned malformed JSON: {exc}",
            )

        if status != 200 or not isinstance(body, Mapping):
            return DispatchReceipt(
                outcome=OUTCOME_RETRYABLE,
                downstream_kind=self.kind,
                downstream_ref_id=downstream_ref_id,
                detail=f"research run readback returned status={status} without a usable body",
            )

        parameters = body.get("parameters")
        parameters = dict(parameters) if isinstance(parameters, Mapping) else {}
        if expected_intent is not None:
            expected_tenant = str(expected_intent.get("tenant_id") or "").strip()
            expected_decision = str(expected_intent.get("decision_id") or "").strip()
            if (
                not expected_tenant
                or not expected_decision
                or str(parameters.get("tenant_id") or "") != expected_tenant
                or str(parameters.get("decision_id") or "") != expected_decision
            ):
                return DispatchReceipt(
                    outcome=OUTCOME_TERMINAL_ERROR,
                    downstream_kind=self.kind,
                    downstream_ref_id=downstream_ref_id,
                    downstream_status=str(body.get("status") or "").strip().lower() or None,
                    detail=(
                        "research run readback does not belong to the expected "
                        f"tenant/decision ({expected_tenant!r}, {expected_decision!r})"
                    ),
                    evidence={
                        "run_id": body.get("run_id"),
                        "task_id": body.get("task_id"),
                        "parameters": parameters,
                    },
                )

        run_status = str(body.get("status") or "").strip().lower()
        if run_status in _RESEARCH_SUCCESS_STATUSES:
            outcome = OUTCOME_SUCCEEDED
        elif run_status in _RESEARCH_FAILURE_STATUSES:
            outcome = OUTCOME_FAILED
        else:
            outcome = OUTCOME_PENDING
        return DispatchReceipt(
            outcome=outcome,
            downstream_kind=self.kind,
            downstream_ref_id=downstream_ref_id,
            downstream_status=run_status or None,
            detail=f"research run {downstream_ref_id} reported status={run_status or 'unknown'}",
            evidence={
                "run_id": body.get("run_id"),
                "task_id": body.get("task_id"),
                "status": body.get("status"),
                "artifact_refs": list(body.get("artifact_refs") or []),
                "parameters": parameters,
                "updated_at": body.get("updated_at"),
            },
        )


class UnsupportedPlaneAdapter:
    """Refuse, with a reason, any plane that has no real receipt source.

    Governance, deployment, and runtime actions need an authoritative owner and
    operational inputs an EvolutionDecision does not carry.  Returning an
    explicit ``unsupported`` receipt keeps those decisions approved and
    auditable; the alternative — a synthesised success — is precisely the
    stub-executed state this task removes.
    """

    kind = "unsupported"

    def __init__(self, plane: str, reason: str) -> None:
        self.plane = plane
        self.reason = reason

    def submit(self, intent: Mapping[str, Any]) -> DispatchReceipt:
        return DispatchReceipt(
            outcome=OUTCOME_UNSUPPORTED,
            downstream_kind=self.kind,
            detail=self.reason,
        )

    def read_receipt(
        self,
        downstream_ref_id: str,
        *,
        expected_intent: Mapping[str, Any] | None = None,
    ) -> DispatchReceipt:
        return DispatchReceipt(
            outcome=OUTCOME_UNSUPPORTED,
            downstream_kind=self.kind,
            downstream_ref_id=downstream_ref_id,
            detail=self.reason,
        )


_UNSUPPORTED_PLANE_REASONS = {
    "governance": (
        "governance-plane actions are executed by their authoritative governance "
        "owner; automatic dispatch cannot supply the committee decision context"
    ),
    "deployment": (
        "deployment-plane follow-through needs a fresh ApprovalDecision and "
        "DeploymentPlan that an EvolutionDecision does not carry"
    ),
    "runtime": (
        "runtime-plane mitigation needs an active binding, a replacement plan, "
        "and an MFA-authenticated operator; it is dispatched by the Rollback "
        "Controller, not by evolution auto-dispatch"
    ),
}


def build_adapter_registry(
    *,
    research_api_url: str,
    timeout: float = 20.0,
    http_json: Callable[..., tuple[int, Any]] = _http_json,
) -> dict[str, Any]:
    """Return the plane -> adapter registry used by the dispatch worker."""
    registry: dict[str, Any] = {
        "research": ResearchPlaneAdapter(
            api_url=research_api_url, timeout=timeout, http_json=http_json
        )
    }
    for plane, reason in _UNSUPPORTED_PLANE_REASONS.items():
        registry[plane] = UnsupportedPlaneAdapter(plane, reason)
    return registry


def supported_planes(registry: Mapping[str, Any]) -> list[str]:
    return sorted(
        plane
        for plane, adapter in registry.items()
        if not isinstance(adapter, UnsupportedPlaneAdapter)
    )


def verify_terminal_receipt(
    registry: Mapping[str, Any],
    *,
    execution_plane: str,
    downstream_kind: str,
    downstream_ref_id: str,
    tenant_id: str,
    decision_id: str,
) -> DispatchReceipt:
    """Re-read a downstream record and require it to be terminal.

    The evolution service calls this before it will move a decision to
    ``executed``.  It re-reads the downstream itself rather than trusting the
    status a caller supplied: a client-asserted receipt is not evidence, and
    accepting one would reopen the synthetic-executed hole from the other side.
    """
    adapter = registry.get(execution_plane)
    if adapter is None:
        raise DispatchReceiptError(
            f"no downstream receipt source for execution plane {execution_plane!r}"
        )
    if isinstance(adapter, UnsupportedPlaneAdapter):
        raise DispatchReceiptError(adapter.reason)
    if downstream_kind != adapter.kind:
        raise DispatchReceiptError(
            f"receipt downstream_kind {downstream_kind!r} does not match the "
            f"{execution_plane!r} plane's receipt source {adapter.kind!r}"
        )
    if not str(downstream_ref_id).strip():
        raise DispatchReceiptError("receipt downstream_ref_id is required")

    receipt = adapter.read_receipt(
        str(downstream_ref_id).strip(),
        expected_intent={
            "tenant_id": str(tenant_id).strip(),
            "decision_id": str(decision_id).strip(),
        },
    )
    if not receipt.is_terminal:
        raise DispatchReceiptError(
            f"downstream {downstream_kind} {downstream_ref_id} is not terminal: "
            f"outcome={receipt.outcome} status={receipt.downstream_status!r} "
            f"({receipt.detail})"
        )
    return receipt
