#!/usr/bin/env python3
"""Dispatch Agora Wave U1 — Phase 3 (Research) + Phase 4 (Candidate/TradingRoom/Dashboard).

Spec (all committed to dev, workers MUST follow exactly):
  docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/   (A1-A4, B1-B3, C1-C4, matrix)
  services/control-plane/specs/agora/*.schema.json               (canonical schemas from AG-XR-001)
  services/control-plane/openapi/agora_v1.openapi.yaml
  services/control-plane/specs/agora/capability_manifest.json

This is the dependency closure that unblocks the design-closure "Wave U1": the
Phase 3 research/consult parents + Phase 4 candidate/trading-room/dashboard
tasks (incl. AG-BE-CP-001 / AG-BE-DB-001 / AG-FE-DB-001 / AG-FE-TR-002 from the
unblock matrix). depends_on gates everything against Phase 0/1/2.

Repo routing: artifacts starting "execute-plans/" -> execute_plans repo; else pantheon.
Owner policy: Claude -> Claude2 -> Codex (never Codex2 / Antigravity).
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_BY = "dispatch_agora_wave_u1_phase3_4_2026-06-20"

# Appended to every task summary — RAISE-BLOCKER-FIRST design-adherence contract.
DESIGN_RULE = (
    " 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + "
    "docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical "
    "services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。"
    "只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,"
    "用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。"
    "可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、"
    "不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。"
)
# Extra UI clause appended only to FE / UI tasks.
UI_RULE = (
    " 【UI 一律照設計稿,不要自己發想】凡與畫面有關(頁面、route、layout、component、widget、chart、互動、文案、樣式)"
    "必須嚴格依 SD §9/§10/§11/§12/§23 的 IA/版面/元件規格、design-closure A3 widget_registry/chart grammar,"
    "以及 V10/V11 視覺參考實作;沿用既有 design tokens 與共用元件,不得自創畫面、元件、版面、route 或自由發揮樣式。"
    "設計稿沒涵蓋到的畫面或互動,先開 blocker 問清楚再做。"
)
# Appended to every acceptance line.
ACC_RULE = (
    ";【驗收】實作與引用 spec/schema 逐欄位一致,無自創欄位/route/enum;"
    "遇疑問須先開 blocker 澄清而非自行實作;自行臆測或偏離設計稿一律不通過"
)

# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    # ---------------- Phase 3 — Research & Consultation ----------------
    (
        "AG-BE-RS-001",
        "ResearchPlan facade/router",
        "依 SD §7.1/§7.2/§17.2 與 specs/agora/research_plan.schema.json 在既有 Research Orchestrator 上做 Agora ResearchPlan facade:"
        "plan create(draft/approve)、stage 規劃與工具路由(vectorbt/qlib/statsmodels/quantlib/finrl/rllib/ray_tune backendHint),不新增 duplicate worker。",
        "Claude", "Claude2", "EPIC AGORA-RS / Phase 3", "AG-BE-SW-002",
        "可由 StrategySpec 版本建立 ResearchPlan 並核准;stage 路由符合 §7.2;plan 形狀符合 research_plan.schema.json",
        "services/control-plane/bff/agora/research.py,services/research/strategy_spec/workshop_projection.py",
    ),
    (
        "AG-BE-RS-002",
        "Unified run/progress/result projection",
        "依 SD §7.3 與 specs/agora/research_run_summary.schema.json 做統一 ResearchRunSummary 投影:run/progress/result/metrics/"
        "artifactRefs/evidenceRefs,§17.2 research-runs list/create,SSE progress。研究工具不得寫 RuntimeBinding(§7.4 §9 治理鐵律)。",
        "Codex", "Claude", "EPIC AGORA-RS / Phase 3", "AG-BE-RS-001",
        "run 進度/結果以 ResearchRunSummary 形狀投影且可查證據;no-order-route 測試通過",
        "services/control-plane/bff/agora/research.py",
    ),
    (
        "AG-BE-RS-003",
        "Consult/committee/red-team ContextBundle workflows",
        "依 SD §5.6/§7.3 與 design-closure C1 expert-consult SPEC、B1 information-lead-proxy policy 做 consult/committee/red_team workflow:"
        "中央人格只收受限 ContextBundle(沿用 AG-BE-ID-004 redaction,raw_prompt_included=false),產出 ConsultMemo/RiskNote/CritiqueResult/EvidenceBundle。"
        "資訊領先只能產出 B1 允許的 proxy 並附 disclaimer,不得斷言內線/操縱。",
        "Claude2", "Claude", "EPIC AGORA-RS / Phase 3", "AG-BE-ID-004",
        "consult/committee/red_team 走受限 ContextBundle;輸出符合 C1 schema;B1 disclaimer 強制;raw prompt 永不外洩",
        "integrations/openclaw/skills/agora/expert-consult/,services/control-plane/bff/agora/research.py",
    ),
    (
        "AG-BE-RS-004",
        "Evidence/result synthesis skill",
        "依 design-closure C1 result-synthesis SPEC 與 SD §7 做 evidence-grounded 結果整合 skill:把多個 ResearchRunSummary + ConsultMemo 整合成"
        "可討論卡片資料(VersionPatchProposal/EvidenceSummary),每個結論必須 grounded 在 evidence_refs,不得無根據生成。",
        "Claude", "Codex", "EPIC AGORA-RS / Phase 3", "AG-BE-RS-002",
        "synthesis 輸出符合 C1 SPEC 且每結論有 evidence_refs;無 ungrounded 主張;附測試",
        "integrations/openclaw/skills/agora/result-synthesis/",
    ),
    (
        "AG-FE-RS-001",
        "Research plan/run/consult/backtest cards",
        "依 SD §11.2 做策略工坊的 ResearchPlanCard/ResearchRunCard/ConsultResultCard/BacktestResultCard,資料來自 AG-BE-RS-002 投影與 §7.4 "
        "backtest comparison 欄位;走 BFF client research.ts(live strict,禁止頁面直接 fetch)。",
        "Claude", "Codex", "EPIC AGORA-FE / Phase 3", "AG-FE-SW-002,AG-BE-RS-002",
        "四種研究卡由 BFF 投影驅動且欄位對齊 §7.3/§7.4;live strict 無 fallback;附 UI 測試",
        "execute-plans/src/agora/components/ResearchRunCard.tsx,execute-plans/src/agora/components/BacktestResultCard.tsx,execute-plans/src/lib/bff-v1/agora/research.ts",
    ),
    # ---------------- Phase 4 BE — Candidate / Dashboard / Trading Room ----------------
    (
        "AG-BE-CP-001",
        "CandidatePool/Member/Discussion/Monitoring records",
        "依 SD §8 與 specs/agora/candidate_pool.schema.json、design-closure A2(candidate_scoring_recipe.schema.json + "
        "winner_branch.default.json)做 candidate pool/member/discussion/monitoring 持久化與 §17.3 endpoint:score 必須由 A2 recipe 計算"
        "(score_components_json 對齊 recipe),rejected 候選保留為 negative example。",
        "Claude2", "Claude", "EPIC AGORA-CP / Phase 4", "AG-BE-RS-002",
        "candidate 形狀符合 schema;score 由 A2 recipe 算出且 components 對齊;rejected 保留;§17.3 endpoint 到位",
        "services/control-plane/bff/agora/research.py,services/control-plane/specs/agora/candidate_pool.schema.json",
    ),
    (
        "AG-BE-DB-001",
        "DashboardRecipe/WidgetSpec persistence and validator",
        "依 SD §9 與 design-closure A3(widget_registry.v1.json + widget_spec.schema.json + chart_spec.schema.json)、specs/agora/"
        "dashboard_recipe.schema.json 做 recipe/widget 持久化 + §9.6 validator:widgetType 必須在 widget_registry.v1.json、dataSource 在 allowlist、"
        "不得含 raw prompt/other-user/management-only/broker/JS-HTML;§17.5 endpoint + optimistic concurrency。前後端 registry checksum 必須一致。",
        "Claude", "Claude2", "EPIC AGORA-DB / Phase 4", "AG-BE-000",
        "recipe/widget 持久化符合 schema;validator 依 A3 registry 擋掉非法 widget/dataSource/注入;version 衝突回 DASHBOARD_RECIPE_VERSION_CONFLICT",
        "services/control-plane/bff/agora/dashboard.py,services/control-plane/specs/agora/dashboard_recipe.schema.json",
    ),
    (
        "AG-BE-TR-001",
        "Trading room aggregate and event queues",
        "依 SD §12/§13.1 與 specs/agora/trading_event.schema.json 做 trading room aggregate 與 entry/add/reduce/exit/review 事件佇列,§17.4 endpoint;"
        "事件欄位(confidence/probability/EV/rationale/riskNotes/evidenceRefs/invalidation)對齊 schema。",
        "Claude2", "Codex", "EPIC AGORA-TR / Phase 4", "AG-BE-CP-001",
        "trading room aggregate 與事件佇列符合 trading_event.schema.json;§17.4 endpoint 到位;附測試",
        "services/control-plane/bff/agora/trading_room.py,services/control-plane/specs/agora/trading_event.schema.json",
    ),
    (
        "AG-BE-TR-002",
        "Governed TradingIntent / handoff",
        "依 SD §12.4/§21 與 specs/agora/trading_intent.schema.json 做 governed TradingIntent/handoff:requestedMode 僅 shadow/paper/canary_request/"
        "live_request,canary/live 只建 request 不送 order(TRADING_INTENT_NOT_ALLOWED 守門),idempotencyKey 必填。絕不從 BFF 發 broker order。",
        "Codex", "Claude2", "EPIC AGORA-TR / Phase 4", "AG-BE-TR-001",
        "TradingIntent 符合 schema;canary/live 只 handoff 不下單;直接下單路徑被擋並有測試證明",
        "services/control-plane/bff/agora/trading_room.py,services/control-plane/specs/agora/trading_intent.schema.json",
    ),
    # ---------------- Phase 4 FE — Trading Room / Dashboard ----------------
    (
        "AG-FE-TR-001",
        "Trading Room tab + multi-strategy switcher",
        "依 SD §10.4/§12.1 在 TradingDeskShell 做交易作戰室頁籤 + 多策略 switcher(每策略獨立 workspace/view set),route /agora/trading-room(:strategyId);"
        "資料走 tradingRoom.ts(live strict)。",
        "Claude", "Codex", "EPIC AGORA-FE / Phase 4", "AG-FE-SW-001,AG-BE-TR-001",
        "交易作戰室頁籤可多策略切換且 deep-link;資料來自 §17.4 投影;live strict;附 UI 測試",
        "execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx,execute-plans/src/lib/bff-v1/agora/tradingRoom.ts",
    ),
    (
        "AG-FE-TR-002",
        "Candidate review and entry/position/exit queues",
        "依 SD §12.2/§12.3 與 A2 score components 做 CandidateReviewDrawer + entry/add/reduce/exit 佇列卡;候選分數須顯示 A2 多 component(不可只顯示單一數字);"
        "裝示產生 governed TradingIntent(經 AG-BE-TR-002),不從 UI 直接下單。",
        "Claude", "Codex", "EPIC AGORA-FE / Phase 4", "AG-FE-TR-001,AG-BE-CP-001",
        "候選審查與進出場佇列符合設計;score 顯示 A2 components;裝示走 governed intent;附 UI 測試",
        "execute-plans/src/agora/components/CandidateReviewDrawer.tsx,execute-plans/src/agora/components/TradeDecisionCard.tsx",
    ),
    (
        "AG-FE-DB-001",
        "WidgetRegistry/Renderer/ChartRenderer",
        "依 SD §9.8 與 design-closure A3(widget_registry.v1.json + widget_spec.schema.json + chart_spec.schema.json)做前端 WidgetRegistry/"
        "WidgetRenderer/ChartSpecRenderer:首發只渲染 registry active widgets,ChartSpec 依 A3 grammar 對應 echarts/recharts;前後端 registry checksum 一致。"
        "Agent 產出只能是宣告式 spec,前端不得 eval 任意 code。",
        "Claude2", "Codex", "EPIC AGORA-FE / Phase 4", "AG-FE-000",
        "WidgetRegistry/Renderer 依 A3 registry;ChartSpec 渲染對齊 chart_spec.schema.json;非 registry widget 不渲染;無任意 code 注入",
        "execute-plans/src/agora/widgets/registry.ts,execute-plans/src/agora/widgets/WidgetRenderer.tsx,execute-plans/src/agora/widgets/ChartSpecRenderer.tsx",
    ),
    (
        "AG-FE-DB-002",
        "Drag/resize/add/remove/change chart editor",
        "依 SD §9.1/§9.4/§9.8 做 DashboardGridEditor(react-grid-layout):drag/resize/add/remove/change-chart,佈局存成 WidgetPlacement(x/y/w/h/minW...);"
        "每次操作發 PersonalizationEvent(對齊 specs/agora/personalization_event.schema.json)。",
        "Claude", "Codex", "EPIC AGORA-FE / Phase 4", "AG-FE-DB-001",
        "grid 編輯產生符合 WidgetPlacement 的佈局;操作發 personalization_event;附 drag/resize 測試",
        "execute-plans/src/agora/dashboard/DashboardGridEditor.tsx",
    ),
    (
        "AG-FE-DB-003",
        "Widget conversation revision + before/after",
        "依 SD §9 §7.5(AG-FR-DB-003/004)做單一 widget 對話修改 + Before/After Preview:點選 widget→交代副人改呈現→產生新 WidgetSpec(經 AG-BE-DB-001 validate)→預覽前後差異。",
        "Claude", "Claude2", "EPIC AGORA-FE / Phase 4", "AG-FE-DB-001,AG-BE-DB-001",
        "可對單一 widget 以對話修改並顯示 before/after;新 spec 經後端 validate;附測試",
        "execute-plans/src/agora/widgets/WidgetRevisionDrawer.tsx",
    ),
    (
        "AG-FE-DB-004",
        "Recipe proposal/change log/version rollback",
        "依 SD §9.1/§12.2 做 DashboardRecipe proposal 預覽、change log 與 version rollback(對齊 dashboard_recipe.schema.json 的 version/previousVersionId/status);"
        "rollback 走 §17.5 endpoint 與 optimistic concurrency。",
        "Claude2", "Claude", "EPIC AGORA-FE / Phase 4", "AG-FE-DB-001,AG-BE-DB-001",
        "recipe 提案/變更紀錄/回滾符合 schema 與 §17.5;版本衝突有處理;附測試",
        "execute-plans/src/agora/dashboard/DashboardChangeLog.tsx,execute-plans/src/agora/dashboard/DashboardProposalPreview.tsx",
    ),
    # ---------------- Phase 4 E2E ----------------
    (
        "AG-E2E-TR-001",
        "Winner-branch strategy -> full trading room workspace",
        "依 SD §24.3 step 9-11 寫 winner-branch(賣家節點)策略→加入交易作戰室→產生/編輯/接受 dashboard recipe→產生交易事件與使用者裝示的 E2E;"
        "斷言 governed intent(不下單)、widget 全來自 registry、score 用 A2 components。",
        "Codex", "Claude", "EPIC AGORA-TR / Phase 4", "AG-FE-TR-002,AG-FE-DB-002",
        "賣家節點策略可走完交易作戰室全流程;無直接下單;widget/score 對齊設計;E2E 綠燈收錄 CI",
        "services/control-plane/tests/agora/test_winner_branch_trading_room_e2e.py",
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
        is_ui = task_id.startswith("AG-FE-") or "execute-plans/" in arts
        full_summary = summary + DESIGN_RULE + (UI_RULE if is_ui else "")
        env_extra = {
            "TASK_SUMMARY_ZH": full_summary, "TASK_PHASE": phase, "TASK_DEPENDS_ON": deps,
            "TASK_ACCEPTANCE": acc + ACC_RULE, "TASK_ARTIFACTS": arts, "TASK_AUTO_CREATED_BY": AUTO_BY,
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
