# HG-PENDING-DECISIONS — Canonical pending HumanGateDecision store

Created 2026-05-21 as V3 dispatch follow-up. Provides the 5 pending HumanGateDecision
records the blueprint §17 acceptance condition #12 ("HumanGateDecision records exist
for all live activation placeholders") requires.

## Files

- `decisions.json` — `HumanGateDecisionStore` JSON file. Five records, all with
  `status=blocked` + `can_proceed=false` (auto-derived because readiness
  `base_ready()` is false until humans sign). The blueprint phrasing "pending"
  maps to schema `blocked` per `decision_model.calculated_status()`.
- `seed_pending_decisions.py` — One-shot seeder. Idempotent (skips existing
  decision_ids). Run with:
  `PYTHONPATH=/home/lupin/code/pantheon python3 support/evidence/HG-PENDING-DECISIONS/seed_pending_decisions.py`

## Mapping

| Placeholder task | decision_id | target_type |
|---|---|---|
| BLA-LIVE-001-V2 | HGD-BLA-LIVE-001-V2 | broker_live_activation |
| CBL-LIVE-001-V2 | HGD-CBL-LIVE-001-V2 | capital_binding_live |
| HA-PROD-001-V2 | HGD-HA-PROD-001-V2 | bff_ha_cutover |
| PROD-WRITES-001-V2 | HGD-PROD-WRITES-001-V2 | production_real_writes_enable |
| LIVE-SCALE-001-V2 | HGD-LIVE-SCALE-001-V2 | live_scale_up |

All five share `required_roles=("risk_owner","operator")` and
`can_proceed_input.blocking_reasons=("pending_human_go_no_go",)`.

## For auditors (BPC-001-V2)

Acceptance check for blueprint §17 condition 12 is satisfied when:

1. Each placeholder task above exists in `ai-status.json` with
   `task_class=human_gate`, `non_dispatchable=true`, `allowed_workers=[]`,
   `gate_status=pending_human_go_no_go`.
2. A matching record exists in `decisions.json` with the corresponding
   `target_id` and `status in {pending, blocked}` (both indicate
   "not yet approved").
3. Records validate against the v1 schema
   (`HumanGateDecision.v1` via `validate_decision`).

No production flag is touched by the existence of these records.
