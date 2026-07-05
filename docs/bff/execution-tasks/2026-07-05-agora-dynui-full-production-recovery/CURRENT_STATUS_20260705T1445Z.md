# Agora DYNUI Production Recovery Current Status - 2026-07-05T14:45Z

Status: active production recovery, not production complete.

## Merged And Deployed Pantheon Work

| PR | Merge SHA | What it proves | Production caveat |
|---|---|---|---|
| #3020 | `9a7d1f3260767585962bf2a673437ae85318d494` | Workshop readiness can be projected into Trading Room backend reads. | Live proof used a SQL-seeded completeness snapshot, not the hosted UI/API workflow. |
| #3021 | `96d6a7288061047ceca7b911843555d6296d8425` | Dev BFF uses Postgres Strategy Workshop store. | Store durability alone does not create ready strategies. |
| #3022 | `aab5c301f7ba5e5872e9f0f5b195832be34acbeb` | Exhausted or disabled fleet lanes are removed from the mainline supervisor path. | Runtime task board still needs existing stale assignments corrected. |
| #3023 | `532332a949a15e770285055c013b1f19adf767f7` | Codex worker task worktrees resolve the CLI correctly. | Does not complete Agora product work by itself. |

## Live BFF Truth

Direct dev BFF `127.0.0.1:18001` with
`Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa`
and `X-Tenant-Id: pantheon-dev`:

- Created workshop `ce63ec2a-c5f1-4e41-8219-e410d22037c7` for
  `tenant:pantheon-dev:user:pantheon-dev-browser`.
- `GET /bff/agora/workshops/ce63ec2a-c5f1-4e41-8219-e410d22037c7/readiness`
  returns `200`.
- `GET /bff/agora/workshops/ce63ec2a-c5f1-4e41-8219-e410d22037c7/cards`
  returns `200`.
- That workshop is not ready for Trading Room: `highest_ready_gate = null`,
  with blockers for missing completeness snapshot, Strategy Registry reference,
  and full-validation readiness.
- `GET /bff/agora/trading-room` returns `strategies: []` for the same browser
  user.
- Cross-user reads return `403 CROSS_USER_ACCESS_FORBIDDEN`; this is expected
  scope enforcement, not a missing route.

SQL-seeded backend proof:

- Workshop `d237eb8f-44a6-4805-9b27-d5723f8c99eb` under user
  `agora-test-user` can reach `highest_ready_gate = trading_room`.
- Trading Room detail for strategy `strat-full003-live-20260705T131055Z`
  returns `200`.
- This is backend projection proof only. It cannot close hosted UI/API E2E
  because the setup bypassed the public workshop completeness workflow.

## Execute-Plans Frontend Truth

- `AG-DYNUI-FULL-004` opened execute-plans PR #185:
  `https://github.com/ajoe734/execute-plans/pull/185`.
- Head commit: `4668d52bd76c973946d8466f1d65ab1f43358cc2`.
- The PR wires Strategy Workshop handoff context into
  `/agora/trading-room/:strategyId`.
- The FE-BFF `integration-gate` check is still in progress at this status
  capture. Do not mark `AG-DYNUI-FULL-004` done until checks, merge, deploy,
  and hosted proof are recorded.

## Fleet Reality

- Claude and Claude2 are exhausted and must not be used for mainline recovery.
- Antigravity, Antigravity2, and Copilot are disabled in durable config.
- Codex is the only immediately usable implementation lane.
- Codex2 is the intended reviewer lane when available, but may be quota-paused.
- Runtime task board entries for `AG-DYNUI-FULL-005`, `AG-DYNUI-FULL-006`, and
  `AG-DYNUI-FULL-007` that point at Antigravity lanes are stale and must be
  reassigned before execution.

## Functional Gap Summary

| Function | Current status | Next owner task |
|---|---|---|
| Live cards/readiness route existence | Direct BFF proven | `AG-DYNUI-FULL-005` must consume in hosted flow |
| Readiness reaches `trading_room` through public flow | Not proven | `AG-DYNUI-FULL-005` |
| Browser user gets non-empty Trading Room aggregate | Not proven | `AG-DYNUI-FULL-005` |
| Strategy Workshop CTA explicit strategy route | PR open, gate pending | `AG-DYNUI-FULL-004` |
| Proposal/accept/workspace/grid/revision/version/rollback live | Not proven | `AG-DYNUI-FULL-005` |
| Hosted no-fixture desktop/mobile E2E | Not proven | `AG-DYNUI-FULL-006` |
| Final production closeout | Not valid yet | `AG-DYNUI-FULL-007` |
