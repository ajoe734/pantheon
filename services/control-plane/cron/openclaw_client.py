from __future__ import annotations

import os
import uuid
from typing import Any, Callable

from models import OpenClawRuntimePin, WorkflowDefinition, utc_now


Transport = Callable[[dict[str, Any]], dict[str, Any]]


def _clean(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


class OpenClawCronClient:
    """Prepare versioned cron workflow dispatch envelopes for the upstream runtime."""

    def __init__(
        self,
        runtime_pin: OpenClawRuntimePin | None = None,
        base_url: str | None = None,
        transport: Transport | None = None,
    ):
        self.runtime_pin = runtime_pin or OpenClawRuntimePin(
            release_tag=os.environ.get("OPENCLAW_RELEASE_TAG", ""),
            commit_sha=os.environ.get("OPENCLAW_COMMIT_SHA", ""),
            image_ref=os.environ.get("OPENCLAW_IMAGE_REF", ""),
        )
        self.base_url = (
            base_url
            or os.environ.get("OPENCLAW_BASE_URL")
            or os.environ.get("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789")
        )
        self.transport = transport
        self._adapter_boundary = {
            "integration_boundary": "pantheon-openclaw-gateway-adapter",
            "workspace_ref": _clean(os.environ.get("PANTHEON_WORKSPACE_REF")),
            "auth_profile_ref": _clean(os.environ.get("PANTHEON_AUTH_PROFILE_REF")),
            "persona_id": _clean(os.environ.get("PANTHEON_PERSONA_ID")),
            "session_id": _clean(os.environ.get("PANTHEON_SESSION_ID")),
            "trace_id": _clean(os.environ.get("PANTHEON_TRACE_ID")),
            "request_id": _clean(os.environ.get("PANTHEON_REQUEST_ID")),
            "credential_sharing": "disallowed",
            "filesystem_scope": "persona_workspace_only",
        }

    def prepare_dispatch(self, workflow: WorkflowDefinition, payload: dict[str, Any]) -> dict[str, Any]:
        missing = [key for key in workflow.required_payload_keys if key not in payload]
        if missing:
            raise ValueError(f"{workflow.workflow_id} missing required payload keys: {missing}")

        return {
            "request_id": f"{workflow.workflow_id}-{uuid.uuid4()}",
            "prepared_at": utc_now(),
            "runtime": {
                **self.runtime_pin.to_dict(),
                "base_url": self.base_url,
            },
            "pantheon_adapter": {
                key: value
                for key, value in self._adapter_boundary.items()
                if value is not None
            },
            "workflow": {
                "workflow_id": workflow.workflow_id,
                "upstream_entrypoint": workflow.upstream_entrypoint,
                "schedule": workflow.schedule,
                "description": workflow.description,
            },
            "governance": {
                "policy_id": workflow.policy_id,
                "execution_context": workflow.execution_context,
                "allowed_tool_classes": list(workflow.allowed_tool_classes),
                "approval_required": workflow.approval_required,
                "artifact_type": workflow.artifact_type,
                "initial_lifecycle_state": workflow.initial_lifecycle_state,
                "uses_promotion_gate": workflow.uses_promotion_gate,
            },
            "payload": payload,
        }

    def dispatch_prepared(self, workflow_id: str, request: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        if dry_run or self.transport is None:
            return {
                "status": "prepared",
                "mode": "dry_run" if dry_run else "local_only",
                "workflow_id": workflow_id,
                "request_id": request["request_id"],
            }

        response = self.transport(request)
        if not isinstance(response, dict):
            raise ValueError("OpenClaw transport must return a dictionary response")
        return response
