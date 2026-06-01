#!/usr/bin/env bash
set -euo pipefail

# Round-2 dispatch for Phase 8 V2 — per design-team formal reply 2026-05-19.
# Implements:
#   1. Audit reconciliation task (GOLIVE-RESET-RECON-001)
#   2. OPS-WAVE-001..005-V2 (delivery governance)
#   3. Six RES-ACT adapter-specific child tasks
#   4. One new RES-ACT-DISCLOSURE-001-V2 (covers the re-spec'd RES-ACT-005 scope
#      that the original RES-ACT-005-V2 didn't deliver — original is already done)
#   5. Track B engineering tasks: BLA-001..010-V2, CBL-001..007-V2, HA-001..010-V2
#   6. Re-spec of EP5-003-V2 / EP5-004-V2 / RES-ACT-002/003/004/006-V2 (still todo)
#   7. LIVE-gate metadata additions (non_dispatchable, gate_status, etc.)

cd "$(dirname "${BASH_SOURCE[0]}")/.."

AUTO_CREATED_BY="phase8-2026-05-19-pantheon-live-ready-engineering-round2"

export TASK_AUTO_CREATED_BY="$AUTO_CREATED_BY"
export TASK_AUTO_GENERATED=true
export TASK_MUTATES_CANONICAL=false

ACC_PRIMARY="Schema/code matches 2026-05-19 supplement section,Unit tests cover happy path and at least one fail-closed case,Reviewer signs off via ai-status.sh approve,Artifact exists in worktree at closeout,No L1 canonical doc modified"

assign_primary() {
  local id="$1" owner="$2" reviewer="$3" title="$4" summary="$5" depends_csv="$6" artifacts_csv="$7" phase_label="$8"
  export TASK_TITLE="$title"
  export TASK_SUMMARY_ZH="$summary"
  export TASK_PHASE="$phase_label"
  export TASK_DEPENDS_ON="$depends_csv"
  export TASK_ARTIFACTS="$artifacts_csv"
  export TASK_ACCEPTANCE="$ACC_PRIMARY"
  export TASK_CLASS=primary
  unset TASK_METADATA_JSON
  printf '==> %s\n' "$id"
  ./scripts/ai-status.sh assign "$id" "$owner" "$reviewer" "$title"
}

reassign_respec() {
  local id="$1" owner="$2" reviewer="$3" title="$4" summary="$5"
  export TASK_TITLE="$title"
  export TASK_SUMMARY_ZH="$summary"
  printf '== respec %s ==\n' "$id"
  ./scripts/ai-status.sh assign "$id" "$owner" "$reviewer" "$title"
}

update_live_gate_metadata() {
  local id="$1" owner="$2" reviewer="$3" title="$4"
  export TASK_TITLE="$title"
  export TASK_CLASS=human_gate
  export TASK_METADATA_JSON='{"non_dispatchable": true, "gate_status": "pending_human_go_no_go", "allowed_workers": [], "human_required_roles": ["risk_owner", "operator"], "activation_effect": ["irreversible_or_high_risk"]}'
  printf '== metadata %s ==\n' "$id"
  ./scripts/ai-status.sh assign "$id" "$owner" "$reviewer" "$title"
  unset TASK_METADATA_JSON
}

# =====================================================================
# Phase 1: Audit reconciliation (GOLIVE-RESET-RECON-001)
# =====================================================================
export TASK_CLASS=audit_reconciliation
assign_primary "GOLIVE-RESET-RECON-001" "Claude" "Codex" \
  "Reconcile dropped 2026-05-18 GOLIVE assignments against V2 set" \
  "Audit-only reconciliation. List the 7 preserved assign log records for the rolled-back 2026-05-18 -GOLIVE batch, map each old GOLIVE intent to its V2 task id (or mark superseded). Identify any unique requirement not covered by V2; if none, mark the old GOLIVE set superseded_by: blueprint_v2_2026_05_19. blocks_execution: false." \
  "" \
  "support/audit/golive-reset-reconciliation/README.md" \
  "Phase 8 / EPIC-AUDIT-RECON"
export TASK_CLASS=primary

# =====================================================================
# Phase 2: OPS-WAVE-001..005-V2 (delivery governance)
# =====================================================================
assign_primary "OPS-WAVE-001-V2" "Codex" "Claude" \
  "Wave open guard: no-skip, cooldown, baton-owner" \
  "Guard on wave open: reject if wave number skips, reject if previous wave closed < 60 min ago, reject if actor != baton owner for open/close/freeze. Per design reply section 3 H1/H4." \
  "" \
  ".orchestrator/wave_guards.py,scripts/ai_status.py,tests/orchestrator/test_wave_open_guard.py" \
  "Phase 8 / EPIC-OPS-WAVE"

assign_primary "OPS-WAVE-002-V2" "Codex" "Claude" \
  "Freeze stage and assign blocking" \
  "Add wave state 'frozen', require freeze before close (minimum 30 min freeze duration), reject new assigns while frozen. Per design reply section 3." \
  "OPS-WAVE-001-V2" \
  "scripts/ai_status.py,tests/orchestrator/test_wave_freeze_guard.py" \
  "Phase 8 / EPIC-OPS-WAVE"

assign_primary "OPS-WAVE-003-V2" "Codex" "Claude" \
  "Chair-review wave-health check" \
  "Scan wave history; detect short cycles, skipped wave, actor mismatch, missing freeze; emit wave_health_report. Per design reply section 3." \
  "OPS-WAVE-001-V2,OPS-WAVE-002-V2" \
  ".orchestrator/chair_review_wave_health.py,tests/orchestrator/test_chair_review_wave_health.py" \
  "Phase 8 / EPIC-OPS-WAVE"

assign_primary "OPS-WAVE-004-V2" "Codex" "Claude" \
  "Release branch and publish tag discipline" \
  "Map wave to publish/vYYYY.WW.0; validate no missing wave before publish; require closeout evidence before release branch. Per design reply section 3 (NEW SCOPE vs earlier OPS-WAVE-004 in 2026-05-18 packet)." \
  "OPS-WAVE-001-V2" \
  "scripts/release_branch_discipline.py,tests/orchestrator/test_release_branch_discipline.py" \
  "Phase 8 / EPIC-OPS-WAVE"

assign_primary "OPS-WAVE-005-V2" "Codex" "Claude" \
  "Evidence retention and sidecar cleanup policy enforcement" \
  "Classify evidence retention per Part H5; archive eligible sidecars; never delete live/canary/human-gate/rollback/postmortem evidence. Per design reply section 3 (NEW SCOPE)." \
  "" \
  "scripts/evidence_retention_policy.py,tests/orchestrator/test_evidence_retention_policy.py" \
  "Phase 8 / EPIC-OPS-WAVE"

# =====================================================================
# Phase 3: RES-ACT adapter-specific child tasks (6)
# =====================================================================
assign_primary "RES-ACT-QLIB-001-V2" "Codex" "Gemini" \
  "Qlib production data proof and rolling OOS admission packet" \
  "Adapter-specific child: Qlib production data proof (entitlement, PIT, durable storage) + R5 rolling OOS admission packet. Parent: RES-ACT-001-V2." \
  "RES-ACT-001-V2" \
  "integrations/qlib/production_data_proof.md,integrations/qlib/rolling_oos_admission_packet.md" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "RES-ACT-TRL-001-V2" "Codex" "Gemini" \
  "TRL runtime preference-data proof and DPO real-backend evidence" \
  "Adapter-specific child: TRL preference dataset proof + DPO real-backend install/config evidence. Parent: RES-ACT-001-V2." \
  "RES-ACT-001-V2" \
  "integrations/trl/preference_data_proof.md,integrations/trl/dpo_backend_evidence.md" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "RES-ACT-RL-001-V2" "Codex" "Gemini" \
  "FinRL/RLlib no-order-route proof and research-only admission evidence" \
  "Adapter-specific child: FinRL/RLlib no-order-route proof + research-only admission evidence. Parent: RES-ACT-001-V2." \
  "RES-ACT-001-V2" \
  "integrations/finrl/no_order_route_proof.md,integrations/rllib/research_only_admission.md" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "RES-ACT-WANDB-001-V2" "Codex" "Gemini" \
  "W&B credentialed online sync test-project evidence" \
  "Adapter-specific child: W&B credentialed online sync proof asserting W&B does not replace Pantheon registry as truth. Parent: RES-ACT-001-V2. Note: original WNB-ACT-001-V2 delivered against earlier spec; this task is the canonical replacement child." \
  "RES-ACT-001-V2" \
  "integrations/wandb/credentialed_sync_proof.md,tests/integrations/test_wandb_sync.py" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "RES-ACT-QUANTLIB-001-V2" "Codex" "Gemini" \
  "QuantLib production pricing evidence retention and admission proof" \
  "Adapter-specific child: QuantLib production pricing evidence retention + admission proof. Parent: RES-ACT-001-V2." \
  "RES-ACT-001-V2" \
  "integrations/quantlib/pricing_evidence_retention.md,integrations/quantlib/admission_proof.md" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "RES-ACT-STAT-001-V2" "Codex" "Gemini" \
  "statsmodels cointegration production evidence and admission proof" \
  "Adapter-specific child: statsmodels cointegration production evidence + admission proof. Parent: RES-ACT-001-V2." \
  "RES-ACT-001-V2" \
  "integrations/statsmodels/cointegration_production_evidence.md,integrations/statsmodels/admission_proof.md" \
  "Phase 8 / EPIC-RES-ACT"

# =====================================================================
# Phase 4: Disclosure task (covers new RES-ACT-005 scope that the
#           originally-delivered RES-ACT-005-V2 didn't address)
# =====================================================================
assign_primary "RES-ACT-DISCLOSURE-001-V2" "Codex" "Codex2" \
  "Research backend real/stub backend disclosure report" \
  "Disclosure report listing, for each research adapter, whether it currently uses a real backend or a stub/mock. The original RES-ACT-005-V2 delivered a no-order-route scanner; this new task addresses the design-team's re-spec'd intent for RES-ACT-005 (which is now embodied here under a clean ID)." \
  "" \
  "services/governance/research_activation/disclosure_report.py,tests/governance/test_disclosure_report.py" \
  "Phase 8 / EPIC-RES-ACT"

# =====================================================================
# Phase 5: Track B — Broker Live engineering (10)
# =====================================================================
assign_primary "BLA-001-V2" "Codex" "Codex2" \
  "Broker live activation criteria JSON + validator" \
  "Implement broker_live_activation_criteria.json schema and validator per Part B2." \
  "" \
  "services/broker/live_activation/criteria.json,services/broker/live_activation/validator.py,tests/broker/test_live_activation_validator.py" \
  "Phase 8 / EPIC-BROKER-LIVE"

assign_primary "BLA-002-V2" "Codex" "Codex2" \
  "Risk-owner checklist generator (Part B3)" \
  "Generator for the 10-item risk-owner checklist per Part B3." \
  "BLA-001-V2" \
  "services/broker/live_activation/risk_owner_checklist.py,tests/broker/test_risk_owner_checklist.py" \
  "Phase 8 / EPIC-BROKER-LIVE"

assign_primary "BLA-003-V2" "Codex" "Codex2" \
  "Operator checklist generator (Part B4)" \
  "Generator for the 10-item operator checklist per Part B4." \
  "BLA-001-V2" \
  "services/broker/live_activation/operator_checklist.py,tests/broker/test_operator_checklist.py" \
  "Phase 8 / EPIC-BROKER-LIVE"

assign_primary "BLA-004-V2" "Codex" "Codex2" \
  "Rollback drill dry-run" \
  "Dry-run runner for broker rollback drill. Produces rollback evidence without live side effects." \
  "BLA-001-V2" \
  "services/broker/live_activation/rollback_drill_dry_run.py,tests/broker/test_rollback_drill_dry_run.py" \
  "Phase 8 / EPIC-BROKER-LIVE"

assign_primary "BLA-005-V2" "Codex" "Codex2" \
  "Kill-switch demo evidence collector" \
  "Evidence collector for kill-switch demo per Part B5 runbook." \
  "BLA-001-V2" \
  "services/broker/live_activation/kill_switch_evidence.py,tests/broker/test_kill_switch_evidence.py" \
  "Phase 8 / EPIC-BROKER-LIVE"

assign_primary "BLA-006-V2" "Codex" "Codex2" \
  "Broker credential vault readiness spec" \
  "BrokerCredentialReadiness schema + vault readiness spec per Part B5." \
  "BLA-001-V2" \
  "services/broker/live_activation/credential_readiness.py,docs/operations/broker_credential_vault_readiness.md,tests/broker/test_credential_readiness.py" \
  "Phase 8 / EPIC-BROKER-LIVE"

assign_primary "BLA-007-V2" "Codex" "Codex2" \
  "First-week observation report builder" \
  "Builder for the 13-check first-week observation report per Part B5 runbook." \
  "BLA-001-V2" \
  "services/broker/live_activation/first_week_observation.py,docs/operations/first_week_observation_window.md,tests/broker/test_first_week_observation.py" \
  "Phase 8 / EPIC-BROKER-LIVE"

assign_primary "BLA-008-V2" "Codex" "Codex2" \
  "Approval revoke / withdraw model" \
  "Model for risk-owner / operator approval revoke and withdraw lifecycle, tied to the HumanGateDecision schema in EP5-003-V2/EP5-004-V2." \
  "BLA-001-V2,EP5-004-V2" \
  "services/broker/live_activation/approval_revoke_withdraw.py,tests/broker/test_approval_revoke_withdraw.py" \
  "Phase 8 / EPIC-BROKER-LIVE"

assign_primary "BLA-009-V2" "Codex" "Codex2" \
  "Live activation simulator" \
  "Simulator that walks through the full broker live activation criteria + dual gate without flipping production flags. Asserts no live side effects." \
  "BLA-001-V2,BLA-002-V2,BLA-003-V2" \
  "services/broker/live_activation/simulator.py,tests/broker/test_live_activation_simulator.py" \
  "Phase 8 / EPIC-BROKER-LIVE"

assign_primary "BLA-010-V2" "Codex" "Codex2" \
  "Broker go/no-go dashboard" \
  "Dashboard read model showing broker live readiness state, checklist progress, blocking reasons." \
  "BLA-001-V2,BLA-002-V2,BLA-003-V2" \
  "services/broker/live_activation/dashboard.py,apps/management/src/screens/BrokerGoNoGo/,tests/broker/test_dashboard.py" \
  "Phase 8 / EPIC-BROKER-LIVE"

# =====================================================================
# Phase 6: Track B — Capital Binding Live engineering (7)
# =====================================================================
assign_primary "CBL-001-V2" "Codex" "Codex2" \
  "CapitalBindingLiveReadiness schema (Part C2)" \
  "Implement CapitalBindingLiveReadiness data model with roles, required_evidence, controls, approval, result subtrees per Part C2." \
  "" \
  "services/capital/binding_live/readiness_model.py,tests/capital/test_binding_live_readiness.py" \
  "Phase 8 / EPIC-CAPITAL-BINDING-LIVE"

assign_primary "CBL-002-V2" "Codex" "Codex2" \
  "Sponsor responsibility model" \
  "SponsorPersonaResponsibility model: sponsor persona, live owner, escalation chain." \
  "CBL-001-V2" \
  "services/capital/binding_live/sponsor_responsibility.py,tests/capital/test_sponsor_responsibility.py" \
  "Phase 8 / EPIC-CAPITAL-BINDING-LIVE"

assign_primary "CBL-003-V2" "Codex" "Codex2" \
  "Conflict resolution log gate" \
  "ConflictResolutionLog schema + gate; binding cannot be enabled while open conflicts exist." \
  "CBL-001-V2" \
  "services/capital/binding_live/conflict_resolution_log.py,tests/capital/test_conflict_resolution_log.py" \
  "Phase 8 / EPIC-CAPITAL-BINDING-LIVE"

assign_primary "CBL-004-V2" "Codex" "Codex2" \
  "Binding TTL / revoke / suspend semantics" \
  "BindingTTL + BindingRevocationPolicy + suspend semantics. Binding must have TTL and revocation semantics." \
  "CBL-001-V2" \
  "services/capital/binding_live/lifecycle.py,tests/capital/test_binding_lifecycle.py" \
  "Phase 8 / EPIC-CAPITAL-BINDING-LIVE"

assign_primary "CBL-005-V2" "Codex" "Codex2" \
  "Live binding simulator" \
  "Simulator for capital binding live activation + dual gate without flipping production flags." \
  "CBL-001-V2,CBL-002-V2,CBL-003-V2,CBL-004-V2" \
  "services/capital/binding_live/simulator.py,tests/capital/test_binding_live_simulator.py" \
  "Phase 8 / EPIC-CAPITAL-BINDING-LIVE"

assign_primary "CBL-006-V2" "Codex" "Codex2" \
  "Capital binding evidence collector" \
  "Evidence collector resolving CapitalBindingLiveReadiness.required_evidence refs." \
  "CBL-001-V2" \
  "services/capital/binding_live/evidence_collector.py,tests/capital/test_binding_evidence_collector.py" \
  "Phase 8 / EPIC-CAPITAL-BINDING-LIVE"

assign_primary "CBL-007-V2" "Codex" "Codex2" \
  "Capital binding go/no-go dashboard" \
  "Dashboard read model showing capital binding live readiness state, sponsor mandate, conflict log status, TTL." \
  "CBL-001-V2,CBL-002-V2,CBL-003-V2" \
  "services/capital/binding_live/dashboard.py,apps/management/src/screens/CapitalBindingGoNoGo/,tests/capital/test_dashboard.py" \
  "Phase 8 / EPIC-CAPITAL-BINDING-LIVE"

# =====================================================================
# Phase 7: Track B — BFF HA engineering (10)
# =====================================================================
assign_primary "HA-001-V2" "Codex2" "Claude" \
  "BFF HA topology doc (Part D2)" \
  "Document the production-grade BFF HA topology per Part D2 Mermaid diagram. Includes LB, replicas, shared idempotency+audit store, SSE fanout, registry, governance, runtime manager, telemetry." \
  "" \
  "docs/bff/bff_ha_topology.md" \
  "Phase 8 / EPIC-BFF-HA"

assign_primary "HA-002-V2" "Codex2" "Claude" \
  "SLA targets JSON (Part D3)" \
  "Define dev / staging / production SLA targets per Part D3: uptime, p99 latency, SSE connections, RTO, RPO, monthly cost ceiling." \
  "" \
  "services/bff/ha/sla_targets.json,tests/bff/test_sla_targets.py" \
  "Phase 8 / EPIC-BFF-HA"

assign_primary "HA-003-V2" "Codex2" "Claude" \
  "Degraded mode matrix implementation (Part D4)" \
  "Implement the 7-row degraded mode matrix per Part D4: typed 503 responses, UI state, command guards. Strict mode: no silent fallback." \
  "HA-001-V2" \
  "services/bff/ha/degraded_mode.py,tests/bff/test_degraded_mode.py" \
  "Phase 8 / EPIC-BFF-HA"

assign_primary "HA-004-V2" "Codex2" "Claude" \
  "Failover runbook" \
  "Failover runbook covering active-passive failover demo, RTO/RPO assertion, on-call escalation." \
  "HA-001-V2" \
  "docs/operations/bff_ha_failover_runbook.md" \
  "Phase 8 / EPIC-BFF-HA"

assign_primary "HA-005-V2" "Codex2" "Claude" \
  "Observability spec" \
  "Observability spec: latency histograms, error rates, SSE connection count, idempotency cache hit ratio, audit write rate, degraded-mode count." \
  "HA-001-V2" \
  "docs/bff/bff_ha_observability_spec.md" \
  "Phase 8 / EPIC-BFF-HA"

assign_primary "HA-006-V2" "Codex2" "Claude" \
  "Cost ceiling monitor" \
  "Monitor: production monthly cost ceiling (USD 800 per SLA). Alarm path defined; auto-cap behavior documented." \
  "HA-002-V2" \
  "services/bff/ha/cost_ceiling_monitor.py,tests/bff/test_cost_ceiling_monitor.py" \
  "Phase 8 / EPIC-BFF-HA"

assign_primary "HA-007-V2" "Codex2" "Claude" \
  "Multi-replica dev PoC" \
  "Stand up BFF in 3-replica configuration in dev. Smoke test that idempotency, audit, SSE all function correctly across replicas." \
  "HA-001-V2,HA-003-V2" \
  "services/bff/ha/multi_replica_poc.md,scripts/bff/run_multi_replica_smoke.sh,tests/bff/test_multi_replica_smoke.py" \
  "Phase 8 / EPIC-BFF-HA"

assign_primary "HA-008-V2" "Codex2" "Claude" \
  "SSE Last-Event-ID replay test" \
  "Test that SSE clients can reconnect and resume from Last-Event-ID without gaps or duplicates after replica failover." \
  "HA-007-V2" \
  "tests/bff/test_sse_replay.py" \
  "Phase 8 / EPIC-BFF-HA"

assign_primary "HA-009-V2" "Codex2" "Claude" \
  "Idempotency under multi-replica test" \
  "Test that idempotency works correctly when commands hit different replicas. Same idempotency key on different replicas produces same response." \
  "HA-007-V2" \
  "tests/bff/test_idempotency_multi_replica.py" \
  "Phase 8 / EPIC-BFF-HA"

assign_primary "HA-010-V2" "Codex2" "Claude" \
  "Failover demo" \
  "Demo + evidence packet: trigger failover from replica A to replica B; assert RTO/RPO met; assert in-flight commands either succeed or fail closed (no silent loss)." \
  "HA-001-V2,HA-003-V2,HA-007-V2" \
  "scripts/bff/failover_demo.sh,support/evidence/bff-ha-failover-demo/README.md" \
  "Phase 8 / EPIC-BFF-HA"

# =====================================================================
# Phase 8: Re-spec EP5-003-V2 / EP5-004-V2 (HumanGate core absorbed)
# =====================================================================
reassign_respec "EP5-003-V2" "Codex" "Codex2" \
  "HumanGateDecision core schema + signoff API" \
  "Expanded scope per design reply section 2: HumanGateDecision schema (signature object, required roles, evidence reviewed list, evidence-hash binding, can_proceed calculation input, validation rules) + create/read/append-signature API. Replaces the original narrower 'signoff record API' scope."

reassign_respec "EP5-004-V2" "Codex" "Codex2" \
  "HumanGateDecision lifecycle operations" \
  "Expanded scope per design reply section 2: revoke, expire, withdraw, supersede, recompute can_proceed, TTL enforcement, blocking reason update. Replaces the original narrower 'revoke/expire semantics' scope."

# =====================================================================
# Phase 9: Re-spec RES-ACT-002/003/004/006-V2 (still todo, new scope)
#          RES-ACT-001 in review and RES-ACT-005 already done — left as-is.
# =====================================================================
reassign_respec "RES-ACT-002-V2" "Codex" "Codex2" \
  "Research artifact admission gate" \
  "Re-spec'd per design reply section 4. Admission gate validator enforcing artifact_state=draft/candidate and deployment_stage=none. Original todo scope (PIT/license/freshness) moves to RES-ACT-003-V2."

reassign_respec "RES-ACT-003-V2" "Codex" "Gemini" \
  "Production data entitlement and PIT validator" \
  "Re-spec'd per design reply section 4. Validator for production data: entitlement, license, PIT correctness, durable storage, freshness, audit ref."

reassign_respec "RES-ACT-004-V2" "Codex" "Codex2" \
  "No-order-route enforcement test harness" \
  "Re-spec'd per design reply section 4. Test harness asserting no research adapter calls broker order routes. Generic across all adapters; per-adapter children carry their own proofs."

reassign_respec "RES-ACT-006-V2" "Claude" "Claude2" \
  "Research activation dashboard read model" \
  "Re-spec'd per design reply section 4. Management Console read model showing research activation tier (R0-R7) per adapter, evidence presence, admission gate status."

# =====================================================================
# Phase 10: LIVE-gate metadata additions
# =====================================================================
update_live_gate_metadata "BLA-LIVE-001-V2" "Human/Ops" "Codex" \
  "Broker production live enable (human gate)"

update_live_gate_metadata "CBL-LIVE-001-V2" "Human/Ops" "Codex" \
  "Capital binding live enable (human gate)"

update_live_gate_metadata "HA-PROD-001-V2" "Human/Ops" "Codex" \
  "Production BFF HA cutover (human gate)"

# =====================================================================
echo
echo "Round-2 dispatch complete. Running sync to refresh derived state."
./scripts/ai-status.sh sync || true
echo
echo "Final state:"
python3 -c "
import json
s=json.load(open('ai-status.json'))
v2=[t for t in s['tasks'] if t['id'].endswith('-V2') or t['id']=='GOLIVE-RESET-RECON-001']
print(f'Phase 8 tasks: {len(v2)}')
from collections import Counter
c=Counter(t['status'] for t in v2)
for st,n in c.most_common():
    print(f'  {st}: {n}')
"
