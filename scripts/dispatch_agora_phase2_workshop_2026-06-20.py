#!/usr/bin/env python3
"""Dispatch Agora Phase 2 (Strategy Workshop) releasable tasks.

Spec: docs/04/pantheon_agora_cross_repo_2026-06-20/ (SD §6, §11, §17.2, §25)

Releasable now (spec frozen, acceptance-testable). HELD OUT on purpose:
  - AG-BE-SW-003 (completeness/conflict/next-best-question skill) — blocked by
    OPEN_DESIGN_ITEMS_FOR_SD_TEAM.md A1 (scoring weights) + C1 (skill IO spec).
  - AG-E2E-SW-001 (winner-branch description -> valid draft) — depends on SW-003.
Both stay in DISPATCH_PHASE0_PHASE1_2026-06-20.md until SD team closes A1/C1.

These tasks depend on Phase 1 (AG-BE-ID-001 / AG-FE-ID-001) and stay gated by
depends_on until those land — assigning now just widens the future parallel pool.

Repo routing: artifacts starting "execute-plans/" -> execute_plans repo; else pantheon.
Owner policy: Claude -> Claude2 -> Codex (never Codex2 / Antigravity).
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_BY = "dispatch_agora_phase2_workshop_2026-06-20"

# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    (
        "AG-BE-SW-001",
        "Workshop session/event persistence",
        "依 SD §6.5 在 control-plane Postgres 建立 strategy_workshop_session / strategy_workshop_event / "
        "strategy_completeness_snapshot 三表與 persistence,event 只存 private_content_ref + redacted_summary(私人原文進加密 "
        "store,不落 event payload)。Workshop session 不複製 StrategySpec 真相,只引用 registry draft id。含 §22.6 索引 "
        "(workshop_id,created_at) 等與 §17.2 list/create/get workshop endpoint。",
        "Claude", "Claude2", "EPIC AGORA-SW / Phase 2", "AG-BE-ID-001",
        "可建立/讀取 workshop session 與 event;event 不含私人原文(只 ref+redacted);completeness snapshot 可持久化;migration 與索引到位;附測試",
        "services/control-plane/bff/agora/strategy_workshop.py,services/control-plane/specs/agora/strategy_workshop.schema.json,services/control-plane/specs/agora/strategy_completeness.schema.json",
    ),
    (
        "AG-BE-SW-002",
        "StrategySpec draft patch/version linkage",
        "依 SD §6/§7.4(VersionPatchProposal)整合既有 research strategy_spec:workshop_projection / patching / version_compare,"
        "提供 draft create/patch、version create/select(§17.2 versions / select-version),patch 以 JSON path from/to 形式套用,"
        "version 比較產生 diff。draft 真相留在既有 Registry,workshop 只引用。",
        "Claude2", "Claude", "EPIC AGORA-SW / Phase 2", "AG-BE-SW-001",
        "可對 StrategySpec draft 套 patch 產生新 version 並選定;version diff 正確;真相落既有 Registry 不重複;附 patch/compare 單元測試",
        "services/research/strategy_spec/workshop_projection.py,services/research/strategy_spec/patching.py,services/research/strategy_spec/version_compare.py",
    ),
    (
        "AG-BE-SW-004",
        "Streaming workshop aggregate",
        "依 SD §17.2(workshops/{id}/stream)與 §8.3 串接 workshop SSE aggregate:把 message ack、completeness 更新、"
        "research progress、version 事件以 streaming 聚合推給前端;首個 acknowledgement < 2s,長任務走 progress。沿用 AG-BE-ID-003 "
        "的 SSE 機制與 §8.2 audit 欄位,OpenClaw 降級回 OPENCLAW_UPSTREAM_DEGRADED。",
        "Codex", "Claude", "EPIC AGORA-SW / Phase 2", "AG-BE-SW-001",
        "workshop stream 以 SSE 聚合推 completeness/progress/version 事件;首 ack < 2s;斷線可重連;事件帶 trace/audit;附整合測試",
        "services/control-plane/bff/agora/strategy_workshop.py,services/control-plane/bff/agora/research.py",
    ),
    (
        "AG-FE-SW-001",
        "TradingDeskShell + Strategy Workshop tab",
        "依 SD §10.4/§11.1 在 execute-plans 建立 TradingDeskShell(頂部命令列、三主頁籤、右側抽屜、底部 jobs/shadow/journal)"
        "與策略工坊頁籤骨架,route /agora/strategy-workshop(:workshopId)。BFF client workshops.ts 走 live strict(禁止頁面直接 fetch)。"
        "只接 Agora-scoped BFF,不揭露 Management。",
        "Claude", "Codex", "EPIC AGORA-FE / Phase 2", "AG-FE-ID-001",
        "TradingDeskShell 三頁籤可切換且 deep-link;策略工坊頁可建立/載入 workshop;workshops.ts live strict;Agora bundle 不含 management;附前端測試",
        "execute-plans/src/agora/TradingDeskLayout.tsx,execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx,execute-plans/src/lib/bff-v1/agora/workshops.ts",
    ),
    (
        "AG-FE-SW-002",
        "Conversation/result cards + completeness rail",
        "依 SD §11.1/§11.2 實作策略工坊左側對話與結果卡(UserStrategyDescription / ServantReconstruction / CompletenessUpdate / "
        "NextQuestion / ResearchPlanProposal / ResearchProgress / ConsultResult / EvidenceSummary / BacktestResult / "
        "VersionPatchProposal)與右側 StrategyCompletenessRail(已確認/推定/缺漏/薄弱/衝突 + 下一個高價值決策)。卡片資料來自 "
        "AG-BE-SW-004 stream。",
        "Claude", "Codex", "EPIC AGORA-FE / Phase 2", "AG-FE-SW-001",
        "對話卡片型別齊全且由 stream 驅動;completeness rail 反映五態 + next question;卡片可繼續討論(§11.3 入口);附 reducer/UI 測試",
        "execute-plans/src/agora/components/StrategyCompletenessRail.tsx,execute-plans/src/agora/components/ResearchPlanCard.tsx,execute-plans/src/agora/components/ConsultResultCard.tsx",
    ),
    (
        "AG-FE-SW-003",
        "Version comparison and readiness UI",
        "依 SD §7.4/§11.3/§6.4 實作 VersionCompareCard(多版本 diff + 預測效果)與 readiness UI(Preliminary / Full validation / "
        "Trading-room readiness 三 gate 的狀態與缺項),並把『加入交易作戰室』按鈕在未達 readiness 時 disable。資料來自 AG-BE-SW-002。",
        "Claude2", "Codex", "EPIC AGORA-FE / Phase 2", "AG-FE-SW-002",
        "可比較策略版本與套用建議版本;三 readiness gate 狀態正確顯示;未達 readiness 時無法加入交易作戰室;附 UI 測試",
        "execute-plans/src/agora/components/VersionCompareCard.tsx,execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx",
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
            print(f"ASSIGN  {task_id:16} owner={owner:8} reviewer={reviewer:8} deps={deps or '-'}")
    print("Done." if ok else "Completed with failures.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
