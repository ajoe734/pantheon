"""Supervised durable consultation committee/red-team workflow executor.

The executor is a tenant-scoped reconciler:

* discovers actionable ``ConsultRequest`` records from consultation-svc;
* claims each request through a durable SQLite lease with fencing;
* obtains a real qualified contribution from an authenticated HTTP provider;
* persists each participant/event/evidence/memo/handoff phase idempotently;
* waits for a durable downstream acknowledgement before completing work; and
* moves repeatedly blocked work to a visible DLQ that requires explicit replay.

Consultation remains advisory.  This worker never grants deployment, broker,
capital, or approval authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from services.knowledge import case_file as decision_case

from .provider import (
    ContributionBlocked,
    ContributionProviderError,
    HttpContributionProvider,
    QualifiedContribution,
)
from .workflow_state import (
    StaleWorkflowClaim,
    WorkflowClaim,
    WorkflowStateStore,
)


def _build_loop_writer(
    *, dsn: str, tenant_id: str, lease_duration_seconds: int | None = None
) -> Any:
    if not dsn:
        return None
    module = importlib.import_module("services.loop-control")
    deployment_sha = str(
        os.getenv("PANTHEON_DEPLOYMENT_SHA")
        or os.getenv("GIT_SHA")
        or "unknown"
    )
    return module.LoopControllerWriter(
        dsn,
        tenant_id=tenant_id,
        environment=os.getenv("PANTHEON_ENV", "dev"),
        controller_id=os.getenv("CONSULTATION_WORKFLOW_EXECUTOR_ID")
        or f"{CONSUMER_NAME}:{socket.gethostname()}:{os.getpid()}",
        controller_name=CONSUMER_NAME,
        deployment_sha=deployment_sha,
        lease_duration_seconds=lease_duration_seconds,
    )


def build_loop_truth(
    *, result: Mapping[str, Any], health: Mapping[str, Any], checked_at: str
) -> dict[str, Any]:
    """Derive the writer fields from values the tick already read."""

    outcomes = [o for o in result.get("outcomes", []) if isinstance(o, dict)]
    memo_refs = [
        f"consultation://memos/{o['detail'].split('memo=')[1].split(' ')[0]}"
        for o in outcomes
        if o.get("outcome") == "completed" and "memo=" in str(o.get("detail", ""))
    ]
    discovered = int(result.get("requests_discovered") or 0)
    state_counts = result.get("state_counts") or {}
    ok = health.get("status") == "ok"
    return {
        "desired_state": {
            "present": discovered > 0,
            "source": "consultation.workflow_state_store",
            "checked_at": checked_at,
            "summary": f"{discovered} actionable consultation request(s) discovered",
        },
        "downstream_actual_state": {
            "status": "ready" if ok else "degraded",
            "source": "consultation.workflow_state_store",
            "checked_at": checked_at,
            "summary": (
                f"completed={result.get('completed', 0)} "
                f"blocked={result.get('blocked', 0)} "
                f"dead_letter={state_counts.get('dead_letter', 0)}"
            ),
        },
        # The tick observation ref keeps an idle tick admissible evidence.
        "evidence_refs": [
            f"consultation://workflow-ticks/{CONSUMER_NAME}/{checked_at}",
            *memo_refs,
        ],
        "backlog": state_counts.get("in_progress", 0) + state_counts.get("memo_pending", 0),
        "dlq_count": state_counts.get("dead_letter", 0),
    }


def publish_loop_truth(
    writer: Any,
    *,
    loop_id: str,
    result: Mapping[str, Any],
    health: Mapping[str, Any],
    checked_at: str | None = None,
) -> None:
    truth = build_loop_truth(
        result=result, health=health, checked_at=checked_at or _utc_now()
    )
    payload = {"health": dict(health), "result": dict(result)}
    if health.get("status") == "ok":
        asyncio.run(
            writer.record_success(
                loop_id=loop_id,
                summary=f"Processed {result.get('completed', 0)} request(s)",
                payload=payload,
                **truth,
            )
        )
    else:
        asyncio.run(
            writer.record_failure(
                loop_id=loop_id,
                reason=health.get("last_failure_reason") or "workflow degraded",
                payload=payload,
                **truth,
            )
        )



CONSUMER_NAME = "consultation-workflow-executor"
ACTIONABLE_STATUSES = (
    "submitted",
    "assigned",
    "in_progress",
    "memo_pending",
)
COMMITTEE_TYPES = frozenset(
    {
        "strategy_review",
        "capital_pool",
        "execution_risk",
        "incident",
        "persona_policy",
    }
)
REDTEAM_TYPES = frozenset({"redteam", "data_leakage"})


class WorkflowBlocked(RuntimeError):
    """The workflow cannot advance until an external condition is repaired."""


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _stable_id(prefix: str, tenant_id: str, request_id: str) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}\0{request_id}".encode("utf-8")
    ).hexdigest()[:20]
    return f"{prefix}-{digest}"


@dataclass(frozen=True)
class ExecutorConfig:
    api_url: str
    tenant_id: str
    api_token: str
    provider_url: str
    provider_token: str
    provider_service_actor: str
    handoff_sink_url: str
    handoff_token: str
    worker_id: str
    state_path: str
    database_url: str = ""
    memory_url: str = ""
    evolution_url: str = ""
    lease_seconds: int = 60
    retry_after_seconds: int = 15
    max_blocked_attempts: int = 3
    batch_size: int = 10
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "ExecutorConfig":
        data_dir = str(
            os.getenv("CONSULTATION_DATA_DIR") or "/tmp/pantheon/consultation"
        )
        worker_id = str(
            os.getenv("CONSULTATION_WORKFLOW_EXECUTOR_ID")
            or f"{CONSUMER_NAME}:{socket.gethostname()}:{os.getpid()}"
        )
        return cls(
            api_url=str(
                os.getenv("CONSULTATION_API_URL") or "http://127.0.0.1:8096"
            ).rstrip("/"),
            tenant_id=str(os.getenv("PANTHEON_TENANT_ID") or "default"),
            api_token=str(
                os.getenv("CONSULTATION_API_TOKEN")
                or os.getenv("CONSULTATION_SERVICE_TOKEN")
                or ""
            ),
            provider_url=str(os.getenv("CONSULTATION_PROVIDER_URL") or ""),
            provider_token=str(os.getenv("CONSULTATION_PROVIDER_TOKEN") or ""),
            provider_service_actor=str(
                os.getenv("CONSULTATION_PROVIDER_SERVICE_ACTOR")
                or CONSUMER_NAME
            ),
            handoff_sink_url=str(
                os.getenv("CONSULTATION_HANDOFF_SINK_URL") or ""
            ),
            handoff_token=str(
                os.getenv("CONSULTATION_HANDOFF_TOKEN")
                or os.getenv("CONSULTATION_PROVIDER_TOKEN")
                or ""
            ),
            worker_id=worker_id,
            state_path=str(
                os.getenv("CONSULTATION_WORKFLOW_STATE_PATH")
                or Path(data_dir) / "consult_workflow_state.sqlite3"
            ),
            database_url=str(os.getenv("DATABASE_URL") or ""),
            memory_url=str(
                os.getenv("PANTHEON_MEMORY_API_URL")
                or os.getenv("MEMORY_SVC_URL")
                or ""
            ).rstrip("/"),
            evolution_url=str(
                os.getenv("PANTHEON_EVOLUTION_API_URL")
                or os.getenv("EVOLUTION_URL")
                or ""
            ).rstrip("/"),
            lease_seconds=_env_int(
                "CONSULTATION_WORKFLOW_LEASE_SECONDS", 60, minimum=1
            ),
            retry_after_seconds=_env_int(
                "CONSULTATION_WORKFLOW_RETRY_SECONDS", 15, minimum=0
            ),
            max_blocked_attempts=_env_int(
                "CONSULTATION_WORKFLOW_MAX_BLOCKED_ATTEMPTS", 3, minimum=1
            ),
            batch_size=_env_int(
                "CONSULTATION_WORKFLOW_BATCH_SIZE", 10, minimum=1
            ),
            timeout_seconds=float(
                os.getenv("CONSULTATION_WORKFLOW_EXECUTOR_TIMEOUT_SECONDS", "30")
            ),
        )


def _api_headers(config: ExecutorConfig) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "X-Pantheon-Service-Actor": CONSUMER_NAME,
        "X-Pantheon-Tenant-Id": config.tenant_id,
    }
    if config.api_token:
        headers["Authorization"] = f"Bearer {config.api_token}"
    return headers


def _json_request(
    url: str,
    *,
    method: str,
    timeout_seconds: float,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None = None,
) -> Any:
    request_headers = dict(headers)
    body = None
    if payload is not None:
        body = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method=method,
    )
    with urllib.request.urlopen(  # noqa: S310 - configured service boundary
        request,
        timeout=timeout_seconds,
    ) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _api_get(config: ExecutorConfig, path: str) -> Any:
    return _json_request(
        config.api_url.rstrip("/") + path,
        method="GET",
        timeout_seconds=config.timeout_seconds,
        headers=_api_headers(config),
    )


def _api_post(
    config: ExecutorConfig,
    path: str,
    payload: Mapping[str, Any],
) -> Any:
    return _json_request(
        config.api_url.rstrip("/") + path,
        method="POST",
        timeout_seconds=config.timeout_seconds,
        headers=_api_headers(config),
        payload=payload,
    )


def fetch_pending_requests(config: ExecutorConfig) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for status in ACTIONABLE_STATUSES:
        query = urllib.parse.urlencode({"status": status})
        response = _api_get(config, f"/api/consult/requests?{query}")
        if not isinstance(response, list):
            raise WorkflowBlocked(
                "consultation pending-request response must be an array"
            )
        results.extend(item for item in response if isinstance(item, dict))
    deduped: dict[str, dict[str, Any]] = {}
    for item in results:
        request_id = str(item.get("request_id") or "")
        if request_id:
            deduped[request_id] = item
    return list(deduped.values())


def _request_type_to_role(request_type: str) -> str:
    if request_type in COMMITTEE_TYPES:
        return "primary_reviewer"
    if request_type in REDTEAM_TYPES:
        return "red_team"
    raise WorkflowBlocked(f"unknown request_type={request_type!r}")


def _request_type_to_memo_type(request_type: str) -> str:
    if request_type in REDTEAM_TYPES:
        return "redteam_report"
    if request_type in COMMITTEE_TYPES:
        return "committee_summary"
    raise WorkflowBlocked(f"unknown request_type={request_type!r}")


def _request_type_to_gate(request_type: str) -> str:
    if request_type in REDTEAM_TYPES:
        return f"consultation.redteam.{request_type}.reviewed"
    if request_type in COMMITTEE_TYPES:
        return f"consultation.committee.{request_type}.reviewed"
    raise WorkflowBlocked(f"unknown request_type={request_type!r}")


def _renew(
    state: WorkflowStateStore,
    claim: WorkflowClaim,
    config: ExecutorConfig,
) -> WorkflowClaim:
    return state.renew(claim, lease_seconds=config.lease_seconds)


def _phase_hook(
    hook: Callable[[str], None] | None,
    phase: str,
) -> None:
    if hook is not None:
        hook(phase)


def _qualified_contribution(
    claim: WorkflowClaim,
    *,
    request: Mapping[str, Any],
    config: ExecutorConfig,
    provider: HttpContributionProvider,
) -> QualifiedContribution:
    if claim.contribution is not None:
        return QualifiedContribution.from_payload(
            claim.contribution,
            expected_tenant_id=config.tenant_id,
            expected_request_id=claim.request_id,
        )
    return provider.obtain(tenant_id=config.tenant_id, request=request)


def _existing_acknowledged_handoff(
    config: ExecutorConfig,
    request_id: str,
) -> tuple[str, str] | None:
    handoffs = _api_get(
        config,
        "/api/consult/handoffs?"
        + urllib.parse.urlencode({"request_id": request_id}),
    )
    if not isinstance(handoffs, list):
        raise WorkflowBlocked("consultation handoff response must be an array")
    for handoff in handoffs:
        if not isinstance(handoff, Mapping):
            continue
        if handoff.get("status") != "acknowledged":
            continue
        memo_ids = handoff.get("memo_ids")
        if isinstance(memo_ids, list) and memo_ids:
            return str(memo_ids[0]), str(handoff.get("handoff_id") or "")
    return None


def _dispatch_handoff(
    config: ExecutorConfig,
    *,
    request: Mapping[str, Any],
    handoff: Mapping[str, Any],
    extra: Mapping[str, Any] | None = None,
) -> str:
    handoff_id = str(handoff.get("handoff_id") or "")
    if not handoff_id:
        raise WorkflowBlocked("persisted handoff response is missing handoff_id")
    if not config.handoff_sink_url:
        raise WorkflowBlocked(
            "CONSULTATION_HANDOFF_SINK_URL is not configured; "
            "downstream acknowledgement is required"
        )
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Idempotency-Key": f"consultation-handoff:{config.tenant_id}:{handoff_id}",
        "X-Pantheon-Service-Actor": CONSUMER_NAME,
        "X-Pantheon-Tenant-Id": config.tenant_id,
    }
    if config.handoff_token:
        headers["Authorization"] = f"Bearer {config.handoff_token}"
    response = _json_request(
        config.handoff_sink_url,
        method="POST",
        timeout_seconds=config.timeout_seconds,
        headers=headers,
        payload={
            "tenant_id": config.tenant_id,
            "request_id": request.get("request_id"),
            "handoff": dict(handoff),
            **(dict(extra) if extra else {}),
        },
    )
    if not isinstance(response, Mapping):
        raise WorkflowBlocked("handoff sink response must be a JSON object")
    if response.get("acknowledged") is not True:
        raise WorkflowBlocked(
            str(response.get("reason") or "handoff sink did not acknowledge")
        )
    if str(response.get("handoff_id") or "") != handoff_id:
        raise WorkflowBlocked("handoff sink acknowledgement identity mismatch")
    return str(response.get("consumer_ref") or "configured-handoff-sink")


def execute_claim(
    *,
    config: ExecutorConfig,
    state: WorkflowStateStore,
    provider: HttpContributionProvider,
    claim: WorkflowClaim,
    phase_hook: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Execute one fenced claim through durable downstream acknowledgement."""

    request = _api_get(config, f"/api/consult/requests/{claim.request_id}")
    if not isinstance(request, Mapping):
        raise WorkflowBlocked("ConsultRequest response must be an object")
    if str(request.get("tenant_id") or "") != config.tenant_id:
        raise WorkflowBlocked("ConsultRequest tenant does not match worker tenant")

    # A panel claim never short-circuits on an acknowledged handoff: the hold
    # is persisted before the memory writeback, so the handoff alone does not
    # prove the memory work finished. The panel path is fully idempotent.
    is_panel = isinstance((request.get("metadata") or {}).get("panel"), Mapping)
    recovered = (
        None if is_panel else _existing_acknowledged_handoff(config, claim.request_id)
    )
    if recovered is not None:
        memo_id, handoff_id = recovered
        state.complete(claim, memo_id=memo_id, handoff_id=handoff_id)
        return {
            "request_id": claim.request_id,
            "outcome": "completed",
            "detail": f"recovered acknowledged handoff={handoff_id}",
        }

    panel = (request.get("metadata") or {}).get("panel")
    if isinstance(panel, Mapping):
        return _execute_panel_claim(
            config=config,
            state=state,
            provider=provider,
            claim=claim,
            request=request,
            panel=panel,
            phase_hook=phase_hook,
        )

    request_type = str(request.get("request_type") or "")
    role = _request_type_to_role(request_type)
    memo_type = _request_type_to_memo_type(request_type)
    target_gate = _request_type_to_gate(request_type)

    contribution = _qualified_contribution(
        claim,
        request=request,
        config=config,
        provider=provider,
    )
    contribution_data = contribution.to_dict()
    state.save_progress(
        claim,
        phase="contribution_received",
        contribution=contribution_data,
    )
    _phase_hook(phase_hook, "contribution_received")

    claim = _renew(state, claim, config)
    participant = _api_post(
        config,
        f"/api/consult/requests/{claim.request_id}/participants",
        {
            "participant_type": contribution.participant_type,
            "participant_ref": contribution.participant_ref,
            "role": role,
            "trace_id": str(request.get("trace_id") or _stable_id(
                "trace", config.tenant_id, claim.request_id
            )),
            "initiated_by": {
                "actor_type": "service",
                "actor_id": CONSUMER_NAME,
            },
            "idempotency_key": contribution.contribution_id + ":participant",
        },
    )
    if not isinstance(participant, Mapping) or not participant.get("participant_id"):
        raise WorkflowBlocked("participant assignment was not durably acknowledged")
    state.save_progress(claim, phase="participant_assigned")
    _phase_hook(phase_hook, "participant_assigned")

    claim = _renew(state, claim, config)
    event = _api_post(
        config,
        f"/api/consult/requests/{claim.request_id}/events",
        {
            "request_id": claim.request_id,
            "event_type": contribution.event_type,
            "actor": {
                "actor_type": contribution.author_type,
                "actor_id": contribution.participant_ref,
            },
            "content": contribution.event_content,
            "evidence_refs": [
                str(item["id"]) for item in contribution.evidence
            ],
            "idempotency_key": contribution.contribution_id + ":event",
        },
    )
    if not isinstance(event, Mapping) or not event.get("event_id"):
        raise WorkflowBlocked("participant transcript event was not acknowledged")
    state.save_progress(claim, phase="transcript_recorded")
    _phase_hook(phase_hook, "transcript_recorded")

    for evidence in contribution.evidence:
        claim = _renew(state, claim, config)
        attachment = _api_post(
            config,
            f"/api/consult/requests/{claim.request_id}/evidence",
            {
                "evidence_ref": dict(evidence),
                "attached_by": {
                    "actor_type": contribution.author_type,
                    "actor_id": contribution.participant_ref,
                },
                "trace_id": str(request.get("trace_id")),
                "idempotency_key": (
                    contribution.contribution_id
                    + ":evidence:"
                    + str(evidence["id"])
                ),
            },
        )
        if not isinstance(attachment, Mapping) or not attachment.get("attachment_id"):
            raise WorkflowBlocked("participant evidence was not acknowledged")
    state.save_progress(claim, phase="evidence_recorded")
    _phase_hook(phase_hook, "evidence_recorded")

    claim = _renew(state, claim, config)
    memo = _api_post(
        config,
        "/api/consult/memos",
        {
            "request_id": claim.request_id,
            "memo_type": memo_type,
            "author_type": contribution.author_type,
            "author_ref": contribution.participant_ref,
            "summary": contribution.summary,
            "findings": list(contribution.findings),
            "recommendation": contribution.recommendation,
            "confidence": contribution.confidence,
            "trace_id": str(request.get("trace_id")),
            "idempotency_key": contribution.contribution_id + ":memo",
        },
    )
    if not isinstance(memo, Mapping) or not memo.get("memo_id"):
        raise WorkflowBlocked("participant memo was not acknowledged")
    memo_id = str(memo["memo_id"])
    state.save_progress(claim, phase="memo_submitted", memo_id=memo_id)
    _phase_hook(phase_hook, "memo_submitted")

    claim = _renew(state, claim, config)
    published = _api_post(
        config,
        f"/api/consult/memos/{memo_id}/publish",
        {},
    )
    if not isinstance(published, Mapping) or published.get("status") != "published":
        raise WorkflowBlocked("participant memo publish was not acknowledged")
    state.save_progress(claim, phase="memo_published", memo_id=memo_id)
    _phase_hook(phase_hook, "memo_published")

    claim = _renew(state, claim, config)
    handoff = _api_post(
        config,
        "/api/consult/handoffs",
        {
            "request_id": claim.request_id,
            "target_gate": target_gate,
            "memo_ids": [memo_id],
            "evidence_refs": [
                str(item["id"]) for item in contribution.evidence
            ],
            "trace_id": str(request.get("trace_id")),
            "initiated_by": {
                "actor_type": "service",
                "actor_id": CONSUMER_NAME,
            },
            "idempotency_key": contribution.contribution_id + ":handoff",
        },
    )
    if not isinstance(handoff, Mapping) or not handoff.get("handoff_id"):
        raise WorkflowBlocked("gate handoff was not durably acknowledged")
    handoff_id = str(handoff["handoff_id"])
    state.save_progress(
        claim,
        phase="handoff_persisted",
        memo_id=memo_id,
        handoff_id=handoff_id,
    )
    _phase_hook(phase_hook, "handoff_persisted")

    claim = _renew(state, claim, config)
    consumer_ref = _dispatch_handoff(
        config,
        request=request,
        handoff=handoff,
    )
    acknowledged = _api_post(
        config,
        f"/api/consult/handoffs/{handoff_id}/acknowledge",
        {
            "consumer_ref": consumer_ref,
        },
    )
    if (
        not isinstance(acknowledged, Mapping)
        or acknowledged.get("status") != "acknowledged"
    ):
        raise WorkflowBlocked("handoff acknowledgement was not persisted")
    state.save_progress(
        claim,
        phase="handoff_acknowledged",
        memo_id=memo_id,
        handoff_id=handoff_id,
    )
    _phase_hook(phase_hook, "handoff_acknowledged")

    state.complete(claim, memo_id=memo_id, handoff_id=handoff_id)
    return {
        "request_id": claim.request_id,
        "outcome": "completed",
        "detail": (
            f"memo={memo_id} handoff={handoff_id} acknowledged_by={consumer_ref}"
        ),
    }



# ---------------------------------------------------------------------------
# Persona panel (consult policy): each participant persona answers through its
# own agent independently; one synthesis; no vote; risk veto -> operator.
# ---------------------------------------------------------------------------

PANEL_SOURCE_EVENT = "consultation_closed"
PANEL_WRITE_AUTHORITY = "consultation-svc"


def _persona_triggers() -> Any:
    return importlib.import_module("services.persona.consult_triggers")


def _validated_panel(
    request: Mapping[str, Any], panel: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Re-derive the policy requirement and refuse an under-staffed panel."""

    triggers = _persona_triggers()
    decision = panel.get("gated_decision")
    if not isinstance(decision, Mapping):
        raise WorkflowBlocked("panel.gated_decision is required")
    if str(decision.get("type") or "") != str(request.get("target_type") or "") or str(
        decision.get("id") or ""
    ) != str(request.get("target_id") or ""):
        raise WorkflowBlocked(
            "panel gated_decision must match the request target_type/target_id"
        )
    initiator = str(panel.get("initiator_persona_id") or "")
    if not initiator or initiator != str(request.get("from_persona_id") or ""):
        raise WorkflowBlocked("panel initiator must be the requesting persona")
    required = triggers.required_consultation(
        str(decision.get("type") or ""),
        str(decision.get("id") or ""),
        initiator_persona_id=initiator,
        touches=panel.get("touches") or (),
    )
    if required is None:
        raise WorkflowBlocked("panel gated_decision type is not a gated decision")
    declared = {
        str(item.get("persona_id")): item
        for item in panel.get("participants") or []
        if isinstance(item, Mapping)
    }
    for item in required.participants:
        if item.persona_id not in declared:
            raise WorkflowBlocked(
                f"panel is missing required participant {item.persona_id} ({item.role})"
            )
    participants = [
        {
            "persona_id": item.persona_id,
            "role": item.role,
            "may_veto": item.may_veto,
        }
        for item in required.participants
    ]
    return participants, dict(decision)


def _compose_panel_case_file(
    config: ExecutorConfig, request: Mapping[str, Any], decision: Mapping[str, Any]
) -> dict[str, Any]:
    """Resolve the existing request subject through owners; callers add no fields.

    Canary candidacy targets a persona, so resolve its pool members rather than
    treating the persona or ranking snapshot id as a registry artifact.
    """
    from dataclasses import replace

    owners = decision_case.Owners.from_env()
    if config.memory_url:
        owners = replace(owners, memory=decision_case.OwnerEndpoint(
            config.memory_url, _memory_headers().get("Authorization", "").removeprefix("Bearer ")))
    persona = str(request["from_persona_id"])
    panel = request["metadata"]["panel"]
    canary = "paper_canary_candidacy" in (panel.get("touches") or [])
    try:
        if decision["type"] == "trial_admission" and not canary:
            refs = [str(decision["id"])]
        else:
            pool = decision_case.http_json(
                owners.pool, f"/api/personas/{urllib.parse.quote(persona, safe='')}/strategy-pool",
                tenant_id=config.tenant_id)
            refs = [str(m["artifact_ref"]) for m in pool.get("members", [])
                    if m.get("stage") != "removed" and m.get("artifact_ref")]
        if not refs:
            raise WorkflowBlocked("panel_case_file: no subject member artifacts")
        if len(set(refs)) > decision_case.Budgets().max_items_per_group:
            raise WorkflowBlocked("panel_case_file: subject exceeds member budget")
        # Resolve family/spec/backtest context from registry ownership, never a model.
        families: dict[str, list[dict[str, Any]]] = {}
        for ref in dict.fromkeys(refs):
            row = decision_case.http_json(
                owners.registry, f"/api/registry/entries/{urllib.parse.quote(ref, safe='')}",
                tenant_id=config.tenant_id)
            entry = row.get("entry", row)
            if entry.get("registry_id") != ref or not entry.get("strategy_id"):
                raise WorkflowBlocked("panel_case_file: registry subject binding missing")
            if entry.get("tenant_id") not in (config.tenant_id, "__builtin__"):
                raise WorkflowBlocked("panel_case_file: registry tenant binding mismatch")
            families.setdefault(str(entry["strategy_id"]), []).append(entry)
        caller = decision_case.Caller(
            actor_id=os.getenv("PANTHEON_MEMORY_ACTOR_ID", CONSUMER_NAME),
            actor_roles=tuple(x.strip() for x in os.getenv(
                "PANTHEON_MEMORY_ACTOR_ROLES", "operator").split(",") if x.strip()),
            session_id=f"consult-{request['request_id']}",
            # Service caller: the shared file may NEVER include private memory.
            persona_id="")
        files = []
        for family, entries in families.items():
            metadata = [e.get("metadata") or {} for e in entries]
            subject = decision_case.Subject(
                decision_type="trial_admission" if decision["type"] == "trial_admission" else "consult",
                persona_id=persona, tenant_id=config.tenant_id, strategy_family=family,
                member_artifact_refs=tuple(e["registry_id"] for e in entries),
                backtest_run_refs=tuple(dict.fromkeys(
                    run for e in entries for run in (e.get("lineage") or {}).get("source_run_ids", []))),
                underlyings=tuple(dict.fromkeys(u for m in metadata for u in m.get("underlyings", []))),
                instrument_class=next((str(m["instrument_class"]) for m in metadata if m.get("instrument_class")), ""))
            files.append(decision_case.compose_case_file(subject, caller, owners))
    except (decision_case.OwnerUnavailable, decision_case.CaseFileError) as exc:
        raise WorkflowBlocked(f"panel_case_file: {exc}") from exc
    case = files[0]
    # Multi-family canary pools use the same bounded library per family. Union
    # the actual owner results, preserving unavailable reasons and item ids.
    budgets = decision_case.Budgets()
    for name in decision_case.GROUPS:
        groups = [f["groups"][name] for f in files]
        items = {i["item_id"]: i for g in groups for i in g["items"]}
        group = case["groups"][name]
        group["items"] = list(items.values())[:budgets.max_items_per_group]
        group["unavailable"] = [u for g in groups for u in g["unavailable"]]
        group["truncated"] = any(g["truncated"] for g in groups) or len(items) > len(group["items"])
        group["status"] = ("partial" if group["unavailable"] else "ok") if group["items"] else (
            "unavailable" if group["unavailable"] else "empty")
        if group["status"] == "unavailable":
            group["reason"] = "; ".join(f"{u['owner']}:{u['reason']}" for u in group["unavailable"])
        else:
            group.pop("reason", None)
    case["subject"]["member_artifact_refs"] = list(dict.fromkeys(refs))
    case["subject"]["strategy_family"] = " ".join(families)
    case["subject"]["underlyings"] = list(dict.fromkeys(u for f in files for u in f["subject"]["underlyings"]))
    case["subject"]["backtest_run_refs"] = list(dict.fromkeys(r for f in files for r in f["subject"]["backtest_run_refs"]))
    while sum(len(i["excerpt"]) for g in case["groups"].values() for i in g["items"]) > budgets.max_total_chars:
        group = max(case["groups"].values(), key=lambda g: len(g["items"]))
        group["items"].pop()
        group["truncated"] = True
    case["lesson_query"] = {
        "query": " ".join([str(decision["type"]).replace("_", " "), str(decision["id"]),
                           *panel.get("touches", []), *[f["lesson_query"]["query"] for f in files]]),
        "tags": list(dict.fromkeys(t for f in files for t in f["lesson_query"]["tags"]))}
    case.pop("case_file_id")
    case.pop("digest")
    digest = hashlib.sha256(json.dumps(case, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, default=str).encode()).hexdigest()
    return {"case_file_id": f"casefile-{digest[:24]}", "digest": digest, **case}


def _case_file_rejection(contribution: QualifiedContribution, case: Mapping[str, Any]) -> str | None:
    """Every claim, summary and finding must cite an existing case item.

    A bare global evidence array is not a citation for an individual claim.
    Subject assertions, when supplied, must match the cited owner's fields or
    the decision subject. This checks attribution, not semantic entailment.
    """
    items = {i["item_id"]: i for g in case["groups"].values() for i in g["items"]}
    if any(not isinstance(e["id"], str) or e["id"] not in items for e in contribution.evidence):
        return "evidence_not_in_case_file"
    attached = {e["id"] for e in contribution.evidence}
    claims = contribution.event_content.get("claims")
    if not isinstance(claims, list) or not claims:
        return "claims_missing"
    content = contribution.event_content
    if set(content) - {"claims", "summary_evidence_refs", "committee_memory_status", "claim", "evidence_refs"}:
        return "unvalidated_claim_fields"
    nodes = [*claims, {"claim": contribution.summary, "evidence_refs": content.get("summary_evidence_refs")},
             *contribution.findings]
    # A legacy free claim beside the claims list cannot escape validation.
    if content.get("claim"):
        nodes.append({"claim": content["claim"], "evidence_refs": content.get("evidence_refs")})
    for node in nodes:
        if not isinstance(node, Mapping) or not str(node.get("claim") or "").strip():
            return "claim_malformed"
        refs = node.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            return "claim_uncited"
        if any(not isinstance(ref, str) or ref not in items for ref in refs):
            return "case_item_unknown"
        if any(ref not in attached for ref in refs):
            return "case_item_not_attached"
        fields = node.get("subject_fields", {})
        if not isinstance(fields, Mapping):
            return "claim_subject_malformed"
        for field, value in fields.items():
            if value is None or not any(
                (field in items[ref].get("subject_fields", {}) and value == items[ref]["subject_fields"][field])
                or (field in case["subject"] and value == case["subject"][field]) for ref in refs):
                return "claim_subject_mismatch"
    return None


def _panel_contribution(
    *,
    claim: WorkflowClaim,
    state: WorkflowStateStore,
    config: ExecutorConfig,
    provider: HttpContributionProvider,
    request: Mapping[str, Any],
    participant: Mapping[str, Any],
    gathered: dict[str, dict[str, Any]],
) -> QualifiedContribution:
    persona_id = str(participant["persona_id"])
    if persona_id in gathered:
        return QualifiedContribution.from_payload(
            gathered[persona_id],
            expected_tenant_id=config.tenant_id,
            expected_request_id=claim.request_id,
        )
    # Independence: a participant sees the request and its own role only,
    # never another participant's answer.
    ask = dict(request)
    ask["participant"] = {
        "persona_id": persona_id,
        "role": participant["role"],
        "agent": "persona_own_agent",
    }
    contribution = provider.obtain(
        tenant_id=config.tenant_id,
        request=ask,
        participant_scope=persona_id,
    )
    if contribution.participant_type != "persona" or (
        contribution.participant_ref != persona_id
    ):
        raise ContributionProviderError(
            f"provider answered as {contribution.participant_ref!r}, "
            f"not the asked persona {persona_id!r}"
        )
    if any(
        item.get("contribution_id") == contribution.contribution_id
        for item in gathered.values()
    ):
        raise ContributionProviderError("provider reused a contribution_id")
    gathered[persona_id] = contribution.to_dict()
    state.save_progress(
        claim,
        phase="panel_contribution_received",
        contribution={"case_file": request["case_file"], "panel_contributions": gathered},
    )
    return contribution


def _synthesize(
    participants: list[dict[str, Any]],
    contributions: list[QualifiedContribution],
) -> tuple[dict[str, Any], str, str, list[dict[str, Any]], float]:
    triggers = _persona_triggers()
    by_persona = {item.participant_ref: item for item in contributions}
    gate = triggers.evaluate_gate(
        participants,
        {pid: item.recommendation for pid, item in by_persona.items()},
    )
    lines = [
        f"{pid} ({next(p['role'] for p in participants if p['persona_id'] == pid)})"
        f" -> {item.recommendation}: {item.summary}"
        for pid, item in by_persona.items()
    ]
    summary = "Panel synthesis (no vote). " + " | ".join(lines)
    if gate.agreements:
        summary += " Agreements: " + "; ".join(gate.agreements) + "."
    if gate.disagreements:
        summary += " Disagreements: " + "; ".join(gate.disagreements) + "."
    summary += (
        " Gate: " + gate.outcome + " - " + "; ".join(gate.reasons) + "."
    )
    findings: list[dict[str, Any]] = []
    for text in gate.disagreements:
        findings.append(
            {
                "severity": "medium",
                "category": "panel_disagreement",
                "claim": "Participants disagree - " + text,
                "evidence_refs": [by_persona[next(iter(by_persona))].evidence[0]["id"]],
                "recommendation": "Operator reads each persona answer before deciding.",
            }
        )
    if gate.vetoed_by:
        risk = by_persona[gate.vetoed_by]
        findings.append(
            {
                "severity": "high",
                "category": "risk_veto",
                "claim": f"Risk persona {gate.vetoed_by} opposed ({risk.recommendation}).",
                "evidence_refs": [risk.evidence[0]["id"]],
                "recommendation": "Do not auto-adopt; route to an operator decision.",
            }
        )
    elif gate.operator_decision_required:
        findings.append(
            {
                "severity": "high",
                "category": "operator_decision",
                "claim": "; ".join(gate.reasons),
                "evidence_refs": [contributions[0].evidence[0]["id"]] if contributions else [],
                "recommendation": "Route to an operator decision.",
            }
        )
    if gate.operator_decision_required:
        recommendation = "escalate"
    elif all(item.recommendation == "approve" for item in contributions):
        recommendation = "approve"
    else:
        recommendation = "approve_with_conditions"
    confidence = min((item.confidence for item in contributions), default=0.0)
    return gate.to_dict(), summary, recommendation, findings, confidence


def _memory_headers() -> dict[str, str]:
    from services.runtime_auth import resolve_runtime_manager_auth

    return {
        "Accept": "application/json",
        **resolve_runtime_manager_auth(
            token_env_key="PANTHEON_MEMORY_AUTH_TOKEN",
            token_file_env_key="PANTHEON_MEMORY_AUTH_TOKEN_FILE",
            default_token="",
        ).headers(),
    }


def _write_panel_memory(
    config: ExecutorConfig,
    *,
    request: Mapping[str, Any],
    panel: Mapping[str, Any],
    decision: Mapping[str, Any],
    participants: list[dict[str, Any]],
    gate: Mapping[str, Any],
    memo_id: str,
    summary: str,
    confidence: float,
    evidence_ids: list[str],
) -> Mapping[str, Any]:
    """Write initiator-memory outcome + institutional cross-persona observation.

    One idempotent Learn-feedback writeback: the source event id is the
    consultation request, so a retry replays instead of duplicating.
    """

    if not config.memory_url:
        raise WorkflowBlocked(
            "PANTHEON_MEMORY_API_URL is not configured; "
            "consultation outcome memory writeback is required"
        )
    initiator = str(panel["initiator_persona_id"])
    others = [p["persona_id"] for p in participants if p["persona_id"] != initiator]
    outcome = (
        "operator decision required" if gate.get("operator_decision_required")
        else "adopt eligible"
    )
    payload = {
        "tenant_id": config.tenant_id,
        "source_event_type": PANEL_SOURCE_EVENT,
        "source_event_id": f"consultation:{request.get('request_id')}",
        "write_authority": PANEL_WRITE_AUTHORITY,
        "sponsor_persona_id": initiator,
        "contributing_persona_ids": [initiator],
        "summary": (
            f"Consultation on {decision.get('type')} {decision.get('id')}: "
            f"{outcome}. Panel: {', '.join([initiator, *others])}."
        ),
        "headline": f"Consultation outcome: {decision.get('type')} {decision.get('id')}",
        "body": summary,
        "memo_id": memo_id,
        "outcome": gate.get("outcome"),
        "evidence_refs": [f"consultation://memos/{memo_id}", *evidence_ids],
        "confidence": confidence,
        "trace_id": str(request.get("trace_id") or ""),
        "tags": ["consult_panel", str(decision.get("type")), *[f"panel:{p}" for p in others]],
    }
    response = _json_request(
        config.memory_url + "/api/memory/writebacks/learn-feedback",
        method="POST",
        timeout_seconds=config.timeout_seconds,
        headers=_memory_headers(),
        payload=payload,
    )
    if not isinstance(response, Mapping):
        raise WorkflowBlocked("memory writeback response must be a JSON object")
    return response


def _probe(config: ExecutorConfig, base: str, label: str) -> None:
    try:
        _json_request(
            base + "/health",
            method="GET",
            timeout_seconds=config.timeout_seconds,
            headers={"Accept": "application/json"},
        )
    except Exception as exc:
        raise WorkflowBlocked(f"{label} address is not reachable: {_error_detail(exc)}") from exc


def _execute_panel_claim(
    *,
    config: ExecutorConfig,
    state: WorkflowStateStore,
    provider: HttpContributionProvider,
    claim: WorkflowClaim,
    request: Mapping[str, Any],
    panel: Mapping[str, Any],
    phase_hook: Callable[[str], None] | None,
) -> dict[str, str]:
    participants, decision = _validated_panel(request, panel)
    trace_id = str(
        request.get("trace_id") or _stable_id("trace", config.tenant_id, claim.request_id)
    )
    if decision.get("type") == "harness_patch":
        if not config.evolution_url:
            raise WorkflowBlocked(
                "PANTHEON_EVOLUTION_API_URL is not configured for a harness patch panel"
            )
        _probe(config, config.evolution_url, "evolution")

    saved = claim.contribution or {}
    case = saved.get("case_file")
    if not isinstance(case, Mapping):
        try:
            # Owner reads are sequential inside the knowledge library. Keep the
            # durable lease alive during a slow composition and always join it.
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_compose_panel_case_file, config, request, decision)
                while True:
                    try:
                        case = future.result(timeout=max(0.05, config.lease_seconds / 3))
                        break
                    except FutureTimeout:
                        claim = _renew(state, claim, config)
        except WorkflowBlocked as exc:
            _api_post(config, f"/api/consult/requests/{claim.request_id}/events", {
                "request_id": claim.request_id, "event_type": "panel_case_file_unavailable",
                "actor": {"actor_type": "service", "actor_id": CONSUMER_NAME},
                "content": {"reason": str(exc), "gated_decision": decision},
                "idempotency_key": f"{claim.request_id}:case-unavailable:{hashlib.sha256(str(exc).encode()).hexdigest()[:16]}"})
            raise
        state.save_progress(claim, phase="case_file_composed", contribution={"case_file": case})
    case_binding = {"case_file_id": case["case_file_id"], "digest": case["digest"],
                    "request_id": claim.request_id, "gated_decision": decision,
                    "initiator_persona_id": panel["initiator_persona_id"]}
    request = {**request, "case_file": case, "case_file_binding": case_binding}
    recorded = _api_post(config, f"/api/consult/requests/{claim.request_id}/events", {
        "request_id": claim.request_id, "event_type": "panel_case_file_attached",
        "actor": {"actor_type": "service", "actor_id": CONSUMER_NAME},
        "content": {**case_binding, "case_file": case},
        "idempotency_key": f"{claim.request_id}:case-file"})
    if not isinstance(recorded, Mapping) or not recorded.get("event_id"):
        raise WorkflowBlocked("panel case file was not durably acknowledged")

    gathered: dict[str, dict[str, Any]] = {}
    if isinstance(claim.contribution, Mapping):
        gathered = {
            str(k): dict(v)
            for k, v in (claim.contribution.get("panel_contributions") or {}).items()
        }
    contributions: list[QualifiedContribution] = []
    rejected: list[dict[str, Any]] = []
    for participant in participants:
        claim = _renew(state, claim, config)
        contribution = _panel_contribution(
                claim=claim,
                state=state,
                config=config,
                provider=provider,
                request=request,
                participant=participant,
                gathered=gathered,
            )
        reason = _case_file_rejection(contribution, case)
        if reason:
            invalid = {"persona_id": participant["persona_id"], "contribution_id": contribution.contribution_id,
                       "reason": reason, **case_binding}
            rejected.append(invalid)
            event = _api_post(config, f"/api/consult/requests/{claim.request_id}/events", {
                "request_id": claim.request_id, "event_type": "panel_contribution_rejected",
                "actor": {"actor_type": "service", "actor_id": CONSUMER_NAME},
                "content": invalid, "idempotency_key": contribution.contribution_id + ":rejected"})
            if not isinstance(event, Mapping) or not event.get("event_id"):
                raise WorkflowBlocked("panel rejection was not durably acknowledged")
        else:
            contributions.append(contribution)
    _phase_hook(phase_hook, "panel_contributions_received")

    service_actor = {"actor_type": "service", "actor_id": CONSUMER_NAME}
    all_evidence: list[str] = []
    for contribution in contributions:
        participant = next(p for p in participants if p["persona_id"] == contribution.participant_ref)
        claim = _renew(state, claim, config)
        base = contribution.contribution_id
        assigned = _api_post(
            config,
            f"/api/consult/requests/{claim.request_id}/participants",
            {
                "participant_type": "persona",
                "participant_ref": contribution.participant_ref,
                "role": participant["role"],
                "trace_id": trace_id,
                "initiated_by": service_actor,
                "idempotency_key": base + ":participant",
            },
        )
        if not isinstance(assigned, Mapping) or not assigned.get("participant_id"):
            raise WorkflowBlocked("panel participant assignment was not acknowledged")
        actor = {
            "actor_type": "persona",
            "actor_id": contribution.participant_ref,
        }
        event = _api_post(
            config,
            f"/api/consult/requests/{claim.request_id}/events",
            {
                "request_id": claim.request_id,
                "event_type": contribution.event_type,
                "actor": actor,
                "content": {
                    **contribution.event_content,
                    "recommendation": contribution.recommendation,
                    "role": participant["role"],
                    **case_binding,
                },
                "evidence_refs": [str(i["id"]) for i in contribution.evidence],
                "idempotency_key": base + ":event",
            },
        )
        if not isinstance(event, Mapping) or not event.get("event_id"):
            raise WorkflowBlocked("panel transcript event was not acknowledged")
        for evidence in contribution.evidence:
            attachment = _api_post(
                config,
                f"/api/consult/requests/{claim.request_id}/evidence",
                {
                    "evidence_ref": dict(evidence),
                    "attached_by": actor,
                    "trace_id": trace_id,
                    "idempotency_key": base + ":evidence:" + str(evidence["id"]),
                },
            )
            if not isinstance(attachment, Mapping) or not attachment.get("attachment_id"):
                raise WorkflowBlocked("panel evidence was not acknowledged")
            all_evidence.append(str(evidence["id"]))
    state.save_progress(claim, phase="panel_recorded")
    _phase_hook(phase_hook, "panel_recorded")

    gate, summary, recommendation, findings, confidence = _synthesize(
        participants, contributions
    )
    missing = [f"{name}/{u['owner']}: {u['reason']}" for name, group in case["groups"].items()
               for u in group["unavailable"] if not u.get("expected")]
    if missing:
        gate["outcome"] = "operator_decision"
        gate["operator_decision_required"] = True
        gate["reasons"] = [*gate["reasons"], *missing]
        recommendation = "escalate"
        summary += " Case-file sources unavailable; operator decision required: " + "; ".join(missing) + "."
    summary += f" Case file: {case['case_file_id']} digest={case['digest']}."
    if rejected:
        summary += " Invalid contributions excluded: " + "; ".join(
            f"{r['persona_id']}: {r['reason']}" for r in rejected) + "."
    synth_ref = f"consult-panel:{claim.request_id}"
    synth_key = _stable_id("panel-synthesis", config.tenant_id, claim.request_id)
    claim = _renew(state, claim, config)
    assigned = _api_post(
        config,
        f"/api/consult/requests/{claim.request_id}/participants",
        {
            "participant_type": "committee",
            "participant_ref": synth_ref,
            "role": "observer",
            "trace_id": trace_id,
            "initiated_by": service_actor,
            "idempotency_key": synth_key + ":participant",
        },
    )
    if not isinstance(assigned, Mapping) or not assigned.get("participant_id"):
        raise WorkflowBlocked("panel synthesis participant was not acknowledged")
    synth_event = _api_post(
        config,
        f"/api/consult/requests/{claim.request_id}/events",
        {
            "request_id": claim.request_id,
            "event_type": "panel_synthesis",
            "actor": {"actor_type": "committee", "actor_id": synth_ref},
            "content": {"voting": False, "gate": gate, "gated_decision": decision,
                        **case_binding, "rejected_contributions": rejected},
            "evidence_refs": all_evidence,
            "idempotency_key": synth_key + ":event",
        },
    )
    if not isinstance(synth_event, Mapping) or not synth_event.get("event_id"):
        raise WorkflowBlocked("panel synthesis event was not acknowledged")
    memo = _api_post(
        config,
        "/api/consult/memos",
        {
            "request_id": claim.request_id,
            "memo_type": "committee_summary",
            "author_type": "committee",
            "author_ref": synth_ref,
            "summary": summary,
            "findings": findings,
            "recommendation": recommendation,
            "confidence": confidence,
            "trace_id": trace_id,
            "idempotency_key": synth_key + ":memo",
        },
    )
    if not isinstance(memo, Mapping) or not memo.get("memo_id"):
        raise WorkflowBlocked("panel synthesis memo was not acknowledged")
    memo_id = str(memo["memo_id"])
    state.save_progress(claim, phase="memo_submitted", memo_id=memo_id)
    claim = _renew(state, claim, config)
    published = _api_post(config, f"/api/consult/memos/{memo_id}/publish", {})
    if not isinstance(published, Mapping) or published.get("status") != "published":
        raise WorkflowBlocked("panel synthesis memo publish was not acknowledged")
    state.save_progress(claim, phase="memo_published", memo_id=memo_id)
    _phase_hook(phase_hook, "memo_published")

    # Fail-closed hold first: persist and dispatch the gate handoff (which
    # installs the operator-decision hold at the decision owner) before any
    # asynchronous memory write, so a memory outage cannot leave a published
    # veto without a hold.
    claim = _renew(state, claim, config)
    memo_ref = f"consultation://memos/{memo_id}"
    handoff = _api_post(
        config,
        "/api/consult/handoffs",
        {
            "request_id": claim.request_id,
            "target_gate": f"consultation.panel.{decision['type']}.reviewed",
            "memo_ids": [memo_id],
            "evidence_refs": [memo_ref, *all_evidence],
            "trace_id": trace_id,
            "initiated_by": service_actor,
            "idempotency_key": synth_key + ":handoff",
        },
    )
    if not isinstance(handoff, Mapping) or not handoff.get("handoff_id"):
        raise WorkflowBlocked("panel gate handoff was not durably acknowledged")
    handoff_id = str(handoff["handoff_id"])
    state.save_progress(
        claim, phase="handoff_persisted", memo_id=memo_id, handoff_id=handoff_id
    )
    claim = _renew(state, claim, config)
    consumer_ref = _dispatch_handoff(
        config,
        request=request,
        handoff=handoff,
        extra={
            "gated_decision": decision,
            "gate": gate,
            "memo_evidence_ref": memo_ref,
        },
    )
    acknowledged = _api_post(
        config,
        f"/api/consult/handoffs/{handoff_id}/acknowledge",
        {"consumer_ref": consumer_ref},
    )
    if not isinstance(acknowledged, Mapping) or acknowledged.get("status") != "acknowledged":
        raise WorkflowBlocked("panel handoff acknowledgement was not persisted")
    state.save_progress(
        claim, phase="handoff_acknowledged", memo_id=memo_id, handoff_id=handoff_id
    )
    _phase_hook(phase_hook, "handoff_acknowledged")

    claim = _renew(state, claim, config)
    _write_panel_memory(
        config,
        request=request,
        panel=panel,
        decision=decision,
        participants=participants,
        gate=gate,
        memo_id=memo_id,
        summary=summary,
        confidence=confidence,
        evidence_ids=all_evidence,
    )
    state.save_progress(claim, phase="memory_written", memo_id=memo_id)
    _phase_hook(phase_hook, "memory_written")

    state.complete(claim, memo_id=memo_id, handoff_id=handoff_id)
    return {
        "request_id": claim.request_id,
        "outcome": "completed",
        "detail": (
            f"memo={memo_id} handoff={handoff_id} acknowledged_by={consumer_ref} "
            f"gate={gate['outcome']}"
        ),
    }


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_error={exc.code} {exc.reason} url={exc.url}"
    return str(exc)


def run_tick(
    *,
    config: ExecutorConfig | None = None,
    state: WorkflowStateStore | None = None,
    provider: HttpContributionProvider | None = None,
) -> dict[str, Any]:
    """Discover, claim, and process a bounded batch of consultation work."""

    config = config or ExecutorConfig.from_env()
    state = state or WorkflowStateStore(config.state_path)
    provider = provider or HttpContributionProvider(
        endpoint=config.provider_url,
        bearer_token=config.provider_token,
        service_actor=config.provider_service_actor,
        timeout_seconds=config.timeout_seconds,
    )
    errors: list[str] = []
    outcomes: list[dict[str, str]] = []
    discovered = 0
    try:
        requests = fetch_pending_requests(config)
        discovered = len(requests)
        for request in requests:
            request_id = str(request.get("request_id") or "")
            tenant_id = str(request.get("tenant_id") or "")
            if tenant_id != config.tenant_id:
                errors.append(
                    f"request_id={request_id} tenant_mismatch={tenant_id!r}"
                )
                continue
            state.ensure_request(
                tenant_id=config.tenant_id,
                request_id=request_id,
            )
    except Exception as exc:  # discovery failure must not hide durable backlog
        errors.append(f"discovery_error={_error_detail(exc)}")

    for _ in range(config.batch_size):
        claim = state.claim_next(
            tenant_id=config.tenant_id,
            lease_owner=config.worker_id,
            lease_seconds=config.lease_seconds,
        )
        if claim is None:
            break
        try:
            outcome = execute_claim(
                config=config,
                state=state,
                provider=provider,
                claim=claim,
            )
            outcomes.append(outcome)
        except StaleWorkflowClaim as exc:
            errors.append(
                f"request_id={claim.request_id} stale_claim={_error_detail(exc)}"
            )
        except (ContributionBlocked, ContributionProviderError, WorkflowBlocked) as exc:
            reason = _error_detail(exc)
            try:
                terminal_status = state.block(
                    claim,
                    reason=reason,
                    max_blocked_attempts=config.max_blocked_attempts,
                    retry_after_seconds=config.retry_after_seconds,
                )
            except StaleWorkflowClaim as stale:
                errors.append(
                    f"request_id={claim.request_id} stale_claim={stale}"
                )
                continue
            outcomes.append(
                {
                    "request_id": claim.request_id,
                    "outcome": terminal_status,
                    "detail": reason,
                }
            )
        except Exception as exc:  # transport/validation failures are bounded
            reason = _error_detail(exc)
            try:
                terminal_status = state.block(
                    claim,
                    reason=reason,
                    max_blocked_attempts=config.max_blocked_attempts,
                    retry_after_seconds=config.retry_after_seconds,
                )
            except StaleWorkflowClaim as stale:
                errors.append(
                    f"request_id={claim.request_id} stale_claim={stale}"
                )
                continue
            outcomes.append(
                {
                    "request_id": claim.request_id,
                    "outcome": terminal_status,
                    "detail": reason,
                }
            )
            errors.append(f"request_id={claim.request_id} error={reason}")

    counts = state.counts(tenant_id=config.tenant_id)
    return {
        "tenant_id": config.tenant_id,
        "requests_discovered": discovered,
        "completed": sum(
            item["outcome"] == "completed" for item in outcomes
        ),
        "blocked": sum(item["outcome"] == "blocked" for item in outcomes),
        "dead_lettered": sum(
            item["outcome"] == "dead_letter" for item in outcomes
        ),
        "errors": errors,
        "outcomes": outcomes,
        "state_counts": counts,
    }


def _write_health(path: str, state: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    payload = json.dumps(dict(state), indent=2, sort_keys=True) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def initial_health_state(config: ExecutorConfig) -> dict[str, Any]:
    """Return the executor-owned health record consumed by compose wiring."""

    return {
        "consumer_name": CONSUMER_NAME,
        "worker_id": config.worker_id,
        "tenant_id": config.tenant_id,
        "status": "starting",
        "ticks": 0,
        "last_tick_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "total_completed": 0,
        "total_dead_lettered": 0,
        "functional_health": {
            "ready": False,
            "blocked_count": 0,
            "dead_letter_count": 0,
            "last_success_at": None,
        },
    }


def update_functional_health(
    health: dict[str, Any],
    result: Mapping[str, Any],
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Apply one tick without allowing process liveness to mask blocked work."""

    timestamp = observed_at or _utc_now()
    raw_counts = result.get("state_counts")
    counts = dict(raw_counts) if isinstance(raw_counts, Mapping) else {}
    blocked_count = int(counts.get("blocked") or 0)
    dead_letter_count = int(counts.get("dead_letter") or 0)
    errors = [str(item) for item in (result.get("errors") or []) if str(item)]

    health["ticks"] = int(health.get("ticks") or 0) + 1
    health["last_tick_at"] = timestamp
    health["total_completed"] = int(health.get("total_completed") or 0) + int(
        result.get("completed") or 0
    )
    health["total_dead_lettered"] = int(
        health.get("total_dead_lettered") or 0
    ) + int(result.get("dead_lettered") or 0)
    health["state_counts"] = counts

    failures = list(errors)
    if blocked_count:
        failures.append(f"{blocked_count} blocked item(s)")
    if dead_letter_count:
        failures.append(f"{dead_letter_count} dead-letter item(s)")

    if failures:
        health["status"] = "degraded"
        health["last_failure_at"] = timestamp
        health["last_failure_reason"] = "; ".join(failures)
    else:
        health["status"] = "ok"
        health["last_success_at"] = timestamp
        health["last_failure_reason"] = None

    health["functional_health"] = {
        "ready": health["status"] == "ok" and health["last_success_at"] is not None,
        "blocked_count": blocked_count,
        "dead_letter_count": dead_letter_count,
        "last_success_at": health["last_success_at"],
    }
    return health


def main() -> int:
    config = ExecutorConfig.from_env()
    state = WorkflowStateStore(config.state_path)
    provider = HttpContributionProvider(
        endpoint=config.provider_url,
        bearer_token=config.provider_token,
        service_actor=config.provider_service_actor,
        timeout_seconds=config.timeout_seconds,
    )
    interval_seconds = _env_int(
        "CONSULTATION_WORKFLOW_EXECUTOR_INTERVAL_SECONDS",
        15,
        minimum=1,
    )
    max_ticks = _env_int(
        "CONSULTATION_WORKFLOW_EXECUTOR_MAX_TICKS",
        0,
        minimum=0,
    )
    health_file = str(
        os.getenv("CONSULTATION_WORKFLOW_EXECUTOR_HEALTH_FILE")
        or Path(config.state_path).with_name("workflow-health.json")
    )
    replay_ids = [
        value.strip()
        for value in str(
            os.getenv("CONSULTATION_WORKFLOW_REPLAY_DLQ_REQUESTS") or ""
        ).split(",")
        if value.strip()
    ]
    for request_id in replay_ids:
        state.replay_dead_letter(
            tenant_id=config.tenant_id,
            request_id=request_id,
        )

    # The lease must outlive the gap between ticks plus one tick's timeout.
    writer = _build_loop_writer(
        dsn=config.database_url,
        tenant_id=config.tenant_id,
        lease_duration_seconds=int(interval_seconds + config.timeout_seconds) + 1,
    )
    loop_id = os.getenv("PANTHEON_LOOP_ID") or "consultation"

    health = initial_health_state(config)
    tick = 0
    while True:
        tick += 1
        result = run_tick(config=config, state=state, provider=provider)
        update_functional_health(health, result)
        _write_health(health_file, health)
        if writer is not None:
            try:
                publish_loop_truth(
                    writer, loop_id=loop_id, result=result, health=health
                )
            except Exception as exc:
                print(f"Warning: failed to write to LoopControllerWriter: {exc}", file=sys.stderr)
        print(
            json.dumps(
                {"tick": tick, "health": health, "result": result},
                sort_keys=True,
            ),
            flush=True,
        )
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
