# Agora DYNUI Full Production Recovery Execution Packet - 2026-07-05

Status: dispatchable production-recovery tasks.

Routing repair, 2026-07-05:

- `AG-DYNUI-FULL-001` is closed as the source-truth/parity-matrix task and
  must not be re-created by a later dispatch.
- Claude and Claude2 are unavailable for the remaining mainline work because
  their quota is exhausted.
- Gemini and Gemini2 are not valid mainline owners for this wave because the
  supervisor guard marks them disabled/sidecar-only/auth-down and auto-routes
  them away.
- Underutilization sidecar dispatch is configured to exclude Claude and
  Claude2 for the same quota reason.
- The remaining task owners/reviewers below are the current executable lanes.

Source audit:

- `docs/04/pantheon_agora_dynui_full_production_recovery_2026-07-05/INDEX.md`

Wave 0 owner artifact:

- `AG-DYNUI-FULL-001-source-truth-and-parity-matrix.md`

Dispatch command:

```sh
AI_NAME=Codex python3 scripts/dispatch_agora_dynui_full_production_recovery_2026-07-05.py
```

Live task-board dispatch from a clean worktree:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  AI_NAME=Codex \
  python3 scripts/dispatch_agora_dynui_full_production_recovery_2026-07-05.py
```

The dispatch script is idempotent. It creates or refreshes the task set below,
preserves progress fields for tasks already started, and assigns owner/reviewer
pairs for fleet execution.

## Execution Order

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `AG-DYNUI-FULL-001` | Codex | Claude2 | Closed source-truth/parity matrix; historical reviewer only. |
| 1 | `AG-DYNUI-FULL-002` | Codex | Codex2 | Implement live Strategy Workshop cards/readiness BFF routes and tests. |
| 2 | `AG-DYNUI-FULL-003` | Codex2 | Codex | Materialize ready strategies into the live Trading Room aggregate. |
| 2 | `AG-DYNUI-FULL-004` | Codex2 | Codex | Wire frontend workshop handoff and explicit strategy route behavior. |
| 3 | `AG-DYNUI-FULL-005` | Copilot | Codex | Prove live proposal/accept/workspace/grid/revision/version/rollback without fixtures. |
| 4 | `AG-DYNUI-FULL-006` | Codex2 | Codex | Replace hosted E2E fixture gate with no-fixture production gate and fix CI gate failures. |
| 5 | `AG-DYNUI-FULL-007` | Codex | Codex2 | Final production closeout, deploy evidence, and residual-risk audit. |

## Global Rules

- Do not reuse archived `AG-DYNUI-PROD-*` IDs.
- Do not re-create archived terminal `AG-DYNUI-FULL-*` IDs on dispatch.
- Do not assign remaining `AG-DYNUI-FULL-*` mainline work to Claude,
  Claude2, Gemini, or Gemini2 while the live quota/guardrail state marks those
  lanes unavailable.
- Do not close from `AG-DYNUI-PROD-006` fixture-backed evidence.
- Do not use `page.route()` or mocked BFF responses in production-gate E2E.
- Do not fabricate design details if the design zip remains missing.
- Do not remove strict BFF auth, tenant scoping, idempotency, optimistic
  concurrency, or WidgetSpec allowlist validation to make tests pass.
- If a task needs execute-plans changes, it must include execute-plans PR,
  checks, merge SHA, dev FE deploy evidence, and hosted proof.
- If a task needs Pantheon BFF changes, it must include Pantheon PR, Branch CI,
  merge SHA, deploy evidence, and live BFF curl proof.

## Required Live Proofs

At packet closeout, these probes must pass against the hosted dev environment:

```sh
curl -fsS \
  -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' \
  -H 'X-Tenant-Id: pantheon-dev' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room
```

Expected: `strategies.length > 0` after the E2E creates or restores a ready
strategy.

```sh
curl -fsS \
  -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' \
  -H 'X-Tenant-Id: pantheon-dev' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops/<workshop_id>/readiness
```

Expected: `highest_ready_gate` is present and can reach `trading_room`.

```sh
curl -fsS \
  -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' \
  -H 'X-Tenant-Id: pantheon-dev' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops/<workshop_id>/cards
```

Expected: live cards are returned from scoped workshop state.

The hosted browser E2E must prove the visible flow:

1. Strategy Workshop opens from hosted FE.
2. A real workshop is created or restored.
3. Cards and readiness update from live BFF.
4. Readiness reaches Trading Room gate.
5. "Add to Trading Room" navigates with real strategy/version context.
6. Trading Room shows the strategy workspace.
7. Proposal generation returns a live BFF proposal.
8. Accept creates a live workspace.
9. Grid edit persists through live layout PATCH.
10. Widget revision proposal and accept persist through live BFF routes.
11. Version history lists live versions.
12. Rollback creates a live version and visible UI update.
13. Desktop and mobile screenshots show the production UI, not a fixture shell.
