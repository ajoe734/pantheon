# AG-FE-ID-001 Followup-43 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-43` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-22` |
| Status | `review approved; owner closeout note captured` |
| Current Pantheon dev base | `61ec24785126cb8328396f36c2fe8fd567104896` |
| Previous packet closeout | Followup-42 archived `done` at `2026-06-22T07:10:33Z`; packet PR `#2216` merged at `fb665b4e`; review artifact PR `#2217` merged at `9e5d9816`; packet commit `4dd8129c`; review commit `49c3deae` |
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
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-42` closed and Pantheon `dev`
advanced from `9e5d9816` to `61ec2478`.

Material refresh findings:

1. Pantheon `origin/dev` advanced through AG-FE-RS-001 support, Management AI
   kernel repair, AG-BE-TR-002 implementation, AG-BE-TR-002 support, and
   AG-BE-TR-002 closeout merges.
2. The dev-window diff includes AG-BE-TR-002 trading-room runtime changes,
   auth identity extraction support, test updates, two task briefs, and
   adjacent support packets.
3. `services/control-plane/bff/main.py` changed in generic structured-token
   capability normalization. This touches shared BFF auth extraction, but does
   not add or alter the Agora identity/servant route contracts used by the
   parent shell.
4. `services/control-plane/bff/agora/router.py`,
   `services/control-plane/bff/agora/servant`,
   `services/control-plane/bff/agora/identity`, and `docs/contracts/agora`
   had no post-followup-42 diff.
5. `support/sidecars/AG-FE-ID-001/` had no post-followup-42 diff before this
   packet.
6. execute-plans PR `#66` remains open and `UNSTABLE` at head `d1ae3149`; the
   surfaced check remains `integration-gate`, failed in run `27923882836`, job
   `82622466995`.
7. execute-plans PR `#63` remains open and `UNSTABLE` with its older failed
   integration gate.
8. The Agora compatibility deployment gate remains fail-closed:
   compatibility is not compatible, the frontend runtime commit is a
   placeholder, and blocking reasons are non-empty.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex` and
`PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon` where shared live status state
was needed.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-43` | active `in_progress`; owner `Codex`; reviewer `Claude` | This packet is the only declared artifact and should go to Claude for review. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-42` | archived `done` at `2026-06-22T07:10:33Z` | Previous support packet and Claude review artifact are merged and form the baseline for this refresh. |
| `AG-FE-ID-001` | active `blocked`; owner `Claude`; reviewer `Codex`; waiting for `Gemini` | Parent PR `#66` remains blocked by the execute-plans aggregate release gate, not by Agora-specific shell/client code review. |
| `AG-FE-000` | archived `done` | Entry/build/audience split remains accepted dependency context. |
| `AG-BE-ID-003` | archived `done` | Servant-session backend facade remains available; parent still must prove frontend session client/UI readiness before enabling session controls. |
| `AG-BE-TR-002` | archived `done` at `2026-06-22T07:42:08Z` | Governed TradingIntent/handoff is now merged Phase 4 context. It must not expand the parent Phase 1 identity/servant status shell. |

Dependency honesty rule: `AG-FE-ID-001` may continue to rely on identity,
capability, servant ensure, and servant-session BFF routes as backend-available
facts. It still must not claim execute-plans dev deployment readiness until PR
`#66` merges or the release-gate blocker is explicitly dispositioned.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_43.md` | This sidecar's generated support-only assignment. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-43` | Confirms active owner/reviewer/status and support-only artifact path after `start`. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-42` | Confirms predecessor archived `done`, packet/review PRs, commits, and parent blocker note. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-42.md` | Previous AG-FE-ID-001 support baseline. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-42-REVIEW.md` | Confirms Claude approved the prior support packet and preserved the execute-plans PR `#66` gate blocker. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent remains `blocked`, waiting for `Gemini`, with PR `#66` aggregate-gate blocker. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-000`, `AG-BE-ID-003`, and `AG-BE-TR-002` | Confirms parent dependencies remain done and AG-BE-TR-002 is newly archived done. |
| `git fetch origin --prune`; `git rev-parse origin/dev` | Confirms current Pantheon dev base `61ec24785126cb8328396f36c2fe8fd567104896`. |
| `git log --oneline --decorate 9e5d9816..origin/dev` | Confirms post-followup-42 dev advancement through PRs `#2218`, `#2219`, `#2220`, `#2221`, and `#2222`. |
| `git diff --name-status 9e5d9816..origin/dev` | Confirms exact file list in the post-followup-42 dev window. |
| `git diff --name-status 9e5d9816..origin/dev -- services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant services/control-plane/bff/agora/identity services/control-plane/bff/main.py docs/contracts/agora` | Shows only `services/control-plane/bff/main.py` changed in the checked identity/servant pathset. |
| `git diff --unified=3 9e5d9816..origin/dev -- services/control-plane/bff/main.py` | Confirms the `main.py` change is structured-token capability normalization. |
| `git diff --name-status 9e5d9816..origin/dev -- support/sidecars/AG-FE-ID-001` | No output; no newer AG-FE-ID-001 support file supersedes followup-42 before this packet. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | Confirms PR `#66` is `OPEN` / `UNSTABLE`; head `d1ae3149`; updated `2026-06-22T01:31:49Z`. |
| `gh pr checks 66 --repo ajoe734/execute-plans` | Confirms `integration-gate` failed in run `27923882836`, job `82622466995`. |
| `gh api repos/ajoe734/execute-plans/git/ref/heads/dev --jq .object.sha` | Confirms execute-plans `dev` is `ee835e2e6f1037e612d7929279a11efb32c61975`. |
| `gh api repos/ajoe734/execute-plans/compare/ee835e2e...d1ae3149 --jq ...` | Confirms PR `#66` is three commits ahead, zero behind, and touches only the five AG-FE-ID-001 shell/client/test files. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | Confirms legacy compatibility PR remains `OPEN` / `UNSTABLE` with failed gate. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q` | `39 passed in 37.72s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_candidate_pool.py -q` | `3 passed in 16.27s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/agora/trading_room/test_trading_room.py -q` | `31 passed in 16.99s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py services/control-plane/bff/test_bff_session_auth_me_contract.py services/test_structured_token_mfa_suffix.py -q` | `96 passed in 55.58s`. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Expected fail-closed: `compatibility_status must be compatible`, `frontend.runtime_commit is a placeholder commit`, and `blocking_reasons must be empty for deployment`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-42 Closeout

Baseline: followup-42 review artifact merge target `9e5d9816`. Current Pantheon
dev base: `61ec2478`.

| Change | What changed | Parent implication |
|---|---|---|
| AG-FE-RS-001 support | PR `#2218` added `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.md`. | Adjacent research-card frontend handoff only. No parent `AG-FE-ID-001` identity/servant readiness change. |
| Management AI kernel repair | PR `#2219` restored kernel activation wiring. | Management AI/dev-kernel surface only. Do not route AG-FE-ID-001 through Management controls. |
| AG-BE-TR-002 implementation | PR `#2220` implemented governed TradingIntent/handoff routes; PR `#2222` finalized the approved task. | Phase 4 trading-room context is now available, but it is outside the Phase 1 identity/servant status shell. |
| AG-BE-TR-002 support | PR `#2221` added AG-BE-TR-002 followup support material. | Adjacent support packet; no AG-FE-ID-001 source or parent-shell contract change. |
| Generic BFF auth | `services/control-plane/bff/main.py` now normalizes structured JWT `capability`/`capabilities` claims through `_stub_identity_capabilities`. | Parent identity client should keep trusting BFF-filtered `/me` and `/capabilities` results. No frontend schema or route expansion is implied. |
| Identity/servant package paths | `agora/router.py`, `agora/servant`, `agora/identity`, and `docs/contracts/agora` produced no diff. | Followup-42 identity/servant route ledger remains valid, with the generic auth normalization caveat above. |
| AG-FE-ID-001 support folder | Diff over `support/sidecars/AG-FE-ID-001` produced no output. | No newer AG-FE-ID-001 packet supersedes followup-42 before this packet. |
| execute-plans PR `#66` | Still `OPEN` / `UNSTABLE`; head `d1ae3149`; failed `integration-gate`. | No merge-readiness improvement. Aggregate gate failure unchanged. |
| execute-plans PR `#63` | Still `OPEN` / `UNSTABLE`; head `e1cb9125`; failed `integration-gate`. | Continue to treat as unresolved legacy compatibility follow-through risk. |
| Compatibility manifest | Deployment gate still exits fail-closed with the same three blocker classes. | Parent must not claim deployment readiness. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Frontend handoff rule |
|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; no package-route diff since followup-42. Shared auth extraction now normalizes structured-token capabilities. | Parent may use for identity readiness through strict BFF transport. Do not infer servant/session success from identity alone, and do not expand capabilities client-side. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; no route diff since followup-42. | Parent may use for readiness/capability display. Keep route status distinct from research/workshop/candidate/trading capabilities. |
| `POST /bff/agora/servant/ensure` | Implemented in `services/control-plane/bff/agora/servant/router.py`; no diff since followup-42. Requires request/idempotency discipline and returns user-private servant profile envelope. | `servant.ts` should send required headers, parse the envelope, and map auth/validation/dependency errors explicitly. |
| `POST /bff/agora/servant/sessions` | Implemented by `AG-BE-ID-003`; focused tests still pass. | Frontend may target this only with strict servant-session clients and UI tests. Do not use legacy `/bff/agora/sessions` for these modes. |
| `GET /bff/agora/servant/sessions/{session_id}` | Implemented; scoped detail response remains covered by the 39-test focused suite. | Safe as the servant-session detail route after create. |
| `POST /bff/agora/servant/sessions/{session_id}/messages` | Implemented; OpenClaw provider degradation remains covered by focused tests. | Client must show degraded/error state without fabricating assistant success. |
| `POST /bff/agora/servant/sessions/{session_id}/terminate` | Implemented; terminate/cancel path remains covered by focused tests. | Use only for sessions created through the servant facade. |
| `GET /bff/agora/servant/sessions/{session_id}/stream` | Implemented; SSE stream remains covered by focused tests. | Can be used for session-scoped stream once frontend session UI is in scope. |
| `GET /bff/agora/servant` | No current servant sub-router handler identified. | Do not make the shell depend on this route. PR `#66` already removed this unsupported preflight. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler identified. | Keep out of the parent UI unless runtime support lands or reviewer records a disposition. |
| `GET/POST /bff/agora/sessions*` | Legacy routes still live outside the servant facade. | Do not treat as proof of `interactive`, `trainer`, or `research_task` servant-session readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface remains separate from the servant facade. | Do not use for parent servant-session controls unless explicitly reassigned. |
| `GET/POST /bff/agora/candidate-pools*` and nested member/score/discussion/monitoring routes | Available through AG-XR-CP-001 / AG-BE-CP-001. Focused candidate-pool tests pass. | Separate candidate-pool/research context. Do not fold into AG-FE-ID-001 Phase 1 identity/servant status shell or use it to add new widgets. |
| `GET /bff/agora/trading-room`, strategy detail, decision-event routes, and stream | Available through AG-BE-TR-001 / AG-BE-TR-002 lineage. Focused trading-room tests now pass `31/31`. | Separate Phase 4 trading-room context. Do not expose trading-room queues, trader decisions, or strategy widgets through the parent Phase 1 shell. |
| `GET /bff/agora/trading-intents/{intent_id}` | Implemented by AG-BE-TR-002 as governed TradingIntent detail. | Separate Phase 4 route. Do not add intent detail UI to AG-FE-ID-001. |
| `POST /bff/agora/trading-intents/{intent_id}/handoffs` | Implemented by AG-BE-TR-002 as request-only governed handoff; no broker/order route. | Separate Phase 4 route. Parent shell must not expose canary/live/promotion handoff controls. |
| `POST /bff/agora/trading-intents/{intent_id}/withdraw` | Implemented by AG-BE-TR-002 as record withdrawal; no live execution cancellation. | Separate Phase 4 route. Parent shell must not expose withdrawal/order controls. |
| Workshop SSE stream | `AG-BE-SW-004` landed workshop aggregate streaming. | Separate Phase 2 workshop context; not AG-FE-ID-001 status-shell acceptance. |
| Research plan/run routes | `AG-BE-RS-001` and `AG-BE-RS-002` are archived `done`; AG-FE-RS-001 followup-8 adds only frontend handoff guidance. | Separate Phase 3 research UI/client scope. Do not fold into the identity/servant shell. |

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

The release-gate ownership from followup-42 remains the current handoff model:

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

Newly merged Phase 4 trading intent routes do not change this journey. The
frontend still must not expose Management, capital pool, broker order,
RuntimeBinding, live order controls, candidate-pool widgets, research widgets,
workshop widgets, trading-room widgets, or governed TradingIntent/handoff
controls through the AG-FE-ID-001 Phase 1 status shell.

## 9. Parent Absorption Checklist

Before parent owner closeout, keep these checks explicit:

| Gate | Required evidence |
|---|---|
| Identity readiness | `identity.ts` calls `/bff/agora/me` and `/bff/agora/capabilities` through strict BFF transport and handles auth/audience errors. |
| Structured capability handling | Frontend renders BFF-returned capability facts; it does not derive or expand capability allowlists locally from token claims. |
| Servant ensure | `servant.ts` sends required request/idempotency headers, parses the servant profile envelope, and handles auth/validation/dependency errors. |
| Servant-session boundary | Session controls stay skeleton/disabled unless strict `/bff/agora/servant/sessions*` clients and UI tests are in scope. |
| Unsupported servant routes | `GET /bff/agora/servant` and `POST /bff/agora/servant/reconcile` remain excluded unless runtime support lands. |
| No Management leakage | Agora bundle/source does not import Management routes/components or expose capital, RuntimeBinding, broker, or live-order controls. |
| No Phase 3/4 expansion | Candidate-pool, research, workshop, trading-room, and TradingIntent/handoff routes remain outside AG-FE-ID-001 Phase 1. |
| Frontend PR gate | execute-plans PR `#66` merges or an explicit gate exception is recorded. |
| Compatibility gate | Dev compatibility manifest records a non-placeholder frontend runtime commit, `compatibility_status: compatible`, and no blocking reasons. |

## 10. Reviewer Focus

Claude should review this packet for:

1. scope discipline: only this support file should be committed for the packet;
2. factual accuracy of the `9e5d9816..61ec2478` dev-window summary;
3. correct treatment of the `main.py` auth-capability change as shared BFF auth
   context, not a new parent frontend contract;
4. correct carry-forward of execute-plans PR `#66` and PR `#63` unstable gate
   blockers;
5. correct exclusion of newly merged AG-BE-TR-002 Phase 4 TradingIntent/handoff
   routes from the AG-FE-ID-001 Phase 1 operator journey.

Expected reviewer disposition if accurate: approve the support packet and hand
back to Codex for task closeout. The parent should remain blocked on the
execute-plans aggregate gate until that gate clears or is formally exceptioned.

## 11. Owner Closeout Note

Claude approved this packet in
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-43-REVIEW.md`.
The packet PR `#2223` merged at `4cdd90e5`, and the review artifact PR `#2224`
merged at `6a6987ab`. This note only records owner finalization context for the
support sidecar; it does not broaden the packet, promote canonical truth, or
change the parent dependency model.

Closeout still preserves the parent blocker: `AG-FE-ID-001` remains blocked on
the execute-plans PR `#66` aggregate gate until that gate merges cleanly or a
formal exception is recorded.
