# AG-FE-ID-001 Followup-31 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-21` |
| Status | `review handoff prepared` |
| Current Pantheon dev base | `dd812370282b6096205718e58fb8f40781841d07` |
| Previous packet closeout | Followup-30 archived `done`; packet commit `f3c7811b`; closeout review commit `4b3d7517`; final closeout PR `#2085` merged at `e048d60c` |
| Parent implementation PR | execute-plans PR `#66`, `OPEN` / `UNKNOWN`, head `de7834b8c33d39942e37f0fb8d4511726d828ad8`, updated `2026-06-21T11:34:55Z`; `integration-gate` still failed |
| execute-plans dev base | `574cc541bf326e031a2f6bf9081e428a708b929a` |
| Legacy compatibility PR | execute-plans PR `#63`, still `OPEN` / `UNKNOWN`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`, updated `2026-06-20T16:53:49Z` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-FE-ID-001` BFF/frontend handoff after
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30` closed.

Material changes since followup-30:

1. Pantheon `origin/dev` advanced from `e048d60c` to `dd812370`. The new Agora
   runtime deltas are research/workshop surfaces: `AG-BE-SW-004`,
   `AG-BE-RS-001`, and AG-BE-RS support sidecars. There is still no delta in
   `services/control-plane/bff/agora/router.py` or
   `services/control-plane/bff/agora/servant/*`.
2. Focused BFF validation still passes for the AG-FE-ID-001 identity and
   servant-session surface: `39 passed in 30.34s`.
3. execute-plans `origin/dev` advanced from `c357688c` to `574cc541` through
   PR `#68`, refreshing `src/lib/bff-v1/agora/types.ts` and adjusting eslint
   ignore behavior for the Pantheon contract mirror. Parent PR `#66` has not
   absorbed that dev update yet; its head remains `de7834b8`.
4. execute-plans PR `#66` remains open with merge state `UNKNOWN`. Its latest
   code-level review is approved for the AG-FE-ID-001 shell/client slice, but
   the aggregate `integration-gate` check still fails.
5. execute-plans PR `#63` remains open/unknown with unchanged head and timestamp.
6. The Pantheon Agora compatibility manifest deployment gate remains
   fail-closed because the manifest is not compatible, the frontend runtime
   commit is still a placeholder, and blocking reasons remain non-empty.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31` | active `in_progress`; owner `Codex`, reviewer `Claude` | This packet is the support-only artifact for review. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30` | archived `done`; packet PR `#2082`; closeout artifact PR `#2085` merged at `e048d60c` | Previous support packet is durable on `dev` and is the baseline for this refresh. |
| `AG-FE-ID-001` | active `blocked`; owner `Claude`, reviewer `Codex`, waiting for `Gemini` | Parent PR `#66` remains blocked by the execute-plans aggregate release gate, not by the Agora-specific shell/client review. |
| `AG-FE-000` | archived `done` | Entry/build/audience split remains accepted dependency context. |
| `AG-BE-ID-003` | archived `done` | Servant-session backend facade remains available; parent must still prove frontend session client/UI readiness before enabling session controls. |
| `AG-BE-SW-004` | archived `done` | Workshop SSE aggregate stream is new Phase 2 context; do not fold it into the Phase 1 identity/servant shell. |
| `AG-BE-RS-001` | archived `done` | ResearchPlan facade is new Phase 3 context; not parent shell acceptance scope. |
| `AG-BE-RS-002` | active `in_progress`; owner `Codex`, reviewer `Claude` | Research run/progress/result projection work is in flight and separate from AG-FE-ID-001. |

Dependency honesty rule: `AG-FE-ID-001` may rely on identity, capability,
servant ensure, and servant-session BFF routes as backend-available facts. It
still must not claim execute-plans dev deployment readiness until PR `#66`
merges or the release-gate blocker is explicitly dispositioned.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_31.md` | This sidecar's support-only assignment. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31` | Confirms active owner/reviewer/status and support-only artifact path. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30` | Confirms predecessor archived `done`, packet/review commits, and closeout context. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001` | Confirms parent `blocked`, waiting for `Gemini`, with PR `#66` aggregate-gate blocker. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-000` | Confirms Phase 0 frontend dependency is archived `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-ID-003` | Confirms servant-session backend dependency is archived `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-SW-004` | Confirms workshop SSE aggregate stream is archived `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-001` | Confirms ResearchPlan facade is archived `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-002` | Confirms research run/progress/result work is active and separate. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30.md` | Previous AG-FE-ID-001 support baseline. |
| `git log --oneline e048d60c..origin/dev --decorate` | Shows Pantheon dev delta through PR `#2090`. |
| `git diff --name-status e048d60c..origin/dev -- <checked pathset>` | Confirms research/workshop deltas, no identity/servant router delta. |
| `rg -n "@router\\.(get|post|delete)|sessions|ensure|reconcile|stream" services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant/router.py` | Confirms active identity and servant route families. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | Confirms PR `#66` remains open, merge state `UNKNOWN`, head `de7834b8`. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | Confirms `integration-gate` failed. |
| `gh api repos/ajoe734/execute-plans/issues/66/comments ...` | Confirms latest Codex re-review approved the narrow code slice while keeping PR merge blocked by aggregate gate. |
| execute-plans remote tree probes | Confirm PR branch contains the new shell/client files; `origin/dev` still lacks those files but now has refreshed `types.ts`. |
| `python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py` | `39 passed in 30.34s`. |
| `python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: not compatible, placeholder frontend runtime commit, blocking reasons non-empty. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-30 Closeout

Baseline: followup-30 closeout merge `e048d60c`.

| Change | What changed | Parent implication |
|---|---|---|
| Pantheon dev advanced | `origin/dev` moved from `e048d60c` to `dd812370`. | Use `dd812370`, not the followup-30 base, when checking current support facts. |
| AG-FE-DB followup | `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-23` merged support/review artifacts. | No AG-FE-ID-001 identity/servant implication. |
| `AG-BE-SW-004` | Workshop SSE aggregate stream landed; `strategy_workshop/router.py` changed. | Workshop stream context only. Do not treat as parent status-shell acceptance or session UI readiness. |
| `AG-BE-RS-001` | ResearchPlan facade landed; `research/router.py` and `research/store.py` changed. | Research plan/run surface is Phase 3 scope; keep separate from AG-FE-ID-001 Phase 1 identity/servant shell. |
| `AG-BE-RS-002` sidecar | Support packet for research run/progress/result projection merged; parent `AG-BE-RS-002` is now active. | Research progress/result UI remains separate follow-through, not a reason to broaden AG-FE-ID-001. |
| Identity and servant routes | `git diff e048d60c..origin/dev -- services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant` returned no changed files. | The BFF identity/servant ledger from followup-30 remains valid. |
| execute-plans dev advanced | PR `#68` merged at `574cc541`: eslint ignores `pantheon-contract`, and `src/lib/bff-v1/agora/types.ts` was refreshed. | Parent PR `#66` should rebase or otherwise absorb this refreshed type baseline before claiming current dev compatibility. |
| execute-plans PR `#66` | Still open; merge state changed from `UNSTABLE` in followup-30 to `UNKNOWN`; head unchanged at `de7834b8`. | This is not a merge-readiness improvement. The aggregate gate still fails. |
| execute-plans PR `#63` | Still open; merge state `UNKNOWN`; head/timestamp unchanged. | Continue to treat as unresolved legacy compatibility follow-through risk. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Frontend handoff rule |
|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover identity scope and servant policy context. | Parent may use for identity readiness through strict BFF transport. Do not infer servant/session success from identity alone. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered capabilities. | Parent may use for readiness/capability display. Keep route status distinct from research/workshop capabilities. |
| `POST /bff/agora/servant/ensure` | Implemented in `services/control-plane/bff/agora/servant/router.py`; requires `Idempotency-Key` and `X-Request-Id`; returns a user-private `ServantProfile` envelope and maps OpenClaw sync failures to 503 dependency unavailable. | `servant.ts` should send required headers, parse the envelope, and map 401/403/422/503 explicitly. |
| `POST /bff/agora/servant/sessions` | Implemented by `AG-BE-ID-003`; focused tests cover default `interactive`, explicit `trainer` and `research_task`, unknown type rejection, audit fields, no-authority context, and 201 create. | Frontend may target this only with strict servant-session clients and UI tests. Do not use legacy `/bff/agora/sessions` for these modes. |
| `GET /bff/agora/servant/sessions/{session_id}` | Implemented; tests cover scoped detail response and audit fields. | Safe as the servant-session detail route after create. |
| `POST /bff/agora/servant/sessions/{session_id}/messages` | Implemented; tests cover message path and OpenClaw provider degradation. | Client must show degraded/error state without fabricating assistant success. |
| `POST /bff/agora/servant/sessions/{session_id}/terminate` | Implemented; tests cover terminate/cancel path and audit. | Use only for sessions created through the servant facade. |
| `GET /bff/agora/servant/sessions/{session_id}/stream` | Implemented; tests cover SSE stream events. | Can be used for session-scoped stream once frontend session UI is in scope. |
| `GET /bff/agora/servant` | No current servant sub-router handler identified. | Do not make the shell depend on this route. PR `#66` correctly removed this unsupported preflight according to re-review. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler identified. | Keep out of the parent UI unless runtime support lands or reviewer records a disposition. |
| `GET/POST /bff/agora/sessions*` | Legacy routes still live outside the servant facade. | Do not treat as proof of `interactive`, `trainer`, or `research_task` servant-session readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface remains separate from the servant facade. | Do not use for parent servant-session controls unless explicitly reassigned. |
| Workshop SSE stream | `AG-BE-SW-004` landed workshop aggregate streaming. | Separate Phase 2 workshop context; not AG-FE-ID-001 status-shell acceptance. |
| Research plan/run routes | `AG-BE-RS-001` changed research router/store; `AG-BE-RS-002` run/projection work is active. | Separate Phase 3 research UI/client scope. Do not fold research progress/result cards into the identity/servant shell. |

Safe parent-shell facts now are: user-private identity scope, filtered
capability readiness, successful servant profile ensure through
`/bff/agora/servant/ensure`, and available backend servant-session routes. The
frontend still needs PR-level merge/gate evidence before operator-ready
deployment claims.

## 6. Frontend Surface To Hand Off

Remote probe source: `/home/lupin/code/execute-plans` after
`git fetch origin --prune`. execute-plans `origin/dev` is now `574cc541`.

| Surface | `origin/dev` (`574cc541`) | PR `#66` head (`de7834b8`) | Handoff implication |
|---|---|---|---|
| `src/agora/AgoraApp.tsx` | Missing | Added | Parent shell exists only on PR `#66`. |
| `src/lib/bff-v1/agora/identity.ts` | Missing | Added | PR branch has strict identity client for `/me` and `/capabilities`. |
| `src/lib/bff-v1/agora/identity.test.ts` | Missing | Added | Focused identity tests are part of PR `#66`. |
| `src/lib/bff-v1/agora/servant.ts` | Missing | Added | PR branch has strict servant ensure client using BFF transport and headers. |
| `src/lib/bff-v1/agora/servant.test.ts` | Missing | Added | Focused servant tests are part of PR `#66`. |
| `src/lib/bff-v1/agora/types.ts` | Present and refreshed by PR `#68` | Present but differs from `origin/dev` | Parent PR should update/rebase to avoid carrying stale generated type deltas. |
| `src/agora/pages/AskPersonas.tsx` | Present | Present | Existing ask UI remains separate from the new status shell. |
| `src/lib/bff/agora.ts` | Present | Present | Legacy helper is not sufficient for parent acceptance. |

`git diff --name-status origin/dev..origin/task/AG-FE-ID-001 -- <target
pathset>` currently shows the five parent shell/client/test files added plus a
`types.ts` modification. That `types.ts` modification should be rechecked
against the refreshed `origin/dev` baseline from PR `#68`.

Observed PR `#66` review state from the latest Codex comment:

- Prior code-level blockers are resolved at `de7834b8`.
- `AgoraApp` calls identity readiness before servant ensure.
- Identity and servant clients use the strict BFF transport path.
- The unsupported `GET /bff/agora/servant` preflight is removed.
- Local focused validation cited by the review passed: boundary check,
  targeted eslint, Agora vitest 23/23, and repo test 612/612.
- PR merge/owner closeout remains blocked by the aggregate release gate.

## 7. Execute-Plans PR #66 Gate State

`gh pr checks 66 --repo ajoe734/execute-plans` reports:

| Check | State | Evidence |
|---|---|---|
| `integration-gate` | `fail` | Run `27902747928`, job `82565909429` |

The latest release-gate summary still records overall `FAIL`. Its visible gate
breakdown in the PR comments includes:

| Gate | Result | Owner | Notes |
|---|---|---|---|
| Gate 0 Preconditions | `PASS` | - | Run and deployment metadata present. |
| Gate 1 Static / Build / Unit | `FAIL` | Gemini | Lint-related open checks recorded in the summary. |
| Gate 2 Contract Drift | `FAIL` | Codex | Six drift checks were still open in the recorded run. |
| Gate 3 BFF Route Probes | `WARN` | Codex | One backend write-probe warning recorded. |
| Gate 4 Browser Frontend E2E | `PASS` | - | Hosted browser probe passed in the recorded run. |

PR `#68` may address part of the Gate 1/Gate 2 root cause on `origin/dev`, but
PR `#66` has not been rerun with that dev baseline as of this packet. Parent
should not mark itself done until the gate reruns cleanly or the repository
records a formal exception.

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

Backend session journey available for future frontend follow-through:

```text
frontend creates a servant session with POST /bff/agora/servant/sessions
  -> session_type is interactive by default, or trainer/research_task when supplied
  -> BFF sends approved context_bundle to OpenClaw with audit fields
  -> frontend sends messages through POST /sessions/{id}/messages
  -> stream reads use GET /sessions/{id}/stream
  -> terminate uses POST /sessions/{id}/terminate
  -> OpenClaw degradation maps to typed dependency-unavailable behavior
```

The frontend still must not expose Management, capital pool, broker order,
RuntimeBinding, or live order controls through the Agora shell.

## 9. Parent Absorption Checklist

Claude should not absorb this sidecar into parent completion unless the parent
evidence answers these checks:

| Check | Required evidence |
|---|---|
| Parent PR status | PR `#66` is merged into execute-plans `dev`, or parent remains blocked with the aggregate-gate blocker recorded. |
| Dev baseline freshness | PR `#66` has absorbed execute-plans PR `#68` or otherwise reconciled the refreshed `src/lib/bff-v1/agora/types.ts` baseline. |
| Backend dependency | `AG-BE-ID-003` is treated as done, and frontend session work uses `/bff/agora/servant/sessions*`, not legacy sessions. |
| Identity route truth | `/me` and `/capabilities` are strict live calls; failures produce blocked states with no seed/mock fallback. |
| Servant ensure truth | `/servant/ensure` sends required idempotency/request headers and maps 401/403/422/503 without fabricating success. |
| Unsupported servant routes | Parent does not claim `GET /bff/agora/servant` or `POST /bff/agora/servant/reconcile` is runtime-supported unless backend adds it. |
| Session route family | Parent does not mix servant sessions, legacy sessions, and quick-ask sessions without explicit backend disposition. |
| Session frontend scope | If session controls are enabled, tests cover create/detail/message/stream/terminate plus degradation and audit semantics. |
| Research/workshop separation | New AG-BE-SW and AG-BE-RS surfaces are not silently absorbed into the Phase 1 status shell. |
| Strict clients | Page components avoid direct route fetches; BFF clients preserve strict live semantics. |
| Bundle isolation | Agora shell tests or static checks prove no Management/runtime-binding/capital/broker/order code leaks into the app shell. |
| Compatibility honesty | Parent does not claim dev deployment readiness while PR `#66` is unmerged, PR `#63` remains open/unknown, or the compatibility manifest has placeholder frontend runtime commit. |
| Gate ownership | Aggregate gate failures are left with their recorded owners rather than being buried in AG-FE-ID-001 closeout. |

## 10. Verification Performed For This Sidecar

Commands and results:

| Command | Result |
|---|---|
| `git status -sb`; `git branch --show-current`; `git remote -v` | Started on expected task branch with origin `https://github.com/ajoe734/pantheon.git`; only generated followup-31 task brief was untracked. |
| `AI_NAME=Codex ./scripts/ai-status.sh progress AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31 ...` | Recorded owner progress using the required `AI_NAME=Codex`. |
| `git fetch origin --prune` | Completed; `origin/dev` advanced to `dd812370`. |
| `git merge --ff-only origin/dev` | Fast-forwarded this task branch from `af5f803b` to `dd812370`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31` | Active `in_progress`, owner `Codex`, reviewer `Claude`, support artifact path. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30` | Archived `done`; packet/review/closeout durable on `dev`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001` | Parent active `blocked`, waiting for `Gemini`; PR `#66` gate blocker recorded. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-000` | Archived `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-ID-003` | Archived `done`; servant-session facade accepted. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-SW-004` | Archived `done`; workshop SSE aggregate stream accepted. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-001` | Archived `done`; ResearchPlan facade accepted. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-002` | Active `in_progress`; research run/progress/result projection separate from parent shell. |
| `git log --oneline e048d60c..origin/dev --decorate --max-count=80` | Shows Pantheon dev delta through PR `#2090`. |
| `git diff --name-status e048d60c..origin/dev -- services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant services/control-plane/bff/agora/research services/control-plane/bff/agora/strategy_workshop` | Shows only `research/router.py`, `research/store.py`, and `strategy_workshop/router.py` changes; no identity/servant route delta. |
| `rg -n "@router\\.(get|post|delete)|sessions|ensure|reconcile|stream" services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant/router.py` | Confirms `/me`, `/capabilities`, `/servant/ensure`, servant session create/detail/message/terminate/stream routes; no `GET /servant` or `/servant/reconcile` handler. |
| `git -C /home/lupin/code/execute-plans fetch origin --prune` | Completed; `origin/dev` now `574cc541`. |
| `git -C /home/lupin/code/execute-plans log --oneline c357688c..origin/dev --decorate --max-count=80` | Shows PR `#68` merge and commit `06769d7` refreshing Agora types. |
| `git -C /home/lupin/code/execute-plans diff --name-status c357688c..origin/dev -- src/lib/bff-v1/agora/types.ts ...` | Shows `src/lib/bff-v1/agora/types.ts` modified. |
| `git -C /home/lupin/code/execute-plans diff --name-status origin/dev..origin/task/AG-FE-ID-001 -- <target pathset>` | Adds `AgoraApp.tsx`, `identity.ts`, `servant.ts`, focused tests, and still modifies `types.ts`. |
| `gh pr view 66 --repo ajoe734/execute-plans --json number,state,mergeStateStatus,headRefName,baseRefName,headRefOid,updatedAt,url,reviewDecision,isDraft` | PR `#66` `OPEN` / `UNKNOWN`, base `dev`, head `task/AG-FE-ID-001`, commit `de7834b8`, not draft. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | `integration-gate` failed. |
| `gh pr view 63 --repo ajoe734/execute-plans --json number,state,mergeStateStatus,headRefName,baseRefName,headRefOid,updatedAt,url,reviewDecision,isDraft` | PR `#63` `OPEN` / `UNKNOWN`, head `e1cb9125`, timestamp unchanged. |
| `python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py` | `39 passed in 30.34s`. |
| `python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Exit `1`; expected fail-closed with compatibility status, placeholder frontend runtime commit, and blocking reason errors. |

## 11. Reviewer Handoff

This packet is ready for review by `Claude`. Review should check:

- The update from followup-30 is accurate: Pantheon dev moved to `dd812370`,
  identity/servant routes are unchanged, and new research/workshop surfaces are
  separate from AG-FE-ID-001.
- execute-plans facts are accurate: `origin/dev` now has PR `#68`, PR `#66`
  remains open at `de7834b8`, and the gate is still red.
- The BFF ledger correctly distinguishes supported servant routes from
  unsupported `GET /bff/agora/servant` and `/servant/reconcile`.
- The parent absorption checklist keeps AG-BE-SW/AG-BE-RS scope out of the
  Phase 1 status shell.
- This packet changed support material only and does not mutate canonical truth
  or runtime/source files.

Approval does not implement or approve parent `AG-FE-ID-001`. Parent owner
`Claude` decides whether and how to absorb the packet after the execute-plans
gate is resolved.

*Prepared by Codex for the `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31` support slice.*
