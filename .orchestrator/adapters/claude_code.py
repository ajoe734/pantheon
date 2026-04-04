from __future__ import annotations

from adapters.base import DeliveryCapability, DeliveryRequest, DeliveryResult
from adapters.file_inbox import FileInboxAdapter


class ClaudeCodeAdapter(FileInboxAdapter):
    name = "claude_code"

    def capability(self, agent_id: str) -> DeliveryCapability:
        return DeliveryCapability(
            adapter=self.name,
            supported=True,
            requires_manual_confirmation=True,
            can_auto_deliver=False,
            can_auto_approve_edits=True,
            delivery_mode="file_inbox",
            verified="partial",
            host="Claude Code VS Code extension + workspace inbox",
            notes="Delivery falls back to inbox files because no verified shell command can inject a message directly into Claude Code in this environment.",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        result = super().deliver(request)
        result.adapter = self.name
        result.notes = f"{result.notes}. Claude will read shared-state files after you resume the workspace conversation."
        return result
