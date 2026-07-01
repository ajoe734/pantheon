#!/usr/bin/env python3
"""Dispatch Agora design-pack dynamic UI completion tasks.

Source design pack:
  /home/lupin/code/pantheon/AI Trading Desk Design.zip

This script deliberately assigns dynamic UI contract/runtime work before visual
parity work. The V10/V11 design is not a static-page conversion: it requires
workspace proposals, widget revision proposals, edit mode, per-widget servant
context, version history, and rollback.

Repo routing follows the existing artifact-prefix convention:
  artifacts starting with "execute-plans/" -> execute-plans repo
  all other artifacts -> pantheon repo
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_BY = "dispatch_agora_design_pack_dynui_2026-06-28"

DESIGN_SOURCES = (
    "動工前必讀 /home/lupin/code/pantheon/AI Trading Desk Design.zip；"
    "主要檔案為 uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V10_Expert_Strategy_Dialogue_2026-06-18.md、"
    "uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md、"
    "uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V6_MultiStrategy_Dashboard_2026-06-18.md、"
    "uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V4_AI_Dashboard_Control_2026-05-20.md、Agora.dc.html、"
    "screenshots/01-v10-mid.png、02-v10-mid.png、01-applied.png、01-aifix.png。"
)

BLOCKER_RULE = (
    " 【有疑問一定要提出,不要自己亂做】若設計稿讀不到、V10/V11 與既有 schema 或 code 衝突、"
    "依賴不清、欄位/route/widget/互動未定義、或驗收不可重現,一律 STOP 並開 blocker；"
    "不得自行補欄位、補 route、補 widget、改語意、繞過 validator、或先做再說。"
)

DYNAMIC_RULE = (
    " 【這不是靜態頁面切版】交付必須支援 V10/V11 的動態 UI 系統："
    "Strategy Workshop 由事件/stream 驅動；Trading Room 由 TradingRoomWorkspaceProposal 生成完整 views/widgets；"
    "widget 以受控 WidgetSpec/ChartSpec 宣告；trader 可 drag/resize/add/remove/restore/change chart；"
    "點 widget 可開啟帶 context 的 servant adjustment；servant 只能先產生 WidgetRevisionProposal 與 before/after preview；"
    "workspace version/change log/rollback 必須可用。"
)

UI_RULE = (
    " 【UI 一律照設計稿,不要自己發想】畫面、版面、元件、widget menu、drawer、preview、文案、樣式、互動狀態"
    "都要對齊設計包與 docs/04/agora_design_pack_dynui_2026-06-28/README.md；"
    "不得用白底舊版 skeleton、不得做 landing page、不得把設計稿降級為一組硬編 mock cards。"
)

SAFETY_RULE = (
    " 【安全邊界】Agora 不得直接下單、不得綁資金、不得暴露 Management/RuntimeBinding/broker 後台詞彙；"
    "agent 不得生成任意 React/JavaScript/HTML 並注入 production；所有 widget/chart 必須通過 allowlist validator。"
)

ACC_RULE = (
    ";【共同驗收】已讀設計包並在交付證據中列出引用檔案；無自創欄位/route/enum/widget；"
    "遇疑問先 blocker；local validation + relevant tests + screenshot/E2E evidence 齊全；"
    "任何只做靜態頁或硬編 mock state 的交付不通過"
)


# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    (
        "AG-DYNUI-SRC-001",
        "Freeze Agora design-pack dynamic UI source map",
        "凍結 AI Trading Desk Design.zip 的任務 source map：整理 V10 Strategy Workshop、V11 WinnerBranch TradingRoom、"
        "V6/V4 dashboard-control、Agora.dc.html 動態 prototype 與 screenshots 的引用位置；產出 current implementation gap map，"
        "明確列出不得用靜態頁替代的 dynamic invariants。若 zip 不在 worker 環境或設計稿與現有 schema 衝突，先 blocker。",
        "Codex",
        "Claude",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Intake",
        "",
        "docs source map 列出 V10/V11/V6/V4/Agora.dc.html/screenshots 引用與重點; gap map 區分已完成 skeleton 與缺失 dynamic behavior; blocker list 已建立或確認無 blocker",
        "docs/04/agora_design_pack_dynui_2026-06-28/,services/control-plane/specs/agora/,execute-plans/src/agora/",
    ),
    (
        "AG-BE-DYNUI-001",
        "Trading Room workspace proposal contract",
        "依 V11 §5/§12/§13 建立 TradingRoomWorkspaceProposal、TradingRoomViewSpec、TradingRoomWidgetSpec、"
        "TradingRoomWorkspace、WidgetPlacement 的 schema/model/persistence/validator 與 BFF routes："
        "POST/GET/accept trading-room proposals、GET workspace、PATCH layout、POST/PATCH views。"
        "proposal 必須一次包含完整 view set、widgets、rationale、dataAvailability、warnings、personalizationApplied。",
        "Claude",
        "Claude2",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Backend Contracts",
        "AG-DYNUI-SRC-001,AG-BE-TR-001,AG-BE-DB-001",
        "Workspace proposal schema/model 與 V11 欄位一致; proposals accept 後建立 per trader/per strategy/per strategy version workspace; PATCH layout/views 有 optimistic concurrency; tests cover scope isolation",
        "services/control-plane/bff/agora/trading_room.py,services/control-plane/bff/agora/dashboard.py,services/control-plane/specs/agora/trading_room_workspace.schema.json,services/control-plane/specs/agora/widget_spec.schema.json",
    ),
    (
        "AG-BE-DYNUI-002",
        "Widget revision proposals and workspace versioning",
        "依 V11 §8/§10/§12/§13 建立 WidgetRevisionProposal 與 DashboardVersion/WorkspaceVersion contract："
        "POST widget revision-proposals、accept、keep original and add modified copy、GET versions、rollback。"
        "accept 時必須保留 beforeSpec/proposedSpec/rationale/warnings/dataAvailability/status，並記錄 change log。",
        "Claude2",
        "Claude",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Backend Contracts",
        "AG-BE-DYNUI-001",
        "Revision proposal 不能直接 mutate widget; accept/apply/keep-copy/cancel 狀態正確; version history per user/strategy/version; rollback 可回到指定版本; concurrency 與 cross-user isolation 測試通過",
        "services/control-plane/bff/agora/trading_room.py,services/control-plane/bff/agora/dashboard.py,services/control-plane/specs/agora/widget_revision_proposal.schema.json",
    ),
    (
        "AG-BE-DYNUI-003",
        "Servant workspace generator and safe widget validator",
        "整合 trading servant workspace generator：由 ready StrategySpec version 產生完整 TradingRoomWorkspaceProposal，"
        "包含 V11 要求的多 view workspace、widget reasons、data availability、warnings、personalizationApplied。"
        "所有 WidgetSpec/ChartSpec 必須走 allowlist validator；若 renderer 不支援，回傳 supported fallback 或建立新 component task request，"
        "不得任意生成 frontend code。",
        "Claude",
        "Codex",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Backend Servant Runtime",
        "AG-BE-DYNUI-001,AG-BE-DYNUI-002",
        "Winner Branch strategy 可生成 complete workspace proposal; unsupported renderer 走 fallback 或 component-task request; validator blocks arbitrary code and unsupported data sources; evidence refs and data freshness are preserved",
        "services/control-plane/bff/agora/trading_room.py,integrations/openclaw/skills/agora/,services/control-plane/specs/agora/widget_registry.v1.json",
    ),
    (
        "AG-XR-DYNUI-001",
        "Dynamic Trading Room OpenAPI and generated frontend types",
        "把 AG-BE-DYNUI-001/002 的 dynamic Trading Room contracts 補進 OpenAPI/schema bundle，"
        "並在 execute-plans 重新生成 Agora BFF client types。加 drift check，確保 frontend 的 TradingRoomWorkspaceProposal、"
        "TradingRoomWidgetSpec、WidgetRevisionProposal 與 backend schema checksum 一致。",
        "Codex",
        "Claude",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Cross Repo Contract",
        "AG-BE-DYNUI-001,AG-BE-DYNUI-002",
        "OpenAPI 包含 V11 §13 endpoints; generated types match schemas; drift check fails on stale types; execute-plans BFF clients use generated types and strict live mode",
        "services/control-plane/openapi/agora_v1.openapi.yaml,services/control-plane/specs/agora/,execute-plans/src/lib/bff-v1/agora/types.ts,execute-plans/src/lib/bff-v1/agora/tradingRoom.ts",
    ),
    (
        "AG-FE-DYNUI-001",
        "V10 Strategy Workshop dynamic runtime",
        "依 V10 建立 Strategy Workshop 動態 runtime：不是聊天頁與不是表單。長描述送出後第一回應必須插入 Strategy Reconstruction Card；"
        "左側 conversation/result cards 由 stream/event 驅動；右側 12 strategy blocks 顯示 confirmed/inferred/missing/weak/conflict；"
        "composer 支援交代策略、修改規則、要求研究、質疑結果、指定版本、要求重跑；join Trading Room readiness gate 必須真實控制。",
        "Claude",
        "Codex",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Frontend Runtime",
        "AG-DYNUI-SRC-001,AG-FE-SW-001,AG-FE-SW-002,AG-BE-SW-004",
        "Strategy Reconstruction Card appears before questions; 12-block rail state derives from data not hardcoded copy; readiness disables join until gates pass; stream/reducer tests cover reconstruction/completeness/version/research events",
        "execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx,execute-plans/src/agora/components/StrategyCompletenessRail.tsx,execute-plans/src/agora/components/StrategyReconstructionCard.tsx",
    ),
    (
        "AG-FE-DYNUI-002",
        "V11 Trading Room proposal preview and workspace shell",
        "依 V11 §5 建立按下加入交易操盤室後的 generation progress、完整 Workspace Proposal preview、所有 View thumbnails、"
        "每個 View 的 widget count/purpose/data availability/warnings/personalization applied，以及 accept 後的 workspace shell。"
        "不得直接進入空白 dashboard 或舊版 Trading Desk skeleton。",
        "Claude2",
        "Codex",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Frontend Runtime",
        "AG-XR-DYNUI-001,AG-BE-DYNUI-003,AG-FE-DYNUI-001,AG-FE-TR-001",
        "Proposal preview shows all generated views and widget counts; accept creates workspace and active view; empty/static fallback is impossible in strict mode; Playwright covers join-to-preview-to-accept",
        "execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx,execute-plans/src/agora/trading-room/WorkspaceProposalPreview.tsx,execute-plans/src/lib/bff-v1/agora/tradingRoom.ts",
    ),
    (
        "AG-FE-DYNUI-003",
        "Trading Room grid editor and personalization events",
        "依 V11 §6/§7/§9/§10 建立 dashboard/view editor runtime：view tabs、grid drop targets、drag handles、resize handles、"
        "remove/restore/more menu、add widget、change chart、duplicate、save/discard unsaved changes、PATCH layout、版本 bump、personalization event。"
        "佈局必須使用 TradingRoomWidgetSpec.placement，不可用硬編 CSS 排列假裝可拖曳。",
        "Claude",
        "Codex",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Frontend Runtime",
        "AG-FE-DYNUI-002,AG-BE-DYNUI-001",
        "Drag/resize/remove/restore/add/change chart update declarative placements; unsaved save/discard works; layout PATCH and personalization event are emitted; tests cover edit mode and persistence",
        "execute-plans/src/agora/trading-room/DashboardGridEditor.tsx,execute-plans/src/agora/widgets/WidgetRenderer.tsx,execute-plans/src/agora/widgets/ChartSpecRenderer.tsx",
    ),
    (
        "AG-FE-DYNUI-004",
        "Widget adjustment drawer and before-after revision flow",
        "依 V11 §8 建立 widget-level servant adjustment：點任何 widget 可開 drawer；drawer 顯示 widget purpose/dataSource/fields/filter/window/chart/"
        "strategy/view/evidence context；交易員可輸入調整需求；BFF 回 WidgetRevisionProposal；UI 顯示 before/after preview；"
        "操作支援套用修改、再調整、保留原圖並新增一張、取消。",
        "Claude2",
        "Claude",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Frontend Runtime",
        "AG-FE-DYNUI-003,AG-BE-DYNUI-002",
        "Drawer context is populated from selected widget and evidence refs; revision proposal never mutates before acceptance; before/after preview renders both specs; apply/adjust/keep-copy/cancel paths are tested",
        "execute-plans/src/agora/widgets/WidgetRevisionDrawer.tsx,execute-plans/src/agora/widgets/WidgetBeforeAfterPreview.tsx,execute-plans/src/lib/bff-v1/agora/tradingRoom.ts",
    ),
    (
        "AG-FE-DYNUI-005",
        "Design-pack visual parity on top of dynamic runtime",
        "在 AG-FE-DYNUI-001~004 完成後才做視覺 parity。依 screenshots 與 Agora.dc.html 對齊 dark AGORA shell、Strategy Workshop、"
        "Trading Room proposal、dashboard editor、widget menu、revision drawer、change log、versions modal。"
        "這個任務只負責讓已存在的動態 runtime 長得像設計稿，不得改回 hardcoded demo UI。",
        "Claude",
        "Codex",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Visual Parity",
        "AG-FE-DYNUI-001,AG-FE-DYNUI-002,AG-FE-DYNUI-003,AG-FE-DYNUI-004",
        "Playwright screenshots match 01-v10-mid/02-v10-mid/01-applied/01-aifix key layouts; mobile/desktop no overlap; visual state is driven by real proposal/workspace/revision data; no static-only mock screen accepted",
        "execute-plans/src/agora/agoraDesign.css,execute-plans/src/agora/TradingDeskLayout.tsx,execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx,execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx",
    ),
    (
        "AG-E2E-DYNUI-001",
        "Winner Branch dynamic UI end-to-end acceptance",
        "寫完整 E2E：交易員輸入長篇 Winner Branch V4 假說 -> Strategy Reconstruction Card -> 12-block completeness -> readiness -> "
        "join Trading Room -> workspace proposal preview -> accept -> edit grid -> widget revision proposal -> before/after -> keep original and add copy -> "
        "version history -> rollback。斷言 no direct order、no arbitrary frontend code、strict live BFF、cross-user scope isolation。",
        "Codex",
        "Claude",
        "EPIC AGORA-DESIGN-PACK-DYNUI / Acceptance",
        "AG-BE-DYNUI-003,AG-XR-DYNUI-001,AG-FE-DYNUI-005",
        "E2E covers full V10-to-V11 dynamic workflow; screenshot artifacts attached; no direct broker order path; widget specs all pass validator; rollback restores prior layout; CI/local validation documented",
        "services/control-plane/tests/agora/test_winner_branch_dynamic_ui_e2e.py,execute-plans/tests/agora/winner-branch-dynui.spec.ts",
    ),
]


def _run(cmd: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)


def _task_env(summary: str, phase: str, deps: str, acceptance: str, artifacts: str) -> dict[str, str]:
    full_summary = DESIGN_SOURCES + " " + summary + BLOCKER_RULE + DYNAMIC_RULE + UI_RULE + SAFETY_RULE
    return {
        "TASK_SUMMARY_ZH": full_summary,
        "TASK_PHASE": phase,
        "TASK_DEPENDS_ON": deps,
        "TASK_ACCEPTANCE": acceptance + ACC_RULE,
        "TASK_ARTIFACTS": artifacts,
        "TASK_AUTO_CREATED_BY": AUTO_BY,
    }


def _print_dry_run(task: tuple[str, str, str, str, str, str, str, str, str]) -> None:
    task_id, title, _summary, owner, reviewer, phase, deps, acceptance, artifacts = task
    print(f"DRYRUN {task_id:18} owner={owner:8} reviewer={reviewer:8} deps={deps or '-'}")
    print(f"       title={title}")
    print(f"       phase={phase}")
    print(f"       artifacts={artifacts}")
    print(f"       acceptance={acceptance}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print task assignments without mutating ai-status.json.")
    args = parser.parse_args(argv)

    ok = True
    for task in TASKS:
        task_id, title, summary, owner, reviewer, phase, deps, acceptance, artifacts = task
        if args.dry_run:
            _print_dry_run(task)
            continue

        r = _run(
            [sys.executable, "scripts/ai_status.py", "assign", task_id, owner, reviewer, title],
            env_extra=_task_env(summary, phase, deps, acceptance, artifacts),
        )
        if r.returncode != 0:
            print(f"ASSIGN FAIL {task_id}: {r.stderr.strip() or r.stdout.strip()}", file=sys.stderr)
            ok = False
        else:
            print(f"ASSIGN  {task_id:18} owner={owner:8} reviewer={reviewer:8} deps={deps or '-'}")

    print("Dry run complete." if args.dry_run else ("Done." if ok else "Completed with failures."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
