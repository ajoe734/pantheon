# Risk Owner Approval — M7 Canary Promotion

**Task:** M7-CANARY-CLOSEOUT  
**Packet:** `support/evidence/M7-CANARY-CLOSEOUT/promotion_readiness_packet.json`  
**Environment:** canary  
**Gate:** `risk_owner_approval`

## Instructions

This template must be completed and committed before `can_proceed` may be set to `true`
in the PromotionReadinessPacket. Do **not** enable `BROKER_PRODUCTION_LIVE_ENABLED` or
`CAPITAL_BINDING_LIVE_ENABLED` until both this template and the operator approval
template are signed.

## Evidence Summary

| Evidence Key | Path | Status |
|---|---|---|
| broker_sandbox_smoke_consumed | `docs/deployment/evidence/ep5-broker-tw-002/20260517T054748Z/sandbox-smoke/summary.json` | passed |
| shioaji_sandbox_evidence_packet_consumed | `docs/deployment/evidence/ep5-broker-tw-002/20260517T054748Z/evidence-packet/shioaji-sandbox-evidence-packet.json` | passed |
| canary_activation_gate_refs_present | `docs/deployment/evidence/execution-sandbox-canary-activation-ready/20260504T045936Z/README.md` | present |

## Risk Assessment Checklist

- [ ] I have reviewed the broker sandbox smoke evidence and confirm all acceptance checks passed.
- [ ] I confirm that `live_broker_fail_closed` is `pass` in the evidence packet.
- [ ] I confirm that `capital_binding_enabled = false` in the evidence packet.
- [ ] I have assessed the risk of canary activation and found it acceptable.
- [ ] I confirm that activating canary does NOT require setting `BROKER_PRODUCTION_LIVE_ENABLED=true`.
- [ ] I confirm that activating canary does NOT require setting `CAPITAL_BINDING_LIVE_ENABLED=true`.

## Approval Signature

**Risk Owner Name:** ___________________________  
**Role / Title:** ___________________________  
**Date (UTC):** ___________________________  
**Signature / Token:** ___________________________  

**Notes:**

> (Optional: record any conditions, observation windows, or restrictions on this approval.)

---

*After signing, update `promotion_readiness_packet.json` field `risk_owner_approval_recorded` to `true`
and record this file path under `gate_results[risk_owner_approval].source_ref`.*
