# BFF-LUV-FE-001 Sidecar BFF Handoff Packet

Task ID: BFF-LUV-FE-001-SIDECAR-BFF-HANDOFF
Parent Task: BFF-LUV-FE-001
Helper kind: bff_handoff_packet
Owner: Claude
Reviewer: Codex2
Prepared: 2026-05-09T16:30:00Z

## Scope

Support-only sidecar for BFF-LUV-FE-001. Does not define canonical architecture, update route truth, or change runtime behavior. Downstream task owners (BFF-LUV-FE-002, -003, -004) may use this packet as a short reference for what transport/session foundation was laid and what each task must wire next.

## Delivered Foundation Summary

BFF-LUV-FE-001 delivered a clean integration branch (`feat/bff-luv-fe-001`, commit `ac104b44`) with the following durable foundation in `execute-plans`:

| Module | Path | What it does |
|---|---|---|
| Live transport | `src/lib/bff-v1/liveTransport.ts` | `withLiveOrMock(req, mockFn, adaptLive?)` — single call site for live-or-fallback across all route families |
| BFF client | `src/lib/bff-v1/client.ts` | `bffFetch` / `bffRequest` — typed fetch with mock/live switch, `credentials: "include"`, header injection |
| Auth headers | `src/lib/bff-v1/headers.ts` | Bearer token + Tenant-Id from browser storage (`pantheon.bff.bearerToken` / `pantheon.bff.tenantId`); cookie session via `credentials: "include"` |
| Typed paths | `src/lib/bff-v1/paths.ts` | Canonical typed path builders for all known BFF routes |
| Session / me | `src/lib/v4/session/me.ts` | `fetchMe()` / `useMe()` / `refreshSession()` / `logoutSession()` — live transport wired, TTL cache, strict-mode support |
| Me re-export | `src/lib/bff-v1/me.ts` | Re-exports from `v4/session/me.ts`; downstream callers import from here |
| Live status | `src/lib/bff-v1/liveStatus.ts` | Fallback tracking, API-version mismatch detection, `useLiveStatus()` hook |

## Environment Variables

| Env var | Values | Default | Effect |
|---|---|---|---|
| `VITE_BFF_MODE` | `live` / `mock` | `mock` | Switches transport to live or mock |
| `VITE_BFF_BASE_URL` | URL string | `""` | BFF host for live mode |
| `VITE_BFF_FALLBACK` | `auto` / `strict` | `auto` | `auto`: transport failure silently falls back to mock. `strict`: surfaces BffError; no silent mock |
| `VITE_BFF_REAL_WRITES` | `true` / `false` | `false` | Gates state-machine action writes; keep `false` until auth/confirm-token/two-man signing are validated |

Pre-configured env templates (from the parent task delivery):
- `.env.example` — mock mode, no BFF
- `.env.dev.example` — shared dev BFF, `live + auto`
- `.env.development.example` — lupin dev BFF, `live + auto`
- `.env.staging-live.example` — staging-live BFF, `live + strict`

## Auth / Session Token Storage

Bearer token resolution order (automatic, no code changes needed):

1. `sessionStorage.getItem("pantheon.bff.bearerToken")`
2. `localStorage.getItem("pantheon.bff.bearerToken")`
3. `sessionStorage.getItem("pantheon_operator_token")` (legacy key)
4. `localStorage.getItem("pantheon_operator_token")` (legacy key)

Tenant id resolution: same order with keys `pantheon.bff.tenantId` / `pantheon_tenant_id`.

Cookie-based sessions: `credentials: "include"` is set on every live fetch automatically.

To inject a dev/test token without touching source:
```
sessionStorage.setItem("pantheon.bff.bearerToken", "<token>")
sessionStorage.setItem("pantheon.bff.tenantId", "<tenant>")
```

## Operator Session Journey

Typical operator flow through the BFF session foundation:

1. **App load** — `useMe()` fires `fetchMe()` → `GET /bff/me` via `withLiveOrMock`.
   - Live mode: sends cookie + optional Bearer/Tenant headers; returns `MeResponse` with `user`, `tenant`, `roles`, `capabilities`, `env`, `featureFlags`, `sessionExpiresAt`.
   - Auto fallback: transport/network/5xx falls back to `mockMe()` silently; `liveStatus` records `fellBackAt`.
   - Strict mode: 4xx or transport failure throws `BffError`; `useMe()` surfaces `error` to the component tree.

2. **TopBar health probe** — `GET /health` every 30 s; `LiveBffBanner` / `RealtimeStatusBadge` display current `liveStatus.effective`.

3. **Session refresh** — `POST /bff/auth/refresh` → `refreshSession()` updates TTL cache; auto fallback returns `mockMe()`.

4. **Logout** — `POST /bff/logout` → `logoutSession()` clears cache; auto fallback clears local state.

5. **Downstream reads** — all route families in BFF-LUV-FE-002 and BFF-LUV-FE-003 use `withLiveOrMock` and inherit the same auth headers / fallback semantics established here.

6. **Governed writes** — `VITE_BFF_REAL_WRITES=false` keeps write-path mutations in mock/overlay until BFF-LUV-FE-004 validates auth, confirm-token, and two-man signing.

## Route-Level Live/Fallback Summary (BFF-LUV-FE-001 scope)

| Route | Live behavior | Auto fallback | Strict behavior |
|---|---|---|---|
| `GET /health` | Probed by TopBar every 30 s | Returns `{ status: "mock" }` | Surfaces BffError on transport failure |
| `GET /bff/me` | Hits real BFF; returns `MeResponse` | Returns `mockMe()` | Throws BffError; `useMe()` shows error |
| `POST /bff/auth/refresh` | Updates session cache | Returns `mockMe()` | Throws BffError |
| `POST /bff/logout` | Clears session on BFF + local cache | Clears local cache only | Throws BffError |
| `GET /bff/v5/interventions?status=pending` | Reads live intervention list | Returns seed-derived list | Surfaces error |
| `POST /bff/actions/{entityType}/{entityId}/{actionId}` | Writes if `VITE_BFF_REAL_WRITES=true` | Mock mutation + overlay | Surfaces error once writes enabled |

Routes not yet wired to live (left for BFF-LUV-FE-002/003/004):
- All Management Console read routes (`/bff/strategies`, `/bff/personas`, `/bff/capital-pools`, `/bff/rebalances`, `/bff/deployments`, `/bff/jobs`, `/bff/approvals`, `/bff/alerts`, `/bff/incidents`, etc.)
- Agora and v5 route families (`/bff/agora/*`, `/bff/v5/loop-runs`, `/bff/v5/sentinel/findings`)
- SSE / realtime (`/bff/events/stream`)
- Evolution/experiments (`/bff/evolution-programs`, `/bff/research-experiments`)
- Safe write flows (confirm-token, command envelopes, decision routes)

## Downstream Task Handoff Notes

### BFF-LUV-FE-002 — Management Console Read Adapters

The transport pattern is: wrap each read call in `withLiveOrMock(req, mockFn, adaptLive?)`. Use typed paths from `paths.ts`. Auth headers are injected automatically. Owner should:

1. Import `withLiveOrMock` from `@/lib/bff-v1/liveTransport` and typed path builders from `@/lib/bff-v1/paths`.
2. Replace each mock-only read in `src/lib/bff/client.ts` (or wherever Management Console currently pulls data) with `withLiveOrMock`.
3. Ensure `adaptLive` converts the BFF JSON envelope to the frontend model without inventing intermediate state.
4. Do not silently swallow 4xx — let `BffError` propagate so the UI can show the standard error state.
5. Hybrid fallback: each route should document whether auto-fallback is acceptable or if strict mode is required.

### BFF-LUV-FE-003 — Agora v5 and Realtime BFF

1. Agora reads (`/bff/agora/*`) follow the same `withLiveOrMock` pattern.
2. SSE / EventSource (`/bff/events/stream`): use `paths.sse()` as the stream URL. Mock mode should use the existing mock event bus. Live mode must not silently fall back to mock without surfacing a fallback indicator.
3. v5 loop run and sentinel routes (`/bff/v5/loop-runs`, `/bff/v5/sentinel/findings`, `/bff/v5/interventions`) use typed path builders already in `paths.ts`.
4. Mock simulator must be limited to mock mode; real mode must not invoke the mock event generator.

### BFF-LUV-FE-004 — Safe Real Write Flows

1. Writes are currently gated by `realWritesEnabled()` from `liveTransport.ts` (checks `VITE_BFF_REAL_WRITES`). Use this guard before sending mutations.
2. Confirm-token/command/decision envelopes must include `Idempotency-Key` — already built into `buildHeaders` for mutations.
3. `If-Match` (optimistic lock): pass `ifMatchVersion` in the `BffRequest` to inject `If-Match` header.
4. High-risk actions: `POST /bff/actions/{entityType}/{entityId}/{actionId}` is the canonical write path per `paths.action(entityType, entityId, actionId)`.
5. No live-capital side effects in smoke mode: verify `VITE_BFF_REAL_WRITES=false` remains the default in all env templates.

## Verification Evidence from Parent Task

Commands run by Codex2 (BFF-LUV-FE-001 owner):

```bash
npm install --package-lock-only
npm run test    # 44 files / 369 tests passed
npm run build   # Vite build passed (chunk-size and realtime import warnings only)
```

Branch: `origin/feat/bff-luv-fe-001`
Commit: `ac104b446fe4d8f78df96ecf23a768ac8f754dd0`
Worktree after push: clean

Review approval (Claude, 2026-05-09):
> 所有驗收標準通過：工作樹乾淨、branch 已推送、npm run test 369 tests passed、npm run build 通過。/bff/me strict 模式正確拋出 BffError 而非靜默 mock；auto 模式以 liveStatus.reportFallback 提供可見 fallback。credentials: include 已設；Bearer token 從 sessionStorage/localStorage 注入；README 逐路由記錄 fallback 行為。架構正確以 bff-v1/liveTransport.ts 取代 legacy transport.ts，無重複抽象。

## Key Source Files for Downstream Reference

- `execute-plans/src/lib/bff-v1/liveTransport.ts` — `withLiveOrMock`, `realWritesEnabled`
- `execute-plans/src/lib/bff-v1/client.ts` — `bffFetch`, `bffRequest`, `BffRequest`
- `execute-plans/src/lib/bff-v1/headers.ts` — `buildHeaders`, `setAuthProvider`, `BFF_AUTH_STORAGE_KEYS`
- `execute-plans/src/lib/bff-v1/paths.ts` — all typed path builders
- `execute-plans/src/lib/v4/session/me.ts` — `fetchMe`, `useMe`, `refreshSession`, `logoutSession`, `mockMe`
- `execute-plans/src/lib/bff-v1/liveStatus.ts` — `liveStatus`, `useLiveStatus`, `shouldUseLive`
- `execute-plans/README.md` — env var reference and per-route fallback docs
- `docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-001-repo-hygiene-transport-session.md` — canonical parent task artifact

## Reviewer Handoff

Reviewer (Codex2) should confirm that:

1. This packet is support-only and does not redefine canonical route truth.
2. Every suggestion traces back to the parent task artifact or the delivered source files.
3. The downstream task notes are consistent with what BFF-LUV-FE-001 actually delivered (transport foundation only, no broad read/write adapters yet).
4. No canonical L1 docs were modified by this sidecar.

This packet is ready for Codex2 review and parent-owner absorption decision.
