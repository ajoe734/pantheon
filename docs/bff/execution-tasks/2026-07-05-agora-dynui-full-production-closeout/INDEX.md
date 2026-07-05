# AG-DYNUI-FULL Production Closeout - 2026-07-05

Status: production functionality proven; task-board closeout and design-parity
hardening remain as fleet execution tasks.

This packet supersedes the stale 2026-07-03 live-auth recovery notes for the
AG-DYNUI-FULL line. It records the authoritative 2026-07-05 evidence after the
Agora Trading Room was repaired from a failing/fixture-backed state into a live
end-to-end workflow.

## Authoritative Evidence

### Completed and published

| Area | Repository | PR | Merge commit | Checks/deploy |
|---|---|---:|---|---|
| Source parity matrix | `ajoe734/pantheon` | [#3006](https://github.com/ajoe734/pantheon/pull/3006) | published | merged |
| Source parity finalization/review | `ajoe734/pantheon` | [#3009](https://github.com/ajoe734/pantheon/pull/3009) | published | merged |
| Live cards/readiness/reassess foundation | `ajoe734/pantheon` | [#3013](https://github.com/ajoe734/pantheon/pull/3013), [#3014](https://github.com/ajoe734/pantheon/pull/3014), [#3018](https://github.com/ajoe734/pantheon/pull/3018), [#3019](https://github.com/ajoe734/pantheon/pull/3019) | published | merged |
| Live ready strategy materialization setup | `ajoe734/pantheon` | [#3020](https://github.com/ajoe734/pantheon/pull/3020), [#3021](https://github.com/ajoe734/pantheon/pull/3021) | published | merged |
| Hosted FE handoff evidence | `ajoe734/pantheon` | [#3028](https://github.com/ajoe734/pantheon/pull/3028) | published | merged |
| Live workshop readiness materialization | `ajoe734/pantheon` | [#3030](https://github.com/ajoe734/pantheon/pull/3030) | `4933c36564b30085480dce5a0e0bfc71d7806c49` | merged |
| Strategy Workshop to Trading Room handoff | `ajoe734/execute-plans` | [#185](https://github.com/ajoe734/execute-plans/pull/185) | `4cce2d10f14abcc7af5f15638e0e0efa63885944` | integration gate and dev FE deploy passed |
| Live dynamic workspace wiring | `ajoe734/pantheon` | [#3032](https://github.com/ajoe734/pantheon/pull/3032) | `66efc0e849f3facb33889634fe48a5947603cafb` | merged |
| Browser-readable workspace ETags | `ajoe734/pantheon` | [#3033](https://github.com/ajoe734/pantheon/pull/3033) | `3e553bb3a1c4e2d8572d233c3030349249b99d75` | deploy run [28748417821](https://github.com/ajoe734/pantheon/actions/runs/28748417821) succeeded |
| `If-Match` CORS preflight | `ajoe734/pantheon` | [#3034](https://github.com/ajoe734/pantheon/pull/3034) | `f010383fd367c8b960f6341c0c3c4ad93c1865cd` | deploy run [28748692234](https://github.com/ajoe734/pantheon/actions/runs/28748692234) succeeded |
| Layout `widgetCount` normalization | `ajoe734/pantheon` | [#3035](https://github.com/ajoe734/pantheon/pull/3035) | `d002ed5a7fcec5c30c8fee13efd6cb6c30fbf8fb` | deploy run [28748861121](https://github.com/ajoe734/pantheon/actions/runs/28748861121) succeeded |
| Fixture-free hosted live E2E gate | `ajoe734/execute-plans` | [#187](https://github.com/ajoe734/execute-plans/pull/187) | `37f8e320ac9a3fed621bfe3d36d34138f2b7c73d` | integration gate run [28749332352](https://github.com/ajoe734/execute-plans/actions/runs/28749332352) succeeded |

### Live workflow proof

The live gate now exercises the real hosted FE/BFF workflow without
`page.route`, `route.fulfill`, or fixture-backed Agora responses:

1. Discover a real workshop with `highest_ready_gate = trading_room`.
2. Verify required card types:
   `user_strategy_description`, `completeness_update`, `readiness_gate`.
3. Open Strategy Workshop and use the real "Add to Trading Room" handoff.
4. Generate and accept a real Trading Room workspace proposal.
5. Edit grid layout and save dashboard version 2.
6. Submit a widget revision proposal and accept keep-original-plus-copy,
   producing dashboard version 3.
7. Read dashboard version history.
8. Roll back to an earlier version, producing dashboard version 4.

Evidence artifacts produced by the gate:

- Durable Pantheon evidence packet:
  [`docs/deployment/evidence/ag-dynui-full-006/20260705T175529Z`](../../../deployment/evidence/ag-dynui-full-006/20260705T175529Z/)
- `/tmp/ag-dynui-full-006-live-summary-desktop.json`
- `/tmp/ag-dynui-full-006-live-summary-mobile.json`
- `/tmp/ag-dynui-full-006-01-live-ready-workshop-*.png`
- `/tmp/ag-dynui-full-006-02-live-workspace-proposal-*.png`
- `/tmp/ag-dynui-full-006-03-live-workspace-accepted-*.png`
- `/tmp/ag-dynui-full-006-04-live-grid-unsaved-*.png`
- `/tmp/ag-dynui-full-006-05-live-grid-saved-v2-*.png`
- `/tmp/ag-dynui-full-006-06-live-widget-revision-preview-*.png`
- `/tmp/ag-dynui-full-006-07-live-widget-revision-v3-*.png`
- `/tmp/ag-dynui-full-006-08-live-version-history-*.png`
- `/tmp/ag-dynui-full-006-09-live-rollback-applied-*.png`
- `/tmp/ag-dynui-full-006-hosted-trading-room-final.png`

Last direct hosted proof:

- URL: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room`
- Result: AGORA dark Trading Room renders ready strategies and proposal state.
- `Failed to load Trading Room` was absent.
- Agora BFF response count: 3.
- Agora BFF 4xx/5xx responses: 0.

## Board State Audit

The runtime board in `.orchestrator/state.json` is stale relative to the
published evidence:

| Task | Board status observed | Evidence-based status |
|---|---|---|
| `AG-DYNUI-FULL-001` | archived done | done |
| `AG-DYNUI-FULL-002` | archived done | done |
| `AG-DYNUI-FULL-003` | `in_progress` | stale; published by PRs #3020/#3021/#3030 |
| `AG-DYNUI-FULL-004` | archived done | done |
| `AG-DYNUI-FULL-005` | `todo` | stale; dynamic workflow published by PR #3032 and proven by live gate |
| `AG-DYNUI-FULL-006` | `todo` | stale; fixture-free live E2E gate published by execute-plans PR #187 |
| `AG-DYNUI-FULL-007` | `todo` | still required as closeout/board/design-parity hardening |

Do not hand-edit dirty runtime state to hide this mismatch. Use the repository
workflow and the task-status tooling where it can safely operate from a clean
task branch/worktree.

## Remaining Fleet Tasks

The remaining work is not to rebuild Agora from scratch. It is to close the
production paper trail and harden the UI against design-parity regressions.

- [AG-DYNUI-FULL-007](./AG-DYNUI-FULL-007-production-closeout.md)
- [AG-DYNUI-FULL-008](./AG-DYNUI-FULL-008-design-parity-hardening.md)

Recommended lane policy:

- Do not wait for Claude or Claude2 quota recovery.
- Use Codex/Codex2 for board/closeout and execute-plans review.
- Use Copilot for independent design/spec critique.
- Use Antigravity/Antigravity2 only for deploy/probe/runtime verification work.

## Production-Level Definition

A task in this packet is not done until all applicable items are true:

1. Source changes are on a clean task branch/worktree.
2. Relevant local validation has passed.
3. Only intended files are staged.
4. Commit contains required trailers.
5. Branch is pushed.
6. PR is opened against the correct base.
7. Required checks are green.
8. PR is merged.
9. Runtime deployment is completed when runtime code changed.
10. Hosted live proof exists for user-visible behavior.
11. Evidence paths, PR number, merge commit, checks, deploy run, and residual
    risks are recorded.
