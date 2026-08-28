# Pantheon Architecture Cleanup GAP / SA / SD — 2026-08-27

Status: **code-first architecture cleanup baseline approved; execution catalog added
2026-08-28 and gated by independent plan freeze**

Scope: Pantheon product backend, the separate `execute-plans` frontend, Management
loop truth, and dev deployment verification. The 2026-08-27 files remain the
planning baseline and do not implement cleanup. The operator later authorized task
materialization; the 2026-08-28 execution catalog records that separate decision.

## Purpose

This package answers one question: which existing implementation owns each
behavior, which callers must move, which duplicated mechanisms must merge, which
surfaces can be removed, and which claims still need runtime evidence before a
decision is safe.

Every disposition uses exactly one of these labels:

- **KEEP** — the sole canonical owner remains.
- **MIGRATE** — callers move to the canonical owner; no second owner is added.
- **MERGE** — two implementations contain required behavior and must become one
  implementation before the old path is deleted.
- **REMOVE** — the artifact has no valid caller, is shadowed, or has been replaced.
- **VERIFY** — code inspection is insufficient; caller or runtime evidence is a
  prerequisite to KEEP, MIGRATE, MERGE, or REMOVE.

The machine-readable matrix contains 102 scoped decisions across all nine requested
priorities: 15 KEEP, 29 MIGRATE, 25 MERGE, 26 REMOVE, and 7 VERIFY. A row's label
describes the current artifact; an action may move callers and then delete the old
artifact without creating a second disposition or a second owner.

## Frozen audit baseline

| Evidence plane | Frozen identity / observation |
|---|---|
| Pantheon source | `origin/dev` at `f4a14b29789d6a0e53d64df066042c53bb6c5534` |
| `execute-plans` source | `origin/dev` at `6766ebe4c95153c019b206cc6326a5a0d9771138` |
| Hosted accepted frontend | `a10767709b01ed20d32c2590543c37902ad1b671` |
| Hosted accepted BFF | `3c79a185a97d920f41005bd41675433a046b6ece` |
| Hosted profile | `read-only`; deployment state `accepted` |
| Root-stack source at runtime inspection | `c6202636beddb906f59e67596b7469f02049a87f` |
| Runtime inspection time | 2026-08-27 UTC |

Source truth and hosted truth are intentionally separate. A current `dev` file is
not proof that its behavior is deployed, and an accepted older FE/BFF pair is not
proof that a newer root-stack worker is healthy.

## Documents

1. [`CURRENT_GAP_2026-08-27.md`](CURRENT_GAP_2026-08-27.md) — code and runtime
   findings, ordered by the nine requested priorities, plus the complete
   disposition matrix.
2. [`SA_ARCHITECTURE_CLEANUP_2026-08-27.md`](SA_ARCHITECTURE_CLEANUP_2026-08-27.md)
   — target ownership, dependency direction, invariants, migration waves, and
   deletion policy.
3. [`SD_ARCHITECTURE_CLEANUP_2026-08-27.md`](SD_ARCHITECTURE_CLEANUP_2026-08-27.md)
   — file-level design and verification gates from which execution tasks can be
   generated later.
4. [`DISPOSITION_MATRIX_2026-08-27.json`](DISPOSITION_MATRIX_2026-08-27.json) —
   machine-readable planning inventory. It is not a canonical task packet.
5. [`EXECUTION_DAG_2026-08-28.md`](EXECUTION_DAG_2026-08-28.md) — the complete
   parallel DAG, exclusive hot-file owners, existing-task reconciliation, and
   materialization boundary.
6. [`EXECUTION_TASK_CATALOG_2026-08-28.json`](EXECUTION_TASK_CATALOG_2026-08-28.json)
   — exact contracts for one plan-freeze task and 28 execution/integration tasks.
   Canonical task authority remains the V2 TaskStore after Human/Ops materialization;
   this file is the immutable reviewed source catalog.

## Non-goals

- no new product feature;
- no security-hardening program;
- no new compatibility façade, read-store wrapper, or alternate truth database;
- no task-state mutation from the original 2026-08-27 planning delivery; the later
  2026-08-28 catalog is materialized only through governed local tooling;
- no claim that all twelve functional loops are closed; and
- no deletion based only on file size or a name containing `legacy`, `stub`, or
  `compat`.

The cleanup is complete only when the canonical behavior remains functional after
callers have moved and the old implementation is actually deleted. Moving the same
monolith into another filename is not completion.
