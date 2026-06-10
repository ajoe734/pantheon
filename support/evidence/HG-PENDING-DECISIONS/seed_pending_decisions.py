"""Seed pending HumanGateDecision records for all 5 blueprint §14.1 live placeholders.

This is a one-shot seeder run as part of the V3 dispatch follow-up. Produces a
canonical JSON-backed store at decisions.json that the BPC-001-V2 auditor (and
later operators / chair-review) can read for §17 acceptance condition 12.

Records are created with status=pending, no signatures, can_proceed=false. They
exist to prove "HumanGateDecision records exist for all live activation
placeholders"; activation is still gated on real human signature.
"""
from __future__ import annotations

from pathlib import Path

from services.governance.human_gate.decision_model import (
    CanProceedInput,
    EvidenceReviewed,
    HumanGateDecision,
    stable_hash,
    utc_now,
    validate_decision,
)
from services.governance.promotion_readiness.signoff_api import (
    HumanGateDecisionStore,
    SignoffAPI,
)


PLACEHOLDERS = [
    {
        "decision_id": "HGD-BLA-LIVE-001-V2",
        "target_type": "broker_live_activation",
        "target_id": "BLA-LIVE-001-V2",
        "target_environment": "production",
        "reason": "Broker production live enable — awaiting human go/no-go per blueprint §14.1.",
    },
    {
        "decision_id": "HGD-CBL-LIVE-001-V2",
        "target_type": "capital_binding_live",
        "target_id": "CBL-LIVE-001-V2",
        "target_environment": "production",
        "reason": "Capital binding live enable — awaiting human go/no-go per blueprint §14.1.",
    },
    {
        "decision_id": "HGD-HA-PROD-001-V2",
        "target_type": "bff_ha_cutover",
        "target_id": "HA-PROD-001-V2",
        "target_environment": "production",
        "reason": "Production BFF HA cutover — awaiting human go/no-go per blueprint §14.1.",
    },
    {
        "decision_id": "HGD-PROD-WRITES-001-V2",
        "target_type": "production_real_writes_enable",
        "target_id": "PROD-WRITES-001-V2",
        "target_environment": "production",
        "reason": "Enable production real writes — awaiting human go/no-go per blueprint §14.1.",
    },
    {
        "decision_id": "HGD-LIVE-SCALE-001-V2",
        "target_type": "live_scale_up",
        "target_id": "LIVE-SCALE-001-V2",
        "target_environment": "production",
        "reason": "Live capital scale-up — awaiting human go/no-go per blueprint §14.1.",
    },
]

REQUIRED_ROLES = ("risk_owner", "operator")
STORE_PATH = Path(__file__).resolve().parent / "decisions.json"


def build_pending(record: dict) -> HumanGateDecision:
    now = utc_now()
    blueprint_evidence = EvidenceReviewed(
        key="blueprint_section_14_1",
        evidence_hash=stable_hash({
            "doc": "Pantheon_開發團隊_藍圖完成開發規劃_2026-05-19.md",
            "section": "§14.1 human-gated tasks",
            "decision_id": record["decision_id"],
        }),
        source_ref="docs/04/pantheon_blueprint_2026-05-19.md#14-1",
        status="not_applicable",
        reviewed_at=None,
        metadata={"note": "Placeholder evidence ref for pending record; real evidence reviewed at signoff time."},
    )
    can_proceed_input = CanProceedInput(
        readiness_packet_can_proceed=False,
        blocking_reasons=("pending_human_go_no_go",),
    )
    decision = HumanGateDecision(
        decision_id=record["decision_id"],
        target_type=record["target_type"],
        target_id=record["target_id"],
        target_environment=record["target_environment"],
        required_roles=REQUIRED_ROLES,
        evidence_reviewed=(blueprint_evidence,),
        can_proceed_input=can_proceed_input,
        signatures=(),
        status="pending",
        created_at=now,
        updated_at=now,
        reason=record["reason"],
        can_proceed=False,
    )
    return validate_decision(decision)


def main() -> int:
    store = HumanGateDecisionStore(path=STORE_PATH)
    api = SignoffAPI(store=store)
    created = 0
    skipped = 0
    for record in PLACEHOLDERS:
        decision = build_pending(record)
        if store.get(decision.decision_id):
            skipped += 1
            continue
        api.create_decision(decision)
        created += 1
    print(f"store path: {STORE_PATH}")
    print(f"created: {created}, skipped (already existed): {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
