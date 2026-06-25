# AG-FE-ID-001 Sidecar BFF and Frontend Handoff Packet

**Sidecar task:** `AG-FE-ID-001-SIDECAR-BFF-HANDOFF`<br>
**Helper parent:** `AG-FE-ID-001` - Agora auth/session/servant status shell<br>
**Helper kind:** `bff_handoff_packet`<br>
**Parent owner / reviewer:** `Claude` / `Codex`<br>
**Original sidecar owner / reviewer:** `Codex2` / `Claude`<br>
**Closeout owner / reviewer:** `Codex` / `Claude`<br>
**Date:** `2026-06-20`<br>
**Status:** `review-approved; owner closeout prepared`

> Scope constraint: support artifact only. This packet does not modify L1
> canonical truth, core contract truth, BFF runtime behavior, registry,
> governance implementation, or execute-plans frontend implementation.

## 1. Purpose

This packet gives the `AG-FE-ID-001` parent owner and reviewer a compact
BFF/frontend handoff for the Agora auth/session/servant status shell. It
records:

1. the locally observed Agora contract and BFF route surface
2. the current BFF query gaps blocking a truthful servant-status shell
3. the execute-plans frontend handoff targets
4. the operator journey the parent should implement once upstream routes are
   ready
5. review and absorption gates for the parent task

This packet is not a parent implementation and does not approve or reopen the
parent task.

## 2. Sources Used

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff.md` | Sidecar assignment and support-only boundary |
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent task definition, owner/reviewer, dependencies, acceptance text |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` | Frozen local Agora v1 schema/capability/route catalog |
| `services/control-plane/bff/agora/router.py` | Implemented package routes for `/bff/agora/me` and `/bff/agora/capabilities` |
| `services/control-plane/bff/agora/servant/router.py` | Current servant ensure route stub |
| `services/control-plane/bff/tests/test_agora_router.py` | BFF route behavior tests |
| `services/control-plane/bff/tests/test_agora_identity_scope.py` | User-private scope and servant policy tests |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora app entry point |
| `execute-plans/src/lib/bff/agora.ts` | Current legacy Agora fetch helper |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated Agora v1 contract snapshot/types |
| `support/sidecars/AG-FE-000/AG-FE-000-SIDECAR-REVIEW.md` | Upstream FE entry/build sidecar evidence and scope attention item |

## 3. Parent Task Boundary

Parent `AG-FE-ID-001` is defined in the 2026-06-20 dispatch script as:

| Field | Value |
|---|---|
| Parent task | `AG-FE-ID-001` |
| Title | Agora auth/session/servant status shell |
| Owner / reviewer | `Claude` / `Codex` |
| Phase | `EPIC AGORA-FE / Phase 1` |
| Parent dependencies from dispatch | `AG-FE-000`, `AG-BE-ID-003` |
| Parent artifacts from dispatch | `execute-plans/src/agora/AgoraApp.tsx`, `execute-plans/src/lib/bff-v1/agora/identity.ts`, `execute-plans/src/lib/bff-v1/agora/servant.ts` |

Parent acceptance summary from dispatch:

- after login, ensure the user-private servant and display status
- BFF clients must use live strict behavior, with no page-level direct `fetch`
- Agora bundle must not import or reveal Management, capital pool, or
  RuntimeBinding surfaces
- cross-audience tokens must be rejected
- frontend tests must cover the shell/client behavior
- implementation must not invent fields, routes, enums, widgets, scoring, or
  capital/order authority

## 4. BFF Surface Observed Locally

### 4.1 Implemented identity readiness routes

`services/control-plane/bff/agora/router.py` currently implements:

| Route | Status | Notes for frontend |
|---|---|---|
| `GET /bff/agora/me` | implemented | Returns `{data, meta}` envelope with `agora.identity.v1`, user scope, tenant/user predicate, capabilities, and servant policy |
| `GET /bff/agora/capabilities` | implemented | Returns filtered capability manifest plus backend scope predicate |

Focused tests confirm this behavior:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py
```

Result: `22 passed in 12.01s`.

### 4.2 Servant ensure route is present but not ready

`services/control-plane/bff/agora/servant/router.py` registers:

| Route | Current behavior | Owner noted in code |
|---|---|---|
| `POST /bff/agora/servant/ensure` | HTTP 501 `NOT_IMPLEMENTED` | `AG-BE-ID-002` |

`services/control-plane/bff/tests/test_agora_router.py` explicitly asserts that
`POST /bff/agora/servant/ensure` returns 501 today. Therefore
`AG-FE-ID-001` must not claim that servant provisioning is live unless a later
backend task changes this route to a successful `ServantProfile` response.

### 4.3 Identity/session route migration is partial

`services/control-plane/bff/agora/identity/router.py` is currently a placeholder
and documents that many identity/session routes are still implemented in
`services/control-plane/bff/main.py`, including:

- `GET /bff/agora/sessions`
- `POST /bff/agora/sessions`
- `GET /bff/agora/sessions/{sessionId}`
- `GET /bff/agora/sessions/{sessionId}/messages`
- `POST /bff/agora/sessions/{sessionId}/messages`
- `POST /bff/agora/ask`
- `GET|POST /bff/agora/ask/sessions`

For `AG-FE-ID-001`, the frontend should treat package-local identity readiness
as limited to `/me`, `/capabilities`, and the current `/servant/ensure` stub.
Any session or message workflow should stay a skeleton until the parent owner
confirms `AG-BE-ID-003` is landed and reviewable.

## 5. Contract Attention Items

### 5.1 Local SD does not contain the referenced UI section

The dispatch text tells FE/UI workers to follow `SD section 23`, but the
checked-out `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md`
only contains sections 0 through 8, a section 17 route anchor, section 22.1,
and an appendix. No local section 23 UI/IA spec was present in this worktree.

Parent action: before implementing any UI beyond the minimal status shell, find
the actual section 23 design source or open a blocker. Do not fill the missing
layout/component behavior by invention.

### 5.2 Route catalog and implementation disagree on servant ensure

The local frozen route catalog lists 61 Agora routes, but does not list
`POST /bff/agora/servant/ensure`. The parent dispatch and BFF stub both require
that route.

Parent action: treat `/bff/agora/servant/ensure` as a backend handoff route
whose canonical route-catalog status needs reconciliation. The frontend may
prepare a client around the existing stub, but it should keep a blocked or
degraded UI state until the backend contract is resolved.

### 5.3 Upstream AG-FE-000 scope narrative still needs parent awareness

`support/sidecars/AG-FE-000/AG-FE-000-SIDECAR-REVIEW.md` records that the
merged AG-FE-000 PR file list appeared wider than its parent brief narrative.
This sidecar does not reopen AG-FE-000, but `AG-FE-ID-001` should avoid
assuming the prior FE entry/build slice is fully narrated until its owner and
reviewer accept that discrepancy.

## 6. Frontend Surface Observed Locally

| Surface | Current state | Implication for AG-FE-ID-001 |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Exists and renders `AskPersonas` | Parent still needs the requested `AgoraApp.tsx` shell or equivalent approved entry swap |
| `execute-plans/src/agora/AgoraApp.tsx` | Not present | Required parent artifact missing in this snapshot |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | Not present | Required strict identity client missing |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | Not present | Required strict servant client missing |
| `execute-plans/src/lib/bff/agora.ts` | Exists as legacy helper using direct `fetch` | Should not be the final AG-FE-ID-001 client path |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Exists with generated Agora v1 types and capability snapshot | Parent should reuse these types instead of hand-typing DTOs |
| `execute-plans/vite.agora.config.ts` | Defaults `VITE_AUTH_AUDIENCE` to `pantheon-agora` | Parent should preserve Agora audience separation |
| `execute-plans/src` unit tests | No Agora auth/servant unit test found | Parent needs focused tests for identity and servant clients |

The current `execute-plans/src/lib/bff/agora.ts` helper resolves the base URL
from `window.location.origin` and calls `fetch` directly. For the parent task,
page components should not call BFF routes directly and should not rely on a
legacy helper that bypasses the strict live/fallback contract expected for
`src/lib/bff-v1/agora/*`.

## 7. Recommended Frontend Handoff Shape

### 7.1 `identity.ts`

Recommended responsibilities:

- expose `getAgoraMe()` for `GET /bff/agora/me`
- expose `getAgoraCapabilities()` for `GET /bff/agora/capabilities`
- return typed Agora envelope data using generated types from
  `execute-plans/src/lib/bff-v1/agora/types.ts`
- preserve strict live behavior: no mock fallback in live mode, no silent
  downgrade to seed data, and no page-level `fetch`
- surface typed errors for `401`, `403`, and audience/scope failures

The identity client should be the first call the Agora shell makes after auth.
If it fails with auth or audience errors, the shell should render a blocked
state, not an empty app.

### 7.2 `servant.ts`

Recommended responsibilities:

- expose `ensureAgoraServant()` for `POST /bff/agora/servant/ensure`
- type the successful response as a `ServantProfile` envelope once backend
  support lands
- map the current 501 response to an explicit `provisioning_unavailable` or
  `backend_not_ready` state for the shell
- never infer an active servant from `/bff/agora/me` alone
- never create a local mock servant in live/strict mode

The frontend may display the policy fields already returned by `/bff/agora/me`
as identity policy context, but should not label that as a provisioned servant.

### 7.3 `AgoraApp.tsx`

Recommended shell behavior:

1. read auth context for the Agora app audience (`pantheon-agora`)
2. call `getAgoraMe()`
3. call `getAgoraCapabilities()`
4. call `ensureAgoraServant()`
5. render one of these states:
   - authenticated scope loaded, servant active
   - authenticated scope loaded, servant provisioning unavailable
   - auth/audience blocked
   - BFF unavailable in strict mode
6. keep command-line/session UI as a disabled skeleton unless the parent can
   verify `AG-BE-ID-003` routes are ready

The shell must not show or import Management, capital pool, broker, live order,
or RuntimeBinding controls.

## 8. Operator Journey for Parent Implementation

| Step | BFF call or frontend state | Expected result today |
|---|---|---|
| 1. Open Agora app | `agora.html` via `agora-main.tsx` | App loads the Agora entry |
| 2. Auth/audience check | Agora audience should be `pantheon-agora` | Mis-scoped token should block |
| 3. Identity readiness | `GET /bff/agora/me` | 200 in focused BFF tests with operator stub |
| 4. Capability readiness | `GET /bff/agora/capabilities` | 200 in focused BFF tests with operator stub |
| 5. Servant ensure | `POST /bff/agora/servant/ensure` | 501 in current BFF tests |
| 6. Status shell | Render status from identity + ensure result | Must say provisioning unavailable unless backend is updated |
| 7. Command-line skeleton | Disabled or read-only placeholder | Must not imply session facade is complete |

## 9. Parent Absorption Gates

The parent owner should not hand `AG-FE-ID-001` to review as complete until all
applicable gates are satisfied or explicitly scoped down:

| Gate | Pass condition |
|---|---|
| G1 identity client | `identity.ts` calls `/bff/agora/me` and `/bff/agora/capabilities` through strict BFF transport, with typed auth/audience errors |
| G2 servant client | `servant.ts` handles both successful `ServantProfile` and current 501 backend-not-ready behavior without fabricating success |
| G3 shell state | `AgoraApp.tsx` renders identity/capability/servant status and keeps command/session UI disabled when backend support is absent |
| G4 no Management leakage | Agora bundle/source does not import Management routes/components or expose capital/RuntimeBinding controls |
| G5 direct-fetch ban | Agora pages call `src/lib/bff-v1/agora/*` clients, not page-local `fetch` or the legacy `src/lib/bff/agora.ts` helper |
| G6 tests | Frontend tests cover identity success, cross-audience/auth failure, servant 501, strict no-fallback behavior, and no Management import |
| G7 design blocker | Missing local section 23 / `/servant/ensure` route-catalog mismatch is resolved or carried as an explicit blocker |

## 10. Suggested Verification for Parent

Backend readiness checks:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py
```

Frontend checks after implementation:

```bash
cd execute-plans
npm run build:agora
npx vitest run src/lib/bff-v1/agora src/agora
```

Bundle/scope checks after build:

```bash
cd execute-plans
rg -n "/management|RuntimeBinding|capital-pool|broker" dist/agora
```

Expected result for the bundle/scope check is no matches except any explicitly
approved inert contract text. Any live route import or visible control should
block review.

## 11. Sidecar Verification

Commands run by this sidecar:

```bash
git status -sb
git branch --show-current
git remote -v
sed -n '1,260p' .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff.md
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF
rg -n "AG-FE-ID-001" --glob '!current-work.md' --glob '!ai-activity-log.jsonl' --glob '!docs-site/**'
sed -n '1,230p' scripts/dispatch_agora_cross_repo_2026-06-20.py
sed -n '1,320p' docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md
sed -n '1,260p' services/control-plane/bff/agora/router.py
sed -n '1,260p' services/control-plane/bff/agora/servant/router.py
sed -n '1,260p' services/control-plane/bff/tests/test_agora_router.py
sed -n '1,260p' services/control-plane/bff/tests/test_agora_identity_scope.py
sed -n '1,240p' execute-plans/src/lib/bff/agora.ts
sed -n '1,220p' execute-plans/src/entries/agora-main.tsx
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py
test -f execute-plans/src/lib/bff-v1/agora/identity.ts
test -f execute-plans/src/lib/bff-v1/agora/servant.ts
test -f execute-plans/src/agora/AgoraApp.tsx
```

The three `test -f` checks for `identity.ts`, `servant.ts`, and `AgoraApp.tsx`
returned non-zero, confirming those required parent artifacts are not present
in this snapshot.

## 12. Reviewer Handoff

Reviewer: `Claude`

Please review this sidecar for:

1. support-only scope compliance
2. accurate BFF gap classification for `/me`, `/capabilities`, and
   `/servant/ensure`
3. accurate frontend handoff target list for `AG-FE-ID-001`
4. correct treatment of the missing local section 23 and route-catalog mismatch
5. usefulness for parent-owner absorption

Suggested approval command:

```bash
AI_NAME=Claude python3 scripts/ai_status.py approve AG-FE-ID-001-SIDECAR-BFF-HANDOFF "Sidecar BFF/frontend handoff packet approved; support-only artifact documents AG-FE-ID-001 identity, servant ensure, strict client, and UI shell gates."
```

Suggested reopen command if changes are required:

```bash
AI_NAME=Claude python3 scripts/ai_status.py reopen AG-FE-ID-001-SIDECAR-BFF-HANDOFF "Describe the exact packet correction needed."
```

## 13. Owner Closeout Finalization

Closeout owner: `Codex`

Finalization reason: `owned_finalize_dispatch` after sidecar ownership was
auto-reassigned from `Codex2` to `Codex`.

Reviewer approval is recorded in task state by `Claude`: the review note
approves the support-only scope, `/me` and `/capabilities` implementation
classification, `/servant/ensure` 501 backend-not-ready classification,
frontend target list, absorption gates, and missing section 23 / route-catalog
attention items.

Closeout scope confirmation:

- changed support packet metadata only
- did not change L1 canonical truth
- did not change core contract truth
- did not change BFF runtime, registry, governance, or frontend implementation

Closeout verification:

```bash
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py
```

Result: task state showed `review_approved` with owner `Codex`, reviewer
`Claude`; focused BFF verification passed with `22 passed in 12.53s`.

*Prepared by Codex2 for the `AG-FE-ID-001-SIDECAR-BFF-HANDOFF` support slice.*
