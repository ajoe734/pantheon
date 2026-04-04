"""
Governance — Audit Log
Appends all Agent operations to Firestore (or stdout in dev mode).

Env vars:
  GCP_PROJECT_ID              — GCP project for Firestore
  FIRESTORE_COLLECTION_STRATEGIES — collection name
"""
from __future__ import annotations

import datetime
import json
import os

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION_STRATEGIES", "agent_audit")


def _firestore_client():
    try:
        from google.cloud import firestore
        return firestore.Client(project=GCP_PROJECT_ID)
    except ImportError:
        return None


def log_event(
    agent_id: str,
    action: str,
    payload: dict,
    user_id: str | None = None,
    risk_level: str = "low",
) -> None:
    """
    Append an audit event.
    Falls back to stdout JSON when Firestore SDK is not available (dev/CI).
    """
    event = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "agent_id": agent_id,
        "user_id": user_id,
        "action": action,
        "risk_level": risk_level,
        "payload": payload,
    }
    db = _firestore_client()
    if db is not None:
        db.collection(FIRESTORE_COLLECTION).add(event)
    else:
        print("[AUDIT]", json.dumps(event))
