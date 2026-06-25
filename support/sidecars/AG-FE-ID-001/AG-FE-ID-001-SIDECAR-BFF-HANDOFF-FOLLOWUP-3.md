# AG-FE-ID-001 Followup-3 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
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

This followup packet gives the parent owner a final narrow handoff before
`AG-FE-ID-001` starts implementation. It builds on the original handoff and
followup-2, but focuses on the current decision surface after `AG-FE-000`
closed:

1. which BFF route facts are safe to depend on today
2. which frontend shell behavior can be implemented as a blocked/degraded shell
3. how to avoid reintroducing Management route strings into the Agora bundle
4. what Claude should review before the parent absorbs this sidecar

This packet does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Observed status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | active `in_progress` | This support packet is the only intended deliverable. |
| `AG-FE-ID-001` | active `todo` | Parent implementation has not started in this checkout. |
| `AG-FE-000` | archived `done` | Separate Agora/Management entry work is available; bundle isolation must not regress. |
| `AG-BE-ID-002` | active `blocked`, waiting for `Codex` | Servant ensure cannot be presented as successful. |
| `AG-BE-ID-003` | active `todo`, depends on `AG-BE-ID-002` | Session/command surfaces must remain disabled or explicitly blocked. |

Dependency honesty rule for the parent: `AG-FE-ID-001` depends on
`AG-BE-ID-003`, which depends on blocked `AG-BE-ID-002`. The parent may prepare
strict clients and a blocked-shell experience, but it should not mark the
status shell complete as a live servant/session flow unless the backend
dependency chain clears or reviewer explicitly narrows the parent acceptance to
"blocked shell only."

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_3.md` | This sidecar's support-only assignment |
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent and dependency task definitions |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF.md` | First FE handoff packet |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Followup-2 route-contract and frontend handoff packet |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-REVIEW.md` | Claude approval record for followup-2 |
| `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-BFF-HANDOFF.md` | Backend servant ensure gap analysis |
| `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-ACCEPTANCE.md` | Backend acceptance and blocker framing |
| `services/control-plane/bff/agora/router.py` | Implemented `/bff/agora/me` and `/bff/agora/capabilities` routes |
| `services/control-plane/bff/agora/servant/router.py` | Current `/bff/agora/servant/ensure` 501 stub |
| `services/control-plane/bff/tests/test_agora_router.py` | Focused BFF route behavior evidence |
| `services/control-plane/bff/tests/test_agora_identity_scope.py` | User-private scope and servant policy evidence |
| `services/control-plane/specs/agora/capability_manifest.json` | Frozen seven Agora capabilities and path prefixes |
| `services/control-plane/specs/agora/servant_profile.schema.json` | Servant profile safety schema |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora entry still renders `AskPersonas` directly |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Current Agora page still uses the legacy `@/lib/bff/agora` ask helper |
| `execute-plans/src/lib/bff/agora.ts` | Legacy Agora helper now inlines ask paths, but still uses direct `fetch` |
| `execute-plans/src/lib/bff-v1/paths.ts` | Broad path object still contains Management and capital path strings |

## 4. BFF Query Gap Still True

| Route | Runtime status | Generated/contract status | Parent handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; covered by focused BFF tests | Exact route still absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | Parent can use it only as accepted interim runtime truth; do not claim generated contract coverage. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; covered by focused BFF tests | Exact route still absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | Same as `/me`; keep the client local to the Agora identity surface. |
| `POST /bff/agora/servant/ensure` | Registered in `services/control-plane/bff/agora/servant/router.py`; returns HTTP 501 `NOT_IMPLEMENTED` | Exact route still absent from OpenAPI, capability manifest path prefixes, generated Agora types, and frontend `paths.ts` | Client must map 501 to `backend_not_ready`; no servant success state until `AG-BE-ID-002` resolves. |

The current BFF-safe facts are identity scope, capability manifest filtering, and
servant policy display. A successful `ServantProfile` is not available today.

## 5. Post-AG-FE-000 Bundle Isolation Lesson

`AG-FE-000` closed with an important frontend lesson: importing a broad BFF path
surface can put Management route strings into the Agora production bundle even
when the visible page is Agora-only.

Current local facts:

| Surface | Observed state | Parent rule |
|---|---|---|
| `execute-plans/src/lib/bff-v1/paths.ts` | Contains Agora helpers, Management helpers, capital-pool helpers, broker/readiness strings, and Runtime-adjacent management paths in one exported object | Do not import the broad `paths` object into the new Agora shell or clients unless a bundle check proves it does not leak forbidden strings. |
| `execute-plans/src/lib/bff/agora.ts` | No longer imports `paths.ts`; it inlines ask route strings and calls `fetch` directly | Can remain for existing Ask behavior, but it is not the final `AG-FE-ID-001` identity/servant client path. |
| `execute-plans/src/entries/agora-main.tsx` | Renders `AskPersonas` directly | Parent should route through `AgoraApp.tsx` or equivalent approved shell before exposing Ask/session surfaces. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Imports `postAsk`, `openAskSse`, and `getAskSession` from the legacy helper | Parent shell should gate this page behind auth/scope/backend readiness, not treat it as the status shell. |

Recommended parent implementation boundary:

- Put exact route constants for `/bff/agora/me`, `/bff/agora/capabilities`, and
  `/bff/agora/servant/ensure` inside narrow Agora client modules, or use a
  route-specific generated source after the contract is reconciled.
- Do not import `@/lib/bff-v1/paths` wholesale into Agora identity/servant
  clients.
- Do not import Management client modules, capital pool helpers, broker helpers,
  RuntimeBinding controls, or Management AI routes from `AgoraApp.tsx`.
- Keep `AskPersonas` behind a disabled/read-only state until `AG-BE-ID-003`
  clears or the parent acceptance is explicitly narrowed.

## 6. Minimal Blocked-Shell Shape For Parent

If the parent owner proceeds before backend dependencies clear, the only safe
frontend deliverable is a blocked or degraded status shell:

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
| Auth blocked | Missing auth or `401` from `/me` or `/capabilities` | Render blocked auth state; do not show servant/session controls. |
| Scope/audience blocked | `403`, wrong tenant, wrong audience, or missing Agora capability | Render blocked scope state; do not retry with seed/mock data. |
| Identity ready, backend not ready | `/me` and `/capabilities` succeed, `/servant/ensure` returns 501 | Show identity/capability/policy facts and a servant unavailable status. |
| BFF unavailable in strict mode | Network error or 5xx while configured for live strict behavior | Render unavailable state; no silent mock fallback. |
| Servant active | Future non-501 `ServantProfile` response after backend work lands | Display profile/status only; do not expose order, broker, capital, or RuntimeBinding authority. |

## 7. Parent Absorption Checklist

Claude should not accept parent absorption unless the parent evidence answers
these checks:

| Check | Required evidence |
|---|---|
| Route truth | Parent explicitly states whether `/me`, `/capabilities`, and `/servant/ensure` are accepted interim runtime routes or blocked pending OpenAPI/manifest reconciliation. |
| 501 handling | `servant.ts` tests prove current 501 maps to `backend_not_ready`, not a fabricated profile or success state. |
| Strict transport | `identity.ts` and `servant.ts` use live strict semantics and never fall back to seed/mock data in live strict mode. |
| Narrow imports | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` do not import `@/lib/bff-v1/paths`, Management clients, capital helpers, broker helpers, or RuntimeBinding controls. |
| Bundle scan | `npm run build:agora` followed by `rg -n "/management|RuntimeBinding|capital-pool|broker" dist/agora` has no forbidden matches, or any match is explicitly reviewed as non-route/non-control noise. |
| Current page gating | `AskPersonas` is gated behind the status shell and cannot imply sessions are ready while `AG-BE-ID-003` is todo. |
| Missing artifacts | `execute-plans/src/agora/AgoraApp.tsx`, `identity.ts`, and `servant.ts` exist only after parent implementation; they remain missing in this sidecar snapshot. |
| Backend dependency honesty | Parent does not close as a successful servant/session flow while `AG-BE-ID-002` is blocked and `AG-BE-ID-003` is todo. |

## 8. Suggested Verification For Parent

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
```

Expected current sidecar interpretation:

- `/me` and `/capabilities` appear only in
  `services/control-plane/bff/agora/router.py`.
- `/servant/ensure` appears only in the BFF servant stub and focused route
  tests.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing, as
  expected before parent implementation.
- `paths.ts` still contains Management and capital route strings; importing it
  into Agora shell/client code is a bundle-isolation risk.

## 9. Sidecar Verification

Commands run for this sidecar:

```bash
git status -sb
git branch --show-current
git remote -v
sed -n '1,220p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_3.md
sed -n '1,220p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,240p' ai-status.json
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-000
sed -n '1,260p' services/control-plane/bff/agora/router.py
sed -n '1,240p' services/control-plane/bff/agora/servant/router.py
sed -n '1,220p' services/control-plane/bff/agora/identity/router.py
sed -n '1,280p' services/control-plane/bff/tests/test_agora_router.py
sed -n '1,300p' services/control-plane/bff/tests/test_agora_identity_scope.py
sed -n '1,220p' services/control-plane/specs/agora/capability_manifest.json
sed -n '1,220p' services/control-plane/specs/agora/servant_profile.schema.json
sed -n '1,220p' execute-plans/src/lib/bff/agora.ts
sed -n '1,220p' execute-plans/src/entries/agora-main.tsx
sed -n '1,240p' execute-plans/src/lib/bff-v1/paths.ts
test -f execute-plans/src/agora/AgoraApp.tsx && printf 'EXISTS\n' || printf 'MISSING\n'
test -f execute-plans/src/lib/bff-v1/agora/identity.ts && printf 'EXISTS\n' || printf 'MISSING\n'
test -f execute-plans/src/lib/bff-v1/agora/servant.ts && printf 'EXISTS\n' || printf 'MISSING\n'
rg -n -P '/bff/agora/me(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/capabilities(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/servant/ensure(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/servant/router.py services/control-plane/bff/tests/test_agora_router.py
rg -n "@/lib/bff-v1/paths|paths\\." execute-plans/src/agora execute-plans/src/entries/agora-main.tsx execute-plans/src/lib/bff/agora.ts
rg -n "@/lib/bff/agora|postAsk|openAskSse|getAskSession" execute-plans/src/agora execute-plans/src/entries/agora-main.tsx
```

Final focused validation run before handoff:

```bash
git diff --check -- support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_3.md
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
```

Results:

- `git diff --check` passed.
- `22 passed in 11.89s`.
- Exact-route searches still show `/me` and `/capabilities` only in
  `services/control-plane/bff/agora/router.py`.
- Exact-route search still shows `/servant/ensure` only in
  `services/control-plane/bff/agora/servant/router.py` and
  `services/control-plane/bff/tests/test_agora_router.py`.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing, as expected
  for parent absorption.

## 10. Handoff

This packet is ready for Claude review. The intended parent use is to absorb the
blocked-shell implementation boundary, route-contract caveats, and bundle
isolation checks into `AG-FE-ID-001` before any execute-plans code is written.
