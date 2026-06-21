# AG-FE-ID-001 Followup-30 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude2` |
| Date | `2026-06-21` |
| Status | `review handoff prepared` |
| Current dev base | `2c797ec065ba1f676ea5b00905790307a1cbf78a` |
| Previous packet closeout | Followup-29 archived `done`; packet `ff416b5f`, review record `b216e80c`, closeout durable on `dev` through PRs `#2019` and `#2045`; done delivery target `ccc70fa1` |
| Parent implementation PR | execute-plans PR `#66`, `OPEN` / `UNSTABLE`, head `de7834b8c33d39942e37f0fb8d4511726d828ad8`, updated `2026-06-21T11:34:55Z` |
| Legacy compatibility PR | execute-plans PR `#63`, still `OPEN` / `UNSTABLE`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`, updated `2026-06-20T16:53:49Z` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-FE-ID-001` BFF/frontend handoff after
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-29` closed.

The material update is larger than followup-29:

1. `AG-BE-ID-003` is now archived `done`. The servant-session BFF facade is no
   longer merely a pending dependency; focused BFF tests verify create, detail,
   message, terminate, stream, audit, and OpenClaw degradation behavior.
2. Parent `AG-FE-ID-001` has an execute-plans implementation PR (`#66`) with
   the requested shell/client files on the PR branch, but the parent task remains
   `blocked` because the aggregate release gate is red on repo-wide issues.
3. execute-plans PR `#66` has Agora-specific evidence passing, including F13
   Agora. Its blocker is not the Agora shell diff itself; it is the aggregate
   integration gate.
4. Pantheon `origin/dev` advanced from followup-29 closeout base `ccc70fa1` to
   `2c797ec0`. The checked pathset shows no `services/control-plane/bff/agora/*`
   changes in that delta. New Agora material is v1.3/v4 design and spec
   addition work, plus separate strategy-workshop and management-stream changes.
5. execute-plans `origin/dev` still does not contain the new parent shell/client
   files. They exist on PR `#66` only.
6. Compatibility PR `#63` is still open/unstable with unchanged head and
   timestamp. Do not read it as a deployment-readiness signal.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30` | active `in_progress`; owner `Codex`, reviewer `Claude2` | This packet is the support-only artifact for review. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-29` | archived `done`; packet `ff416b5f`, review `b216e80c`, closeout durable on `dev` | Previous support packet is the baseline. |
| `AG-FE-ID-001` | active `blocked`; owner `Claude`, reviewer `Codex`, waiting for `Gemini` | Parent PR `#66` is review-approved for Agora-specific work but blocked by aggregate execute-plans release gate failures outside the AG-FE-ID-001 slice. |
| `AG-FE-000` | archived `done` | Entry/build/audience split remains accepted dependency context. |
| `AG-BE-ID-003` | archived `done` | Servant-session backend facade landed; frontend may now target `/bff/agora/servant/sessions*` only through strict, spec-aligned clients. |

Dependency honesty rule: `AG-FE-ID-001` no longer needs to say servant sessions
are blocked by `AG-BE-ID-003`, but it still must not claim frontend session UI
or dev deployment readiness until PR `#66` merges and the aggregate gate clears
or is explicitly dispositioned.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_30.md` | This sidecar's support-only assignment. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30` | Confirms active owner/reviewer/status and support-only artifact path. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-29` | Confirms predecessor archived `done`, packet/review commits, and closeout context. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001` | Confirms parent `blocked`, waiting for `Gemini`, with PR `#66` aggregate-gate blocker. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-000` | Confirms Phase 0 frontend dependency is archived `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-ID-003` | Confirms servant-session backend dependency is archived `done`. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-29.md` | Previous AG-FE-ID-001 support baseline. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-29-REVIEW.md` | Reviewer-approved corrections for followup-29. |
| `git log --oneline ccc70fa1..origin/dev --decorate` | Shows Pantheon dev delta through PR `#2080`. |
| `git diff --name-status ccc70fa1..origin/dev -- <checked handoff pathset>` | Shows v1.3/v4 design/spec additions and management stream changes, but no `services/control-plane/bff/agora/*` runtime delta. |
| `rg -n "@router\\.(get|post|delete)|sessions|ensure" services/control-plane/bff/agora/servant/router.py services/control-plane/bff/agora/router.py` | Confirms active runtime route families for identity, ensure, and servant sessions. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | Confirms parent PR `#66` is open/unstable at head `de7834b8`. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | Confirms the only GitHub check surfaced is `integration-gate` failing. |
| execute-plans remote tree probes | Confirm parent shell/client files exist on `origin/task/AG-FE-ID-001` but not `origin/dev`. |
| `gh api repos/ajoe734/execute-plans/issues/66/comments ...` | Provides clean release-gate summary for PR `#66`: F13 Agora pass, aggregate failures outside the narrow shell diff. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | Confirms legacy compatibility PR remains open/unstable with unchanged head/timestamp. |
| `python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py` | `39 passed in 19.16s`. |
| `python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: not compatible, placeholder frontend runtime commit, blocking reasons non-empty. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-29 Closeout

Baseline: followup-29 closeout delivery target `ccc70fa1`.

| Change | What changed | Parent implication |
|---|---|---|
| Pantheon dev advanced | `origin/dev` moved from `ccc70fa1` to `2c797ec0`. | Rebase/compare parent support facts against `2c797ec0`, not the older followup-29 packet base. |
| No Agora BFF runtime delta after `ccc70fa1` | `git diff --name-status ccc70fa1..origin/dev -- services/control-plane/bff/agora` returns no output. | Identity, capability, ensure, and servant-session route behavior is unchanged since followup-29 closeout. |
| Management stream control | `services/control-plane/bff/main.py` changed around `/bff/management/nl/ask/stream`: stream timeout helper, SSE frame helper, control-command path, idempotency headers. | Management-only. No direct AG-FE-ID-001 Agora shell/servant implication. |
| v1.3/v4 design and spec additions | `design-closure-round2/*`, `services/control-plane/openapi/agora_v1_3.openapi.yaml`, and `services/control-plane/specs/agora/v4/*` were added. | Parent must not silently absorb v1.3 workshop/trading-room scope into the Phase 1 status shell unless the owning design/review tasks explicitly route that work. |
| Strategy spec/workshop support | `AG-BE-SW-002` added patching/version compare/workshop projection modules under `services/research/strategy_spec/*`. | Strategy workshop backend evolution only. Do not treat as identity/servant readiness or shell acceptance. |
| `AG-BE-ID-003` state | Archived `done`; implementation PR `#2025` merged at `aeceba68`, closeout PR `#2029` merged at `8049242d`. | Backend session facade is available for frontend follow-through, but frontend still needs strict clients and UI acceptance before enabling session controls. |
| `AG-FE-ID-001` state | Active `blocked`; PR `#66` open/unstable; review notes say Agora-specific checks pass but aggregate release gate fails. | Parent should stay blocked until gate clears or an explicit exception is recorded by the right owner. |
| execute-plans PR `#66` | Adds `src/agora/AgoraApp.tsx`, `src/lib/bff-v1/agora/identity.ts`, `src/lib/bff-v1/agora/servant.ts`, and focused tests. | Parent target files exist on the PR branch only, not on execute-plans `origin/dev`. |
| execute-plans PR `#63` | Still `OPEN` / `UNSTABLE`; head `e1cb9125`; updated `2026-06-20T16:53:49Z`. | No improvement in legacy compatibility signal. Continue to treat as unresolved follow-through risk. |
| Compatibility manifest gate | Still fail-closed in Pantheon with placeholder frontend runtime commit. | Parent cannot claim dev deployment compatibility from local or PR behavior alone. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Frontend handoff rule |
|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover identity scope and servant policy context. | Parent may use for identity readiness. Keep strict live semantics and do not infer servant/session success from identity alone. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered capabilities. | Parent may use for readiness/capability display. Keep route status distinct from generated OpenAPI operation status. |
| `POST /bff/agora/servant/ensure` | Implemented in `services/control-plane/bff/agora/servant/router.py`; requires `Idempotency-Key` and `X-Request-Id`; tests cover successful profile readiness and auth/header errors. | `servant.ts` should send required headers, parse the `ServantProfile` envelope, and map 401/403/422/503 explicitly. |
| `POST /bff/agora/servant/sessions` | Implemented by `AG-BE-ID-003`; tests cover default `interactive`, explicit `trainer` and `research_task`, unknown type rejection, audit fields, no-authority context, and 201 create. | Frontend may target this only with strict v1.2/v1.3-aligned servant-session clients. Do not use legacy `/bff/agora/sessions` for these modes. |
| `GET /bff/agora/servant/sessions/{session_id}` | Implemented; tests cover detail response and six audit fields. | Safe as the servant-session detail route after create. |
| `POST /bff/agora/servant/sessions/{session_id}/messages` | Implemented; tests cover message path and OpenClaw provider degradation. | Client must map `OPENCLAW_UPSTREAM_DEGRADED` or approved equivalent without fabricating success. |
| `POST /bff/agora/servant/sessions/{session_id}/terminate` | Implemented; tests cover terminate/cancel path and audit. | Use only for servant sessions created through the servant facade. |
| `GET /bff/agora/servant/sessions/{session_id}/stream` | Implemented; tests cover SSE stream events. | Can be used for session-scoped stream once frontend session UI is in scope. |
| `GET /bff/agora/servant` | No current servant sub-router handler was identified in checked runtime route grep. | Do not make the shell depend on this route. Any exported frontend helper for it must remain unused or be removed before a claim of route support. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler was identified in checked runtime route grep. | Keep out of the parent UI until runtime support exists or reviewer records a disposition. |
| `GET/POST /bff/agora/sessions*` | Legacy routes still live outside the servant facade. | Do not treat as proof of `interactive`, `trainer`, or `research_task` servant-session readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface remains separate from the servant facade. | Do not use for parent servant-session controls unless explicitly reassigned. |
| Strategy Workshop v1.3/v4 material | New specs and strategy-spec modules are workshop/trading-room evolution. | Do not absorb into the `AG-FE-ID-001` status shell unless parent scope is explicitly expanded and reviewed. |
| Management nl/ask stream | Changed in `main.py`, management-only. | No AG-FE-ID-001 implication. |

Safe parent-shell facts now are: user-private identity scope, filtered
capability readiness, successful servant profile ensure/reconcile through
`/servant/ensure`, and available servant-session backend routes. Frontend
session controls still require PR-level client/UI evidence before they are
operator-ready.

## 6. Frontend Surface To Hand Off

Remote probe source: `/home/lupin/code/execute-plans` after
`git fetch origin --prune`.

| Surface | `origin/dev` (`c357688c`) | PR `#66` head (`de7834b8`) | Handoff implication |
|---|---|---|---|
| `src/agora/AgoraApp.tsx` | Missing | Added | Parent shell exists only on the PR branch. |
| `src/lib/bff-v1/agora/identity.ts` | Missing | Added | PR branch has strict identity client for `/me` and `/capabilities`; also contains legacy `/bff/agora/sessions` helpers that should not be treated as servant-session facade support. |
| `src/lib/bff-v1/agora/identity.test.ts` | Missing | Added | Focused identity tests are part of PR `#66`. |
| `src/lib/bff-v1/agora/servant.ts` | Missing | Added | PR branch has `agoraServantClient.ensure()` using `bffFetch`/live status. It also exports direct-fetch helpers for `GET /servant` and `POST /ensure`; do not claim `GET /servant` runtime support. |
| `src/lib/bff-v1/agora/servant.test.ts` | Missing | Added | Focused servant tests are part of PR `#66`. |
| `src/lib/bff-v1/agora/types.ts` | Present | Present | Still needs contract-drift discipline; execute-plans gate currently reports repo-wide contract drift failures. |
| `src/entries/agora-main.tsx` | Not listed in the checked tree probe | Not changed by PR `#66` | Entry/build truth remains inherited from `AG-FE-000`; parent PR is not changing the entry file. |
| `vite.agora.config.ts` | Not listed in the checked tree probe | Not changed by PR `#66` | Build config truth remains inherited from `AG-FE-000`. |
| `agora.html` | Not listed in the checked tree probe | Not changed by PR `#66` | HTML entry truth remains inherited from `AG-FE-000`. |
| `src/agora/pages/AskPersonas.tsx` | Present | Present | Existing ask UI remains separate from the new status shell. |
| `src/lib/bff/agora.ts` | Present | Present | Legacy helper is not sufficient for parent acceptance. |

Observed PR `#66` shell behavior from remote source inspection:

```text
AgoraApp
  -> agoraIdentityClient.getMe()
  -> agoraIdentityClient.getCapabilities()
  -> agoraServantClient.ensure()
  -> render servant status bar and three IA tab skeletons
  -> no direct fetch in AgoraApp.tsx
```

This supports the parent status shell, but it is not merged into `origin/dev`
and it is not a deployment-ready artifact while the aggregate gate remains red.

## 7. Execute-Plans PR #66 Gate State

`gh pr checks 66 --repo ajoe734/execute-plans` reports:

| Check | State |
|---|---|
| `integration-gate` | `fail`; run `27902747928`, job `82565909429` |

The PR release-gate comment records:

| Gate | Result | Owner | Notes |
|---|---|---|---|
| Gate 0 Preconditions | `PASS` | - | frontend SHA and BFF SHA recorded; intended BFF URL present. |
| Gate 1 Static / Build / Unit | `FAIL` | Gemini / Codex | `npm run lint` fails; `npm run test:contract` fails. `npm run test` and `npm run build` pass. |
| Gate 2 Contract Drift | `FAIL` | Codex | Six repo-wide drift checks fail. |
| Gate 3 BFF Route Probes | `WARN` | Codex | Route probes largely pass; dry-run create endpoints not explicitly exercised. |
| Gate 4 Browser Frontend E2E | `PASS` | - | hosted browser probe passes with intended BFF URL and no BFF failures. |
| Gate 5 Playwright User Flows | `FAIL` | Codex | F05 Sentinel fails; F13 Agora passes with 3 runnable specs. |
| Gate 6 A11y / Perf | `FAIL` | Codex2 | Performance budget and SSE rerender checks fail. |
| Gate 7 Release Decision | `FAIL` | Codex | Aggregate decision: 11 failing or missing checks. |

Parent status correctly records this as an aggregate release-gate blocker, not
as a reason to reopen the reviewed Agora-specific shell/client behavior.

## 8. Minimal Current Operator Journey

Current honest status-shell journey, assuming PR `#66` or equivalent frontend
code is used:

```text
Operator opens the approved Agora entry
  -> frontend verifies Agora-scoped auth/audience
  -> frontend calls GET /bff/agora/me through the strict identity client
  -> BFF returns tenant/user predicate, capabilities, and servant policy
  -> frontend calls GET /bff/agora/capabilities
  -> frontend calls POST /bff/agora/servant/ensure with required headers
  -> BFF returns a user-private agora_servant ServantProfile envelope
  -> shell renders servant status and no-authority policy facts
  -> session/command UI remains skeleton or disabled unless strict
     /bff/agora/servant/sessions* clients and UI acceptance are in scope
```

Backend session journey now available for future frontend follow-through:

```text
frontend creates a servant session with POST /bff/agora/servant/sessions
  -> session_type is interactive by default, or trainer/research_task when supplied
  -> BFF sends approved context_bundle to OpenClaw with audit fields
  -> frontend sends messages through POST /sessions/{id}/messages
  -> stream reads use GET /sessions/{id}/stream
  -> terminate uses POST /sessions/{id}/terminate
  -> OpenClaw degradation maps to OPENCLAW_UPSTREAM_DEGRADED or equivalent error
```

The frontend still must not expose Management, capital pool, broker order,
RuntimeBinding, or live order controls through the Agora shell.

## 9. Parent Absorption Checklist

Claude should not absorb this sidecar into parent completion unless the parent
evidence answers these checks:

| Check | Required evidence |
|---|---|
| Parent PR status | PR `#66` is merged into execute-plans `dev`, or parent remains blocked with the aggregate-gate blocker recorded. |
| Backend dependency | `AG-BE-ID-003` is treated as done, and frontend session work uses `/bff/agora/servant/sessions*`, not legacy sessions. |
| Identity route truth | `/me` and `/capabilities` are strict live calls; failures produce blocked states with no seed/mock fallback. |
| Servant ensure truth | `/servant/ensure` sends required idempotency/request headers and maps 401/403/422/503 without fabricating success. |
| Unsupported servant profile route | Parent does not claim `GET /bff/agora/servant` is runtime-supported unless backend adds it. |
| Session route family | Parent does not mix `/bff/agora/servant/sessions`, legacy `/bff/agora/sessions`, and `/bff/agora/ask/sessions` without explicit backend disposition. |
| Session frontend scope | If session controls are enabled, tests cover create/detail/message/stream/terminate plus degradation and audit semantics. |
| v1.3/v4 separation | New workshop/trading-room spec material is not silently absorbed into this Phase 1 status shell. |
| Strict clients | Page components avoid direct route fetches; BFF clients preserve strict live semantics. |
| Bundle isolation | Agora shell tests or static checks prove no Management/runtime-binding/capital/broker/order code leaks into the app shell. |
| Compatibility honesty | Parent does not claim dev deployment readiness while PR `#66` is unmerged, PR `#63` remains open/unstable, or the compatibility manifest has placeholder frontend runtime commit. |
| Gate ownership | Aggregate gate failures are left with their recorded owners (`Gemini`, `Codex`, `Codex2`) rather than being buried in AG-FE-ID-001 closeout. |

## 10. Verification Performed For This Sidecar

Commands and results:

| Command | Result |
|---|---|
| `git status -sb && git branch --show-current && git remote -v` | Started on expected task branch; origin is `https://github.com/ajoe734/pantheon.git`; only generated followup-30 task brief was untracked. |
| `git fetch origin --prune` | Completed. |
| `git merge --ff-only origin/dev` | Fast-forwarded task branch to `2c797ec0`; no task-owned edits existed yet. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30` | Active `in_progress`, owner `Codex`, reviewer `Claude2`, support artifact path. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-29` | Archived `done`; packet/review/closeout durable on `dev`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001` | Parent active `blocked`, waiting for `Gemini`; PR `#66` gate blocker recorded. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-000` | Archived `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-ID-003` | Archived `done`; servant-session facade accepted. |
| `git log --oneline ccc70fa1..origin/dev --decorate --max-count=140` | Shows Pantheon dev delta through PR `#2080`. |
| `git diff --name-status ccc70fa1..origin/dev -- services/control-plane/bff/agora` | No output; no Agora BFF runtime delta since followup-29 closeout. |
| `git diff --name-status ccc70fa1..origin/dev -- services/control-plane/openapi services/control-plane/specs/agora` | Adds `agora_v1_3.openapi.yaml`, `bundle_index.v1_3.json`, and v4 schema files. |
| `git diff ccc70fa1..origin/dev -- services/control-plane/bff/main.py` | Management nl/ask stream-control changes only. |
| `git -C /home/lupin/code/execute-plans fetch origin --prune` | Completed. |
| `gh pr view 66 --repo ajoe734/execute-plans --json number,state,mergeStateStatus,headRefName,baseRefName,headRefOid,updatedAt,url,reviewDecision,isDraft` | PR `#66` `OPEN` / `UNSTABLE`, base `dev`, head `task/AG-FE-ID-001`, commit `de7834b8`, not draft. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | `integration-gate` failed. |
| `gh api repos/ajoe734/execute-plans/issues/66/comments ...` | Release-gate summary confirms F13 Agora pass and aggregate failures listed above. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | PR `#63` still `OPEN` / `UNSTABLE`, head `e1cb9125`, timestamp unchanged. |
| `git -C /home/lupin/code/execute-plans diff --name-status origin/dev..origin/task/AG-FE-ID-001 -- <target pathset>` | Adds only `AgoraApp.tsx`, `identity.ts`, `servant.ts`, and focused identity/servant tests. |
| `git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev -- <target pathset>` | `origin/dev` has `AskPersonas.tsx`, `types.ts`, and legacy `agora.ts`; new shell/client files are absent. |
| `git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/task/AG-FE-ID-001 -- <target pathset>` | PR branch has `AgoraApp.tsx`, `identity.ts`, `servant.ts`, `types.ts`, `AskPersonas.tsx`, and legacy `agora.ts`. |
| `python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py` | `39 passed in 19.16s`. |
| `python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Exit `1`; expected fail-closed: incompatible status, placeholder frontend runtime commit, blocking reasons non-empty. |

## 11. Reviewer Handoff

This packet is ready for review by `Claude2`. Review should check:

- The state delta is accurate: `AG-BE-ID-003` is now done, parent
  `AG-FE-ID-001` is blocked on execute-plans aggregate gate, not on Agora
  backend readiness.
- The BFF ledger correctly distinguishes available servant-session facade routes
  from unsupported `GET /bff/agora/servant`, legacy sessions, and quick-ask
  routes.
- PR `#66` facts are accurate: new shell/client files exist on the PR branch
  only, F13 Agora passes, and the remaining blocker is the integration gate.
- v1.3/v4 workshop/trading-room design/spec additions are not treated as parent
  shell acceptance scope.
- This packet changed support material only and does not mutate canonical truth
  or runtime/source files.

Approval does not implement or approve parent `AG-FE-ID-001`. Parent owner
`Claude` decides whether and how to absorb the packet after the execute-plans
gate is resolved.

*Prepared by Codex for the `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30` support slice.*
