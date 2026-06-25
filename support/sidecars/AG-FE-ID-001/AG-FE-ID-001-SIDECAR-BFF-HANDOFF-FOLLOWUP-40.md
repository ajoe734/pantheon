# AG-FE-ID-001 Followup-40 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-40` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-22` |
| Status | `review approved; owner closeout recorded` |
| Current Pantheon dev base | `a93bdb71570a8a8b818e7e19bf9541aec2a97c9d` |
| Previous packet closeout | Followup-39 archived `done` at `2026-06-22T05:03:48Z`; packet PR `#2202` merged at `977887d6dba69303a91d61ddf485203ea21ed5fc`; review artifact PR `#2203` merged at `246e41f6fec9de5ffc8d0ed6297f87f623321922`; closeout PR `#2204` merged at `dced92d4af76d5049415effea60ff5ec3b3802a2`; packet commit `e0ee7059`; review commit `5f125a16`; closeout commit `81f000f3` |
| Parent implementation PR | execute-plans PR `#66`, `OPEN` / `UNSTABLE`, head `d1ae3149935986782993a363b92227d38555cc1b`, updated `2026-06-22T01:31:49Z`; `integration-gate` failed |
| execute-plans dev base | `ee835e2e6f1037e612d7929279a11efb32c61975` |
| Legacy compatibility PR | execute-plans PR `#63`, `OPEN` / `UNSTABLE`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`, updated `2026-06-20T16:53:49Z`; `integration-gate` failed |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-FE-ID-001` BFF/frontend handoff after
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-39` closed and Pantheon `dev`
advanced from `dced92d4` to `a93bdb71`.

Material changes since followup-39 closeout:

1. Pantheon `origin/dev` advanced by three support-only sidecar packet merges:
   `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`,
   `AG-FE-SW-001-SIDECAR-BFF-HANDOFF`, and
   `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`.
2. The checked dev-window diff only adds support packet files under
   `support/sidecars/AG-FE-RS-001/` and `support/sidecars/AG-FE-SW-001/`.
3. No identity/servant parent-shell source or contract path changed:
   `services/control-plane/bff/agora/router.py`,
   `services/control-plane/bff/agora/servant`,
   `services/control-plane/bff/agora/identity`,
   `services/control-plane/bff/main.py`, and `docs/contracts/agora` produced no
   diff since followup-39 closeout.
4. No Agora BFF route path changed since followup-39 closeout.
5. execute-plans PR `#66` remains open and `UNSTABLE` at head `d1ae3149`.
   The only surfaced check remains `integration-gate`, failed in run
   `27923882836`, job `82622466995`.
6. execute-plans `dev` remains `ee835e2e`; comparing PR `#66` head against it
   still shows three commits ahead, zero behind, and only five files:
   `AgoraApp.tsx`, `identity.ts`, `identity.test.ts`, `servant.ts`, and
   `servant.test.ts`.
7. execute-plans PR `#63` remains open and `UNSTABLE` with its older failed
   integration gate.
8. The Agora compatibility deployment gate remains fail-closed: compatibility
   is not compatible, the frontend runtime commit is a placeholder, and
   blocking reasons are non-empty.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex` and the shared
`PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-40` | active `in_progress`; owner `Codex`; reviewer `Claude` | This packet is the only declared artifact and should go to Claude for review. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-39` | archived `done` at `2026-06-22T05:03:48Z` | Previous support packet, review artifact, and owner closeout are merged and are the baseline for this refresh. |
| `AG-FE-ID-001` | active `blocked`; owner `Claude`; reviewer `Codex`; waiting for `Gemini` | Parent PR `#66` remains blocked by the execute-plans aggregate release gate, not by the Agora-specific shell/client code review. |
| `AG-FE-000` | archived `done` | Entry/build/audience split remains accepted dependency context. |
| `AG-BE-ID-003` | archived `done` | Servant-session backend facade remains available; parent still must prove frontend session client/UI readiness before enabling session controls. |
| `AG-XR-CP-001` | archived `done` | Candidate-pool v1.4 contract remains additive Phase 4 context, outside the parent status-shell acceptance slice. |
| `AG-BE-CP-001` | archived `done` | Candidate-pool BFF implementation remains available Phase 4 context, outside the parent status-shell acceptance slice. |
| `AG-BE-TR-001` | archived `done` | Trading-room aggregate and decision-event queue routes remain available Phase 4 context and must not expand the parent Phase 1 shell. |

Dependency honesty rule: `AG-FE-ID-001` may continue to rely on identity,
capability, servant ensure, and servant-session BFF routes as backend-available
facts. It still must not claim execute-plans dev deployment readiness until PR
`#66` merges or the release-gate blocker is explicitly dispositioned.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_40.md` | This sidecar's generated support-only assignment. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-40` | Confirms active owner/reviewer/status and support-only artifact path. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-39` | Confirms predecessor archived `done`, packet/review/closeout PRs, packet/review/closeout commits, and parent blocker note. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-39.md` | Previous AG-FE-ID-001 support baseline and owner closeout record. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-39-REVIEW.md` | Confirms Claude approved the prior support packet and preserved the execute-plans PR `#66` gate blocker. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent remains `blocked`, waiting for `Gemini`, with PR `#66` aggregate-gate blocker. |
| `git fetch origin --prune`; `git rev-parse origin/dev` | Confirms current Pantheon dev base `a93bdb71570a8a8b818e7e19bf9541aec2a97c9d`. |
| `git log --oneline dced92d4..origin/dev --decorate` | Confirms three support-only sidecar packet merges after followup-39 closeout. |
| `git diff --name-status dced92d4..origin/dev -- <identity/servant pathset>` | No output; identity/servant/main/docs contract surfaces unchanged. |
| `git diff --name-status dced92d4..origin/dev -- services/control-plane/bff/agora` | No output; no Agora BFF route source changed since followup-39 closeout. |
| `git diff --name-status dced92d4..origin/dev -- support/sidecars` | Confirms only AG-FE-RS-001 and AG-FE-SW-001 support packet files were added. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | Confirms PR `#66` is `OPEN` / `UNSTABLE`; head `d1ae3149`; updated `2026-06-22T01:31:49Z`. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | Confirms `integration-gate` failed in run `27923882836`, job `82622466995`. |
| `gh api repos/ajoe734/execute-plans/git/ref/heads/dev --jq .object.sha` | Confirms execute-plans `dev` is `ee835e2e6f1037e612d7929279a11efb32c61975`. |
| `gh api repos/ajoe734/execute-plans/compare/ee835e2e...d1ae3149 --jq ...` | Confirms PR `#66` is three commits ahead, zero behind, and touches only the five AG-FE-ID-001 shell/client/test files. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | Confirms legacy compatibility PR remains `OPEN` / `UNSTABLE` with failed gate. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q` | `39 passed in 25.73s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_candidate_pool.py -q` | `3 passed in 10.99s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/agora/trading_room/test_trading_room.py -q` | `24 passed in 6.69s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: `compatibility_status must be compatible`, `frontend.runtime_commit is a placeholder commit`, and `blocking_reasons must be empty for deployment`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-39 Closeout

Baseline: followup-39 closeout merge target `dced92d4`. Current Pantheon dev
base: `a93bdb71`.

| Change | What changed | Parent implication |
|---|---|---|
| Pantheon dev advanced | PR `#2205` added `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md`; PR `#2206` added `AG-FE-SW-001-SIDECAR-BFF-HANDOFF.md`; PR `#2207` added `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`. | These are adjacent support packets only. They do not change parent `AG-FE-ID-001` runtime readiness. |
| Identity/servant checked paths | Diff over `agora/router.py`, `agora/servant`, `agora/identity`, `main.py`, and `docs/contracts/agora` produced no output. | Followup-39 identity/servant route ledger remains valid. |
| Agora BFF checked paths | Diff over `services/control-plane/bff/agora` produced no output. | No new route surface has landed since followup-39 closeout. |
| execute-plans PR `#66` | Still `OPEN` / `UNSTABLE`; head `d1ae3149`; failed `integration-gate`. | No merge-readiness improvement. Aggregate gate failure unchanged. |
| execute-plans PR `#63` | Still `OPEN` / `UNSTABLE`; head `e1cb9125`; failed `integration-gate`. | Continue to treat as unresolved legacy compatibility follow-through risk. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Frontend handoff rule |
|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover identity scope and servant policy context. No diff since followup-39. | Parent may use for identity readiness through strict BFF transport. Do not infer servant/session success from identity alone. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered capabilities. No diff since followup-39. | Parent may use for readiness/capability display. Keep route status distinct from research/workshop/candidate/trading capabilities. |
| `POST /bff/agora/servant/ensure` | Implemented in `services/control-plane/bff/agora/servant/router.py`; requires `Idempotency-Key` and `X-Request-Id`; returns a user-private `ServantProfile` envelope and maps OpenClaw sync failures to 503 dependency unavailable. No diff since followup-39. | `servant.ts` should send required headers, parse the envelope, and map 401/403/422/503 explicitly. |
| `POST /bff/agora/servant/sessions` | Implemented by `AG-BE-ID-003`; focused tests cover default `interactive`, explicit `trainer` and `research_task`, unknown type rejection, audit fields, no-authority context, and 201 create. No diff since followup-39. | Frontend may target this only with strict servant-session clients and UI tests. Do not use legacy `/bff/agora/sessions` for these modes. |
| `GET /bff/agora/servant/sessions/{session_id}` | Implemented; tests cover scoped detail response and audit fields. | Safe as the servant-session detail route after create. |
| `POST /bff/agora/servant/sessions/{session_id}/messages` | Implemented; tests cover message path and OpenClaw provider degradation. | Client must show degraded/error state without fabricating assistant success. |
| `POST /bff/agora/servant/sessions/{session_id}/terminate` | Implemented; tests cover terminate/cancel path and audit. | Use only for sessions created through the servant facade. |
| `GET /bff/agora/servant/sessions/{session_id}/stream` | Implemented; tests cover SSE stream events. | Can be used for session-scoped stream once frontend session UI is in scope. |
| `GET /bff/agora/servant` | No current servant sub-router handler identified. | Do not make the shell depend on this route. PR `#66` correctly removed this unsupported preflight per prior review. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler identified. | Keep out of the parent UI unless runtime support lands or reviewer records a disposition. |
| `GET/POST /bff/agora/sessions*` | Legacy routes still live outside the servant facade. | Do not treat as proof of `interactive`, `trainer`, or `research_task` servant-session readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface remains separate from the servant facade. | Do not use for parent servant-session controls unless explicitly reassigned. |
| `GET/POST /bff/agora/candidate-pools*` and nested member/score/discussion/monitoring routes | Available through AG-XR-CP-001 / AG-BE-CP-001. Focused candidate-pool tests pass. | Separate candidate-pool/research context. Do not fold into AG-FE-ID-001 Phase 1 identity/servant status shell or use it to add new widgets. |
| `GET /bff/agora/trading-room`, `GET /bff/agora/trading-room/strategies/{strategy_id}`, decision-event routes, stream, and governed trading-intent stubs | Available through AG-BE-TR-001. Focused trading-room tests pass. Route comments and tests preserve decision-support/no-order-route framing. No diff since followup-39. | Separate Phase 4 trading-room context. Do not expose trading-room queues, trader decisions, governed intent handoff, withdraw, order, capital, or RuntimeBinding controls through the parent Phase 1 shell. |
| Workshop SSE stream | `AG-BE-SW-004` landed workshop aggregate streaming. | Separate Phase 2 workshop context; not AG-FE-ID-001 status-shell acceptance. |
| Research plan/run routes | `AG-BE-RS-001` and `AG-BE-RS-002` are archived `done`; research router also carries candidate-pool routes. | Separate Phase 3/4 research UI/client scope. Do not fold into the identity/servant shell. |

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
`dev`, zero behind, and touches only the five parent shell/client/test files
listed above.

Observed PR `#66` review state from the latest Codex review note remains:

- Prior code-level blockers are resolved at `de7834b8` and retained at current
  head `d1ae3149`.
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

The release-gate ownership from followup-39 remains the current handoff model:

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

Candidate-pool journey available for later research/candidate UI scope:

```text
frontend creates or opens a candidate pool through /bff/agora/candidate-pools*
  -> BFF computes scores from the A2 recipe through candidate-pool endpoints
  -> member reviews, discussions, and monitoring remain research/candidate scope
  -> no broker order, RuntimeBinding, or capital binding authority is exposed
```

Trading-room journey available for later Phase 4 decision-support UI scope:

```text
frontend opens /bff/agora/trading-room decision-support views
  -> BFF lists strategy and decision-event queues
  -> trader decisions remain support records with no_order_route_proof
  -> governed intent handoff/withdraw stubs remain AG-BE-TR-002 territory
  -> no broker order, RuntimeBinding, capital binding, or direct order route is exposed
```

The frontend still must not expose Management, capital pool, broker order,
RuntimeBinding, live order controls, candidate-pool widgets, or trading-room
widgets through the AG-FE-ID-001 Phase 1 status shell.

## 9. Parent Absorption Checklist

Before parent owner closeout, keep these checks explicit:

| Check | Required disposition |
|---|---|
| PR `#66` merge readiness | execute-plans PR `#66` must merge into execute-plans `dev`, or the parent must record a formal exception for the aggregate gate. |
| Compatibility manifest | `docs/contracts/agora/dev-compatibility-manifest.json` must stop using a placeholder frontend runtime commit and must report `compatibility_status: compatible` with empty blocking reasons before deployment readiness is claimed. |
| Gate ownership | Gate 1 remains Gemini-owned; Gate 2/5/7 remain Codex-owned; Gate 6 remains Codex2-owned unless reassigned in status. |
| Identity/servant shell scope | Parent may absorb identity, capability, servant ensure, and no-authority status facts; it must not absorb candidate-pool, trading-room, research, or workshop UI into the Phase 1 shell. |
| Unsupported servant routes | Do not add `GET /bff/agora/servant` or `POST /bff/agora/servant/reconcile` client dependencies without runtime support and review disposition. |
| Direct fetch / strict mode | Page components must use strict `src/lib/bff-v1/agora/*` clients and no page-level direct `fetch` or seed/mock fallback in live mode. |
| No Management leakage | Agora bundle/source must not import Management routes/components or expose capital, broker order, RuntimeBinding, or live-order controls. |

## 10. Reviewer Focus For Claude

Suggested review questions:

1. Confirm this packet is support-only and only claims facts rechecked in the
   followup-39 closeout to followup-40 refresh window.
2. Confirm Pantheon dev advancement after followup-39 is limited to AG-FE-RS
   and AG-FE-SW support packet files.
3. Confirm no identity/servant/Agora BFF path changed since followup-39.
4. Confirm execute-plans PR `#66` and PR `#63` remain open/unstable with the
   aggregate gate blocker preserved.
5. Confirm the parent absorption checklist does not silently close gate owner
   responsibilities or broaden the Phase 1 shell.

## 11. Suggested Handoff Message

```text
FOLLOWUP-40 support packet updates AG-FE-ID-001 handoff after followup-39
closed at Pantheon dev dced92d4. Current Pantheon dev is a93bdb71; the only
new dev-window changes are AG-FE-RS/AG-FE-SW support packet files. Identity,
servant, and Agora BFF route paths are unchanged. execute-plans PR #66 remains
OPEN/UNSTABLE on integration-gate, PR #63 remains OPEN/UNSTABLE, and the Agora
compatibility manifest remains fail-closed. Backend-focused validation passed:
39 identity/servant/session tests, 3 candidate-pool tests, and 24 trading-room
tests. Please review scope discipline and factual accuracy before parent
absorption.
```

## 12. Owner Closeout Record

Closeout recorded by Codex after Claude review approval.

| Item | Result |
|---|---|
| Packet PR | Pantheon PR `#2208` merged at `ea209f34975063fa7c73350babc212c747d292ed`; packet commit `2b3f1f329577e2e0e5c8c9d85ab1c9b1e03185fe`. |
| Review artifact PR | Pantheon PR `#2209` merged at `c885402f4175be1af563584ab54eb47d74c3986e`; review commit `2034868edc92c83460e10ba8ffb660c16f80a056`. |
| Owner finalization scope | This section only records closeout metadata for the sidecar support packet. |
| Canonical/runtime impact | None. No L1 truth, BFF runtime, route registry, governance, compatibility manifest, or execute-plans source changed. |
| Parent carry-forward | `AG-FE-ID-001` remains blocked on execute-plans PR `#66` / aggregate gate disposition; legacy PR `#63` and compatibility manifest blockers remain unresolved. |
