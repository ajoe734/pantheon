# Agora Gap Assessment — 2026-07-12

Status: execution in progress.

The first closure gap is durable Strategy Workshop storage on the Pantheon dev
BFF. Dev deployment must select the Postgres workshop store, report that
selection at startup without logging credentials, and prove that a workshop is
still readable after the BFF container restarts.

Execution packet:

- `docs/bff/execution-tasks/2026-07-12-agora-gap-closure/INDEX.md`
- `docs/bff/execution-tasks/2026-07-12-agora-gap-closure/AG-GAP-001-workshop-postgres-live.md`

This assessment does not change workshop API semantics, database schema, or
staging/live deployment policy.
