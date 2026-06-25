# AG-FE-ID-001 Followup-6 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-20` |
| Status | `review approved; ready for parent absorption` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, registry code,
governance implementation, OpenClaw adapter code, or execute-plans source.

## 1. Purpose

This sixth followup packet gives parent `AG-FE-ID-001` the latest handoff after
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` closed and after the backend
support packet `AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` merged. It focuses
on the current decision boundary for the frontend parent:

1. the BFF query ledger for identity readiness and servant ensure remains
   unchanged;
2. the backend servant provisioning parent remains blocked, even after the
   latest support packet merged;
3. the frontend parent must either stop on the backend/design blocker or deliver
   only a truthful blocked/degraded status shell.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Observed status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | active `in_progress` before this handoff | This packet is the only intended deliverable. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | archived `done`; PR #1813 merged | Previous FE handoff/review is durable and remains the baseline. |
| `AG-FE-ID-001` | active `todo`; owner `Claude`, reviewer `Codex` | Parent implementation has not started in this checkout. |
| `AG-FE-000` | archived `done` | Separate Agora/Management entry and bundle split exists as upstream base. |
| `AG-BE-ID-002` | active `blocked`; owner `Codex2`, reviewer `Codex`, waiting for `Codex` | Successful servant provisioning cannot be treated as available. |
| `AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | archived `done`; PR #1814 plus closeout PR #1821 merged | Latest backend support packet clarifies decisions, but does not unblock implementation. |
| `AG-BE-ID-003` | active `todo`; depends on `AG-BE-ID-002` | Interactive/trainer/research session facade cannot be treated as ready. |

Parent dependency honesty rule: `AG-FE-ID-001` depends on `AG-BE-ID-003`, and
`AG-BE-ID-003` depends on blocked `AG-BE-ID-002`. The parent must not close as a
successful servant/session flow while that backend chain remains unresolved.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_6.md` | This sidecar's support-only assignment |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001` | Parent status, dependencies, artifacts, and strict no-invention instructions |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure parent is still blocked |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms session facade parent still depends on the blocked backend task |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md` | Previous approved FE handoff baseline |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5-REVIEW.md` | Claude approval record for the previous FE handoff |
| `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Latest backend-side decision matrix and frontend handoff notes |
| `services/control-plane/bff/agora/router.py` | Runtime implements `/bff/agora/me` and `/bff/agora/capabilities` |
| `services/control-plane/bff/agora/servant/router.py` | Runtime registers `/bff/agora/servant/ensure`, authenticates, then returns 501 |
| `services/control-plane/bff/tests/test_agora_router.py` | Focused tests assert the current route behavior |
| `services/control-plane/bff/tests/test_agora_identity_scope.py` | Focused tests assert tenant/user predicate and no-authority servant policy |
| `services/control-plane/specs/agora/capability_manifest.json` | Frozen seven Agora capabilities; no servant-specific allow/deny set |
| `services/control-plane/specs/agora/servant_profile.schema.json` | User-private `ServantProfile` schema with no runtime/broker/capital authority |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` | Local SD route/schema snapshot; still has no local section 23 UI spec |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora entry still renders `AskPersonas` directly |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Current Agora page still imports the legacy direct-fetch ask helper |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch Agora ask helper |
| `execute-plans/src/lib/bff-v1/paths.ts` | Broad path object still contains Management/capital route strings |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated Agora schema snapshot includes `ServantProfile`, but not readiness operations |

## 4. Delta Since Followup-5

The approved FOLLOWUP-5 packet remains accurate. The only material delta is that
the backend support packet `AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` is now
merged and archived. That packet makes the backend unblock decisions explicit:

| Decision area | Backend support packet says parent must settle |
|---|---|
| Route authority | Decide whether `POST /bff/agora/servant/ensure` becomes an Agora v1 OpenAPI operation now or remains deferred/internal. |
| Request shape | Freeze whether ensure is bodyless/idempotent from auth scope or accepts safe display/profile preferences; client tenant/user fields remain forbidden. |
| Success envelope | Bind `ServantProfile` into the BFF envelope and define create/existing/suspended/retired/partial-sync outcomes. |
| Registry write owner | Name the exact persona registry helper/service that creates or reconciles one user-private `agora_servant` record. |
| OpenClaw facade | Name the Pantheon-owned adapter/helper that maps the registry record to an OpenClaw agent and private workspace. |
| Failure taxonomy | Freeze registry, policy, cross-tenant, OpenClaw transport, and OpenClaw sync failure codes. |
| Capability policy | Confirm whether the response returns the frozen seven Agora capabilities or a new versioned servant policy. |
| Frontend activation gate | State the evidence needed before execute-plans may show an active servant flow instead of backend-not-ready. |

Frontend implication: this merged backend support packet strengthens the blocker;
it does not authorize the FE parent to treat servant provisioning as complete.

## 5. BFF Query Ledger For Parent

| Route | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; tests cover envelope, tenant/user predicate, seven Agora capabilities, and servant policy | Exact route absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | Parent may use it only as accepted interim runtime route truth; keep any client narrow and local to Agora identity readiness. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; tests cover filtered manifest and backend scope | Exact route absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | Same as `/me`; do not claim generated contract coverage. |
| `POST /bff/agora/servant/ensure` | Registered in `services/control-plane/bff/agora/servant/router.py`; authenticates then returns HTTP 501 `NOT_IMPLEMENTED` | Exact route absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts`; no accepted success envelope exists | `servant.ts` must map 501 to `backend_not_ready`; no successful servant profile or active state until `AG-BE-ID-002` resolves. |

The safe BFF facts today are identity scope, capability filtering, and
display-only servant policy. A successful `ServantProfile` response is not
available in this checkout.

## 6. Frontend Surface To Hand Off

| Surface | Current state | Required parent decision |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Renders `AskPersonas` directly | Parent should route through `AgoraApp.tsx` or an approved equivalent shell before exposing Ask/session behavior. |
| `execute-plans/src/agora/AgoraApp.tsx` | Missing | Parent must implement it only if scope is narrowed to blocked-shell-only or backend blockers are cleared. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | Missing | Parent must implement narrow strict clients for `/me` and `/capabilities`, or block pending contract reconciliation. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | Missing | Parent must implement explicit 501/backend-not-ready handling if it calls ensure. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present generated schema snapshot, including `ServantProfile`, but no readiness operations | Reuse schema types where compatible; do not claim route operation coverage. |
| `execute-plans/src/lib/bff-v1/paths.ts` | Broad path object contains Management, capital pool, broker/readiness, and Management AI path strings | Do not import it into Agora shell/client code unless a bundle scan proves no forbidden strings leak. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch helper for Ask routes | Not sufficient for parent identity/servant acceptance; status shell should use strict BFF-v1 Agora clients. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Imports `postAsk`, `openAskSse`, and `getAskSession` from the legacy helper | Parent shell must gate Ask/session controls until identity, servant, and session readiness are truthfully available. |

Current source scan found only the schema enum literal `redacted_management` in
the Agora v1 generated types, not a Management route or control inside the
current Agora source scan. The parent still needs a post-build bundle scan after
implementing the shell and clients.

## 7. Minimal Blocked-Shell Contract

If parent `AG-FE-ID-001` proceeds before backend dependencies clear, the only
safe frontend shape is:

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

## 8. Operator Journey

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
  -> ensure creates or reconciles exactly one user-private servant profile
  -> BFF persists/reconciles the Persona Registry record with tenant/user scope
  -> BFF invokes a governed OpenClaw adapter facade for private agent sync
  -> BFF returns { data: ServantProfile, meta: ... }
  -> downstream Ask/session surfaces bind to that persona_id
```

This success journey remains blocked until `AG-BE-ID-002` and `AG-BE-ID-003`
resolve their route, registry, OpenClaw facade, error, response-envelope, and
session facade gaps.

## 9. Parent Absorption Checklist

Claude should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Backend blocker disposition | Parent either stops on the unresolved `AG-BE-ID-002` blocker or explicitly narrows completion to blocked-shell-only. |
| Backend decision matrix | Parent records accepted answers for the route authority, request shape, success envelope, registry write owner, OpenClaw facade, failure taxonomy, capability policy, and frontend activation gate from the latest backend support packet. |
| Route truth | Parent states that `/me`, `/capabilities`, and `/servant/ensure` are interim runtime routes, not generated contract-complete routes. |
| Strict clients | `identity.ts` and `servant.ts` use live strict semantics, do not fall back to mock/seed data, and do not issue page-local `fetch` from UI components. |
| 501 handling | `servant.ts` tests prove current 501 maps to `backend_not_ready`, not success. |
| No broad path import | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` do not import `@/lib/bff-v1/paths`, Management clients, capital helpers, broker helpers, or RuntimeBinding controls. |
| Ask/session gating | `AskPersonas` is gated behind shell status and cannot imply session readiness while `AG-BE-ID-003` is todo. |
| Bundle isolation | `npm run build:agora` followed by forbidden-string scan has no Management/capital/broker/RuntimeBinding leakage, excluding explicitly reviewed inert schema text. |
| UI design source | Missing local SD section 23 UI layout source is supplied, or parent carries a blocker; do not invent layout/widgets. |
| Tests | Frontend tests cover identity success, auth blocked, scope/audience blocked, strict BFF failure, servant 501, and no forbidden imports. |

## 10. Suggested Parent Verification

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
- Local `SD_2026-06-20.md` has sections 0 through 8 and no local section 23 UI
  layout source; parent must not invent layout/widgets from the dispatch
  reference alone.
- Latest backend support packets are merged, but active `AG-BE-ID-002` remains
  blocked; backend-not-ready remains the only truthful FE state for servant
  ensure.

## 11. Sidecar Verification

Commands run for this sidecar:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin
git merge --ff-only origin/dev
sed -n '1,220p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_6.md
sed -n '1,220p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,260p' ai-status.json
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-000
sed -n '1,300p' services/control-plane/bff/agora/router.py
sed -n '1,280p' services/control-plane/bff/agora/servant/router.py
sed -n '1,240p' services/control-plane/bff/agora/identity/router.py
sed -n '1,380p' services/control-plane/bff/tests/test_agora_router.py
sed -n '1,400p' services/control-plane/bff/tests/test_agora_identity_scope.py
sed -n '1,300p' services/control-plane/specs/agora/capability_manifest.json
sed -n '1,320p' services/control-plane/specs/agora/servant_profile.schema.json
sed -n '1,300p' execute-plans/src/entries/agora-main.tsx
sed -n '1,340p' execute-plans/src/agora/pages/AskPersonas.tsx
sed -n '1,320p' execute-plans/src/lib/bff/agora.ts
sed -n '1,380p' execute-plans/src/lib/bff-v1/paths.ts
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
rg -n '@/lib/bff/agora|postAsk|openAskSse|getAskSession' execute-plans/src/agora execute-plans/src/entries/agora-main.tsx
rg -n -P '/bff/agora/(me|capabilities|servant/ensure)(?=["`\s:]|$)|agoraMe|agoraCapabilities|ServantProfile|ensureAgoraServant|servant' execute-plans/src/lib/bff-v1 execute-plans/src/lib/bff execute-plans/src/agora execute-plans/src/entries
```

Final focused validation before handoff:

```bash
git diff --check -- .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_6.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
git diff --check --no-index -- /dev/null support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
```

Results:

- `git diff --check` passed.
- New-file `git diff --check --no-index` emitted no whitespace errors; exit 1
  was expected because `/dev/null` differs from the new file.
- Focused BFF Agora tests: `22 passed in 14.81s`.

## 12. Handoff

Claude approved this packet with no changes requested. The intended parent use
is to absorb the latest backend decision matrix together with the existing FE
blocked-shell contract before any execute-plans implementation work begins.
