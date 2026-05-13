# Pantheon / Execute-Plans FE × BFF × Backend Integration Test Package

版本：2026-05-10-A  
適用 repo：
- Frontend: `ajoe734/execute-plans`
- Backend/BFF: `ajoe734/pantheon`

此包的目的不是再補概念規格，而是把「前端 UI 使用者 flow」轉成可跑的整合測試與 release gate。

## 內容

```text
docs/testing/Pantheon_FE_BE_Integration_Test_Blueprint_2026-05-10.md
docs/testing/User_Flow_Test_Matrix_2026-05-10.csv
docs/testing/Release_Gate_Checklist_2026-05-10.md

src/lib/bff-v1/__tests__/contract-drift.test.ts

scripts/probe-bff-routes.mjs
scripts/probe-bff-authenticated-live.mjs
scripts/probe-hosted-browser-bff.mjs

playwright.config.ts
e2e/helpers/bff.ts
e2e/helpers/env.ts
e2e/01-startup-session.spec.ts
e2e/02-control-room.spec.ts
e2e/03-execution-loop.spec.ts
e2e/04-sentinel-remediation.spec.ts
e2e/05-interventions.spec.ts
e2e/06-entity-registry.spec.ts
e2e/07-high-risk-confirm.spec.ts
e2e/08-sse-reconnect.spec.ts
e2e/09-strict-vs-hybrid.spec.ts
e2e/10-a11y-v5-smoke.spec.ts

.github/workflows/pantheon-integration-gate.yml
package.integration-scripts.patch.md
```

## 安裝與套用

1. 將本包內容複製到 `execute-plans` repo root。
2. 依 `package.integration-scripts.patch.md` 更新 `package.json`。
3. 安裝新增 dev dependencies：

```bash
npm install -D @playwright/test tsx
npx playwright install chromium
```

4. 先跑 FE contract drift 與 mock tests：

```bash
npm run test
npm run test:contract
```

5. 跑匿名路由註冊 probe：

```bash
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
node scripts/probe-bff-routes.mjs --anonymous
```

6. 跑 authenticated smoke，需 token：

```bash
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
PANTHEON_BFF_SMOKE_BEARER_TOKEN=... \
node scripts/probe-bff-authenticated-live.mjs
```

7. 跑 hosted browser probe：

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
node scripts/probe-hosted-browser-bff.mjs
```

8. 跑 Playwright E2E：

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
npm run e2e
```

## Release gate 核心原則

- `mock` 模式可用於本地開發與 UI 快速驗證。
- `hybrid` 模式可用於 demo，但不能作為整合通過依據。
- `strict` 模式是 CI / release gate 的基準；live BFF failure 不得 fallback mock。
- browser probe 必須證明部署後 bundle 指向正確 BFF URL，且沒有舊 URL。
- authenticated smoke 必須證明 BFF 不是只有 route 註冊，而是真的返回 contract DTO。
