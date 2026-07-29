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

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

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
) -> str:
    handoff_id = str(handoff.get("handoff_id") or "")
    if not handoff_id:
        raise WorkflowBlocked("persisted handoff response is missing handoff_id")
    if not config.handoff_sink_url:
        # The consultation API's successful durable create is the minimum
        # acknowledgement boundary.  Production may configure a governance
        # sink for an additional service-boundary acknowledgement.
        return "consultation-api"
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

    recovered = _existing_acknowledged_handoff(config, claim.request_id)
    if recovered is not None:
        memo_id, handoff_id = recovered
        state.complete(claim, memo_id=memo_id, handoff_id=handoff_id)
        return {
            "request_id": claim.request_id,
            "outcome": "completed",
            "detail": f"recovered acknowledged handoff={handoff_id}",
        }

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


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_error={exc.code} {exc.reason}"
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

    health: dict[str, Any] = {
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
    }
    tick = 0
    while True:
        tick += 1
        result = run_tick(config=config, state=state, provider=provider)
        health["ticks"] = tick
        health["last_tick_at"] = _utc_now()
        health["total_completed"] += result["completed"]
        health["total_dead_lettered"] += result["dead_lettered"]
        health["state_counts"] = result["state_counts"]
        if result["errors"] or result["state_counts"]["dead_letter"]:
            health["status"] = "degraded"
            health["last_failure_at"] = _utc_now()
            health["last_failure_reason"] = "; ".join(result["errors"]) or (
                f"{result['state_counts']['dead_letter']} dead-letter item(s)"
            )
        else:
            health["status"] = "ok"
            health["last_success_at"] = _utc_now()
            health["last_failure_reason"] = None
        _write_health(health_file, health)
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
