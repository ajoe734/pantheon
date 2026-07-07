# Agora And SRCLIVE Production Inventory - 2026-07-06

Status: execution packet for fleet supervision.

Do not wait for Claude or Claude2 quota recovery for any item in this packet. Use Codex, Codex2, Copilot, Antigravity, or Antigravity2 only when the worker is actually available.

## Scope

This packet is the current source of truth for the Agora Trading Room failure report and the open SRCLIVE items. It separates completed live work, stale board/archive state, and work that still needs production acceptance.

## Production-Level Rule

No item is complete just because it works locally, exists in a stale board, or was assigned to an exhausted worker. Completion requires clean branch/worktree, validation, staged intended files only, commit trailers, push, PR, green required checks, merge, deploy when runtime changes are made, and recorded live proof for user-visible behavior.

## Agora Inventory

| Item | Current assessment | Evidence | Remaining action |
|---|---|---|---|
| Direct /agora/trading-room page load | Working on hosted dev at execute-plans #198 | HTTP 200, no Failed to load Trading Room, no strict typed error, desktop/mobile screenshots under /tmp/agora-live-proof-9a4d164d | Re-run after the next dev FE deploy |
| Live strategy/readiness/cards | Implemented and live-tested | execute-plans PR #187 and hosted E2E proof through /tmp/agora-live-proof-9a4d164d | Keep under regression gate; do not rebuild |
| Real end-to-end workflow | Implemented and live-tested | Hosted E2E covers readiness cards, handoff, proposal accept, grid save, widget revision, version history, rollback | Re-run after each new dev FE deploy |
| Dynamic UI model | Implemented as BFF-driven workflow, not static screenshots | E2E uses real FE/BFF URLs and contains no page.route or route.fulfill fixture path | Do not replace with embedded/static UI |
| Design parity dark surface | Published | execute-plans PR #190, merge 705649c430d3b6064cf34aa7d854c3936b4c86af | Keep visual proof current with latest deploy |
| Proposal overflow polish | Published | execute-plans PR #195, merge 2dd6cf39157adc5d965b721e9e9ec53fbcfc0dac | Keep visual proof current with latest deploy |
| Task board/archive status | Stale | Root board still shows AG-DYNUI-FULL-003 active and AG-DYNUI-FULL-005/006/007 todo in the live status checkout | Run AG-DYNUI-FULL-007 reconciliation from a clean reviewed path; do not hand-edit dirty live state |

## SRCLIVE Inventory

| Item | Current assessment | Evidence | Remaining action |
|---|---|---|---|
| SRCLIVE-001 code/runbook | Merged | pantheon PR #2517, merge 8da3d35766a041bfbb7b85aa018ee4ef65114cfd | Production acceptance is still missing |
| SRCLIVE-001 production acceptance | Not complete | No confirmed VM-local source-ingest activation, health-usage snapshot, or BFF persona-tw-equity readback evidence found in the current audit | Execute the live activation task and archive proof |
| SRCLIVE-004 implementation | Merged | pantheon PRs #2539, #2548, #2554, #2557 with green Branch CI gates | Do not reimplement |
| SRCLIVE-004 board/archive state | Stale/missing from current status root | scripts/ai_status.py show SRCLIVE-004 reports unknown task in the current root | Reconcile archive/status and optionally re-run the readback verifier |

## Execution Tasks

- AG-DYNUI-FULL-007 board/archive reconciliation: ../2026-07-05-agora-dynui-full-production-closeout/AG-DYNUI-FULL-007-production-closeout.md
- AG-DYNUI-FULL-008 design-parity maintenance: ../2026-07-05-agora-dynui-full-production-closeout/AG-DYNUI-FULL-008-design-parity-hardening.md
- SRCLIVE-001 production acceptance: ../2026-07-06-srclive-production-closeout/SRCLIVE-001-live-activation-acceptance.md
- SRCLIVE-004 status/readback reconciliation: ../2026-07-06-srclive-production-closeout/SRCLIVE-004-state-readback-reconciliation.md

## Immediate Supervisor Instructions

1. Do not assign work to Claude or Claude2 while quota is exhausted.
2. Do not create duplicate AG-DYNUI implementation tasks for completed live workflow pieces.
3. Block any worker that tries to replace the dynamic UI with embedded/static pages.
4. Require hosted FE/BFF proof after every execute-plans dev deploy.
5. Keep SRCLIVE-001 blocked until live activation proof exists.
6. Keep SRCLIVE-004 out of reimplementation lanes; it only needs reconciliation/fresh verification.

## 2026-07-07 SRCLIVE Verifier Note

Read-only verifier command attempted from the clean task worktree:

python3 scripts/verify_srclive_readback.py --json

Result: blocked by live BFF auth. The endpoint /bff/v5/execution/persona-health returned HTTP 401 AUTH_REQUIRED with SESSION_LOGGED_OUT. This does not prove SRCLIVE-004 failed; it means the fleet needs a valid operator/admin token to complete fresh readback proof. SRCLIVE-001 still requires VM-local source-ingest activation and health-usage-snapshot evidence.
