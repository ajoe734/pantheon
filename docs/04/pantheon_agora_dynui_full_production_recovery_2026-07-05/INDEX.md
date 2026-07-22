# Agora DYNUI Full Production Recovery - 2026-07-05

Status: production recovery packet; not a completion certificate.

## Why This Exists

The previous `AG-DYNUI-PROD-001` through `AG-DYNUI-PROD-006` fleet wave
restored important Agora pieces, but the closeout evidence does not prove the
full live dynamic UI product. The task board archived those tasks as `done`;
current hosted/live evidence contradicts a production-complete reading.

This packet reopens the remaining work as new follow-up execution tasks instead
of reusing archived task IDs.

## Routing Repair

Updated on 2026-07-05 after live task-board inspection and post-merge live
BFF verification:

- `AG-DYNUI-FULL-001` is done and archived as the source-truth/parity-matrix
  task; redispatch tooling must skip it instead of re-creating it.
- Claude and Claude2 are exhausted/unavailable for the remaining mainline
  recovery path.
- Antigravity, Antigravity2, and Copilot are disabled in durable supervisor
  config for this recovery path.
- Gemini and Gemini2 are rejected by the supervisor mainline guard for this
  wave as disabled, sidecar-only, or auth-down.
- Underutilization sidecar dispatch is configured to exclude exhausted or
  disabled lanes, so support slices do not consume unavailable workers.
- Remaining executable work is routed through Codex/Codex2 only. Codex2 may be
  quota-paused; if so, tasks may proceed with Codex implementation but cannot
  close production gates without recorded review evidence.

## Current Live Evidence

Captured on 2026-07-05 after merging and deploying:

- Pantheon PR #3020, merge `9a7d1f3260767585962bf2a673437ae85318d494`,
  added Trading Room projection from Workshop readiness.
- Pantheon PR #3021, merge `96d6a7288061047ceca7b911843555d6296d8425`,
  switched the live dev Strategy Workshop store to Postgres.
- Pantheon PR #3022, merge `aab5c301f7ba5e5872e9f0f5b195832be34acbeb`,
  disabled exhausted or no-op fleet lanes in supervisor config.
- Pantheon PR #3023, merge `532332a949a15e770285055c013b1f19adf767f7`,
  fixed Codex CLI resolution for task worktrees.

Direct dev BFF proof on `127.0.0.1:18001`:

- `GET /bff/agora/workshops/ce63ec2a-c5f1-4e41-8219-e410d22037c7/readiness`
  returns `200` for `tenant:pantheon-dev:user:pantheon-dev-browser`.
- `GET /bff/agora/workshops/ce63ec2a-c5f1-4e41-8219-e410d22037c7/cards`
  returns `200` and includes the user strategy description card plus the
  readiness gate card.
- The same new browser-scoped workshop has
  `highest_ready_gate = null`, with blockers for missing completeness state,
  missing Strategy Registry reference, and full-validation readiness.
- `GET /bff/agora/trading-room` still returns `strategies: []` for the
  browser-scoped user. The non-empty strategy proof exists only for the
  SQL-seeded `agora-test-user` workshop
  `d237eb8f-44a6-4805-9b27-d5723f8c99eb`, so it is useful backend proof but
  not a full hosted UI/API E2E proof.
- Cross-user reads correctly return `403 CROSS_USER_ACCESS_FORBIDDEN`, proving
  the scoped route boundary is active.

Execute-plans frontend status:

- `AG-DYNUI-FULL-004` merged execute-plans PR #185
  (`https://github.com/ajoe734/execute-plans/pull/185`) from reviewed head
  `722cb18fd6d5e5e33b2c4e4866c72bcdd17a8571` into merge commit
  `4cce2d10f14abcc7af5f15638e0e0efa63885944`.
- Its FE-BFF `integration-gate` check succeeded:
  `https://github.com/ajoe734/execute-plans/actions/runs/28744994745/job/85234274266`
- Its dev FE deploy succeeded:
  `https://github.com/ajoe734/execute-plans/actions/runs/28745373659`
- Hosted evidence is recorded at
  `docs/deployment/evidence/ag-dynui-full-004/20260705T152800Z/`.
- The closeout is frontend handoff partial because the live browser-scoped BFF
  still returns `strategies: []`, and the hosted Strategy Workshop CTA remains
  disabled until readiness reaches `trading_room`.

Previous `AG-DYNUI-PROD-006` evidence also disclosed that the hosted E2E
proposal/accept/grid/revision/version/rollback path was exercised with
`page.route()` fixtures because live dev had zero ready strategies. That is
useful diagnostic evidence, but it is not a production-level live E2E proof.

## Work Inventory

This is the current truth split. "Production-proven" means merged, deployed
where relevant, and proven against hosted dev/live BFF without test fixtures.
"Partial" means useful implementation exists but cannot close product
readiness alone.

| Area | Current status | Evidence | Remaining production gap |
|---|---|---|---|
| Trading Room BFF default read | Production-proven for availability only | Direct dev BFF returns `200` for `/bff/agora/trading-room`. | Browser-scoped aggregate still returns `strategies: []`; hosted UI workflow is not proven. |
| Trading Room decision events read | Production-proven for availability only | Post-deploy curl returns `200` for `/bff/agora/trading-room/decision-events`. | Does not prove strategy creation, workspace mutation, or readiness handoff. |
| Strict auth/header path for hosted FE | Partial/building block | Hosted FE can call the dev BFF with dev browser auth and no longer fails the Trading Room load on the default route. | Needs full workshop-to-trading-room flow under the same auth path. |
| Standalone Agora shell/navigation | Partial/building block | Prior `AG-DYNUI-PROD-002/003/004` work changed the shell and default route behavior. | User still observed embedded/shell mismatch; visual parity cannot be closed until canonical design source is recovered and inspected. |
| Dynamic workspace frontend components | Partial/building block | Execute-plans PR #176 wired proposal preview, grid editor, widget revision drawer, version history, and rollback into `TradingRoomPage.tsx`; local/component tests passed in prior closeout. | The live default aggregate has no ready strategy, so these components are not proven through a real hosted workflow. |
| Dynamic workspace BFF operations | Partial/building block | Prior backend tests for Trading Room proposal/accept/workspace/layout/widget revision/version/rollback passed locally (`45 passed` in prior closeout). | Need live post-deploy workflow proof from a browser-created ready strategy/version, including ETag/idempotency behavior. |
| Hosted Winner Branch E2E | Not production-level | `AG-DYNUI-PROD-006` explicitly used `page.route()` fixtures for steps after the initial live workshop/default-route checks. | Replace with no-fixture hosted E2E that creates/restores a real workshop, reaches readiness, materializes a strategy, and completes the workspace workflow live. |
| Strategy Workshop cards route | Building block proven live | Direct dev BFF `GET /bff/agora/workshops/ce63ec2a-c5f1-4e41-8219-e410d22037c7/cards` returns `200` for the browser user. | Must be exercised inside hosted no-fixture E2E. |
| Strategy Workshop readiness route | Building block proven live | Direct dev BFF `GET /bff/agora/workshops/ce63ec2a-c5f1-4e41-8219-e410d22037c7/readiness` returns `200` for the browser user. | Need real workflow evidence that readiness reaches `trading_room` without SQL seeding. |
| Workshop-to-Trading Room strategy materialization | Backend partial | SQL-seeded workshop `d237eb8f-44a6-4805-9b27-d5723f8c99eb` can project a ready strategy into Trading Room. | Browser-created workflow still returns empty aggregate until completeness and Strategy Registry ref are produced through live API/UI. |
| Frontend handoff route context | Partial/building block | execute-plans PR #185 merged, integration gate passed, dev FE deploy published merge `4cce2d10f14abcc7af5f15638e0e0efa63885944`, and hosted no-fixture screenshots/readbacks are recorded under `docs/deployment/evidence/ag-dynui-full-004/20260705T152800Z/`. | Live browser-scoped BFF still has zero ready strategies, so CTA navigation cannot be exercised without fixtures; `AG-DYNUI-FULL-005` must produce ready-strategy materialization. |
| Design parity | Blocked source-truth | Exact `/home/lupin/code/pantheon/AI%20Trading%20Desk%20Design.zip` was not found; closure packs are contract/spec archives, not visual design proof. | Recover the exact design zip or get reviewed approval that closure packs are the canonical replacement before visual closeout. |
| Execute-plans FE-BFF Integration Gate | Not production-level | Prior execute-plans PR #177 merged and dev FE deploy succeeded, but the integration gate was not green. | Fix the gate and require success on the no-fixture production path. |

## Functional Completion Matrix

| Product function | Fully complete? | Required next task |
|---|---:|---|
| Open hosted `/agora/trading-room` without red load failure | Yes, for empty/default state only | `AG-DYNUI-FULL-007` must re-prove after all changes. |
| Show design-pack/workshop cards from live workshop state | Yes for direct BFF, not hosted E2E | `AG-DYNUI-FULL-005`, `AG-DYNUI-FULL-006` |
| Calculate and persist readiness from live workshop evidence | Yes for direct BFF incomplete workshop | `AG-DYNUI-FULL-005`, `AG-DYNUI-FULL-006` |
| Promote readiness to `trading_room` gate | Backend-only partial from SQL-seeded proof | `AG-DYNUI-FULL-005` |
| Create or expose a ready `strategyId` / `strategyVersion` | Backend-only partial from SQL-seeded proof | `AG-DYNUI-FULL-005` |
| Populate Trading Room aggregate with real ready strategies | Backend-only partial; browser workflow still empty | `AG-DYNUI-FULL-005` |
| Navigate from Strategy Workshop to explicit strategy route | Frontend code merged and deployed; live CTA remains blocked by missing ready strategy | `AG-DYNUI-FULL-005` |
| Load real strategy workspace in hosted UI | No | `AG-DYNUI-FULL-003`, `AG-DYNUI-FULL-004` |
| Generate workspace proposal live | Not proven live | `AG-DYNUI-FULL-005` |
| Accept proposal and persist workspace live | Not proven live | `AG-DYNUI-FULL-005` |
| Patch grid layout with `If-Match`/idempotency live | Not proven live | `AG-DYNUI-FULL-005` |
| Propose/apply/keep-copy widget revision live | Not proven live | `AG-DYNUI-FULL-005` |
| List version history and rollback live | Not proven live | `AG-DYNUI-FULL-005` |
| Hosted desktop/mobile E2E without BFF fixtures | No | `AG-DYNUI-FULL-006` |
| Visual parity with design team's source | Blocked | `AG-DYNUI-FULL-001` |

## Source Truth

The user referenced:

- `/home/lupin/code/pantheon/AI%20Trading%20Desk%20Design.zip`

Current workspace search did not find that exact file. The only nearby local
design archives found were:

- `/home/lupin/code/pantheon/Pantheon_Agora_Design_Closure_Pack_2026-06-20.zip`
- `/home/lupin/code/pantheon-live-root-cleanup-archive-20260627T124239Z/Pantheon_Agora_Design_Closure_Round2_v1_3_2026-06-21.zip`

The fleet must not rebuild the UI from memory. `AG-DYNUI-FULL-001` must either
recover the exact design zip, prove the closure packs are the canonical
replacement, or open a blocker that names the missing source and the exact
work that cannot continue without it.

## Production Definition

Agora DYNUI is production-level only when all of these are true:

- The canonical design source is recovered or formally replaced by a reviewed
  closure pack, with a screen/state parity matrix.
- Strategy Workshop has live BFF `cards`, `readiness`, and
  `readiness/reassess` routes backed by scoped persistence.
- Readiness can progress to `highest_ready_gate = trading_room` from real
  workshop evidence, not from frontend-only fixtures.
- A ready strategy/version is materialized into the live Trading Room aggregate.
- The Strategy Workshop "Add to Trading Room" action passes
  `strategyId`, `strategyVersion`, and readiness context to the route.
- `/agora/trading-room/:strategyId` loads a real live strategy workspace and
  does not depend on the aggregate having seeded mock strategies.
- Proposal generation, accept, grid edit, widget revision, version history,
  and rollback all run against the live dev BFF with auth, tenant scope,
  idempotency, ETag/If-Match, and WidgetSpec allowlists.
- Hosted E2E uses no `page.route()` or network response fixtures for the
  production gate path.
- Execute-plans FE-BFF Integration Gate, Pantheon Branch CI, deploy, hosted
  browser proof, and post-deploy live curls all pass.

## Closeout Rules

No task in this packet may be closed from:

- local-only tests,
- a merged PR without deploy proof,
- a hosted screenshot that relies on BFF route fixtures,
- a CI run where the aggregate release gate failed,
- a live probe that only checks `/health`,
- a frontend route that works only with seeded/mock strategies.

Every task closeout must record:

- branch,
- PR URL,
- merge SHA,
- local validation commands,
- CI/check URLs and results,
- deploy run ID when runtime changes are involved,
- live curl or browser proof against `pantheon-lupin-dev-fe` /
  `pantheon-lupin-dev-bff`,
- residual risks and follow-up owner if any.

## Execution Packet

Fleet tasks are materialized in:

- `docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/INDEX.md`
- `scripts/dispatch_agora_dynui_full_production_recovery_2026-07-05.py`

Wave 0 source-truth and parity matrix:

- `docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/AG-DYNUI-FULL-001-source-truth-and-parity-matrix.md`
