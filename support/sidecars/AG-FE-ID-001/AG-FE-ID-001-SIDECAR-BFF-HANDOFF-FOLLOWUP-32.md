# AG-FE-ID-001 Followup-32 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-32` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-21` |
| Status | `review handoff prepared` |
| Current Pantheon dev base | `7b391454eacaeb01f5d7e859a16c3906856d5557` |
| Previous packet closeout | Followup-31 archived `done`; packet commits `ef82f321`, `eeddc748`; PR `#2093` merged at `4bde7a97b6481d1952e9add55aedc80c1055a98e` |
| Parent implementation PR | execute-plans PR `#66`, `OPEN` / `UNSTABLE`, head `de7834b8c33d39942e37f0fb8d4511726d828ad8`, updated `2026-06-21T11:34:55Z`; `integration-gate` still failed |
| execute-plans dev base | `574cc541bf326e031a2f6bf9081e428a708b929a` |
| Legacy compatibility PR | execute-plans PR `#63`, `OPEN` / `UNSTABLE`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`, updated `2026-06-20T16:53:49Z` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-FE-ID-001` BFF/frontend handoff after
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31` closed.

Material changes since followup-31:

1. Pantheon `origin/dev` advanced from `4bde7a97` to `7b391454`. The visible
   commits are AG-BE-RS-002 closeout evidence and AG-FE-DB-002 sidecar
   acceptance follow-up work. They do not change the checked Agora identity,
   servant, contract, or AG-FE-ID-001 support paths.
2. `AG-BE-RS-002` is now archived `done`; followup-31's temporary status/code
   mismatch is resolved. Research run/progress/result remains a separate Phase
   3 surface and is not AG-FE-ID-001 acceptance scope.
3. execute-plans PR `#66` remains open and unstable at the same head
   `de7834b8`. The latest Codex re-review approved the narrow AG-FE-ID-001
   shell/client code slice, but the aggregate `integration-gate` check is still
   failed.
4. execute-plans `origin/dev` remains `574cc541`, so PR `#66` still has not
   absorbed the refreshed `src/lib/bff-v1/agora/types.ts` baseline from PR
   `#68`.
5. execute-plans PR `#63` remains open/unstable with unchanged head and
   timestamp.
6. The Pantheon Agora compatibility manifest deployment gate remains
   fail-closed because the manifest is not compatible, the frontend runtime
   commit is still a placeholder, and blocking reasons remain non-empty.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`. The status wrapper reads the shared
status root at `/home/lupin/code/pantheon`; the worker-local `ai-status.json`
did not contain this generated followup until the wrapper materialized it.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-32` | active `in_progress`; owner `Codex`, reviewer `Claude` | This packet is the support-only artifact for review. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31` | archived `done`; packet PR `#2093` merged at `4bde7a97` | Previous support packet is the baseline for this refresh. |
| `AG-FE-ID-001` | active `blocked`; owner `Claude`, reviewer `Codex`, waiting for `Gemini` | Parent PR `#66` remains blocked by the execute-plans aggregate release gate, not by the Agora-specific shell/client review. |
| `AG-FE-000` | archived `done` | Entry/build/audience split remains accepted dependency context. |
| `AG-BE-ID-003` | archived `done` | Servant-session backend facade remains available; parent must still prove frontend session client/UI readiness before enabling session controls. |
| `AG-BE-SW-004` | archived `done` | Workshop SSE aggregate stream remains Phase 2 context; do not fold it into the Phase 1 identity/servant shell. |
| `AG-BE-RS-001` | archived `done` | ResearchPlan facade remains Phase 3 context; not parent shell acceptance scope. |
| `AG-BE-RS-002` | archived `done`; implementation PR `#2092`, closeout PR `#2094` | Research progress/result closeout is now durable; it still remains separate from AG-FE-ID-001. |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-24` | archived `done`; PRs `#2095` and `#2097` | DB acceptance support material only; no AG-FE-ID-001 identity/servant implication. |

Dependency honesty rule: `AG-FE-ID-001` may rely on identity, capability,
servant ensure, and servant-session BFF routes as backend-available facts. It
still must not claim execute-plans dev deployment readiness until PR `#66`
merges or the release-gate blocker is explicitly dispositioned.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_32.md` | This sidecar's generated support-only assignment. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-32` | Confirms active owner/reviewer/status and support-only artifact path after wrapper materialization. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31` | Confirms predecessor archived `done`, packet/review commits, and closeout context. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent `blocked`, waiting for `Gemini`, with PR `#66` aggregate-gate blocker. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Confirms research run projection is now archived `done`, resolving followup-31's active-status mismatch. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-24` | Confirms the newest Pantheon dev delta is DB acceptance support-only closeout. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31.md` | Previous AG-FE-ID-001 support baseline. |
| `git log --oneline 4bde7a97..origin/dev --decorate` | Shows Pantheon dev delta through PR `#2097`. |
| `git diff --name-status 4bde7a97..origin/dev -- <checked pathset>` | Confirms no checked Agora BFF, contract, or AG-FE-ID-001 support-path delta. |
| `rg -n "@router\\.(get|post|delete)|sessions|ensure|reconcile|stream" services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant/router.py` | Confirms active identity, ensure, and servant-session route families. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | Confirms PR `#66` remains open/unstable at head `de7834b8`. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | Confirms `integration-gate` failed in run `27902747928`, job `82565909429`. |
| `gh api repos/ajoe734/execute-plans/issues/66/comments ...` | Confirms latest Codex re-review approved the narrow code slice while keeping PR merge blocked by aggregate gate. |
| execute-plans remote tree probes | Confirm PR branch contains the new shell/client files; `origin/dev` still lacks those files and differs on `types.ts`. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | Confirms legacy compatibility PR remains open/unstable with unchanged head/timestamp. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q` | `39 passed in 36.17s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: not compatible, placeholder frontend runtime commit, blocking reasons non-empty. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-31 Closeout

Baseline: followup-31 closeout merge `4bde7a97`.

| Change | What changed | Parent implication |
|---|---|---|
| Pantheon dev advanced | `origin/dev` moved from `4bde7a97` to `7b391454`. | Use `7b391454`, not the followup-31 base, when checking current support facts. |
| AG-BE-RS-002 closeout | PR `#2094` merged closeout evidence; `ai-status` now archives `AG-BE-RS-002` as `done`. | Removes prior status mismatch. Research progress/result UI remains separate Phase 3 scope. |
| AG-FE-DB followup | PRs `#2095` and `#2097` merged AG-FE-DB-002 acceptance support and closeout artifacts. | No AG-FE-ID-001 identity/servant implication. |
| Checked Agora paths | `git diff --name-status 4bde7a97..origin/dev -- services/control-plane/bff/agora services/control-plane/bff/main.py services/control-plane/specs/agora services/control-plane/openapi docs/contracts/agora support/sidecars/AG-FE-ID-001` returned no changed files. | The BFF identity/servant ledger from followup-31 remains valid. |
| execute-plans PR `#66` | Still open; merge state is `UNSTABLE`; head unchanged at `de7834b8`; updated timestamp unchanged from the latest Codex re-review. | This is not a merge-readiness improvement. The aggregate gate still fails. |
| execute-plans PR `#63` | Still open/unstable; head `e1cb9125`; timestamp unchanged. | Continue to treat as unresolved legacy compatibility follow-through risk. |

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
| Workshop SSE stream | `AG-BE-SW-004` landed workshop aggregate streaming before this packet. | Separate Phase 2 workshop context; not AG-FE-ID-001 status-shell acceptance. |
| Research plan/run routes | `AG-BE-RS-001` and `AG-BE-RS-002` are now archived `done`. | Separate Phase 3 research UI/client scope. Do not fold research progress/result cards into the identity/servant shell. |

Safe parent-shell facts now are: user-private identity scope, filtered
capability readiness, successful servant profile ensure through
`/bff/agora/servant/ensure`, and available backend servant-session routes. The
frontend still needs PR-level merge/gate evidence before operator-ready
deployment claims.

## 6. Frontend Surface To Hand Off

Remote probe source: `/home/lupin/code/execute-plans` after
`git fetch origin --prune`. The checkout is detached, but remote refs were
freshly fetched and no execute-plans files were edited.

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
`types.ts` modification.

Observed PR `#66` review state from the latest Codex comment:

- Prior code-level blockers are resolved at `de7834b8`.
- `AgoraApp` calls identity readiness before servant ensure.
- Identity and servant clients use the strict BFF transport path.
- The unsupported `GET /bff/agora/servant` preflight is removed.
- Local focused validation cited by the review passed: boundary check,
  targeted eslint, Agora vitest 23/23, and repo test 612/612.
- CI install/build for the PR passed in the cited re-review, but the aggregate
  release gate remains red on repo-wide release criteria outside this task
  slice.
- PR merge/owner closeout remains blocked by the aggregate release gate.

## 7. Execute-Plans PR #66 Gate State

`gh pr checks 66 --repo ajoe734/execute-plans` reports:

| Check | State | Evidence |
|---|---|---|
| `integration-gate` | `fail` | Run `27902747928`, job `82565909429` |

`gh pr view 66` reports `OPEN` / `UNSTABLE`, with only the failed
`integration-gate` check surfaced in `statusCheckRollup`.

The latest release-gate summary comment still records overall `FAIL`. Its
visible gate breakdown includes:

| Gate | Result | Owner | Notes |
|---|---|---|
| Gate 0 Preconditions | `PASS` | - | Run and deployment metadata present. |
| Gate 1 Static / Build / Unit | `FAIL` | Gemini | Lint and contract-test failures recorded in the release summary. |
| Gate 2 Contract Drift | `FAIL` | Codex | Six drift checks recorded in the release summary. |
| Gate 3 BFF Route Probes | `WARN` | Codex | One backend write-probe warning recorded. |
| Gate 4 Browser Frontend E2E | `PASS` | - | Hosted browser probe passed in the recorded run. |
| Gate 5 Playwright User Flows | `FAIL` | Codex | Two open checks recorded. |
| Gate 6 A11y / Perf | `FAIL` | Codex2 | Two open checks recorded. |
| Gate 7 Release Decision | `FAIL` | Codex | Aggregate release decision remains failed. |

The latest human-authored Codex re-review narrows the AG-FE-ID-001 code-slice
disposition to approved, but it does not clear the PR's aggregate merge gate.
Parent should not mark itself done until the gate reruns cleanly or the
repository records a formal exception.

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
| Research/workshop separation | AG-BE-SW and AG-BE-RS surfaces are not silently absorbed into the Phase 1 status shell. |
| Strict clients | Page components avoid direct route fetches; BFF clients preserve strict live semantics. |
| Bundle isolation | Agora shell tests or static checks prove no Management/runtime-binding/capital/broker/order code leaks into the app shell. |
| Compatibility honesty | Parent does not claim dev deployment readiness while PR `#66` is unmerged, PR `#63` remains open/unstable, or the compatibility manifest has placeholder frontend runtime commit. |
| Gate ownership | Aggregate gate failures are left with their recorded owners rather than being buried in AG-FE-ID-001 closeout. |

## 10. Verification Performed For This Sidecar

Commands and results:

| Command | Result |
|---|---|
| `git status -sb`; `git branch --show-current`; `git remote -v` | Started on expected task branch with origin `https://github.com/ajoe734/pantheon.git`; only generated followup-32 task brief was untracked. |
| `./scripts/git/task_start.sh "AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-32"` | Rebased the task branch to `origin/dev` at `7b391454`; generated task brief remained task-scoped local context. |
| `AI_NAME=Codex ./scripts/ai-status.sh progress AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-32 ...` | Recorded owner progress using the required `AI_NAME=Codex`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001` | Active `blocked`; parent waits for `Gemini` on execute-plans PR `#66` aggregate gate. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-31` | Archived `done`; packet/review/closeout durable on `dev`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Archived `done`; implementation and closeout durable. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-24` | Archived `done`; newest dev delta is DB acceptance support-only. |
| `git log --oneline 4bde7a97..origin/dev --decorate` | Shows AG-BE-RS-002 closeout and AG-FE-DB-002 support/closeout merges after followup-31. |
| `git diff --name-status 4bde7a97..origin/dev -- <checked pathset>` | No checked Agora BFF, contract, or AG-FE-ID-001 support-path delta. |
| `rg -n "@router\\.(get|post|delete)|sessions|ensure|reconcile|stream" services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant/router.py` | Confirms route ledger in this packet. |
| `git -C /home/lupin/code/execute-plans fetch origin --prune --quiet` | Completed; `origin/dev` is `574cc541`, `origin/task/AG-FE-ID-001` is `de7834b8`. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | PR `#66` is `OPEN` / `UNSTABLE`; head `de7834b8`; updated `2026-06-21T11:34:55Z`. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | Failed because `integration-gate` is failing; run `27902747928`, job `82565909429`. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | PR `#63` is `OPEN` / `UNSTABLE`; head `e1cb9125`; updated `2026-06-20T16:53:49Z`. |
| `git -C /home/lupin/code/execute-plans diff --name-status origin/dev..origin/task/AG-FE-ID-001 -- <target pathset>` | Parent shell/client/test files are only on PR `#66`; `types.ts` differs from refreshed `origin/dev`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q` | `39 passed in 36.17s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: compatibility status not compatible, frontend runtime commit placeholder, blocking reasons non-empty. |

## 11. Handoff To Reviewer

Reviewer `Claude`: please review this support-only packet for factual accuracy
and scope discipline. The recommended disposition is to approve the sidecar if
the refreshed facts match current state, while keeping parent `AG-FE-ID-001`
blocked until execute-plans PR `#66` is merged or the aggregate gate receives a
formal repository disposition.
