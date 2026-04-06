from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class OpenClawRuntimePin:
    repository_url: str = "https://github.com/openclaw/openclaw"
    release_tag: str = ""
    commit_sha: str = ""
    image_ref: str = ""
    integration_mode: str = "separate_runtime"

    @property
    def pinned(self) -> bool:
        return bool(self.release_tag and self.commit_sha)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    schedule: str
    description: str
    upstream_entrypoint: str
    handoff_type: str | None
    from_stage: str
    to_stage: str
    execution_context: str
    policy_id: str
    allowed_tool_classes: tuple[str, ...]
    artifact_type: str
    initial_lifecycle_state: str
    approval_required: bool
    required_payload_keys: tuple[str, ...] = ()
    produces_strategy_ref: bool = False
    uses_promotion_gate: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed_tool_classes"] = list(self.allowed_tool_classes)
        data["required_payload_keys"] = list(self.required_payload_keys)
        return data


@dataclass
class WorkflowRunResult:
    workflow_id: str
    dispatch_request: dict[str, Any]
    upstream_response: dict[str, Any]
    handoff: dict[str, Any] | None = None
    registry_entry: dict[str, Any] | None = None
    deployment_request: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "dispatch_request": self.dispatch_request,
            "upstream_response": self.upstream_response,
            "handoff": self.handoff,
            "registry_entry": self.registry_entry,
            "deployment_request": self.deployment_request,
            "notes": list(self.notes),
        }
