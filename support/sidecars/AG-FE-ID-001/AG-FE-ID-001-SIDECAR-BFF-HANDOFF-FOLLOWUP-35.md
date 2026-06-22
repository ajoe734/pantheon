# AG-FE-ID-001 Followup-35 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-22` |
| Status | `review approved; owner closeout ready` |
| Current Pantheon dev base | `e01f19e7a4b73e7a70d0a8b607159e7db4192d6b` |
| Previous packet closeout | Followup-34 archived `done` at `2026-06-22T01:59:00Z`; PR `#2174` merged into Pantheon dev at `2a811d565f9ff9d67494ecb431e1e79a5747889a`; task head `d4ab2ecbe9edf893c0fdba43ad336b0891b7a528` |
| Parent implementation PR | execute-plans PR `#66`, `OPEN` / `UNSTABLE`, head `d1ae3149935986782993a363b92227d38555cc1b`, updated `2026-06-22T01:31:49Z`; `integration-gate` still failed |
| execute-plans dev base | `ee835e2e6f1037e612d7929279a11efb32c61975` |
| Legacy compatibility PR | execute-plans PR `#63`, `OPEN` / `UNSTABLE`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`, updated `2026-06-20T16:53:49Z`; `integration-gate` failed |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-FE-ID-001` BFF/frontend handoff after
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34` closed.

Material changes since followup-34:

1. Pantheon `origin/dev` advanced from `2a811d565f9f` to `e01f19e7a4b7`.
   The new dev window includes PRs `#2176`, `#2177`, `#2178`, `#2179`,
   `#2180`, `#2181`, and `#2182`.
2. `AG-XR-CP-001` merged via PR `#2179` and added additive Agora v1.4
   candidate-pool contract files. `AG-BE-CP-001` merged via PR `#2181` and
   implemented candidate-pool/research BFF routes. These are Agora research /
   candidate-pool surfaces, not the `AG-FE-ID-001` Phase 1 identity/servant
   status shell.
3. The identity and servant route files used by the parent shell did not
   change in this window: no diff was found for `services/control-plane/bff/agora/router.py`,
   `services/control-plane/bff/agora/servant`, `services/control-plane/bff/agora/identity`,
   `services/control-plane/bff/main.py`, or `docs/contracts/agora`.
4. Execute-plans PR `#66` remains open and `UNSTABLE` at head `d1ae3149`.
   The only surfaced check is `integration-gate`, still failed in run
   `27923882836`, job `82622466995`.
5. Execute-plans `dev` remains `ee835e2e`; comparing PR `#66` head against it
   shows only five files: `AgoraApp.tsx`, `identity.ts`, `identity.test.ts`,
   `servant.ts`, and `servant.test.ts`.
6. Execute-plans PR `#63` remains open and `UNSTABLE` with the older failed
   integration gate.
7. The Agora compatibility deployment gate remains fail-closed: compatibility
   is not compatible, the frontend runtime commit is a placeholder, and
   blocking reasons are non-empty.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`. The status wrapper reads the shared
status root at `/home/lupin/code/pantheon`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35` | active `review_approved`; owner `Codex`; reviewer `Claude` | Review is approved in `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35-REVIEW.md`; owner closeout remains PR merge plus `done`. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34` | archived `done` at `2026-06-22T01:59:00Z` | Previous support packet is the baseline for this refresh. |
| `AG-FE-ID-001` | active `blocked`; owner `Claude`; reviewer `Codex`; waiting for `Gemini` | Parent PR `#66` remains blocked by the execute-plans aggregate release gate, not by the Agora-specific shell/client code review. |
| `AG-FE-000` | archived `done` | Entry/build/audience split remains accepted dependency context. |
| `AG-BE-ID-003` | archived `done` | Servant-session backend facade remains available; parent must still prove frontend session client/UI readiness before enabling session controls. |
| `AG-XR-CP-001` | archived `done` | Candidate-pool contract is now on Pantheon dev as additive v1.4 context, but outside the parent status-shell acceptance slice. |
| `AG-BE-CP-001` | archived `done` | Candidate-pool BFF implementation is now on Pantheon dev, but outside the parent status-shell acceptance slice. |

Dependency honesty rule: `AG-FE-ID-001` may continue to rely on identity,
capability, servant ensure, and servant-session BFF routes as backend-available
facts. It still must not claim execute-plans dev deployment readiness until PR
`#66` merges or the release-gate blocker is explicitly dispositioned.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_35.md` | This sidecar's generated support-only assignment. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35` | Confirms active owner/reviewer/status and support-only artifact path. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35-REVIEW.md` | Confirms Claude approved the support packet and preserved the execute-plans PR `#66` gate blocker. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34` | Confirms predecessor archived `done`, PR `#2174`, and parent blocker note. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001` | Confirms parent remains `blocked`, waiting for `Gemini`, with PR `#66` aggregate-gate blocker. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-000` | Confirms frontend entry/build/audience dependency archived `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-ID-003` | Confirms servant-session BFF facade archived `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-CP-001` | Confirms candidate-pool v1.4 contract archived `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-CP-001` | Confirms candidate-pool BFF implementation archived `done`. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-34.md` | Previous AG-FE-ID-001 support baseline. |
| `git log --oneline 2a811d565f9f..origin/dev --decorate` | Shows Pantheon dev delta through PR `#2182`. |
| `git diff --name-status 2a811d565f9f..origin/dev -- <identity/servant pathset>` | Confirms no identity/servant/main/docs contract route changes. |
| `git diff --name-status 2a811d565f9f..origin/dev -- <research/candidate pathset>` | Shows additive candidate-pool contract and BFF implementation deltas. |
| `rg -n "@router\\.(get\\|post\\|delete)\\|ensure\\|sessions\\|reconcile\\|stream\\|candidate\\|research" ...` | Confirms identity, servant, and candidate-pool route families in current code. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | Confirms PR `#66` is `OPEN` / `UNSTABLE`; head `d1ae3149`; updated `2026-06-22T01:31:49Z`. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | Confirms `integration-gate` failed in run `27923882836`, job `82622466995`. |
| `gh api repos/ajoe734/execute-plans/git/ref/heads/dev --jq .object.sha` | Confirms execute-plans `dev` is `ee835e2e6f1037e612d7929279a11efb32c61975`. |
| `gh api repos/ajoe734/execute-plans/compare/ee835e2e...d1ae3149 --jq ...` | Confirms PR `#66` is three commits ahead and touches only the five AG-FE-ID-001 shell/client/test files. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | Confirms legacy compatibility PR remains `OPEN` / `UNSTABLE` with failed gate. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q` | `39 passed in 64.54s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_candidate_pool.py -q` | `3 passed in 27.15s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: compatibility not compatible, placeholder frontend runtime commit, blocking reasons non-empty. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-34 Closeout

Baseline: followup-34 merge target `2a811d565f9f`. Current Pantheon dev base:
`e01f19e7a4b7`.

| Change | What changed | Parent implication |
|---|---|---|
| Pantheon dev advanced | `origin/dev` moved from `2a811d565f9f` to `e01f19e7a4b7`. | Use `e01f19e7a4b7`, not followup-34, for current support facts. |
| AG-FE-RS-001 / AG-BE-TR-001 sidecars | PRs `#2176` and `#2177` merged separate support packets. | No AG-FE-ID-001 identity/servant implication. |
| AG-FE-DB-001B and review closeout | PRs `#2178` and `#2180` merged task brief/review support. | No AG-FE-ID-001 identity/servant implication. |
| AG-XR-CP-001 | PR `#2179` merged additive candidate-pool v1.4 OpenAPI/spec bundle. | This is new Agora contract context but outside the Phase 1 status shell. Do not use it to expand AG-FE-ID-001 UI. |
| AG-BE-CP-001 | PR `#2181` merged candidate-pool BFF routes into `services/control-plane/bff/agora/research/*`. | Candidate-pool routes are available BFF facts, but parent shell should keep them out of identity/servant status acceptance. |
| AG-FE-DB-002 sidecar | PR `#2182` merged acceptance followup support. | No AG-FE-ID-001 identity/servant implication. |
| Identity/servant checked paths | Diff over `agora/router.py`, `agora/servant`, `agora/identity`, `main.py`, and `docs/contracts/agora` produced no output. | Followup-34 identity/servant route ledger remains valid. |
| Research/candidate checked paths | Diff shows new candidate-pool contract/spec files and research router/store changes. | Packet must no longer say "no Agora path changes"; it must say "candidate-pool/research changed, identity/servant did not." |
| execute-plans PR `#66` | Still `OPEN` / `UNSTABLE`; head `d1ae3149`; failed `integration-gate`. | No merge-readiness improvement. Aggregate gate failure unchanged. |
| execute-plans PR `#63` | Still `OPEN` / `UNSTABLE`; head `e1cb9125`; failed `integration-gate`. | Continue to treat as unresolved legacy compatibility follow-through risk. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Frontend handoff rule |
|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover identity scope and servant policy context. No diff since followup-34. | Parent may use for identity readiness through strict BFF transport. Do not infer servant/session success from identity alone. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered capabilities. No diff since followup-34. | Parent may use for readiness/capability display. Keep route status distinct from research/workshop/candidate-pool capabilities. |
| `POST /bff/agora/servant/ensure` | Implemented in `services/control-plane/bff/agora/servant/router.py`; requires `Idempotency-Key` and `X-Request-Id`; returns a user-private `ServantProfile` envelope and maps OpenClaw sync failures to 503 dependency unavailable. No diff since followup-34. | `servant.ts` should send required headers, parse the envelope, and map 401/403/422/503 explicitly. |
| `POST /bff/agora/servant/sessions` | Implemented by `AG-BE-ID-003`; focused tests cover default `interactive`, explicit `trainer` and `research_task`, unknown type rejection, audit fields, no-authority context, and 201 create. No diff since followup-34. | Frontend may target this only with strict servant-session clients and UI tests. Do not use legacy `/bff/agora/sessions` for these modes. |
| `GET /bff/agora/servant/sessions/{session_id}` | Implemented; tests cover scoped detail response and audit fields. | Safe as the servant-session detail route after create. |
| `POST /bff/agora/servant/sessions/{session_id}/messages` | Implemented; tests cover message path and OpenClaw provider degradation. | Client must show degraded/error state without fabricating assistant success. |
| `POST /bff/agora/servant/sessions/{session_id}/terminate` | Implemented; tests cover terminate/cancel path and audit. | Use only for sessions created through the servant facade. |
| `GET /bff/agora/servant/sessions/{session_id}/stream` | Implemented; tests cover SSE stream events. | Can be used for session-scoped stream once frontend session UI is in scope. |
| `GET /bff/agora/servant` | No current servant sub-router handler identified. | Do not make the shell depend on this route. PR `#66` correctly removed this unsupported preflight per prior review. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler identified. | Keep out of the parent UI unless runtime support lands or reviewer records a disposition. |
| `GET/POST /bff/agora/sessions*` | Legacy routes still live outside the servant facade. | Do not treat as proof of `interactive`, `trainer`, or `research_task` servant-session readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface remains separate from the servant facade. | Do not use for parent servant-session controls unless explicitly reassigned. |
| `GET/POST /bff/agora/candidate-pools*` and nested member/score/discussion/monitoring routes | Newly available through AG-XR-CP-001 / AG-BE-CP-001. Focused candidate-pool tests pass. | Separate candidate-pool/research context. Do not fold into AG-FE-ID-001 Phase 1 identity/servant status shell or use it to add new widgets. |
| Workshop SSE stream | `AG-BE-SW-004` landed workshop aggregate streaming. | Separate Phase 2 workshop context; not AG-FE-ID-001 status-shell acceptance. |
| Research plan/run routes | `AG-BE-RS-001` and `AG-BE-RS-002` are archived `done`; research router now also carries candidate-pool routes. | Separate Phase 3/4 research UI/client scope. Do not fold into the identity/servant shell. |

Safe parent-shell facts remain: user-private identity scope, filtered capability
readiness, successful servant profile ensure through `/bff/agora/servant/ensure`,
and available backend servant-session routes. The frontend still needs PR-level
merge/gate evidence before operator-ready deployment claims.

## 6. Frontend Surface To Hand Off

Remote evidence source: GitHub execute-plans PR and compare APIs. No
execute-plans files were edited.

| Surface | execute-plans `dev` (`ee835e2e`) | PR `#66` head (`d1ae3149`) | Handoff implication |
|---|---|---|---|
| `src/agora/AgoraApp.tsx` | Missing from current diff base | Added | Parent shell exists only on PR `#66`. |
| `src/lib/bff-v1/agora/identity.ts` | Missing from current diff base | Added | PR branch has strict identity client for `/me` and `/capabilities`. |
| `src/lib/bff-v1/agora/identity.test.ts` | Missing from current diff base | Added | Focused identity tests are part of PR `#66`. |
| `src/lib/bff-v1/agora/servant.ts` | Missing from current diff base | Added | PR branch has strict servant ensure client using BFF transport and headers. |
| `src/lib/bff-v1/agora/servant.test.ts` | Missing from current diff base | Added | Focused servant tests are part of PR `#66`. |
| `src/lib/bff-v1/agora/types.ts` | Present on current `dev` | Not in current PR `#66` diff | Prior stale generated type concern remains resolved in the current PR diff. |

Current compare evidence: PR `#66` is three commits ahead of execute-plans
`dev` and touches only the five parent shell/client/test files listed above.

Observed PR `#66` review state from the latest Codex review note remains:

- Prior code-level blockers are resolved at `de7834b8` and retained at
  current head `d1ae3149`.
- `AgoraApp` calls identity readiness before servant ensure.
- Identity and servant clients use the strict BFF transport path.
- The unsupported `GET /bff/agora/servant` preflight is removed.
- Focused Agora checks were reported as passing by the parent review note.
- The aggregate release gate remains red on repo-wide release criteria outside
  the AG-FE-ID-001 code slice.

## 7. Execute-Plans PR Gate State

`gh pr checks 66 --repo ajoe734/execute-plans` reports:

| Check | State | Evidence |
|---|---|---|
| `integration-gate` | `fail` | Run `27923882836`, job `82622466995` |

`gh pr view 66` reports `OPEN` / `UNSTABLE`. The only surfaced status check is
the failed `integration-gate`. Head is `d1ae3149`; updated timestamp is
`2026-06-22T01:31:49Z`.

The release-gate ownership from followup-34 remains the current handoff model:

| Gate | Result | Owner | Notes |
|---|---|---|---|
| Gate 0 Preconditions | `PASS` | - | Run and deployment metadata present in prior release summary. |
| Gate 1 Static / Build / Unit | `FAIL` | Gemini | Lint and contract-test failures recorded in the release summary. |
| Gate 2 Contract Drift | `FAIL` | Codex | Six drift checks recorded in the release summary. |
| Gate 3 BFF Route Probes | `WARN` | Codex | One backend write-probe warning recorded. |
| Gate 4 Browser Frontend E2E | `PASS` | - | Hosted browser probe passed in the recorded run. |
| Gate 5 Playwright User Flows | `FAIL` | Codex | Two open checks recorded. |
| Gate 6 A11y / Perf | `FAIL` | Codex2 | Two open checks recorded. |
| Gate 7 Release Decision | `FAIL` | Codex | Aggregate release decision remains failed. |

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

Candidate-pool journey now available for later research/candidate UI scope:

```text
frontend creates or opens a candidate pool through /bff/agora/candidate-pools*
  -> BFF computes scores from the A2 recipe through candidate-pool endpoints
  -> member reviews, discussions, and monitoring remain research/candidate scope
  -> no broker order, RuntimeBinding, or capital binding authority is exposed
```

The frontend still must not expose Management, capital pool, broker order,
RuntimeBinding, live order controls, or candidate-pool widgets through the
AG-FE-ID-001 Phase 1 status shell.

## 9. Parent Absorption Checklist

Claude should not absorb this sidecar into parent completion unless the parent
evidence answers these checks:

| Check | Required evidence |
|---|---|
| Parent PR status | PR `#66` is merged into execute-plans `dev`, or parent remains blocked with the aggregate-gate blocker recorded. |
| Dev baseline freshness | Current PR `#66` diff against execute-plans `dev` remains limited to the five shell/client/test files and does not reintroduce `src/lib/bff-v1/agora/types.ts`. |
| Backend dependency | `AG-BE-ID-003` is treated as done, and frontend session work uses `/bff/agora/servant/sessions*`, not legacy sessions. |
| Identity route truth | `/me` and `/capabilities` are strict live calls; failures produce blocked states with no seed/mock fallback. |
| Servant ensure truth | `/servant/ensure` sends required idempotency/request headers and maps 401/403/422/503 without fabricating success. |
| Unsupported servant routes | Parent does not claim `GET /bff/agora/servant` or `POST /bff/agora/servant/reconcile` is runtime-supported unless backend adds it. |
| Session route family | Parent does not mix servant sessions, legacy sessions, and quick-ask sessions without explicit backend disposition. |
| Session frontend scope | If session controls are enabled, tests cover create/detail/message/stream/terminate plus degradation and audit semantics. |
| Candidate-pool separation | AG-XR-CP-001 / AG-BE-CP-001 candidate-pool routes are not silently absorbed into the Phase 1 identity/servant shell. |
| Research/workshop separation | AG-BE-SW and AG-BE-RS surfaces are not silently absorbed into the Phase 1 status shell. |
| Strict clients | Page components avoid direct route fetches; BFF clients preserve strict live semantics. |
| Bundle isolation | Agora shell tests or static checks prove no Management/runtime-binding/capital/broker/order code leaks into the app shell. |
| Compatibility honesty | Parent does not claim dev deployment readiness while PR `#66` is unmerged, PR `#63` remains open, or the compatibility manifest has placeholder frontend runtime commit. |
| Gate ownership | Aggregate gate failures are left with their recorded owners (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6: Codex2) rather than being buried in AG-FE-ID-001 closeout. |

## 10. Verification Performed For This Sidecar

| Command | Result |
|---|---|
| `git status -sb`; `git branch --show-current`; `git remote -v` | Started on expected branch `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35`; remote is `origin` `https://github.com/ajoe734/pantheon.git`; only generated task brief was untracked. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35` | Active `review_approved`; owner `Codex`; reviewer `Claude`; artifact path is this support packet; review file is recorded. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001` | Parent remains `blocked`, waiting for `Gemini`; PR `#66` aggregate release gate remains the blocker. |
| `git fetch origin --prune`; `git rev-parse origin/dev` | Pantheon `origin/dev` is `e01f19e7a4b73e7a70d0a8b607159e7db4192d6b`. |
| `git log --oneline 2a811d565f9f..origin/dev --decorate` | Dev delta includes PRs `#2176` through `#2182`. |
| `git diff --name-status 2a811d565f9f..origin/dev -- services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant services/control-plane/bff/agora/identity services/control-plane/bff/main.py docs/contracts/agora` | No output; identity/servant/main/docs-contract surfaces unchanged. |
| `git diff --name-status 2a811d565f9f..origin/dev -- services/control-plane/bff/agora/research services/control-plane/openapi services/control-plane/specs/agora` | Shows candidate-pool/research additions and changes from AG-XR-CP-001 / AG-BE-CP-001. |
| `gh pr view 66 --repo ajoe734/execute-plans --json state,mergeStateStatus,headRefOid,updatedAt,statusCheckRollup` | PR `#66` is `OPEN` / `UNSTABLE`; head `d1ae3149`; updated `2026-06-22T01:31:49Z`; `integration-gate` failed. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | `integration-gate fail 9m19s https://github.com/ajoe734/execute-plans/actions/runs/27923882836/job/82622466995`. |
| `gh api repos/ajoe734/execute-plans/git/ref/heads/dev --jq .object.sha` | execute-plans `dev` is `ee835e2e6f1037e612d7929279a11efb32c61975`. |
| `gh api repos/ajoe734/execute-plans/compare/ee835e2e...d1ae3149 --jq '.ahead_by, .behind_by, .files[].filename'` | `3` ahead, `0` behind; files are `AgoraApp.tsx`, `identity.test.ts`, `identity.ts`, `servant.test.ts`, `servant.ts`. |
| `gh pr view 63 --repo ajoe734/execute-plans --json state,mergeStateStatus,headRefOid,updatedAt,statusCheckRollup` | PR `#63` remains `OPEN` / `UNSTABLE`; head `e1cb9125`; updated `2026-06-20T16:53:49Z`; `integration-gate` failed. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q` | `39 passed in 64.54s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_candidate_pool.py -q` | `3 passed in 27.15s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: `compatibility_status must be compatible`, `frontend.runtime_commit is a placeholder commit`, `blocking_reasons must be empty for deployment`. |

## 11. Review And Owner Closeout

Claude approved this packet in
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35-REVIEW.md`.
The approval confirms the packet is support-only, keeps candidate-pool deltas
outside the `AG-FE-ID-001` Phase 1 identity/servant shell, and preserves
execute-plans PR `#66` as the parent aggregate-gate blocker.

Owner closeout remains mechanical: commit this approved task state, merge the
task PR into Pantheon `dev`, then run
`AI_NAME=Codex ./scripts/ai-status.sh done AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35
"Approved sidecar packet merged; parent AG-FE-ID-001 remains blocked on execute-plans PR #66 aggregate gate."`

*Prepared and finalized by Codex for the
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35` support slice on `2026-06-22`.*
