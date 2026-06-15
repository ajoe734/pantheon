#!/usr/bin/env python3
"""Dispatch CONSOLE-DATA: populate the management-console pages that EXIST but
return empty because the BFF read-model is disconnected from the live services.

Distinct from BFFGAP-CONSOLE (dispatch_bff_console_gap_2026-06-15.py), which
builds 404 endpoints. These endpoints already return 200 with an empty /
source-unavailable envelope; the work is to PRODUCE real data via each domain's
real producer and RECONCILE the BFF read-path so the page renders it.

Worked example (clone this): docs/05/system-verification-rounds/
console-population-research-slice.md + scripts/project_research_to_bff_surfaces.py
(research -> strategies/artifacts now render real data).
"""
from __future__ import annotations
import os, subprocess, sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_BY = "dispatch_console_data_population_2026-06-15"

CONTRACT = (
    "用該 domain 的真實 producer 產生真資料(禁止捏造);再重接 BFF 讀路徑"
    "(設 PANTHEON_BFF_*_STORE / 指向 live service / 加投影,如 scripts/project_research_to_bff_surfaces.py);"
    "驗收:live curl(Bearer op-dev:admin:mfa)該 /bff 面回 count>0 且 surface status=ok;"
    "在 services/control-plane/bff/tests 加/更新 contract test;stub dispatch 為 dev 安全姿態。"
    "範式見 docs/05/system-verification-rounds/console-population-research-slice.md。"
)

# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    ("CONSOLE-DATA-EVOLUTION", "Populate /bff/evolution-programs with real proposals",
     "evolution svc 已有真 proposal(evo-vslice-1);重接 proposals→evolution-programs 讀映射使其顯示。"+CONTRACT,
     "Claude","Claude2","EPIC CONSOLE-DATA / evolution","",
     "/bff/evolution-programs count>0;contract test 綠","services/evolution,services/control-plane/bff"),
    ("CONSOLE-DATA-APPROVALS", "Populate /bff/approvals via promotion approvals",
     "promotion svc POST /api/v1/approvals 產真 approval;接 PANTHEON_GOVERNANCE_APPROVAL_API_URL 讀路徑。"+CONTRACT,
     "Claude2","Codex","EPIC CONSOLE-DATA / approvals","",
     "/bff/approvals count>0","services/promotion,services/control-plane/bff"),
    ("CONSOLE-DATA-INCIDENTS-REVIEWS", "Populate /bff/incidents + /bff/reviews",
     "incidents svc 產 well-formed incident(含 runtime_id+title);review 流程產 review。"+CONTRACT,
     "Codex","Claude","EPIC CONSOLE-DATA / incidents","",
     "/bff/incidents 與 /bff/reviews count>0 且 incident 非 Untitled","services/incidents,services/control-plane/bff"),
    ("CONSOLE-DATA-REGISTRY", "Populate /bff/skills,/bff/tools,/bff/mcp-servers,/bff/mcp-tools",
     "用 registry create API 註冊真 skill/tool/mcp-server/mcp-tool;接 PANTHEON_BFF_*_STORE 讀路徑。"+CONTRACT,
     "Claude","Claude2","EPIC CONSOLE-DATA / registry","",
     "四個 /bff 面 count>0","services/registry-core,services/control-plane/bff"),
    ("CONSOLE-DATA-AGORA", "Populate /bff/agora/* (20 surfaces)",
     "consultation/agora producers 產真 session/inbox/signals/journal/notes 等;接各 agora read-surface。可拆子任務。"+CONTRACT,
     "Claude2","Codex","EPIC CONSOLE-DATA / agora","",
     "至少 inbox/signals/sessions/insights count>0","services/consultation,services/control-plane/bff"),
    ("CONSOLE-DATA-KNOWLEDGE-RESEARCH", "Populate /bff/knowledge,/bff/research-analyses,/bff/research/tasks",
     "research-orchestrator + memory svc 產真 analysis/task/knowledge;投影進 BFF 讀面(延用 research slice 投影)。"+CONTRACT,
     "Codex","Claude","EPIC CONSOLE-DATA / knowledge","",
     "三面 count>0","services/research,services/memory,services/control-plane/bff"),
    ("CONSOLE-DATA-RANKINGS", "Populate /bff/rankings + /bff/ranking-formulas",
     "ranking producer 產真 ranking/formula;接讀路徑。"+CONTRACT,
     "Claude","Claude2","EPIC CONSOLE-DATA / rankings","",
     "兩面 count>0","services/control-plane/bff"),
    ("CONSOLE-DATA-OODA", "Populate /bff/ooda/packets",
     "OODA loop producer 產真 packet 寫入 PANTHEON_BFF_OODA_PACKET_STORE / PANTHEON_OODA_DATA_DIR。"+CONTRACT,
     "Claude2","Codex","EPIC CONSOLE-DATA / ooda","",
     "/bff/ooda/packets count>0","services/control-plane/ooda,services/control-plane/bff"),
    ("CONSOLE-DATA-ROUTE-WORKFLOWS", "Populate /bff/route-policies,/bff/workflows,/bff/hooks,/bff/jobs",
     "各 svc 產真 route-policy/workflow/hook/job;接讀路徑。"+CONTRACT,
     "Codex","Claude","EPIC CONSOLE-DATA / route-workflows","",
     "四面 count>0","services/control-plane/bff"),
]

def run(cmd, env_extra=None):
    env = os.environ.copy()
    if env_extra: env.update(env_extra)
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)

def main() -> int:
    dry = "--dry-run" in sys.argv
    ok = True
    for task_id, title, summary, owner, reviewer, phase, deps, acc, arts in TASKS:
        if dry:
            print(f"PLAN  {task_id:32} owner={owner:8} reviewer={reviewer:8}  {title}")
            continue
        env_extra = {"TASK_SUMMARY_ZH": summary, "TASK_PHASE": phase, "TASK_DEPENDS_ON": deps,
                     "TASK_ACCEPTANCE": acc, "TASK_ARTIFACTS": arts, "TASK_AUTO_CREATED_BY": AUTO_BY}
        r = run([sys.executable, "scripts/ai_status.py", "assign", task_id, owner, reviewer, title], env_extra)
        if r.returncode != 0:
            print(f"ASSIGN FAIL {task_id}: {r.stderr.strip() or r.stdout.strip()}", file=sys.stderr); ok = False
        else:
            print(f"ASSIGN  {task_id:32} owner={owner:8} reviewer={reviewer}")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
