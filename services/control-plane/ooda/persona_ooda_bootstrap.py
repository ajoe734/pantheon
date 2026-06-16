"""Bootstrap the initial OODA loop packet for a newly created persona.

When a persona is created, this module opens a ``persona_synthesis`` OODA
packet in the ``paper`` environment and persists it to the shared JSONL
store so BFF /bff/ooda/packets immediately reflects an active loop.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonl_store import DEFAULT_OODA_PACKET_STORE_PATH, OodaJsonlAppendStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def bootstrap_persona_ooda_packet(
    persona_id: str,
    capital_pool_id: str | None = None,
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create and persist the initial open OODA loop packet for *persona_id*.

    The packet has:
    - ``loop_type = persona_synthesis`` — represents the persona's research-to-proposal loop
    - ``environment = paper`` — paper-only until operator promotes to live
    - ``status = open`` — engine has not yet produced its first observation
    - ``live_capital_side_effects = False`` — enforced at packet level

    Returns the persisted packet dict.
    """
    now = _utc_now()
    packet: dict[str, Any] = {
        "packet_id": f"ooda-{uuid.uuid4().hex[:12]}",
        "loop_type": "persona_synthesis",
        "status": "open",
        "environment": "paper",
        "capital_pool_id": capital_pool_id,
        "strategy_id": None,
        "persona_ids": [persona_id],
        "observe": {
            "source_refs": [],
            "telemetry_refs": [],
            "signal_refs": [],
            "market_refs": [],
            "incident_refs": [],
            "human_feedback_refs": [],
        },
        "orient": {
            "regime_state_ref": None,
            "universe_selection_ref": None,
            "signal_inference_refs": [],
            "allocation_proposal_refs": [],
            "risk_adjudication_ref": None,
            "persona_proposal_refs": [],
            "evidence_bundle_refs": [],
        },
        "decide": {
            "approval_decision_id": None,
            "deployment_plan_id": None,
            "evolution_decision_id": None,
            "sponsor_persona_id": None,
            "decision_rationale_ref": None,
            "policy_decision_refs": [],
        },
        "act": {
            "runtime_binding_id": None,
            "command_receipt_refs": [],
            "broker_evidence_refs": [],
            "rollback_refs": [],
            "safe_mode_refs": [],
            "live_capital_side_effects": False,
        },
        "learn": {
            "telemetry_refs": [],
            "postmortem_refs": [],
            "evolution_followthrough_refs": [],
            "trainer_refs": [],
            "retrain_refs": [],
            "observation_window": None,
        },
        "audit_refs": [],
        "created_at": now,
        "updated_at": now,
        "closed_at": None,
    }

    resolved_path = Path(store_path) if store_path else DEFAULT_OODA_PACKET_STORE_PATH
    store = OodaJsonlAppendStore(resolved_path)
    store.append_packet(packet)
    return packet
