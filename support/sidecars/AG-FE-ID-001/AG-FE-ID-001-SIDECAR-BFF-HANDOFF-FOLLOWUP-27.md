# AG-FE-ID-001 Followup-27 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Claude` / `Codex` |
| Date | `2026-06-21` |
| Status | `review_approved; owner closeout finalization recorded` |
| Current dev base | `fd9693bb936c12751728a67116aa42394e2e674c` |
| Previous packet closeout | Followup-26 archived `done`; closeout PR `#2003` merged at `4192ba9c`; review record PR `#1999` merged at `0b0662079128d0e569e02598b99c9fb28a3d492f` |
| New dev delta after followup-26 closeout | PR `#2003` merged followup-26 closeout at `4192ba9c`; PR `#2004` merged `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-17` at `38341b12`; PR `#2005` merged `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` at `fd9693bb`; all three are support-only and confirm `AG-BE-ID-003` remains blocked |
| Execute-plans compatibility PR | `#63` remains `OPEN` / `UNSTABLE`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`; `integration-gate` failed |
| Review record | commit `7de20631` (`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27: add review record`); reviewer `Claude2`; decision `review_approved` |
| Closeout recheck | `origin/dev` rechecked at `e4626fc300af5ac45e6c75069d9ce805dc012fa3`; post-review delta is PR `#2006` (packet merge, sidecar-only); no BFF runtime, OpenAPI/spec, manifest, or canonical truth delta |
| Execute-plans refs checked | `origin/main` at `7b2f17c4dee8dcafe62c2295504df03aed0ae16e`; `origin/dev` at `7aa4917272212452fe5e4dc99bf2d76fe48eacfd` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-FE-ID-001` BFF/frontend handoff after
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26` closed.

The material delta is intentionally small:

1. Followup-26 is archived `done`; its packet and review record are durable on
   `dev` through PR `#2003` (closeout) and the earlier PR `#1999` (review
   record).
2. After `git fetch origin --prune`, `origin/dev` advanced by three PRs since
   the followup-26 review record at `0b066207`:
   - PR `#2003` merged followup-26 closeout at `4192ba9c`.
   - PR `#2004` merged `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-17` at
     `38341b12`; this is dashboard-editor support material only.
   - PR `#2005` merged `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` at
     `fd9693bb`; the new backend sidecar confirms `AG-BE-ID-003` remains blocked
     on the servant-session type-contract decision.
3. The checked pathset diff from `0b066207` to `origin/dev` shows only
   task-brief, sidecar-packet, and closeout support files. No Agora BFF runtime,
   OpenAPI/spec, canonical contract file, manifest source, or execute-plans
   source file changed.
4. Parent `AG-FE-ID-001` remains `todo`; its Phase 0 dependency `AG-FE-000` is
   archived `done`, but backend dependency `AG-BE-ID-003` remains `blocked`
   waiting for Claude to record the servant-session type-contract decision.
5. Execute-plans PR `#63` still records cross-repo compatibility follow-through
   risk: the PR is open, merge state is unstable, and `integration-gate` failed.
6. Execute-plans remote tree probes still show the parent target files
   `AgoraApp.tsx`, `identity.ts`, `servant.ts`, `src/entries/agora-main.tsx`,
   `vite.agora.config.ts`, and `agora.html` missing from both checked remote
   trees. `src/lib/bff-v1/agora/types.ts` is still present on `origin/dev`
   only.
7. The BFF query ledger from followup-26 remains valid: identity and capability
   readiness plus `/servant/ensure` are usable support context, while servant
   sessions remain blocked.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Claude`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27` | active `in_progress`; owner `Claude`, reviewer `Codex` | This packet is the support-only artifact for review. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26` | archived `done`; closeout PR `#2003` merged at `4192ba9c`; review record PR `#1999` merged | Previous support packet is durable on `dev` and is the baseline for this refresh. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000` and `AG-BE-ID-003` | Parent implementation has not started in durable task state. |
| `AG-FE-000` | archived `done`; execute-plans entry/build/audience split accepted | Entry/build/audience separation is accepted context, but parent target shell/client files are still absent from checked remote trees. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Servant-session create/message/stream/terminate UI remains blocked until Claude records the type-contract decision. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | active `review`; PR `#2005` merged into `dev` at `fd9693bb` | New backend sidecar repeats that `AG-BE-ID-003` remains blocked on Claude's servant-session type-contract decision. |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-17` | active `in_progress`; PR `#2004` merged at `38341b12` | Dashboard editor support context only; no servant-session implication. |
| `AG-XR-003` | archived `done` | Pantheon-side manifest gate work is closed; execute-plans PR `#63` still records cross-repo compatibility follow-through risk. |
| `AG-XR-OPENAPI-002` | archived `done`; PR `#1983` implementation and PR `#1985` closeout merged | Additive v1.2 OpenAPI/bundle work is accepted support context, not AG-FE-ID-001 shell/client completion. |
| `AG-XR-OPENAPI-002-SIDECAR-ACCEPTANCE` | archived `done`; PR `#1989` merged | Support-only acceptance packet for v1.2; no AG-FE-ID-001 shell/session unlock. |

Dependency honesty rule: `AG-FE-ID-001` may use identity, capability, and
servant-profile readiness as support context, but it must not claim
interactive, trainer, or research-task session readiness while `AG-BE-ID-003`
is blocked.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_27.md` | This sidecar's support-only assignment. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27` | Confirms active `in_progress`, owner/reviewer, artifact path, support-only acceptance. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26` | Confirms predecessor archived `done` through closeout PR `#2003` at `4192ba9c`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent remains `todo` and depends on `AG-FE-000` plus `AG-BE-ID-003`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-FE-000` | Confirms Phase 0 frontend entry/build/audience dependency is archived `done`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms parent backend session dependency remains `blocked` waiting for `Claude`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | Confirms the new backend sidecar is in `review` after PR `#2005` merged; its key conclusion keeps `AG-BE-ID-003` blocked. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-XR-003` | Confirms AG-XR-003 is archived `done`; execute-plans PR `#63` remains separate follow-through evidence. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-XR-OPENAPI-002` | Confirms additive v1.2 OpenAPI/bundle work is archived `done`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-XR-OPENAPI-002-SIDECAR-ACCEPTANCE` | Confirms the v1.2 acceptance sidecar is archived `done` and support-only. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26.md` | Previous AG-FE-ID-001 approved support baseline. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26-REVIEW.md` | Reviewer-approved facts for the predecessor baseline. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14.md` | New backend sidecar; still says AG-BE-ID-003 blocked on type-contract decision. |
| `git log --oneline 0b0662079128d0e569e02598b99c9fb28a3d492f..origin/dev --decorate` | Shows PRs `#2003` (followup-26 closeout), `#2004` (AG-FE-DB-002 followup-17), `#2005` (AG-BE-ID-003 followup-14). |
| `git diff --name-status 0b0662079128d0e569e02598b99c9fb28a3d492f..origin/dev -- <checked handoff pathset>` | Shows only `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14.md` (added) and `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26.md` (modified for closeout); no Agora BFF runtime, OpenAPI/spec, manifest, execute-plans mirror, or canonical truth delta. |
| `gh pr view 63 --repo ajoe734/execute-plans` | Confirms execute-plans PR `#63` remains `OPEN`, `UNSTABLE`, head `e1cb9125`; `integration-gate` failed. |
| Execute-plans remote tree probes | Confirm parent frontend target files remain absent from checked `origin/main` and `origin/dev`. |
| `python3 -m pytest services/control-plane/bff/tests/test_agora_router.py integrations/openclaw/test_persona_agent_sync.py services/control-plane/bff/tests/test_agora_identity_scope.py` | `35 passed in 14.86s`. |
| `npm --prefix /home/lupin/code/execute-plans run test:contract` | `5 passed`; contract drift test suite passed. |
| `python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: `compatibility_status` not compatible, frontend runtime commit is placeholder, blocking reasons not empty. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-26 Closeout

Baseline: `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26` review record PR
`#1999` merged into `dev` at `0b0662079128d0e569e02598b99c9fb28a3d492f`.

| Change | What changed | Parent implication |
|---|---|---|
| Followup-26 closed | Archive records sidecar `done` at `4192ba9c`; Claude approved the support-only packet and owner closeout ran after PR `#2003` merged. | Treat followup-26 as accepted support evidence on `dev`. |
| AG-FE-DB-002 followup-17 | PR `#2004` merged dashboard-editor support only at `38341b12`. | No BFF route, OpenAPI servant-session decision, canonical contract change, or execute-plans source change. |
| AG-BE-ID-003 followup-14 | PR `#2005` merged new backend sidecar at `fd9693bb`; key conclusion keeps `AG-BE-ID-003` blocked. | Reinforces this packet's gating rule rather than unblocking sessions. |
| Parent `AG-FE-ID-001` | Still `todo`. | No parent implementation evidence exists to absorb or review. |
| Parent dependency `AG-FE-000` | Archived `done`. | Entry/build/audience split is available as dependency context, but parent still needs shell and strict clients. |
| Parent dependency `AG-BE-ID-003` | Still `blocked`, waiting for `Claude`. | Session UI and strict session clients remain gated on Claude's type-contract decision. |
| Execute-plans PR #63 | Still `OPEN` / `UNSTABLE`; `integration-gate` failed; last updated `2026-06-20T16:53:49Z`. | Parent must not claim dev deployment compatibility readiness from local shell behavior alone. |
| Execute-plans target files | `AgoraApp.tsx`, `identity.ts`, `servant.ts`, `src/entries/agora-main.tsx`, `vite.agora.config.ts`, and `agora.html` remain absent from both checked remote trees. `types.ts` still on `origin/dev` only. | Parent still needs to add these files or explicitly block on missing design/base authority. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover identity scope and servant policy context. | Runtime route, not a generated OpenAPI v1.1/v1.2 operation in the checked specs. | Parent may use it as narrow identity readiness. Keep the client strict and document runtime-only route status. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered capabilities. | Runtime route, not a generated OpenAPI v1.1/v1.2 operation in the checked specs. | Parent may use it for readiness/capability display; do not infer generated operation support. |
| `POST /bff/agora/servant/ensure` | Implemented in `services/control-plane/bff/agora/servant/router.py`; runtime requires `Idempotency-Key` and `X-Request-Id`; tests cover current 200 provisioning/reconcile, missing-header 422, and unauth 401. | Present in v1.1 and v1.2 OpenAPI as `ensureAgoraServant`; runtime returns current-200 with no body, while OpenAPI route expectations differ. | `servant.ts` should send both headers, parse current 200 `ServantProfile`, and map 401/403/422/503 explicitly. |
| `GET /bff/agora/servant` | No current servant sub-router handler was identified in checked runtime paths. | Present in v1.1 and v1.2 OpenAPI. | Do not make the shell depend on this route until runtime support lands or reviewer records a disposition. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler was identified in checked runtime paths. | Present in v1.1 and v1.2 OpenAPI. | Keep out of the parent UI until runtime support exists or reviewer records a disposition. |
| `POST /bff/agora/servant/sessions*` | Still no accepted BFF runtime implementation in checked paths; parent `AG-BE-ID-003` is blocked. | Present in v1.1 and v1.2 OpenAPI, but `ServantSessionCreateRequest` lacks a public session type field and rejects undeclared top-level fields. | Do not call these routes from the parent frontend until `AG-BE-ID-003` lands and the type decision is approved. |
| `GET/POST /bff/agora/sessions*` | Legacy routes live in `main.py`; create accepts `mode` or `sessionType` and defaults to `quick_ask`. | Not the servant-session facade. | Do not treat these routes as proof of `interactive`, `trainer`, or `research_task` readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface in `main.py`; close/stream semantics are ask-channel oriented. | Ownership remains separate from the servant-session facade unless explicitly reassigned. | Do not use for parent `interactive`, `trainer`, or `research_task` controls. |
| Strategy Workshop v1.2 routes/storage | v1.2 contract/storage/capability material is workshop/private-content oriented. | AG-XR-OPENAPI-002 is archived `done`, but remains additive v1.2 context. | Do not absorb it into the AG-FE-ID-001 identity/servant status shell unless parent scope is explicitly expanded and reviewed. |

Safe parent-shell facts are unchanged: user-private identity scope, filtered
capability readiness, successful servant profile ensure/reconcile through
`/ensure`, and no validated servant-session facade.

## 6. Frontend Surface To Hand Off

Remote probe source: `/home/lupin/code/execute-plans` after
`git fetch origin --prune`.

| Surface | Current remote-tree state | Required parent decision |
|---|---|---|
| `src/agora/AgoraApp.tsx` | Missing from both `origin/main` (`7b2f17c4`) and `origin/dev` (`7aa49172`). | Parent must add the shell or block for missing design/spec authority. |
| `src/lib/bff-v1/agora/identity.ts` | Missing from both `origin/main` and `origin/dev`. | Parent should add strict clients for `/me` and `/capabilities`; these are runtime routes, not generated OpenAPI operations. |
| `src/lib/bff-v1/agora/servant.ts` | Missing from both `origin/main` and `origin/dev`. | Parent should add a strict ensure client for `/servant/ensure`, including idempotency/request headers and typed failure mapping. |
| `src/lib/bff-v1/agora/types.ts` | Missing from `origin/main`; present on `origin/dev`. | Parent must confirm the actual frontend delivery branch before relying on generated Agora types. |
| `src/entries/agora-main.tsx` | Missing from both checked remote trees. | Parent must resolve frontend entry/build delivery-base truth before attaching a shell. |
| `vite.agora.config.ts` | Missing from both checked remote trees. | Parent must not assume an Agora-specific Vite entry is visible on checked remotes. |
| `agora.html` | Missing from both checked remote trees. | Parent must verify delivery base or a clean task worktree before depending on this entry. |
| `src/agora/pages/AskPersonas.tsx` | Present on both checked remote trees. | Ask/session UI must remain gated behind identity/servant readiness and the AG-BE-ID-003 session decision. |
| `src/lib/bff/agora.ts` | Present on both checked remote trees. | Not sufficient for parent acceptance; strict clients under `src/lib/bff-v1/agora/*` are still needed. |
| `/home/lupin/code/execute-plans` worktree | Stale local checkout; use remote tree checks or a clean frontend task worktree for implementation truth. | Do not use this local checkout as implementation ground truth. |

Parent shell and clients must not import or expose Management, capital pool,
broker order, live order, or RuntimeBinding controls.

## 7. Minimal Status-Shell Contract

If parent `AG-FE-ID-001` proceeds before `AG-BE-ID-003` clears, the safe
frontend shape remains:

```text
approved Agora entry
  -> AgoraApp.tsx or approved shell
     -> identity.getAgoraMe()
     -> identity.getAgoraCapabilities()
     -> servant.ensureAgoraServant({ idempotencyKey, requestId })
     -> current runtime 200 maps to servant_profile_ready
     -> Ask/session/command surfaces remain disabled or read-only
        while AG-BE-ID-003 is blocked
```

Required shell states:

| State | Trigger | UI/runtime rule |
|---|---|---|
| Auth blocked | Missing auth or `401` from readiness/ensure call. | Render blocked auth state; no servant/session controls. |
| Scope/audience blocked | `403`, wrong tenant, wrong audience, or missing Agora capability. | Render blocked scope state; no seed/mock retry. |
| Identity ready | `/me` and `/capabilities` succeed. | Show tenant/user predicate, granted capabilities, and servant policy facts. |
| Servant profile ready | `/servant/ensure` returns current runtime `200` with `ServantProfile`. | Show servant persona/status/policy; no broker, capital, RuntimeBinding, or order authority. |
| Servant ensure validation failed | Missing `Idempotency-Key` or `X-Request-Id`. | Treat as a client implementation defect; no mock retry. |
| OpenClaw sync degraded | Runtime returns `503` dependency unavailable during servant agent sync. | Show provisioning/reconcile failed state with no session controls. |
| Session facade unavailable | `AG-BE-ID-003` remains blocked. | Keep Ask/session/command surfaces disabled or read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured. | Render unavailable state; no silent mock fallback. |
| Compatibility follow-through pending | execute-plans PR `#63` remains open/unstable or frontend runtime commit is placeholder. | Do not claim deployment readiness from local shell behavior alone. |

`servant_policy.execution_authority = "none"` and prohibited authority facts
may be displayed as safety context. They must not become operator controls.

## 8. Operator Journey

### Current honest journey

```text
Operator opens the approved Agora entry
  -> frontend verifies Agora-scoped auth/audience
  -> frontend calls GET /bff/agora/me through a strict identity client
  -> BFF returns tenant_id, user_id, fail-closed read_predicate,
     Agora capabilities, and servant_policy
  -> frontend calls GET /bff/agora/capabilities through a strict identity client
  -> BFF returns filtered capability manifest and backend scope
  -> frontend calls POST /bff/agora/servant/ensure with Idempotency-Key
     and X-Request-Id
  -> BFF creates or reconciles one user-private agora_servant persona,
     syncs OpenClaw agent metadata, and returns current runtime 200
     ServantProfile envelope
  -> shell renders servant profile ready and no-authority policy facts
  -> Ask/session/command surfaces remain disabled or read-only because
     AG-BE-ID-003 is blocked
```

### Future session journey, still blocked

```text
AG-BE-ID-003 resolves the BFF session contract
  -> route family is frozen, preferably /bff/agora/servant/sessions
  -> approved public session_type/session_kind or deterministic derivation exists
  -> research_task maps to an approved OpenClaw skill/session kind
  -> runtime implements create/message/terminate/session-scoped stream
  -> message writes carry required audit fields
  -> OPENCLAW_UPSTREAM_DEGRADED or an approved equivalent is reachable and tested
  -> frontend adds strict session clients under src/lib/bff-v1/agora/*
  -> AskPersonas or replacement command UI is enabled only after readiness
     is proven
```

## 9. Parent Absorption Checklist

Codex should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Parent dependency disposition | Parent either waits for `AG-BE-ID-003`, or explicitly narrows completion to an identity plus servant-profile status shell while leaving sessions disabled. |
| Frontend base truth | Parent identifies the exact execute-plans branch/commit it is building from; do not assume local `/home/lupin/code/execute-plans` has current truth. |
| Identity route truth | Parent states `/me` and `/capabilities` are interim runtime routes, not generated OpenAPI operations. |
| Servant ensure truth | Parent proves `/servant/ensure` success and typed 401/403/422/503 failure handling where applicable. |
| Ensure contract/runtime mismatch | Parent explicitly notes current runtime accepts no body and returns 200 for create/reconcile, while OpenAPI expectations differ. |
| Type mirror truth | Parent verifies generated Agora frontend types on the actual delivery branch before reuse. |
| Servant session contract | Parent does not send undeclared `session_type` or `sessionType` to `ServantSessionCreateRequest`; it waits for approved schema or derivation. |
| Route family decision | Parent does not mix `/bff/agora/servant/sessions`, legacy `/bff/agora/sessions`, and `/bff/agora/ask/sessions` without explicit backend disposition. |
| Strategy Workshop separation | v1.2 workshop/private-content routes are not treated as AG-FE-ID-001 shell/session readiness. |
| Strict clients | `identity.ts` and `servant.ts` use strict live semantics, do not fall back to mock/seed data, and keep page components away from direct route fetches. |
| Bundle isolation | Agora shell tests or static checks prove no Management/runtime-binding/capital/broker/order code leaks into the app shell. |
| Compatibility honesty | Parent does not claim dev deployment readiness while PR `#63` is open/unstable or the manifest frontend runtime commit is a placeholder. |
| Tests | Parent adds focused frontend tests for identity success, auth/audience failure, strict no-fallback, servant ensure success/failure mapping, disabled session controls while `AG-BE-ID-003` is blocked, and no forbidden bundle strings. |

## 10. Verification Performed For This Sidecar

Commands and results:

| Command | Result |
|---|---|
| `git branch --show-current` | `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27`. |
| `git status --short` | Only the generated task brief and this packet file; no unrelated dirty files. |
| `git remote -v` | `origin` is `https://github.com/ajoe734/pantheon.git`. |
| `git fetch origin --prune` | Completed; `origin/dev` resolves to `fd9693bb`. |
| `git merge --no-edit origin/dev` | Fast-forward merged PRs `#2004` and `#2005` support-only deltas into task branch. |
| `git log --oneline 0b0662079128d0e569e02598b99c9fb28a3d492f..origin/dev --decorate` | Shows PR `#2003` (followup-26 closeout), PR `#2004` (AG-FE-DB-002 followup-17), PR `#2005` (AG-BE-ID-003 followup-14). |
| `git diff --name-status 0b0662079128d0e569e02598b99c9fb28a3d492f..origin/dev -- <checked handoff pathset>` | Shows only `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14.md` added and `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26.md` modified; no BFF runtime, OpenAPI/spec, manifest, execute-plans mirror, or canonical truth file. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27` | Active `in_progress`, owner `Claude`, reviewer `Codex`, support-only artifact path. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26` | Archived `done`; closeout PR `#2003` merged at `4192ba9c`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-FE-ID-001` | Parent `todo`; depends on `AG-FE-000` and `AG-BE-ID-003`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-FE-000` | Archived `done`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-ID-003` | Active `blocked`, waiting for `Claude`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | Active `review`; PR `#2005` merged at `fd9693bb`; key conclusion keeps `AG-BE-ID-003` blocked. |
| `gh pr view 63 --repo ajoe734/execute-plans --json number,state,mergeStateStatus,headRefOid,updatedAt` | PR `#63` is `OPEN`, `UNSTABLE`, head `e1cb9125`; last updated `2026-06-20T16:53:49Z`. |
| `git -C /home/lupin/code/execute-plans fetch origin --prune` | Completed. |
| `git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/main -- <frontend target files>` | Only `src/agora/pages/AskPersonas.tsx` and `src/lib/bff/agora.ts` present. |
| `git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev -- <frontend target files>` | Only `src/agora/pages/AskPersonas.tsx`, `src/lib/bff-v1/agora/types.ts`, and `src/lib/bff/agora.ts` present. |
| `python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Exit `1`; expected fail-closed: status not compatible, frontend runtime commit is placeholder, blocking reasons not empty. |
| `npm --prefix /home/lupin/code/execute-plans run test:contract` | `5 passed`. |
| `python3 -m pytest services/control-plane/bff/tests/test_agora_router.py integrations/openclaw/test_persona_agent_sync.py services/control-plane/bff/tests/test_agora_identity_scope.py` | `35 passed in 14.86s`. |

## 11. Reviewer Handoff

This packet is ready for review by `Codex`. The packet is support material only
and should be reviewed for accuracy of delta facts, task state, BFF ledger,
frontend surface state, and absorption checklist completeness.

The reviewer should confirm:
- The post-followup-26 dev delta is correctly characterised as sidecar/support-only.
- `AG-BE-ID-003` still blocked on Claude's type-contract decision.
- Execute-plans PR `#63` still open/unstable.
- Frontend target files still absent as stated.
- No canonical truth, BFF runtime, OpenAPI/spec, manifest, or execute-plans source was changed.

The approval does not implement or approve parent `AG-FE-ID-001`. Parent owner
`Claude` decides whether and how to absorb the packet into the eventual parent
implementation.

*Prepared by Claude for the `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27` support slice.*
