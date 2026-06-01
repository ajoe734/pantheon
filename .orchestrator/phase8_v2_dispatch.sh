#!/usr/bin/env bash
set -euo pipefail

# Dispatcher for Phase 8 Track A — Pantheon design blueprint supplement 2026-05-19.
# Adds 33 -V2 tasks via scripts/ai-status.sh assign. See:
#   docs/02-architecture/consensus/sessions/phase8-2026-05-19-pantheon-live-ready-engineering/execution-materialization.md
#
# One-shot script. Safe to delete after success.
#
# Usage: AI_NAME=Claude bash .orchestrator/phase8_v2_dispatch.sh

cd "$(dirname "${BASH_SOURCE[0]}")/.."

AUTO_CREATED_BY="phase8-2026-05-19-pantheon-live-ready-engineering"

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
  printf '==> %s\n' "$id"
  ./scripts/ai-status.sh assign "$id" "$owner" "$reviewer" "$title"
}

assign_human_gate() {
  local id="$1" owner="$2" reviewer="$3" title="$4" summary="$5" waiting_csv="$6"
  export TASK_TITLE="$title"
  export TASK_SUMMARY_ZH="$summary"
  export TASK_PHASE="Phase 8 / EPIC-LIVE-GATE"
  export TASK_DEPENDS_ON=""
  export TASK_ARTIFACTS=""
  export TASK_ACCEPTANCE="Human risk-owner + operator signoff,Preconditions satisfied,Activation logged in audit chain"
  export TASK_CLASS=human_gate
  printf '==> %s (human_gate)\n' "$id"
  ./scripts/ai-status.sh assign "$id" "$owner" "$reviewer" "$title"
  # Mark blocked with waiting_for via blocker subcommand
  AI_NAME=Claude ./scripts/ai-status.sh blocker "$id" "Awaiting: $waiting_csv" "Human activation gate; not engineering. Preconditions: $waiting_csv" || true
}

# ===== EPIC-EP5 (10) =====
assign_primary "EP5-001-V2" "Codex" "Codex2" \
  "PromotionReadinessPacket schema (2026-05-19 supplement A2.1)" \
  "Implement PromotionReadinessPacket data model with evidence/approval/flags/blocking_reasons subtrees per Part A2.1 of the 2026-05-19 design blueprint supplement." \
  "" \
  "services/governance/promotion_readiness/packet_model.py,tests/governance/test_promotion_readiness_packet.py" \
  "Phase 8 / EPIC-EP5"

assign_primary "EP5-002-V2" "Codex" "Codex2" \
  "Readiness validator + blocking_reasons" \
  "Validator that takes a PromotionReadinessPacket and produces blocking_reasons. Missing evidence, missing approvals, expired/revoked approvals, conflicting flags all produce typed blocking_reasons." \
  "EP5-001-V2" \
  "services/governance/promotion_readiness/validator.py,tests/governance/test_promotion_readiness_validator.py" \
  "Phase 8 / EPIC-EP5"

assign_primary "EP5-003-V2" "Codex" "Codex2" \
  "Signoff record API (with HumanGateDecision schema)" \
  "Implements POST /api/v1/ep5/readiness-packets/{id}/risk-owner-signoff and /operator-signoff endpoints; persists signoff with TTL. Also delivers HumanGateDecision schema per Part H3 used by these endpoints." \
  "EP5-001-V2" \
  "services/governance/promotion_readiness/signoff_api.py,services/governance/human_gate/decision_model.py,tests/governance/test_signoff_api.py" \
  "Phase 8 / EPIC-EP5"

assign_primary "EP5-004-V2" "Codex" "Codex2" \
  "Revoke/expire semantics + evidence-hash binding" \
  "Implements POST /api/v1/ep5/readiness-packets/{id}/revoke; signature TTL expiry detection; evidence-hash binding (changing packet evidence invalidates prior signoff). Revoked/expired blocks can_proceed." \
  "EP5-003-V2" \
  "services/governance/promotion_readiness/revoke_expire.py,services/governance/human_gate/signature_lifecycle.py,tests/governance/test_revoke_expire.py" \
  "Phase 8 / EPIC-EP5"

assign_primary "EP5-005-V2" "Codex" "Codex2" \
  "EP5ProofPacket generator (Part A2.2)" \
  "Generator emits EP5ProofPacket from a canary run: runtime, proof flags (canary_runtime_started, runtime_heartbeat_received, order_route_mode, telemetry_ingested, etc.), result." \
  "EP5-001-V2" \
  "services/governance/ep5_proof/packet_generator.py,tests/governance/test_ep5_proof_generator.py" \
  "Phase 8 / EPIC-EP5"

assign_primary "EP5-006-V2" "Codex" "Codex2" \
  "Canary dry-run command (no live side effects)" \
  "Implements POST /api/v1/ep5/proofs/dry-run. Runs the canary execution path in validate_only or sandbox mode; asserts live_capital_side_effects=false." \
  "EP5-005-V2" \
  "services/governance/ep5_proof/dry_run.py,tests/governance/test_ep5_dry_run.py" \
  "Phase 8 / EPIC-EP5"

assign_primary "EP5-007-V2" "Codex" "Codex2" \
  "Rollback drill harness" \
  "Harness for executing a rollback drill and producing rollback_drill_completed evidence into EP5ProofPacket. Includes the drill runbook reference." \
  "EP5-005-V2" \
  "services/governance/ep5_proof/rollback_drill_harness.py,docs/operations/rollback_drill_runbook.md,tests/governance/test_rollback_drill_harness.py" \
  "Phase 8 / EPIC-EP5"

assign_primary "EP5-008-V2" "Codex" "Codex2" \
  "Kill-switch demo harness" \
  "Harness for executing the kill-switch demo and producing kill_switch_demo_completed evidence into EP5ProofPacket." \
  "EP5-005-V2" \
  "services/governance/ep5_proof/kill_switch_harness.py,docs/operations/kill_switch_demo_runbook.md,tests/governance/test_kill_switch_harness.py" \
  "Phase 8 / EPIC-EP5"

assign_primary "EP5-009-V2" "Codex2" "Codex" \
  "Canary observation report builder" \
  "Builds the canary observation report (telemetry refs + audit refs + incident refs + reconciliation status). Produces evidence_refs for EP5ProofPacket." \
  "EP5-005-V2,EP5-007-V2,EP5-008-V2" \
  "services/governance/ep5_proof/observation_report.py,tests/governance/test_observation_report.py" \
  "Phase 8 / EPIC-EP5"

assign_primary "EP5-010-V2" "Codex2" "Codex" \
  "EP5 closeout renderer (Markdown + JSON)" \
  "Renders the EP5 closeout packet as both JSON and reviewer-friendly Markdown. Final closeout artifact for the EP5 canary proof." \
  "EP5-002-V2,EP5-005-V2,EP5-009-V2" \
  "services/governance/ep5_proof/closeout_renderer.py,docs/deployment/evidence/ep5-canary/README.md,tests/governance/test_closeout_renderer.py" \
  "Phase 8 / EPIC-EP5"

# ===== EPIC-LSP (6) =====
assign_primary "LSP-001-V2" "Codex2" "Claude" \
  "CI wrapper around audit script" \
  "Wraps the strict-publish audit invocation in a CI workflow with the required env vars (VITE_BFF_MODE=live, VITE_BFF_FALLBACK=strict, VITE_BFF_REAL_WRITES=false)." \
  "" \
  "scripts/lovable/ci_strict_publish_audit.sh,.github/workflows/strict-publish-audit.yml" \
  "Phase 8 / EPIC-LSP"

assign_primary "LSP-002-V2" "Claude" "Claude2" \
  "Browser probe runner" \
  "Headless browser hits the deployed URL, probes /health and /bff/me, asserts no silent fallback to mock seed." \
  "" \
  "scripts/lovable/browser_probe.py,tests/lovable/test_browser_probe.py" \
  "Phase 8 / EPIC-LSP"

assign_primary "LSP-003-V2" "Codex2" "Claude" \
  "Hosted bundle hash recorder" \
  "Script fetches deployed bundles (index.html + JS chunks) and records SHA-256 hashes into the audit packet." \
  "" \
  "scripts/lovable/capture_bundle_hashes.py,tests/lovable/test_capture_bundle_hashes.py" \
  "Phase 8 / EPIC-LSP"

assign_primary "LSP-004-V2" "Codex2" "Claude" \
  "Forbidden runtime path scanner" \
  "Scanner reads hosted JS bundles and fails if any forbidden signal is present (per Part E4): /mocks/, seed., mockSeed, silent fallback markers, VITE_BFF_FALLBACK=auto, local seed hydration." \
  "" \
  "scripts/lovable/forbidden_path_scanner.py,tests/lovable/test_forbidden_path_scanner.py" \
  "Phase 8 / EPIC-LSP"

assign_primary "LSP-005-V2" "Codex2" "Claude" \
  "Final audit evidence packet generator" \
  "Orchestrates LSP-002/003/004 against the real Lovable deployment URL; emits strict-publish-audit.json and strict-publish-audit.md per Part E2." \
  "LSP-001-V2,LSP-002-V2,LSP-003-V2,LSP-004-V2" \
  "scripts/lovable/strict_publish_audit.py,support/evidence/lsp-final-audit/strict-publish-audit.json,support/evidence/lsp-final-audit/strict-publish-audit.md" \
  "Phase 8 / EPIC-LSP"

assign_primary "LSP-006-V2" "Codex2" "Claude" \
  "Publish gate checker" \
  "Gate checker: refuses to mark the publish complete unless strict-publish-audit passed. Wired into release pipeline as a required check." \
  "LSP-005-V2" \
  "scripts/lovable/publish_gate_checker.py,tests/lovable/test_publish_gate_checker.py" \
  "Phase 8 / EPIC-LSP"

# ===== EPIC-RES-ACT (7) =====
assign_primary "RES-ACT-001-V2" "Codex" "Gemini" \
  "Production data proof schema (generic across adapters)" \
  "Implements ProductionDataProof schema per Part F2/F3. Generic across Qlib / TRL / FinRL / W&B / etc. Per-adapter evidence is captured as instances." \
  "" \
  "services/governance/research_activation/production_data_proof.py,tests/governance/test_production_data_proof.py" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "RES-ACT-002-V2" "Codex" "Gemini" \
  "PIT / license / freshness checker" \
  "Validator for production data proofs: PIT correctness, license entitlement, freshness, durable storage, audit ref." \
  "RES-ACT-001-V2" \
  "services/governance/research_activation/pit_license_freshness.py,tests/governance/test_pit_license_freshness.py" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "RES-ACT-003-V2" "Codex" "Codex2" \
  "Candidate artifact admission gate" \
  "Admission gate at the boundary between research adapters and registry. Admits candidate_packet + registry_admission_packet. Rejects orders, runtime binding mutations, capital binding mutations, deployment stage mutations per Part F3." \
  "RES-ACT-001-V2" \
  "services/governance/research_activation/admission_gate.py,tests/governance/test_admission_gate.py" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "RES-ACT-004-V2" "Codex" "Gemini" \
  "Repeated OOS evidence runner" \
  "Runner that executes rolling out-of-sample validation across configured windows; produces R5 evidence per the activation tier table." \
  "RES-ACT-001-V2" \
  "services/governance/research_activation/oos_runner.py,tests/governance/test_oos_runner.py" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "RES-ACT-005-V2" "Codex" "Codex2" \
  "No-order-route scanner (generic)" \
  "Static + dynamic proof that research adapters do not call any broker order route. Includes code-path scan + integration test asserting empty broker outbox after a training step. Generic across FinRL/RLlib/Qlib/TRL/etc." \
  "" \
  "services/governance/research_activation/no_order_route_scanner.py,tests/integrations/test_research_no_order_route.py" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "RES-ACT-006-V2" "Codex" "Codex2" \
  "Governance review handoff packet" \
  "Packet that hands off a candidate from R5 to the governance review (R6). Includes admission gate output + OOS evidence + lineage." \
  "RES-ACT-003-V2,RES-ACT-004-V2" \
  "services/governance/research_activation/handoff_packet.py,tests/governance/test_research_handoff_packet.py" \
  "Phase 8 / EPIC-RES-ACT"

assign_primary "WNB-ACT-001-V2" "Codex" "Gemini" \
  "W&B credentialed sync proof" \
  "Demonstrates W&B online sync works with credentialed access. Asserts W&B does not replace Pantheon registry as truth (registry remains the artifact-admission system of record)." \
  "" \
  "integrations/wandb/credentialed_sync_proof.md,tests/integrations/test_wandb_sync.py" \
  "Phase 8 / EPIC-RES-ACT"

# ===== EPIC-OODA-CANARY (5) =====
assign_primary "OODA-CANARY-001-V2" "Codex" "Codex2" \
  "Canary OODA packet schema (Part G2)" \
  "Implements CanaryOodaPacket data model per Part G2: stages (observe/orient/decide/act/learn), assertions, status." \
  "" \
  "services/ooda/canary_packet_model.py,tests/ooda/test_canary_packet_schema.py" \
  "Phase 8 / EPIC-OODA-CANARY"

assign_primary "OODA-CANARY-002-V2" "Codex" "Codex2" \
  "Canary transition tests" \
  "Tests that exercise observe→orient→decide→act→learn stage transitions in canary scope." \
  "OODA-CANARY-001-V2" \
  "tests/ooda/test_canary_transitions.py" \
  "Phase 8 / EPIC-OODA-CANARY"

assign_primary "OODA-CANARY-003-V2" "Codex" "Codex2" \
  "Canary telemetry-to-evolution test" \
  "End-to-end test: canary telemetry event → incident → postmortem → evolution proposal. Asserts live capital scope limited / human gate valid / validation errors empty." \
  "OODA-CANARY-001-V2" \
  "tests/e2e/test_canary_telemetry_to_evolution.py" \
  "Phase 8 / EPIC-OODA-CANARY"

assign_primary "OODA-CANARY-004-V2" "Codex" "Codex2" \
  "Canary rollback drill linkage" \
  "Links CanaryOodaPacket.act.rollback_drill_ref to the EP5 rollback drill output. Asserts rollback drill is referenced and validated." \
  "OODA-CANARY-001-V2,EP5-007-V2" \
  "services/ooda/canary_rollback_drill_linkage.py,tests/ooda/test_canary_rollback_linkage.py" \
  "Phase 8 / EPIC-OODA-CANARY"

assign_primary "OODA-CANARY-005-V2" "Codex2" "Codex" \
  "Canary packet closure renderer" \
  "Renders the closed CanaryOodaPacket as both JSON and reviewer-friendly Markdown. Final closeout artifact for the canary OODA proof." \
  "OODA-CANARY-001-V2,OODA-CANARY-002-V2,OODA-CANARY-003-V2,OODA-CANARY-004-V2" \
  "services/ooda/canary_closure_renderer.py,tests/ooda/test_canary_closure_renderer.py" \
  "Phase 8 / EPIC-OODA-CANARY"

# ===== EPIC-HG (2 residual) =====
assign_primary "HG-005-V2" "Codex" "Codex2" \
  "Human gate audit log projection (AuditAction)" \
  "Project every HumanGateDecision state change (submit / revoke / expire) into AuditAction immutable log. Audit entry includes trace_id and correlation_id. Cross-cutting concern not absorbed by EP5-003/004." \
  "EP5-003-V2" \
  "services/audit/projections/human_gate.py,tests/audit/test_human_gate_projection.py" \
  "Phase 8 / EPIC-HG"

assign_primary "HG-006-V2" "Claude" "Claude2" \
  "Management Console UI read model for human gate status" \
  "Read-only Management Console surface displaying HumanGateDecision status, pending signatures, blocking reasons, expiry countdown. No write actions; submit/revoke happen via existing approval flow." \
  "EP5-003-V2" \
  "apps/management/src/screens/HumanGate/HumanGateStatus.tsx,apps/management/src/screens/HumanGate/index.ts,tests/management/HumanGateStatus.test.tsx" \
  "Phase 8 / EPIC-HG"

# ===== EPIC-LIVE-GATE (3 human_gate placeholders) =====
assign_human_gate "BLA-LIVE-001-V2" "Human/Ops" "Codex" \
  "Broker production live enable (human gate)" \
  "Not engineering. Represents the human risk-owner + operator signoff event that flips BROKER_PRODUCTION_LIVE_ENABLED. Awaits BLA engineering completion + dual approval." \
  "risk_owner_signoff,operator_signoff,BLA-track-engineering-complete"

assign_human_gate "CBL-LIVE-001-V2" "Human/Ops" "Codex" \
  "Capital binding live enable (human gate)" \
  "Not engineering. Represents the human signoff event that flips CAPITAL_BINDING_LIVE_ENABLED. Must come after broker live; awaits CBL engineering completion + dual approval + BLA-LIVE-001-V2 done." \
  "risk_owner_signoff,operator_signoff,CBL-track-engineering-complete,BLA-LIVE-001-V2-done"

assign_human_gate "HA-PROD-001-V2" "Human/Ops" "Codex" \
  "Production HA cutover (human gate)" \
  "Not engineering. Represents the production BFF HA cutover authorization. Requires infra-decision-maker signoff in addition to risk-owner + operator." \
  "infra_decision_maker_signoff,HA-track-engineering-complete"

echo
echo "Dispatch complete. Running sync to refresh derived state."
./scripts/ai-status.sh sync || true
echo
echo "Final task count:"
python3 -c "import json; s=json.load(open('ai-status.json')); print('tasks:', len(s['tasks']))"
