# C3 — execute-plans 漸進式 Monorepo 遷移計畫

> 狀態：Design Frozen v1.0  
> 決策：不另開 Agora repo；`execute-plans` 採同 repo 兩個 app、獨立 build/auth/deploy。先分 entry，再抽 packages，最後搬 apps；不阻塞產品主線。

---

## 1. 工具選擇

第一階段採 **npm workspaces + Vite**，不立即導入 Turbo/Nx。原因：現 repo 已使用 npm/Vite；先降低 migration 風險。若 CI 全量時間 > 15 分鐘或 package 數 > 12，再評估 Turbo。

---

## 2. 目標結構

```text
execute-plans/
├── apps/
│   ├── agora/
│   └── management/
├── packages/
│   ├── ui/
│   ├── design-tokens/
│   ├── contracts/
│   ├── bff-client/
│   ├── auth/
│   ├── realtime/
│   ├── charts/
│   ├── widget-runtime/
│   ├── dashboard-runtime/
│   ├── i18n/
│   └── test-utils/
└── package.json
```

---

## 3. 不等搬檔的 Phase M0

立即完成：

```text
src/entries/agora-main.tsx
src/entries/management-main.tsx
src/agora/AgoraApp.tsx
src/management/ManagementApp.tsx
agora.html
management.html
vite.agora.config.ts
vite.management.config.ts
```

Acceptance：

- Agora build 不含 `/management` route chunk。
- Management build 不含 Agora private stores。
- Auth audience、CSP、env、deployment artifact 分開。
- Path-based CI 可單獨 build/test/deploy。

---

## 4. Package 抽取順序

### M1 — Contracts

先抽：

```text
packages/contracts
packages/bff-client
```

因為跨 app drift 風險最高。Generated types 只從 OpenAPI/schema 產生，不手寫重複 DTO。

### M2 — Auth / Realtime

抽：

```text
packages/auth
packages/realtime
```

App-specific audience/policy 由 adapter config 注入。

### M3 — UI / Tokens / i18n

抽純視覺與工具：

```text
packages/ui
packages/design-tokens
packages/i18n
```

禁止把 Management route/action 放進 generic UI package。

### M4 — Charts / Widget / Dashboard

抽：

```text
packages/charts
packages/widget-runtime
packages/dashboard-runtime
```

Widget renderer 共用，WidgetRegistry allowlist 分 Agora/Management profile。

### M5 — App relocation

穩定後才將 `src/agora` 與 `src/management` 搬到 `apps/*`。

---

## 5. 開始搬 App 的門檻

全部達成：

- M0 雙 build 在 dev 穩定 14 日。
- Agora 三主頁籤骨架與 contracts 已 freeze。
- Shared package unit/contract tests 通過。
- No circular imports。
- 每個 app 可獨立部署與 rollback。
- 兩個 app 的 e2e baseline 已建立。

---

## 6. Dependency Rules

```text
apps/* -> packages/*
packages/* -X-> apps/*
Agora app -X-> Management app
Management app -X-> Agora private state
contracts package -X-> UI package
widget-runtime -> contracts + charts + ui only
```

由 ESLint boundaries 或 dependency-cruiser 驗證。

---

## 7. Branch / Release

```text
feature/agora-*
feature/management-*
feature/shared-*
→ execute-plans/dev
→ app-specific release artifact
→ main stable promotion
```

永久 `agora-dev`／`management-dev` 分支禁止，避免 shared package 漂移。

---

## 8. CI Matrix

Agora-only path：

```text
lint agora
test agora
contract tests
build agora
e2e agora
bundle leak scan
deploy agora dev
```

Management-only path 類似。

Shared path：兩 app 全測，但可分別 deploy。

---

## 9. Rollback

每一階段保持舊 import alias 1 個 release；build artifact 可回到上個 SHA。禁止 big-bang rewrite。

---

## 10. Definition of Done

- 雙 app 真正獨立 bundle/auth/deploy。
- 共用 contracts、client、renderer 不重複。
- App-specific route/code 不外洩。
- Path-based CI。
- 遷移期間產品開發可持續。
