# AG-FE-ID-001 Followup-4 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-20` |
| Status | `ready for Claude review` |
| Mutates canonical truth | `false` |

Scope constraint: this is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, registry code,
governance implementation, or execute-plans source.

## 1. Purpose

This fourth followup packet gives the parent owner a narrow handoff for
`AG-FE-ID-001` after the previous AG-FE-ID-001 sidecars and the AG-BE-ID-002
support packets. It focuses on the implementation decision the parent now faces:

1. `AG-BE-ID-002` is blocked, so successful servant provisioning is not
   available.
2. `AG-BE-ID-003` still depends on that blocked backend task, so session and
   command surfaces cannot be treated as complete.
3. The frontend can only implement a strict auth/scope/status shell that
   truthfully renders backend-not-ready states, or it must block pending backend
   design clarification.

This packet does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Observed status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | active `in_progress` | This support packet is the only intended deliverable. |
| `AG-FE-ID-001` | active `todo` | Parent implementation has not started in this checkout. |
| `AG-FE-000` | archived `done` | Separate Agora/Management entry work exists, but the parent must keep bundle isolation from regressing. |
| `AG-BE-ID-002` | active `blocked`, waiting for `Codex` | `POST /bff/agora/servant/ensure` cannot be treated as a successful route. |
| `AG-BE-ID-003` | active `todo`, depends on `AG-BE-ID-002` | Interactive/trainer/research session facade cannot be treated as ready. |

Dependency honesty rule for the parent: `AG-FE-ID-001` depends on
`AG-BE-ID-003`, and `AG-BE-ID-003` depends on blocked `AG-BE-ID-002`. The parent
must either stop and carry a blocker, or explicitly scope its frontend work to a
blocked/degraded status shell. It must not close as a successful
servant/session flow while this backend chain remains unresolved.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_4.md` | This sidecar's support-only assignment |
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent and backend dependency task definitions |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF.md` | Original FE handoff packet |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Runtime-vs-contract route gap and frontend handoff packet |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` | Bundle isolation and blocked-shell packet |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-REVIEW.md` | Claude approval record for followup-2 |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-REVIEW.md` | Claude approval record for followup-3 |
| `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-BFF-HANDOFF.md` | Backend servant ensure gap packet |
| `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-ACCEPTANCE.md` | Backend acceptance/blocker framing |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` | Frozen local Agora route/schema/capability snapshot |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/` | Design closure files cited by dispatch; no local §23 UI layout source found |
| `services/control-plane/bff/agora/router.py` | Implemented `/bff/agora/me` and `/bff/agora/capabilities` routes |
| `services/control-plane/bff/agora/servant/router.py` | Current `/bff/agora/servant/ensure` 501 stub |
| `services/control-plane/bff/agora/identity/router.py` | Notes session/ask routes still live in `main.py` pending migration |
| `services/control-plane/bff/tests/test_agora_router.py` | Focused route behavior evidence |
| `services/control-plane/bff/tests/test_agora_identity_scope.py` | User-private scope and servant policy evidence |
| `services/control-plane/specs/agora/capability_manifest.json` | Frozen seven Agora capabilities and route prefixes |
| `services/control-plane/specs/agora/servant_profile.schema.json` | Servant profile safety schema |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora entry still renders `AskPersonas` directly |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Current Agora page still uses legacy direct-fetch ask helper |
| `execute-plans/src/lib/bff/agora.ts` | Legacy Agora ask helper, direct `fetch` |
| `execute-plans/src/lib/bff-v1/paths.ts` | Broad path object still contains Management and capital route strings |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated Agora type snapshot; lacks the three readiness routes |

## 4. Current BFF Query Gap

| Route | Runtime status | Contract/generated status | Parent handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover envelope, tenant/user predicate, capabilities, and servant policy | Exact route absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | Parent may use it only as accepted interim runtime truth. Do not claim generated contract coverage. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered manifest and backend scope | Exact route absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | Same as `/me`; keep the client narrow and local to Agora identity readiness. |
| `POST /bff/agora/servant/ensure` | Registered in `services/control-plane/bff/agora/servant/router.py`; authenticates then returns HTTP 501 `NOT_IMPLEMENTED` | Exact route absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | Parent client must map 501 to `backend_not_ready`; no servant success state until `AG-BE-ID-002` resolves. |

The current safe BFF facts are identity scope, capability filtering, and
servant policy display. A successful `ServantProfile` is not available today.

## 5. AG-BE-ID-002 Blocker Impact On AG-FE-ID-001

`AG-BE-ID-002` is not merely pending implementation. It is actively blocked with
design/adapter questions:

- the parent task asks for `POST /bff/agora/servant/ensure`, but the frozen
  route catalog does not list it
- the task cites a §5.4 capability set, but the local design surface does not
  provide a servant-specific allow/deny set beyond the seven frozen
  `agora.*.v1` capabilities and `ServantProfile.policy`
- the current OpenClaw adapter surface exposes session/tool workflows, but no
  accepted BFF-callable servant provisioning facade is present
- requested old artifact paths such as `integrations/openclaw/adapter/agora_servant.py`
  are missing in this checkout

Frontend consequence: `AG-FE-ID-001` should not implement UI that implies
servant provisioning, OpenClaw reconciliation, session startup, or command
execution is live. The honest implementation options are:

| Option | Parent action | Review condition |
|---|---|---|
| Stop on blocker | Carry a blocker that backend route/catalog/provisioning truth is unresolved | Preferred if parent acceptance still requires "login then ensure servant" as a successful flow |
| Blocked shell only | Implement strict auth/scope clients and a status shell that renders `backend_not_ready` for ensure/session | Acceptable only if reviewer narrows parent completion to a truthful blocked/degraded shell |
| Full success shell | Implement servant/session success states | Not acceptable until `AG-BE-ID-002` and `AG-BE-ID-003` land and are reviewable |

## 6. Frontend Surface Still Missing

| Surface | Current state | Parent action |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Renders `AskPersonas` directly | Parent should route through `AgoraApp.tsx` or an approved equivalent shell before exposing Ask/session surfaces. |
| `execute-plans/src/agora/AgoraApp.tsx` | Missing | Parent artifact still needs implementation or explicit blocker. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | Missing | Parent artifact still needs implementation if interim runtime routes are accepted. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | Missing | Parent artifact still needs 501/backend-not-ready handling if the route is called. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present with generated snapshot and `ServantProfile`-adjacent types, but no exact readiness operations | Reuse shared generated types where compatible; do not overclaim route coverage. |
| `execute-plans/src/lib/bff-v1/paths.ts` | Broad path object contains Management, capital pool, broker/readiness, and Management AI path strings | Do not import it into Agora identity/servant clients unless bundle scan proves no forbidden strings leak. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch helper for ask routes | Not sufficient for parent acceptance; status shell should use strict clients, not page-local direct `fetch`. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Imports `postAsk`, `openAskSse`, and `getAskSession` from the legacy helper | Parent shell should gate it behind auth/scope/backend-readiness status. |

The previous AG-FE-000 lesson still applies: broad BFF path imports can pull
Management route strings into the Agora bundle. The parent should keep exact
route constants local to narrow Agora client modules or wait for route-specific
generated helpers after contract reconciliation.

## 7. Minimal Blocked-Shell Contract For Parent

If parent `AG-FE-ID-001` proceeds before backend dependencies clear, the safe
shape is:

```text
agora-main.tsx
  -> AgoraApp.tsx
     -> identity.getAgoraMe()
     -> identity.getAgoraCapabilities()
     -> servant.ensureAgoraServant()
     -> 501 maps to backend_not_ready
     -> Ask/session/command surfaces disabled or read-only
```

Required states:

| State | Trigger | UI/runtime rule |
|---|---|---|
| Auth blocked | Missing auth or `401` from `/me`, `/capabilities`, or `/servant/ensure` | Render blocked auth state; do not show servant/session controls. |
| Scope/audience blocked | `403`, wrong tenant, wrong audience, or missing Agora capability | Render blocked scope state; do not retry with seed/mock data. |
| Identity ready, backend not ready | `/me` and `/capabilities` succeed, `/servant/ensure` returns 501 | Show identity/capability/policy facts and unavailable servant status. |
| Session facade unavailable | `AG-BE-ID-003` still todo or session route status cannot be tied to private servant | Keep Ask/session/command surfaces disabled or explicitly read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured | Render unavailable state; no silent mock fallback. |
| Servant active | Future non-501 `ServantProfile` response after backend work lands | Display profile/status only; do not expose order, broker, capital, or RuntimeBinding authority. |

The shell may show `servant_policy.execution_authority = "none"` and
`prohibited_authority = ["runtime_binding", "broker_order", "capital_binding"]`
as safety facts. It must not turn those facts into controls.

## 8. Operator Journey To Hand To Parent

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

## 9. Parent Absorption Checklist For Claude

Claude should not accept parent absorption unless the parent evidence answers
these checks:

| Check | Required evidence |
|---|---|
| Backend dependency decision | Parent either opens/carries a blocker for unresolved servant/session backend truth, or explicitly narrows scope to blocked-shell-only. |
| Route truth | Parent states that `/me`, `/capabilities`, and `/servant/ensure` are interim runtime routes, not generated contract-complete routes. |
| 501 handling | `servant.ts` tests prove current 501 maps to `backend_not_ready`, not a fabricated profile or success state. |
| Strict transport | `identity.ts` and `servant.ts` use live strict semantics and never fall back to seed/mock data in live strict mode. |
| No page-level direct fetch | `AgoraApp.tsx` and status-shell pages call the strict Agora clients, not page-local `fetch` or the legacy ask helper. |
| Narrow imports | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` do not import `@/lib/bff-v1/paths`, Management clients, capital helpers, broker helpers, or RuntimeBinding controls. |
| Bundle isolation | `npm run build:agora` followed by forbidden-string scan has no Management/capital/broker/RuntimeBinding leakage. |
| Ask/session gating | `AskPersonas` is gated behind the status shell and cannot imply sessions are ready while `AG-BE-ID-003` is todo. |
| Missing UI spec | The local missing §23 UI source is supplied, or the parent carries it as a blocker; do not invent layout/widgets. |

## 10. Suggested Verification For Parent

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

Expected current sidecar interpretation:

- `/me` and `/capabilities` appear only in
  `services/control-plane/bff/agora/router.py`.
- `/servant/ensure` appears only in the BFF servant stub and focused route
  tests.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing, as
  expected before parent implementation.
- Local SD has no §23 UI layout source in this checkout; parent must not invent
  layout/widgets from the dispatch reference alone.

## 11. Sidecar Verification

Commands run for this sidecar:

```bash
git status -sb
git branch --show-current
git remote -v
sed -n '1,220p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_4.md
sed -n '1,220p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,220p' ai-status.json
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-000
sed -n '1,300p' services/control-plane/bff/agora/router.py
sed -n '1,260p' services/control-plane/bff/agora/servant/router.py
sed -n '1,220p' services/control-plane/bff/agora/identity/router.py
sed -n '1,320p' services/control-plane/bff/tests/test_agora_router.py
sed -n '1,340p' services/control-plane/bff/tests/test_agora_identity_scope.py
sed -n '1,220p' services/control-plane/specs/agora/capability_manifest.json
sed -n '1,260p' services/control-plane/specs/agora/servant_profile.schema.json
sed -n '1,260p' support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-BFF-HANDOFF.md
sed -n '1,260p' support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-ACCEPTANCE.md
sed -n '1,240p' execute-plans/src/entries/agora-main.tsx
sed -n '1,260p' execute-plans/src/agora/pages/AskPersonas.tsx
sed -n '1,260p' execute-plans/src/lib/bff/agora.ts
sed -n '1,300p' execute-plans/src/lib/bff-v1/paths.ts
test -f execute-plans/src/agora/AgoraApp.tsx && printf 'EXISTS\n' || printf 'MISSING\n'
test -f execute-plans/src/lib/bff-v1/agora/identity.ts && printf 'EXISTS\n' || printf 'MISSING\n'
test -f execute-plans/src/lib/bff-v1/agora/servant.ts && printf 'EXISTS\n' || printf 'MISSING\n'
find execute-plans/src/lib/bff-v1/agora -maxdepth 2 -type f -print | sort
rg -n -P '/bff/agora/me(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/capabilities(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/servant/ensure(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/servant/router.py services/control-plane/bff/tests/test_agora_router.py
rg -n "section 23|§23|^## 23|^# 23|^## [0-9]+|^# [0-9]+" docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md
rg -n "servant/ensure|agora/me|agora/capabilities|ServantProfile|agora.identity.v1|§5\\.4|5\\.4|OpenClaw|adapter" docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure
```

Final focused validation before handoff:

```bash
git diff --check -- support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_4.md
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
```

Results:

- `git diff --check` passed.
- `22 passed in 11.09s`.

## 12. Handoff

This packet is ready for Claude review. The intended parent use is to decide
whether `AG-FE-ID-001` should stop on backend blockers or proceed only as a
truthful blocked-shell implementation, with no successful servant/session
claims until the backend dependency chain clears.
