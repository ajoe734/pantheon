"""Pure shared contract for auto-integrator unblock requests."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


REQUEST_SCHEMA = "pantheon-auto-integrator-unblock-request/v1"
REQUEST_INBOX = ".orchestrator/auto-integrator-unblock-inbox"
TASK_ID_LIMIT = 96
REQUEST_FIELDS = frozenset(
    {
        "schema", "status_root", "status_identity_sha256", "command_runtime_sha",
        "source_task_id", "source_task_generation", "unblock_task_id", "reason",
        "detail", "repository_id", "repository_slug", "pr", "head_sha", "owner",
        "reviewer",
    }
)


def task_id(source_task_id: str, reason: str) -> str:
    safe_reason = "".join(
        character if character.isalnum() else "-" for character in reason.upper()
    ).strip("-")
    value = f"INTEGRATION-UNBLOCK-{source_task_id}-{safe_reason}"
    if len(value) <= TASK_ID_LIMIT:
        return value
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()
    return f"{value[: TASK_ID_LIMIT - len(suffix) - 1].rstrip('-')}-{suffix}"


def canonical_bytes(request: Mapping[str, Any]) -> bytes:
    return json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def request_filename(request: Mapping[str, Any]) -> str:
    return f"{hashlib.sha256(canonical_bytes(request)).hexdigest()}.json"
