#!/usr/bin/env python3
"""Dispatch MPOS full-loop gap closure tasks to supervisor/autoworker queue.

Source packet:
  docs/04/pantheon_multi_persona_ooda_gap_dispatch_2026-06-09/
    MPOS_GAP_ASSESSMENT_AND_DISPATCH_2026-06-09.md

This script materializes the gap closure work through the existing
scripts/ai_status.py assignment path. It only updates repository task-state
artifacts; it does not grant runtime, shell, broker, or capital authority.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "ai-status.json"

PACKET = (
    "docs/04/pantheon_multi_persona_ooda_gap_dispatch_2026-06-09/"
    "MPOS_GAP_ASSESSMENT_AND_DISPATCH_2026-06-09.md"
)
MULTI_PERSONA_E2E = "tests/e2e/test_multi_persona_ooda_packet.py"
PERSONA_RUNTIME_E2E = "services/control-plane/governance/test_persona_proposal_runtime_binding_e2e.py"
ALLOCATION_REGISTRY_TEST = "services/registry/test_allocation_policy_artifact.py"
DEPLOYMENT_PLAN = "services/control-plane/governance/deployment_plan.py"
APPROVAL_DECISION = "services/control-plane/governance/approval_decision.py"
CONFLICT_CLASSIFIER = "services/optimizer-svc/portfolio_synthesis/conflict_classifier.py"
RISK_POLICY = "services/capital/risk_policy.py"
CONSULT_BRIDGE = "services/consultation/sponsor_decision_bridge.py"
CONSULT_E2E = "services/consultation/test_e2e_consult_review.py"
MEMORY_MAIN = "services/memory/main.py"
TELEMETRY_ADAPTER = "services/telemetry/feedback_adapter.py"
RESEARCH_DISPATCH = "services/research/experiment_orchestrator/parallel_dispatch.py"
RESEARCH_MATRIX = "RESEARCH_BACKEND_MATURITY_MATRIX.md"

SPRINT_ID = "2026-06-09-mpos-full-loop-gap-closure"
SPRINT_OBJECTIVE = (
    "Close the remaining multi-persona OODA gaps: prove Persona A/B/C research-to-proposal packets, "
    "run approved AllocationPolicyArtifact through DeploymentPlan RuntimeBinding paper LEAN telemetry, "
    "enforce consultation and homogeneity/correlation gates before LEAN, and write Learn feedback back "
    "to persona or sponsor memory while live broker authority remains fail-closed."
)

PHASE_PERSONA = "Sprint MPOS-P1 / Persona OODA evidence"
PHASE_E2E = "Sprint MPOS-P1 / Allocation policy runtime closure"
PHASE_CONSULT = "Sprint MPOS-P1 / Consultation governance gate"
PHASE_RISK = "Sprint MPOS-P1 / Homogeneity correlation gate"
PHASE_MEMORY = "Sprint MPOS-P1 / Learn feedback attribution"
PHASE_VERIFY = "Sprint MPOS-P1 / Supervisor closure evidence"
PHASE_BACKEND = "Sprint MPOS-P2 / Research backend clarity"


# task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts, gap_ids
TASKS = [
    (
        "MPOS-P1-PER-002",
        "Prove Persona A/B/C research-to-proposal OODA packets",
        "補三個 persona 各自從 Observe/Orient 到 PersonaAllocationProposal 的證據鏈，避免多人格 synthesis 只吃手寫 proposal fixture。",
        "Copilot",
        "Codex",
        PHASE_PERSONA,
        "MPOS-P0-VAL-001,MPOS-P1-PER-001,MPOS-P0-E2E-001",
        "Persona A B C each start from source or strategy evidence;"
        "each packet includes StrategySpecSeed or StrategySpec and ExperimentRun or OOS validation;"
        "each packet records regime risk mandate fit evidence quality and no-order-route proof;"
        "each PersonaAllocationProposal evidence_refs point back to its packet;"
        "ineligible or suspended persona remains excluded by policy or health gate",
        f"{PACKET},{MULTI_PERSONA_E2E},tests/e2e/test_source_to_strategy_spec.py,tests/e2e/test_strategy_spec_to_experiment_run.py,services/source_ingestion/strategy_seed_builder.py,services/research/strategy_spec/conversion.py,services/optimizer-svc/portfolio_synthesis/models.py",
        "G2",
    ),
    (
        "MPOS-P1-E2E-002",
        "Run approved AllocationPolicyArtifact through paper LEAN loop",
        "把已核准 AllocationPolicyArtifact 實際接到 DeploymentPlan、RuntimeBinding、paper LEAN、fills/telemetry 與 lineage 查詢。",
        "Claude",
        "Codex",
        PHASE_E2E,
        "MPOS-P1-ART-001,MPOS-P1-PER-002,MPOS-P1-RISK-001,MPOS-P1-MEM-001,MPOS-P1-PER-001",
        "Start from synthesized AllocationPolicyArtifact;"
        "register allocation artifact as candidate and advance to approved with ApprovalDecision evidence;"
        "create DeploymentPlan whose artifact_type is allocation_policy;"
        "create RuntimeBinding with sponsor persona and persona capital binding attribution;"
        "run paper LEAN only and assert no live broker order route;"
        "capture fills telemetry and query lineage by runtime binding deployment plan capital pool artifact and persona capital binding",
        f"{PACKET},{ALLOCATION_REGISTRY_TEST},{DEPLOYMENT_PLAN},{APPROVAL_DECISION},{PERSONA_RUNTIME_E2E},tests/e2e/test_deployment_plan_to_paper_run.py,services/execution/lean_runtime/paper_runtime.py,{TELEMETRY_ADAPTER}",
        "G1",
    ),
    (
        "MPOS-P1-CONSULT-001",
        "Require consultation handoff for high-risk allocation approval",
        "把 consultation/committee memo 與 sponsor decision handoff 變成 allocation approval 的硬門檻，而不是旁路資料。",
        "Claude2",
        "Codex",
        PHASE_CONSULT,
        "MPOS-P1-ART-001,MPOS-P1-PER-002",
        "Open conflicts or high-risk allocation paths require consultation request;"
        "committee memo and service_handoff refs are stored as approval evidence;"
        "allocation approval rejects missing stale or mismatched committee handoff;"
        "sponsor decision bridge can emit approval proposal for allocation_policy;"
        "tests cover approve approve-with-conditions reject missing-handoff and stale-handoff cases",
        f"{PACKET},{CONSULT_E2E},{CONSULT_BRIDGE},services/control-plane/bff/test_cw03_committee_board_contract.py,services/consultation/store.py,{APPROVAL_DECISION},{ALLOCATION_REGISTRY_TEST}",
        "G3",
    ),
    (
        "MPOS-P1-RISK-002",
        "Add homogeneity and correlation review to allocation gate",
        "在 pre-LEAN allocation gate 補 homogeneity/correlation review，避免多個 persona 同時堆疊高度相關或重複 exposure。",
        "Codex",
        "Claude",
        PHASE_RISK,
        "MPOS-P1-RISK-001,MPOS-P1-PER-002",
        "Add first-class homogeneity or correlation review to allocation conflict taxonomy or adjacent gate;"
        "detect duplicated strategy family high target overlap high correlation bucket and concentration by capital pool;"
        "escalate or reject according to RiskPolicy evaluator precedence;"
        "risk veto still outranks committee escalation;"
        "tests include low correlation pass high correlation committee escalation and hard veto",
        f"{PACKET},{CONFLICT_CLASSIFIER},{RISK_POLICY},services/optimizer-svc/test_allocation_conflict_classifier.py,services/optimizer-svc/test_portfolio_synthesis.py,services/capital/test_risk_policy.py",
        "G4",
    ),
    (
        "MPOS-P1-MEM-002",
        "Automate persona and sponsor Learn feedback writeback",
        "把 runtime telemetry、postmortem、evolution 結果自動寫回 persona memory 與 sponsor-attributed institutional memory。",
        "Codex2",
        "Claude",
        PHASE_MEMORY,
        "MPOS-P1-MEM-001,MPOS-P1-E2E-002",
        "Telemetry postmortem or evolution outcomes create persona memory writebacks;"
        "sponsor-attributed institutional memory includes sponsor persona and contributing persona ids;"
        "contributor memory entries link proposal ids and runtime telemetry evidence;"
        "writeback is idempotent by source event id;"
        "tests cover success duplicate replay missing persona attribution and unauthorized writeback",
        f"{PACKET},{MEMORY_MAIN},services/memory/persona_memory_store.py,services/memory/institutional_memory_store.py,{TELEMETRY_ADAPTER},services/incident/incident.py,services/evolution/postmortem_bridge.py,services/memory/test_main.py",
        "G5",
    ),
    (
        "MPOS-P1-VERIFY-001",
        "Produce supervisor closure packet for MPOS full-loop proof",
        "彙整所有 MPOS P1 修補任務的 PR、commit、CI 與本機驗證，產生 supervisor 可審的完整閉環證據包。",
        "Gemini2",
        "Codex",
        PHASE_VERIFY,
        "MPOS-P1-PER-002,MPOS-P1-E2E-002,MPOS-P1-CONSULT-001,MPOS-P1-RISK-002,MPOS-P1-MEM-002",
        "Build one supervisor-visible closure packet for all MPOS P1 gates;"
        "include task PR commit and check refs for all implementation tasks;"
        "include requirement matrix against MPOS gap dispatch doc;"
        "include local validation commands and CI status;"
        "mark live or canary broker activation as intentionally fail-closed when not activated",
        f"{PACKET},.orchestrator/task-briefs,current-work.md,dashboard-bundle.json,tests/e2e,services/telemetry/lineage_read/service.py",
        "G1,G2,G3,G4,G5",
    ),
    (
        "MPOS-P2-BACKEND-001",
        "Normalize MPOS Observe backend maturity matrix",
        "整理 Qlib/vectorbt/statsmodels/QuantLib 在 MPOS Observe 流程中的 maturity、no-order-route 與驗收證據。",
        "Copilot",
        "Claude",
        PHASE_BACKEND,
        "MPOS-P1-PER-002",
        "Create MPOS Observe backend matrix for vectorbt Qlib statsmodels and QuantLib;"
        "state production or readiness posture for each backend;"
        "state no-order-route guarantees and proof tests;"
        "clarify whether Qlib remains activation-ready or production-active;"
        "clarify whether QuantLib is default-dispatch separate governed path or deferred",
        f"{PACKET},{RESEARCH_DISPATCH},{RESEARCH_MATRIX},services/research/qlib,services/research/vectorbt,services/research/statsmodels,services/research/quantlib",
        "G6",
    ),
]


def update_sprint_metadata() -> None:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    state["sprint"] = SPRINT_ID
    state["sprint_started_at"] = "2026-06-09T00:00:00Z"
    state["objective"] = SPRINT_OBJECTIVE
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Sprint metadata updated: {SPRINT_ID}")


def dispatch_one(
    task_id: str,
    title: str,
    summary_zh: str,
    owner: str,
    reviewer: str,
    phase: str,
    depends_on: str,
    acceptance: str,
    artifacts: str,
    gap_ids: str,
) -> None:
    env = os.environ.copy()
    env["AI_NAME"] = "Operator"
    env["TASK_SUMMARY_ZH"] = summary_zh
    env["TASK_PHASE"] = phase
    env["TASK_DEPENDS_ON"] = depends_on
    env["TASK_ACCEPTANCE"] = acceptance
    env["TASK_ARTIFACTS"] = artifacts
    env["TASK_CLASS"] = "mpos_full_loop_gap_closure"
    env["TASK_AUTO_CREATED_BY"] = "dispatch_mpos_gap_closure_2026-06-09"
    env["TASK_AUTO_GENERATED"] = "true"
    env["AI_STATUS_LOG_ROTATE_MAX_BYTES"] = "0"
    env["TASK_METADATA_JSON"] = json.dumps(
        {
            "source_packet": PACKET,
            "gap_ids": [item.strip() for item in gap_ids.split(",") if item.strip()],
            "dispatch_sprint": SPRINT_ID,
        },
        ensure_ascii=False,
    )
    cmd = [sys.executable, "scripts/ai_status.py", "assign", task_id, owner, reviewer, title]
    result = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"FAIL {task_id}: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
        sys.exit(result.returncode)
    print(f"OK   {task_id}  owner={owner}  reviewer={reviewer}  phase={phase}")


def main() -> int:
    expected_ids = [task[0] for task in TASKS]
    if len(expected_ids) != len(set(expected_ids)):
        print("Duplicate task IDs in dispatch list", file=sys.stderr)
        return 1
    update_sprint_metadata()
    print(f"Dispatching {len(TASKS)} MPOS gap closure tasks ...")
    for task in TASKS:
        dispatch_one(*task)
    print(
        f"\nDispatched {len(TASKS)} tasks for {SPRINT_ID}. "
        "The existing ai_status sync path refreshed supervisor dashboard and task brief outputs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
