from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DeliveryCapability:
    adapter: str
    supported: bool
    requires_manual_confirmation: bool
    can_auto_deliver: bool
    can_auto_approve_edits: bool
    delivery_mode: str
    verified: str
    notes: str = ""
    host: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeliveryRequest:
    agent_id: str
    provider: str
    delivery_mode: str
    message: str
    task_id: str | None = None
    reason: str | None = None
    context_files: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryResult:
    ok: bool
    adapter: str
    mode: str
    target: str
    auto_delivered: bool
    manual_confirmation_required: bool
    notes: str = ""
    command: list[str] = field(default_factory=list)
    payload_path: str | None = None
    log_path: str | None = None
    pid: int | None = None
    run_id: str | None = None
    session_id: str | None = None
    resume_token: str | None = None
    session_url: str | None = None
    pr_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseAdapter:
    name = "base"

    def __init__(self, *, config: dict[str, Any], provider_capabilities: dict[str, Any]) -> None:
        self.config = config
        self.provider_capabilities = provider_capabilities

    def capability(self, agent_id: str) -> DeliveryCapability:
        raise NotImplementedError

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        raise NotImplementedError
