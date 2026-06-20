# AG-FE-ID-001 Followup-7 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex2` / `Claude` |
| Date | `2026-06-20` |
| Status | `review approved; owner closeout pending` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, registry code,
governance implementation, OpenClaw adapter code, or execute-plans source.

## 1. Purpose

This seventh followup packet updates the AG-FE-ID-001 handoff after two recent
`dev` changes:

1. `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` merged through PR #1824 and is
   the approved frontend-side baseline.
2. The Agora contract-layer closure archive merged through PR #1819. That pack
   gives v1.1 direction for servant, workshop, dashboard, UI IA, and dependency
   decisions, but explicitly does not auto-unblock stopped implementation tasks.

The parent handoff outcome is unchanged in the important operational sense:
`AG-FE-ID-001` must not present a successful servant/session flow while
`AG-BE-ID-002`, `AG-BE-ID-003`, and the contract-extension predecessor tasks are
still unresolved. The only safe near-term parent shape is a truthful
blocked/degraded status shell, or a parent blocker that waits for the v1.1
contract and generated frontend types.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex2`.

| Task | Observed status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | active `in_progress` before this packet | This packet is the only intended deliverable. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | merged by PR #1824 | Previous approved handoff remains the baseline. |
| `AG-FE-ID-001` | active `todo`; owner `Claude`, reviewer `Codex` | Parent implementation has not started in this checkout. |
| `AG-FE-000` | archived `done` | Separate Agora/Management bundle work exists, with prior review history around management string leakage. |
| `AG-BE-ID-002` | active `blocked`; owner `Codex2`, reviewer `Codex`, waiting for `Codex` | Successful servant ensure remains unavailable. |
| `AG-BE-ID-003` | active `todo`; depends on `AG-BE-ID-002` | Interactive/trainer/research session facade remains unavailable. |
| `AG-XR-001A` | active `in_progress`; owner `Codex`, reviewer `Claude` | Additive v1.1 bundle predecessor is not done. |
| `AG-XR-OPENAPI-001` | active `todo`; depends on `AG-XR-001A` | Servant/workshop OpenAPI v1.1 and capability v1.1 are not merged or mirrored. |
| `AG-XR-DASH-001` | active `todo`; depends on `AG-XR-001A` | Dashboard v2 contract work is not merged. |
| `AG-XR-003` | active `todo`; depends on `AG-XR-001A` | Compatibility manifest gate is not landed. |

Parent dependency honesty rule: `AG-FE-ID-001` depends on `AG-BE-ID-003`, and
`AG-BE-ID-003` depends on blocked `AG-BE-ID-002`. The new contract-closure pack
adds another practical gate: servant success should wait for
`AG-XR-OPENAPI-001` plus generated execute-plans types, not just BFF code.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_7.md` | This sidecar's support-only assignment |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | Confirms owner, reviewer, artifact, and in-progress state |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent is still todo and depends on `AG-BE-ID-003` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure parent is still blocked |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms session facade parent still depends on blocked servant ensure |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-001A` | Confirms additive extension predecessor is active, not done |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms OpenAPI v1.1/capability v1.1 predecessor is todo |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md` | Previous approved FE handoff baseline |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6-REVIEW.md` | Claude approval record for the previous followup |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/INDEX.md` | Contract pack authority statement: proposal until canonical artifacts merge and mirror |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/ARCHIVE_NOTES.md` | Explicitly says the pack does not auto-unblock stopped tasks |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md` | Future servant route family, headers, adapter, and capability direction |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/05_execute_plans_agora_ui_ia_and_dependencies.md` | Future Agora IA and FE BFF boundary direction |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/07_dispatch_unblock_matrix_v2.md` | States stopped tasks remain STOP until contracts merge, verify, and types mirror |
| `services/control-plane/bff/agora/router.py` | Runtime implements `/bff/agora/me` and `/bff/agora/capabilities` |
| `services/control-plane/bff/agora/servant/router.py` | Runtime registers `/bff/agora/servant/ensure`, authenticates, then returns 501 |
| `services/control-plane/bff/tests/test_agora_router.py` | Focused tests assert the current route behavior |
| `services/control-plane/bff/tests/test_agora_identity_scope.py` | Focused tests assert tenant/user predicate and no-authority servant policy |
| `services/control-plane/specs/agora/capability_manifest.json` | Frozen seven Agora capabilities; no `agora.servant.v1` yet |
| `services/control-plane/specs/agora/servant_profile.schema.json` | User-private `ServantProfile` schema with no runtime/broker/capital authority |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora entry still renders `AskPersonas` directly |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Current Agora page still imports legacy ask helper and assistant catalog surfaces |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch ask helper |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated schema snapshot includes `ServantProfile`, but not readiness operations |

## 4. Delta Since Followup-6

The approved FOLLOWUP-6 packet remains accurate. The material delta is the
merged contract-closure archive. It adds a better answer to "what should the
future contract be?" but not an implementation-unblock answer.

| New source | What changed | FE parent implication |
|---|---|---|
| Contract closure `INDEX.md` | The pack is a design decision proposal until canonical artifacts are merged into `pantheon@dev` and mirrored/generated into `execute-plans@dev`. | Parent may cite it as direction, not as active generated contract truth. |
| `ARCHIVE_NOTES.md` | The pack does not auto-unblock stopped tasks; it defines predecessor tasks that must be implemented, hash-verified, and type-mirrored first. | Parent must keep successful servant/session flow blocked. |
| `03_servant_and_workshop_contracts.md` | Future servant family is `/bff/agora/servant*`; ensure derives tenant/user from auth, requires `Authorization`, `Idempotency-Key`, `X-Request-Id`, and uses existing adapter extensions for `/api/openclaw-adapter/agents/*`. | Future `servant.ts` should align to this shape after v1.1 lands, but current runtime is still a 501 stub. |
| `05_execute_plans_agora_ui_ia_and_dependencies.md` | Future primary IA is `/agora/trading-room`, `/agora/strategy-workshop`, `/agora/strategy-performance`; all reads/writes use `src/lib/bff-v1/agora/*`; pages must not call `fetch()` directly. | Parent shell should not invent another unrelated side-menu app. If built before v1.1, it must stay blocked-shell-only. |
| `07_dispatch_unblock_matrix_v2.md` | `AG-BE-ID-002` remains STOP until `AG-XR-OPENAPI-001` merges OpenAPI v1.1 + capability v1.1 + adapter contract. | FE parent must not treat the closure pack itself as backend readiness. |

The design pack also notes that `agora_openapi_extension_v1_1.yaml` is a seed,
not complete authority. Prose docs 03/04 are the authority for the future
contract tasks. Therefore parent `AG-FE-ID-001` should not generate client
behavior directly from the seed file or treat it as a complete route list.

## 5. BFF Query Ledger For Parent

| Route | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; tests cover envelope, tenant/user predicate, seven Agora capabilities, and servant policy | Exact route absent from frozen OpenAPI, frozen capability manifest path prefixes, generated Agora types, and frontend `paths.ts`; contract-closure does not add this route as a generated v1.1 operation | Parent may use it only as accepted interim runtime route truth for identity readiness. Keep any client narrow and local to Agora identity readiness. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; tests cover filtered manifest and backend scope | Exact route absent from frozen OpenAPI, frozen capability manifest path prefixes, generated Agora types, and frontend `paths.ts`; contract-closure does not add this route as a generated v1.1 operation | Same as `/me`; do not claim generated contract coverage. |
| `POST /bff/agora/servant/ensure` | Registered in `services/control-plane/bff/agora/servant/router.py`; authenticates then returns HTTP 501 `NOT_IMPLEMENTED` | Absent from frozen OpenAPI, frozen manifest, generated Agora types, and frontend `paths.ts`; present in contract-closure prose/seed as future v1.1 direction, but `AG-XR-OPENAPI-001` is not done | `servant.ts` must map current 501 to `backend_not_ready`; no successful `ServantProfile`, active servant, or session flow until v1.1 contract and `AG-BE-ID-002` implementation land. |

The safe BFF facts today remain identity scope, capability filtering, and
display-only servant policy. A successful `ServantProfile` response is not
available in this checkout.

## 6. Frontend Surface To Hand Off

| Surface | Current state | Required parent decision |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Renders `AskPersonas` directly | Parent should route through `AgoraApp.tsx` or an approved equivalent status shell before exposing Ask/session behavior. |
| `execute-plans/src/agora/AgoraApp.tsx` | Missing | Parent must implement it only if scope is narrowed to blocked-shell-only or backend/contract blockers are cleared. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | Missing | Parent may add narrow strict clients for `/me` and `/capabilities` as interim runtime clients, or wait for contract reconciliation. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | Missing | Parent may add explicit 501/backend-not-ready handling; success path must wait for v1.1 generated contract and backend implementation. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present generated schema snapshot, including `ServantProfile`, but no readiness operations | Reuse schema types where compatible; do not claim route operation coverage. |
| `execute-plans/src/lib/bff-v1/paths.ts` | Broad path object contains Management, capital pool, broker/readiness, and Management AI path strings | Do not import it into Agora shell/client code unless a bundle scan proves no forbidden strings leak. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch helper for Ask routes | Not sufficient for parent identity/servant acceptance; status shell should use strict BFF-v1 Agora clients. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Imports `postAsk`, `openAskSse`, and `getAskSession` from the legacy helper; also imports assistant catalog surfaces | Parent shell must gate Ask/session/command surfaces until identity, servant, and session readiness are truthfully available. |
| Contract-closure IA | Three-tab IA proposed: trading room, strategy workshop, strategy performance | Useful future design direction, but not enough to implement active servant/session UX before the v1.1 contracts and types land. |

Current source scan found only the schema enum literal `redacted_management` in
the Agora v1 generated types when scanning the current Agora source areas for
Management/capital/broker/RuntimeBinding leakage. The parent still needs a
post-build bundle scan after implementing the shell and clients.

## 7. Minimal Blocked-Shell Contract

If parent `AG-FE-ID-001` proceeds before backend and v1.1 contract dependencies
clear, the only safe frontend shape remains:

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
| Contract not mirrored | v1.1 servant capability/OpenAPI/types are not present in execute-plans | Do not show active servant or generated-client completion claims. |
| Session facade unavailable | `AG-BE-ID-003` still todo or route status cannot be tied to a private servant | Keep Ask/session/command surfaces disabled or explicitly read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured | Render unavailable state; no silent mock fallback. |
| Servant active | Future non-501 `ServantProfile` response after v1.1 contract and backend work land | Display profile/status only; no order, broker, capital, or RuntimeBinding authority. |

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

### Future v1.1 journey, still blocked

```text
Contract tasks land and generated types mirror
  -> frontend uses v1.1 servant client under src/lib/bff-v1/agora/*
  -> ensure sends auth-derived identity only, with Idempotency-Key and X-Request-Id
  -> BFF creates or reconciles exactly one user-private servant profile
  -> BFF persists/reconciles the Persona Registry record with tenant/user scope
  -> BFF invokes governed OpenClaw adapter agent ensure/reconcile routes
  -> BFF returns { data: ServantProfile, meta: ... }
  -> downstream servant sessions bind to that persona_id
```

This success journey remains blocked until `AG-XR-OPENAPI-001`, `AG-BE-ID-002`,
and `AG-BE-ID-003` resolve their contract, registry, OpenClaw facade, error,
response-envelope, type generation, and session facade gaps.

## 9. Parent Absorption Checklist

Claude should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Backend blocker disposition | Parent either stops on unresolved `AG-BE-ID-002`/`AG-BE-ID-003`, or explicitly narrows completion to blocked-shell-only. |
| Contract-closure disposition | Parent states whether it is waiting for `AG-XR-OPENAPI-001` and generated execute-plans types, or proceeding only with interim runtime route clients. |
| Route truth | Parent states that `/me`, `/capabilities`, and current `/servant/ensure` are interim runtime routes, not generated contract-complete routes. |
| v1.1 seed handling | Parent does not generate client success behavior from `agora_openapi_extension_v1_1.yaml`; prose/accepted artifacts and generated types must lead. |
| Strict clients | `identity.ts` and `servant.ts` use live strict semantics, do not fall back to mock/seed data, and do not issue page-local `fetch` from UI components. |
| 501 handling | `servant.ts` tests prove current 501 maps to `backend_not_ready`, not success. |
| Future headers | Any future-facing servant ensure client design accounts for `Idempotency-Key` and `X-Request-Id` once v1.1 is accepted. |
| No broad path import | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` do not import `@/lib/bff-v1/paths`, Management clients, capital helpers, broker helpers, or RuntimeBinding controls. |
| Ask/session gating | `AskPersonas` is gated behind shell status and cannot imply session readiness while `AG-BE-ID-003` is todo. |
| IA alignment | Parent does not invent an unrelated IA. It either uses blocked-shell-only scope or waits for accepted three-tab IA/contracts. |
| Bundle isolation | `npm run build:agora` followed by forbidden-string scan has no Management/capital/broker/RuntimeBinding leakage, excluding explicitly reviewed inert schema text. |
| Tests | Frontend tests cover identity success, auth blocked, scope/audience blocked, strict BFF failure, servant 501, and no forbidden imports. |

## 10. Suggested Parent Verification

Backend current-state checks:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
rg -n -P '/bff/agora/me(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/capabilities(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/servant/ensure(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/servant/router.py services/control-plane/bff/tests/test_agora_router.py
```

Contract-closure readiness checks:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-001A
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003
rg -n 'does NOT auto-unblock|OpenAPI v1.1|agora.servant.v1|generated types' docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure
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
- `/servant/ensure` appears in the BFF servant stub and focused route tests; it
  also appears in the new contract-closure proposal/seed, but not in active
  frozen OpenAPI or generated execute-plans operation types.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing before
  parent implementation.
- The contract-closure pack gives future IA/route direction, not current
  successful servant/session availability.

## 11. Sidecar Verification

Commands run for this sidecar:

```bash
git status -sb
git branch --show-current
git remote -v
sed -n '1,240p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_7.md
sed -n '1,240p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,260p' ai-status.json
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-000
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-001A
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DASH-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003
sed -n '1,260p' support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
sed -n '1,220p' support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6-REVIEW.md
sed -n '1,220p' docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/ARCHIVE_NOTES.md
sed -n '1,240p' docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/INDEX.md
sed -n '1,300p' docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md
sed -n '1,300p' docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/05_execute_plans_agora_ui_ia_and_dependencies.md
sed -n '1,300p' docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/07_dispatch_unblock_matrix_v2.md
sed -n '1,240p' docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/agora_openapi_extension_v1_1.yaml
sed -n '1,260p' docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/agora_contract_extension_manifest_v1_1.json
sed -n '1,280p' services/control-plane/bff/agora/router.py
sed -n '1,260p' services/control-plane/bff/agora/servant/router.py
sed -n '1,220p' services/control-plane/bff/agora/identity/router.py
sed -n '1,340p' services/control-plane/bff/tests/test_agora_router.py
sed -n '1,360p' services/control-plane/bff/tests/test_agora_identity_scope.py
sed -n '1,280p' services/control-plane/specs/agora/capability_manifest.json
sed -n '1,300p' services/control-plane/specs/agora/servant_profile.schema.json
sed -n '1,240p' execute-plans/src/entries/agora-main.tsx
sed -n '1,320p' execute-plans/src/agora/pages/AskPersonas.tsx
sed -n '1,260p' execute-plans/src/lib/bff/agora.ts
find execute-plans/src/lib/bff-v1/agora -maxdepth 2 -type f -print
rg -n -P '/bff/agora/(me|capabilities|servant/ensure)(?=["`\s:]|$)|agoraMe|agoraCapabilities|ensureAgoraServant|ServantProfile' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json services/control-plane/bff/agora services/control-plane/bff/tests/test_agora_router.py execute-plans/src/lib/bff-v1 execute-plans/src/lib/bff execute-plans/src/agora execute-plans/src/entries
rg -n '@/lib/bff-v1/paths|paths\.|/bff/management|management|RuntimeBinding|capital-pool|broker' execute-plans/src/agora execute-plans/src/entries/agora-main.tsx execute-plans/src/lib/bff-v1/agora execute-plans/src/lib/bff/agora.ts
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
git diff --check -- .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_7.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md
```

The `test -f` checks for `identity.ts`, `servant.ts`, and `AgoraApp.tsx`
reported all three as missing. The current Agora source leakage scan found only
`redacted_management` in generated schema text.

## 12. Review Approval And Closeout

Reviewer: `Claude`

Review artifact:
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7-REVIEW.md`

Claude approved this packet on `2026-06-20` with no changes requested. The
review independently confirmed support-only scope compliance, current 22-test
BFF verification, route-ledger accuracy, missing frontend artifact status, and
no Management/capital/broker/RuntimeBinding leakage in the scanned Agora source
areas.

Owner closeout should preserve the approved packet and review artifact as the
task-scoped deliverables, then mark the sidecar done only after the task PR is
merged:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh done AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7 "FOLLOWUP-7 sidecar closed after Claude approval; support packet and review artifact merged."
```

*Prepared by Codex2 for the `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` support slice.*
