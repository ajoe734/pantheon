#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
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
    config_path,
    load_config,
    load_json,
    new_runtime_id,
    resolve_path,
    utc_now,
    write_activity_log,
    write_approval_evidence,
    write_json,
)
from runtime_state import default_approval_state, load_approval_state, load_runtime_state, save_approval_state


@contextmanager
def approval_lock(config: dict[str, Any]):
    lock_path = config_path(config, "approval_queue").with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
    if item.get("status") != "pending":
        return False
    if item.get("task_id") or item.get("worker_run_id"):
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
        and (worker.get("session_id") or worker.get("resume_token"))
    ):
        # Claude can resume from session state after approval even if the original
        # worker process exited, so keep the approval entry live.
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
                note = f"Auto-pruned stale approval after {int(stale_after_seconds)}s without task/worker binding."
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


def create_approval(config: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
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


def _apply_temporary_resume_rule(config: dict[str, Any], item: dict[str, Any], decision: str) -> dict[str, Any]:
    if decision != "allow" or item.get("provider") != "claude" or item.get("remember"):
        return item
    rule = item.get("suggested_rule")
    if not rule:
        return item
    from permission_broker import add_temporary_allow_rule

    inserted = add_temporary_allow_rule(config, rule=rule)
    return {
        **item,
        "resume_override_rule": rule,
        "resume_override_rule_inserted": inserted,
    }


def _approval_tool_input(item: dict[str, Any]) -> dict[str, Any]:
    tool_input = item.get("tool_input")
    if isinstance(tool_input, dict):
        return tool_input
    evidence_ref = str(item.get("evidence_ref") or "").strip()
    if not evidence_ref:
        return {}
    evidence_path = resolve_path(evidence_ref)
    if evidence_path is None or not evidence_path.exists():
        return {}
    evidence = load_json(evidence_path, default={}) or {}
    loaded = evidence.get("tool_input")
    return loaded if isinstance(loaded, dict) else {}


def _suspend_conflicting_resume_rules(config: dict[str, Any], item: dict[str, Any], decision: str) -> dict[str, Any]:
    if decision != "allow" or item.get("provider") != "claude" or item.get("remember"):
        return item
    tool_name = item.get("tool_name")
    tool_input = _approval_tool_input(item)
    if not isinstance(tool_name, str) or not tool_name:
        return item
    from permission_broker import suspend_matching_rules

    suspended_ask_rules = suspend_matching_rules(
        config,
        bucket="ask",
        tool_name=tool_name,
        tool_input=tool_input,
    )
    return {
        **item,
        "resume_override_suspended_ask_rules": suspended_ask_rules,
    }


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
        item = _apply_temporary_resume_rule(config, item, decision)
        item = _suspend_conflicting_resume_rules(config, item, decision)
        item["resolution_ref"] = write_approval_evidence(
            config,
            approval_id=approval_id,
            stage="resolution",
            payload={
                "provider": item.get("provider"),
                "task_id": item.get("task_id"),
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
                "resume_override_rule": item.get("resume_override_rule"),
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
    session_id: str | None,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    tool_input_signature: str | None = None,
) -> tuple[str | None, str, str]:
    return (
        session_id,
        tool_name,
        str(tool_input_signature or approval_tool_input_signature(tool_input if tool_input is not None else {})),
    )


def find_resume_override(
    config: dict[str, Any],
    *,
    session_id: str | None,
    tool_name: str,
    tool_input: dict[str, Any],
) -> dict[str, Any] | None:
    state = load_approval_state(config)
    signature = _approval_signature(session_id, tool_name, tool_input)
    for item in reversed(state.get("history", [])):
        if not item.get("resume_override_active"):
            continue
        if item.get("decision") != "allow":
            continue
        if item.get("resume_override_consumed_at"):
            continue
        item_signature = _approval_signature(
            item.get("session_id"),
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
            if not item.get("resume_override_active"):
                return item
            if item.get("resume_override_consumed_at"):
                return item
            updated = {
                **item,
                "resume_override_consumed_at": utc_now(),
                "resume_override_consumed_reason": reason,
            }
            rule = updated.get("resume_override_rule")
            inserted = bool(updated.get("resume_override_rule_inserted"))
            suspended_ask_rules = list(updated.get("resume_override_suspended_ask_rules") or [])
            history[index] = updated
            save_approval_state(config, state)
            if inserted and rule:
                from permission_broker import remove_temporary_allow_rule
                from permission_broker import restore_rules

                remove_temporary_allow_rule(config, rule=rule)
                restore_rules(config, bucket="ask", rules=suspended_ask_rules)
            elif suspended_ask_rules:
                from permission_broker import restore_rules

                restore_rules(config, bucket="ask", rules=suspended_ask_rules)
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
