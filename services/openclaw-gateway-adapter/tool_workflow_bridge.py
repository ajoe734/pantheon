"""Pantheon tool/workflow bridge for the OpenClaw gateway adapter.

This module implements SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE acceptance:
- allowed tool and workflow policy is enforced (deny-by-default)
- Pantheon operator identity and request context map into upstream calls
- request/response audit trail is written per invocation
- unknown or disallowed tools fail closed
- no broker, paper, or live execution is enabled

Policy configuration (env vars):
  OPENCLAW_ALLOWED_TOOLS      — comma-separated list of allowed tool names
                                empty (default) means ALL tools are denied
  OPENCLAW_ALLOWED_WORKFLOWS  — comma-separated list of allowed workflow refs
                                empty (default) means ALL workflows are denied
  OPENCLAW_BRIDGE_AUDIT_PATH  — override audit log file path
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from assistant_command_policy import (
    ASSISTANT_COMMAND_TOOL_NAME,
    AssistantCommandPolicy,
    build_command_audit_entry,
    command_argv_hash,
)


ASSISTANT_SKILL_DESCRIPTOR_SCHEMA_VERSION = "assistant_skill_descriptor.v1"
_DEFAULT_EFFECTIVE_SKILL_MODE = "kernel_debug"
_DEFAULT_OPERATOR_ROLE = "operator"
_TOOL_SKILL_MODES = ("kernel_debug", "kernel_repair")
_ASSISTANT_COMMAND_SKILL_MODES = ("kernel_observe", "kernel_debug", "kernel_repair")
_WORKFLOW_SKILL_MODES = ("kernel_repair",)
_OPERATOR_ROLE_ALIASES = {
    "admin": "admin",
    "approver": "approver",
    "capability_admin": "approver",
    "operator": "operator",
    "reviewer": "reviewer",
    "viewer": "viewer",
}
_ROLE_GATE: Dict[str, frozenset[str]] = {
    "viewer": frozenset({"viewer", "reviewer", "operator", "approver", "admin"}),
    "reviewer": frozenset({"reviewer", "operator", "approver", "admin"}),
    "operator": frozenset({"operator", "admin"}),
    "approver": frozenset({"approver", "admin"}),
    "admin": frozenset({"admin"}),
}


def _normalize_mode(mode: Optional[str]) -> str:
    clean = str(mode or "").strip()
    return clean if clean else _DEFAULT_EFFECTIVE_SKILL_MODE


def _normalize_operator_role(role: Optional[str]) -> str:
    clean = str(role or "").strip().lower()
    return _OPERATOR_ROLE_ALIASES.get(clean, clean or _DEFAULT_OPERATOR_ROLE)


def _role_allowed(*, required_role: str, operator_role: str) -> bool:
    allowed_roles = _ROLE_GATE.get(required_role, frozenset())
    return operator_role in allowed_roles


def _title_from_ref(ref: str) -> str:
    clean = str(ref or "").strip()
    if not clean:
        return "Unnamed Skill"
    return " ".join(part.capitalize() for part in clean.replace(":", ".").replace("_", ".").split(".") if part)


def _mode_gate(allowed_modes: tuple[str, ...]) -> Dict[str, Any]:
    return {
        "type": "allowlist",
        "default": "deny",
        "allowed_modes": list(allowed_modes),
    }


def _open_input_schema(metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    metadata = metadata or {}
    for key in ("input_schema", "inputSchema", "schema", "parameters"):
        schema = metadata.get(key)
        if isinstance(schema, dict):
            return schema
    return {"type": "object", "additionalProperties": True}


@dataclasses.dataclass(frozen=True)
class AssistantSkillDescriptor:
    """Pantheon assistant-skill descriptor derived from OpenClaw policy state."""

    id: str
    title: str
    surface: str
    mode_gate: Dict[str, Any]
    role: str
    confirm_policy: Dict[str, Any]
    input_schema: Dict[str, Any]
    handler_ref: str
    result_surface: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "surface": self.surface,
            "mode_gate": dict(self.mode_gate),
            "role": self.role,
            "confirm_policy": dict(self.confirm_policy),
            "input_schema": dict(self.input_schema),
            "handler_ref": self.handler_ref,
            "result_surface": self.result_surface,
        }


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _args_hash(args: Any) -> str:
    try:
        blob = json.dumps(args, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except Exception:
        blob = repr(args).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


class BridgeAuditLog:
    """Append-only JSONL audit log for tool and workflow invocations."""

    def __init__(
        self,
        path: Optional[str | os.PathLike[str]] = None,
        *,
        clock: Callable[[], str] = _utc_now_iso,
    ) -> None:
        if path is None:
            raw = os.getenv("OPENCLAW_BRIDGE_AUDIT_PATH", "")
            path = raw if raw else "/tmp/openclaw-gateway-adapter/bridge_audit.jsonl"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()

    def record(self, entry: Dict[str, Any]) -> None:
        entry.setdefault("at", self._clock())
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def read(
        self,
        *,
        session_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        results: List[Dict[str, Any]] = []
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if session_id is not None and entry.get("session_id") != session_id:
                        continue
                    if operator_id is not None and entry.get("operator_id") != operator_id:
                        continue
                    results.append(entry)
        except OSError:
            return []
        return results[-limit:]


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------

# Tool names that are always blocked regardless of the allowlist.
# These map to broker / paper / live / capital paths that must remain disabled.
_ALWAYS_BLOCKED_TOOLS = frozenset({
    "broker_order",
    "submit_order",
    "live_order",
    "paper_order",
    "canary_order",
    "capital_bind",
    "capital_release",
    "lean_deploy",
    "live_execute",
    "paper_execute",
    "canary_execute",
    "broker_session_create",
    "broker_session_cancel",
})

_ALWAYS_BLOCKED_TOOL_PREFIXES = (
    "broker.",
    "live.",
    "paper.",
    "canary.",
    "capital.",
    "lean.",
)

_ALWAYS_BLOCKED_WORKFLOW_PREFIXES = (
    "broker.",
    "live.",
    "paper.",
    "canary.",
    "capital.",
    "lean.",
)


@dataclasses.dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    policy_class: str  # "always_blocked" | "not_in_allowlist" | "allowlist" | "deny_all"


class ToolPolicy:
    """Evaluates whether a tool or workflow invocation is allowed.

    Denial is always the safe default — an empty allowlist means deny all.
    """

    def __init__(
        self,
        *,
        allowed_tools: Optional[List[str]] = None,
        allowed_workflows: Optional[List[str]] = None,
    ) -> None:
        if allowed_tools is None:
            raw = os.getenv("OPENCLAW_ALLOWED_TOOLS", "").strip()
            allowed_tools = [t.strip() for t in raw.split(",") if t.strip()] if raw else []
        if allowed_workflows is None:
            raw = os.getenv("OPENCLAW_ALLOWED_WORKFLOWS", "").strip()
            allowed_workflows = [w.strip() for w in raw.split(",") if w.strip()] if raw else []
        self._allowed_tools: frozenset[str] = frozenset(allowed_tools)
        self._allowed_workflows: frozenset[str] = frozenset(allowed_workflows)

    @property
    def allowed_tools(self) -> List[str]:
        return sorted(self._allowed_tools)

    @property
    def allowed_workflows(self) -> List[str]:
        return sorted(self._allowed_workflows)

    def evaluate_tool(self, tool_name: str) -> PolicyDecision:
        normalized = tool_name.lower()
        if normalized in _ALWAYS_BLOCKED_TOOLS:
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' is always blocked (broker/live/paper path).",
                policy_class="always_blocked",
            )
        for prefix in _ALWAYS_BLOCKED_TOOL_PREFIXES:
            if normalized.startswith(prefix):
                return PolicyDecision(
                    allowed=False,
                    reason=f"Tool '{tool_name}' matches always-blocked prefix '{prefix}'.",
                    policy_class="always_blocked",
                )
        if not self._allowed_tools:
            return PolicyDecision(
                allowed=False,
                reason="No tools are in the allowlist. All tool invocations are denied by default.",
                policy_class="deny_all",
            )
        if tool_name not in self._allowed_tools:
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' is not in the allowed tool list.",
                policy_class="not_in_allowlist",
            )
        return PolicyDecision(
            allowed=True,
            reason=f"Tool '{tool_name}' is in the allowed tool list.",
            policy_class="allowlist",
        )

    def evaluate_workflow(self, workflow_ref: str) -> PolicyDecision:
        for prefix in _ALWAYS_BLOCKED_WORKFLOW_PREFIXES:
            if workflow_ref.lower().startswith(prefix):
                return PolicyDecision(
                    allowed=False,
                    reason=f"Workflow ref '{workflow_ref}' matches always-blocked prefix '{prefix}'.",
                    policy_class="always_blocked",
                )
        if not self._allowed_workflows:
            return PolicyDecision(
                allowed=False,
                reason="No workflows are in the allowlist. All workflow triggers are denied by default.",
                policy_class="deny_all",
            )
        if workflow_ref not in self._allowed_workflows:
            return PolicyDecision(
                allowed=False,
                reason=f"Workflow ref '{workflow_ref}' is not in the allowed workflow list.",
                policy_class="not_in_allowlist",
            )
        return PolicyDecision(
            allowed=True,
            reason=f"Workflow ref '{workflow_ref}' is in the allowed workflow list.",
            policy_class="allowlist",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed_tools": self.allowed_tools,
            "allowed_workflows": self.allowed_workflows,
            "assistant_command_tool": ASSISTANT_COMMAND_TOOL_NAME,
            "always_blocked_tools": sorted(_ALWAYS_BLOCKED_TOOLS),
            "always_blocked_tool_prefixes": list(_ALWAYS_BLOCKED_TOOL_PREFIXES),
            "always_blocked_workflow_prefixes": list(_ALWAYS_BLOCKED_WORKFLOW_PREFIXES),
            "default_posture": "deny_all",
            "note": (
                "Empty allowlist means all tools/workflows are denied. "
                "Set OPENCLAW_ALLOWED_TOOLS / OPENCLAW_ALLOWED_WORKFLOWS env vars "
                "to enable specific tools or workflow refs."
            ),
        }


def _tool_skill_descriptor(
    tool_name: str,
    *,
    upstream_metadata: Optional[Dict[str, Any]] = None,
) -> AssistantSkillDescriptor:
    metadata = upstream_metadata or {}
    if tool_name == ASSISTANT_COMMAND_TOOL_NAME:
        return AssistantSkillDescriptor(
            id=tool_name,
            title="Assistant Command Authorization",
            surface="assistant_command",
            mode_gate=_mode_gate(_ASSISTANT_COMMAND_SKILL_MODES),
            role="operator",
            confirm_policy={
                "required": False,
                "note": "This descriptor authorizes brokered command requests only; execution has a separate policy gate.",
            },
            input_schema={
                "type": "object",
                "required": ["session_id", "mode", "argv"],
                "properties": {
                    "session_id": {"type": "string"},
                    "mode": {"type": "string"},
                    "command_class": {"type": "string"},
                    "argv": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
            handler_ref=f"openclaw.tool:{tool_name}",
            result_surface="assistant_command_authorization",
        )
    title = str(metadata.get("title") or metadata.get("display_name") or "").strip()
    return AssistantSkillDescriptor(
        id=tool_name,
        title=title or _title_from_ref(tool_name),
        surface="openclaw_tool",
        mode_gate=_mode_gate(_TOOL_SKILL_MODES),
        role="operator",
        confirm_policy={"required": False},
        input_schema=_open_input_schema(metadata),
        handler_ref=f"openclaw.tool:{tool_name}",
        result_surface="openclaw_tool_result",
    )


def _workflow_skill_descriptor(workflow_ref: str) -> AssistantSkillDescriptor:
    return AssistantSkillDescriptor(
        id=f"workflow:{workflow_ref}",
        title=_title_from_ref(workflow_ref),
        surface="openclaw_workflow",
        mode_gate=_mode_gate(_WORKFLOW_SKILL_MODES),
        role="operator",
        confirm_policy={
            "required": True,
            "policy": "bff_command_or_control_mode",
        },
        input_schema={"type": "object", "additionalProperties": True},
        handler_ref=f"openclaw.workflow:{workflow_ref}",
        result_surface="openclaw_workflow_result",
    )


def _descriptor_effective(
    descriptor: AssistantSkillDescriptor,
    *,
    mode: str,
    operator_role: str,
) -> bool:
    allowed_modes = descriptor.mode_gate.get("allowed_modes")
    mode_allowed = isinstance(allowed_modes, list) and mode in {str(item) for item in allowed_modes}
    return mode_allowed and _role_allowed(required_role=descriptor.role, operator_role=operator_role)


def _upstream_tool_metadata(raw_tools: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    metadata: Dict[str, Dict[str, Any]] = {}
    for raw in raw_tools or []:
        if isinstance(raw, dict):
            name = str(raw.get("name") or raw.get("tool_name") or raw.get("id") or "").strip()
            if name:
                metadata[name] = dict(raw)
        else:
            name = str(raw).strip()
            if name:
                metadata[name] = {"name": name}
    return metadata


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class BridgeError(Exception):
    """Raised by the bridge for policy or upstream errors."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": "bridge_error",
            "error_code": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ToolWorkflowBridge:
    """Bridges Pantheon operator requests to OpenClaw upstream tool/workflow calls.

    Responsibilities:
    - Validate operator identity is present on every request.
    - Apply ToolPolicy (deny-by-default) before any upstream call.
    - Write an audit entry for every invocation attempt regardless of outcome.
    - Map Pantheon operator context into the upstream request context bundle.
    - Return structured upstream results or typed BridgeError payloads.
    """

    def __init__(
        self,
        *,
        policy: Optional[ToolPolicy] = None,
        command_policy: Optional[AssistantCommandPolicy] = None,
        audit_log: Optional[BridgeAuditLog] = None,
        clock: Callable[[], str] = _utc_now_iso,
        trace_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._policy = policy or ToolPolicy()
        self._command_policy = command_policy or AssistantCommandPolicy()
        self._audit = audit_log or BridgeAuditLog()
        self._clock = clock
        self._trace_id_factory = trace_id_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def invoke_tool(
        self,
        *,
        session_id: str,
        tool_name: str,
        args: Any,
        operator_id: str,
        trace_id: Optional[str] = None,
        upstream: Any = None,
    ) -> Dict[str, Any]:
        """Invoke a tool through the bridge.

        Raises BridgeError when:
        - operator_id is missing
        - session_id is missing
        - tool is denied by policy
        - upstream call fails
        """
        if not operator_id:
            raise BridgeError("BRIDGE_OPERATOR_REQUIRED", "operator_id is required.", status_code=401)
        if not session_id:
            raise BridgeError("BRIDGE_SESSION_REQUIRED", "session_id is required.", status_code=400)
        if not tool_name:
            raise BridgeError("BRIDGE_TOOL_NAME_REQUIRED", "tool_name is required.", status_code=400)

        trace_id = trace_id or self._trace_id_factory()
        decision = self._policy.evaluate_tool(tool_name)

        base_entry: Dict[str, Any] = {
            "request_type": "tool_invoke",
            "trace_id": trace_id,
            "operator_id": operator_id,
            "session_id": session_id,
            "tool_name": tool_name,
            "args_hash": _args_hash(args),
            "policy_decision": "allowed" if decision.allowed else "denied",
            "policy_class": decision.policy_class,
            "policy_reason": decision.reason,
        }

        if not decision.allowed:
            self._audit.record({**base_entry, "outcome": "denied"})
            raise BridgeError(
                "BRIDGE_TOOL_DENIED",
                decision.reason,
                status_code=403,
                details={"tool_name": tool_name, "policy_class": decision.policy_class},
            )

        if upstream is None:
            self._audit.record({**base_entry, "outcome": "upstream_not_configured"})
            raise BridgeError(
                "BRIDGE_UPSTREAM_NOT_CONFIGURED",
                "Upstream OpenClaw client is not configured.",
                status_code=503,
                retryable=False,
            )

        self._audit.record({**base_entry, "outcome": "pending"})
        try:
            result = upstream.invoke_tool(
                session_id=session_id,
                tool_name=tool_name,
                args=args,
                operator_context=self._build_operator_context(operator_id, trace_id),
            )
        except Exception as exc:
            error_payload = _coerce_upstream_error(exc)
            self._audit.record({**base_entry, "outcome": "upstream_error", "error": error_payload})
            raise BridgeError(
                error_payload.get("error_code", "BRIDGE_UPSTREAM_ERROR"),
                error_payload.get("message", str(exc)),
                status_code=error_payload.get("status_code", 502),
                retryable=bool(error_payload.get("retryable")),
                details=error_payload,
            ) from exc

        self._audit.record({**base_entry, "outcome": "ok"})
        return {
            "status": "ok",
            "trace_id": trace_id,
            "session_id": session_id,
            "tool_name": tool_name,
            "result": result,
        }

    def trigger_workflow(
        self,
        *,
        workflow_ref: str,
        context: Any,
        operator_id: str,
        trace_id: Optional[str] = None,
        upstream: Any = None,
    ) -> Dict[str, Any]:
        """Trigger a workflow through the bridge.

        Raises BridgeError when:
        - operator_id is missing
        - workflow_ref is denied by policy
        - upstream call fails
        """
        if not operator_id:
            raise BridgeError("BRIDGE_OPERATOR_REQUIRED", "operator_id is required.", status_code=401)
        if not workflow_ref:
            raise BridgeError("BRIDGE_WORKFLOW_REF_REQUIRED", "workflow_ref is required.", status_code=400)

        trace_id = trace_id or self._trace_id_factory()
        decision = self._policy.evaluate_workflow(workflow_ref)

        base_entry: Dict[str, Any] = {
            "request_type": "workflow_trigger",
            "trace_id": trace_id,
            "operator_id": operator_id,
            "workflow_ref": workflow_ref,
            "context_hash": _args_hash(context),
            "policy_decision": "allowed" if decision.allowed else "denied",
            "policy_class": decision.policy_class,
            "policy_reason": decision.reason,
        }

        if not decision.allowed:
            self._audit.record({**base_entry, "outcome": "denied"})
            raise BridgeError(
                "BRIDGE_WORKFLOW_DENIED",
                decision.reason,
                status_code=403,
                details={"workflow_ref": workflow_ref, "policy_class": decision.policy_class},
            )

        if upstream is None:
            self._audit.record({**base_entry, "outcome": "upstream_not_configured"})
            raise BridgeError(
                "BRIDGE_UPSTREAM_NOT_CONFIGURED",
                "Upstream OpenClaw client is not configured.",
                status_code=503,
                retryable=False,
            )

        self._audit.record({**base_entry, "outcome": "pending"})
        try:
            result = upstream.trigger_workflow(
                workflow_ref=workflow_ref,
                context=context,
                operator_context=self._build_operator_context(operator_id, trace_id),
            )
        except Exception as exc:
            error_payload = _coerce_upstream_error(exc)
            self._audit.record({**base_entry, "outcome": "upstream_error", "error": error_payload})
            raise BridgeError(
                error_payload.get("error_code", "BRIDGE_UPSTREAM_ERROR"),
                error_payload.get("message", str(exc)),
                status_code=error_payload.get("status_code", 502),
                retryable=bool(error_payload.get("retryable")),
                details=error_payload,
            ) from exc

        self._audit.record({**base_entry, "outcome": "ok"})
        job_id = str(result.get("job_id") or result.get("id") or "")
        return {
            "status": "ok",
            "trace_id": trace_id,
            "workflow_ref": workflow_ref,
            "job_id": job_id or None,
            "result": result,
        }

    def request_assistant_command(
        self,
        *,
        session_id: str,
        operator_id: str,
        mode: str,
        argv: List[Any],
        command_class: Optional[str] = None,
        cwd: Optional[str] = None,
        trace_id: Optional[str] = None,
        command_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Gate an assistant command request through OpenClaw and mode policy.

        This method only authorizes or denies the request and writes audit.  It
        does not execute the command.
        """
        if not operator_id:
            raise BridgeError("BRIDGE_OPERATOR_REQUIRED", "operator_id is required.", status_code=401)
        if not session_id:
            raise BridgeError("BRIDGE_SESSION_REQUIRED", "session_id is required.", status_code=400)
        if not argv:
            raise BridgeError("BRIDGE_COMMAND_ARGV_REQUIRED", "command argv is required.", status_code=400)

        trace_id = trace_id or self._trace_id_factory()
        command_id = command_id or f"asst_cmd_{uuid.uuid4().hex}"
        normalized_argv = [str(arg) for arg in argv if str(arg) != ""]

        tool_decision = self._policy.evaluate_tool(ASSISTANT_COMMAND_TOOL_NAME)
        base_entry: Dict[str, Any] = {
            "event_type": "assistant.command.denied",
            "request_type": "assistant_command",
            "outcome": "denied",
            "command_id": command_id,
            "trace_id": trace_id,
            "operator_id": operator_id,
            "session_id": session_id,
            "mode": str(getattr(mode, "value", mode) or ""),
            "command_class": command_class,
            "argv_hash": command_argv_hash(normalized_argv),
            "argv_head": normalized_argv[0] if normalized_argv else None,
            "cwd": cwd,
            "broker_tool": ASSISTANT_COMMAND_TOOL_NAME,
            "policy_layer": "openclaw_tool_policy",
            "policy_decision": "allowed" if tool_decision.allowed else "denied",
            "policy_class": tool_decision.policy_class,
            "policy_reason": tool_decision.reason,
        }
        if not tool_decision.allowed:
            self._audit.record(base_entry)
            raise BridgeError(
                "BRIDGE_ASSISTANT_COMMAND_DENIED",
                tool_decision.reason,
                status_code=403,
                details={
                    "broker_tool": ASSISTANT_COMMAND_TOOL_NAME,
                    "policy_layer": "openclaw_tool_policy",
                    "policy_class": tool_decision.policy_class,
                },
            )

        decision = self._command_policy.evaluate(
            mode=mode,
            argv=normalized_argv,
            command_class=command_class,
            cwd=cwd,
        )
        entry = build_command_audit_entry(
            command_id=command_id,
            session_id=session_id,
            operator_id=operator_id,
            trace_id=trace_id,
            decision=decision,
            extra={
                "broker_tool": ASSISTANT_COMMAND_TOOL_NAME,
                "tool_policy_class": tool_decision.policy_class,
                "policy_layer": "assistant_command_policy",
            },
        )
        self._audit.record(entry)
        if not decision.allowed:
            raise BridgeError(
                "BRIDGE_ASSISTANT_COMMAND_DENIED",
                decision.reason,
                status_code=403,
                details={
                    "command_id": command_id,
                    "mode": decision.mode,
                    "command_class": decision.command_class,
                    "policy_layer": "assistant_command_policy",
                    "policy_class": decision.policy_class,
                },
            )

        return {
            "status": "allowed",
            "trace_id": trace_id,
            "command_id": command_id,
            "session_id": session_id,
            "mode": decision.mode,
            "command_class": decision.command_class,
            "argv_hash": command_argv_hash(decision.argv),
            "policy_class": decision.policy_class,
            "reason": decision.reason,
            "note": "Command authorization only; execution is handled by a separate broker runner.",
        }

    def list_effective_tools(
        self,
        *,
        agent_id: str,
        session_id: Optional[str] = None,
        operator_id: str,
        mode: Optional[str] = None,
        operator_role: Optional[str] = None,
        upstream: Any = None,
    ) -> Dict[str, Any]:
        """Return the effective tool set visible to this operator/session.

        The effective tool set is the intersection of:
        1. The executable policy allowlist (Pantheon-owned)
        2. What the upstream reports for the agent/session (if reachable)
        3. The assistant-skill descriptor mode/role gates

        If the upstream is absent, the response shows the policy allowlist only
        (degraded mode).
        """
        if not operator_id:
            raise BridgeError("BRIDGE_OPERATOR_REQUIRED", "operator_id is required.", status_code=401)

        resolved_mode = _normalize_mode(mode)
        resolved_operator_role = _normalize_operator_role(operator_role)
        policy_tools = self._policy.allowed_tools
        executable_policy_tools = [
            tool_name
            for tool_name in policy_tools
            if self._policy.evaluate_tool(tool_name).allowed
        ]
        blocked_policy_tools = sorted(set(policy_tools) - set(executable_policy_tools))
        policy_workflows = self._policy.allowed_workflows
        executable_policy_workflows = [
            workflow_ref
            for workflow_ref in policy_workflows
            if self._policy.evaluate_workflow(workflow_ref).allowed
        ]
        blocked_policy_workflows = sorted(set(policy_workflows) - set(executable_policy_workflows))
        upstream_tools: Optional[List[Dict[str, Any]]] = None
        upstream_status = "not_configured"

        if upstream is not None:
            try:
                if session_id:
                    raw = upstream.resolve_tools(agent_id=agent_id, session_id=session_id)
                else:
                    raw = upstream.list_tools(agent_id=agent_id)
                upstream_tools = raw if isinstance(raw, list) else raw.get("tools", [])
                upstream_status = "ok"
            except Exception as exc:
                err = _coerce_upstream_error(exc)
                upstream_status = "degraded"
                upstream_tools = None

        effective: List[str]
        upstream_metadata = _upstream_tool_metadata(upstream_tools)
        if upstream_tools is not None:
            upstream_names = set(upstream_metadata)
            if executable_policy_tools:
                effective = sorted(frozenset(executable_policy_tools) & upstream_names)
            else:
                effective = []
        else:
            effective = list(executable_policy_tools)

        effective = [
            tool_name
            for tool_name in effective
            if _descriptor_effective(
                _tool_skill_descriptor(tool_name, upstream_metadata=upstream_metadata.get(tool_name)),
                mode=resolved_mode,
                operator_role=resolved_operator_role,
            )
        ]
        effective_workflows = [
            workflow_ref
            for workflow_ref in executable_policy_workflows
            if _descriptor_effective(
                _workflow_skill_descriptor(workflow_ref),
                mode=resolved_mode,
                operator_role=resolved_operator_role,
            )
        ]
        effective_skill_descriptors = [
            _tool_skill_descriptor(tool_name, upstream_metadata=upstream_metadata.get(tool_name)).to_dict()
            for tool_name in effective
        ] + [
            _workflow_skill_descriptor(workflow_ref).to_dict()
            for workflow_ref in effective_workflows
        ]

        return {
            "status": "ok" if upstream_status == "ok" else "degraded",
            "schema_version": ASSISTANT_SKILL_DESCRIPTOR_SCHEMA_VERSION,
            "upstream_status": upstream_status,
            "agent_id": agent_id,
            "session_id": session_id,
            "mode": resolved_mode,
            "operator_role": resolved_operator_role,
            "policy_allowed_tools": policy_tools,
            "policy_blocked_tools": blocked_policy_tools,
            "policy_allowed_workflows": policy_workflows,
            "policy_blocked_workflows": blocked_policy_workflows,
            "effective_tools": effective,
            "effective_workflows": effective_workflows,
            "effective_skills": effective_skill_descriptors,
            "skill_resolution": {
                "default_posture": "deny_all",
                "source": "openclaw_tool_workflow_policy",
                "mode_gate": "deny_unless_mode_allowed",
                "role_gate": "deny_unless_role_allowed",
                "unknown_skills": "fail_closed",
                "descriptor_fields": [
                    "id",
                    "title",
                    "surface",
                    "mode_gate",
                    "role",
                    "confirm_policy",
                    "input_schema",
                    "handler_ref",
                    "result_surface",
                ],
            },
            "note": (
                "effective_tools is the intersection of the Pantheon executable policy allowlist "
                "and upstream-reported tools, then filtered by assistant-skill descriptor mode/role "
                "gates. effective_skills contains the descriptor form derived from the same policy "
                "state; no second registry is consulted. Always-blocked broker/live/paper/capital "
                "tools are excluded even if configured in the allowlist. An empty list means no "
                "tools are available to this operator/session."
            ),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_operator_context(operator_id: str, trace_id: str) -> Dict[str, Any]:
        return {
            "pantheon_operator_id": operator_id,
            "pantheon_trace_id": trace_id,
            "pantheon_source": "openclaw-gateway-adapter",
        }


def _coerce_upstream_error(exc: Exception) -> Dict[str, Any]:
    to_payload = getattr(exc, "to_payload", None)
    if callable(to_payload):
        try:
            payload = to_payload()
            if isinstance(payload, dict):
                payload.setdefault("retryable", bool(getattr(exc, "retryable", False)))
                payload.setdefault("status_code", getattr(exc, "status_code", 502))
                return payload
        except Exception:
            pass
    return {
        "status": "upstream_error",
        "error_code": getattr(exc, "error_code", "UPSTREAM_UNKNOWN"),
        "message": str(exc),
        "retryable": bool(getattr(exc, "retryable", False)),
        "status_code": getattr(exc, "status_code", 502),
    }
