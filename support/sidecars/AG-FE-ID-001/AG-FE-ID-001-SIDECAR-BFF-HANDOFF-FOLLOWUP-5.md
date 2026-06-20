# AG-FE-ID-001 Followup-5 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-20` |
| Status | `ready for Claude review` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, registry code,
governance implementation, or execute-plans source.

## 1. Purpose

This fifth followup packet gives parent `AG-FE-ID-001` a reviewer-ready handoff
focused on three surfaces:

1. the exact BFF query ledger the frontend may and may not rely on
2. the truthful operator journey while servant/session backend dependencies are
   still unresolved
3. the frontend implementation checklist for `AgoraApp.tsx`, `identity.ts`, and
   `servant.ts`

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Observed status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | active `in_progress` | This support packet is the only intended deliverable. |
| `AG-FE-ID-001` | active `todo`; owner `Claude`, reviewer `Codex` | Parent implementation has not started in this checkout. |
| `AG-FE-000` | archived `done` | Separate Agora/Management entry and bundle split exists as upstream base. |
| `AG-BE-ID-002` | active `blocked`, waiting for `Codex` | Successful servant provisioning cannot be treated as available. |
| `AG-BE-ID-003` | active `todo`, depends on `AG-BE-ID-002` | Interactive/trainer/research session facade cannot be treated as ready. |

Parent dependency honesty rule: `AG-FE-ID-001` depends on `AG-BE-ID-003`, and
`AG-BE-ID-003` depends on blocked `AG-BE-ID-002`. The parent must either carry a
backend blocker or explicitly narrow its frontend delivery to a strict
blocked/degraded shell. It must not close as a successful servant/session flow
while this backend dependency chain remains unresolved.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_5.md` | This sidecar's support-only assignment |
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent and backend dependency definitions |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF.md` | Original BFF/frontend handoff |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Runtime-vs-generated route gap |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` | Bundle isolation and blocked-shell warning |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` | Backend blocker impact and absorption gates |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-REVIEW.md` | Latest Claude approval record for this handoff family |
| `services/control-plane/bff/agora/router.py` | Implemented `/bff/agora/me` and `/bff/agora/capabilities` |
| `services/control-plane/bff/agora/servant/router.py` | Current `/bff/agora/servant/ensure` 501 stub |
| `services/control-plane/bff/agora/identity/router.py` | Documents identity/session route migration gap |
| `services/control-plane/bff/tests/test_agora_router.py` | Focused route behavior tests |
| `services/control-plane/bff/tests/test_agora_identity_scope.py` | User-private scope and servant policy tests |
| `services/control-plane/specs/agora/capability_manifest.json` | Frozen seven Agora capabilities and route prefixes |
| `services/control-plane/specs/agora/servant_profile.schema.json` | Servant profile safety and no-authority schema |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` | Local SD section and route catalog snapshot |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/` | Design closure references cited by dispatch |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora entry surface |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Current Agora page still reachable from entry |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch Agora ask helper |
| `execute-plans/src/lib/bff-v1/paths.ts` | Broad path object containing Management/capital strings |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated Agora type snapshot |

## 4. BFF Query Ledger For Parent

| Route | Runtime BFF status | Generated/contract status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; tests cover envelope, tenant/user predicate, seven capabilities, and servant policy | Exact route absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | Parent may use it only as accepted interim runtime route truth; keep a narrow route constant in `identity.ts` or wait for contract reconciliation. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; tests cover filtered manifest and backend scope | Exact route absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | Same as `/me`; do not overclaim generated contract coverage. |
| `POST /bff/agora/servant/ensure` | Registered in `services/control-plane/bff/agora/servant/router.py`; authenticates then returns HTTP 501 `NOT_IMPLEMENTED` | Exact route absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | `servant.ts` must map 501 to `backend_not_ready`; no successful servant profile or active state until `AG-BE-ID-002` resolves. |

The safe BFF facts today are identity scope, capability filtering, and
display-only servant policy. A successful `ServantProfile` response is not
available in this checkout.

## 5. Frontend Surface To Hand Off

| Surface | Current state | Required parent decision |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Renders `AskPersonas` directly | Parent should route through `AgoraApp.tsx` or an approved equivalent shell before exposing Ask/session behavior. |
| `execute-plans/src/agora/AgoraApp.tsx` | Missing | Parent must implement it or carry a blocker. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | Missing | Parent must implement narrow strict clients for `/me` and `/capabilities`, or block pending contract reconciliation. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | Missing | Parent must implement explicit 501/backend-not-ready handling if it calls ensure. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present generated snapshot, but does not include the three readiness operations | Reuse generated schemas/types where compatible; do not claim route operation coverage. |
| `execute-plans/src/lib/bff-v1/paths.ts` | Broad path object contains Management, capital pool, broker/readiness, and Management AI path strings | Do not import it into Agora shell/client code unless a bundle scan proves no forbidden strings leak. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch helper for Ask routes | Not sufficient for parent identity/servant acceptance; status shell should use strict BFF-v1 Agora clients. |

Current source scan of `execute-plans/src/agora`, `execute-plans/src/entries/agora-main.tsx`,
`execute-plans/src/lib/bff-v1/agora`, and `execute-plans/src/lib/bff/agora.ts`
found only the schema enum literal `redacted_management`, not a Management route
or control. The parent still needs a post-build bundle scan after implementing
the shell and new clients.

## 6. Minimal Blocked-Shell Contract

If parent `AG-FE-ID-001` proceeds before backend dependencies clear, the safe
shape is:

```text
agora-main.tsx
  -> AgoraApp.tsx
     -> identity.getAgoraMe()
     -> identity.getAgoraCapabilities()
     -> servant.ensureAgoraServant()
     -> 501 maps to backend_not_ready
     -> Ask/session/command surfaces remain disabled or read-only
```

Required shell states:

| State | Trigger | UI/runtime rule |
|---|---|---|
| Auth blocked | Missing auth or `401` from any readiness call | Render blocked auth state; no servant/session controls. |
| Scope/audience blocked | `403`, wrong tenant, wrong audience, or missing Agora capability | Render blocked scope state; no seed/mock retry. |
| Identity ready, backend not ready | `/me` and `/capabilities` succeed, `/servant/ensure` returns 501 | Show identity/capability/policy facts and unavailable servant status. |
| Session facade unavailable | `AG-BE-ID-003` still todo or route status cannot be tied to a private servant | Keep Ask/session/command surfaces disabled or explicitly read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured | Render unavailable state; no silent mock fallback. |
| Servant active | Future non-501 `ServantProfile` response after backend work lands | Display profile/status only; no order, broker, capital, or RuntimeBinding authority. |

`servant_policy.execution_authority = "none"` and
`prohibited_authority = ["runtime_binding", "broker_order", "capital_binding"]`
may be displayed as safety facts. They must not become operator controls.

## 7. Operator Journey

### Current honest journey

```text
Operator opens agora.html
  -> Agora bundle loads from the separate Agora entry
  -> frontend verifies Agora-scoped auth/audience
  -> frontend calls GET /bff/agora/me through a strict client
  -> BFF returns tenant_id, user_id, fail-closed read_predicate,
     seven Agora capabilities, and servant_policy
  -> frontend calls GET /bff/agora/capabilities through a strict client
  -> BFF returns filtered capability manifest and backend scope
  -> frontend calls POST /bff/agora/servant/ensure only if parent accepts the
     interim runtime stub as callable
  -> current backend returns 501 NOT_IMPLEMENTED
  -> shell renders servant provisioning unavailable/backend not ready
  -> Ask/session/command surfaces remain disabled or read-only
```

### Journey that remains blocked

```text
Operator logs in
  -> ensure creates or reconciles a user-private servant profile
  -> OpenClaw agent is provisioned/reconciled through a governed adapter
  -> frontend receives { data: ServantProfile, meta: ... }
  -> session facade starts interactive/trainer/research flows
```

This success journey remains blocked until `AG-BE-ID-002` and `AG-BE-ID-003`
resolve their backend contract and implementation gaps.

## 8. Parent Absorption Checklist

Claude should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Backend dependency decision | Parent either stops on a backend blocker or explicitly narrows completion to blocked-shell-only. |
| Route truth | Parent states that `/me`, `/capabilities`, and `/servant/ensure` are interim runtime routes, not generated contract-complete routes. |
| Strict clients | `identity.ts` and `servant.ts` use live strict semantics, do not fall back to mock/seed data, and do not issue page-local `fetch` from UI components. |
| 501 handling | `servant.ts` tests prove current 501 maps to `backend_not_ready`, not success. |
| No broad path import | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` do not import `@/lib/bff-v1/paths`, Management clients, capital helpers, broker helpers, or RuntimeBinding controls. |
| Ask/session gating | `AskPersonas` is gated behind shell status and cannot imply session readiness while `AG-BE-ID-003` is todo. |
| Bundle isolation | `npm run build:agora` followed by forbidden-string scan has no Management/capital/broker/RuntimeBinding leakage, excluding explicitly reviewed inert schema text. |
| UI design source | Missing local SD §23 UI layout source is supplied, or parent carries a blocker; do not invent layout/widgets. |
| Tests | Frontend tests cover identity success, auth blocked, scope/audience blocked, strict BFF failure, servant 501, and no forbidden imports. |

## 9. Suggested Parent Verification

Backend current-state checks:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
rg -n -P '/bff/agora/me(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/capabilities(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/servant/ensure(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/servant/router.py services/control-plane/bff/tests/test_agora_router.py
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

- `/me` and `/capabilities` appear only in
  `services/control-plane/bff/agora/router.py`.
- `/servant/ensure` appears only in the BFF servant stub and focused route
  tests.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing before
  parent implementation.
- Local `SD_2026-06-20.md` has sections 0 through 8 and no local §23 UI layout
  source; parent must not invent layout/widgets from the dispatch reference
  alone.

## 10. Sidecar Verification

Commands run for this sidecar:

```bash
git status -sb
git branch --show-current
git remote -v
sed -n '1,220p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_5.md
sed -n '1,220p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,220p' ai-status.json
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-000
sed -n '1,280p' services/control-plane/bff/agora/router.py
sed -n '1,260p' services/control-plane/bff/agora/servant/router.py
sed -n '1,220p' services/control-plane/bff/agora/identity/router.py
sed -n '1,340p' services/control-plane/bff/tests/test_agora_router.py
sed -n '1,360p' services/control-plane/bff/tests/test_agora_identity_scope.py
sed -n '1,260p' services/control-plane/specs/agora/capability_manifest.json
sed -n '1,280p' services/control-plane/specs/agora/servant_profile.schema.json
sed -n '1,260p' scripts/dispatch_agora_cross_repo_2026-06-20.py
sed -n '1,260p' execute-plans/src/entries/agora-main.tsx
sed -n '1,300p' execute-plans/src/agora/pages/AskPersonas.tsx
sed -n '1,280p' execute-plans/src/lib/bff/agora.ts
sed -n '1,340p' execute-plans/src/lib/bff-v1/paths.ts
find execute-plans/src/lib/bff-v1/agora -maxdepth 2 -type f -print | sort
test -f execute-plans/src/agora/AgoraApp.tsx && printf 'AgoraApp.tsx EXISTS\n' || printf 'AgoraApp.tsx MISSING\n'
test -f execute-plans/src/lib/bff-v1/agora/identity.ts && printf 'identity.ts EXISTS\n' || printf 'identity.ts MISSING\n'
test -f execute-plans/src/lib/bff-v1/agora/servant.ts && printf 'servant.ts EXISTS\n' || printf 'servant.ts MISSING\n'
rg -n -P '/bff/agora/me(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/capabilities(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/servant/ensure(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/servant/router.py services/control-plane/bff/tests/test_agora_router.py
rg -n 'section 23|§23|^## 23|^# 23|^## [0-9]+|^# [0-9]+' docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md
rg -n 'servant/ensure|agora/me|agora/capabilities|ServantProfile|agora.identity.v1|§5\.4|5\.4|OpenClaw|adapter' docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure
rg -n '@/lib/bff-v1/paths|paths\.|/bff/management|management|RuntimeBinding|capital-pool|broker' execute-plans/src/agora execute-plans/src/entries/agora-main.tsx execute-plans/src/lib/bff-v1/agora execute-plans/src/lib/bff/agora.ts
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
```

Final focused validation before handoff:

```bash
git diff --check -- support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
```

Results:

- Focused route tests: `22 passed in 8.77s`.
- Exact route search confirmed `/me` and `/capabilities` only in
  `services/control-plane/bff/agora/router.py`.
- Exact route search confirmed `/servant/ensure` only in
  `services/control-plane/bff/agora/servant/router.py` and
  `services/control-plane/bff/tests/test_agora_router.py`.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing, as
  expected before parent implementation.

## 11. Handoff

This packet is ready for Claude review. The intended parent use is to absorb the
BFF query ledger, operator journey, and frontend checklist before any
execute-plans implementation work begins. The parent should proceed only as a
truthful blocked-shell implementation or stop on the unresolved backend/design
blockers.
