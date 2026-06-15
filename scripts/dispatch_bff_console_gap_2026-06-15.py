#!/usr/bin/env python3
"""Dispatch BFFGAP-CONSOLE: implement the management-console BFF endpoints that
currently 404 (FE pages built, no backend).

Spec: docs/04/pantheon_bff_console_gap_2026-06-15/README.md
All tasks are independent (depends_on=[]) ready-now. P3 studios endpoints are
documented but intentionally NOT dispatched (need a real backtest/skill engine).
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_BY = "dispatch_bff_console_gap_2026-06-15"

CONTRACT = (
    "端點回 canonical list 信封(data/items/page_info/meta);store 空時回明確 degraded "
    "信封(status:unavailable,source:missing)非裸[];沿用既有 auth/CORS;更新 BFF_API_CONTRACT.md "
    "與 OpenAPI;在 services/control-plane/bff/tests/ 加 contract test(仿 test_bff_management_cockpit.py)。"
    "main.py 約47k行,優先用新模組(console_gap/<feature>.py 暴露 APIRouter)+單行 include_router,降低衝突。"
)

# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    (
        "BFFGAP-DATASOURCES",
        "BFF GET /bff/management/data-sources (P0)",
        "新增 GET /bff/management/data-sources:回資料來源登錄+擷取健康(market data ingestion)。" + CONTRACT
        + " 供 FE DataSourceManagement 頁取真資料。dev 無真實來源時回 degraded 信封。",
        "Claude", "Claude2", "EPIC BFFGAP-CONSOLE / data-sources", "",
        "GET /bff/management/data-sources 回 200 canonical 信封(空時 degraded);contract+test 綠;"
        "live curl(Bearer op-dev:admin:mfa)非 404",
        "services/control-plane/bff/main.py,services/control-plane/bff/tests,services/control-plane/bff/BFF_API_CONTRACT.md",
    ),
    (
        "BFFGAP-ALPHAFACTORY",
        "BFF GET /bff/alpha-factory (P1)",
        "新增 GET /bff/alpha-factory:回 idea→strategy 看板(lanes/cards)。" + CONTRACT
        + " 供 FE AlphaFactoryBoard。",
        "Claude2", "Claude", "EPIC BFFGAP-CONSOLE / alpha-factory", "",
        "GET /bff/alpha-factory 回 200 canonical 信封;contract+test 綠;live curl 非 404",
        "services/control-plane/bff/main.py,services/control-plane/bff/tests,services/control-plane/bff/BFF_API_CONTRACT.md",
    ),
    (
        "BFFGAP-GOVRULES",
        "BFF governance sub-rules: permissions + memory + consult + route-policies (P1)",
        "新增 4 個治理子規則讀端點:GET /bff/management/permissions、GET /bff/management/memory-governance、"
        "GET /bff/management/consult-rules、GET /bff/route-policies(list)。" + CONTRACT
        + " 供 FE governance/{permissions,memory,consult,policies} 頁。",
        "Claude", "Codex2", "EPIC BFFGAP-CONSOLE / governance-rules", "",
        "四端點皆回 200 canonical 信封(空時 degraded);contract+test 綠;live curl 四條皆非 404",
        "services/control-plane/bff/main.py,services/control-plane/bff/tests,services/control-plane/bff/BFF_API_CONTRACT.md",
    ),
    (
        "BFFGAP-LINEAGE",
        "BFF GET /bff/lineage (P1)",
        "新增 GET /bff/lineage:回資料/artifact 血緣圖(nodes/edges)。" + CONTRACT
        + " 供 FE LineageExplorer。",
        "Claude2", "Codex", "EPIC BFFGAP-CONSOLE / lineage", "",
        "GET /bff/lineage 回 200 canonical 信封(nodes/edges);contract+test 綠;live curl 非 404",
        "services/control-plane/bff/main.py,services/control-plane/bff/tests,services/control-plane/bff/BFF_API_CONTRACT.md",
    ),
    (
        "BFFGAP-KNOWLEDGE",
        "BFF GET /bff/knowledge (P2)",
        "新增 GET /bff/knowledge:回 knowledge inbox 條目。" + CONTRACT + " 供 FE KnowledgeInbox。",
        "Codex", "Claude", "EPIC BFFGAP-CONSOLE / knowledge", "",
        "GET /bff/knowledge 回 200 canonical 信封;contract+test 綠;live curl 非 404",
        "services/control-plane/bff/main.py,services/control-plane/bff/tests,services/control-plane/bff/BFF_API_CONTRACT.md",
    ),
    (
        "BFFGAP-WORKFLOWS-HOOKS",
        "BFF GET /bff/workflows + GET /bff/hooks (P2)",
        "新增兩個自動化登錄讀端點:GET /bff/workflows(workflow templates)、GET /bff/hooks(cron/hook registry)。"
        + CONTRACT + " 供 FE WorkflowTemplates / HookCronManager。",
        "Codex2", "Claude2", "EPIC BFFGAP-CONSOLE / workflows-hooks", "",
        "兩端點皆回 200 canonical 信封;contract+test 綠;live curl 兩條皆非 404",
        "services/control-plane/bff/main.py,services/control-plane/bff/tests,services/control-plane/bff/BFF_API_CONTRACT.md",
    ),
]


def run(cmd, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)


def main() -> int:
    ok = True
    for task_id, title, summary, owner, reviewer, phase, deps, acc, arts in TASKS:
        env_extra = {
            "TASK_SUMMARY_ZH": summary, "TASK_PHASE": phase, "TASK_DEPENDS_ON": deps,
            "TASK_ACCEPTANCE": acc, "TASK_ARTIFACTS": arts, "TASK_AUTO_CREATED_BY": AUTO_BY,
        }
        r = run([sys.executable, "scripts/ai_status.py", "assign", task_id, owner, reviewer, title],
                env_extra=env_extra)
        if r.returncode != 0:
            print(f"ASSIGN FAIL {task_id}: {r.stderr.strip() or r.stdout.strip()}", file=sys.stderr)
            ok = False
        else:
            print(f"ASSIGN  {task_id:24} owner={owner:8} reviewer={reviewer:8} deps={deps or '-'}")
    print("Done." if ok else "Completed with failures.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
