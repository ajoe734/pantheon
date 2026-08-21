# LIFECYCLE-PROJ-RELATIONAL-WORKER-20260821 evidence

This directory is the pre-review evidence manifest for connecting the bounded
Trade Journey reducer to `ProjectionStore` in an explicit shadow-only worker.

The relational worker is disabled unless
`LIFECYCLE_PROJECTOR_WRITER_BACKEND=shadow`. It requires the separate
`LIFECYCLE_PROJECTOR_PROJECTION_DSN` runtime-DML credential and does not run
DDL. Any other enabled backend value fails closed: production/read cutover is
owned by later migration, capacity, and cutover tasks.

The focused real-Postgres check was run against an isolated local E2E database
using a unique test schema that the test removes in `finally`. It verifies
restart hydration from stage contract fields, exact duplicate safety, ignored
receipt disposition, and contiguous checkpoint advancement without creating a
legacy `controller_state.json` file.

`SHA256SUMS` is generated from the repository root. It intentionally does not
hash itself.

## Rebase verification

The existing PR was rebased onto `origin/dev` commit
`bb83df12e3cec11de0f441850f08a179ddd7394a` before fresh review. The focused
real-Postgres restart/duplicate/ignored-receipt regression passed again.

The full `services/trade_journey` suite against a throwaway local PostgreSQL
16 container produced `176 passed, 2 failed`. Both failures are outside this
task's diff and are present in the rebased `origin/dev` surface: the BFF-only
deployment contract still expects a compose command without the now-present
`--force-recreate`, and migration restart submits a mixed duplicate/new batch
that the already-merged ProjectionStore correctly rejects. They are recorded
in `evidence.json` for the fresh independent reviewer; they are not presented
as a passing task verification.
