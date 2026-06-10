from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from models import CommandStatus, CommandType, ObjectType, TargetObject

log = logging.getLogger(__name__)


class CommandStore:
    def __init__(self, file_path: str = "commands.jsonl"):
        self.file_path = file_path
        # Initialize the file if it doesn't exist
        if not os.path.exists(self.file_path):
            with open(self.file_path, "w") as f:
                pass

    def _save_command(self, command: Dict[str, Any]):
        with open(self.file_path, "a") as f:
            f.write(json.dumps(command) + "\n")

    def _get_all_commands(self) -> List[Dict[str, Any]]:
        commands = []
        if not os.path.exists(self.file_path):
            return commands
        with open(self.file_path, "r") as f:
            for line in f:
                if line.strip():
                    commands.append(json.loads(line))
        return commands

    def _update_commands(self, commands: List[Dict[str, Any]]):
        with open(self.file_path, "w") as f:
            for cmd in commands:
                f.write(json.dumps(cmd) + "\n")

    def submit_command(
        self,
        command_id: str,
        command_type: CommandType,
        target: TargetObject,
        submitted_at: str,
        params: Dict[str, Any],
        audit_context: Dict[str, Any],
        foundation_context: Optional[Dict[str, Any]] = None,
    ):
        record = {
            "command_id": command_id,
            "type": command_type.value,
            "target": target.model_dump(),
            "submitted_at": submitted_at,
            "status": CommandStatus.SUBMITTED.value,
            "params": params,
            "audit": audit_context,
            "foundation": foundation_context,
            "result": None,
            "error": None,
        }
        self._save_command(record)
        return record

    def get_command(self, command_id: str) -> Optional[Dict[str, Any]]:
        for cmd in self._get_all_commands():
            if cmd["command_id"] == command_id:
                return cmd
        return None

    @staticmethod
    def _operator_id_from_command(command: Dict[str, Any]) -> Optional[str]:
        audit = command.get("audit") if isinstance(command.get("audit"), dict) else {}
        for key in ("operator_id", "actor", "actor_id"):
            value = str(audit.get(key) or "").strip()
            if value:
                return value

        foundation = command.get("foundation") if isinstance(command.get("foundation"), dict) else {}
        trace = foundation.get("trace_context") if isinstance(foundation.get("trace_context"), dict) else {}
        actor_ref = trace.get("actor_ref") if isinstance(trace.get("actor_ref"), dict) else {}
        value = str(actor_ref.get("actor_id") or "").strip()
        return value or None

    def get_command_by_idempotency_key(
        self,
        idempotency_key: str,
        *,
        operator_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        clean_operator_id = str(operator_id or "").strip()
        for cmd in self._get_all_commands():
            foundation = cmd.get("foundation") if isinstance(cmd.get("foundation"), dict) else {}
            record = foundation.get("idempotency_record") if isinstance(foundation.get("idempotency_record"), dict) else {}
            if record.get("idempotency_key") != idempotency_key:
                continue
            if clean_operator_id and self._operator_id_from_command(cmd) != clean_operator_id:
                continue
            return cmd
        return None

    def update_status(
        self,
        command_id: str,
        status: CommandStatus,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
        audit: Optional[Dict[str, Any]] = None,
    ):
        commands = self._get_all_commands()
        updated = False
        for i, cmd in enumerate(commands):
            if cmd["command_id"] == command_id:
                commands[i]["status"] = status.value
                if result:
                    commands[i]["result"] = result
                if error:
                    commands[i]["error"] = error
                if audit:
                    # Merge audit updates into existing audit record
                    existing = commands[i].get("audit") or {}
                    existing.update(audit)
                    commands[i]["audit"] = existing
                updated = True
                break
        if updated:
            self._update_commands(commands)
        return updated

    def get_active_commands_for_target(self, target_type: str, target_id: str) -> List[Dict[str, Any]]:
        """Return commands that are currently submitted or processing for a given target."""
        active_statuses = {CommandStatus.SUBMITTED.value, CommandStatus.PROCESSING.value}
        return [
            cmd for cmd in self._get_all_commands()
            if cmd.get("target", {}).get("type") == target_type
            and cmd.get("target", {}).get("id") == target_id
            and cmd.get("status") in active_statuses
        ]
