from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, Iterator, List, Optional

from .models import CommandStatus, CommandType, ObjectType, TargetObject

log = logging.getLogger(__name__)


class CommandStore:
    def __init__(self, file_path: str = "commands.jsonl"):
        self.file_path = file_path
        self._lock = RLock()
        self._cache: Optional[List[Dict[str, Any]]] = None
        parent = os.path.dirname(os.path.abspath(self.file_path))
        os.makedirs(parent, exist_ok=True)
        # Initialize the file if it doesn't exist
        if not os.path.exists(self.file_path):
            with open(self.file_path, "w", encoding="utf-8") as f:
                f.flush()
                os.fsync(f.fileno())

    @contextmanager
    def serialized_transaction(self) -> Iterator[None]:
        """Serialize a multi-step admission check and its durable write."""
        with self._lock:
            yield

    def _save_command(self, command: Dict[str, Any]):
        with self._lock:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(command) + "\n")
                f.flush()
                os.fsync(f.fileno())
            if self._cache is not None:
                self._cache.append(command)

    def _get_all_commands(self) -> List[Dict[str, Any]]:
        with self._lock:
            if self._cache is not None:
                return list(self._cache)
            commands = []
            if not os.path.exists(self.file_path):
                self._cache = commands
                return list(commands)
            with open(self.file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        commands.append(json.loads(line))
            self._cache = commands
            return list(commands)

    def _update_commands(self, commands: List[Dict[str, Any]]):
        with self._lock:
            temp_path = f"{self.file_path}.{uuid.uuid4().hex}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                for cmd in commands:
                    f.write(json.dumps(cmd) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.file_path)
            self._cache = list(commands)

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

    def submit_terminal_command(
        self,
        command_id: str,
        command_type: CommandType,
        target: TargetObject,
        submitted_at: str,
        params: Dict[str, Any],
        audit_context: Dict[str, Any],
        foundation_context: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append one already-complete command record without a crash window."""
        record = {
            "command_id": command_id,
            "type": command_type.value,
            "target": target.model_dump(),
            "submitted_at": submitted_at,
            "status": CommandStatus.EXECUTED.value,
            "params": params,
            "audit": audit_context,
            "foundation": foundation_context,
            "result": result,
            "error": None,
        }
        self._save_command(record)
        return record

    def submit_terminal_command_if_no_active_target(
        self,
        command_id: str,
        command_type: CommandType,
        target: TargetObject,
        submitted_at: str,
        params: Dict[str, Any],
        audit_context: Dict[str, Any],
        foundation_context: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        with self._lock:
            active = self.get_active_commands_for_target(target.type.value, target.id)
            if active:
                return None, active[0]
            return self.submit_terminal_command(
                command_id,
                command_type,
                target,
                submitted_at,
                params,
                audit_context,
                foundation_context,
                result,
            ), None

    def submit_command_if_no_active_target(
        self,
        command_id: str,
        command_type: CommandType,
        target: TargetObject,
        submitted_at: str,
        params: Dict[str, Any],
        audit_context: Dict[str, Any],
        foundation_context: Optional[Dict[str, Any]] = None,
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        with self._lock:
            active = self.get_active_commands_for_target(target.type.value, target.id)
            if active:
                return None, active[0]
            return self.submit_command(
                command_id,
                command_type,
                target,
                submitted_at,
                params,
                audit_context,
                foundation_context,
            ), None

    def submit_command_with_confirm_token_redeem_if_no_active_target(
        self,
        command_id: str,
        command_type: CommandType,
        target: TargetObject,
        submitted_at: str,
        params: Dict[str, Any],
        audit_context: Dict[str, Any],
        foundation_context: Optional[Dict[str, Any]],
        *,
        confirm_token_id: str,
        confirmation_id: str,
        confirmation_command_id: str,
        confirmation_idempotency_key: str,
        confirmation_request_hash: str,
        operator_id: str,
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Atomically persist a guarded command and consume its confirm token.

        The caller must hold ``serialized_transaction`` while revalidating the
        token immediately before invoking this method.  The re-entrant lock
        keeps that validation, the active-target check, and this single atomic
        file replacement in one critical section.
        """
        with self._lock:
            active = self.get_active_commands_for_target(target.type.value, target.id)
            if active:
                return None, active[0]

            command_record = {
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
            confirmation_foundation = {
                "idempotency_record": {
                    "idempotency_key": confirmation_idempotency_key,
                    "request_hash": confirmation_request_hash,
                    "status": "succeeded",
                    "result_ref": f"command:{command_id}",
                }
            }
            confirmation_params = {
                "confirm_token": confirm_token_id,
                "command_id": command_id,
                "confirmation_id": confirmation_id,
                "confirmed_at": submitted_at,
                "confirmed_by": operator_id,
            }
            confirmation_record = {
                "command_id": confirmation_command_id,
                "type": CommandType.CONFIRM_TOKEN_REDEEM.value,
                "target": {
                    "type": ObjectType.CONFIRM_TOKEN.value,
                    "id": confirm_token_id,
                },
                "submitted_at": submitted_at,
                "status": CommandStatus.EXECUTED.value,
                "params": confirmation_params,
                "audit": {
                    "actor": operator_id,
                    "reason": "Confirm token consumed by guarded command admission",
                    **confirmation_params,
                    "foundation": confirmation_foundation,
                },
                "foundation": confirmation_foundation,
                "result": {
                    "status": "redeemed",
                    **confirmation_params,
                },
                "error": None,
            }

            commands = self._get_all_commands()
            commands.extend((command_record, confirmation_record))
            self._update_commands(commands)
            return command_record, None

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
        with self._lock:
            commands = self._get_all_commands()
            updated = False
            for i, cmd in enumerate(commands):
                if cmd["command_id"] == command_id:
                    commands[i]["status"] = status.value
                    if result is not None:
                        commands[i]["result"] = result
                    if error is not None:
                        commands[i]["error"] = error
                    elif status in {
                        CommandStatus.SUBMITTED,
                        CommandStatus.PROCESSING,
                        CommandStatus.EXECUTED,
                    }:
                        # A retry must not retain the terminal error from its
                        # previous attempt after it is re-queued or succeeds.
                        commands[i]["error"] = None
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
