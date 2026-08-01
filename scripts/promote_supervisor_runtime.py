#!/usr/bin/env python3
"""Supervisor runtime promotion snapshot & invariant validation module.

Provides read-only snapshot collection and runtime promotion invariant checks.
Does not perform process termination, launch, rollback, or live promotion.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supervisor_runtime_health import (
    evaluate_runtime_health,
    pid_is_alive,
    resolved_coordinator_status_root,
    parse_utc_timestamp,
    lock_held,
)


def load_json_strict(path: Path) -> dict[str, Any]:
    """Load JSON from path, failing closed (raising exception/returning error envelope)."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    content = path.read_text(encoding="utf-8")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"JSON at {path} must be a dictionary object, got {type(data).__name__}")
    return data


def capture_promotion_snapshot(
    repo_root: Path,
    *,
    config_path_arg: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Capture live-schema supervisor runtime state and evaluate promotion invariants."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config_path_resolved = config_path_arg or (repo_root / ".orchestrator" / "config.json")

    file_errors: list[dict[str, str]] = []

    try:
        config = load_json_strict(config_path_resolved)
    except Exception as e:
        config = {}
        file_errors.append({"file": str(config_path_resolved), "error": str(e)})

    health_report = evaluate_runtime_health(
        repo_root,
        config_path_arg=config_path_resolved,
        now=now,
    )

    paths = config.get("paths") if isinstance(config.get("paths"), dict) else None
    if paths is None or "status_file" not in paths:
        file_errors.append({
            "file": "config.json:paths.status_file",
            "error": "Missing required paths.status_file in config",
        })
        status_path = repo_root / "ai-status.json"
    else:
        status_file_raw = str(paths["status_file"])
        status_path = Path(status_file_raw)
        if not status_path.is_absolute():
            status_path = repo_root / status_path

    try:
        ai_status = load_json_strict(status_path)
    except Exception as e:
        ai_status = {}
        file_errors.append({"file": str(status_path), "error": str(e)})

    if paths is None or "state_file" not in paths:
        file_errors.append({
            "file": "config.json:paths.state_file",
            "error": "Missing required paths.state_file in config",
        })
        state_path = repo_root / ".orchestrator" / "state.json"
    else:
        state_file_raw = str(paths["state_file"])
        state_path = Path(state_file_raw)
        if not state_path.is_absolute():
            state_path = repo_root / state_path

    try:
        state = load_json_strict(state_path)
    except Exception as e:
        state = {}
        file_errors.append({"file": str(state_path), "error": str(e)})

    provider_cap_path = None
    provider_capabilities: dict[str, Any] = {}
    if paths is not None and "provider_capabilities" in paths:
        raw_cap = str(paths["provider_capabilities"])
        p_path = Path(raw_cap)
        if not p_path.is_absolute():
            p_path = repo_root / p_path
        provider_cap_path = p_path
    else:
        file_errors.append({
            "file": "config.json:paths.provider_capabilities",
            "error": "Missing required paths.provider_capabilities in config",
        })
        provider_cap_path = repo_root / ".orchestrator" / "provider_capabilities.json"

    try:
        provider_capabilities = load_json_strict(provider_cap_path)
    except Exception as e:
        provider_capabilities = {}
        file_errors.append({"file": str(provider_cap_path), "error": str(e)})

    coord_root = resolved_coordinator_status_root(repo_root, config)
    lock_path = coord_root / ".orchestrator" / "supervisor.lock"

    # Evaluate promotion invariants
    invariants = evaluate_promotion_invariants(
        health_report=health_report,
        ai_status=ai_status,
        state=state,
        provider_capabilities=provider_capabilities,
        lock_path=lock_path,
        file_errors=file_errors,
        now=now,
        config=config,
    )

    all_invariants_pass = all(inv["ok"] for inv in invariants)

    return {
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "repo_root": str(repo_root),
        "config_path": str(config_path_resolved),
        "health_report": health_report,
        "ai_status_summary": {
            "sprint": ai_status.get("sprint"),
            "updated_at": ai_status.get("updated_at"),
            "tasks_count": len(ai_status.get("tasks", [])) if isinstance(ai_status.get("tasks"), list) else 0,
            "agents_count": len(ai_status.get("agents", [])) if isinstance(ai_status.get("agents"), list) else 0,
        },
        "supervisor_state_summary": {
            "lifecycle": state.get("supervisor", {}).get("lifecycle") if isinstance(state.get("supervisor"), dict) else None,
            "last_heartbeat_at": state.get("supervisor", {}).get("last_heartbeat_at") if isinstance(state.get("supervisor"), dict) else None,
        },
        "file_errors": file_errors,
        "invariants": invariants,
        "eligible_for_promotion": all_invariants_pass,
    }


def evaluate_promotion_invariants(
    health_report: dict[str, Any],
    ai_status: dict[str, Any],
    state: dict[str, Any],
    provider_capabilities: dict[str, Any] | None = None,
    lock_path: Path | None = None,
    file_errors: list[dict[str, str]] | None = None,
    now: datetime | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate read-only promotion invariants against live schema state."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config = config or {}
    file_errors = file_errors or []
    provider_capabilities = provider_capabilities or {}
    invariants: list[dict[str, Any]] = []

    # Invariant 0: Fail closed file reading invariant
    invariants.append({
        "name": "config_and_state_files_readable",
        "ok": len(file_errors) == 0,
        "details": {"file_errors": file_errors},
    })

    # Invariant 1: Health checks must all pass
    health_ok = health_report.get("healthy", False)
    invariants.append({
        "name": "runtime_health_clean",
        "ok": health_ok,
        "details": {"healthy": health_ok, "failed_checks": [c["name"] for c in health_report.get("checks", []) if not c.get("ok")]},
    })

    # Invariant 2: Supervisor lifecycle must be explicitly valid (e.g., 'running') and not None or 'degraded'
    supervisor_info = health_report.get("supervisor", {})
    lifecycle = supervisor_info.get("lifecycle")
    valid_lifecycles = {"running", "idle", "active"}
    lifecycle_ok = isinstance(lifecycle, str) and lifecycle in valid_lifecycles
    invariants.append({
        "name": "supervisor_lifecycle_valid",
        "ok": lifecycle_ok,
        "details": {"lifecycle": lifecycle, "valid_lifecycles": list(valid_lifecycles)},
    })

    # Invariant 3: Supervisor PID binding AND lock_path validation (must require PID alive AND lock held when running)
    pid = supervisor_info.get("pid")
    is_alive = pid_is_alive(pid) if pid is not None else False
    is_lock_held = lock_held(lock_path) if lock_path else False
    supervisor_bound = (pid is not None and is_alive) and is_lock_held
    invariants.append({
        "name": "supervisor_pid_bound_and_locked",
        "ok": supervisor_bound,
        "details": {"pid": pid, "pid_alive": is_alive, "lock_held": is_lock_held, "lock_path": str(lock_path) if lock_path else None},
    })

    # Invariant 4: ai-status.json must be a valid dict with tasks list
    has_valid_status = isinstance(ai_status, dict) and "tasks" in ai_status and isinstance(ai_status["tasks"], list)
    invariants.append({
        "name": "ai_status_schema_valid",
        "ok": has_valid_status,
        "details": {"is_dict": isinstance(ai_status, dict), "has_tasks": "tasks" in ai_status if isinstance(ai_status, dict) else False},
    })

    # Invariant 5: Task state shadow authoritative / ok / caught_up validation
    # Live schema requires task_state_shadow to exist and have:
    # mode == "authoritative", ok is True, caught_up is True, last_error is None,
    # and projected_state_sha256 == expected_state_sha256 (if hash keys present/populated)
    shadow = supervisor_info.get("task_state_shadow")
    if not isinstance(shadow, dict):
        shadow = state.get("supervisor", {}).get("task_state_shadow") if isinstance(state.get("supervisor"), dict) else None

    shadow_ok = False
    shadow_reasons: list[str] = []
    if not isinstance(shadow, dict) or not shadow:
        shadow_reasons.append("task_state_shadow_missing")
    else:
        if shadow.get("mode") != "authoritative":
            shadow_reasons.append(f"mode_not_authoritative:{shadow.get('mode')}")
        if shadow.get("ok") is not True:
            shadow_reasons.append(f"ok_not_true:{shadow.get('ok')}")
        if shadow.get("caught_up") is not True:
            shadow_reasons.append(f"caught_up_not_true:{shadow.get('caught_up')}")
        if shadow.get("last_error") is not None:
            shadow_reasons.append(f"has_last_error:{shadow.get('last_error')}")
        proj_sha = shadow.get("projected_state_sha256")
        exp_sha = shadow.get("expected_state_sha256")
        if not proj_sha or not isinstance(proj_sha, str) or not proj_sha.strip():
            shadow_reasons.append("missing_projected_state_sha256")
        if not exp_sha or not isinstance(exp_sha, str) or not exp_sha.strip():
            shadow_reasons.append("missing_expected_state_sha256")
        if proj_sha and exp_sha and proj_sha != exp_sha:
            shadow_reasons.append(f"sha_mismatch:{proj_sha}!={exp_sha}")

    shadow_ok = len(shadow_reasons) == 0
    invariants.append({
        "name": "task_state_shadow_valid",
        "ok": shadow_ok,
        "details": {"task_state_shadow": shadow, "reasons": shadow_reasons},
    })

    # Invariant 6: Fresh-loop sequence or staleness check
    # Requires last_successful_loop_at, last_loop_started_at, last_loop_finished_at, last_loop_error == None,
    # and bounded freshness (last_successful_loop_at age <= stall_after_seconds)
    supervisor_state = state.get("supervisor", {}) if isinstance(state.get("supervisor"), dict) else {}
    last_successful_loop_raw = supervisor_state.get("last_successful_loop_at")
    last_started_raw = supervisor_state.get("last_loop_started_at")
    last_finished_raw = supervisor_state.get("last_loop_finished_at")
    last_error = supervisor_state.get("last_loop_error")

    last_successful_loop_at = parse_utc_timestamp(last_successful_loop_raw)
    last_started_at = parse_utc_timestamp(last_started_raw)
    last_finished_at = parse_utc_timestamp(last_finished_raw)

    max_stall = float(config.get("supervisor", {}).get("stall_after_seconds", 900))
    loop_reasons: list[str] = []

    if last_successful_loop_at is None:
        loop_reasons.append("missing_last_successful_loop_at")
    if last_started_at is None:
        loop_reasons.append("missing_last_loop_started_at")
    if last_finished_at is None:
        loop_reasons.append("missing_last_loop_finished_at")
    if last_error is not None:
        loop_reasons.append(f"has_last_loop_error:{last_error}")

    if last_successful_loop_at is not None:
        loop_age = (now - last_successful_loop_at).total_seconds()
        if loop_age > max_stall or loop_age < 0:
            loop_reasons.append(f"loop_stale_or_future:age={loop_age},max={max_stall}")

    loop_fresh = len(loop_reasons) == 0
    invariants.append({
        "name": "fresh_loop_sequence",
        "ok": loop_fresh,
        "details": {
            "last_successful_loop_at": last_successful_loop_at.isoformat() if last_successful_loop_at else None,
            "max_stall": max_stall,
            "reasons": loop_reasons,
        },
    })
    # Invariant 7: state.workers, state.queue object ("events"), worker_worktrees.leases parity
    workers = state.get("workers", {}) if isinstance(state.get("workers"), dict) else {}
    queue_obj = state.get("queue", {}) if isinstance(state.get("queue"), dict) else {}
    queue_events = queue_obj.get("events", {}) if isinstance(queue_obj.get("events"), dict) else {}
    worker_worktrees = state.get("worker_worktrees", {}) if isinstance(state.get("worker_worktrees"), dict) else {}
    leases = worker_worktrees.get("leases", {}) if isinstance(worker_worktrees.get("leases"), dict) else {}

    active_worker_tasks: set[str] = set()
    duplicate_workers: list[str] = []
    parity_reasons: list[str] = []

    # Map active queue events by task_id and event_id
    active_queue_events_by_id: dict[str, dict[str, Any]] = {}
    active_queue_events_by_task: dict[str, list[dict[str, Any]]] = {}

    for evt_id, evt_info in queue_events.items():
        if isinstance(evt_info, dict):
            q_status = evt_info.get("status") or evt_info.get("state")
            if q_status not in ("completed", "failed", "cancelled", "done"):
                actual_id = evt_info.get("id") or evt_id
                active_queue_events_by_id[actual_id] = evt_info
                q_task = evt_info.get("task_id")
                if q_task:
                    active_queue_events_by_task.setdefault(q_task, []).append(evt_info)

    # Helper for canonical worker run_id: nonempty w_info.get("run_id") or w_name
    def get_canonical_run_id(w_name: str, w_info: dict[str, Any]) -> str:
        r_id = w_info.get("run_id")
        if isinstance(r_id, str) and r_id.strip():
            return r_id.strip()
        return w_name

    # Build mapping of canonical_run_id -> (w_name, w_info) across ALL workers (for historical resolution)
    # Detect duplicate canonical run identities and fail closed if active event or lineage touches one.
    workers_by_run_id: dict[str, tuple[str, dict[str, Any]]] = {}
    duplicate_canonical_run_ids: set[str] = set()

    for w_name, w_info in workers.items():
        if isinstance(w_info, dict):
            c_run_id = get_canonical_run_id(w_name, w_info)
            if c_run_id in workers_by_run_id:
                duplicate_canonical_run_ids.add(c_run_id)
            else:
                workers_by_run_id[c_run_id] = (w_name, w_info)

    # Validate active workers
    for w_name, w_info in workers.items():
        if isinstance(w_info, dict):
            task_id = w_info.get("current_task_id") or w_info.get("task_id")
            w_status = w_info.get("status")
            if w_status in ("running", "started", "active") and task_id:
                if task_id in active_worker_tasks:
                    duplicate_workers.append(f"{w_name}:{task_id}")
                else:
                    active_worker_tasks.add(task_id)

                w_q_evt_id = w_info.get("queue_event_id")
                w_canonical_run_id = get_canonical_run_id(w_name, w_info)

                # Verify lease parity: active task_id must exist in leases and match worker queue_event_id/run_id if specified
                matching_lease = None
                matching_lease_id = None
                for lease_id, lease_info in leases.items():
                    if isinstance(lease_info, dict) and lease_info.get("task_id") == task_id:
                        matching_lease = lease_info
                        matching_lease_id = lease_id
                        break

                if matching_lease is None:
                    parity_reasons.append(f"active_worker_missing_lease:{w_name}:{task_id}")
                else:
                    l_q_evt_id = matching_lease.get("last_queue_event_id") or matching_lease.get("queue_event_id")
                    l_run_id = matching_lease.get("run_id")
                    if w_q_evt_id and l_q_evt_id and w_q_evt_id != l_q_evt_id:
                        parity_reasons.append(f"mismatched_lease_queue_event_id:{matching_lease_id}:{l_q_evt_id}!={w_q_evt_id}")
                    if l_run_id and w_canonical_run_id != l_run_id:
                        parity_reasons.append(f"mismatched_lease_run_id:{matching_lease_id}:{l_run_id}!={w_canonical_run_id}")

                if w_q_evt_id and w_q_evt_id in active_queue_events_by_id:
                    q_evt = active_queue_events_by_id[w_q_evt_id]
                    q_worker = q_evt.get("worker") or q_evt.get("assigned_worker")
                    if q_worker and q_worker != w_name:
                        parity_reasons.append(f"mismatched_queue_event_worker:{w_q_evt_id}:{q_worker}!={w_name}")
                elif task_id in active_queue_events_by_task:
                    q_evts = active_queue_events_by_task[task_id]
                    matched_evt = False
                    for q_evt in q_evts:
                        q_evt_id = q_evt.get("id")
                        if w_q_evt_id and q_evt_id == w_q_evt_id:
                            matched_evt = True
                            break
                        elif not w_q_evt_id:
                            matched_evt = True
                            break
                    if w_q_evt_id and not matched_evt:
                        first_q_id = q_evts[0].get("id") if q_evts else "unknown"
                        parity_reasons.append(f"mismatched_worker_queue_event_id:{w_name}:{w_q_evt_id}!={first_q_id}")

    if len(duplicate_workers) > 0:
        parity_reasons.append(f"duplicate_active_workers:{duplicate_workers}")

    # Helper function to trace parent_run_id lineage through actual worker records
    def trace_retry_lineage(start_run_id: str, target_run_id: str, expected_task: str, expected_queue_evt_id: str) -> str | None:
        """Trace start_run_id -> target_run_id along parent_run_id links.

        Returns None if valid, or error reason string if invalid.
        """
        curr = start_run_id
        visited: set[str] = set()

        while curr:
            if curr in visited:
                return f"cycle_in_retry_lineage:{start_run_id}"
            visited.add(curr)

            if curr in duplicate_canonical_run_ids:
                return f"duplicate_canonical_run_id:{curr}"

            if curr not in workers_by_run_id:
                return f"missing_history:{start_run_id}->{curr}"

            _, node_info = workers_by_run_id[curr]
            node_task = node_info.get("current_task_id") or node_info.get("task_id")
            node_q_evt = node_info.get("queue_event_id")

            if expected_task and node_task and node_task != expected_task:
                return f"cross_task_retry_lineage:{start_run_id}:{node_task}!={expected_task}"
            if expected_queue_evt_id and node_q_evt and node_q_evt != expected_queue_evt_id:
                return f"cross_event_retry_lineage:{start_run_id}:{node_q_evt}!={expected_queue_evt_id}"

            if curr == target_run_id:
                return None  # Reached target event.run_id successfully and verified task/queue_event!

            parent = node_info.get("parent_run_id")
            if not parent or not isinstance(parent, str) or not parent.strip():
                return f"broken_worker_retry_lineage:{start_run_id}:{curr}!={target_run_id}"

            curr = parent.strip()

        return f"broken_worker_retry_lineage:{start_run_id}:did_not_reach_{target_run_id}"

    # Reverse-link validation: exactly one active worker for each active event matching queue_event_id and task_id
    for evt_id, evt_info in active_queue_events_by_id.items():
        q_task = evt_info.get("task_id")
        q_run_id = evt_info.get("run_id")
        q_lease_owner = evt_info.get("lease_owner")

        # Active started event requires nonempty lease_owner
        if not q_lease_owner or not isinstance(q_lease_owner, str) or not q_lease_owner.strip():
            parity_reasons.append(f"missing_lease_owner:{evt_id}")
            continue

        q_lease_owner = q_lease_owner.strip()

        # Find active workers reverse-linked to this event (matching canonical run_id == lease_owner)
        matched_workers: list[tuple[str, dict[str, Any]]] = []
        for w_name, w_info in workers.items():
            if isinstance(w_info, dict) and w_info.get("status") in ("running", "started", "active"):
                c_run_id = get_canonical_run_id(w_name, w_info)
                if c_run_id == q_lease_owner or w_info.get("queue_event_id") == evt_id:
                    matched_workers.append((w_name, w_info))

        if len(matched_workers) == 0:
            parity_reasons.append(f"active_queue_event_missing_worker:{evt_id}:{q_task}")
        elif len(matched_workers) > 1:
            m_names = [mw[0] for mw in matched_workers]
            parity_reasons.append(f"active_queue_event_multiple_workers:{evt_id}:{m_names}")
        else:
            w_name, w_info = matched_workers[0]
            w_task = w_info.get("current_task_id") or w_info.get("task_id")
            w_canonical_run_id = get_canonical_run_id(w_name, w_info)
            w_q_evt_id = w_info.get("queue_event_id")

            if w_task and q_task and w_task != q_task:
                parity_reasons.append(f"mismatched_queue_event_worker_task:{evt_id}:{w_name}:{w_task}!={q_task}")
            if w_q_evt_id and w_q_evt_id != evt_id:
                parity_reasons.append(f"mismatched_queue_event_worker_id:{evt_id}:{w_name}:{w_q_evt_id}!={evt_id}")

            # Exactly one active reverse-linked canonical run must equal lease_owner
            if w_canonical_run_id != q_lease_owner:
                parity_reasons.append(f"mismatched_worker_lease_owner:{w_name}:{w_canonical_run_id}!={q_lease_owner}")

            # Verify event.run_id resolves to an actual worker record
            if not q_run_id or not isinstance(q_run_id, str) or q_run_id.strip() not in workers_by_run_id:
                parity_reasons.append(f"missing_history:{evt_id}:run_id_{q_run_id}_not_found")
            else:
                q_run_id = q_run_id.strip()
                # Verify initial run or trace parent_run_id retry lineage
                if w_canonical_run_id != q_run_id:
                    err = trace_retry_lineage(
                        start_run_id=w_canonical_run_id,
                        target_run_id=q_run_id,
                        expected_task=q_task or "",
                        expected_queue_evt_id=evt_id,
                    )
                    if err:
                        parity_reasons.append(err)

    # Check for orphan active leases (leases with status active/running/started that have no active worker or active queue event)
    for lease_id, lease_info in leases.items():
        if not isinstance(lease_info, dict):
            continue
        l_status = lease_info.get("status") or lease_info.get("state")
        l_task = lease_info.get("task_id") or lease_id
        is_explicitly_active = l_status in ("active", "running", "started")
        if is_explicitly_active:
            if l_task not in active_worker_tasks and l_task not in active_queue_events_by_task:
                parity_reasons.append(f"orphan_active_lease:{l_task}")

    lease_parity_ok = len(parity_reasons) == 0
    invariants.append({
        "name": "worker_lease_parity_and_no_duplicates",
        "ok": lease_parity_ok,
        "details": {
            "workers_count": len(workers),
            "queue_events_count": len(queue_events),
            "leases_count": len(leases),
            "duplicate_active_workers": duplicate_workers,
            "reasons": parity_reasons,
        },
    })

    # Invariant 8: Provider readiness baseline comparing provider_capabilities against configured active providers
    provider_reasons: list[str] = []
    baseline_capabilities: dict[str, Any] = {}

    cap_providers = provider_capabilities.get("providers") if isinstance(provider_capabilities.get("providers"), dict) else {}
    configured_providers = config.get("providers", {}) if isinstance(config.get("providers"), dict) else {}

    if not cap_providers:
        provider_reasons.append("no_provider_capabilities_loaded")

    # Determine required active provider types ONLY from active workers or active queue events (or leases bound to an active worker/queue event)
    active_providers_required: set[str] = set()
    for w_name, w_info in workers.items():
        if isinstance(w_info, dict) and w_info.get("status") in ("running", "started", "active"):
            provider_type = w_info.get("provider") or w_info.get("type")
            if provider_type and isinstance(provider_type, str):
                active_providers_required.add(provider_type.lower())

    for lease_id, lease_info in leases.items():
        if isinstance(lease_info, dict):
            l_task = lease_info.get("task_id")
            l_status = lease_info.get("status") or lease_info.get("state")
            is_active_ref = (l_task in active_worker_tasks) or (l_task in active_queue_events_by_task) or (l_status in ("active", "running", "started"))
            if is_active_ref:
                provider_type = lease_info.get("provider") or lease_info.get("type")
                if provider_type and isinstance(provider_type, str):
                    active_providers_required.add(provider_type.lower())

    # Build readiness baseline for all configured providers
    for p_id, p_config in configured_providers.items():
        if not isinstance(p_config, dict):
            continue
        is_enabled = p_config.get("enabled", True)
        p_id_lower = p_id.lower()
        p_cap = cap_providers.get(p_id)
        baseline_entry = {
            "enabled": is_enabled,
            "required": p_id_lower in active_providers_required,
            "auth_ready": p_cap.get("auth_ready") if isinstance(p_cap, dict) else None,
            "local_worker_ready": p_cap.get("local_cli_worker_supported") if isinstance(p_cap, dict) else None,
        }
        baseline_capabilities[p_id] = baseline_entry

        # Readiness is only required for active providers in use
        if is_enabled and p_id_lower in active_providers_required:
            if not isinstance(p_cap, dict):
                provider_reasons.append(f"missing_provider_capability:{p_id}")
            else:
                if p_cap.get("auth_ready") is not True:
                    provider_reasons.append(f"provider_auth_not_ready:{p_id}")
                if p_cap.get("local_cli_worker_supported") is not True:
                    provider_reasons.append(f"provider_local_worker_not_ready:{p_id}")

    provider_readiness_ok = len(provider_reasons) == 0
    invariants.append({
        "name": "provider_readiness_baseline",
        "ok": provider_readiness_ok,
        "details": {
            "reasons": provider_reasons,
            "active_providers_required": sorted(list(active_providers_required)),
            "baseline_capabilities": baseline_capabilities,
        },
    })

    # Invariant 9: No orphaned in_progress tasks without owner
    tasks = ai_status.get("tasks", []) if isinstance(ai_status, dict) else []
    orphaned_tasks = []
    if isinstance(tasks, list):
        for task in tasks:
            if isinstance(task, dict) and task.get("status") == "in_progress":
                if not task.get("owner"):
                    orphaned_tasks.append(task.get("id"))
    invariants.append({
        "name": "no_orphaned_in_progress_tasks",
        "ok": len(orphaned_tasks) == 0,
        "details": {"orphaned_tasks": orphaned_tasks},
    })

    return invariants


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture supervisor promotion snapshot & check invariants.")
    parser.add_argument("--repo", default=".", help="Pantheon repository root. Defaults to cwd.")
    parser.add_argument("--config-path", default=None, help="Path to .orchestrator/config.json.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON snapshot.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo).expanduser().resolve()
    config_path = Path(args.config_path).expanduser().resolve() if args.config_path else None

    snapshot = capture_promotion_snapshot(repo_root, config_path_arg=config_path)

    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        status_str = "ELIGIBLE" if snapshot["eligible_for_promotion"] else "INELIGIBLE"
        print(f"supervisor_promotion_snapshot={status_str} timestamp={snapshot['timestamp']}")
        for inv in snapshot["invariants"]:
            print(f"invariant {inv['name']}: {'ok' if inv['ok'] else 'FAIL'}")

    return 0 if snapshot["eligible_for_promotion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
