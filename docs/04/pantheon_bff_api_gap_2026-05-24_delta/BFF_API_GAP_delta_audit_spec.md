# BFF API GAP — Delta Audit Dispatch Spec (2026-05-24)

Source audit: `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`
（Lovable live probe against `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`，2026-05-24 執行）

Baseline: `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`
（39 task dispatcher 2026-05-23，PR #497 archive 完成）

Dispatch sprint: `2026-05-24-pantheon-bff-p0-delta`
Dispatcher script: `scripts/dispatch_bff_gap_2026-05-24_delta.py`

---

## 1. Audit gap 三分類

Lovable 列出 26 missing path + 1 schema deviation + 1 CORS issue = 28 條，盤點到 `services/control-plane/bff/main.py` 後重分類如下。

### Class A — Code 已寫 但 live 仍 404／預檢失敗（部署落後 root cause）

| Path | 已歸檔 task | live 觀察 |
|---|---|---|
| `POST /bff/approvals/batch-decide` | BFF-B1-010 (done) | 404 |
| `GET /bff/command-confirmations/{token}` | BFF-B1-009 (done) | 404 |
| `GET /bff/management/cockpit` | BFF-B3-001 (done) | 404 |
| `GET /bff/management/persona-league/rankings` | BFF-PM12-005 (review_approved) | 404 |
| `GET /bff/management/quarterly-ranking` | BFF-PM12-006 (review_approved) | 404 |
| `GET /bff/management/performance-attribution` | BFF-PM12-009 (review_approved) | 404 |
| `GET /bff/management/portfolio-book` | BFF-PM12-001 (review_approved) | 404 |

**處置**: 不重派路由開發任務。新增單一 OPS 任務 `OPS-BFF-LUPIN-DEV-REDEPLOY-20260524`，職責：
1. 確認 `services/control-plane/bff` deploy pipeline 對應 master HEAD commit
2. 重新 build + push image to lupin dev BFF runtime
3. Ingress / sidecar OPTIONS 攔截檢查
4. 用 `Authorization: Bearer pantheon-dev-browser:reviewer` 對上表 7 條 path 全部 curl 200 驗收
5. OPTIONS preflight 對所有 path 回 204 + CORS headers

### Class B — Code 真的沒寫，需要新開 route — 19 條

#### B1 §8 PM-Live 12 條 → `EPIC-BFF-DELTA-MGMT-LIVE`

| Path | 任務 ID | spec ref |
|---|---|---|
| `GET /bff/management/persona-league/movers` | BFF-MGMT-DELTA-001 | §8.1 movers |
| `GET /bff/management/persona-league/heatmap` | BFF-MGMT-DELTA-002 | §8.1 heatmap |
| `GET /bff/management/strategy-allocation` | BFF-MGMT-DELTA-003 | §8.2 |
| `GET /bff/management/capital-flow` | BFF-MGMT-DELTA-004 | §8.3 |
| `GET /bff/management/risk-radar` | BFF-MGMT-DELTA-005 | §8.4 |
| `GET /bff/management/incident-timeline` | BFF-MGMT-DELTA-006 | §8.5 |
| `GET /bff/management/governance-ledger` | BFF-MGMT-DELTA-007 | §8.6 |
| `GET /bff/management/cost-attribution` | BFF-MGMT-DELTA-008 | §8.7 |
| `GET /bff/management/sentinel-pulse` | BFF-MGMT-DELTA-009 | §8.8 |
| `GET /bff/management/loop-throughput` | BFF-MGMT-DELTA-010 | §8.9 |
| `GET /bff/management/hiq-backlog` | BFF-MGMT-DELTA-011 | §8.10 |
| `GET /bff/management/intervention-stream` | BFF-MGMT-DELTA-012 | §8.11 |

#### B2 §9 PM-12 子路徑 7 條 → `EPIC-BFF-DELTA-PM12-SUB`

| Path | 任務 ID | 父任務（已 done）|
|---|---|---|
| `GET /bff/management/quarterly-ranking/drilldown` | BFF-PM12-DELTA-001 | BFF-PM12-006 |
| `GET /bff/management/performance-attribution/by-persona` | BFF-PM12-DELTA-002 | BFF-PM12-009 |
| `GET /bff/management/performance-attribution/by-strategy` | BFF-PM12-DELTA-003 | BFF-PM12-009 |
| `GET /bff/management/performance-attribution/by-pool` | BFF-PM12-DELTA-004 | BFF-PM12-009 |
| `GET /bff/management/portfolio-book/positions` | BFF-PM12-DELTA-005 | BFF-PM12-001 |
| `GET /bff/management/portfolio-book/exposure` | BFF-PM12-DELTA-006 | BFF-PM12-001 |
| `GET /bff/management/board-pack` | BFF-PM12-DELTA-007 | BFF-PM12-001 |

### Class C — Infra / Schema — 2 條 → `EPIC-BFF-DELTA-INFRA`

| 議題 | 任務 ID | Owner |
|---|---|---|
| CORS preflight regression（B1-001 已 done，live OPTIONS 仍 400） | BFF-B1-001-DELTA | Claude |
| Error envelope shape 偏差（`detail.error` → `error + meta.correlationId`） | BFF-INFRA-ENVELOPE-001 | Codex |

---

## 2. 任務總覽

| Sprint EPIC | 任務數 | Owner / Reviewer 對 |
|---|---|---|
| `EPIC-BFF-DELTA-INFRA` | 3（含 OPS redeploy） | Class A: Gemini2 / Claude · Class C: Claude/Codex + Codex/Claude |
| `EPIC-BFF-DELTA-MGMT-LIVE` | 12 | Codex / Claude（沿用 PM-Live owner 慣例） |
| `EPIC-BFF-DELTA-PM12-SUB` | 7 | Codex2 / Claude2（沿用 PM-12 owner 慣例） |
| **合計** | **22** | |

---

## 3. Class A redeploy 任務 acceptance

`OPS-BFF-LUPIN-DEV-REDEPLOY-20260524`

1. lupin dev BFF runtime image rebuild from `pantheon@master` HEAD
2. Image push + service rollout 完成；新 pod ready
3. CORS preflight：對 `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/me` 帶 `Origin: https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app` 的 OPTIONS 回 204 + ACAO/ACAH/ACAM/ACEH 完整
4. 下列 7 條 path 帶 `Authorization: Bearer pantheon-dev-browser:reviewer` curl 結果記錄於 closeout evidence（單一 path 必須 200，不可 404/500）：
   - `POST /bff/approvals/batch-decide`（dry-run body）
   - `GET /bff/command-confirmations/{token}`（用任一 issued token）
   - `GET /bff/management/cockpit`
   - `GET /bff/management/persona-league/rankings`
   - `GET /bff/management/quarterly-ranking?quarter=2026-Q2`
   - `GET /bff/management/performance-attribution`
   - `GET /bff/management/portfolio-book`
5. evidence 寫入 `support/evidence/bff-delta-20260524/redeploy-curl-results.md` + commit

---

## 4. Class B 路由 acceptance 共通條款

對 12 + 7 條新 route，每條任務 acceptance 包含：

1. Path 註冊在 `services/control-plane/bff/main.py`，FastAPI route 對齊 Pack D `/bff/*` namespace
2. 認證：`Authorization: Bearer pantheon-dev-browser:reviewer` 200；anonymous 401
3. Response envelope 符合 Pack D `{"data": {...}, "meta": {"correlationId": "..."}}` 形狀（與 Class C envelope 任務對齊）
4. CORS preflight 對該 path 回 204（依 Class C CORS 任務）
5. pytest contract test 加入 `services/control-plane/bff/test_bff_management_delta_routes.py`（一條 path 一個 test function）
6. FE side 對應 `execute-plans/src/lib/bff-v1/management.ts` 已有 mock 形狀；本任務不動 FE 側

各 path 的 response payload 細則：

### §8 PM-Live 12 條 payload outline
- `persona-league/movers`：top N persona 按 ranking delta 排序（promote/demote/idle 三 bucket）
- `persona-league/heatmap`：persona × time-bucket 二維 grid，cell = composite score
- `strategy-allocation`：active strategy 對 capital pool 的 allocation slice + drift
- `capital-flow`：deployment in/out flow events stream（窗口 24h / 7d）
- `risk-radar`：跨 persona/strategy 的 risk indicator 聚合（drawdown, exposure, var）
- `incident-timeline`：incidents 按時序 + severity bucket（高/中/低）
- `governance-ledger`：approvals / interventions / overrides 統一 audit ledger view
- `cost-attribution`：cost 拆分（LLM / data / runtime）by persona/strategy
- `sentinel-pulse`：sentinel digest 即時心跳（findings count, severity histogram）
- `loop-throughput`：v5 loop runs per minute / queue depth / lag
- `hiq-backlog`：HiQ queue current state（pending count, sla breach）
- `intervention-stream`：intervention events stream（per persona, last 24h）

### §9 PM-12 子路徑 7 條 payload outline
- `quarterly-ranking/drilldown`：父 ranking 中單一 persona 的 contribution breakdown
- `performance-attribution/by-persona`：attribution 按 persona 維度 group_by
- `performance-attribution/by-strategy`：按 strategy
- `performance-attribution/by-pool`：按 capital pool
- `portfolio-book/positions`：book 內 positions list（symbol, qty, mark, pnl）
- `portfolio-book/exposure`：exposure breakdown（asset class / sector / region）
- `board-pack`：composite payload 整合 cockpit + quarterly-ranking + sentinel pulse for board review export

---

## 5. Class C infra acceptance

### `BFF-B1-001-DELTA`（CORS regression）

1. 對 live BFF host 從 4 個 Lovable origin（preview UUID + lovableproject + pantheon-dev）發 OPTIONS：
   - `/bff/me`, `/bff/openapi.json`, `/health`, 任一 `/bff/management/*` path
   - 預期：204 + ACAO echoes origin + ACAH 完整 + ACAM 完整 + ACEH 完整
2. 排查 root cause 並修：可能在 (a) middleware order, (b) ingress preflight intercept, (c) ASGI router 404 before CORS。修法寫進 commit message 與 evidence
3. 與 OPS-BFF-LUPIN-DEV-REDEPLOY-20260524 共同收斂：redeploy 後 curl OPTIONS 驗 204

### `BFF-INFRA-ENVELOPE-001`（Error envelope deviation）

1. 修 `services/control-plane/bff/main.py` 中錯誤路徑回傳：
   - **舊**：`{"detail": {"error": {"code": "...", "message": "..."}}}`
   - **新**：`{"error": {"code": "...", "message": "..."}, "meta": {"correlationId": "<echo>"}}`
2. `meta.correlationId` 必須 echo 自 `X-Correlation-Id` request header；若 request 未帶，BFF 生 UUIDv4 並同步寫入 response header
3. 涵蓋所有 FastAPI exception handler：HTTPException, RequestValidationError, ValueError, generic 500
4. contract test：`services/control-plane/bff/test_bff_error_envelope_shape.py` 驗證 401/404/422/500 四種狀態各一 case
5. 不破壞既有 success envelope `{"data": ..., "meta": {...}}`

---

## 6. 與既有歸檔對應

| 已歸檔 task | 對應 delta task 處置 |
|---|---|
| BFF-B1-001 (CORS, done) | regression → 重派 `BFF-B1-001-DELTA` |
| BFF-B1-009 (confirm-token, done) | code OK → 由 OPS redeploy 任務驗收 |
| BFF-B1-010 (batch-decide, done) | code OK → 由 OPS redeploy 任務驗收 |
| BFF-B3-001 (cockpit, done) | code OK → 由 OPS redeploy 任務驗收 |
| BFF-PM12-001 (portfolio-book, approved) | code OK → 由 OPS redeploy 任務驗收 |
| BFF-PM12-005 (persona-league rankings) | code OK → 由 OPS redeploy 任務驗收 |
| BFF-PM12-006 (quarterly-ranking) | code OK → 由 OPS redeploy 任務驗收 |
| BFF-PM12-009 (performance-attribution) | code OK → 由 OPS redeploy 任務驗收 |

---

## 7. Dispatch 執行步驟

1. `python3 scripts/dispatch_bff_gap_2026-05-24_delta.py`
2. 22 個 task 進入 `ai-status.json`，sprint 更新為 `2026-05-24-pantheon-bff-p0-delta`
3. `python3 scripts/ai_status.py sync` 重新生 `current-work.md` / `dashboard-bundle.json`
4. Supervisor wake → 開始分派 worker

依賴鏈：
- `BFF-B1-001-DELTA` 與 `BFF-INFRA-ENVELOPE-001` 互不阻擋，可並行
- `OPS-BFF-LUPIN-DEV-REDEPLOY-20260524` 依賴 `BFF-B1-001-DELTA` + `BFF-INFRA-ENVELOPE-001` merge 後才能 redeploy 驗收
- Class B 19 條互不阻擋；deploy 流程不阻擋 code 撰寫，但合併後需 redeploy 才能 live 驗收
