#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import (
    approval_tool_input_preview,
    approval_tool_input_signature,
    load_config,
    new_runtime_id,
    utc_now,
    write_activity_log,
    write_approval_evidence,
)
from runtime_state import (
    load_approval_state,
    load_runtime_state,
    runtime_state_lock,
    save_approval_state,
)


@contextmanager
def approval_lock(config: dict[str, Any]):
    """Compatibility name for the shared runtime-admission transaction."""

    with runtime_state_lock(config, shared=False, nonblocking=False):
        yield


def list_pending(config: dict[str, Any], include_history: bool = False) -> dict[str, Any]:
    state = load_approval_state(config)
    payload = {"pending": state.get("pending", [])}
    if include_history:
        payload["history"] = state.get("history", [])
    return payload


def _parse_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stale_pending_seconds(config: dict[str, Any]) -> float:
    return float(config.get("approvals", {}).get("stale_pending_seconds", 1800))


def _is_stale_pending(item: dict[str, Any], *, now: datetime, stale_after_seconds: float) -> bool:
    # Applies to every pending item once it has waited past the configured
    # threshold, including approvals bound to a task/worker. A live worker
    # (even a resumable Claude session) must not be able to suspend forever
    # just because it is still bound to a task; see
    # OPS-APPROVAL-BROKER-RISK-CLASS-001 (2026-07-17 8.5h suspended_approval
    # incident, where every stuck item had a task_id/worker_run_id and was
    # therefore silently excluded from this check).
    if item.get("status") != "pending":
        return False
    created_at = _parse_utc(item.get("created_at"))
    if created_at is None:
        return False
    return (now - created_at).total_seconds() >= stale_after_seconds


def _pid_is_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    return os.path.exists(f"/proc/{value}")


def _provider_uses_claude_cli(config: dict[str, Any], provider_id: str | None) -> bool:
    normalized = str(provider_id or "").strip().lower()
    if not normalized:
        return False
    provider = (config.get("providers", {}) or {}).get(normalized, {}) or {}
    delivery_mode = str(provider.get("delivery_mode") or "").strip()
    if delivery_mode:
        return delivery_mode == "claude_cli"
    return normalized.startswith("claude")


def _orphaned_worker_note(config: dict[str, Any], item: dict[str, Any], workers: dict[str, Any]) -> str | None:
    run_id = item.get("worker_run_id")
    if not run_id:
        return None
    worker = workers.get(run_id)
    if worker is None:
        return "Auto-pruned orphaned approval after its worker state disappeared."
    if (
        _provider_uses_claude_cli(config, worker.get("provider"))
        and worker.get("status") in {"waiting_approval", "suspended_approval"}
        and worker.get("queue_event_id")
    ):
        # The supervisor can return the exact durable intent to the single
        # delivery path after approval, even when the original process exited.
        return None
    if not _pid_is_alive(worker.get("pid")):
        return "Auto-pruned approval because the worker exited before approval could be applied."
    return None


def _pruned_pending_item(item: dict[str, Any], *, note: str) -> dict[str, Any]:
    return {
        **item,
        "status": "resolved",
        "decision": "deny",
        "resolved_at": utc_now(),
        "note": note,
        "remember": False,
        "resume_override_active": False,
        "resume_override_consumed_at": None,
        "resume_override_consumed_reason": None,
    }


def prune_stale_approvals(config: dict[str, Any]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stale_after_seconds = _stale_pending_seconds(config)
    pruned: list[dict[str, Any]] = []
    with approval_lock(config):
        state = load_approval_state(config)
        runtime_state = load_runtime_state(config)
        workers = runtime_state.get("workers", {})
        keep: list[dict[str, Any]] = []
        for item in state.get("pending", []):
            orphaned_note = _orphaned_worker_note(config, item, workers)
            if orphaned_note:
                pruned_item = _pruned_pending_item(item, note=orphaned_note)
                pruned_item["resolution_ref"] = write_approval_evidence(
                    config,
                    approval_id=str(item.get("approval_id") or ""),
                    stage="pruned",
                    payload={
                        "provider": item.get("provider"),
                        "task_id": item.get("task_id"),
                        "worker_run_id": item.get("worker_run_id"),
                        "tool_name": item.get("tool_name"),
                        "decision": "deny",
                        "note": orphaned_note,
                        "request_ref": item.get("evidence_ref"),
                    },
                )
                pruned.append(pruned_item)
                continue
            if _is_stale_pending(item, now=now, stale_after_seconds=stale_after_seconds):
                note = f"Auto-pruned stale approval after {int(stale_after_seconds)}s without a broker decision."
                pruned_item = _pruned_pending_item(item, note=note)
                pruned_item["resolution_ref"] = write_approval_evidence(
                    config,
                    approval_id=str(item.get("approval_id") or ""),
                    stage="pruned",
                    payload={
                        "provider": item.get("provider"),
                        "task_id": item.get("task_id"),
                        "worker_run_id": item.get("worker_run_id"),
                        "tool_name": item.get("tool_name"),
                        "decision": "deny",
                        "note": note,
                        "request_ref": item.get("evidence_ref"),
                    },
                )
                pruned.append(pruned_item)
                continue
            keep.append(item)
        if not pruned:
            return []
        state["pending"] = keep
        state.setdefault("history", []).extend(pruned)
        save_approval_state(config, state)
        for item in pruned:
            write_activity_log(
                config,
                {
                    "type": "approval_pruned",
                    "provider": item.get("provider"),
                    "task_id": item.get("task_id"),
                    "message": f"Auto-pruned stale approval {item.get('approval_id')}",
                    "approval_id": item.get("approval_id"),
                    "worker_run_id": item.get("worker_run_id"),
                    "decision": "deny",
                    "evidence_ref": item.get("resolution_ref") or item.get("evidence_ref"),
                },
            )
    return pruned


def _default_expires_at(config: dict[str, Any], created_at: str) -> str | None:
    created = _parse_utc(created_at)
    if created is None:
        return None
    expires = created + timedelta(seconds=_stale_pending_seconds(config))
    return expires.isoformat().replace("+00:00", "Z")


def validated_approval_binding(item: dict[str, Any]) -> tuple[str, int, str]:
    task_id = str(item.get("task_id") or "").strip()
    worker_run_id = str(item.get("worker_run_id") or "").strip()
    raw_generation = item.get("task_generation")
    try:
        generation = int(raw_generation)
    except (TypeError, ValueError) as exc:
        raise ValueError("approval requires a positive task generation") from exc
    if not task_id:
        raise ValueError("approval requires a task id")
    if generation <= 0:
        raise ValueError("approval requires a positive task generation")
    if not worker_run_id:
        raise ValueError("approval requires a worker run id")
    return task_id, generation, worker_run_id


def create_approval(config: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    task_id, task_generation, worker_run_id = validated_approval_binding(item)
    item = {
        **item,
        "task_id": task_id,
        "task_generation": task_generation,
        "worker_run_id": worker_run_id,
    }
    approval_id = new_runtime_id("apr")
    raw_tool_input = item.get("tool_input")
    tool_input_signature = approval_tool_input_signature(raw_tool_input if raw_tool_input is not None else {})
    tool_input_preview = approval_tool_input_preview(raw_tool_input if raw_tool_input is not None else {})
    evidence_ref = write_approval_evidence(
        config,
        approval_id=approval_id,
        stage="request",
        payload={
            "provider": item.get("provider"),
            "task_id": item.get("task_id"),
            "task_generation": item.get("task_generation"),
            "worker_run_id": item.get("worker_run_id"),
            "session_id": item.get("session_id"),
            "tool_use_id": item.get("tool_use_id"),
            "tool_name": item.get("tool_name"),
            "tool_input": raw_tool_input,
            "risk_class": item.get("risk_class"),
            "suggested_rule": item.get("suggested_rule"),
            "agent_id": item.get("agent_id"),
            "request_payload": item.get("request_payload"),
            "broker_decision": item.get("broker_decision"),
        },
    )
    approval = {
        "approval_id": approval_id,
        "status": "pending",
        "created_at": utc_now(),
        "resolved_at": None,
        "decision": None,
        "note": None,
        "remember": False,
        "resume_override_active": False,
        "resume_override_consumed_at": None,
        "resume_override_consumed_reason": None,
        **{
            key: value
            for key, value in item.items()
            if key not in {"tool_input", "request_payload", "broker_decision"}
        },
        "tool_input_signature": tool_input_signature,
        "tool_input_preview": tool_input_preview,
        "evidence_ref": evidence_ref,
        "resolution_ref": None,
    }
    if not approval.get("expires_at"):
        approval["expires_at"] = _default_expires_at(config, approval["created_at"])
    with approval_lock(config):
        state = load_approval_state(config)
        state.setdefault("pending", []).append(approval)
        save_approval_state(config, state)
        write_activity_log(
            config,
            {
                "type": "approval_requested",
                "provider": approval.get("provider"),
                "task_id": approval.get("task_id"),
                "message": f"Approval requested for {approval.get('tool_name')} ({approval['approval_id']})",
                "approval_id": approval["approval_id"],
                "worker_run_id": approval.get("worker_run_id"),
                "risk_class": approval.get("risk_class"),
                "evidence_ref": evidence_ref,
            },
        )
    return approval


def find_pending(state: dict[str, Any], approval_id: str) -> tuple[int, dict[str, Any] | None]:
    for index, item in enumerate(state.get("pending", [])):
        if item.get("approval_id") == approval_id:
            return index, item
    return -1, None


def _apply_remember_rule(config: dict[str, Any], item: dict[str, Any], decision: str) -> None:
    if not item.get("remember") or item.get("provider") != "claude":
        return
    rule = item.get("suggested_rule")
    if not rule:
        return
    from permission_broker import remember_rule

    remember_rule(config, decision=decision, rule=rule)


def resolve_approval(
    config: dict[str, Any],
    approval_id: str,
    *,
    decision: str,
    note: str | None = None,
    remember: bool = False,
) -> dict[str, Any]:
    if decision not in {"allow", "deny"}:
        raise ValueError(f"Unsupported decision: {decision}")
    with approval_lock(config):
        state = load_approval_state(config)
        index, item = find_pending(state, approval_id)
        if item is None:
            raise KeyError(approval_id)
        item = {
            **item,
            "status": "resolved",
            "decision": decision,
            "resolved_at": utc_now(),
            "note": note,
            "remember": remember,
            "resume_override_active": bool(
                decision == "allow"
                and _provider_uses_claude_cli(config, item.get("provider"))
                and not remember
            ),
            "resume_override_consumed_at": None,
            "resume_override_consumed_reason": None,
        }
        item["resolution_ref"] = write_approval_evidence(
            config,
            approval_id=approval_id,
            stage="resolution",
            payload={
                "provider": item.get("provider"),
                "task_id": item.get("task_id"),
                "task_generation": item.get("task_generation"),
                "worker_run_id": item.get("worker_run_id"),
                "session_id": item.get("session_id"),
                "tool_name": item.get("tool_name"),
                "tool_input_signature": item.get("tool_input_signature"),
                "tool_input_preview": item.get("tool_input_preview"),
                "decision": decision,
                "note": note,
                "remember": remember,
                "request_ref": item.get("evidence_ref"),
                "resume_override_active": item.get("resume_override_active"),
            },
        )
        state["pending"].pop(index)
        state.setdefault("history", []).append(item)
        save_approval_state(config, state)
        _apply_remember_rule(config, item, decision)
        write_activity_log(
            config,
            {
                "type": "approval_resolved",
                "provider": item.get("provider"),
                "task_id": item.get("task_id"),
                "message": f"Approval {decision} for {item.get('tool_name')} ({approval_id})",
                "approval_id": approval_id,
                "decision": decision,
                "worker_run_id": item.get("worker_run_id"),
                "remember": remember,
                "evidence_ref": item.get("resolution_ref") or item.get("evidence_ref"),
            },
        )
    return item


def _approval_signature(
    task_id: str | None,
    task_generation: str | int | None,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    tool_input_signature: str | None = None,
) -> tuple[str, int, str, str] | None:
    normalized_task_id = str(task_id or "").strip()
    try:
        normalized_generation = int(task_generation)
    except (TypeError, ValueError):
        return None
    if not normalized_task_id or normalized_generation <= 0 or not tool_name:
        return None
    return (
        normalized_task_id,
        normalized_generation,
        tool_name,
        str(tool_input_signature or approval_tool_input_signature(tool_input if tool_input is not None else {})),
    )


def find_resume_override(
    config: dict[str, Any],
    *,
    task_id: str | None,
    task_generation: str | int | None,
    tool_name: str,
    tool_input: dict[str, Any],
) -> dict[str, Any] | None:
    state = load_approval_state(config)
    signature = _approval_signature(task_id, task_generation, tool_name, tool_input)
    if signature is None:
        return None
    for item in reversed(state.get("history", [])):
        if not item.get("resume_override_active"):
            continue
        if item.get("decision") != "allow":
            continue
        if item.get("resume_override_consumed_at"):
            continue
        item_signature = _approval_signature(
            item.get("task_id"),
            item.get("task_generation"),
            item.get("tool_name") or "",
            tool_input_signature=item.get("tool_input_signature"),
        )
        if item_signature == signature:
            return item
    return None


def consume_resume_override(
    config: dict[str, Any],
    *,
    approval_id: str,
    reason: str,
) -> dict[str, Any] | None:
    with approval_lock(config):
        state = load_approval_state(config)
        history = state.get("history", [])
        for index in range(len(history) - 1, -1, -1):
            item = history[index]
            if item.get("approval_id") != approval_id:
                continue
            if _approval_signature(
                item.get("task_id"),
                item.get("task_generation"),
                item.get("tool_name") or "",
                tool_input_signature=item.get("tool_input_signature"),
            ) is None:
                return None
            if not item.get("resume_override_active"):
                return None
            if item.get("resume_override_consumed_at"):
                return None
            updated = {
                **item,
                "resume_override_consumed_at": utc_now(),
                "resume_override_consumed_reason": reason,
            }
            history[index] = updated
            save_approval_state(config, state)
            write_activity_log(
                config,
                {
                    "type": "approval_resume_override_consumed",
                    "provider": updated.get("provider"),
                    "task_id": updated.get("task_id"),
                    "message": (
                        f"Consumed approval resume override {approval_id}: {reason}"
                    ),
                    "approval_id": approval_id,
                    "worker_run_id": updated.get("worker_run_id"),
                },
            )
            return updated
    return None


def wait_for_decision(config: dict[str, Any], approval_id: str, *, poll_interval: float = 1.0, timeout_seconds: float | None = None) -> dict[str, Any]:
    started = time.time()
    while True:
        state = load_approval_state(config)
        for item in state.get("history", []):
            if item.get("approval_id") == approval_id:
                return item
        for item in state.get("pending", []):
            if item.get("approval_id") == approval_id:
                break
        else:
            return {"approval_id": approval_id, "status": "missing", "decision": "deny", "note": "Approval item missing"}
        if timeout_seconds is not None and time.time() - started >= timeout_seconds:
            return {"approval_id": approval_id, "status": "timeout", "decision": "deny", "note": "Approval timed out"}
        time.sleep(poll_interval)


class ApprovalHandler(BaseHTTPRequestHandler):
    config: dict[str, Any] | None = None

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        config = self.config or load_config()
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True, "ts": utc_now()})
            return
        if parsed.path == "/approvals":
            self._json(HTTPStatus.OK, list_pending(config))
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        config = self.config or load_config()
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/approvals/"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        _, _, approval_id, action = parsed.path.split("/", 3)
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0")).decode("utf-8").strip()
        payload = json.loads(raw) if raw else {}
        try:
            resolved = resolve_approval(
                config,
                approval_id,
                decision="allow" if action == "allow" else "deny",
                note=payload.get("note"),
                remember=bool(payload.get("remember", False)),
            )
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": f"Unknown approval: {approval_id}"})
            return
        self._json(HTTPStatus.OK, resolved)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List, resolve, or serve the local approval queue.")
    parser.add_argument("--config", default=".orchestrator/config.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List pending approvals.")
    list_parser.add_argument("--all", action="store_true")
    list_parser.add_argument("--json", action="store_true")

    allow_parser = subparsers.add_parser("allow", help="Approve a pending approval item.")
    allow_parser.add_argument("approval_id")
    allow_parser.add_argument("--note")
    allow_parser.add_argument("--remember", action="store_true")

    deny_parser = subparsers.add_parser("deny", help="Reject a pending approval item.")
    deny_parser.add_argument("approval_id")
    deny_parser.add_argument("--note")
    deny_parser.add_argument("--remember", action="store_true")

    prune_parser = subparsers.add_parser("prune-stale", help="Auto-deny stale pending approvals.")
    prune_parser.add_argument("--json", action="store_true")

    serve_parser = subparsers.add_parser("serve", help="Serve the approval queue over HTTP.")
    serve_parser.add_argument("--listen", default="127.0.0.1:8765")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    if args.command == "list":
        payload = list_pending(config, include_history=args.all)
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            for item in payload.get("pending", []):
                print(
                    f"{item['approval_id']} [{item.get('provider')}] task={item.get('task_id')} "
                    f"tool={item.get('tool_name')} risk={item.get('risk_class')}"
                )
            if not payload.get("pending"):
                print("No pending approvals.")
        return 0

    if args.command in {"allow", "deny"}:
        resolved = resolve_approval(
            config,
            getattr(args, "approval_id"),
            decision=args.command,
            note=getattr(args, "note", None),
            remember=getattr(args, "remember", False),
        )
        print(json.dumps(resolved, indent=2, ensure_ascii=False))
        return 0

    if args.command == "prune-stale":
        pruned = prune_stale_approvals(config)
        payload = {"pruned": pruned, "count": len(pruned)}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            if not pruned:
                print("No stale approvals pruned.")
            else:
                for item in pruned:
                    print(f"{item['approval_id']} pruned ({item.get('tool_name')})")
        return 0

    host, port = args.listen.rsplit(":", 1)
    ApprovalHandler.config = config
    server = ThreadingHTTPServer((host, int(port)), ApprovalHandler)
    print(f"Approval queue listening on http://{args.listen}", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
