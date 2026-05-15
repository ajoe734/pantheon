#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from common import (
    agent_config_for,
    display_name_for,
    execution_context_files,
    new_runtime_id,
    relpath,
    to_bool,
    utc_now,
    write_activity_log,
)
from coordination_repo_mirror import mirror_backend_delivery_bundle, mirror_contract_ready_bundle
from lovable_task_publisher import publish_lovable_task_packet
from multi_repo_registry import (
    coordination_enabled,
    coordination_requests_dir,
    coordination_responses_dir,
    iter_local_repositories,
    matching_repo_id,
    repository_slug,
    resolve_repository,
    worker_route,
)
from runtime_state import enqueue_event

try:
    import yaml
except ImportError:  # pragma: no cover - best effort fallback
    yaml = None


TYPE_TO_LABELS = {
    "bff-gap": ["needs-bff", "blocked"],
    "contract-ready": ["contract-ready", "needs-ui"],
    "lovable-ui-task": ["needs-ui"],
    "ui-done": ["qa-ready"],
    "frontend-feedback": ["feedback-ready"],
    "needs-runtime": ["needs-runtime", "blocked"],
    "needs-engine": ["needs-engine", "blocked"],
    "dispatch-request": [],
}


TYPE_TO_WORKER = {
    "bff-gap": "pantheon-bff-worker",
    "contract-ready": "front-sync-worker",
    "ui-done": "front-sync-worker",
    "needs-runtime": "runtime-worker",
}


ENGINE_MANUAL_TYPES = {"needs-engine"}


def _default_coordination_state() -> dict[str, Any]:
    return {
        "last_scan_at": None,
        "files": {},
        "features": {},
    }


def _coordination_state(state: dict[str, Any]) -> dict[str, Any]:
    coordination = state.setdefault("coordination", {})
    defaults = _default_coordination_state()
    for key, value in defaults.items():
        coordination.setdefault(key, value)
    return coordination


def _load_yaml(path: Path) -> dict[str, Any] | None:
    if yaml is None:
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _event_message(worker_kind: str, feature_id: str, payload: dict[str, Any], source_path: str | None) -> str:
    summary = payload.get("summary") or payload.get("reason") or payload.get("type") or "coordination update"
    source_repo = payload.get("source_repo") or payload.get("source_repo_id") or "unknown"
    lines = [
        "You were dispatched through the Pantheon coordination bus.",
        f"Worker kind: {worker_kind}",
        f"Feature ID: {feature_id}",
        f"Payload type: {payload.get('type') or 'unknown'}",
        f"Source repo: {source_repo}",
        f"Summary: {summary}",
    ]
    if source_path:
        lines.append(f"Coordination file: {source_path}")
    lines.extend(
        [
            "Rules:",
            "- follow the coordination payload and canonical Pantheon docs",
            "- do not invent new endpoints or shadow state",
            "- if you need another repo or runtime layer, emit the next coordination handoff instead of guessing",
            "",
            "Payload:",
            json.dumps(payload, indent=2, ensure_ascii=False),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _target_repo_ids(config: dict[str, Any], repo_id: str, payload: dict[str, Any]) -> list[str]:
    current_type = str(payload.get("type") or "").strip()
    source_repo_id = matching_repo_id(config, str(payload.get("source_repo") or "").strip()) or repo_id
    target_repo_id = matching_repo_id(config, str(payload.get("target_repo") or "").strip())

    if current_type == "bff-gap":
        return [source_repo_id, "pantheon"]
    if current_type == "ui-done":
        return [source_repo_id, "pantheon"]
    if current_type in {"contract-ready", "lovable-ui-task"}:
        return ["pantheon", target_repo_id or "front_ai_trading_system"]
    if current_type == "needs-runtime":
        return [source_repo_id, "runtime_platform"]
    if current_type == "needs-engine":
        return [source_repo_id, "lean_engine"]
    return [source_repo_id]


def _route_payload(config: dict[str, Any], payload: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    current_type = str(payload.get("type") or "").strip()
    worker_kind = None
    if current_type == "dispatch-request":
        worker_kind = str(payload.get("worker_kind") or "").strip() or None
    else:
        worker_kind = TYPE_TO_WORKER.get(current_type)
    return worker_kind, worker_route(config, worker_kind)


def _payload_is_resolved(payload: dict[str, Any]) -> bool:
    if payload.get("resolved_at"):
        return True
    status = str(payload.get("status") or "").strip().lower()
    return status in {"resolved", "completed", "done"}


def queue_coordination_dispatch(
    config: dict[str, Any],
    *,
    worker_kind: str,
    feature_id: str,
    payload: dict[str, Any],
    source_path: str | None,
    reason: str,
) -> bool:
    route = worker_route(config, worker_kind)
    if not route:
        write_activity_log(
            config,
            {
                "type": "coordination_dispatch_skipped",
                "task_id": feature_id,
                "message": f"No worker route configured for {worker_kind}.",
            },
        )
        return False
    if route.get("requires_human_approval") and not payload.get("human_approved"):
        write_activity_log(
            config,
            {
                "type": "coordination_dispatch_waiting_human",
                "task_id": feature_id,
                "message": f"{worker_kind} requires human approval before dispatch.",
            },
        )
        return False

    target_agent = str(route.get("target_agent") or "").strip()
    if not target_agent:
        return False

    agent = agent_config_for(config, target_agent)
    context_files = execution_context_files(config, feature_id)
    if source_path and source_path not in context_files:
        context_files.append(source_path)

    payload_path_list = [source_path] if source_path else []
    queue_payload = {
        "event_id": new_runtime_id("coord"),
        "created_at": utc_now(),
        "event_key": f"coordination:{worker_kind}:{feature_id}:{reason}:{payload.get('dispatch_nonce') or utc_now()}",
        "task_id": feature_id,
        "target_agent": agent["id"],
        "target_display_name": display_name_for(config, agent["id"]),
        "provider": agent.get("provider", agent["id"]),
        "reason": f"coordination:{reason}",
        "message": _event_message(worker_kind, feature_id, payload, source_path),
        "context_files": context_files,
        "target_files": payload_path_list,
        "metadata": {
            "coordination": {
                "feature_id": feature_id,
                "worker_kind": worker_kind,
                "payload_type": payload.get("type"),
                "source_path": source_path,
                "payload": payload,
            },
            "task": {
                "id": feature_id,
                "artifacts": payload_path_list,
                "next": payload.get("summary") or payload.get("reason") or payload.get("type"),
            },
        },
    }
    enqueue_event(config, queue_payload)
    write_activity_log(
        config,
        {
            "type": "coordination_dispatch_queued",
            "task_id": feature_id,
            "target_agent": display_name_for(config, agent["id"]),
            "message": f"Queued {worker_kind} from coordination payload `{payload.get('type')}`.",
            "queue_event_id": queue_payload["event_id"],
        },
    )
    return True


def _record_feature(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    repo_id: str,
    path: Path,
    payload: dict[str, Any],
    payload_digest: str,
) -> dict[str, Any] | None:
    feature_id = str(payload.get("feature_id") or "").strip()
    if not feature_id:
        write_activity_log(
            config,
            {
                "type": "coordination_payload_ignored",
                "message": f"Ignored coordination payload without feature_id: {relpath(path)}",
            },
        )
        return None

    coordination = _coordination_state(state)
    features = coordination.setdefault("features", {})
    record = features.setdefault(feature_id, {"feature_id": feature_id})
    current_type = str(payload.get("type") or "").strip()
    worker_kind, route = _route_payload(config, payload)
    target_agent = str((route or {}).get("target_agent") or "").strip() or None
    source_repo_id = matching_repo_id(config, str(payload.get("source_repo") or "").strip()) or repo_id
    target_repo_id = matching_repo_id(config, str(payload.get("target_repo") or "").strip())
    latest_path = relpath(path)
    entry_updated_at = payload.get("updated_at") or payload.get("created_at") or utc_now()

    record.update(
        {
            "feature_id": feature_id,
            "status": current_type if current_type else record.get("status"),
            "summary": payload.get("summary") or payload.get("reason") or current_type,
            "current_payload_type": current_type,
            "source_repo_id": source_repo_id,
            "source_repo": payload.get("source_repo") or repository_slug(config, source_repo_id) or source_repo_id,
            "target_repo_id": target_repo_id,
            "source_branch": payload.get("source_branch"),
            "screen": payload.get("screen"),
            "latest_path": latest_path,
            "latest_payload_digest": payload_digest,
            "latest_payload": payload,
            "last_updated_at": entry_updated_at,
            "state_labels": TYPE_TO_LABELS.get(current_type, []),
            "worker_kind": worker_kind or record.get("worker_kind"),
            "target_agent": target_agent or record.get("target_agent"),
            "issue_repo_ids": _target_repo_ids(config, repo_id, payload),
            "next_step": payload.get("next_step") or payload.get("summary") or current_type,
        }
    )

    snapshot = {
        "type": current_type,
        "path": latest_path,
        "payload": payload,
        "updated_at": entry_updated_at,
        "source_repo_id": source_repo_id,
        "target_repo_id": target_repo_id,
    }

    if "/requests/" in latest_path.replace("\\", "/"):
        record["latest_request"] = payload
        record["latest_request_path"] = latest_path
        requests_by_type = record.setdefault("requests_by_type", {})
        requests_by_type[current_type] = snapshot
    if "/responses/" in latest_path.replace("\\", "/"):
        record["latest_response"] = payload
        record["latest_response_path"] = latest_path
        responses_by_type = record.setdefault("responses_by_type", {})
        responses_by_type[current_type] = snapshot
    return record


def sync_coordination_files(config: dict[str, Any], state: dict[str, Any]) -> bool:
    if not coordination_enabled(config):
        return False

    coordination = _coordination_state(state)
    files_state = coordination.setdefault("files", {})
    changed = False

    for repo in iter_local_repositories(config):
        repo_id = repo["id"]
        for directory in filter(None, [coordination_requests_dir(config, repo_id), coordination_responses_dir(config, repo_id)]):
            if directory is None or not directory.exists():
                continue
            for path in sorted(directory.glob("*.yaml")):
                if path.name.endswith(".example.yaml"):
                    continue
                digest = _file_digest(path)
                key = f"{repo_id}:{path}"
                if files_state.get(key, {}).get("digest") == digest:
                    continue

                payload = _load_yaml(path)
                if payload is None:
                    write_activity_log(
                        config,
                        {
                            "type": "coordination_payload_invalid",
                            "message": f"Failed to parse coordination YAML: {relpath(path)}",
                        },
                    )
                    files_state[key] = {"digest": digest, "path": relpath(path), "invalid": True}
                    changed = True
                    continue

                record = _record_feature(config, state, repo_id=repo_id, path=path, payload=payload, payload_digest=digest)
                files_state[key] = {
                    "digest": digest,
                    "path": relpath(path),
                    "feature_id": payload.get("feature_id"),
                    "type": payload.get("type"),
                }
                changed = True

                if not record:
                    continue

                current_type = str(payload.get("type") or "").strip()
                mirror_only = to_bool(payload.get("mirror_only"))
                if current_type == "contract-ready":
                    published = publish_lovable_task_packet(config, payload) if not mirror_only else None
                    if published:
                        record["lovable_task"] = published.get("payload")
                        record["lovable_task_path"] = published.get("packet_path")
                        record["lovable_prompt_path"] = published.get("prompt_path")
                        responses_by_type = record.setdefault("responses_by_type", {})
                        responses_by_type["lovable-ui-task"] = {
                            "type": "lovable-ui-task",
                            "path": relpath(Path(str(published.get("packet_path") or ""))) if published.get("packet_path") else None,
                            "payload": published.get("payload"),
                            "updated_at": utc_now(),
                            "source_repo_id": record.get("source_repo_id"),
                            "target_repo_id": record.get("target_repo_id"),
                        }
                        write_activity_log(
                            config,
                            {
                                "type": "lovable_task_packet_published",
                                "task_id": record["feature_id"],
                                "message": f"Published Lovable task packet for {record['feature_id']}.",
                            },
                        )
                    mirrored = mirror_contract_ready_bundle(config, payload, published) if not mirror_only else None
                    if mirrored:
                        record["mirrored_to_target_repo"] = mirrored
                        write_activity_log(
                            config,
                            {
                                "type": "coordination_repo_mirror_synced",
                                "task_id": record["feature_id"],
                                "message": f"Mirrored contract-ready bundle into {mirrored['target_repo_id']}.",
                            },
                        )
                elif current_type == "backend-delivery":
                    mirrored = mirror_backend_delivery_bundle(config, payload) if not mirror_only else None
                    if mirrored:
                        record["mirrored_to_target_repo"] = mirrored
                        write_activity_log(
                            config,
                            {
                                "type": "coordination_repo_mirror_synced",
                                "task_id": record["feature_id"],
                                "message": f"Mirrored backend-delivery bundle into {mirrored['target_repo_id']}.",
                            },
                        )

                worker_kind, route = _route_payload(config, payload)
                if not worker_kind or not route:
                    continue
                if mirror_only:
                    continue
                if _payload_is_resolved(payload):
                    continue
                if current_type in ENGINE_MANUAL_TYPES and not payload.get("human_approved"):
                    continue

                queued = queue_coordination_dispatch(
                    config,
                    worker_kind=worker_kind,
                    feature_id=record["feature_id"],
                    payload=payload,
                    source_path=relpath(path),
                    reason=current_type,
                )
                if queued:
                    record["last_dispatched_at"] = utc_now()

    coordination["last_scan_at"] = utc_now()
    return changed
