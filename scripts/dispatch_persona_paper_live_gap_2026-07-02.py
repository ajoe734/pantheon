#!/usr/bin/env python3
"""Dispatch persona paper-first live promotion gap tasks.

Source packet:
  docs/04/pantheon_persona_paper_live_gap_2026-07-02/

This script materializes active supervisor tasks through scripts/ai_status.py.
It does not grant broker, runtime, or capital authority.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "ai-status.json"
AUTO_BY = "dispatch_persona_paper_live_gap_2026-07-02"
PACKET = "docs/04/pantheon_persona_paper_live_gap_2026-07-02/GAP_AND_EXECUTION_PLAN.md"
TASK_DIR = "docs/bff/execution-tasks/2026-07-02-persona-paper-live-gap"
SPRINT_ID = "2026-07-02-persona-paper-live-gap"
SPRINT_OBJECTIVE = (
    "Implement the paper-first persona lifecycle: create persona directly into "
    "paper runtime, evaluate paper cohorts, require human approval for canary, "
    "live, and quarterly allocation changes, and enforce automatic risk "
    "guardrails that can pause, reduce, risk-off, or freeze immediately."
)


# task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts
TASKS = [
    (
        "PPLG-001",
        "Canonical persona paper/live state and contract alignment",
        "鎖定 paper-first persona lifecycle, schema, endpoint contract, 舊 onboarding spec supersession。建立完成必須是 paper runtime 或 setup_failed。",
        "Codex",
        "Claude",
        "EPIC PPLG / contracts",
        "",
        "contract docs state create-to-paper invariant;canary live quarterly require human decision;automatic guardrails cannot promote or increase allocation;schema/contract tests cover enums",
        f"{PACKET},{TASK_DIR}/PPLG-001-canonical-state-contract.md,services/control-plane/bff/BFF_API_CONTRACT.md,services/control-plane/bff/tests",
    ),
    (
        "PPLG-002",
        "Idempotent create-to-paper persona launch workflow",
        "實作 POST /bff/management/personas/paper-launch，一次完成 persona、paper pool binding、paper plan、paper approval、RuntimeBinding、paper runtime startup。",
        "Claude",
        "Codex",
        "EPIC PPLG / paper launch",
        "PPLG-001",
        "same idempotency key replays safely;different payload conflicts;happy path reaches paper runtime;failed step records retryable repair state;no live pool binding during create",
        f"{PACKET},{TASK_DIR}/PPLG-002-paper-launch-orchestrator.md,services/control-plane/bff,services/control-plane/governance,services/control-plane/bff/tests",
    ),
    (
        "PPLG-003",
        "Persona Fleet readiness projection and payload cleanup",
        "補 Fleet readiness/competition projection 並移除重複大 payload，讓 row 在同一 cohort 顯示 paper challengers、canary challengers、live incumbents。",
        "Codex2",
        "Claude2",
        "EPIC PPLG / fleet read model",
        "PPLG-001",
        "fleet default includes paper challengers canary challengers and live incumbents in one competition projection;mode filters do not create hidden separate datasets;payload materially smaller;contract tests cover row shape",
        f"{PACKET},{TASK_DIR}/PPLG-003-fleet-readiness-read-model.md,services/control-plane/bff,services/control-plane/bff/tests/test_bff_b3_persona_fleet.py",
    ),
    (
        "PPLG-004",
        "Paper eligibility and unified competition ranking engine",
        "實作 paper hard gates、promotion_score、paper/canary/live 同 cohort ranking 與 recommendation packet；系統只推薦，不批准實盤。",
        "Claude2",
        "Codex",
        "EPIC PPLG / evaluation ranking",
        "PPLG-001",
        "ranking snapshots include paper challengers canary challengers and live incumbents in one cohort result;eligible rows expose score components gates percentile evidence;recommendation cannot start canary/live without human decision;tests cover threshold and overrides",
        f"{PACKET},{TASK_DIR}/PPLG-004-paper-evaluation-ranking.md,services/evaluation,services/optimizer-svc,services/control-plane/bff/tests",
    ),
    (
        "PPLG-005",
        "Human review workflows for canary live and quarterly ranking",
        "實作 promotion/canary/live/quarterly/replacement/resume human review，所有真錢資金進出與季度重排都需人審。",
        "Claude",
        "Codex2",
        "EPIC PPLG / human review",
        "PPLG-004",
        "paper recommendation alone cannot create canary/live;quarterly proposal cannot rebalance without human decision;resume risk_off/frozen requires approval;approve/reject/expired tests pass",
        f"{PACKET},{TASK_DIR}/PPLG-005-human-review-workflows.md,services/control-plane/governance,services/control-plane/bff/tests",
    ),
    (
        "PPLG-006",
        "Automatic risk guardrails and incident review evidence",
        "實作虧損、drawdown、exposure、slippage、order/data/runtime/policy/correlation guardrails，可自動 pause/reduce/risk_off/freeze 並建立事件審核。",
        "Codex",
        "Claude2",
        "EPIC PPLG / risk guardrails",
        "PPLG-001",
        "loss/drawdown/policy/data/runtime triggers act immediately;each action records incident and trace;guardrails cannot promote or increase allocation;resume requires human review",
        f"{PACKET},{TASK_DIR}/PPLG-006-risk-guardrails-incident-review.md,services/capital,services/runtime-manager,services/incident,services/control-plane/bff/tests",
    ),
    (
        "PPLG-007",
        "Frontend Create Paper Persona and unified Fleet UX",
        "更新 Persona Registry/Fleet：主要 CTA 是建立 Paper Persona，row action 依狀態顯示，研究/模擬/正式只控制命令上下文，不拆開 paper/live 競爭視圖。",
        "Codex2",
        "Claude",
        "EPIC PPLG / frontend UX",
        "PPLG-002,PPLG-003,PPLG-005",
        "primary create reaches paper_running or setup_failed;mode selector does not hide paper challengers from live incumbent comparison;row actions match concrete states;eligible shows promotion review;canary/live show approval evidence;e2e covers no canary without approval",
        f"{PACKET},{TASK_DIR}/PPLG-007-frontend-paper-persona-flow.md",
    ),
    (
        "PPLG-008",
        "End-to-end release gate and fleet closeout",
        "建立完整驗證包：create->paper runtime->evaluation->human review->canary/live/quarterly/risk-off 全流程證據。",
        "Gemini2",
        "Codex",
        "EPIC PPLG / verification",
        "PPLG-002,PPLG-003,PPLG-004,PPLG-005,PPLG-006,PPLG-007",
        "e2e proves create to paper runtime;mode selector changes command affordances without splitting competition datasets;recommendation cannot start canary without approval;quarterly cannot execute without approval;risk_off interrupts immediately;closeout maps every spec requirement",
        f"{PACKET},{TASK_DIR}/PPLG-008-e2e-release-gate.md,tests/e2e,docs/deployment/evidence",
    ),
]


def dispatch_one(task: tuple[str, str, str, str, str, str, str, str, str]) -> None:
    task_id, title, summary, owner, reviewer, phase, depends_on, acceptance, artifacts = task
    env = os.environ.copy()
    env["AI_NAME"] = "Operator"
    env["TASK_SUMMARY_ZH"] = summary
    env["TASK_PHASE"] = phase
    env["TASK_DEPENDS_ON"] = depends_on
    env["TASK_ACCEPTANCE"] = acceptance
    env["TASK_ARTIFACTS"] = artifacts
    env["TASK_CLASS"] = "persona_paper_live_gap"
    env["TASK_AUTO_CREATED_BY"] = AUTO_BY
    env["TASK_AUTO_GENERATED"] = "true"
    env["AI_STATUS_LOG_ROTATE_MAX_BYTES"] = "0"
    env["TASK_METADATA_JSON"] = json.dumps(
        {
            "source_packet": PACKET,
            "task_packet_dir": TASK_DIR,
            "dispatch_sprint": SPRINT_ID,
        },
        ensure_ascii=False,
    )
    cmd = [sys.executable, "scripts/ai_status.py", "assign", task_id, owner, reviewer, title]
    result = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"FAIL {task_id}: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
        raise SystemExit(result.returncode)
    print(f"OK   {task_id}  owner={owner}  reviewer={reviewer}  deps={depends_on or '-'}")


def update_sprint_metadata() -> None:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    state["sprint"] = SPRINT_ID
    state["sprint_started_at"] = "2026-07-02T00:00:00Z"
    state["objective"] = SPRINT_OBJECTIVE
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Sprint metadata updated: {SPRINT_ID}")


def main() -> int:
    task_ids = [task[0] for task in TASKS]
    if len(task_ids) != len(set(task_ids)):
        print("Duplicate task IDs in dispatch list", file=sys.stderr)
        return 1
    update_sprint_metadata()
    print(f"Dispatching {len(TASKS)} persona paper/live gap tasks ...")
    for task in TASKS:
        dispatch_one(task)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
