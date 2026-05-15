"""
Governance Service — Audit Log

Appends approval-decision events to:
  - An append-only JSONL file on disk (always, when audit_log_path is set)
  - Firestore (when GCP_PROJECT_ID env var is present and the SDK is available)

The audit log is append-only.  Each line is a self-contained JSON event.

Event types emitted by the governance service:
  approval_decision_created
  approval_decision_state_changed   (review accepted)
  approval_decision_decided         (approved / rejected / approved_with_conditions)
  approval_decision_revoked
"""
from __future__ import annotations

import datetime
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

_UTC = datetime.timezone.utc

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
AUDIT_COLLECTION = os.getenv("GOVERNANCE_AUDIT_COLLECTION", "governance_audit")


def _firestore_client():
    if not GCP_PROJECT_ID:
        return None
    try:
        from google.cloud import firestore  # type: ignore
        return firestore.Client(project=GCP_PROJECT_ID)
    except ImportError:
        return None


def append_audit_event(
    event_type: str,
    decision_id: str,
    actor_id: Optional[str],
    actor_role: Optional[str],
    target_type: Optional[str],
    target_id: Optional[str],
    detail: Optional[Dict[str, Any]] = None,
    audit_log_path: Optional[str] = None,
) -> str:
    """Append a single audit event.  Returns the generated event_id."""
    event_id = str(uuid.uuid4())
    event: Dict[str, Any] = {
        "event_id":    event_id,
        "event_type":  event_type,
        "decision_id": decision_id,
        "actor_id":    actor_id,
        "actor_role":  actor_role,
        "target_type": target_type,
        "target_id":   target_id,
        "detail":      detail or {},
        "timestamp":   datetime.datetime.now(_UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
    }

    # Append to JSONL
    if audit_log_path:
        Path(audit_log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(audit_log_path, "a") as fh:
            fh.write(json.dumps(event) + "\n")

    # Firestore (best-effort)
    try:
        db = _firestore_client()
        if db is not None:
            db.collection(AUDIT_COLLECTION).document(event_id).set(event)
    except Exception:
        pass  # Firestore failure must never break the request path

    return event_id
