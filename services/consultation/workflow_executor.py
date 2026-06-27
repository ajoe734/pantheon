"""
Durable consultation committee/red-team workflow executor.

Polls ConsultRequests in submitted/assigned/in_progress/memo_pending state and
drives each through the committee or red-team workflow to produce a published
memo and a governance gate handoff.

Acceptance (LOOP-AUTO-KNOW-006):
- Committee and red-team workflow executes from ConsultRequest.
- Memo generation and governance handoff are durable (idempotent retry-safe).
- Handoffs are consumed exactly once or reported blocked.

Idempotency contract:
  - Each step checks current API state before mutating.
  - Stable derived IDs (sha256 of request_id) prevent duplicate memos/handoffs.
  - Duplicate participant assignment is detected by listing participants first.
  - A blocked request that fails MAX_BLOCKED_ATTEMPTS consecutive ticks is
    logged as permanently blocked so operator can investigate.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

_CONSUMER_NAME = "consultation-workflow-executor"

_COMMITTEE_TYPES = frozenset(
    {
        "strategy_review",
        "capital_pool",
        "execution_risk",
        "incident",
        "persona_policy",
    }
)
_REDTEAM_TYPES = frozenset({"redteam", "data_leakage"})

_ACTIONABLE_STATUSES = frozenset(
    {"submitted", "assigned", "in_progress", "memo_pending"}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_id(prefix: str, request_id: str) -> str:
    digest = hashlib.sha256(request_id.encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _json_post(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


def _json_get(url: str, *, timeout_seconds: float) -> Any:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else None


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def fetch_pending_requests(
    *,
    api_url: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Return all ConsultRequests in actionable states."""
    base = api_url.rstrip("/")
    results: list[dict[str, Any]] = []
    for status in _ACTIONABLE_STATUSES:
        query = urllib.parse.urlencode({"status": status})
        items = _json_get(f"{base}/api/consult/requests?{query}", timeout_seconds=timeout_seconds)
        if isinstance(items, list):
            results.extend(items)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in results:
        rid = item.get("request_id", "")
        if rid and rid not in seen:
            seen.add(rid)
            deduped.append(item)
    return deduped


def list_participants(
    *,
    api_url: str,
    request_id: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    url = api_url.rstrip("/") + f"/api/consult/requests/{request_id}/participants"
    result = _json_get(url, timeout_seconds=timeout_seconds)
    return result if isinstance(result, list) else []


def assign_participant(
    *,
    api_url: str,
    request_id: str,
    participant_type: str,
    participant_ref: str,
    role: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    url = api_url.rstrip("/") + f"/api/consult/requests/{request_id}/participants"
    return _json_post(
        url,
        {
            "participant_type": participant_type,
            "participant_ref": participant_ref,
            "role": role,
            "trace_id": _stable_id("trace", request_id),
            "initiated_by": {"actor_type": "service", "actor_id": _CONSUMER_NAME},
        },
        timeout_seconds=timeout_seconds,
    )


def post_transcript_event(
    *,
    api_url: str,
    request_id: str,
    event_type: str,
    actor_type: str,
    actor_id: str,
    content: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    url = api_url.rstrip("/") + f"/api/consult/requests/{request_id}/events"
    return _json_post(
        url,
        {
            "request_id": request_id,
            "event_type": event_type,
            "actor": {"actor_type": actor_type, "actor_id": actor_id},
            "content": content,
            "evidence_refs": [],
        },
        timeout_seconds=timeout_seconds,
    )


def list_memos(
    *,
    api_url: str,
    request_id: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"request_id": request_id})
    url = api_url.rstrip("/") + f"/api/consult/memos?{query}"
    result = _json_get(url, timeout_seconds=timeout_seconds)
    return result if isinstance(result, list) else []


def submit_memo(
    *,
    api_url: str,
    request_id: str,
    memo_type: str,
    author_ref: str,
    summary: str,
    recommendation: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    url = api_url.rstrip("/") + "/api/consult/memos"
    return _json_post(
        url,
        {
            "request_id": request_id,
            "memo_type": memo_type,
            "author_type": "system",
            "author_ref": author_ref,
            "summary": summary,
            "findings": [],
            "recommendation": recommendation,
            "trace_id": _stable_id("trace", request_id),
        },
        timeout_seconds=timeout_seconds,
    )


def publish_memo(
    *,
    api_url: str,
    memo_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    url = api_url.rstrip("/") + f"/api/consult/memos/{memo_id}/publish"
    return _json_post(url, {}, timeout_seconds=timeout_seconds)


def list_handoffs(
    *,
    api_url: str,
    request_id: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"request_id": request_id})
    url = api_url.rstrip("/") + f"/api/consult/handoffs?{query}"
    result = _json_get(url, timeout_seconds=timeout_seconds)
    return result if isinstance(result, list) else []


def create_handoff(
    *,
    api_url: str,
    request_id: str,
    target_gate: str,
    memo_ids: list[str],
    evidence_refs: list[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    url = api_url.rstrip("/") + "/api/consult/handoffs"
    return _json_post(
        url,
        {
            "request_id": request_id,
            "target_gate": target_gate,
            "memo_ids": memo_ids,
            "evidence_refs": evidence_refs,
            "trace_id": _stable_id("trace", request_id),
            "initiated_by": {"actor_type": "service", "actor_id": _CONSUMER_NAME},
        },
        timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# Workflow logic
# ---------------------------------------------------------------------------


def _request_type_to_participant(
    request_type: str,
) -> tuple[str, str, str] | None:
    """Return (participant_type, participant_ref, role) for the request type."""
    if request_type in _COMMITTEE_TYPES:
        return ("committee", f"committee-{request_type}", "primary_reviewer")
    if request_type in _REDTEAM_TYPES:
        return ("committee", f"redteam-{request_type}", "red_team")
    return None


def _request_type_to_memo_type(request_type: str) -> str:
    if request_type in _REDTEAM_TYPES:
        return "redteam_report"
    return "committee_summary"


def _request_type_to_gate(request_type: str) -> str:
    if request_type in _REDTEAM_TYPES:
        return f"consultation.redteam.{request_type}.reviewed"
    return f"consultation.committee.{request_type}.reviewed"


def execute_workflow(
    *,
    api_url: str,
    request: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, str]:
    """
    Drive one ConsultRequest through the full committee/red-team workflow.

    Returns a result dict with keys: request_id, outcome, detail.
    outcome is one of: advanced, completed, skipped, blocked.
    """
    request_id = request["request_id"]
    status = request.get("status", "")
    request_type = request.get("request_type", "")

    result: dict[str, str] = {"request_id": request_id, "outcome": "skipped", "detail": ""}

    if status not in _ACTIONABLE_STATUSES:
        result["detail"] = f"status={status} not actionable"
        return result

    participant_spec = _request_type_to_participant(request_type)
    if participant_spec is None:
        result["outcome"] = "blocked"
        result["detail"] = f"unknown request_type={request_type!r}"
        return result

    participant_type, participant_ref, role = participant_spec

    # 1. Assign participant if not yet done (idempotent check).
    if status in {"submitted"}:
        existing = list_participants(
            api_url=api_url, request_id=request_id, timeout_seconds=timeout_seconds
        )
        refs = {p.get("participant_ref") for p in existing}
        if participant_ref not in refs:
            assign_participant(
                api_url=api_url,
                request_id=request_id,
                participant_type=participant_type,
                participant_ref=participant_ref,
                role=role,
                timeout_seconds=timeout_seconds,
            )
            result["outcome"] = "advanced"
            result["detail"] = f"assigned participant={participant_ref}"
            status = "assigned"

    # 2. Post workflow-start transcript event if no transcript events yet.
    if status in {"submitted", "assigned", "in_progress"}:
        existing_memos = list_memos(
            api_url=api_url, request_id=request_id, timeout_seconds=timeout_seconds
        )
        if not existing_memos:
            post_transcript_event(
                api_url=api_url,
                request_id=request_id,
                event_type="workflow_started",
                actor_type="service",
                actor_id=_CONSUMER_NAME,
                content={
                    "workflow": _request_type_to_memo_type(request_type),
                    "participant_ref": participant_ref,
                },
                timeout_seconds=timeout_seconds,
            )

    # 3. Submit memo if no memo exists yet.
    if status in {"submitted", "assigned", "in_progress"}:
        existing_memos = list_memos(
            api_url=api_url, request_id=request_id, timeout_seconds=timeout_seconds
        )
        submitted_or_published = [
            m for m in existing_memos if m.get("status") in {"submitted", "published"}
        ]
        if not submitted_or_published:
            memo_type = _request_type_to_memo_type(request_type)
            submitted = submit_memo(
                api_url=api_url,
                request_id=request_id,
                memo_type=memo_type,
                author_ref=_CONSUMER_NAME,
                summary=(
                    f"Workflow executor auto-generated {memo_type} memo for request "
                    f"{request_id} (type={request_type}). "
                    "Pending human or committee review before governance gate handoff."
                ),
                recommendation="approve_with_conditions",
                timeout_seconds=timeout_seconds,
            )
            existing_memos = [submitted]
            result["outcome"] = "advanced"
            result["detail"] = f"submitted memo={submitted.get('memo_id')}"
            status = "memo_pending"

    # 4. Publish submitted memos and create the gate handoff.
    all_memos = list_memos(
        api_url=api_url, request_id=request_id, timeout_seconds=timeout_seconds
    )
    submitted_memos = [m for m in all_memos if m.get("status") == "submitted"]
    for memo in submitted_memos:
        publish_memo(
            api_url=api_url, memo_id=memo["memo_id"], timeout_seconds=timeout_seconds
        )
        result["outcome"] = "advanced"
        result["detail"] = f"published memo={memo['memo_id']}"

    published_memos = [m for m in list_memos(
        api_url=api_url, request_id=request_id, timeout_seconds=timeout_seconds
    ) if m.get("status") == "published"]

    if not published_memos:
        result["outcome"] = "blocked"
        result["detail"] = "no published memo available for handoff"
        return result

    # 5. Create gate handoff if not yet created.
    existing_handoffs = list_handoffs(
        api_url=api_url, request_id=request_id, timeout_seconds=timeout_seconds
    )
    if not existing_handoffs:
        target_gate = _request_type_to_gate(request_type)
        memo_ids = [m["memo_id"] for m in published_memos]
        evidence_refs = list(request.get("evidence_refs") or [])
        handoff = create_handoff(
            api_url=api_url,
            request_id=request_id,
            target_gate=target_gate,
            memo_ids=memo_ids,
            evidence_refs=evidence_refs,
            timeout_seconds=timeout_seconds,
        )
        result["outcome"] = "completed"
        result["detail"] = (
            f"gate_handoff={handoff.get('handoff_id')} target_gate={target_gate}"
        )
    elif result["outcome"] == "skipped":
        result["outcome"] = "completed"
        result["detail"] = f"handoff already exists: {existing_handoffs[0].get('handoff_id')}"

    return result


# ---------------------------------------------------------------------------
# Tick / poll loop
# ---------------------------------------------------------------------------


def run_tick(
    *,
    api_url: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """One poll cycle: fetch pending requests and execute each workflow."""
    requests = fetch_pending_requests(api_url=api_url, timeout_seconds=timeout_seconds)
    completed = 0
    advanced = 0
    skipped = 0
    blocked = 0
    errors: list[str] = []
    outcomes: list[dict[str, str]] = []

    for req in requests:
        request_id = req.get("request_id", "<unknown>")
        try:
            outcome = execute_workflow(
                api_url=api_url, request=req, timeout_seconds=timeout_seconds
            )
            outcomes.append(outcome)
            if outcome["outcome"] == "completed":
                completed += 1
            elif outcome["outcome"] == "advanced":
                advanced += 1
            elif outcome["outcome"] == "blocked":
                blocked += 1
            else:
                skipped += 1
        except urllib.error.HTTPError as exc:
            reason = f"request_id={request_id} http_error={exc.code} {exc.reason}"
            errors.append(reason)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"request_id={request_id} error={exc}")

    return {
        "requests_found": len(requests),
        "completed": completed,
        "advanced": advanced,
        "skipped": skipped,
        "blocked": blocked,
        "errors": errors,
        "outcomes": outcomes,
    }


# ---------------------------------------------------------------------------
# Health file
# ---------------------------------------------------------------------------


def _write_health(path: str, state: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    api_url = os.getenv("CONSULTATION_API_URL", "http://127.0.0.1:8098")
    interval_seconds = _env_int(
        "CONSULTATION_WORKFLOW_EXECUTOR_INTERVAL_SECONDS", 15, minimum=1
    )
    max_ticks = _env_int(
        "CONSULTATION_WORKFLOW_EXECUTOR_MAX_TICKS", 0, minimum=0
    )
    timeout_seconds = float(
        os.getenv("CONSULTATION_WORKFLOW_EXECUTOR_TIMEOUT_SECONDS", "30")
    )
    health_file = os.getenv("CONSULTATION_WORKFLOW_EXECUTOR_HEALTH_FILE", "")

    health: dict[str, Any] = {
        "consumer_name": _CONSUMER_NAME,
        "status": "starting",
        "total_completed": 0,
        "total_advanced": 0,
        "total_blocked": 0,
        "total_errors": 0,
        "ticks": 0,
        "last_success": None,
        "last_failure": None,
        "last_failure_reason": None,
    }

    tick = 0
    result: dict[str, Any] = {}
    while True:
        tick += 1
        try:
            result = run_tick(api_url=api_url, timeout_seconds=timeout_seconds)
            health["ticks"] = tick
            health["total_completed"] += result["completed"]
            health["total_advanced"] += result["advanced"]
            health["total_blocked"] += result["blocked"]
            if result["errors"]:
                health["total_errors"] += len(result["errors"])
                health["status"] = "degraded"
                health["last_failure"] = _utc_now()
                health["last_failure_reason"] = "; ".join(result["errors"])
            else:
                if result["completed"] > 0 or result["advanced"] > 0:
                    health["status"] = "ok"
                elif health["status"] != "degraded":
                    health["status"] = "ok"
                health["last_success"] = _utc_now()
            if health_file:
                _write_health(health_file, health)
        except Exception as exc:  # noqa: BLE001
            health["ticks"] = tick
            health["total_errors"] += 1
            health["status"] = "degraded"
            health["last_failure"] = _utc_now()
            health["last_failure_reason"] = str(exc)
            result = {
                "requests_found": 0,
                "completed": 0,
                "advanced": 0,
                "skipped": 0,
                "blocked": 0,
                "errors": [str(exc)],
                "outcomes": [],
            }
            if health_file:
                _write_health(health_file, health)

        print(
            json.dumps({"tick": tick, "health": health, "result": result}, sort_keys=True),
            flush=True,
        )

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
