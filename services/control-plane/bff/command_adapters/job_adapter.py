"""Job Domain Command Adapter.

Routes ``JobAction`` commands for the unified Management ``Job`` projection
(see ``ports/job_read.py`` and
``docs/operations/bff-upstream-v2-20260911/decisions/research-jobs.md``).

U10A (BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001) scope is read-plumbing
and minimal write wiring. Per §3 "Settle every source x {cancel, retry,
archive, promote}" of the accepted decision, every one of the six qualified
job sources is missing at least one required piece of a *verified* action
(a real worker-stop/cancellation fence, attempt lineage, an owner archive
mutation, or a Governance-gated promotion mapping) as of this task's
baseline. None of that backend closure work is in scope here — it is
tracked as U10B (`services/research/` orchestrator run cancel only) and the
explicit follow-up obligations `GW-STOP-FENCE-001`, `TS-CANCEL-001`,
`SI-CANCEL-001`, `PL-CANCEL-001`, `GOV-PROMOTE-001`.

Every Job action on every source therefore fails closed with
``ActionUnavailableError`` here. This adapter never returns a fake 200/202
"executed" receipt for a Job action.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .base import ActionUnavailableError, DomainCommandAdapter

log = logging.getLogger(__name__)

_JOB_PREFIX_TO_SOURCE = {
    "job-worker-": "research_worker_gateway",
    "job-orchestrator-": "research_orchestrator",
    "job-trainer-": "training_session",
    "job-ingest-": "source_ingestion",
    "job-policy-": "policy_learning",
    "job-openclaw-": "openclaw_gateway_adapter",
}


def _source_for_job_id(job_id: str) -> str:
    for prefix, source in _JOB_PREFIX_TO_SOURCE.items():
        if job_id.startswith(prefix):
            return source
    return "unknown"


class JobCommandAdapter(DomainCommandAdapter):
    """Adapter for the unified ``Job`` projection's action dispatch."""

    _HANDLED_COMMANDS = {"JobAction"}
    _HANDLED_ENTITIES = {"job"}

    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        normalized_cmd = str(command_type or "").strip()
        normalized_entity = str(entity_type or "").strip().lower().replace("_", "-")
        return normalized_cmd in self._HANDLED_COMMANDS or normalized_entity in self._HANDLED_ENTITIES

    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        action_id = str(params.get("action_id") or "").strip()
        job_id = str(params.get("job_id") or params.get("entity_id") or "").strip()
        if not job_id:
            raise ValueError("JobAction requires job_id.")

        source = _source_for_job_id(job_id)
        if source == "openclaw_gateway_adapter":
            reason = "owner_operation_not_in_scope"
            detail = (
                "OpenClaw workflow jobs are permanently read-only diagnostics for the product BFF; "
                "all write actions (cancel/retry/archive/promote) are excluded by architecture."
            )
        elif source == "unknown":
            reason = "owner_operation_not_in_scope"
            detail = f"Job id {job_id!r} does not match any qualified job source prefix."
        else:
            reason = "owner_operation_unsupported"
            detail = (
                f"No verified backend action exists yet for source={source!r}, action={action_id!r}. "
                "U10A is read-plumbing and minimal write wiring only; real action execution closure "
                "for orchestrator run cancel is U10B, and worker/trainer/ingest/policy cancel-fence "
                "closure are tracked as separate follow-up domain tasks "
                "(GW-STOP-FENCE-001, TS-CANCEL-001, SI-CANCEL-001, PL-CANCEL-001, GOV-PROMOTE-001)."
            )

        _raise_job_action_unavailable(
            action_id=action_id,
            job_id=job_id,
            source=source,
            reason=reason,
            detail=detail,
        )


def _raise_job_action_unavailable(
    *, action_id: str, job_id: str, source: str, reason: str, detail: str
) -> None:
    """Raise ``ActionUnavailableError`` carrying ``action_id``/``job_id``/``reason``.

    ``reason`` is one of ``"owner_operation_unsupported"`` (the source is a
    qualified job source but has no verified backend action yet) or
    ``"owner_operation_not_in_scope"`` (the source/id is outside Job action
    dispatch entirely, e.g. OpenClaw or an unrecognized prefix).
    """
    downstream_status = 400 if reason == "owner_operation_not_in_scope" else 503
    error = ActionUnavailableError(
        f"Job action {action_id!r} on {job_id!r} ({source}) is not available: {detail}",
        action_id=action_id,
        entity_type="Job",
        error_code="JOB_ACTION_UNAVAILABLE",
        suggestion="No job action is available yet for this source in U10A.",
        retryable=False,
        downstream_status=downstream_status,
    )
    error.job_id = job_id  # type: ignore[attr-defined]
    error.source = source  # type: ignore[attr-defined]
    error.reason = reason  # type: ignore[attr-defined]
    raise error
