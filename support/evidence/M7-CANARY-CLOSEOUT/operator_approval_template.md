# Operator Approval — M7 Canary Promotion

**Task:** M7-CANARY-CLOSEOUT  
**Packet:** `support/evidence/M7-CANARY-CLOSEOUT/promotion_readiness_packet.json`  
**Environment:** canary  
**Gate:** `operator_approval`

## Instructions

This template must be completed and committed before `can_proceed` may be set to `true`
in the PromotionReadinessPacket. The risk-owner approval must also be recorded before
the operator approval is acted upon.

Do **not** enable `BROKER_PRODUCTION_LIVE_ENABLED` or `CAPITAL_BINDING_LIVE_ENABLED`
until both this template and the risk-owner approval template are signed.

## Evidence Summary

| Evidence Key | Path | Status |
|---|---|---|
| broker_sandbox_smoke_consumed | `docs/deployment/evidence/ep5-broker-tw-002/20260517T054748Z/sandbox-smoke/summary.json` | passed |
| shioaji_sandbox_evidence_packet_consumed | `docs/deployment/evidence/ep5-broker-tw-002/20260517T054748Z/evidence-packet/shioaji-sandbox-evidence-packet.json` | passed |
| canary_activation_gate_refs_present | `docs/deployment/evidence/execution-sandbox-canary-activation-ready/20260504T045936Z/README.md` | present |

## Operator Readiness Checklist

- [ ] I have verified that the risk-owner approval template has been signed.
- [ ] I have reviewed the broker sandbox smoke evidence and confirm all acceptance checks passed.
- [ ] I confirm the system is in a safe state for canary activation.
- [ ] Rollback procedure is documented and available (ref: `ROLLBACK_AND_POSITION_SEMANTICS.md`).
- [ ] Kill switch is tested and reachable (ref: `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`).
- [ ] Observation window is defined: _____ hours from activation.
- [ ] Canary traffic fraction is configured: _____ %.
- [ ] Alert thresholds are confirmed in the monitoring dashboard.
- [ ] I confirm that `BROKER_PRODUCTION_LIVE_ENABLED` will remain `false` at canary activation.
- [ ] I confirm that `CAPITAL_BINDING_LIVE_ENABLED` will remain `false` at canary activation.

## Approval Signature

**Operator Name:** ___________________________  
**Role / Title:** ___________________________  
**Date (UTC):** ___________________________  
**Signature / Token:** ___________________________  

**Observation Window:** _____ hours  
**Canary Traffic Fraction:** _____%  
**Rollback Trigger Threshold:** ___________________________  

**Notes:**

> (Optional: record any conditions, runbook references, or escalation contacts.)

---

*After signing, update `promotion_readiness_packet.json` field `operator_approval_recorded` to `true`
and set `can_proceed` to `true` only if `risk_owner_approval_recorded` is also `true`.*
