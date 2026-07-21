# Delivered Outside The Task System — 2026-07-20 Reconciliation Record

Last updated: 2026-07-20
Status: record, not blueprint authority (`DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md` §3)
Scope: work that is merged and verifiable in source but has no canonical task row and no archive snapshot

## 1. Why this file exists instead of archive entries

An audit on 2026-07-20 found ~21 task IDs that are declared in packet `INDEX.md`
files, have written task briefs, and are **demonstrably delivered in merged
code** — yet have no row in `ai-status.json` and no snapshot under
`ai-task-archive/tasks/`. The board therefore under-reports delivered work, and
every later audit rediscovers these IDs as "never materialized".

The obvious fix — writing the missing `ai-task-archive/tasks/<ID>.json`
snapshots by hand — was rejected deliberately. Archive snapshots are governance
evidence: reaching one requires `command_done`'s owner check, the
`review_approved` precondition, `validate_loop_completion_claim`, distinct
owner/reviewer identities, and an activity-log lineage entry. Hand-authoring the
files would assert that this ladder ran when it did not. In a system whose
control plane is built to fail closed on unproven governance claims, fabricating
the proof is worse than an inaccurate count.

`reconcile_merged_done` is also not applicable: by its own docstring it recovers
"an already-delivered task whose canonical row lost the review-approved
transition". These IDs never had a canonical row at all.

So this file records the truth in the record layer, where it belongs. If any of
these IDs must become tracked tasks, they should be re-entered through the
normal ladder with these commits as the delivery evidence, not back-dated.

## 2. Delivered, verified in source, untracked

Verification method: read the merged source and tests, not status documents.
Checked against `origin/dev` for Pantheon and `ajoe734/execute-plans@dev` for the
frontend.

### Persona interaction, daily strict operator (packet 2026-07-17)

| ID | Evidence |
|---|---|
| `PINT-011` | `f590dd449` (PR #3803); packet `PINT-011-CONTRACT.md`; `services/control-plane/specs/agora/v10/persona_interaction_daily.schema.json` |
| `PINT-012` | `fd907b0dd`, `e1ceaab89` (PR #3806); `services/control-plane/bff/agora/interaction/provider.py`, `runner.py:393` real OpenClaw invocation; simulator removed and regression-asserted in `tests/test_pint_012_real_persona_provider.py:257` |
| `PINT-013` | `74a1d585d`, `8662e8495` (PR #3810); `agora/interaction/store.py`; `tests/test_pint_013_durable_interactions.py` |
| `PINT-014` | `8741a19df`, `4ebe84a3d`, `5a71daf12`, `0b5041edb` (PR #3807); `agora/candidate_decisions/` |
| `PINT-015` | execute-plans PR #384-#386; `src/management/pages/PersonaDetail.tsx`, `src/agora/components/DailyInteractionTimeline.tsx` |
| `PINT-016` | `4245a28d8` (PR #3804); `bff/main.py:7071` `/bff/auth/readiness`, `:6925` `/bff/me`; `tests/test_pint_016_strict_browser_readiness.py` |
| `PINT-017` | execute-plans `0c5a7aea` (PR #382); Pantheon `e78ca0239`, `80a3646cb` (PR #3844); `scripts/release-candidate.mjs` profile separation |

### Persona interaction (packet 2026-07-12)

| ID | Evidence |
|---|---|
| `PINT-006` | execute-plans `ff195d81` (PR #275); Pantheon closeout `ca36f1209` (PR #3480) |
| `PINT-008` | execute-plans `60a08991` (PR #282), `b807e0c3` (PR #283); Pantheon PRs #3462/#3465/#3467/#3468 |
| `PINT-009` | execute-plans PR #277; Pantheon `c42044f2c` (PR #3469), `e2487b1ab` (PR #3470) |
| `PINT-010-R2` | `PINT-010-R2-EVIDENCE.md` (`Status: accepted`) plus two bundle scans; code lanes `1ccff3435`, `c02725bcc`, `8eb22f946`, `8a1f17be9`, `5f0824dad` |

### Agora UI polish (packet 2026-07-13)

`AG-UIPOL-001`, `-002`, `-003`, `-004`, `-005`, `-007`, `-008`, `-009`, `-011`
are delivered with merged PRs on both sides and 58 artifacts under the packet's
`evidence/`. Note `AG-UIPOL-011` was finalized by commit `435c01849`
(2026-07-14) which no task record ever referenced.

## 3. Not to be re-dispatched

- `PINT-010` is superseded. `docs/bff/execution-tasks/2026-07-12-persona-interaction/INDEX.md:223-226`:
  `PINT-010-R2` "carries the original `PINT-010` acceptance … It must not reopen
  or duplicate completed feature implementation."

## 4. Genuinely open at the time of writing

- `PINT-018` hosted daily acceptance and closeout. Zero commits, zero evidence;
  dependencies `PINT-013` through `PINT-017` are all merged, so it is runnable.
- `AG-UIPOL-006` delivered both sides, brief still `review`, and its
  `parity-matrix.md` rows are not re-verdicted.
- `AG-UIPOL-010` frontend merged (execute-plans PR #321), Pantheon-side evidence
  absent, brief `todo`.
