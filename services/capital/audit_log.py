"""
Append-only audit log for the capital service.
"""
from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

_UTC = datetime.timezone.utc


def append_audit_event(
    *,
    event_type: str,
    resource_type: str,
    resource_id: str,
    actor_id: Optional[str],
    actor_role: Optional[str],
    detail: Optional[Dict[str, Any]] = None,
    audit_log_path: Optional[str] = None,
) -> str:
    event_id = str(uuid.uuid4())
    event = {
        "event_id": event_id,
        "event_type": event_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "detail": detail or {},
        "timestamp": datetime.datetime.now(_UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
    }
    if audit_log_path:
        Path(audit_log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(audit_log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
    return event_id
