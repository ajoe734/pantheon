# package.json patch for integration test package

目前 `execute-plans` 的 package.json 已有 Vite / React / Vitest，但沒有 Playwright。請加入以下 devDependencies 與 scripts。

## scripts

```json
{
  "scripts": {
    "test:contract": "vitest run src/lib/bff-v1/__tests__/contract-drift.test.ts",
    "probe:bff:routes": "node scripts/probe-bff-routes.mjs --anonymous",
    "probe:bff:auth": "node scripts/probe-bff-authenticated-live.mjs",
    "probe:browser": "node scripts/probe-hosted-browser-bff.mjs",
    "e2e": "playwright test",
    "e2e:headed": "playwright test --headed",
    "gate:integration": "npm run test && npm run build && npm run test:contract && npm run probe:bff:routes && npm run probe:browser && npm run e2e"
  }
}
```

## devDependencies

```json
{
  "devDependencies": {
    "@playwright/test": "^1.52.0",
    "tsx": "^4.19.0"
  }
}
```

`tsx` 目前不是必需，因為 probe scripts 是 `.mjs`，但保留給後續 TypeScript script / codegen 使用。

## install

```bash
npm install -D @playwright/test tsx
npx playwright install chromium
```
