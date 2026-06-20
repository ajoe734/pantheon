# AG-FE-ID-001 Followup-9 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex2` / `Claude2` |
| Date | `2026-06-20` |
| Status | `review approved; owner closeout in progress` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, registry code,
governance implementation, OpenClaw adapter code, or execute-plans source.

## 1. Purpose

This ninth followup packet updates the AG-FE-ID-001 handoff after the branch was
brought current with `origin/dev` at merge commit `ae7c693d`. Since FOLLOWUP-8,
several adjacent contract and dashboard tasks have advanced:

1. `AG-XR-OPENAPI-001` implementation merged into `dev` via PR #1841, so
   `services/control-plane/openapi/agora_v1_1.openapi.yaml` is now present in
   the repo and covers servant 8/8, workshop 13/13, adapter 3/3, and dashboard
   11/11 operations. Durable task state still shows `review_approved`, with an
   owner closeout artifact commit on the task branch not merged to `dev`.
2. `AG-XR-DASH-001` is archived `done`; dashboard contract evidence is accepted.
3. `AG-BE-DB-001` is archived `done`; dashboard recipe/widget runtime routes are
   implemented in `services/control-plane/bff/agora/dashboard/router.py`.
4. `AG-XR-003` and `AG-FE-DB-001` are now `in_progress`; the
   `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-3` support packet merged via PR
   #1850 without changing the `AG-XR-003` durable status.
5. `AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` merged via PR #1851 and
   records dashboard BFF/frontend handoff details. That packet is useful for
   dashboard renderer work, but does not change the AG-FE-ID-001 servant/session
   status shell handoff.

The parent handoff outcome for `AG-FE-ID-001` remains unchanged for the
servant/session success path. `AG-BE-ID-002` is still `todo`, `AG-BE-ID-003` is
still `todo`, and the execute-plans Agora frontend still lacks the parent target
files. The only safe near-term parent shape remains a truthful blocked/degraded
status shell, or a parent blocker waiting for backend implementation and
frontend type mirroring.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex2`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` | `in_progress` | This packet is the only intended deliverable. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` | merged via PR #1837 | Previous approved handoff remains the baseline. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000`, `AG-BE-ID-003` | Parent implementation has not started in this checkout. |
| `AG-FE-000` | archived `done` | Separate Agora/Management entry and bundle isolation work exists. |
| `AG-XR-OPENAPI-001` | `review_approved`; implementation PR #1841 merged | v1.1 OpenAPI artifact is on `dev`; durable closeout/status is not yet `done`. |
| `AG-XR-DASH-001` | archived `done` | Dashboard contract block complete. |
| `AG-BE-DB-001` | archived `done` | Dashboard recipe/widget runtime routes implemented; not a servant/session unblock. |
| `AG-XR-003` | `in_progress`; owner `Codex`, reviewer `Claude2` | Compatibility manifest/checksum gate still being built. |
| `AG-FE-DB-001` | `in_progress`; owner `Codex`, reviewer `Claude2` | Dashboard renderer work is separate from the parent ID shell. |
| `AG-BE-ID-002` | `todo`; depends on `AG-XR-OPENAPI-001` | Successful servant ensure/provision/reconcile remains unavailable. |
| `AG-BE-ID-003` | `todo`; depends on `AG-BE-ID-002` | Interactive/trainer/research session facade remains unavailable. |

Dependency honesty rule: `AG-FE-ID-001` depends on `AG-BE-ID-003`, and
`AG-BE-ID-003` depends on `AG-BE-ID-002`. Even though the v1.1 OpenAPI file now
exists on `dev`, the runtime servant/session success path is still blocked by
the backend implementation tasks and generated frontend mirror work.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_9.md` | This sidecar's support-only assignment. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` | Confirms owner, reviewer, artifact, and in-progress state. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent is still `todo` and depends on `AG-BE-ID-003`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms durable status is `review_approved`, with review notes approving the route set. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DASH-001` | Confirms dashboard contract task is archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DB-001` | Confirms dashboard runtime task is archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003` | Confirms compatibility manifest work is `in_progress`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-001` | Confirms dashboard renderer work is `in_progress`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure/provision/reconcile remains `todo`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms session facade remains `todo`. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.md` | Previous approved FE handoff baseline. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8-REVIEW.md` | Claude approval record for the previous followup. |
| `support/sidecars/AG-XR-OPENAPI-001/AG-XR-OPENAPI-001-SIDECAR-REVIEW.md` | Review evidence for servant/workshop v1.1 route coverage and safety boundaries. |
| `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md` | Latest compatibility-manifest sidecar support packet; `AG-XR-003` durable status remains `in_progress`. |
| `support/sidecars/AG-BE-DB-001/AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` | Latest dashboard BFF/frontend handoff; reinforces dashboard separation from servant/session readiness. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | Current v1.1 route artifact now visible in this checkout. |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | v1.1 capability families for servant, workshop, and dashboard. |
| `services/control-plane/specs/agora/widget_registry.v1.json` | Dashboard/widget registry added by `AG-BE-DB-001`. |
| `services/control-plane/bff/agora/router.py` | Runtime implements `/bff/agora/me` and `/bff/agora/capabilities`. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime still registers only `/bff/agora/servant/ensure`, authenticates, then returns 501. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Runtime strategy-workshop router remains a placeholder. |
| `services/control-plane/bff/agora/dashboard/router.py` | Runtime dashboard v2 routes and validator are implemented. |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora entry still renders `AskPersonas` directly. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated snapshot is still the frozen v1 bundle, not v1.1 route operations. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-8

The approved FOLLOWUP-8 packet remains accurate. The material deltas are
positive contract/dashboard progress, but not parent servant/session readiness.

| Change | What changed | FE parent implication |
|---|---|---|
| `agora_v1_1.openapi.yaml` present on `dev` | PR #1841 merged the v1.1 OpenAPI file. Review evidence records servant 8/8, workshop 13/13, adapter 3/3, dashboard 11/11 route coverage. | Parent may cite the v1.1 contract as landed in repo files. It still must not claim generated execute-plans clients or runtime success for servant/session. |
| `AG-XR-OPENAPI-001` durable status | `ai-status.sh show` still reports `review_approved`, not archived `done`. The task branch has a closeout/task-brief commit that is not in `origin/dev`. | Treat this as a status closeout gap, not as a reason to ignore the merged OpenAPI artifact. Parent should still wait for downstream backend/type mirror gates. |
| `AG-XR-DASH-001` -> `done` | Dashboard contract route block is archived done. | Useful for dashboard/IA work, but not a prerequisite satisfaction for `AG-FE-ID-001` servant/session shell. |
| `AG-BE-DB-001` -> `done` | Runtime dashboard v2 router implements 11 routes, ETag/If-Match concurrency, and core widget safety validation. | Do not confuse dashboard recipe/widget runtime readiness with servant ensure or session facade readiness. |
| `AG-XR-003` -> `in_progress` | Compatibility manifest and checksum gate work has started. | Parent still needs checksum/type-mirror evidence before claiming cross-repo compatibility. |
| `AG-FE-DB-001` -> `in_progress` | Dashboard WidgetRegistry/Renderer work has started in execute-plans scope. | Separate renderer stream; do not fold it into `AG-FE-ID-001` unless parent scope is explicitly expanded and reviewed. |
| `AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` merged | Dashboard BFF/frontend handoff packet records strict adapter and ETag details for dashboard renderer work. | Helpful context for `AG-FE-DB-001`; no change to parent servant/session readiness. |
| `AG-BE-ID-002` / `AG-BE-ID-003` | Both remain `todo`. | Successful servant profile, active servant, and session facade remain blocked. |

## 5. BFF Query Ledger For Parent

| Route | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover envelope, tenant/user predicate, capabilities, and servant policy. | Still absent from frozen `agora_v1.openapi.yaml`, v1.1 OpenAPI, `capability_manifest.json` path prefixes, and generated `execute-plans` types. | Parent may use it as accepted interim runtime route truth for identity readiness. Keep client narrow and local to Agora identity readiness. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered manifest and backend scope. | Same as `/me`: runtime route, not generated operation coverage. | Same as `/me`; do not claim generated contract coverage. |
| `POST /bff/agora/servant/ensure` | Registered in `services/control-plane/bff/agora/servant/router.py`; authenticates then returns HTTP 501 `NOT_IMPLEMENTED`. | Present in `agora_v1_1.openapi.yaml` and `capability_manifest_v1_1.json`, but not implemented as a successful runtime path and not mirrored into execute-plans generated clients. | `servant.ts` must map current 501 to `backend_not_ready`; no successful `ServantProfile`, active servant, or session flow until `AG-BE-ID-002` implements. |
| `GET/POST /bff/agora/servant/sessions*` | Not implemented in runtime router. | Present as v1.1 OpenAPI contract routes. `AG-BE-ID-003` remains `todo`. | Parent must keep Ask/session/command surfaces disabled or read-only until session facade lands. |
| `GET/POST /bff/agora/workshops*` | `strategy_workshop/router.py` is still a placeholder; older legacy workshop routes remain elsewhere. | Present as v1.1 OpenAPI contract routes. | Not part of the parent servant status shell success path; avoid using workshop contract presence as proof of implemented runtime. |
| Dashboard recipe/widget routes | Implemented in `services/control-plane/bff/agora/dashboard/router.py` by `AG-BE-DB-001`. | Present in v1.1 OpenAPI and `agora.dashboard.v2` capability. | Separate dashboard stream. Do not grant broker, capital, RuntimeBinding, or servant/session authority through dashboard readiness. |

The safe BFF facts today remain identity scope, capability filtering, display
of no-authority servant policy, and explicit backend-not-ready handling for the
servant stub. A successful `ServantProfile` response is not available in this
checkout.

## 6. Frontend Surface To Hand Off

| Surface | Current state | Required parent decision |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Renders `AskPersonas` directly. | Parent should route through `AgoraApp.tsx` or approved equivalent before exposing Ask/session behavior. |
| `execute-plans/src/agora/AgoraApp.tsx` | **MISSING**. | Parent must implement only if scope is narrowed to blocked-shell-only or backend/contract/type gates clear. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | **MISSING**. | Parent may add narrow strict clients for `/me` and `/capabilities` as interim runtime clients, or wait for contract reconciliation. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | **MISSING**. | Parent may add explicit 501/backend-not-ready handling; success path must wait for `AG-BE-ID-002`. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present generated frozen v1 snapshot; includes `ServantProfile` schema but not v1.1 readiness operations. | Reuse schema types only where compatible; do not claim v1.1 operation-client coverage until generated mirror work lands. |
| `execute-plans/src/lib/bff-v1/agora/contract-snapshot.json` | Frozen v1 snapshot. | Parent should not treat it as v1.1 compatibility proof. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch helper for Ask routes. | Not sufficient for parent identity/servant acceptance; status shell must use strict BFF-v1 Agora clients. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Existing Ask UI still present. | Parent shell must gate Ask/session/command surfaces until identity, servant, and session readiness are truthfully available. |

Current source scan from this sidecar:

```text
execute-plans/src/agora/pages/.placeholder
execute-plans/src/agora/pages/AskPersonas.tsx
execute-plans/src/entries/agora-main.tsx
execute-plans/src/lib/bff-v1/agora/contract-snapshot.json
execute-plans/src/lib/bff-v1/agora/types.ts
execute-plans/src/lib/bff/agora.ts
```

`AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing.

## 7. Minimal Blocked-Shell Contract

If parent `AG-FE-ID-001` proceeds before backend servant/session work and
execute-plans type mirror work clear, the only safe frontend shape remains:

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
| Auth blocked | Missing auth or `401` from any readiness call. | Render blocked auth state; no servant/session controls. |
| Scope/audience blocked | `403`, wrong tenant, wrong audience, or missing Agora capability. | Render blocked scope state; no seed/mock retry. |
| Identity ready, backend not ready | `/me` and `/capabilities` succeed, `/servant/ensure` returns 501. | Show identity/capability/policy facts and unavailable servant status. |
| Contract not mirrored | v1.1 route operations are not generated into execute-plans. | Do not show generated-client completion claims. |
| Session facade unavailable | `AG-BE-ID-003` still `todo`. | Keep Ask/session/command surfaces disabled or explicitly read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured. | Render unavailable state; no silent mock fallback. |
| Servant active | Future non-501 `ServantProfile` after `AG-BE-ID-002` lands. | Display profile/status only; no order, broker, capital, or RuntimeBinding authority. |

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
     seven frozen Agora capabilities, and servant_policy
  -> frontend calls GET /bff/agora/capabilities through a strict client
  -> BFF returns filtered capability manifest and backend scope
  -> frontend may call POST /bff/agora/servant/ensure only if parent accepts
     the interim runtime stub as callable
  -> current backend returns 501 NOT_IMPLEMENTED
  -> shell renders servant provisioning unavailable/backend not ready
  -> Ask/session/command surfaces remain disabled or read-only
```

### Future v1.1 servant journey, still blocked

```text
AG-XR-OPENAPI-001 closeout/status is fully durable and AG-XR-003/type mirror work clears
  -> AG-BE-ID-002 implements servant ensure/provision/reconcile
  -> AG-BE-ID-003 implements servant sessions facade
  -> frontend uses v1.1 servant client under src/lib/bff-v1/agora/*
  -> ensure sends auth-derived identity with Idempotency-Key and X-Request-Id
  -> BFF creates or reconciles exactly one user-private servant profile
  -> BFF persists/reconciles the Persona Registry record with tenant/user scope
  -> BFF invokes governed OpenClaw adapter agent ensure/reconcile routes
  -> BFF returns { data: ServantProfile, meta: ... }
  -> downstream servant sessions bind to that persona_id
```

The success journey is closer because the v1.1 route contract exists on `dev`.
It is still blocked by `AG-BE-ID-002`, `AG-BE-ID-003`, compatibility/type mirror
work, and frontend shell/client implementation.

## 9. Parent Absorption Checklist

Claude should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Backend blocker disposition | Parent either stops on unresolved `AG-BE-ID-002`/`AG-BE-ID-003`, or explicitly narrows completion to blocked-shell-only. |
| OpenAPI v1.1 disposition | Parent states that `agora_v1_1.openapi.yaml` is present on `dev`, while runtime success and generated execute-plans clients are still separate gates. |
| Route truth | Parent states `/me` and `/capabilities` are interim runtime routes, and current `/servant/ensure` returns 501 despite v1.1 contract presence. |
| Type mirror truth | Parent does not claim `execute-plans/src/lib/bff-v1/agora/types.ts` contains v1.1 operation coverage until generated mirror work lands. |
| Strict clients | `identity.ts` and `servant.ts` use live strict semantics, do not fall back to mock/seed data, and do not issue page-local `fetch` from UI components. |
| 501 handling | `servant.ts` tests prove current 501 maps to `backend_not_ready`, not success. |
| Future headers | Future-facing servant ensure client design accounts for `Idempotency-Key` and `X-Request-Id` once v1.1 is accepted into runtime implementation. |
| No broad path import | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` do not import broad Management/capital/broker/RuntimeBinding path helpers. |
| Ask/session gating | `AskPersonas` is gated behind shell status and cannot imply session readiness while `AG-BE-ID-003` is `todo`. |
| Dashboard separation | Parent does not confuse completed dashboard route work (`AG-BE-DB-001`) with servant/session readiness. |
| Bundle isolation | `npm run build:agora` followed by forbidden-string scan has no Management/capital/broker/RuntimeBinding leakage, excluding explicitly reviewed inert schema text. |
| Tests | Frontend tests cover identity success, auth blocked, scope/audience blocked, strict BFF failure, servant 501, no forbidden imports, and no forbidden bundle strings. |

## 10. Suggested Parent Verification

Backend current-state checks:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
python3 scripts/agora_schema_bundle.py --verify
python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"
rg -n "/bff/agora/me|/bff/agora/capabilities" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts services/control-plane/bff/agora/router.py
rg -n "ServantProfile|agora\\.servant|/bff/agora/servant" execute-plans/src/lib/bff-v1/agora/types.ts services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/specs/agora/v2/capability_manifest_v1_1.json
```

Contract and task status checks:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DASH-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DB-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001
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

- `/me` and `/capabilities` appear as runtime routes in
  `services/control-plane/bff/agora/router.py`, not as generated contract
  operations.
- `/servant/ensure` appears in v1.1 OpenAPI and the BFF servant stub, but the
  current runtime response is 501.
- v1.1 OpenAPI and v1.1 capability manifest are present in the repo.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing.
- Dashboard runtime work is done; servant/session runtime work is not.

## 11. Sidecar Verification

Commands run for this sidecar:

```bash
git branch --show-current
git status -sb
git fetch origin
git merge --ff-only origin/dev
git merge --ff-only origin/dev
AI_NAME=Codex2 ./scripts/ai-status.sh progress AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9 "Preparing support-only BFF/frontend handoff packet; no canonical/runtime/frontend implementation changes."
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DASH-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DB-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003
ls services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/specs/agora/v2/capability_manifest_v1_1.json services/control-plane/specs/agora/widget_registry.v1.json
test -f execute-plans/src/agora/AgoraApp.tsx && echo EXISTS || echo MISSING
test -f execute-plans/src/lib/bff-v1/agora/identity.ts && echo EXISTS || echo MISSING
test -f execute-plans/src/lib/bff-v1/agora/servant.ts && echo EXISTS || echo MISSING
python3 scripts/agora_schema_bundle.py --verify
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"
```

Results:

- Branch: `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` (correct).
- Branch was brought current with `origin/dev` merge commit `ae7c693d`; the task
  branch then merged `origin/dev` before PR publication.
- `AG-FE-ID-001`: `todo`.
- `AG-XR-OPENAPI-001`: `review_approved`; v1.1 OpenAPI implementation PR #1841
  is already merged into `dev`.
- `AG-XR-DASH-001`: archived `done`.
- `AG-BE-DB-001`: archived `done`.
- `AG-XR-003`: `in_progress`.
- `AG-FE-DB-001`: `in_progress`.
- `AG-BE-ID-002`: `todo`.
- `AG-BE-ID-003`: `todo`.
- `agora_v1_1.openapi.yaml`: present.
- `capability_manifest_v1_1.json`: present.
- `widget_registry.v1.json`: present.
- `AgoraApp.tsx`: MISSING.
- `identity.ts`: MISSING.
- `servant.ts`: MISSING.
- `python3 scripts/agora_schema_bundle.py --verify`: pass; 15 frozen v1 files OK.
- `python3 -m pytest ...`: 22 passed in 19.97s.
- v1.1 OpenAPI YAML parse: OK.

## 12. Reviewer Approval

Reviewer: `Claude2`

Review artifact:
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9-REVIEW.md`

Outcome: approved on `2026-06-20`; no changes requested.

Claude2 confirmed this packet is a faithful incremental update over FOLLOWUP-8.
The key approved additions vs FOLLOWUP-8 are:

1. `agora_v1_1.openapi.yaml` is now present on `dev` after PR #1841, but
   `AG-XR-OPENAPI-001` durable task state is still `review_approved`.
2. `AG-XR-DASH-001` and `AG-BE-DB-001` are done; dashboard runtime progress is
   separated from servant/session readiness.
3. `AG-XR-003` and `AG-FE-DB-001` are in progress.
4. `AG-BE-ID-002`, `AG-BE-ID-003`, `AgoraApp.tsx`, `identity.ts`, and
   `servant.ts` remain unresolved/missing, so parent `AG-FE-ID-001` must still
   avoid successful servant/session claims.

## 13. Owner Closeout

Closeout owner: `Codex2`

Closeout keeps the reviewed support-only scope intact. It publishes the review
artifact and task-brief approval update, then closes the durable task state only
after the task branch PR merges into `dev`.

Focused closeout checks:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9
git diff --check -- .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_9.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9-REVIEW.md
```

Expected closeout interpretation:

- Review gate passed; no packet content changes were requested by Claude2.
- The sidecar still mutates no canonical truth, runtime code, registry code,
  OpenAPI artifact, capability manifest, governance implementation, OpenClaw
  adapter code, or execute-plans source.
- Parent owner may absorb the support packet, but parent completion remains
  blocked/degraded unless backend servant/session implementation and frontend
  type/client gates clear.

*Prepared by Codex2 for the `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` support slice.*
