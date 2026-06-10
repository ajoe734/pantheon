#!/usr/bin/env bash
set -euo pipefail

# V3 dispatch — closes the three remaining gaps versus the 2026-05-19
# development-team blueprint (Pantheon_開發團隊_藍圖完成開發規劃_2026-05-19.md):
#
#   1. Epic G  — Multi-Persona OODA (Pantheon-scope; bridges MGMT-SYN with EP5 canary)
#   2. Epic H  — Telemetry / incident production hardening
#   3. Live-gate placeholder backfill: PROD-WRITES-001-V2 / LIVE-SCALE-001-V2
#   4. Sprint-12 closeout: blueprint acceptance auditor + final Go/No-Go packet
#
# One-shot script. Safe to delete after success.
#
# Usage: AI_NAME=Claude bash .orchestrator/phase8_v3_dispatch.sh

cd "$(dirname "${BASH_SOURCE[0]}")/.."

AUTO_CREATED_BY="phase8-v3-blueprint-residual-2026-05-20"

export TASK_AUTO_CREATED_BY="$AUTO_CREATED_BY"
export TASK_AUTO_GENERATED=true
export TASK_MUTATES_CANONICAL=false

ACC_PRIMARY="Schema/code matches 2026-05-19 blueprint section,Unit tests cover happy path and at least one fail-closed case,Reviewer signs off via ai-status.sh approve,Artifact exists in worktree at closeout,No L1 canonical doc modified"

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

assign_human_gate() {
  local id="$1" owner="$2" reviewer="$3" title="$4" summary="$5" waiting_csv="$6"
  export TASK_TITLE="$title"
  export TASK_SUMMARY_ZH="$summary"
  export TASK_PHASE="Phase 8 / EPIC-LIVE-GATE"
  export TASK_DEPENDS_ON=""
  export TASK_ARTIFACTS=""
  export TASK_ACCEPTANCE="Human risk-owner + operator signoff,Preconditions satisfied,Activation logged in audit chain"
  export TASK_CLASS=human_gate
  export TASK_METADATA_JSON='{"non_dispatchable": true, "gate_status": "pending_human_go_no_go", "allowed_workers": [], "human_required_roles": ["risk_owner", "operator"], "activation_effect": ["irreversible_or_high_risk"]}'
  printf '==> %s (human_gate)\n' "$id"
  ./scripts/ai-status.sh assign "$id" "$owner" "$reviewer" "$title"
  unset TASK_METADATA_JSON
  # Blocker must be filed by the task owner; use a placeholder agent name in waiting_for.
  AI_NAME="$owner" ./scripts/ai-status.sh blocker "$id" "Awaiting: $waiting_csv" "human_gate_council" || true
}

# =====================================================================
# Epic G — Multi-Persona OODA (Pantheon scope)
#   MGMT-SYN-001..007 already cover Management Console allocation. These
#   tasks bridge that surface into Pantheon governance + EP5 canary proof.
# =====================================================================

assign_primary "MPO-001-V2" "Codex" "Codex2" \
  "Multi-persona sponsor resolution service (Pantheon scope)" \
  "Implements sponsor resolver per blueprint §11 MPO-001. Accepts N persona proposals from MGMT-SYN PersonaAllocationProposal store, detects conflicts via existing conflict classifier, produces sponsor-resolved proposal, requires sponsor persona, outputs conflict_resolution_log. Pantheon-scope only — does NOT re-implement MGMT-SYN allocation logic; consumes MGMT-SYN-005 output as input." \
  "" \
  "services/governance/multi_persona/sponsor_resolver.py,services/governance/multi_persona/conflict_resolution_log.py,tests/governance/test_sponsor_resolver.py" \
  "Phase 8 / EPIC-MPO"

assign_primary "MPO-002-V2" "Codex2" "Codex" \
  "Persona registry health gate (suspended/retired exclusion + mandate check)" \
  "Implements pre-synthesis persona registry health gate per blueprint §11 MPO-003. Suspended / retired persona excluded from sponsor candidate pool; missing mandate blocks sponsor role; conflicting role assignments require committee_review flag. Wraps existing PER-001 persona registry (do not modify L1 contracts)." \
  "" \
  "services/persona/registry_health_gate.py,tests/persona/test_registry_health_gate.py" \
  "Phase 8 / EPIC-MPO"

assign_primary "MPO-003-V2" "Claude" "Claude2" \
  "Multi-persona OODA E2E packet (≥2 personas + sponsor-resolved allocation)" \
  "End-to-end test per blueprint §11 MPO-002. At least 2 personas, shared StrategySpec pool, sponsor-selected allocation proposal via MPO-001-V2, conflict log non-null, persona registry health gate enforced via MPO-002-V2, governance review receives synthesized memo. Produces evidence packet linked from EP5 readiness." \
  "MPO-001-V2,MPO-002-V2" \
  "tests/e2e/test_multi_persona_ooda_packet.py,support/evidence/MPO-003-V2/full_packet.json,support/evidence/MPO-003-V2/closure_summary.md" \
  "Phase 8 / EPIC-MPO"

assign_primary "MPO-004-V2" "Codex" "Codex2" \
  "Bridge: multi-persona artifact → EP5ProofPacket sponsor lineage" \
  "Wire MPO-003 synthesis output into EP5ProofPacket so canary readiness can reference sponsor persona, conflict log, and synthesized memo refs. Required for blueprint §17 acceptance #9 closure." \
  "MPO-003-V2" \
  "services/governance/ep5_proof/persona_lineage.py,tests/governance/test_persona_lineage.py" \
  "Phase 8 / EPIC-MPO"

# =====================================================================
# Epic H — Telemetry / Incident Production Hardening
# =====================================================================

assign_primary "TEL-HARD-001-V2" "Codex" "Gemini" \
  "Telemetry ingest 10× load test" \
  "Per blueprint §12 TEL-HARD-001. Drive telemetry ingest at 10× normal load; assert no dropped canonical events, ordering semantics documented in evidence, backpressure handled (no silent loss). Runs against dev compose profile; evidence captures environment, throughput, latency histogram, drop count." \
  "" \
  "tests/telemetry/test_ingest_load_10x.py,support/evidence/TEL-HARD-001-V2/load_report.json,support/evidence/TEL-HARD-001-V2/environment.md" \
  "Phase 8 / EPIC-TEL-HARD"

assign_primary "TEL-HARD-002-V2" "Codex2" "Codex" \
  "Evolution cooldown enforcement integration" \
  "Per blueprint §12 TEL-HARD-002. Same artifact cannot emit repeated EvolutionDecisionProposal inside cooldown window; cooldown override requires HumanGateDecision (reuse EP5-003 schema); every cooldown rejection emits audit evidence. Integrates with existing MGMT-EVO-007 cooldown policy." \
  "" \
  "services/evolution/cooldown_enforcement.py,tests/evolution/test_cooldown_enforcement.py" \
  "Phase 8 / EPIC-TEL-HARD"

assign_primary "TEL-HARD-003-V2" "Codex" "Codex2" \
  "Incident severity escalation SLA test" \
  "Per blueprint §12 TEL-HARD-003. High-severity incident → postmortem within SLA; postmortem → EvolutionDecisionProposal within SLA; critical incident may propose freeze / rollback. SLA values configurable; tests cover happy path + one fail-closed (SLA breach blocks closure)." \
  "" \
  "services/incident/escalation_sla.py,tests/incident/test_escalation_sla.py,docs/operations/incident_sla_runbook.md" \
  "Phase 8 / EPIC-TEL-HARD"

# =====================================================================
# Epic J — Live-gate placeholder backfill (per blueprint §14.1)
# =====================================================================

assign_human_gate "PROD-WRITES-001-V2" "Claude" "Codex2" \
  "Enable production real writes (human gate)" \
  "Human-only activation. Flips VITE_BFF_REAL_WRITES=true and equivalent BFF flags after dual signoff. Cannot be dispatched to AI worker." \
  "LSP-006-V2,HA-PROD-001-V2,risk_owner_signoff,operator_signoff"

assign_human_gate "LIVE-SCALE-001-V2" "Claude2" "Codex" \
  "Live capital scale-up (human gate)" \
  "Human-only activation. Raises live capital budget ceiling above first-window cap after first-week observation report + dual signoff. Cannot be dispatched to AI worker." \
  "CBL-LIVE-001-V2,BLA-007-V2,first_week_observation_report,risk_owner_signoff,operator_signoff"

# =====================================================================
# Epic K — Sprint-12 final closeout (blueprint §15 Sprint 12 + §17 acceptance)
# =====================================================================

assign_primary "BPC-001-V2" "Codex2" "Claude" \
  "Blueprint acceptance auditor (12 conditions per §17)" \
  "Auditor that mechanically verifies blueprint §17 12 final acceptance conditions. Produces blueprint_completion_report.json with one boolean+evidence_ref per condition. Fails closed if any condition lacks evidence ref. Read-only; never mutates state." \
  "MPO-004-V2,TEL-HARD-001-V2,TEL-HARD-002-V2,TEL-HARD-003-V2,PROD-WRITES-001-V2,LIVE-SCALE-001-V2" \
  "tools/blueprint_acceptance_audit.py,tests/tools/test_blueprint_acceptance_audit.py,support/evidence/BPC-001-V2/blueprint_completion_report.json" \
  "Phase 8 / EPIC-BLUEPRINT-CLOSEOUT"

assign_primary "BPC-002-V2" "Claude" "Codex" \
  "Final Go/No-Go packet assembler" \
  "Aggregates all readiness refs (EP5, BLA, CBL, HA, LSP, RES-ACT, MPO, TEL-HARD), HumanGateDecision records, and BPC-001 completion report into a single final_go_no_go_packet.json. Includes Go/No-Go matrix per blueprint §16." \
  "BPC-001-V2" \
  "tools/final_go_no_go_assembler.py,tests/tools/test_final_go_no_go_assembler.py,support/evidence/BPC-002-V2/final_go_no_go_packet.json,docs/operations/final_go_no_go_runbook.md" \
  "Phase 8 / EPIC-BLUEPRINT-CLOSEOUT"

assign_primary "BPC-003-V2" "Claude2" "Codex2" \
  "Design-team signoff record + blueprint_complete=true" \
  "Final chair-only record. Stamps blueprint_complete=true with chair signature, references BPC-002 packet hash, and records the exact closing statement per blueprint §17. No live activation side effect." \
  "BPC-002-V2" \
  "support/evidence/BPC-003-V2/design_team_signoff.json,docs/operations/blueprint_closing_statement.md" \
  "Phase 8 / EPIC-BLUEPRINT-CLOSEOUT"

# =====================================================================
echo
echo "V3 dispatch complete. Running sync to refresh derived state."
./scripts/ai-status.sh sync || true
echo
echo "V3 task summary:"
python3 - <<'PY'
import json
s = json.load(open('ai-status.json'))
v3_ids = {
    'MPO-001-V2','MPO-002-V2','MPO-003-V2','MPO-004-V2',
    'TEL-HARD-001-V2','TEL-HARD-002-V2','TEL-HARD-003-V2',
    'PROD-WRITES-001-V2','LIVE-SCALE-001-V2',
    'BPC-001-V2','BPC-002-V2','BPC-003-V2',
}
rows = [t for t in s.get('tasks', []) if t['id'] in v3_ids]
print(f'V3 active tasks: {len(rows)} / 12 expected')
from collections import Counter
status_c = Counter(t.get('status','?') for t in rows)
class_c = Counter(t.get('task_class','primary') for t in rows)
print('  status:', dict(status_c))
print('  class :', dict(class_c))
print()
for t in sorted(rows, key=lambda r: r['id']):
    deps = ','.join(t.get('depends_on') or []) or '-'
    print(f"  {t['id']:<22} {t.get('status','?'):<10} {t.get('owner','?'):<8}→{t.get('reviewer','?'):<8} {t.get('task_class','primary'):<10} deps={deps}")
PY
