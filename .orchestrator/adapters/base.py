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


def hysteresis_held_auth_ready(
    provider_capabilities: dict[str, Any] | None,
    provider_id: str | None,
    *,
    config: dict[str, Any] | None = None,
    fallback_provider_key: str | None = None,
) -> bool:
    """Return True when the capability report is actively holding auth_ready open.

    CLI adapters derive auth_ready from their own local checks (credential file on
    disk, CLI handshake). Those checks are exactly as flaky under load as the probe
    this hysteresis exists to debounce, so an adapter that ignored the report would
    independently re-derive ``can_auto_deliver=False`` on the first transient failure
    and defeat the hold recorded in ``provider_capabilities.json``.

    The hold requires an *active* streak (>= 1) still under the configured threshold.
    Requiring an active streak keeps this a debounce and not a pin: a provider with
    no failing live probe has streak 0 and gets no hold at all, and a provider whose
    streak has reached the threshold has already flipped to False upstream.
    """

    providers = (provider_capabilities or {}).get("providers")
    if not isinstance(providers, dict):
        return False
    entry = providers.get(provider_id) if provider_id else None
    if not isinstance(entry, dict) and fallback_provider_key:
        entry = providers.get(fallback_provider_key)
    if not isinstance(entry, dict) or entry.get("auth_ready") is not True:
        return False
    try:
        streak = int(entry.get("consecutive_probe_failures", 0))
        threshold = int(
            (config or {}).get("supervisor", {}).get("provider_probe_failure_hysteresis_threshold", 3)
        )
    except (TypeError, ValueError):
        return False
    return 1 <= streak < threshold


class BaseAdapter:
    name = "base"

    def __init__(self, *, config: dict[str, Any], provider_capabilities: dict[str, Any]) -> None:
        self.config = config
        self.provider_capabilities = provider_capabilities

    def capability(self, agent_id: str) -> DeliveryCapability:
        raise NotImplementedError

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        raise NotImplementedError
