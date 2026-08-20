# PFG-SOURCE-STATE-20260820 evidence

`controller_worker` remains the sole Source controller owner. Its local
`ControllerStateStore` now persists a bounded v2 projection: identity
inventories, counters, schedule summary, and terminal SourceRecord summaries.
It never persists a raw controller readback or a previous controller state.
The existing training-session dataset-authority readback consumer accepts both
the preserved v1 format and the migrated v2 controller identity contract.

On startup, a valid v1 snapshot is read once, compacted to v2, and atomically
replaced. The prior checksummed v1 envelope is kept read-only beside the state
file for diagnosis/rollback. A checksum mismatch still fails closed.

`/health`, `/healthz`, `/readyz`, and `/metrics` now read only the bounded
controller snapshot, with a 1 MiB default state-size budget before parsing.
They do not replay the connector, schedule, ingest, or evidence journals.
The detailed `/api/source-ingest/controller/readback` endpoint remains the
explicit operator/controller readback surface; it is not a health probe.

No controller mode or provider execution path changed. In particular,
`reconcile_only` still reports `provider_egress_attempted: false`; the task
does not enable continuous provider egress or create a second scheduler/store.

See `evidence.json` for the owned/not-changing boundary, test commands, and
reviewer handoff.
