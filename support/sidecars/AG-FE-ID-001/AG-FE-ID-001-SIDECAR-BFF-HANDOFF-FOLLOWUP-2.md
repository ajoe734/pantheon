# AG-FE-ID-001 Followup-2 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-20` |
| Status | `review approved; pending owner closeout` |
| Mutates canonical truth | `false` |

Scope constraint: this is support material only. It does not change L1 canonical
truth, OpenAPI, capability manifests, BFF runtime code, registry code,
governance implementation, or execute-plans source.

## 1. Purpose

This followup packet tightens the handoff for parent `AG-FE-ID-001` after the
first `AG-FE-ID-001-SIDECAR-BFF-HANDOFF` packet and the `AG-BE-ID-002`
support packets landed. It focuses on the current BFF query gap, the safe
operator journey, and the frontend client boundary for the Agora status shell.

The main parent task is still an implementation task owned by `Claude`; this
sidecar does not approve, reopen, or implement it.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Current status observed | FE implication |
|---|---|---|
| `AG-FE-000` | archived `done` | Separate Agora/Management entry work is available as an upstream base. |
| `AG-BE-ID-002` | active `blocked`, waiting for `Codex` | Servant provisioning cannot be treated as implemented. |
| `AG-BE-ID-003` | active `todo`, depends on `AG-BE-ID-002` | Interactive/trainer/research session facade is not ready. |
| `AG-FE-ID-001` | active `todo`, depends on `AG-FE-000` and `AG-BE-ID-003` | Parent shell should not claim complete session or successful servant ensure behavior until backend dependencies clear. |

Important dependency note: `AG-FE-ID-001` depends on `AG-BE-ID-003`, and
`AG-BE-ID-003` depends on blocked `AG-BE-ID-002`. The parent owner may prepare
strict frontend clients and blocked/degraded UI states, but should not present
servant provisioning or session operation as live.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_2.md` | This sidecar's support-only assignment |
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent `AG-FE-ID-001`, `AG-BE-ID-002`, and `AG-BE-ID-003` task definitions |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` | Frozen local Agora SD and route catalog snapshot |
| `services/control-plane/bff/agora/router.py` | Implemented `/bff/agora/me` and `/bff/agora/capabilities` handlers |
| `services/control-plane/bff/agora/servant/router.py` | Current `/bff/agora/servant/ensure` 501 stub |
| `services/control-plane/bff/agora/identity/router.py` | Notes that many identity/session routes still live in `main.py` |
| `services/control-plane/bff/tests/test_agora_router.py` | Focused route behavior evidence |
| `services/control-plane/bff/tests/test_agora_identity_scope.py` | Tenant/user isolation and servant policy evidence |
| `services/control-plane/specs/agora/capability_manifest.json` | Frozen seven Agora capability names and route prefixes |
| `services/control-plane/specs/agora/servant_profile.schema.json` | `ServantProfile` safety and user-private schema |
| `services/control-plane/openapi/agora_v1.openapi.yaml` | Generated route truth used by frontend contract snapshot |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated Agora contract snapshot/types |
| `execute-plans/src/lib/bff-v1/paths.ts` | Current frontend v1 path helper surface |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora entry point |
| `execute-plans/src/lib/bff/agora.ts` | Existing legacy direct-fetch Agora helper |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF.md` | First FE handoff packet |
| `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-BFF-HANDOFF.md` | Backend servant ensure gap packet |
| `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-ACCEPTANCE.md` | Backend acceptance/blocker packet |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-REVIEW.md` | Reviewer approval record rechecked during owner closeout |

## 4. BFF Query Gap Summary

| Route | Runtime BFF status | Contract/generated status | Handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py` and covered by focused tests | Exact route is absent from OpenAPI, capability manifest prefixes, generated Agora types, and frontend `paths.ts` | Parent may build a narrow hand-authored strict client only if reviewer accepts the BFF implementation as interim route truth; otherwise raise blocker before implementation. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py` and covered by focused tests | Exact route is absent from OpenAPI, capability manifest prefixes, generated Agora types, and frontend `paths.ts` | Same as `/me`: do not imply generated contract coverage exists. |
| `POST /bff/agora/servant/ensure` | Registered in `services/control-plane/bff/agora/servant/router.py`, but returns HTTP 501 `NOT_IMPLEMENTED` | Exact route is absent from OpenAPI, capability manifest prefixes, generated Agora types, and frontend `paths.ts` | Client must map 501 to `backend_not_ready` or equivalent; no success state unless `AG-BE-ID-002` resolves the backend contract and implementation. |

The first handoff packet already noted that `/servant/ensure` is missing from
the route catalog. This followup adds that `/me` and `/capabilities` are also
runtime-only from the frontend generator's perspective in this checkout.

## 5. Frontend Surface Observed

| Surface | Current state | Parent action |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Renders `AskPersonas` directly | Parent should introduce or route through the approved `AgoraApp.tsx` shell before claiming the status shell is implemented. |
| `execute-plans/src/agora/AgoraApp.tsx` | Missing | Required parent artifact. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | Missing | Required parent artifact; should encapsulate `/me` and `/capabilities` calls if reviewer accepts interim route truth. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | Missing | Required parent artifact; must handle current 501 without fabricating a servant. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present generated snapshot with 61 operations and seven capabilities | Reuse generated shared types and capability names, but do not assume it includes identity readiness or servant ensure routes. |
| `execute-plans/src/lib/bff-v1/paths.ts` | Contains Agora signal/inbox/journal/postmortem/ask helpers, plus broad Management/capital helpers | Agora app code should avoid importing the whole broad path object into the Agora bundle unless tree-shaking/scope checks prove no Management strings leak. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct `fetch` helper for ask routes | Not sufficient for `AG-FE-ID-001`; parent acceptance requires strict BFF-v1 clients and no page-level direct `fetch`. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Imports the legacy Agora helper and still acts as the current entry screen | Can remain a downstream page, but the status shell should gate it behind auth/scope/servant readiness instead of treating it as the app shell. |

## 6. Safe Parent Implementation Shape

### 6.1 `identity.ts`

Recommended minimal responsibilities:

- call `GET /bff/agora/me` through strict BFF transport
- call `GET /bff/agora/capabilities` through strict BFF transport
- return typed envelopes compatible with `agora.identity.v1`
- preserve tenant/user read predicate and servant policy fields from the BFF
- surface `401` and `403` as auth/audience/scope blocked states
- document that these two exact routes are runtime BFF routes not currently
  present in generated OpenAPI/types/path helpers

Do not copy the broad `paths` object into the Agora bundle just to build these
two URLs unless bundle scope verification proves no Management, capital pool,
broker, or RuntimeBinding strings are imported.

### 6.2 `servant.ts`

Recommended minimal responsibilities:

- call `POST /bff/agora/servant/ensure` through strict BFF transport
- return successful `ServantProfile` only after backend implementation exists
- map current HTTP 501 `NOT_IMPLEMENTED` to an explicit backend-not-ready state
- never infer a provisioned servant from `/bff/agora/me` alone
- never create a seed/mock servant in live/strict mode
- never expose runtime binding, broker order, or capital binding authority

The current `servant_profile.schema.json` allows only `execution_authority =
"none"` and `prohibited_authority` values for `runtime_binding`,
`broker_order`, and `capital_binding`. The frontend should display these as
safety facts only, not as controls.

### 6.3 `AgoraApp.tsx`

Recommended shell flow:

```text
Agora entry loads
  -> verify Agora audience/auth context
  -> call identity.getMe()
  -> call identity.getCapabilities()
  -> call servant.ensure()
  -> if ensure returns 501, render servant provisioning unavailable
  -> keep Ask/session/command surfaces disabled or read-only until backend dependencies clear
```

Required visible states:

| State | Trigger | UI requirement |
|---|---|---|
| Auth blocked | `401` or missing auth | Show blocked state; do not render servant/session controls. |
| Scope/audience blocked | `403`, cross-tenant, wrong audience, or missing Agora capability | Show blocked state; do not retry with fallback data. |
| Identity ready, backend not ready | `/me` and `/capabilities` succeed, `/servant/ensure` returns 501 | Show identity/capability context and an unavailable servant state. |
| Servant active | Future `ServantProfile` success from backend | Show profile/status only; keep no-order/no-capital authority boundary visible. |
| BFF unavailable in strict mode | network/5xx transport failure | Show degraded/unavailable state, no mock fallback. |

## 7. Operator Journey To Hand To Parent

```text
Operator opens agora.html
  -> Agora bundle must be separate from Management bundle
  -> auth audience must be pantheon-agora
  -> frontend calls GET /bff/agora/me
  -> BFF returns tenant_id, user_id, fail-closed read_predicate, seven Agora capabilities, and servant_policy
  -> frontend calls GET /bff/agora/capabilities
  -> BFF returns filtered capability manifest and backend scope
  -> frontend calls POST /bff/agora/servant/ensure
  -> current backend returns 501 NOT_IMPLEMENTED
  -> shell renders "servant provisioning unavailable/backend not ready" status
  -> ask/session/command surfaces remain disabled or read-only until AG-BE-ID-002 and AG-BE-ID-003 clear
```

The parent should not route operators into Management, capital pool,
RuntimeBinding, broker order, or live trading controls from this shell.

## 8. Parent Absorption Gates

| Gate | Required parent evidence |
|---|---|
| Identity route truth | Reviewer accepts either interim runtime route truth for `/me` and `/capabilities`, or parent opens a blocker asking for OpenAPI/manifest/path-helper reconciliation first. |
| Servant unavailable truth | `servant.ts` tests prove current 501 maps to backend-not-ready, not success. |
| Strict transport | No page-level direct `fetch`; app code calls `src/lib/bff-v1/agora/identity.ts` and `servant.ts`. |
| No generated-contract overclaim | Parent docs/tests do not claim generated Agora types/path helpers already include the three exact readiness routes. |
| Dependency honesty | Parent does not mark `AG-FE-ID-001` complete while `AG-BE-ID-002`/`AG-BE-ID-003` are unresolved unless scope is explicitly reduced to blocked shell only. |
| Bundle isolation | `dist/agora` has no Management route strings, capital pool controls, broker controls, RuntimeBinding controls, or Management component imports. |
| Auth/audience tests | Frontend tests cover happy identity load, `401`, `403`/cross-audience, strict BFF failure, and servant 501. |
| UI design blocker | Missing local SD section 23/UI detail is either supplied by the parent owner or carried as an explicit blocker; do not invent layout/widgets. |

## 9. Suggested Verification For Parent

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
```

Expected current exact-route search interpretation:

- `/me` and `/capabilities` should only appear in `services/control-plane/bff/agora/router.py`.
- `/servant/ensure` should only appear in the BFF servant stub and focused tests.
- If OpenAPI, generated types, capability manifest, or frontend path helpers later include these routes, parent should regenerate/update the strict clients from that accepted source instead of relying on this interim hand-authored route note.

## 10. Sidecar Verification

Commands run for this sidecar:

```bash
git status -sb
git branch --show-current
git remote -v
sed -n '1,220p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_2.md
sed -n '1,220p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,240p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,260p' ai-status.json
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-000
sed -n '1,260p' services/control-plane/bff/agora/router.py
sed -n '1,240p' services/control-plane/bff/agora/servant/router.py
sed -n '1,220p' services/control-plane/bff/agora/identity/router.py
sed -n '1,260p' services/control-plane/bff/agora/models.py
sed -n '1,240p' services/control-plane/bff/tests/test_agora_router.py
sed -n '1,260p' services/control-plane/bff/tests/test_agora_identity_scope.py
sed -n '1,220p' services/control-plane/specs/agora/capability_manifest.json
sed -n '1,220p' services/control-plane/specs/agora/servant_profile.schema.json
sed -n '1,260p' scripts/dispatch_agora_cross_repo_2026-06-20.py
test -f execute-plans/src/agora/AgoraApp.tsx
test -f execute-plans/src/lib/bff-v1/agora/identity.ts
test -f execute-plans/src/lib/bff-v1/agora/servant.ts
rg -n -P '/bff/agora/me(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/capabilities(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/servant/ensure(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/servant/router.py services/control-plane/bff/tests/test_agora_router.py
```

Focused route tests were re-run during final verification and should be recorded
in the task handoff status message.

Closeout verification re-run after reviewer approval:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
rg -n -P '/bff/agora/me(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/capabilities(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/servant/ensure(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/servant/router.py services/control-plane/bff/tests/test_agora_router.py
test -f execute-plans/src/agora/AgoraApp.tsx && echo EXISTS || echo MISSING
test -f execute-plans/src/lib/bff-v1/agora/identity.ts && echo EXISTS || echo MISSING
test -f execute-plans/src/lib/bff-v1/agora/servant.ts && echo EXISTS || echo MISSING
```

Closeout results:

- `22 passed in 9.24s`.
- `/me` and `/capabilities` still appear only in `services/control-plane/bff/agora/router.py`.
- `/servant/ensure` still appears only in the BFF servant stub and focused tests.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing, as expected for parent absorption.

## 11. Reviewer Approval

Reviewer: `Claude`

Verdict: `approved`

Review file:
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-REVIEW.md`

Approval summary:

- support-only scope compliance confirmed
- runtime-only `/me` and `/capabilities` vs generated route gaps confirmed
- `/servant/ensure` 501 backend-not-ready interpretation confirmed
- `AG-BE-ID-002` blocked / `AG-BE-ID-003` todo dependency honesty confirmed
- frontend absorption gates for `identity.ts`, `servant.ts`, and `AgoraApp.tsx` confirmed actionable

## 12. Owner Closeout Notes

Closeout owner: `Codex`

Closeout keeps this sidecar support-only. It does not change canonical truth,
OpenAPI, BFF runtime code, generated contracts, registry code, governance code,
or execute-plans source. The closeout commit includes only task-scoped support
records for the approved packet, the reviewer approval artifact, and the
generated task brief used to resume this closeout.
