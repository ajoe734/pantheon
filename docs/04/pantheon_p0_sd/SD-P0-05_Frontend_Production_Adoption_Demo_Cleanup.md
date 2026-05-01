---
project: Pantheon
document_type: P0 System Design / Architecture Decision / Codex Implementation Packet
language: zh-TW
status: draft-for-implementation
revision: v1
baseline: >
  Based on Pantheon consolidated blueprint and latest implementation correction:
  current actual LEAN bridge is `pantheon/lean` submodule, remote `ajoe734/pantheon-lean.git`;
  `lean-platform` is not the current Pantheon execution target.
---

# SD-P0-05 — Frontend Production Adoption / Demo Cleanup

## 1. Purpose

本 SD 定義 `front-ai-trading-system` 從 demo shell / route-live 混合狀態，推進到 staging/prod operator console baseline 的 P0 清理工作。

最新盤點指出：

```text
front repo path:
  /home/edna/code/front-ai-trading-system

BFF client:
  已集中接 BFF

App routes:
  operator / research / knowledge / consultation / governance 已掛上

仍存在:
  AuthProvider imports @/demo/api
  demo token writes to pantheon_operator_token
  Login demo copy
  dashboard / persona tabs / health / evolution / tools / settings security / trainer demo islands
```

本 SD 目標：不要求所有 UI 全面 production complete，但 staging/prod 不可再用 demo auth / silent demo data 誤導 operator。

---

## 2. Scope

### 2.1 In scope

```text
1. AuthProvider demo cleanup.
2. Login demo copy cleanup.
3. production route demo import inventory.
4. source_mode / data provenance badges.
5. route classification: live BFF / preview mock / demo island.
6. frontend CI check for @/demo imports in staging/prod routes.
7. separation of dev demo support and staging/prod production path.
```

### 2.2 Out of scope

```text
1. Full enterprise OIDC implementation if not already available.
2. BFF HA/LB.
3. Complete replacement of every legacy page in one PR.
4. Live broker enablement.
5. Full design system rewrite.
```

---

## 3. Environment Policy

### 3.1 dev

Allowed:

```text
- preview mock fallback with explicit banner
- local demo data for pages marked demo_only
- local token only if clearly dev-only
```

Forbidden:

```text
- presenting demo runtime state as authoritative
- hiding demo source mode
```

### 3.2 staging

Allowed:

```text
- real BFF
- degraded / stale BFF projections
- explicit unavailable state
```

Forbidden:

```text
- @/demo auth
- demo token login
- silent mock fallback
- demo runtime / capital pool / incident data on operator routes
```

### 3.3 prod

Allowed:

```text
- real BFF with approved auth
- OIDC / enterprise auth path
- degraded metadata from BFF only
```

Forbidden:

```text
- any demo auth path
- any demo copy suggesting mock system
- any operator / governance / runtime route importing @/demo
```

---

## 4. Route Classification

Every route must be classified:

```text
route_mode:
  - production_bff
  - bff_degraded
  - preview_mock_only
  - demo_only
  - migration_pending
```

### 4.1 Required route metadata

```ts
type RouteSourceMode =
  | "production_bff"
  | "bff_degraded"
  | "preview_mock_only"
  | "demo_only"
  | "migration_pending";
```

### 4.2 Display behavior

| mode | UI behavior |
|---|---|
| production_bff | normal |
| bff_degraded | show stale/degraded badge |
| preview_mock_only | show prominent preview mock banner |
| demo_only | unavailable in staging/prod |
| migration_pending | show blocked/migration notice |

---

## 5. Auth Cleanup

### 5.1 Current problem

```text
AuthProvider imports @/demo/api.
AuthProvider writes demo token to pantheon_operator_token.
Login contains demo copy.
```

### 5.2 Target

Create environment-aware auth provider:

```text
dev:
  may use DevAuthProvider with explicit dev flag

staging/prod:
  must use BffAuthProvider / OidcAuthProvider
  must never import @/demo/api
  must never write demo token
```

### 5.3 Auth modes

```text
auth_mode:
  - dev_local
  - jwt_bff
  - oidc
```

### 5.4 Hard invariants

```text
INV-FE-AUTH-001:
  staging/prod bundle must not import @/demo/api.

INV-FE-AUTH-002:
  staging/prod login must not create demo token.

INV-FE-AUTH-003:
  `pantheon_operator_token` may only be set from approved auth response.

INV-FE-AUTH-004:
  frontend must not store broker secrets.

INV-FE-AUTH-005:
  live broker enabled flag remains false in dev UI.
```

---

## 6. Demo Island Cleanup

### 6.1 Inventory

Codex should scan:

```bash
rg "@/demo|demo/api|mockData|mockBffData|previewMockFallback" src
```

and produce:

```text
route
component
import
classification
replacement plan
```

### 6.2 Production route forbidden imports

For staging/prod routes:

```text
operator
governance
runtime
capital pool
incident
evolution
settings security
trainer if used for real persona mutation
```

Forbidden:

```text
@/demo/*
demo token
silent mock data
```

### 6.3 Allowed demo zones

```text
docs/dev-preview
storybook
explicit demo route
Lovable preview only
```

---

## 7. BFF Client Policy

### 7.1 Current

BFF client already centralized and hard-pinned to dev BFF in current dev UI.

### 7.2 Required

Split or label:

```text
readBffClient
commandBffClient
previewMockFallback
```

Command calls must require:

```text
actor_ref
trace_id
idempotency_key
```

if they affect runtime, deployment, approval, or incident.

---

## 8. Source Mode Badges

### 8.1 Required on pages

```text
Operator runtime status
Deployment review
Governance approval
Incident detail
Evolution decision
Capital pool detail
Persona binding detail
Research artifact detail
```

### 8.2 Badge values

```text
authoritative_bff
derived_projection
stale_cache
preview_mock_only
demo_only
unavailable
```

### 8.3 UI invariant

```text
No operator should mistake demo / preview / stale data for canonical runtime truth.
```

---

## 9. Tests

### Unit tests

```text
test_staging_auth_provider_does_not_import_demo_api
test_prod_login_does_not_write_demo_token
test_dev_auth_labeled_dev_only
test_source_mode_badge_renders
test_preview_mock_banner_visible_when_preview_mock_active
```

### Static tests

```text
test_no_demo_imports_in_production_routes
test_no_mock_runtime_data_in_operator_routes
test_no_broker_secret_references_in_frontend
```

### Integration tests

```text
test_bff_auth_token_used_for_get
test_bff_error_envelope_parsed
test_degraded_bff_surface_renders_degraded_badge
```

---

## 10. Failure Behavior

| Condition | Behavior |
|---|---|
| staging route imports @/demo | CI fail |
| prod login contains demo copy | CI fail |
| operator page uses preview mock | show blocked / CI fail depending environment |
| BFF unavailable | show degraded/unavailable, not demo data |
| auth token invalid | redirect login, no demo fallback |
| runtime source unknown | display unverifiable source mode |

---

## 11. Non-goals

```text
1. Do not implement full OIDC if backend integration is not ready.
2. Do not remove Lovable preview support entirely.
3. Do not enable live broker UI actions.
4. Do not redesign all pages.
5. Do not make frontend canonical store.
6. Do not hide unavailable BFF behind demo data.
```

---

## 12. Acceptance Criteria

```text
AC-FE-001:
  staging/prod auth path has no @/demo/api import.

AC-FE-002:
  demo token cannot be written in staging/prod.

AC-FE-003:
  production operator routes have no @/demo imports.

AC-FE-004:
  source_mode badge appears on runtime/governance/evolution critical surfaces.

AC-FE-005:
  preview mock fallback is clearly labeled and limited to preview host.

AC-FE-006:
  CI can detect demo islands and classify them.

AC-FE-007:
  BFF unavailable never silently becomes demo success state.
```

---

## 13. Codex Task Packets

### TP-FE-001 — Demo island inventory

```yaml
task_id: TP-FE-001
repo: front-ai-trading-system
goal: Scan all @/demo and mock imports and produce inventory.
target_paths:
  - src/**
  - docs/pantheon-handoffs/demo-island-inventory.md
acceptance:
  - every @/demo import listed
  - each import classified as dev_allowed / production_forbidden / migration_pending
```

### TP-FE-002 — AuthProvider staging/prod cleanup

```yaml
task_id: TP-FE-002
repo: front-ai-trading-system
goal: Remove demo auth from staging/prod AuthProvider path.
target_paths:
  - src/**/AuthProvider.tsx
  - src/**/Login.tsx
acceptance:
  - no @/demo/api import in staging/prod bundle
  - no demo token write in staging/prod
  - dev auth remains explicitly labeled
```

### TP-FE-003 — Production route demo import CI

```yaml
task_id: TP-FE-003
repo: front-ai-trading-system
goal: Add CI/static check preventing @/demo imports in production routes.
target_paths:
  - scripts/check_no_demo_prod_routes.*
  - package.json
  - .github/workflows/*
acceptance:
  - CI fails on forbidden demo import
  - allowlist exists for dev-preview/demo-only routes
```

### TP-FE-004 — Add source_mode badges

```yaml
task_id: TP-FE-004
repo: front-ai-trading-system
goal: Add source_mode/degradation badges to critical pages.
target_paths:
  - src/components/*
  - src/pages/operator/*
  - src/pages/governance/*
  - src/pages/evolution/*
acceptance:
  - authoritative_bff / stale_cache / preview_mock_only visible
  - preview mock has explicit banner
```

---

## 14. Open Questions

```text
1. Which auth provider should staging use: HS256 JWT or OIDC?
2. Is `pantheon_operator_token` acceptable for dev only?
3. Which routes are allowed to remain demo_only?
4. Should source_mode come from BFF response meta or frontend wrapper?
5. Should Lovable preview always allow mock fallback?
```

---

## 15. Final Statement

P0 frontend goal:

```text
Keep dev/Lovable preview usable,
but prevent staging/prod operator console from depending on demo auth, demo token, or silent mock runtime data.
```

---

## 16. Closeout Evidence

Task: `P0-FE-DEMO-001`

Frontend repo: `/home/edna/code/front-ai-trading-system`

Reviewed commits:

```text
d321a9b P0-FE-DEMO-001 remove prod demo auth paths
ea284a1 P0-FE-DEMO-001 preserve token on session refresh
```

Delivered scope:

```text
- Removed staging/prod AuthProvider dependency on demo auth paths.
- Removed staging/prod demo-token login path and demo login copy.
- Preserved an existing approved `pantheon_operator_token` during successful BFF session refresh when the BFF returns session metadata without a replacement token.
- Kept token clearing behavior for missing local token, failed refresh, and sign-out.
- Added production-route demo import guard for operator/governance/runtime-facing frontend routes.
- Recorded demo island inventory and kept dev/Lovable preview support explicitly separated from staging/prod behavior.
```

Closeout verification on 2026-05-01:

```text
npm run check:prod-demo-routes
  passed

npx eslint src/auth/AuthProvider.tsx src/pages/auth/Login.tsx src/lib/bffClient.ts src/pages/settings/sections/SecuritySettings.tsx scripts/check_no_demo_prod_routes.mjs
  passed with one existing react-refresh/only-export-components warning in src/auth/AuthProvider.tsx

npm run build
  passed; Vite reported existing browserslist data and chunk-size warnings
```

Reviewer approval:

```text
Codex approved frontend commit ea284a1 after the token-preservation blocker was fixed.
Review file: support/reviews/P0-FE-DEMO-001-codex-review.md
```
