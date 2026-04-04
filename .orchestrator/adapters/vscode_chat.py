from __future__ import annotations

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult
from common import command_exists, run_command


class VSCodeChatAdapter(BaseAdapter):
    name = "vscode_chat"

    def capability(self, agent_id: str) -> DeliveryCapability:
        if not command_exists("code"):
            return DeliveryCapability(
                adapter=self.name,
                supported=False,
                requires_manual_confirmation=True,
                can_auto_deliver=False,
                can_auto_approve_edits=False,
                delivery_mode="vscode_chat",
                verified="unavailable",
                host="VS Code CLI",
                notes="`code` CLI is not available.",
            )

        help_result = run_command(["code", "chat", "--help"])
        output = (help_result.stdout or "") + (help_result.stderr or "")
        supported = "Usage: code chat" in output
        notes = "Verified `code chat` subcommand." if supported else "`code chat` subcommand is not exposed by this VS Code CLI build."
        return DeliveryCapability(
            adapter=self.name,
            supported=supported,
            requires_manual_confirmation=not supported,
            can_auto_deliver=False,
            can_auto_approve_edits=False,
            delivery_mode="vscode_chat",
            verified="verified" if supported else "unavailable",
            host="VS Code CLI",
            notes=notes,
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        capability = self.capability(request.agent_id)
        if not capability.supported:
            return DeliveryResult(
                ok=False,
                adapter=self.name,
                mode="vscode_chat",
                target=request.agent_id,
                auto_delivered=False,
                manual_confirmation_required=True,
                error=capability.notes,
                notes="No verified `code chat` command surface in this environment.",
            )
        return DeliveryResult(
            ok=False,
            adapter=self.name,
            mode="vscode_chat",
            target=request.agent_id,
            auto_delivered=False,
            manual_confirmation_required=True,
            error="`code chat` is available but no verified command template was configured.",
            notes="Provide a command template in a local config if your VS Code build exposes a stable `code chat` syntax.",
        )
