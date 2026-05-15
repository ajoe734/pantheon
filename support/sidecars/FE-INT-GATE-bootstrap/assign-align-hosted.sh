#!/usr/bin/env bash
# Align 9 failing FE-INT-GATE specs to hosted Lovable's actual DOM/UI.
# Each task targets one spec; worker must run npx playwright test --headed
# (or trace mode) against PANTHEON_FE_BASE_URL, capture real DOM/network,
# then adjust selectors / assertions / fixtures until the spec runs green
# in CI hard-gate mode.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PHASE="Pantheon FE Integration Gate 2026-05-13"
BRANCH="bff-luv-fe-006-dev-deploy"
FE_BASE="https://pantheon-dev.lovable.app"
BFF_BASE="https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io"

EVIDENCE="execute-plans/.lovable/audits/baseline/hardgate-postmerge/pantheon-integration-evidence/.lovable/audits/current-run/playwright-results.json"

WORKING_RULES="必須 (1) PANTHEON_FE_BASE_URL=${FE_BASE} PANTHEON_BFF_BASE_URL=${BFF_BASE} 環境下實際跑 npx playwright test <file> --trace=on / --headed，產出 playwright-report/ 抓真實 DOM；(2) 不可以憑空猜 selector；(3) 對 hosted Lovable 已 render 的 UI 對齊 assertion，不可降級 acceptance；(4) 若發現 hosted Lovable 真有 product gap（非 selector 錯），在 task next 註明並 file follow-up；(5) 修完後本地 npx playwright test 對該 spec 至少連續 2 次綠；(6) closeout commit 必須在 /home/lupin/code/execute-plans/ 上 bff-luv-fe-006-dev-deploy branch（不要再產生 phantom mirror，FE-INT-GATE-A10 處理 root cause）。"

assign_align() {
  local id="$1" spec="$2" flow="$3" symptoms="$4"
  TASK_PHASE="$PHASE" \
  TASK_BRANCH="$BRANCH" \
  TASK_SUMMARY_ZH="hard-gate 首次 run 25846710728 (commit 4774678) e2e step fail：${flow} spec ${spec} 對 hosted Lovable assertion 對不上。Symptoms: ${symptoms}. ${WORKING_RULES}" \
  TASK_ARTIFACTS="execute-plans/e2e/${spec},${EVIDENCE}" \
  TASK_ACCEPTANCE="cd execute-plans 後 npx playwright test e2e/${spec} 連續 2 次全綠,assertion 用真實 hosted Lovable DOM/network 對齊,不可降級 blueprint pass condition,若 hosted Lovable 真有 product gap 改 file follow-up task 而非 mask spec,closeout commit 在 execute-plans repo bff-luv-fe-006-dev-deploy branch" \
  python3 scripts/ai_status.py assign "$id" Codex2 Claude "Align ${spec} to hosted Lovable DOM"
}

assign_align FE-INT-GATE-ALIGN-F01 01-startup-session.spec.ts "F01 startup session" \
  "MeResponse shape assert、strict 模式 serving-mock banner 缺、SSE EventSource open、401 不 fallback mock 4 個 case 全 fail"

assign_align FE-INT-GATE-ALIGN-F02 02-control-room.spec.ts "F02 Control Room" \
  "KPI cards / loops / sentinel / interventions render、drill-down 3 條 link、empty data 5 個 case 全 fail"

assign_align FE-INT-GATE-ALIGN-F03 03-execution-loop.spec.ts "F03 execution loop persona health" \
  "PersonaHealthMatrix mode/status/score/strategies/findings render、redacted evidence metadata-only 2 個 case 全 fail"

assign_align FE-INT-GATE-ALIGN-F04 04b-optimization-loop.spec.ts "F04 optimization loop timeline" \
  "ranking → rebalance → approval → apply → promotion stage render、awaiting approval link 到 Approvals/HIQ 2 個 case 全 fail"

assign_align FE-INT-GATE-ALIGN-F05 04-sentinel-remediation.spec.ts "F05 Sentinel remediation" \
  "CONFIRM_TOKEN_REQUIRED non-success precondition、advisory action queue 2 個 case 全 fail"

assign_align FE-INT-GATE-ALIGN-F07 06-entity-registry.spec.ts "F07 Entity Registry" \
  "12 registry surface fixture-backed list render fail（可能 fixtures.ts 提供的 seeded id 在 hosted Lovable 對應實體不存在或 selector 不對）"

assign_align FE-INT-GATE-ALIGN-F15 09-strict-vs-hybrid.spec.ts "F15 strict vs hybrid" \
  "strict 5xx 注入 fail-closed 沒看到 mock data 的 assertion fail（可能 5xx 注入 mock 機制沒生效或 banner selector 抓錯）"

assign_align FE-INT-GATE-ALIGN-F17 17-a11y-v5.spec.ts "F17 a11y" \
  "8 case 全 fail：6 個 v5 頁面 axe critical/serious=0、focus return to trigger、ESC 只關最上層 overlay。WARNING：F17 fail 可能是 hosted Lovable 真有 a11y product gap；如是真 violation，須 file follow-up product 修補 task 而非 mask spec"

assign_align FE-INT-GATE-ALIGN-F18 18-perf.spec.ts "F18 perf soft-fail" \
  "Control Room load timeout、LineageGraph >500 nodes warning 2 個 case 全 fail。可能 SSE rerender 真的爆量或 budget 閾值不切實際；如 budget 過嚴，修閾值並在 task 中說明依據"

echo "Done: 9 ALIGN tasks assigned."
