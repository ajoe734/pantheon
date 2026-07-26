# Trade Journey E2E Execution Packet - 2026-07-11

Status: closed 2026-07-24 — all twelve tasks delivered; see § Packet closeout

Source archive:

- `docs/04/pantheon_trade_journey_e2e_observability_gap_2026-07-11/INDEX.md`
- `docs/04/pantheon_trade_journey_e2e_observability_gap_2026-07-11/TRADE_JOURNEY_E2E_OBSERVABILITY_GAP.md`

## Dispatch

```sh
python3 scripts/dispatch_trade_journey_e2e_2026-07-11.py --dry-run
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/dispatch_trade_journey_e2e_2026-07-11.py
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py sync
```

The live dispatcher must run only after this packet is merged to Pantheon
`dev`. It is idempotent and preserves worker progress.

## Waves

| Wave | Task | Owner | Reviewer | Target |
|---|---|---|---|---|
| 0 | `TJ-E2E-001` | Claude | Antigravity | Producer and correlation inventory |
| 0 | `TJ-E2E-002` | Antigravity | Claude | Journey domain and state contract |
| 1 | `TJ-E2E-003` | Claude | Antigravity | Correlation envelope propagation |
| 1 | `TJ-E2E-004` | Antigravity | Claude | Materializer and reverse index |
| 2 | `TJ-E2E-005` | Claude | Antigravity | Canonical BFF read API |
| 2 | `TJ-E2E-006` | Antigravity | Claude | Frontend P0 workbench |
| 3 | `TJ-E2E-007` | Claude | Antigravity | SSE and attention model |
| 3 | `TJ-E2E-008` | Antigravity | Claude | Governed journey actions |
| 3 | `TJ-E2E-009` | Claude | Antigravity | Cross-entry IA integration |
| 4 | `TJ-E2E-010` | Antigravity | Claude | Replay and legacy backfill |
| 4 | `TJ-E2E-011` | Claude | Antigravity | SLO and data-quality incidents |
| 5 | `TJ-E2E-012` | Antigravity | Human/Ops | Hosted acceptance and closeout |

## Dependencies

```text
001: none
002: 001
003: 001, 002
004: 002, 003
005: 004
006: 005
007: 005, 006
008: 005, 006
009: 006
010: 004, 005
011: 004, 007, 010
012: 001-011
```

Dependencies are hard merge gates. Workers may inspect later work but cannot
claim or merge an implementation that assumes unfinished upstream contracts.

## Repository boundaries

- Pantheon services, BFF, contracts, archive, telemetry and dispatch belong to
  `ajoe734/pantheon`, merge target `dev`.
- Frontend belongs to `ajoe734/execute-plans`, current merge target `main` per
  repository instructions.
- Never materialize an `execute-plans/` source tree inside Pantheon.
- Every task uses a clean task worktree, focused validation, PR, checks, review,
  merge SHA and closeout evidence.

## Non-negotiable fleet constraints

- Frontend must consume the canonical BFF journey model; no client-side domain join.
- Read model/materializer cannot become a synchronous execution dependency.
- Historical events are append-only and replayable.
- Unknown or missing data is displayed as incomplete, never inferred complete.
- Journey actions reuse existing governance/RBAC/human gates and emit receipts.
- A fill is not a completed journey until booking and reconciliation are terminal.
- Paper, canary and live use one schema with explicit environment/capital impact.

## Fleet closeout evidence

Each task records repository, branch, PR, merge SHA, tests, reviewer verdict,
contract/version changes, hosted evidence where applicable, and residual risks.
The packet closes only after `TJ-E2E-012` proves all twelve acceptance scenarios
from the archived gap specification.

## Packet closeout

Closed `2026-07-24`. `TJ-E2E-001`–`TJ-E2E-011` are archived `done`/`completed`.
`TJ-E2E-012` proved all twelve acceptance scenarios against an immutable hosted
run and passed both gates — Human/Ops **APPROVED** `2026-07-23T08:07:19Z` and
governed reviewer `Codex` **APPROVED** `2026-07-24T00:48:58Z`. Final evidence
index:
[TJ-E2E-012-hosted-acceptance-closeout.md](TJ-E2E-012-hosted-acceptance-closeout.md).

The Wave 5 owner/reviewer row above records the original dispatch assignment;
`TJ-E2E-012` was actually delivered by owner `Claude2` with reviewer `Codex`
after governed reassignment. Closure covers the read-only hosted rollout for the
two proven FE/BFF pairs only — not live writes, broker capital, or production
default rollout. Residual risks R1–R4 and R6 remain owned as recorded in the
closeout packet §7.
