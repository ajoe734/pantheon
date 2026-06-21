# AG-FE-ID-001 Followup-19 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-19` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Date | `2026-06-21` |
| Status | `ready for Claude review` |
| Packet observation base | `6de042cd1a88c51b22dbf6275e0785f49a6e7998` |
| Execute-plans remotes checked | `origin/main` at `7b2f17c4dee8dcafe62c2295504df03aed0ae16e`; `origin/dev` at `7aa4917272212452fe5e4dc99bf2d76fe48eacfd` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, OpenClaw adapter code, or
execute-plans source files.

## 1. Purpose

This nineteenth followup refreshes the `AG-FE-ID-001` BFF/frontend handoff after
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-18` closed through PR #1931.

The material delta from followup-18 is narrow:

1. Followup-18 is archived `done`; PR #1931 merged into `dev` at
   `3a2caee4366eea1e5bc239ee860a9dc64bf69965`.
2. Current `origin/dev` has since advanced to `6de042cd`, including
   `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` through PR #1932 and an
   unrelated `AG-FE-DB-002` acceptance sidecar through PR #1933.
3. A focused diff from `3a2caee..origin/dev` over BFF/OpenAPI/spec/Agora
   frontend and `AG-FE-ID-001` support paths is empty. The only checked
   dependency-support delta is the AG-BE-ID-003 followup-9 support packet.
4. This followup branch is rebased onto `origin/dev` and adds only the
   followup-19 support packet in the checked handoff pathset.
5. Parent `AG-FE-ID-001` remains `todo`.
6. Parent dependency `AG-BE-ID-003` remains `blocked` waiting for Claude on the
   servant session type-contract decision.
7. `execute-plans` remote refs were refreshed. `origin/HEAD` still points to
   `origin/main`, and `origin/dev` still exists. The three parent target files
   are still absent from both checked remote trees: `AgoraApp.tsx`,
   `identity.ts`, and `servant.ts`.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex2`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-19` | `in_progress` | This packet prepares the support-only handoff to Claude. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-18` | archived `done`; PR #1931 / merge `3a2caee4` | Previous support packet, review, and closeout are durable on `dev`. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000`, `AG-BE-ID-003` | Parent implementation has not started in durable task state. |
| `AG-FE-000` | archived `done` | Separate Agora/Management entry/build/audience work is accepted, but prior scope/bundle contamination history remains useful review context. |
| `AG-BE-ID-002` | archived `done`; implementation PRs merged | `/bff/agora/servant/ensure` is the accepted servant ensure/provision/reconcile surface. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Servant session facade remains unavailable pending the session type contract decision. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` | `review`; PR #1932 / merge `7169f6b1` | Latest dependency-side support packet keeps the parent blocker unchanged; it is not runtime readiness. |
| `AG-XR-OPENAPI-001` | archived `done` | v1.1 OpenAPI and capability manifest remain present on `dev`. |
| `AG-XR-003` | `blocked`; owner `Codex`, reviewer `Claude2`, waiting for `Claude2` | Cross-repo compatibility/deployment gate is still unresolved. |

Dependency honesty rule: parent `AG-FE-ID-001` still depends on
`AG-BE-ID-003`. The frontend may show identity and servant-profile readiness,
but it must not claim interactive, trainer, or research session readiness.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_19.md` | This sidecar's support-only assignment. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-19` | Confirms active task state, owner, reviewer, artifact, and support-only acceptance. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-18` | Confirms predecessor archived `done` through PR #1931 / merge `3a2caee4`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent remains `todo` and still depends on `AG-BE-ID-003`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms session facade remains `blocked` on the missing session type contract decision. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` | Confirms the latest dependency-side support packet is in `review`, merged through PR #1932, and keeps AG-BE-ID-003 blocked. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure/provision/reconcile is archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms v1.1 OpenAPI and capability manifest work is archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003` | Confirms compatibility manifest/deployment gate remains blocked. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-18.md` | Previous AG-FE-ID-001 approved support baseline. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-18-REVIEW.md` | Claude's review record for followup-18. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-9.md` | Latest dependency-side session-gate support packet. |
| `git diff --name-only 3a2caee..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora execute-plans support/sidecars/AG-FE-ID-001` | Confirms no relevant runtime, contract, source, or AG-FE-ID-001 support delta after followup-18 closeout. |
| `git diff --name-only 3a2caee..origin/dev -- support/sidecars/AG-BE-ID-003` | Shows the only checked dependency-support delta is AG-BE-ID-003 followup-9. |
| `git diff --name-only 6de042cd..HEAD -- ...` | Confirms this branch adds only the followup-19 packet in the checked handoff pathset. |
| Target file probes against `/home/lupin/code/execute-plans` `origin/main` and `origin/dev` | Confirms the parent frontend target files are still absent from the refreshed frontend remote trees. |
| Focused BFF/OpenClaw pytest, schema bundle verify, and OpenAPI YAML load | Confirms the current BFF identity/servant evidence and frozen bundle remain green. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-18

| Change | What changed | Parent implication |
|---|---|---|
| FOLLOWUP-18 closed | Archived `done`; PR #1931 merged at `3a2caee4`. | Treat FOLLOWUP-18 as accepted support evidence on `dev`. |
| AG-BE-ID-003 followup-9 landed | PR #1932 merged at `7169f6b1`; the task is in `review`, not `done`. | Latest session-gate support evidence still keeps AG-BE-ID-003 blocked on the type-contract decision. |
| Unrelated dev sidecar landed | PR #1933 merged `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-9` at `6de042cd`. | No AG-FE-ID-001 BFF/frontend handoff implication in the checked pathset. |
| Pantheon relevant pathset | `3a2caee..origin/dev` over BFF/OpenAPI/execute-plans Agora and AG-FE-ID-001 support paths is empty; `6de042cd..HEAD` contains only this followup-19 support packet. | No Pantheon source or contract change supersedes FOLLOWUP-18 in this branch. |
| Execute-plans remote probe | `origin/main` at `7b2f17c4` and `origin/dev` at `7aa49172` both lack `src/agora/AgoraApp.tsx`, `src/lib/bff-v1/agora/identity.ts`, and `src/lib/bff-v1/agora/servant.ts`. | Parent still needs to add the requested shell/client files or open a blocker. |
| Execute-plans branch ambiguity | `origin/HEAD -> origin/main`, but `origin/dev` also exists and AG-FE-000 archive mentions a dev/default-branch closeout. | Parent must confirm the actual frontend delivery base before coding or reviewing. |
| Execute-plans local checkout | `/home/lupin/code/execute-plans` worktree is `main...origin/main [ahead 2, behind 467]`. | Do not rely on that local checkout as latest frontend truth; use remote tree or a clean task worktree for parent implementation. |
| AG-BE-ID-003 | Still `blocked`, waiting for Claude. | Session UI and session clients remain gated. |
| AG-XR-003 | Still `blocked`, waiting for Claude2. | Do not claim strict cross-repo deployment compatibility from this sidecar. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover envelope, tenant/user predicate, capabilities, and servant policy. | Not generated as an OpenAPI v1.1 operation. | Parent may use it as interim runtime route truth for identity readiness. Keep the client narrow and document runtime-only status. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered capability manifest and backend scope. | Same as `/me`: runtime route, not generated operation coverage. | Parent may use it for readiness/capability display. Do not claim generated operation support. |
| `POST /bff/agora/servant/ensure` | Implemented and archived through `AG-BE-ID-002`; runtime requires `Idempotency-Key` and `X-Request-Id`, derives user scope server-side, creates or reconciles one user-private `agora_servant`, syncs OpenClaw metadata, and returns current `200` profile envelopes in tests. | Present in v1.1 OpenAPI as `ensureAgoraServant`; OpenAPI declares required body and `200` or `201`, while runtime evidence observes no body and current `200`. | `servant.ts` should send both headers, parse current `200` `ServantProfile`, handle 401/403/422/503 explicitly, and record the body/status mismatch. |
| `GET /bff/agora/servant` | No current servant sub-router handler was identified in the checked runtime paths. | Present in v1.1 OpenAPI as `getAgoraServant`. | Do not make the parent shell depend on this read route until runtime support lands. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler was identified in the checked runtime paths. | Present in v1.1 OpenAPI. | Keep out of the parent UI until runtime support exists or reviewer records a disposition. |
| `POST /bff/agora/servant/sessions*` | Still no accepted BFF runtime implementation; parent `AG-BE-ID-003` is blocked before coding. | Present in v1.1 OpenAPI, but `ServantSessionCreateRequest` still lacks `session_type` and rejects undeclared top-level fields. | Do not call these routes from the parent frontend until `AG-BE-ID-003` lands and the contract decision is approved. |
| `GET/POST /bff/agora/sessions*` | Legacy routes live in `main.py`; create accepts `mode` or `sessionType` and defaults to `quick_ask`. | Not the v1.1 servant-session facade. | Do not treat these routes as proof of `interactive`, `trainer`, or `research_task` readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface in `main.py`; close/stream semantics are ask-channel oriented. | Ownership remains separate from the v1.1 servant-session facade unless explicitly reassigned. | Do not use for parent `interactive`, `trainer`, or `research_task` controls. |
| Dashboard recipe/widget routes | Runtime/dashboard work has advanced separately. | Present in v1.1 OpenAPI and generated mirrors. | Dashboard readiness remains separate from identity/servant/session shell readiness. |

The safe parent-shell facts are unchanged: user-private identity scope, filtered
capability readiness, successful servant profile ensure/reconcile through
`/ensure`, and no validated servant-session facade.

Attention item: `services/control-plane/bff/tests/test_agora_router.py` still
has a top-level comment saying `/servant/ensure` returns HTTP 501. The concrete
tests now assert provisioning, reconcile, required-header `422`, and unauth
`401` behavior. Reviewers should rely on the concrete tests and route code, not
that stale header comment.

## 6. Session Gate Status

`AG-BE-ID-003` remains blocked on the same contract decision recorded in the
followup-18 and AG-BE-ID-003 followup-9 support packets.

| Gate | Current blocker | Frontend rule |
|---|---|---|
| Session type field | `ServantSessionCreateRequest` allows only `intent`, `strategy_ref`, and `metadata`; `additionalProperties: false`. | Strict FE clients must not send undeclared top-level fields. |
| Public derivation rule | No reviewer-approved rule says how BFF derives `interactive`, `trainer`, or `research_task` from route/context. | FE must wait for explicit schema or derivation authority. |
| Research task mapping | Checked evidence names `interactive` and `trainer`; `research_task` skill/session ownership remains unresolved. | Research-task controls stay disabled. |
| Runtime route family | v1.1 OpenAPI lists `/bff/agora/servant/sessions*`, but BFF runtime implementation is not accepted. | Do not wire live create/message/terminate/stream clients. |
| Degraded error | `OPENCLAW_UPSTREAM_DEGRADED` was not found in the checked BFF runtime paths for this session facade. | Do not display a tested session degradation state yet. |
| Cross-repo compatibility | `AG-XR-003` remains blocked. | Strict v1.1 live release claims stay gated. |

If the parent later approves an explicit public type field, the expected FE
client shape remains:

```ts
type ServantSessionType = "interactive" | "trainer" | "research_task";

createServantSession(input: {
  sessionType: ServantSessionType;
  intent: string;
  strategyRef?: string;
  metadata?: Record<string, unknown>;
}): Promise<ServantSessionEnvelope>;
```

The actual wire field must match the reviewer-approved OpenAPI/schema field.
This is a handoff expectation, not a claim that the current schema is ready.

## 7. Frontend Surface To Hand Off

Remote probe source: `/home/lupin/code/execute-plans` after
`git fetch origin --prune`, checking `origin/main` at `7b2f17c4` and
`origin/dev` at `7aa49172`.

| Surface | Current remote-tree state | Required parent decision |
|---|---|---|
| `src/agora/AgoraApp.tsx` | Missing from both `origin/main` and `origin/dev`. | Parent must add the shell or block for missing design/spec authority. |
| `src/lib/bff-v1/agora/identity.ts` | Missing from both `origin/main` and `origin/dev`. | Parent should add strict clients for `/me` and `/capabilities`; these are runtime routes, not generated OpenAPI operations. |
| `src/lib/bff-v1/agora/servant.ts` | Missing from both `origin/main` and `origin/dev`. | Parent should add a strict ensure client for `/servant/ensure`, including idempotency/request headers and typed failure mapping. |
| `src/lib/bff-v1/agora/types.ts` | Missing from `origin/main`; present on `origin/dev`. | Parent must confirm the delivery branch and `AG-XR-003` disposition before relying on generated Agora types. |
| `src/entries/agora-main.tsx` | Missing from both checked remote trees, despite `AG-FE-000` archive saying entry/build work landed in its task branch. | Parent must resolve frontend delivery-base truth before claiming the shell can attach to an existing Agora entry. |
| `vite.agora.config.ts` | Missing from both checked remote trees. | Parent must not assume AG-FE-000 entry/build artifacts are visible on the checked frontend remotes. |
| `src/agora/pages/AskPersonas.tsx` | Present on both checked remote trees. | Ask/session UI must remain gated behind identity/servant readiness and the AG-BE-ID-003 session decision. |
| `src/lib/bff/agora.ts` | Present on both checked remote trees. | Not sufficient for parent acceptance; strict clients under `src/lib/bff-v1/agora/*` are still needed. |
| `/home/lupin/code/execute-plans` worktree | Local branch `main` is ahead 2 and behind 467. | Use a clean frontend task worktree or remote tree checks for implementation/review. |

Parent shell and clients must not import or expose Management, capital pool,
broker order, live order, or RuntimeBinding controls.

## 8. Minimal Status-Shell Contract

If parent `AG-FE-ID-001` proceeds before `AG-BE-ID-003` clears, the safe
frontend shape remains:

```text
agora-main.tsx or approved Agora entry
  -> AgoraApp.tsx or approved shell
     -> identity.getAgoraMe()
     -> identity.getAgoraCapabilities()
     -> servant.ensureAgoraServant({ idempotencyKey, requestId })
     -> current 200 maps to servant_profile_ready
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
| OpenClaw sync degraded | Runtime returns a dependency failure during servant agent sync. | Show provisioning/reconcile failed state with no session controls. |
| Session facade unavailable | `AG-BE-ID-003` remains blocked. | Keep Ask/session/command surfaces disabled or read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured. | Render unavailable state; no silent mock fallback. |

`servant_policy.execution_authority = "none"` and
`prohibited_authority = ["runtime_binding", "broker_order", "capital_binding"]`
may be displayed as safety facts. They must not become operator controls.

## 9. Operator Journey

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

## 10. Parent Absorption Checklist

Claude should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Parent dependency disposition | Parent either waits for `AG-BE-ID-003`, or explicitly narrows completion to an identity plus servant-profile status shell while leaving sessions disabled. |
| Frontend base truth | Parent identifies the exact execute-plans branch/commit it is building from; do not assume local `/home/lupin/code/execute-plans` `main`, `origin/main`, or `origin/dev` has all AG-FE-000 artifacts without checking. |
| Identity route truth | Parent states `/me` and `/capabilities` are interim runtime routes, not generated OpenAPI operations. |
| Servant ensure truth | Parent proves `/servant/ensure` success and typed 401/403/422/503 failure handling where applicable. |
| Ensure contract/runtime mismatch | Parent explicitly notes current runtime accepts no body and returns 200 for create/reconcile, while OpenAPI declares a required body and 201 new-create response. |
| Type mirror truth | Parent verifies generated Agora frontend types before reuse; current remote probe finds `types.ts` on `origin/dev` but not `origin/main`. |
| Servant session contract | Parent does not send undeclared `session_type` or `sessionType` to `ServantSessionCreateRequest`; it waits for approved schema or derivation. |
| Route family decision | Parent does not mix `/bff/agora/servant/sessions`, legacy `/bff/agora/sessions`, and `/bff/agora/ask/sessions` without explicit backend disposition. |
| Research task mapping | Parent does not show or call research-task sessions until the OpenClaw skill/session mapping is frozen. |
| Legacy session gap | Parent does not treat `main.py` `/bff/agora/sessions*` as canonical servant-session readiness while it defaults to `quick_ask`. |
| Ask session split | Parent does not use `/bff/agora/ask/sessions*` for `interactive`, `trainer`, or `research_task` controls without explicit backend ownership disposition. |
| Strict clients | `identity.ts` and `servant.ts` use strict live semantics, do not fall back to mock/seed data, and keep page components away from direct route fetches. |
| No broad path import | Agora shell does not import Management, capital pool, broker, order, RuntimeBinding, or dashboard-only control surfaces. |
| Dashboard separation | Dashboard recipe/widget routes remain outside the minimal identity/servant status shell unless the parent scope is explicitly expanded and reviewed. |
| Bundle isolation | Parent tests or static checks prove the Agora bundle does not pull Management/runtime-binding code into the app shell. |
| Tests | Parent adds focused frontend tests for identity success, auth/audience failure, strict no-fallback, servant ensure success/failure mapping, and disabled session controls while `AG-BE-ID-003` is blocked. |

## 11. Suggested Verification For Parent

Backend readiness checks:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py integrations/openclaw/test_persona_agent_sync.py services/control-plane/bff/tests/test_agora_identity_scope.py
python3 scripts/agora_schema_bundle.py --verify
python3 -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('services/control-plane/openapi/agora_v1_1.openapi.yaml').read_text())"
```

Frontend remote probes:

```bash
git -C /home/lupin/code/execute-plans fetch origin --prune
git -C /home/lupin/code/execute-plans symbolic-ref refs/remotes/origin/HEAD
git -C /home/lupin/code/execute-plans rev-parse origin/main
git -C /home/lupin/code/execute-plans rev-parse origin/dev
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/main -- src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/entries/agora-main.tsx vite.agora.config.ts src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev -- src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/entries/agora-main.tsx vite.agora.config.ts src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts
```

Static review spot checks for the parent implementation:

```bash
rg -n "fetch\\(|/bff/agora" /path/to/execute-plans/src/agora /path/to/execute-plans/src/lib/bff-v1/agora
rg -n "management|RuntimeBinding|capital|broker|order" /path/to/execute-plans/src/agora /path/to/execute-plans/src/lib/bff-v1/agora
```

## 12. Verification Performed For This Sidecar

Commands and results:

| Command | Result |
|---|---|
| `git rev-parse origin/dev` | `6de042cd1a88c51b22dbf6275e0785f49a6e7998` |
| `git diff --name-only 3a2caee4366eea1e5bc239ee860a9dc64bf69965..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora execute-plans support/sidecars/AG-FE-ID-001` | Empty output. |
| `git diff --name-only 3a2caee4366eea1e5bc239ee860a9dc64bf69965..origin/dev -- support/sidecars/AG-BE-ID-003` | `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-9.md` |
| `git diff --name-only 6de042cd1a88c51b22dbf6275e0785f49a6e7998..HEAD -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora execute-plans support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003` | `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-19.md` |
| `git -C /home/lupin/code/execute-plans fetch origin --prune` | Completed successfully. |
| `git -C /home/lupin/code/execute-plans symbolic-ref refs/remotes/origin/HEAD` | `refs/remotes/origin/main` |
| `git -C /home/lupin/code/execute-plans rev-parse origin/main` | `7b2f17c4dee8dcafe62c2295504df03aed0ae16e` |
| `git -C /home/lupin/code/execute-plans rev-parse origin/dev` | `7aa4917272212452fe5e4dc99bf2d76fe48eacfd` |
| `git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/main -- ...` | Only `src/agora/pages/AskPersonas.tsx` and `src/lib/bff/agora.ts` were present from the probed list. |
| `git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev -- ...` | Only `src/agora/pages/AskPersonas.tsx`, `src/lib/bff-v1/agora/types.ts`, and `src/lib/bff/agora.ts` were present from the probed list. |
| `python3 -m pytest services/control-plane/bff/tests/test_agora_router.py integrations/openclaw/test_persona_agent_sync.py services/control-plane/bff/tests/test_agora_identity_scope.py` | `35 passed in 29.47s` |
| `python3 scripts/agora_schema_bundle.py --verify` | OK for frozen Agora schemas, capability manifest, and `openapi/agora_v1.openapi.yaml`. |
| `python3 -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('services/control-plane/openapi/agora_v1_1.openapi.yaml').read_text())"` | Passed with no output. |

Note: the verifier path for this checkout is `scripts/agora_schema_bundle.py`.

## 13. Reviewer Handoff

Claude review should focus on whether this packet remains support-only and
whether the narrow delta from followup-18 is correctly represented:

1. Followup-18 closed through PR #1931 at `3a2caee4`.
2. Current dev base is `6de042cd`; the only related support delta since
   followup-18 is AG-BE-ID-003 followup-9 through PR #1932, which keeps the
   session blocker unchanged.
3. No relevant Pantheon runtime, OpenAPI, Agora spec, execute-plans source, or
   AG-FE-ID-001 support delta exists after followup-18 before this packet.
4. Parent `AG-FE-ID-001` remains `todo`.
5. `AG-BE-ID-003` and `AG-XR-003` remain blocked.
6. Execute-plans target files `AgoraApp.tsx`, `identity.ts`, and `servant.ts`
   remain absent from both checked frontend remote trees.
7. The packet does not change canonical truth, BFF runtime code, OpenAPI,
   capability manifests, governance, or frontend source.
