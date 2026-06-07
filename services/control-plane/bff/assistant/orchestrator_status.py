"""Orchestrator status readback for the assistant (ASST-INTEG-007).

This service reads ai-status.json and .orchestrator/state.json to provide
a unified view of the project state and worker activity.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import OrchestratorStatusResponse, OrchestratorTaskStatus, OrchestratorWorkerStatus
from .redaction import redact_payload, redact_text


GITHUB_BUS_SOURCE = ".orchestrator/github-bus-state.json"
DEFAULT_ASSISTANT_DEV_PACKET_INBOX = ".orchestrator/assistant-dev-packets"
ASSISTANT_DEV_PACKET_INBOX_ENV = "PANTHEON_ASSISTANT_DEV_PACKET_INBOX"
FAILURE_CHECK_STATES = {"ACTION_REQUIRED", "CANCELLED", "ERROR", "FAILURE", "FAILED", "STALE", "TIMED_OUT"}
PENDING_CHECK_STATES = {"EXPECTED", "IN_PROGRESS", "PENDING", "QUEUED", "REQUESTED", "WAITING"}
SUCCESS_CHECK_STATES = {"COMPLETED", "NEUTRAL", "SKIPPED", "SUCCESS"}
DEPLOY_CHECK_MARKERS = (
    "deploy",
    "deployment",
    "cloud run",
    "gcp",
    "nonprod",
    "publish",
    "release",
    "staging",
    "production",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _find_repo_root(start: Optional[str] = None) -> Path:
    """Walk up from *start* to find the Pantheon repo root."""
    if start:
        candidate = Path(start)
    else:
        env = os.environ.get("PANTHEON_STATUS_ROOT")
        candidate = Path(env) if env else Path(__file__).resolve()
    candidate = candidate if candidate.is_dir() else candidate.parent
    for _ in range(12):
        if (candidate / "ai-status.json").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return Path(start or os.getcwd())


def _safe(value: Any) -> Any:
    return redact_payload(value).value


def _safe_text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return redact_text(str(value))


def _mtime_z(path: Path) -> Optional[str]:
    try:
        return (
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except OSError:
        return None


def _source_ref(root: Path, path: Path, source_type: str, snapshot_at: str) -> Dict[str, Any]:
    try:
        rel_path = path.relative_to(root).as_posix()
    except ValueError:
        rel_path = path.as_posix()
    available = path.exists()
    ref: Dict[str, Any] = {
        "sourceType": source_type,
        "path": rel_path,
        "available": available,
        "status": "ok" if available else "unavailable",
        "snapshotAt": snapshot_at,
    }
    if available:
        ref["lastModifiedAt"] = _mtime_z(path)
    return ref


def _load_json_source(
    root: Path,
    path: Path,
    source_type: str,
    snapshot_at: str,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    ref = _source_ref(root, path, source_type, snapshot_at)
    if not path.exists():
        ref["message"] = f"{ref['path']} is not present in this repository snapshot."
        return {}, ref
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        ref["status"] = "invalid_json"
        ref["message"] = f"Could not parse JSON at line {exc.lineno}."
        return {}, ref
    except OSError as exc:
        ref["status"] = "unavailable"
        ref["message"] = _safe_text(exc) or "Could not read source file."
        return {}, ref
    if not isinstance(payload, dict):
        ref["status"] = "invalid_shape"
        ref["message"] = "Expected a JSON object."
        return {}, ref
    return payload, ref


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping) and value:
            return value
    return {}


def _task_blockers(blockers: Iterable[Any], task_id: str) -> List[Dict[str, Any]]:
    task_blockers: List[Dict[str, Any]] = []
    for blocker in blockers:
        item = _as_mapping(blocker)
        if not item:
            continue
        related = {
            str(item.get("task_id") or item.get("taskId") or item.get("id") or "").strip(),
        }
        raw_tasks = item.get("tasks") or item.get("task_ids") or item.get("taskIds") or []
        if isinstance(raw_tasks, list):
            related.update(str(value).strip() for value in raw_tasks)
        if task_id not in related:
            continue
        task_blockers.append(
            _safe(
                {
                    "status": item.get("status"),
                    "waitingFor": item.get("waiting_for") or item.get("waitingFor"),
                    "message": item.get("message") or item.get("reason") or item.get("summary"),
                    "createdAt": item.get("created_at") or item.get("createdAt"),
                    "updatedAt": item.get("updated_at") or item.get("updatedAt"),
                    "owner": item.get("owner"),
                }
            )
        )
    return task_blockers


def _check_items(value: Any) -> List[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        for key in ("nodes", "items", "checks", "statusCheckRollup", "status_check_rollup"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, Mapping)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _check_state(item: Mapping[str, Any]) -> str:
    conclusion = str(item.get("conclusion") or item.get("state") or "").strip().upper()
    status = str(item.get("status") or "").strip().upper()
    return conclusion or status or "UNKNOWN"


def _check_item(item: Mapping[str, Any]) -> Dict[str, Any]:
    return _safe(
        {
            "name": item.get("name") or item.get("context") or item.get("workflowName") or item.get("workflow_name"),
            "workflowName": item.get("workflowName") or item.get("workflow_name"),
            "status": item.get("status"),
            "conclusion": item.get("conclusion"),
            "state": item.get("state"),
            "url": item.get("detailsUrl") or item.get("details_url") or item.get("targetUrl") or item.get("target_url"),
            "startedAt": item.get("startedAt") or item.get("started_at"),
            "completedAt": item.get("completedAt") or item.get("completed_at"),
        }
    )


def _summarize_check_states(items: List[Mapping[str, Any]]) -> str:
    states = {_check_state(item) for item in items}
    if states & FAILURE_CHECK_STATES:
        return "failing"
    if states & PENDING_CHECK_STATES:
        return "pending"
    if states and states <= SUCCESS_CHECK_STATES:
        return "success"
    if states:
        return "unknown"
    return "unavailable"


def _normalize_check_rollup(value: Any) -> Dict[str, Any]:
    if value in (None, ""):
        return {"available": False, "summary": "unavailable", "total": 0, "counts": {}}
    if isinstance(value, str):
        return _safe({"available": True, "summary": value, "total": None, "counts": {value: 1}})

    items = _check_items(value)
    if not items:
        return _safe({"available": True, "summary": str(value), "total": None, "counts": {}})

    counts: Dict[str, int] = {}
    normalized_items = [_check_item(item) for item in items]
    for item in items:
        state = _check_state(item)
        counts[state] = counts.get(state, 0) + 1

    failed = [item for item, raw in zip(normalized_items, items) if _check_state(raw) in FAILURE_CHECK_STATES]
    pending = [item for item, raw in zip(normalized_items, items) if _check_state(raw) in PENDING_CHECK_STATES]
    return {
        "available": True,
        "summary": _summarize_check_states(items),
        "total": len(items),
        "counts": counts,
        "items": normalized_items[:20],
        "failed": failed[:10],
        "pending": pending[:10],
    }


def _normalize_pr_status(pr_ref: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if not pr_ref:
        return None
    check_value = pr_ref.get("status_check_rollup")
    if check_value is None:
        check_value = pr_ref.get("statusCheckRollup")
    ci = _normalize_check_rollup(check_value)
    return _safe(
        {
            "available": True,
            "number": pr_ref.get("number"),
            "url": pr_ref.get("url"),
            "title": pr_ref.get("title"),
            "branch": pr_ref.get("branch") or pr_ref.get("headRefName") or pr_ref.get("head_ref_name"),
            "headSha": pr_ref.get("head_sha") or pr_ref.get("headSha"),
            "state": pr_ref.get("state"),
            "lastStatusCheckAt": pr_ref.get("last_status_check_at") or pr_ref.get("lastStatusCheckAt"),
            "ci": ci,
            "merge": {
                "state": pr_ref.get("state"),
                "mergeable": pr_ref.get("mergeable"),
                "mergeStateStatus": pr_ref.get("merge_state_status") or pr_ref.get("mergeStateStatus"),
                "mergedAt": pr_ref.get("merged_at") or pr_ref.get("mergedAt"),
            },
        }
    )


def _normalize_issue_status(issue_ref: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if not issue_ref:
        return None
    return _safe(
        {
            "available": True,
            "number": issue_ref.get("number"),
            "url": issue_ref.get("url"),
            "title": issue_ref.get("title"),
            "state": issue_ref.get("state"),
        }
    )


def _is_deploy_check(item: Mapping[str, Any]) -> bool:
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("name", "workflowName", "workflow_name", "context")
    ).lower()
    return any(marker in haystack for marker in DEPLOY_CHECK_MARKERS)


def _status_from_normalized_checks(items: List[Mapping[str, Any]]) -> str:
    raw_items = [
        {
            "state": item.get("state"),
            "status": item.get("status"),
            "conclusion": item.get("conclusion"),
        }
        for item in items
    ]
    return _summarize_check_states(raw_items)


def _normalize_deployment_status(delivery: Mapping[str, Any], bus_entry: Mapping[str, Any], pr_status: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    for source_name, container in (("ai-status delivery", delivery), ("github bus", bus_entry)):
        for key in ("deployment", "deploymentStatus", "deployment_status", "deploy", "deployStatus", "deploy_status", "deployments"):
            value = container.get(key)
            if value in (None, "", [], {}):
                continue
            if isinstance(value, Mapping):
                status = value.get("status") or value.get("state") or value.get("conclusion") or "available"
                return _safe({"available": True, "source": source_name, "status": status, "detail": dict(value)})
            if isinstance(value, list):
                return _safe({"available": True, "source": source_name, "status": "available", "items": value[:20]})
            return _safe({"available": True, "source": source_name, "status": str(value)})

    ci = _as_mapping((pr_status or {}).get("ci"))
    deploy_items = [item for item in ci.get("items", []) if isinstance(item, Mapping) and _is_deploy_check(item)]
    if deploy_items:
        return _safe(
            {
                "available": True,
                "source": "github status checks",
                "status": _status_from_normalized_checks(deploy_items),
                "items": deploy_items[:10],
            }
        )
    return {"available": False, "status": "not_available"}


def _normalize_github_status(
    *,
    delivery: Mapping[str, Any],
    bus_entry: Mapping[str, Any],
    github_bus_available: bool,
) -> Dict[str, Any]:
    delivery_github = _as_mapping(delivery.get("github"))
    delivery_bus = _as_mapping(delivery.get("github_bus") or delivery.get("githubBus"))
    review_pr = _first_mapping(
        bus_entry.get("review_pr"),
        bus_entry.get("reviewPr"),
        delivery.get("review_pr"),
        delivery_github.get("review_pr"),
        delivery_github.get("reviewPr"),
        delivery_bus.get("review_pr"),
        delivery_bus.get("reviewPr"),
    )
    ops_issue = _first_mapping(
        bus_entry.get("ops_issue"),
        bus_entry.get("opsIssue"),
        delivery_github.get("ops_issue"),
        delivery_github.get("opsIssue"),
        delivery_bus.get("ops_issue"),
        delivery_bus.get("opsIssue"),
    )
    pr_status = _normalize_pr_status(review_pr)
    issue_status = _normalize_issue_status(ops_issue)
    if not pr_status and not issue_status:
        return {
            "available": False,
            "source": GITHUB_BUS_SOURCE,
            "status": "unavailable" if not github_bus_available else "not_found",
        }

    github_status = {
        "available": True,
        "source": GITHUB_BUS_SOURCE if bus_entry else "ai-status delivery",
        "status": (pr_status or {}).get("state") or (issue_status or {}).get("state") or "available",
        "reviewPr": pr_status,
        "opsIssue": issue_status,
    }
    return _safe(github_status)


def _delivery_payload(task: Mapping[str, Any], bus_entry: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    delivery = deepcopy(_as_mapping(task.get("delivery")))
    if bus_entry:
        delivery["github_bus"] = deepcopy(dict(bus_entry))
    return _safe(delivery) if delivery else None


def _normalize_workers(state: Mapping[str, Any]) -> List[OrchestratorWorkerStatus]:
    workers: List[OrchestratorWorkerStatus] = []
    for run_id, worker in sorted(_as_mapping(state.get("workers")).items()):
        item = _as_mapping(worker)
        workers.append(
            OrchestratorWorkerStatus(
                runId=str(run_id),
                taskId=item.get("task_id"),
                agent=item.get("agent_id") or item.get("agent") or "unknown",
                provider=item.get("provider"),
                status=item.get("status") or "unknown",
                startedAt=item.get("started_at"),
                lastEventAt=item.get("last_event_at"),
                lastError=_safe_text(item.get("last_error")),
                queueEventId=item.get("queue_event_id"),
                dispatchReason=item.get("dispatch_reason") or item.get("reason"),
                deliveryMode=item.get("delivery_mode"),
            )
        )
    return workers


def _normalize_queue(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    worker_by_event: Dict[str, Mapping[str, Any]] = {}
    for worker in _as_mapping(state.get("workers")).values():
        item = _as_mapping(worker)
        event_id = str(item.get("queue_event_id") or "").strip()
        if event_id:
            worker_by_event[event_id] = item

    queue: List[Dict[str, Any]] = []
    records = _as_mapping(_as_mapping(state.get("queue")).get("events"))
    for event_id, record in sorted(records.items()):
        item = _as_mapping(record)
        worker = worker_by_event.get(str(event_id), {})
        queue.append(
            _safe(
                {
                    "eventId": event_id,
                    "taskId": item.get("task_id") or worker.get("task_id"),
                    "agent": item.get("agent_id") or worker.get("agent_id") or worker.get("agent"),
                    "provider": item.get("provider") or worker.get("provider"),
                    "status": item.get("status") or "unknown",
                    "reason": item.get("reason") or item.get("dispatch_reason") or worker.get("dispatch_reason"),
                    "attemptCount": item.get("attempt_count"),
                    "queuedAt": item.get("queued_at") or item.get("created_at"),
                    "processedAt": item.get("processed_at"),
                    "lastError": item.get("error") or worker.get("last_error"),
                }
            )
        )
    return queue[:100]


def _normalize_supervisor(state: Mapping[str, Any]) -> Dict[str, Any]:
    supervisor = _as_mapping(state.get("supervisor"))
    return _safe(
        {
            "pid": supervisor.get("pid"),
            "startedAt": supervisor.get("started_at"),
            "lastHeartbeatAt": supervisor.get("last_heartbeat_at"),
            "lifecycle": supervisor.get("lifecycle"),
            "modeStatus": supervisor.get("mode_status"),
            "focusMode": supervisor.get("focus_mode"),
            "lastSuccessfulLoopAt": supervisor.get("last_successful_loop_at"),
            "lastLoopStartedAt": supervisor.get("last_loop_started_at"),
            "lastLoopFinishedAt": supervisor.get("last_loop_finished_at"),
            "lastLoopDurationMs": supervisor.get("last_loop_duration_ms"),
            "lastLoopError": supervisor.get("last_loop_error"),
            "modeOccupancy": supervisor.get("mode_occupancy") if isinstance(supervisor.get("mode_occupancy"), Mapping) else {},
        }
    )


def _normalize_coordination(state: Mapping[str, Any]) -> Dict[str, Any]:
    coordination = _as_mapping(state.get("coordination"))
    files = _as_mapping(coordination.get("files"))
    features = _as_mapping(coordination.get("features"))
    return _safe(
        {
            "lastScanAt": coordination.get("last_scan_at"),
            "fileCount": len(files),
            "featureCount": len(features),
            "featureIds": sorted(str(key) for key in features.keys())[:50],
        }
    )


def _normalize_provider_guardrails(state: Mapping[str, Any]) -> Dict[str, Any]:
    guardrails = _as_mapping(state.get("provider_guardrails"))
    pauses = _as_mapping(guardrails.get("dispatch_pauses"))
    normalized_pauses = []
    for provider, pause in sorted(pauses.items()):
        item = _as_mapping(pause)
        normalized_pauses.append(
            _safe(
                {
                    "provider": provider,
                    "triggerProvider": item.get("trigger_provider"),
                    "blockedUntil": item.get("blocked_until"),
                    "reason": item.get("reason") or item.get("summary"),
                    "summary": item.get("summary"),
                    "failureKind": item.get("failure_kind"),
                    "detail": item.get("detail"),
                }
            )
        )
    failure_streaks = _as_mapping(guardrails.get("task_failure_streaks"))
    return {"dispatchPauses": normalized_pauses, "taskFailureStreakCount": len(failure_streaks)}


def _assistant_inbox_root(root: Path) -> Path:
    configured = os.environ.get(ASSISTANT_DEV_PACKET_INBOX_ENV, "").strip() or DEFAULT_ASSISTANT_DEV_PACKET_INBOX
    path = Path(configured)
    if not path.is_absolute():
        path = root / path
    return path


def _count_json_files(path: Path) -> int:
    try:
        if not path.exists():
            return 0
        return sum(1 for item in path.glob("*.json") if item.is_file())
    except OSError:
        return 0


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _recent_receipts(path: Path, *, limit: int = 5) -> List[Dict[str, Any]]:
    try:
        files = sorted(
            (item for item in path.glob("*.json") if item.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []
    receipts: List[Dict[str, Any]] = []
    for receipt_path in files[:limit]:
        receipt = _read_json_file(receipt_path)
        result = _as_mapping(receipt.get("result"))
        receipts.append(
            _safe(
                {
                    "packetId": receipt.get("packetId") or receipt_path.stem,
                    "status": receipt.get("status"),
                    "drainedAt": receipt.get("drainedAt"),
                    "dryRun": receipt.get("dryRun"),
                    "processedTaskCount": result.get("processedTaskCount") or result.get("processed_task_count"),
                    "errorCount": len(result.get("errors") or []) if isinstance(result.get("errors"), list) else None,
                    "archivedPath": receipt.get("archivedPath"),
                    "error": receipt.get("error"),
                }
            )
        )
    return receipts


def _normalize_assistant_dev_bridge(root: Path, state: Mapping[str, Any]) -> Dict[str, Any]:
    bridge = _as_mapping(state.get("assistant_dev_bridge") or state.get("assistantDevBridge"))
    inbox = _assistant_inbox_root(root)
    last_result = _as_mapping(bridge.get("last_result") or bridge.get("lastResult"))
    counts = {
        "pending": _count_json_files(inbox / "pending"),
        "processed": _count_json_files(inbox / "processed"),
        "failed": _count_json_files(inbox / "failed"),
        "receipts": _count_json_files(inbox / "receipts"),
    }
    status = "idle"
    if counts["pending"] > 0:
        status = "pending"
    if counts["failed"] > 0 or int(last_result.get("errorCount") or last_result.get("error_count") or 0) > 0:
        status = "attention"

    return _safe(
        {
            "status": status,
            "inbox": {
                "path": str(inbox),
                "exists": inbox.exists(),
                "pendingCount": counts["pending"],
                "processedCount": counts["processed"],
                "failedCount": counts["failed"],
                "receiptCount": counts["receipts"],
            },
            "lastDrainAt": bridge.get("last_drain_at") or bridge.get("lastDrainAt"),
            "lastResult": last_result,
            "recentReceipts": _recent_receipts(inbox / "receipts"),
        }
    )


def _provider_status_from_ready(ready: Optional[bool], fallback: str = "unavailable") -> str:
    if ready is True:
        return "ready"
    if ready is False:
        return "degraded"
    return fallback


def _normalize_provider_readiness(
    provider_readiness: Optional[Callable[[], Mapping[str, Any]]],
    snapshot_at: str,
) -> Dict[str, Any]:
    if provider_readiness is None:
        return {
            "available": False,
            "provider": "codex_cli",
            "runtime": "openclaw_gateway_cli_mount",
            "ready": None,
            "status": "not_configured",
            "checkedAt": snapshot_at,
            "source": "not_configured",
        }
    try:
        raw = provider_readiness()
    except Exception as exc:  # noqa: BLE001 - status readback must fail soft
        return _safe(
            {
                "available": False,
                "provider": "codex_cli",
                "runtime": "openclaw_gateway_cli_mount",
                "ready": False,
                "status": "unavailable",
                "reason": type(exc).__name__,
                "message": str(exc),
                "checkedAt": snapshot_at,
                "source": "openclaw_gateway_adapter",
            }
        )

    value = _as_mapping(raw.get("data")) if isinstance(raw, Mapping) and isinstance(raw.get("data"), Mapping) else _as_mapping(raw)
    ready_value = value.get("ready")
    ready = ready_value if isinstance(ready_value, bool) else None
    status = str(value.get("status") or _provider_status_from_ready(ready)).strip() or "unknown"
    return _safe(
        {
            "available": True,
            "provider": value.get("provider") or value.get("provider_name") or "codex_cli",
            "providerName": value.get("provider_name") or value.get("provider") or "codex_cli",
            "runtime": value.get("runtime") or "openclaw_gateway_cli_mount",
            "ready": ready,
            "status": status,
            "reason": value.get("reason") or value.get("degraded_reason"),
            "degradedReason": value.get("degraded_reason") or value.get("reason"),
            "auth": value.get("auth"),
            "authStatus": value.get("auth_status") or value.get("authStatus"),
            "binaryPath": value.get("binary_path") or value.get("binaryPath"),
            "version": value.get("version"),
            "credentialMount": value.get("credential_mount") or value.get("credentialMount"),
            "mountMode": value.get("mount_mode") or value.get("mountMode"),
            "repairWorkspace": value.get("repair_workspace") or value.get("repairWorkspace"),
            "capabilities": value.get("capabilities"),
            "checkedAt": value.get("checked_at") or value.get("checkedAt") or snapshot_at,
            "source": "openclaw_gateway_adapter",
        }
    )


def read_orchestrator_status(
    repo_root: Optional[str] = None,
    *,
    provider_readiness: Optional[Callable[[], Mapping[str, Any]]] = None,
) -> OrchestratorStatusResponse:
    root = _find_repo_root(repo_root)
    snapshot_at = _now()

    ai_status_path = root / "ai-status.json"
    state_path = root / ".orchestrator" / "state.json"
    bus_state_path = root / GITHUB_BUS_SOURCE
    ai_status, ai_status_ref = _load_json_source(root, ai_status_path, "task_status", snapshot_at)
    state, state_ref = _load_json_source(root, state_path, "worker_runtime", snapshot_at)
    bus_state, bus_state_ref = _load_json_source(root, bus_state_path, "github_bus", snapshot_at)
    source_refs = [ai_status_ref, state_ref, bus_state_ref]

    bus_tasks = _as_mapping(bus_state.get("tasks"))
    blockers = list(ai_status.get("blockers", []) or [])

    tasks: List[OrchestratorTaskStatus] = []
    for raw_task in ai_status.get("tasks", []) or []:
        t = _as_mapping(raw_task)
        task_id = str(t.get("id", ""))
        brief_path = f".orchestrator/task-briefs/{task_id.lower().replace('-', '_')}.md"
        if not (root / brief_path).exists():
            brief_path = None

        bus_entry = _as_mapping(bus_tasks.get(task_id))
        delivery_source = _as_mapping(t.get("delivery"))
        delivery = _delivery_payload(t, bus_entry)
        github_status = _normalize_github_status(
            delivery=delivery_source,
            bus_entry=bus_entry,
            github_bus_available=bool(bus_state_ref.get("available")),
        )
        pr_status = _as_mapping(github_status.get("reviewPr"))
        deployment_status = _normalize_deployment_status(delivery_source, bus_entry, pr_status)

        tasks.append(OrchestratorTaskStatus(
            id=task_id,
            title=t.get("title", ""),
            owner=t.get("owner", ""),
            reviewer=t.get("reviewer", ""),
            status=t.get("status", "todo"),
            phase=t.get("phase"),
            next=t.get("next"),
            last_update=t.get("last_update"),
            depends_on=t.get("depends_on", []),
            artifacts=t.get("artifacts", []),
            acceptance=t.get("acceptance", []),
            summary_zh=t.get("summary_zh"),
            waiting_for=t.get("waiting_for"),
            brief_path=brief_path,
            blockers=_task_blockers(blockers, task_id),
            github=github_status,
            deployment=deployment_status,
            delivery=delivery,
        ))

    return OrchestratorStatusResponse(
        snapshotAt=snapshot_at,
        project=ai_status.get("project", "unknown"),
        sprint=ai_status.get("sprint", "unknown"),
        objective=ai_status.get("objective", ""),
        sourceRefs=source_refs,
        tasks=tasks,
        workers=_normalize_workers(state),
        queue=_normalize_queue(state),
        handoffs=_safe(ai_status.get("handoffs", [])),
        blockers=_safe(blockers),
        supervisor=_normalize_supervisor(state),
        providerGuardrails=_normalize_provider_guardrails(state),
        providerReadiness=_normalize_provider_readiness(provider_readiness, snapshot_at),
        assistantDevBridge=_normalize_assistant_dev_bridge(root, state),
        coordination=_normalize_coordination(state),
    )
