#!/usr/bin/env bash
# Three F17 a11y product-fix tasks split out from FE-INT-GATE-ALIGN-F17.
# Evidence: hard-gate run 25858355877 (commit 8a5dfa6), test-results/17-a11y-v5-*
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PHASE="Pantheon FE Integration Gate 2026-05-13"
BRANCH="bff-luv-fe-006-dev-deploy"
EVIDENCE="execute-plans/.lovable/audits/baseline/postfix/pantheon-integration-evidence/test-results/"

TASK_PHASE="$PHASE" \
TASK_BRANCH="$BRANCH" \
TASK_SUMMARY_ZH="F17 a11y axe 8 case 中 6 個是 design token 帶 serious color-contrast 違規（共 6 個 v5 頁面同樣破）。具體 token / element：(1) bg-env-research-bg 上 text-env-research ratio=3.17（需 ≥4.5），出現在 button#radix env switcher；(2) bg-status-warning/10 上 text-status-warning ratio=1.97，出現在 UNVERIFIED BFF badge；(3) bg-sidebar (#0f1729) 上 text-sidebar-foreground/50 ratio=3.63，出現在 SideNav 7 個 category header（閉環 OS / 核心管理 / 研究與治理 / 營運 / 能力管理 / 系統 / Legacy（舊版））；(4) bg-status-warning/15 上 text-status-warning ratio=1.91，出現在「受監控 · 觀察」status badge。修：(a) tailwind.config.ts / index.css token 調暗或調亮對比達 4.5:1；(b) sidebar category header 改用 /70 或更高的 foreground 透明度；(c) status badge 加深 text 顏色或改 background；(d) env switcher color pair 重選。修完後 axe 對 6 個 v5 頁面（control-room、research/execution/optimization loop、sentinel、interventions）critical+serious=0。Evidence path: $EVIDENCE 17-a11y-v5-*color-contrast*/error-context.md" \
TASK_ARTIFACTS="execute-plans/tailwind.config.ts,execute-plans/src/index.css,execute-plans/src/platform/components/SideNav.tsx,execute-plans/src/platform/components/StatusBadge.tsx" \
TASK_ACCEPTANCE="6 個 v5 頁面 axe critical+serious=0,color-contrast ratio 全部 ≥4.5,design token 改動有 commit 訊息 reference token name,本地 npx playwright test e2e/17-a11y-v5.spec.ts 全綠" \
python3 scripts/ai_status.py assign FE-INT-GATE-A11Y-CONTRAST Codex2 Claude "Fix v5 design token color-contrast to 4.5:1"

TASK_PHASE="$PHASE" \
TASK_BRANCH="$BRANCH" \
TASK_SUMMARY_ZH="F17 a11y axe serious 中 list + listitem 違規：Breadcrumb 元件用 <ol> 直接含 <li class=contents>，render 結果 ol 直接帶 span，違反 list 結構規則（cat.structure / wcag131）。實際 HTML：<ol class=\"flex flex-wrap items-center gap-1.5 break-words text-sm text-muted-foreground sm:gap-2.5\"> 內含 li.inline-flex 但 li 是 'display: contents'，造成 li 的 child span 變成 ol 的直接 child。修：(1) 不要用 li.contents（破壞 list 語意）；(2) 改 Breadcrumb 結構讓 <li> 真實是 ol 直接 child；(3) 或不用 ol/li，改用 <nav aria-label=\"breadcrumb\">+<ol>+<li> 但去掉 contents class。修完後 axe list/listitem 規則無 violation。Evidence: $EVIDENCE 17-a11y-v5-*on-control-room*/error-context.md 內含 'List element has direct children that are not allowed: span'" \
TASK_ARTIFACTS="execute-plans/src/platform/components/Breadcrumb.tsx,execute-plans/src/components/ui/breadcrumb.tsx" \
TASK_ACCEPTANCE="<ol> 不再直接 children span,Breadcrumb 在所有 v5 頁面 axe list/listitem 規則 0 violation,nav aria-label='breadcrumb' 保持" \
python3 scripts/ai_status.py assign FE-INT-GATE-A11Y-BREADCRUMB Codex2 Claude "Fix Breadcrumb list semantic violation"

TASK_PHASE="$PHASE" \
TASK_BRANCH="$BRANCH" \
TASK_SUMMARY_ZH="F17 a11y axe 中 2 個 focus / overlay-stack 行為違規：(1) drawer focus returns to the trigger after keyboard close — 按 ESC 關 RightDrawer 後 focus 沒回到 open 按鈕；(2) ESC closes only the top overlay before closing the underlying drawer — 多層 overlay (Drawer + nested Dialog) 按一次 ESC 兩個一起關，應該只關最上層。修：(1) RightDrawer / 任何 overlay 用 ref 記住 trigger element，close 時 trigger.focus()；(2) overlay manager（可能在 src/platform/overlays/ 或 src/lib/overlayStack.ts）改成 stack-based ESC：只把最上層的 close 拿掉、不冒泡。Evidence: $EVIDENCE 17-a11y-v5-*focus*/error-context.md 與 17-a11y-v5-*top-overlay*/error-context.md" \
TASK_ARTIFACTS="execute-plans/src/platform/components/RightDrawer.tsx,execute-plans/src/platform/components/HighRiskConfirm.tsx,execute-plans/src/platform/overlays" \
TASK_ACCEPTANCE="drawer ESC close 後 trigger 取回 focus,多層 overlay ESC 只關最上層,axe focus 規則 0 violation,本地 npx playwright test e2e/17-a11y-v5.spec.ts 對 focus + ESC 2 個 case 全綠" \
python3 scripts/ai_status.py assign FE-INT-GATE-A11Y-OVERLAY Codex2 Claude "Fix drawer focus return and overlay stack ESC handling"

echo "Done: 3 a11y product tasks assigned (CONTRAST / BREADCRUMB / OVERLAY)."
