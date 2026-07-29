# Pantheon and Agora Remaining Work Execution Packet — 2026-07-22

Status: Agora hosted lane completed; reused cross-product gates remain governed
under their own canonical task IDs

Source audit:

- `docs/04/pantheon_agora_remaining_work_2026-07-22/REMAINING_WORK_GAP.md`

This packet reuses the active canonical IDs `PPL-ALLOC-009` and `TJ-E2E-012`.
It does not create replacement tasks for them. `AG-GAP-005` remains correctly
archived as contract-honesty work; `AG-WS-OPS-*` are new implementation tasks
for the capabilities that task deliberately deferred.

## Dispatch

Validate the packet without changing canonical state:

```sh
python3 scripts/dispatch_pantheon_agora_remaining_work_2026-07-22.py --dry-run
```

After this packet is merged to `dev`, dispatch from an operator context:

```sh
AI_NAME=Human/Ops \
PANTHEON_STATUS_ROOT=/home/lupin/pantheon \
python3 scripts/dispatch_pantheon_agora_remaining_work_2026-07-22.py
```

The dispatcher calls the governed `scripts/ai_status.py assign/note/reopen`
commands. It does not write `ai-status.json` or the activity log directly.

## Historical execution frontier

| Priority | Task | Repository | Owner | Reviewer | Depends on |
|---|---|---|---|---|---|
| P0 | `OPS-DISPATCH-LEASE-SYNC-001` | Pantheon | Codex | Claude | — |
| P0 | `PAN-LIFECYCLE-RECOVERY-001` | Pantheon | Codex2 | Antigravity | lease sync |
| P0 | `AG-PERF-TRUTH-001-BE` | Pantheon | Codex | Claude | lease sync |
| P0 | `AG-CAND-TRUTH-001-BE` | Pantheon | Claude | Codex2 | lease sync |
| P1 | `AG-WS-OPS-001` | Pantheon | Claude | Antigravity | lease sync |
| P1 | `PAN-SOURCE-FRESH-001` | Pantheon | Antigravity | Codex2 | lease sync |
| P1 | `OPS-PROMOTE-CONFLICT-RECOVERY-001` | Pantheon | Codex2 | Claude | lease sync |
| P2 | `OPS-TASK-PR-TRIAGE-001` | Pantheon | Antigravity | Codex | lease sync |
| P1 | `OPS-SECURITY-DEPENDENCY-001` | Pantheon | Codex | Claude | lease sync |
| P0 | `PPL-ALLOC-009` | Pantheon + execute-plans | Codex | Codex2 | lease sync, lifecycle recovery, existing PPL children |
| P0 | `TJ-E2E-012` | Pantheon + execute-plans | Codex2 | Claude | lease sync, lifecycle recovery, existing TJ children |
| P0 | `AG-PERF-TRUTH-001-FE` | execute-plans | Antigravity | Codex | `AG-PERF-TRUTH-001-BE` |
| P0 | `AG-CAND-TRUTH-001-FE` | execute-plans | Codex | Claude | `AG-CAND-TRUTH-001-BE` |
| P1 | `AG-WS-OPS-002` | Pantheon | Claude | Antigravity | `AG-WS-OPS-001` |
| P1 | `AG-COMPAT-001-BE` | Pantheon | Codex | Claude | both Agora truth BE tasks, `AG-WS-OPS-002` |
| P1 | `AG-COMPAT-001-FE` | execute-plans | Antigravity | Codex2 | both Agora truth FE tasks, `AG-COMPAT-001-BE` |
| P1 | `AG-COMPAT-002-GATE` | Pantheon | Codex | Claude2 | `AG-COMPAT-001-FE` |
| P1 | `AG-HOSTED-CLOSE-001` (superseded by `AG-HOSTED-CLOSE-002`) | Pantheon evidence | Antigravity | Claude | compatibility gate, source freshness |

The lease repair is the bootstrap frontier because the fleet must not fan out
through a known-broken status lifecycle. After it completes, the next frontier
has two tasks for each of the four enabled fleet owners and disjoint primary
file footprints.
Cross-repository product work is split into backend and frontend tasks. The two
workshop implementation tasks are serialized because they share the versioned
workshop route/store surface.

## DAG

```text
OPS-DISPATCH-LEASE-SYNC-001 ─> eight-way product/ops frontier
                             ├─> PPL-ALLOC-009 (also waits for lifecycle)
                             └─> TJ-E2E-012 (also waits for lifecycle)
PAN-LIFECYCLE-RECOVERY-001 ──┴─> both hosted closeouts

AG-PERF-TRUTH-001-BE ─> AG-PERF-TRUTH-001-FE ─┐
                                               ├─> AG-COMPAT-001-FE
AG-CAND-TRUTH-001-BE ─> AG-CAND-TRUTH-001-FE ─┤
                                               │
AG-WS-OPS-001 ─> AG-WS-OPS-002 ─> AG-COMPAT-001-BE ─┘
AG-COMPAT-001-FE ─> AG-COMPAT-002-GATE ─┐
PAN-SOURCE-FRESH-001 ───────────────────┴─> AG-HOSTED-CLOSE-001
```

## Hard rules

- Every displayed product datum is real with provenance/as-of metadata,
  explicitly sample, or explicitly unavailable. Never mix those states inside
  an unlabeled row/card.
- Governed writes require authz, idempotency, audit, and an authoritative
  receipt/readback. A local toast is not a write.
- Restart tests must preserve tenant/user isolation and must not touch
  production or live-capital routes.
- Existing accepted deployment remains read-only and fail-closed unless a
  task-scoped hosted proof temporarily enables governed dev writes and the
  watchdog restores the safe profile.
- Cleanup tasks must retain recoverability and evidence. No broad branch or
  filesystem deletion.

## Task briefs

- [OPS-DISPATCH-LEASE-SYNC-001](OPS-DISPATCH-LEASE-SYNC-001.md)
- [PAN-LIFECYCLE-RECOVERY-001](PAN-LIFECYCLE-RECOVERY-001.md)
- [AG-PERF-TRUTH-001-BE](AG-PERF-TRUTH-001-BE.md)
- [AG-PERF-TRUTH-001-FE](AG-PERF-TRUTH-001-FE.md)
- [AG-CAND-TRUTH-001-BE](AG-CAND-TRUTH-001-BE.md)
- [AG-CAND-TRUTH-001-FE](AG-CAND-TRUTH-001-FE.md)
- [AG-WS-OPS-001](AG-WS-OPS-001.md)
- [AG-WS-OPS-002](AG-WS-OPS-002.md)
- [AG-COMPAT-001-BE](AG-COMPAT-001-BE.md)
- [AG-COMPAT-001-FE](AG-COMPAT-001-FE.md)
- [AG-COMPAT-002-GATE](AG-COMPAT-002-GATE.md)
- [PAN-SOURCE-FRESH-001](PAN-SOURCE-FRESH-001.md)
- [AG-HOSTED-CLOSE-001](AG-HOSTED-CLOSE-001.md)
- [`AG-HOSTED-CLOSE-002` final evidence](../../../deployment/evidence/agora/ag-hosted-close-002.md)
- [OPS-PROMOTE-CONFLICT-RECOVERY-001](OPS-PROMOTE-CONFLICT-RECOVERY-001.md)
- [OPS-TASK-PR-TRIAGE-001](OPS-TASK-PR-TRIAGE-001.md)
- [OPS-SECURITY-DEPENDENCY-001](OPS-SECURITY-DEPENDENCY-001.md)
- [PPL-ALLOC-009 acceptance addendum](../2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-009-2026-07-22-acceptance-addendum.md)
- [TJ-E2E-012 acceptance addendum](../2026-07-11-trade-journey-e2e/TJ-E2E-012-2026-07-22-acceptance-addendum.md)

## Global closeout

The packet is complete only when all new tasks and the two reused closeout
tasks are terminal with reviewer approval and repository delivery metadata.
`AG-HOSTED-CLOSE-001` must archive the accepted FE/BFF pair, compatibility
hashes, restart-persistence readback on the replacement VM, source freshness,
and residual risks. EP5 remains outside this packet.

**2026-07-24 update:** `AG-HOSTED-CLOSE-001` was blocked on the
Governance/Workshop contract defects and superseded for closeout purposes by
the successor task `AG-HOSTED-CLOSE-002`, which archived the reviewer-consumable
final closeout for the accepted exact pair (frontend
`e4399e3ec68f`, BFF `f71c1f8b`, pair `ec91a4aa…c3de2`) after the
`AG-GOV-WORKSHOP-CONTRACT-001` / `AG-GOV-WORKSHOP-COMPAT-DEPLOY-001` repairs.
Evidence: `docs/deployment/evidence/agora/ag-hosted-close-002.md`.

The successor reached `done` and was archived at `2026-07-24T06:23:50Z`.
Evidence PR #4050 merged as `874103d1a`; the independently approved task brief
merged through PR #4051 as `cd4f42c4f`. The predecessor is terminal as
`superseded`, not a second hosted qualification request.
