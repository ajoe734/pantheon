# Evolution Journal Producer Gap Execution Packet - 2026-07-13

Status: ready for fleet dispatch

Source gap spec:

- `docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/EVOLUTION_JOURNAL_PRODUCER_GAP.md`

Extends:

- `docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/INDEX.md`
  (labeling honesty — done; this packet produces the real events)

## Dispatch Command

Validate without mutating live status:

```sh
python3 scripts/dispatch_evolution_journal_producer_gap_2026-07-13.py --dry-run
```

Dispatch into the live supervisor status root:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/dispatch_evolution_journal_producer_gap_2026-07-13.py
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py sync
```

The dispatch script is idempotent: it preserves progress fields for already
started tasks and only appends assignment events for newly created tasks.

## Execution Order

Frontier is deliberately wide (see `docs/conventions/WAVE_PLANNING_PARALLELISM.md`):
only true build-on-top edges are declared.

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `EVOCHAIN-001` | Claude | Codex | Threshold-breach producer: paper telemetry -> incidents consumer, idempotent, fail-closed, daily. |
| 0 | `EVOCHAIN-002` | Codex | Claude | Enable evolution daily sweep on dev (remove/override profile gate) and prove one sweep tick. |
| 0 | `EVOCHAIN-003` | Codex2 | Claude | Postmortem publisher: incident resolve -> postmortem -> bridge -> proposal admission. |
| 0 | `EVOCHAIN-004` | Codex | Claude | Governance-owned canonical store + service read API for freeze_orders and rollbacks; BFF service_client path. |
| 0 | `EVOCHAIN-006` | Claude | Codex2 | Wire console mutation review actions to evolution service proposal APIs via BFF commands. |
| 0 | `EVOCHAIN-007` | Codex2 | Codex | Server-side persona/mutation filters + paging on /bff/management/evolution-journal; origin:seed marker. |
| 0 | `EVOCHAIN-008` | Claude | Codex | FE data-source badge semantics: live-degraded vs snapshot; name the degraded surfaces. |
| 0 | `EVOCHAIN-009` | Claude | Codex2 | FE journal card formal-entry fields + fixture badge for seed entries. |
| 1 | `EVOCHAIN-005` | Codex2 | Codex | BFF governance freeze/rollback write endpoints persist to the canonical store with audit fields. |
| 2 | `EVOCHAIN-010` | Codex | Claude | Producer-chain live verifier: breach -> incident -> proposal -> formal journal entry; add to run_e2e_verifiers.sh. |
| 3 | `EVOCHAIN-011` | Codex2 | Human/Ops | Dev deploy + closeout: compose update, sweep enabled, surfaces ok, live curl + hosted screenshots, residual risks. |

## Dependencies

```text
EVOCHAIN-001: none (blocked on LIN-003 for the default-validator acceptance
  criterion only — see docs/decisions/LIN-003-live-lineage-write-path.md;
  every other acceptance criterion is met)
EVOCHAIN-002: none
EVOCHAIN-003: none
EVOCHAIN-004: none
EVOCHAIN-006: none
EVOCHAIN-007: none
EVOCHAIN-008: none
EVOCHAIN-009: none
EVOCHAIN-005: EVOCHAIN-004
EVOCHAIN-010: EVOCHAIN-001, EVOCHAIN-002
EVOCHAIN-011: EVOCHAIN-003, EVOCHAIN-005, EVOCHAIN-006, EVOCHAIN-007, EVOCHAIN-008, EVOCHAIN-009, EVOCHAIN-010
```

Owner notes:

- Claude / Codex / Codex2 only. Antigravity is excluded as owner
  (2026-07-12 quota-storm residue); review overflow only if healthy.
- FE tasks (`EVOCHAIN-008`, `EVOCHAIN-009`) are cross-repo
  (`execute-plans`); owners must verify the FE repo path exists in their
  worktree before claiming done (phantom-done precedent).

## Hard Rules

- Do not modify any existing supervisor/poll cadence. New workers own their
  own interval envs.
- Threshold values live in live config; schema follows governance
  `ThresholdEvaluator` fixtures. No image rebuild required to tune values.
- Producers are fail-closed: missing telemetry emits diagnostics, never
  fabricated breaches.
- Keep `evo-vslice-1` seed; mark seed-derived journal entries
  `origin: seed` and badge them `fixture` in the FE.
- Deploy tasks are not done until live curl evidence is archived
  (babysit rule).

## Global Acceptance

Every task must record: branch + PR target; changed files and owned scope;
local validation output; hosted evidence for UI changes; reviewer approval;
merge SHA; residual risk with owner and expiry.

The packet is complete only when the hosted dev flow proves:

```text
real threshold breach -> incident (deduped) -> sweep proposal
  -> formal Evolution Journal entry
  -> Persona Fleet 最近 MUTATION links to that formal entry
  -> freeze_orders / rollbacks surfaces ok
  -> Evolution Journal aggregate surface ok (no SNAPSHOT DATA badge)
```
