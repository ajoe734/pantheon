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
