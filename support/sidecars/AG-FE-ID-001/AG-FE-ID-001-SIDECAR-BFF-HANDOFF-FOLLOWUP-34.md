# AG-FE-ID-001 Followup-34 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Date | `2026-06-21` |
| Status | `review handoff prepared` |
| Current Pantheon dev base | `7b112049a0a735f7fc49e8ba6d8fd973a19d5c75` |
| Previous packet closeout | Followup-33 archived `done` at `2026-06-21T19:38:51Z`; packet PR merged at `29fe886b` |
| Parent implementation PR | execute-plans PR `#66`, `OPEN` / `MERGEABLE`, head `de7834b8c33d39942e37f0fb8d4511726d828ad8`, updated `2026-06-21T11:34:55Z`; `integration-gate` still failed |
| execute-plans dev base | `574cc541bf326e031a2f6bf9081e428a708b929a` |
| Legacy compatibility PR | execute-plans PR `#63`, `OPEN`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`, updated `2026-06-20T16:53:49Z` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-FE-ID-001` BFF/frontend handoff after
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33` closed.

Material changes since followup-33:

1. Pantheon `origin/dev` advanced from `4e745eb0` to `7b112049`. The new
   commits include INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR
   (PR `#2156`), INTEGRATION-UNBLOCK-AG-FE-RS-001-SIDECAR-BFF-HANDOFF-CI-RED (PR `#2159`),
   AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 (PR `#2158`), and related merge
   commits. None of these touch the Agora BFF, contract, identity, servant, or
   AG-FE-ID-001 support paths.
2. `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33` is now archived `done` at
   `2026-06-21T19:38:51Z`; its packet and review files (`FOLLOWUP-33.md`,
   `FOLLOWUP-33-REVIEW.md`) are now present on `origin/dev` as the new baseline.
3. Execute-plans PR `#66` remains open and `MERGEABLE` at the same head
   `de7834b8`. The `integration-gate` check is still failed (run
   `27902747928`, job `82565909429`). Head and updated timestamps are
   identical to what followup-33 recorded.
4. Execute-plans `origin/dev` remains `574cc541`, so PR `#66` still has not
   absorbed the refreshed `src/lib/bff-v1/agora/types.ts` baseline from PR
   `#68`.
5. Execute-plans PR `#63` remains open with unchanged head and timestamp.
6. The Pantheon Agora compatibility manifest deployment gate remains
   fail-closed: compatibility status is not compatible, frontend runtime commit
   is a placeholder, and blocking reasons are non-empty.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Claude2`. The status wrapper reads the shared
status root at `/home/lupin/code/pantheon`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34` | active `in_progress`; owner `Claude2`, reviewer `Claude` | This packet is the support-only artifact for review. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33` | archived `done` at `2026-06-21T19:38:51Z` | Previous support packet is the baseline for this refresh. |
| `AG-FE-ID-001` | active `blocked`; owner `Claude`, reviewer `Codex`, waiting for `Gemini` | Parent PR `#66` remains blocked by the execute-plans aggregate release gate, not by the Agora-specific shell/client review. |
| `AG-FE-000` | archived `done` | Entry/build/audience split remains accepted dependency context. |
| `AG-BE-ID-003` | archived `done` | Servant-session backend facade remains available; parent must still prove frontend session client/UI readiness before enabling session controls. |
| `AG-BE-SW-004` | archived `done` | Workshop SSE aggregate stream remains Phase 2 context; do not fold into Phase 1 identity/servant shell. |
| `AG-BE-RS-001` | archived `done` | ResearchPlan facade remains Phase 3 context; not parent shell acceptance scope. |
| `AG-BE-RS-002` | archived `done` | Research progress/result remains separate Phase 3 scope. |

Dependency honesty rule: `AG-FE-ID-001` may rely on identity, capability,
servant ensure, and servant-session BFF routes as backend-available facts. It
still must not claim execute-plans dev deployment readiness until PR `#66`
merges or the release-gate blocker is explicitly dispositioned.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_34.md` | This sidecar's generated support-only assignment. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34` | Confirms active owner/reviewer/status and support-only artifact path. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33` | Confirms predecessor archived `done`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent `blocked`, waiting for `Gemini`, with PR `#66` aggregate-gate blocker. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33.md` | Previous AG-FE-ID-001 support baseline. |
| `git log --oneline 4e745eb0..origin/dev --decorate` | Shows Pantheon dev delta since followup-33 baseline. |
| `git diff --name-status 4e745eb0..origin/dev -- <checked pathset>` | Confirms only followup-33 packet/review files changed in the AG-FE-ID-001 support path; no Agora BFF, contract, or identity/servant path changes. |
| `rg -n "@router\.(get\|post\|delete)\|sessions\|ensure\|reconcile\|stream" services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant/router.py` | Confirms active identity, ensure, and servant-session route families unchanged. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | Confirms PR `#66` is `OPEN` / `MERGEABLE`; head `de7834b8`; updated `2026-06-21T11:34:55Z`. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | Confirms `integration-gate` failed in run `27902747928`, job `82565909429`. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | Confirms legacy compatibility PR remains open with unchanged head/timestamp. |
| execute-plans remote tree probes | Confirm PR `#66` branch still contains the new shell/client files; `origin/dev` still at `574cc541` and lacks those files. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q` | `39 passed in 24.33s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: not compatible, placeholder frontend runtime commit, blocking reasons non-empty. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-33 Closeout

Baseline: followup-33 closeout base `4e745eb0`.

| Change | What changed | Parent implication |
|---|---|---|
| Pantheon dev advanced | `origin/dev` moved from `4e745eb0` to `7b112049`. | Use `7b112049`, not the followup-33 base, when checking current support facts. |
| INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR | PR `#2156` merged. | No AG-FE-ID-001 identity/servant implication. Separate BFF surface. |
| INTEGRATION-UNBLOCK-AG-FE-RS-001-SIDECAR-BFF-HANDOFF-CI-RED | PR `#2159` merged. | No AG-FE-ID-001 identity/servant implication. Research frontend CI-red unblock. |
| AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 | PR `#2158` merged. | No AG-FE-ID-001 identity/servant implication. Separate BFF surface. |
| Followup-33 packet/review | Followup-33 files added to `support/sidecars/AG-FE-ID-001/`. | These are the previous handoff record; this followup-34 packet supersedes them as the current support baseline. |
| Checked Agora paths | `git diff --name-status 4e745eb0..origin/dev -- services/control-plane/bff/agora services/control-plane/bff/main.py services/control-plane/specs/agora services/control-plane/openapi docs/contracts/agora` | No changes. The BFF identity/servant ledger from followup-33 remains valid. |
| execute-plans PR `#66` | Still open; merge state is `MERGEABLE`; head unchanged at `de7834b8`; `integration-gate` still failing. | Not a merge-readiness improvement. Aggregate gate failure unchanged. |
| execute-plans PR `#63` | Still open; head `e1cb9125`; timestamp unchanged. | Continue to treat as unresolved legacy compatibility follow-through risk. |

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
| `GET /bff/agora/servant` | No current servant sub-router handler identified. | Do not make the shell depend on this route. PR `#66` correctly removed this unsupported preflight per prior review. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler identified. | Keep out of the parent UI unless runtime support lands or reviewer records a disposition. |
| `GET/POST /bff/agora/sessions*` | Legacy routes still live outside the servant facade. | Do not treat as proof of `interactive`, `trainer`, or `research_task` servant-session readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface remains separate from the servant facade. | Do not use for parent servant-session controls unless explicitly reassigned. |
| Workshop SSE stream | `AG-BE-SW-004` landed workshop aggregate streaming. | Separate Phase 2 workshop context; not AG-FE-ID-001 status-shell acceptance. |
| Research plan/run routes | `AG-BE-RS-001` and `AG-BE-RS-002` are archived `done`. | Separate Phase 3 research UI/client scope. Do not fold into the identity/servant shell. |

Safe parent-shell facts remain: user-private identity scope, filtered capability
readiness, successful servant profile ensure through `/bff/agora/servant/ensure`,
and available backend servant-session routes. The frontend still needs PR-level
merge/gate evidence before operator-ready deployment claims.

## 6. Frontend Surface To Hand Off

Remote probe source: `/home/lupin/code/execute-plans` after
`git fetch origin --prune`. No execute-plans files were edited.

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

The five parent shell/client/test files added plus a `types.ts` modification on
PR `#66` remain unchanged from followup-33 observation.

Observed PR `#66` review state from the latest Codex comment:

- Prior code-level blockers are resolved at `de7834b8`.
- `AgoraApp` calls identity readiness before servant ensure.
- Identity and servant clients use the strict BFF transport path.
- The unsupported `GET /bff/agora/servant` preflight is removed.
- Local focused validation cited by the review passed: boundary check,
  targeted eslint, Agora vitest 23/23, and repo test 612/612.
- CI install/build for the PR passed in the cited re-review, but the aggregate
  release gate remains red on repo-wide release criteria outside this task slice.
- PR merge/owner closeout remains blocked by the aggregate release gate.

## 7. Execute-Plans PR #66 Gate State

`gh pr checks 66 --repo ajoe734/execute-plans` reports:

| Check | State | Evidence |
|---|---|---|
| `integration-gate` | `fail` | Run `27902747928`, job `82565909429` |

`gh pr view 66` reports `OPEN` / `MERGEABLE`, with only the failed
`integration-gate` check surfaced in `statusCheckRollup`. The state is
identical to what followup-33 recorded.

The release-gate summary from followup-33 remains authoritative (unchanged
gate state):

| Gate | Result | Owner | Notes |
|---|---|---|---|
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
| Compatibility honesty | Parent does not claim dev deployment readiness while PR `#66` is unmerged, PR `#63` remains open, or the compatibility manifest has placeholder frontend runtime commit. |
| Gate ownership | Aggregate gate failures are left with their recorded owners (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6: Codex2) rather than being buried in AG-FE-ID-001 closeout. |

## 10. Verification Performed For This Sidecar

Commands and results:

| Command | Result |
|---|---|
| `git status -sb`; `git branch --show-current` | Started on expected task branch `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34`; only generated task brief was untracked. |
| `git rev-parse origin/dev` | `7b112049a0a735f7fc49e8ba6d8fd973a19d5c75` |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34` | Active `in_progress`; owner `Claude2`, reviewer `Claude`, support-only artifact. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33` | Archived `done` at `2026-06-21T19:38:51Z`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001` | Active `blocked`; owner `Claude`, reviewer `Codex`; waiting for `Gemini` on execute-plans PR `#66` aggregate gate. |
| `git log --oneline 4e745eb0..origin/dev --decorate` | Shows INTEGRATION-UNBLOCK tasks (PR `#2156`, `#2159`) and AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 (PR `#2158`) after followup-33. |
| `git diff --name-status 4e745eb0..origin/dev -- services/control-plane/bff/agora services/control-plane/bff/main.py services/control-plane/specs/agora services/control-plane/openapi docs/contracts/agora support/sidecars/AG-FE-ID-001` | Only followup-33 packet and review files added in the AG-FE-ID-001 support path; no Agora BFF or contract delta. |
| `git -C /home/lupin/code/execute-plans fetch origin --prune --quiet` | Completed; `origin/dev` is `574cc541`. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | PR `#66` is `OPEN` / `MERGEABLE`; head `de7834b8`; updated `2026-06-21T11:34:55Z`. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | `integration-gate` failing; run `27902747928`, job `82565909429`. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | PR `#63` is `OPEN`; head `e1cb9125`; updated `2026-06-20T16:53:49Z`. |
| `rg -n "@router.(get\|post\|delete)\|sessions\|ensure\|reconcile\|stream" services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant/router.py` | Active identity, ensure, servant-session routes confirmed unchanged. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q` | `39 passed in 24.33s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: compatibility status not compatible, frontend runtime commit placeholder, blocking reasons non-empty. |

## 11. Handoff To Reviewer

Reviewer `Claude`: please review this support-only packet for factual accuracy
and scope discipline. The recommended disposition is to approve the sidecar if
the refreshed facts match current state, while keeping parent `AG-FE-ID-001`
blocked until execute-plans PR `#66` is merged or the aggregate gate receives a
formal repository disposition.

Suggested approval command:

```bash
AI_NAME=Claude python3 scripts/ai_status.py approve AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34 "Followup-34 support packet approved; factually accurate refresh with no canonical truth mutations. Parent AG-FE-ID-001 remains blocked pending execute-plans PR#66 merge or gate disposition."
```

Suggested reopen command if changes are required:

```bash
AI_NAME=Claude python3 scripts/ai_status.py reopen AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34 "Describe the exact packet correction needed."
```

*Prepared by Claude2 for the `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34` support slice.*
