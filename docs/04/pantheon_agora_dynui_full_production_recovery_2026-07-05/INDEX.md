# Agora DYNUI Full Production Recovery - 2026-07-05

Status: production recovery packet; not a completion certificate.

## Why This Exists

The previous `AG-DYNUI-PROD-001` through `AG-DYNUI-PROD-006` fleet wave
restored important Agora pieces, but the closeout evidence does not prove the
full live dynamic UI product. The task board archived those tasks as `done`;
current hosted/live evidence contradicts a production-complete reading.

This packet reopens the remaining work as new follow-up execution tasks instead
of reusing archived task IDs.

## Current Live Evidence

Captured on 2026-07-05 after redeploying dev BFF to
`341924e2c29ee185c925b8f4291485beb08e851e`:

- `GET /bff/agora/trading-room` is now `200` and no longer raises the
  earlier `500 INTERNAL_ERROR`.
- The Trading Room aggregate still returns `strategies: []`.
- `GET /bff/agora/workshops/{id}/readiness` returns `404 RESOURCE_NOT_FOUND`.
- `GET /bff/agora/workshops/{id}/cards` returns `404 RESOURCE_NOT_FOUND`.
- Hosted browser probe for `/agora/trading-room` no longer shows
  `Failed to load Trading Room`; it renders the `Dynamic Entry` empty state.

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
| Trading Room BFF default read | Production-proven for availability only | Post-deploy curl returns `200` for `/bff/agora/trading-room`; hosted browser no longer shows `Failed to load Trading Room` after BFF deploy `341924e2c29ee185c925b8f4291485beb08e851e`. | Aggregate still returns `strategies: []`, so product workflow is not proven. |
| Trading Room decision events read | Production-proven for availability only | Post-deploy curl returns `200` for `/bff/agora/trading-room/decision-events`. | Does not prove strategy creation, workspace mutation, or readiness handoff. |
| Strict auth/header path for hosted FE | Partial/building block | Hosted FE can call the dev BFF with dev browser auth and no longer fails the Trading Room load on the default route. | Needs full workshop-to-trading-room flow under the same auth path. |
| Standalone Agora shell/navigation | Partial/building block | Prior `AG-DYNUI-PROD-002/003/004` work changed the shell and default route behavior. | User still observed embedded/shell mismatch; visual parity cannot be closed until canonical design source is recovered and inspected. |
| Dynamic workspace frontend components | Partial/building block | Execute-plans PR #176 wired proposal preview, grid editor, widget revision drawer, version history, and rollback into `TradingRoomPage.tsx`; local/component tests passed in prior closeout. | The live default aggregate has no ready strategy, so these components are not proven through a real hosted workflow. |
| Dynamic workspace BFF operations | Partial/building block | Prior backend tests for Trading Room proposal/accept/workspace/layout/widget revision/version/rollback passed locally (`45 passed` in prior closeout). | Need live post-deploy workflow proof from a real ready strategy/version, including ETag/idempotency behavior. |
| Hosted Winner Branch E2E | Not production-level | `AG-DYNUI-PROD-006` explicitly used `page.route()` fixtures for steps after the initial live workshop/default-route checks. | Replace with no-fixture hosted E2E that creates/restores a real workshop, reaches readiness, materializes a strategy, and completes the workspace workflow live. |
| Strategy Workshop cards route | Not production-level | Live `GET /bff/agora/workshops/{id}/cards` returns `404 RESOURCE_NOT_FOUND`. | Implement scoped BFF cards read and tests; prove with live curl. |
| Strategy Workshop readiness route | Not production-level | Live `GET /bff/agora/workshops/{id}/readiness` returns `404 RESOURCE_NOT_FOUND`. | Implement readiness read and reassess path backed by workshop state; prove `highest_ready_gate` can reach `trading_room`. |
| Workshop-to-Trading Room strategy materialization | Not production-level | Live Trading Room aggregate remains empty after current hosted probes. | Persist or project a ready strategy/version from readiness into Trading Room; prove aggregate `strategies.length > 0`. |
| Frontend handoff route context | Not production-level | Prior handoff navigated to the default route; no live strategy route context was proven. | CTA must navigate with `strategyId`, `strategyVersion`, and readiness context to `/agora/trading-room/:strategyId`. |
| Design parity | Blocked source-truth | Exact `/home/lupin/code/pantheon/AI%20Trading%20Desk%20Design.zip` was not found; closure packs are contract/spec archives, not visual design proof. | Recover the exact design zip or get reviewed approval that closure packs are the canonical replacement before visual closeout. |
| Execute-plans FE-BFF Integration Gate | Not production-level | Prior execute-plans PR #177 merged and dev FE deploy succeeded, but the integration gate was not green. | Fix the gate and require success on the no-fixture production path. |

## Functional Completion Matrix

| Product function | Fully complete? | Required next task |
|---|---:|---|
| Open hosted `/agora/trading-room` without red load failure | Yes, for empty/default state only | `AG-DYNUI-FULL-007` must re-prove after all changes. |
| Show design-pack/workshop cards from live workshop state | No | `AG-DYNUI-FULL-002` |
| Calculate and persist readiness from live workshop evidence | No | `AG-DYNUI-FULL-002` |
| Promote readiness to `trading_room` gate | No | `AG-DYNUI-FULL-002` |
| Create or expose a ready `strategyId` / `strategyVersion` | No | `AG-DYNUI-FULL-003` |
| Populate Trading Room aggregate with real ready strategies | No | `AG-DYNUI-FULL-003` |
| Navigate from Strategy Workshop to explicit strategy route | No | `AG-DYNUI-FULL-004` |
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
