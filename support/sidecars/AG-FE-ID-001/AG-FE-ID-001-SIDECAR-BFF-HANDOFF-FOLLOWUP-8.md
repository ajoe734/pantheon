# AG-FE-ID-001 Followup-8 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Claude2` / `Claude` |
| Date | `2026-06-20` |
| Status | `in_progress; pending reviewer handoff` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, registry code,
governance implementation, OpenClaw adapter code, or execute-plans source.

## 1. Purpose

This eighth followup packet updates the AG-FE-ID-001 handoff after the most
significant positive progress since the series began: `AG-XR-001A` is now
`done` (archived 2026-06-20T15:23:35Z) and two follow-on contract tasks have
moved to `in_progress`:

1. `AG-XR-001A` — Additive Agora contract extension bundle (v1.1) merged via
   PR #1828 (implementation) and PR #1833 (closeout). The six v2 schema
   artifacts are now present in `services/control-plane/specs/agora/v2/` and
   `bundle_index.v1_1.json` is live.
2. `AG-XR-OPENAPI-001` — Servant/workshop OpenAPI v1.1 (+capability v1.1) is
   now `in_progress` (owner `Claude2`), unblocked by `AG-XR-001A` reaching
   `done`. This is the primary gate for `AG-BE-ID-002` and the servant success
   path.
3. `AG-XR-DASH-001` — Dashboard CRUD routes are now `in_progress` (owner
   `Claude`, reviewer `Claude2`), building on the v2 schema foundation.

The parent handoff outcome remains unchanged in the operationally important
sense: `AG-FE-ID-001` must not present a successful servant/session flow while
`AG-XR-OPENAPI-001`, `AG-BE-ID-002`, and `AG-BE-ID-003` are unresolved. The
only safe near-term parent shape is a truthful blocked/degraded status shell,
or a parent blocker waiting for the v1.1 contract to complete and generated
frontend types to mirror.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Claude2`.

| Task | Status (live) | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` | `in_progress` | This packet is the only intended deliverable. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | merged via PR #1830 | Previous approved handoff is the base; this packet updates it. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000`, `AG-BE-ID-003` | Parent implementation has not started. |
| `AG-XR-001A` | **`done`** (archived 2026-06-20T15:23:35Z) | v2 schema bundle foundation is now present; positive progress. |
| `AG-XR-OPENAPI-001` | **`in_progress`**; owner `Claude2`, reviewer `Claude`; depends on `AG-XR-001A` (done) | Servant/workshop OpenAPI v1.1 is now being built. `AG-BE-ID-002` gates on this. |
| `AG-XR-DASH-001` | **`in_progress`**; owner `Claude`, reviewer `Claude2`; depends on `AG-XR-001A` (done) | Dashboard CRUD routes being built; shares `agora_v1_1.openapi.yaml` with `AG-XR-OPENAPI-001`. |
| `AG-XR-003` | `todo`; owner `Codex`; depends on `AG-XR-001A` (now done) | Compatibility manifest gate; unblocked in principle but not yet started. |
| `AG-BE-ID-002` | `todo`; owner `Claude2`, reviewer `Codex`; depends on `AG-XR-OPENAPI-001` | Servant ensure/provision not implemented; blocked on OpenAPI v1.1 contract. |
| `AG-BE-ID-003` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-BE-ID-002` | Session facade unavailable until servant ensure lands. |

Dependency chain: `AG-XR-001A` (done) → `AG-XR-OPENAPI-001` (in_progress) →
`AG-BE-ID-002` (todo) → `AG-BE-ID-003` (todo) → `AG-FE-ID-001` (todo).

Parent dependency honesty rule: `AG-FE-ID-001` depends on `AG-BE-ID-003`, and
`AG-BE-ID-003` depends on `AG-BE-ID-002`, which depends on `AG-XR-OPENAPI-001`
being complete. `AG-XR-OPENAPI-001` is actively in progress but not done and
not merged. The parent must not unblock solely because `AG-XR-001A` is done.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_8.md` | This sidecar's support-only assignment |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` | Confirms `in_progress`, owner `Claude2`, reviewer `Claude` |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent `todo`, depends on `AG-BE-ID-003` |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-001A` | Confirms **archived done** at 2026-06-20T15:23:35Z |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms `in_progress`, owner `Claude2`, depends on `AG-XR-001A` |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-DASH-001` | Confirms `in_progress`, owner `Claude`, reviewer `Claude2` |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-003` | Confirms `todo`, owner `Codex`, unblocked in principle |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms `todo`, depends on `AG-XR-OPENAPI-001` |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms `todo`, depends on `AG-BE-ID-002` |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md` | Previous approved FE handoff baseline |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7-REVIEW.md` | Claude approval record for the previous followup |
| `services/control-plane/specs/agora/v2/` | v2 schema artifacts from `AG-XR-001A` done work |
| `services/control-plane/specs/agora/bundle_index.v1_1.json` | v1.1 bundle index pointing to v2 schema set |
| `services/control-plane/bff/agora/router.py` | Runtime implements `/bff/agora/me` and `/bff/agora/capabilities` |
| `services/control-plane/bff/agora/servant/router.py` | Runtime registers `/bff/agora/servant/ensure`, authenticates, then returns 501 |
| `services/control-plane/bff/tests/test_agora_router.py` | Focused tests assert current route behavior |
| `services/control-plane/bff/tests/test_agora_identity_scope.py` | Focused tests assert tenant/user predicate and no-authority servant policy |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora entry still renders `AskPersonas` directly |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated schema snapshot; no readiness operations yet |

## 4. Delta Since Followup-7

The approved FOLLOWUP-7 packet remains accurate. The material delta is the
completion of `AG-XR-001A` and the activation of two downstream contract tasks.

| Change | What changed | FE parent implication |
|---|---|---|
| `AG-XR-001A` → `done` | v2 schema bundle fully merged. Six artifacts live in `services/control-plane/specs/agora/v2/`. `bundle_index.v1_1.json` now present. Frozen v1 files and `bundle_index.json` sha256 unchanged. | Parent may note this as positive foundation. Does not unblock servant/session success path — `AG-XR-OPENAPI-001` must complete and types must mirror first. |
| `AG-XR-OPENAPI-001` → `in_progress` | Servant/workshop OpenAPI v1.1 is now actively being written. Owner `Claude2`. Covers 8 servant routes + 13 workshop routes (prose 03 authority, not just seed 24/32). `capability_manifest_v1_1.json` will gain `agora.servant.v1` and `agora.workshop.v1`. | When done and types mirror into `execute-plans@dev`, `AG-BE-ID-002` becomes unblockable. Until then, `servant.ts` must still map current 501 to `backend_not_ready`. |
| `AG-XR-DASH-001` → `in_progress` | Dashboard CRUD routes (strategies/{id}/dashboard-recipes, etc.) now being built on v2 schema. Shares `agora_v1_1.openapi.yaml` with `AG-XR-OPENAPI-001`. | Dashboard route family adds `agora.dashboard.v2` capability; FE parent IA planning may note this future surface. Not a prerequisite for blocked-shell-only scope. |
| `AG-XR-003` | Still `todo`. `AG-XR-001A` done removes one blocker but implementation not yet started. | Compatibility manifest gate (`agora_compat_manifest.py`, `docs/contracts/agora/compatibility-manifest.yaml`) will provide checksum validation once implemented. |

The remaining surface items from FOLLOWUP-7 — missing `AgoraApp.tsx`,
`identity.ts`, `servant.ts`; 501 stub for `/servant/ensure`; legacy `agora.ts`
helper — are all unchanged and still governed by the same handoff rules.

## 5. v2 Schema Artifact Inventory (from AG-XR-001A)

These files are now present and verified by `AG-XR-001A`'s done closeout. The
frozen v1 baseline is unchanged (`bundle_index.json` sha256 intact).

| Artifact | Location | Purpose |
|---|---|---|
| `widget_spec_v2.schema.json` | `services/control-plane/specs/agora/v2/` | WidgetSpec v2 schema; `WidgetSpecV1` legacy remains readable |
| `chart_spec_v1.schema.json` | `services/control-plane/specs/agora/v2/` | ChartSpec v1 (from contract-closure A3) |
| `dashboard_recipe_v2.schema.json` | `services/control-plane/specs/agora/v2/` | DashboardRecipe v2 schema |
| `compatibility_manifest.schema.json` | `services/control-plane/specs/agora/v2/` | Compatibility manifest schema for `AG-XR-003` |
| `capability_manifest_v1_1.json` | `services/control-plane/specs/agora/v2/` | v1.1 capability manifest basis; `agora.servant.v1`/`agora.workshop.v1` to be added by `AG-XR-OPENAPI-001` |
| `bundle_index.v1_1.json` | `services/control-plane/specs/agora/` | v1.1 bundle index extending frozen v1; records `base_bundle_index_sha256` |

FE parent implication: the v2 schema set is the design-closure source of truth
for widget/chart/dashboard types once `AG-XR-OPENAPI-001` completes and types
mirror into `execute-plans@dev`. Parent must not consume or generate from these
files directly before type generation; use `src/lib/bff-v1/agora/types.ts`
(the generated snapshot) as the FE schema source.

## 6. BFF Query Ledger For Parent

Unchanged from FOLLOWUP-7; verified by 22-test suite (22 passed, 14.74s).

| Route | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; tests cover envelope, tenant/user predicate, seven Agora capabilities (v1), and servant policy | Absent from frozen OpenAPI (`agora_v1.openapi.yaml`), frozen capability manifest path prefixes, generated `types.ts`, and `paths.ts`; `AG-XR-OPENAPI-001` (in_progress) will add v1.1 route coverage but is not done | Parent may use as accepted interim runtime route for identity readiness. Keep client narrow and local to Agora identity readiness. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; tests cover filtered manifest and backend scope | Absent from frozen contract surfaces; `AG-XR-OPENAPI-001` (in_progress) will provide v1.1 coverage but is not done | Same as `/me`; do not claim generated contract coverage. |
| `POST /bff/agora/servant/ensure` | Registered in `services/control-plane/bff/agora/servant/router.py`; authenticates then returns HTTP 501 `NOT_IMPLEMENTED` | Absent from frozen OpenAPI and generated types; present in contract-closure prose/seed as future v1.1 direction; `AG-XR-OPENAPI-001` is in_progress but not done; `AG-BE-ID-002` still todo | `servant.ts` must map current 501 to `backend_not_ready`; no successful `ServantProfile`, active servant, or session flow until `AG-XR-OPENAPI-001` merges, types mirror, and `AG-BE-ID-002` is implemented. |

The safe BFF facts today remain identity scope, capability filtering, and
display-only servant policy. A successful `ServantProfile` response is not
available in this checkout.

## 7. Frontend Surface To Hand Off

Unchanged from FOLLOWUP-7.

| Surface | Current state | Required parent decision |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Renders `AskPersonas` directly | Parent should route through `AgoraApp.tsx` or approved equivalent before exposing Ask/session behavior. |
| `execute-plans/src/agora/AgoraApp.tsx` | **MISSING** | Parent must implement only if scope is narrowed to blocked-shell-only or backend/contract blockers cleared. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | **MISSING** | Parent may add narrow strict clients for `/me` and `/capabilities` as interim runtime clients, or wait for contract reconciliation. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | **MISSING** | Parent may add explicit 501/backend-not-ready handling; success path must wait for v1.1 generated contract (pending `AG-XR-OPENAPI-001`) and backend implementation (`AG-BE-ID-002`). |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present generated schema snapshot; includes `ServantProfile` but no readiness operations | Reuse schema types where compatible; do not claim route operation coverage; do not consume v2 schemas directly until types regenerate after `AG-XR-OPENAPI-001` merges. |
| `execute-plans/src/lib/bff-v1/paths.ts` | Broad path object; contains Management, capital pool, broker/readiness, Management AI path strings | Do not import into Agora shell/client code unless bundle scan proves no forbidden strings leak. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch helper for Ask routes | Not sufficient for parent identity/servant acceptance; status shell must use strict BFF-v1 Agora clients. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Imports `postAsk`, `openAskSse`, `getAskSession` from legacy helper; imports assistant catalog surfaces | Parent shell must gate Ask/session/command surfaces until identity, servant, and session readiness are truthfully available. |

Current source scan: no Management/capital/broker/RuntimeBinding leakage found
in `src/agora/`, `src/entries/agora-main.tsx`, `src/lib/bff-v1/agora/`, or
`src/lib/bff/agora.ts`. Only `redacted_management` in generated schema text
(inert). Parent still needs a post-build bundle scan after implementing the
shell and clients.

## 8. Minimal Blocked-Shell Contract

Unchanged from FOLLOWUP-7. Reproduced for reference.

```text
agora-main.tsx
  -> AgoraApp.tsx
     -> identity.getAgoraMe()
     -> identity.getAgoraCapabilities()
     -> servant.ensureAgoraServant()
     -> current 501 maps to backend_not_ready
     -> Ask/session/command surfaces remain disabled or read-only
```

Required shell states:

| State | Trigger | UI/runtime rule |
|---|---|---|
| Auth blocked | Missing auth or `401` from any readiness call | Render blocked auth state; no servant/session controls. |
| Scope/audience blocked | `403`, wrong tenant, wrong audience, or missing Agora capability | Render blocked scope state; no seed/mock retry. |
| Identity ready, backend not ready | `/me` and `/capabilities` succeed, `/servant/ensure` returns 501 | Show identity/capability/policy facts and unavailable servant status. |
| Contract not mirrored | v1.1 servant capability/OpenAPI/types not yet present in execute-plans | Do not show active servant or generated-client completion claims. |
| Session facade unavailable | `AG-BE-ID-003` still todo | Keep Ask/session/command surfaces disabled or explicitly read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured | Render unavailable state; no silent mock fallback. |
| Servant active | Future non-501 `ServantProfile` after v1.1 contract and backend work land | Display profile/status only; no order, broker, capital, or RuntimeBinding authority. |

`servant_policy.execution_authority = "none"` and
`prohibited_authority = ["runtime_binding", "broker_order", "capital_binding"]`
may be displayed as safety facts. They must not become operator controls.

## 9. Operator Journey

### Current honest journey

```text
Operator opens agora.html
  -> Agora bundle loads from the separate Agora entry
  -> frontend verifies Agora-scoped auth/audience
  -> frontend calls GET /bff/agora/me through a strict client
  -> BFF returns tenant_id, user_id, fail-closed read_predicate,
     seven Agora capabilities (v1), and servant_policy
  -> frontend calls GET /bff/agora/capabilities through a strict client
  -> BFF returns filtered capability manifest and backend scope
  -> frontend calls POST /bff/agora/servant/ensure only if parent accepts
     the interim runtime stub as callable
  -> current backend returns 501 NOT_IMPLEMENTED
  -> shell renders servant provisioning unavailable/backend not ready
  -> Ask/session/command surfaces remain disabled or read-only
```

### Future v1.1 journey — still blocked, advancing

```text
AG-XR-OPENAPI-001 completes (in_progress) and types mirror into execute-plans@dev
  -> AG-BE-ID-002 implement servant ensure/provision with v1.1 contract
  -> AG-BE-ID-003 implement session BFF facade
  -> frontend uses v1.1 servant client under src/lib/bff-v1/agora/*
  -> ensure sends auth-derived identity with Idempotency-Key and X-Request-Id
  -> BFF creates or reconciles exactly one user-private servant profile
  -> BFF persists/reconciles the Persona Registry record with tenant/user scope
  -> BFF invokes governed OpenClaw adapter agent ensure/reconcile routes
  -> BFF returns { data: ServantProfile, meta: ... }
  -> downstream servant sessions bind to that persona_id
```

This success journey is now one step closer (`AG-XR-001A` done, `AG-XR-OPENAPI-001`
in_progress) but still requires `AG-XR-OPENAPI-001` to complete and merge, types
to mirror into `execute-plans@dev`, `AG-BE-ID-002` to implement, and `AG-BE-ID-003`
to implement before the parent can unblock.

## 10. Parent Absorption Checklist

Unchanged from FOLLOWUP-7. Reproduced for completeness.

Claude should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Backend blocker disposition | Parent either stops on unresolved `AG-BE-ID-002`/`AG-BE-ID-003`, or explicitly narrows completion to blocked-shell-only. |
| Contract-closure disposition | Parent states whether it is waiting for `AG-XR-OPENAPI-001` done and generated execute-plans types, or proceeding only with interim runtime route clients. |
| Route truth | Parent states `/me`, `/capabilities`, and current `/servant/ensure` are interim runtime routes, not generated contract-complete routes. |
| v1.1 schema handling | Parent does not generate client behavior directly from v2 schema files; generated `types.ts` must lead. |
| Strict clients | `identity.ts` and `servant.ts` use live strict semantics, do not fall back to mock/seed data, and do not issue page-local `fetch` from UI components. |
| 501 handling | `servant.ts` tests prove current 501 maps to `backend_not_ready`, not success. |
| Future headers | Any future-facing servant ensure client design accounts for `Idempotency-Key` and `X-Request-Id` once v1.1 is accepted. |
| No broad path import | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` do not import `@/lib/bff-v1/paths`, Management clients, capital helpers, broker helpers, or RuntimeBinding controls. |
| Ask/session gating | `AskPersonas` is gated behind shell status and cannot imply session readiness while `AG-BE-ID-003` is todo. |
| IA alignment | Parent does not invent an unrelated IA. It either uses blocked-shell-only scope or waits for accepted three-tab IA/contracts. |
| Bundle isolation | `npm run build:agora` followed by forbidden-string scan has no Management/capital/broker/RuntimeBinding leakage, excluding explicitly reviewed inert schema text. |
| Tests | Frontend tests cover identity success, auth blocked, scope/audience blocked, strict BFF failure, servant 501, and no forbidden imports. |

## 11. Suggested Parent Verification

Backend current-state checks:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
rg -n -P '/bff/agora/me(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/capabilities(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/servant/ensure(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/servant/router.py services/control-plane/bff/tests/test_agora_router.py
```

Contract progress checks:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-DASH-001
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-003
ls services/control-plane/specs/agora/v2/
ls services/control-plane/openapi/agora_v1_1.openapi.yaml 2>/dev/null && echo "v1.1 OpenAPI present" || echo "v1.1 OpenAPI: not yet landed"
```

Frontend checks after parent implementation:

```bash
cd execute-plans
npm run build:agora
npx vitest run src/lib/bff-v1/agora src/agora
rg -n "/management|RuntimeBinding|capital-pool|broker" dist/agora
rg -n "@/lib/bff-v1/paths|management|RuntimeBinding|capital-pool|broker" src/agora src/entries/agora-main.tsx src/lib/bff-v1/agora
rg -n "@/lib/bff/agora|postAsk|openAskSse|getAskSession" src/agora src/entries/agora-main.tsx
```

Expected current interpretation:

- `/me` and `/capabilities` appear only in `services/control-plane/bff/agora/router.py`.
- `/servant/ensure` appears in the BFF servant stub and focused route tests;
  absent from active OpenAPI or generated execute-plans operation types.
- `agora_v1_1.openapi.yaml` is not yet present (pending `AG-XR-OPENAPI-001`).
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing.
- v2 schemas are present in `services/control-plane/specs/agora/v2/` but not
  yet surfaced as generated execute-plans types.

## 12. Sidecar Verification

Commands run for this sidecar:

```bash
git branch --show-current
git status --short
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-001A
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-DASH-001
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001
ls services/control-plane/specs/agora/v2/
ls services/control-plane/specs/agora/bundle_index*.json
test -f execute-plans/src/agora/AgoraApp.tsx && echo EXISTS || echo MISSING
test -f execute-plans/src/lib/bff-v1/agora/identity.ts && echo EXISTS || echo MISSING
test -f execute-plans/src/lib/bff-v1/agora/servant.ts && echo EXISTS || echo MISSING
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
```

Results:

- Branch: `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` (correct)
- `AG-XR-001A`: **done** (archived 2026-06-20T15:23:35Z)
- `AG-XR-OPENAPI-001`: **in_progress** (owner `Claude2`)
- `AG-XR-DASH-001`: **in_progress** (owner `Claude`, reviewer `Claude2`)
- `AG-XR-003`: `todo` (owner `Codex`)
- `AG-BE-ID-002`: `todo` (depends on `AG-XR-OPENAPI-001`)
- `AG-BE-ID-003`: `todo` (depends on `AG-BE-ID-002`)
- `AG-FE-ID-001`: `todo`
- v2 schemas: all six present in `services/control-plane/specs/agora/v2/`
- `bundle_index.v1_1.json`: present
- `AgoraApp.tsx`: MISSING
- `identity.ts`: MISSING
- `servant.ts`: MISSING
- BFF tests: 22 passed in 14.74s

## 13. Reviewer Handoff

Reviewer: `Claude`

Review artifact to be created by reviewer:
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8-REVIEW.md`

This packet is ready for Claude's review. The key additions vs FOLLOWUP-7 are:

1. `AG-XR-001A` is now done — §4 and §5 record the v2 schema artifacts.
2. `AG-XR-OPENAPI-001` and `AG-XR-DASH-001` are now in_progress — §4 updates
   their status and FE parent implications.
3. The operator journey (§9) now notes the chain is one step closer but still
   blocked on `AG-XR-OPENAPI-001` completing and merging.
4. All handoff rules, BFF ledger, frontend surface inventory, blocked-shell
   contract, and parent absorption checklist are unchanged from FOLLOWUP-7.

*Prepared by Claude2 for the `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` support slice.*
