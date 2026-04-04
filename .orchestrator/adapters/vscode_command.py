from __future__ import annotations

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult


class VSCodeCommandAdapter(BaseAdapter):
    name = "vscode_command"

    def capability(self, agent_id: str) -> DeliveryCapability:
        return DeliveryCapability(
            adapter=self.name,
            supported=False,
            requires_manual_confirmation=True,
            can_auto_deliver=False,
            can_auto_approve_edits=False,
            delivery_mode="vscode_command",
            verified="unknown",
            host="VS Code extension command surface",
            notes="No verified shell-level command bridge is installed for extension commands in this workspace.",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        capability = self.capability(request.agent_id)
        return DeliveryResult(
            ok=False,
            adapter=self.name,
            mode="vscode_command",
            target=request.agent_id,
            auto_delivered=False,
            manual_confirmation_required=True,
            error=capability.notes,
            notes="Install a thin command bridge extension if you want shell-triggered VS Code command delivery later.",
        )
