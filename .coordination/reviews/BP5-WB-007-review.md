# Review: BP5-WB-007 — Trainer Workbench packet family

**Reviewer:** Codex  
**Task:** BP5-WB-007  
**Date:** 2026-04-16  
**Decision:** CHANGES REQUESTED

---

## Artifact reviewed

- `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` (Trainer Workbench section)

---

## Findings

### 1. Blocking: the `TW-04` dependency row mis-scopes the full `TeachingEvent` schema as a `TW-01` upstream dependency

The task brief explicitly asked for correctness of the internal dependency chain, and the current `TW-04` row is off by one contract boundary.

- `PACKET_FAMILY.md:211` currently says `TW-04 Teaching Replay` depends on "`TW-01`: full `TeachingEvent` schema and transcript events".
- But this same packet keeps the full replay schema as `TW-04`'s own backend gap and prerequisite, not `TW-01`'s:
  - `PACKET_FAMILY.md:157` defines **`TeachingEvent` schema (full)** under `TW-04`.
  - `PACKET_FAMILY.md:165` says replay depends on `TW-01` transcript events and `TW-03` compare evidence being stable.
- The backlog matches that narrower dependency:
  - `pantheon-console-workbench-backlog.md:355-356` says replay is blocked by the standalone replay contract, append-only `TeachingEvent` schema, event ordering, replay cursor semantics, and before/after artifact refs.
  - `pantheon-console-workbench-backlog.md:374` says the upstream dependency is "Teaching dialog transcript events plus Before/after compare evidence refs."

Why this blocks approval:

The dependency row currently makes it look like `TW-01` owns delivery of the **full** replay-grade `TeachingEvent` schema, when the packet's own gap tables and the backlog keep that as `TW-04` scope. That can mis-sequence follow-on work and muddle ownership when the family is broken into execution slices.

Requested fix:

1. Update the `TW-04` row in the Internal Ordering and Dependency Chain section so the `TW-01` dependency stays limited to transcript events / session identity, consistent with the backlog.
2. Keep the full `TeachingEvent` schema requirement scoped to `TW-04`'s own backend gaps and packetization prerequisite.

---

## Recommendation

Do not approve `BP5-WB-007` yet.

After correcting the dependency row above, hand the task back for re-review. The four-module inventory, Wave 3 ordering, and backend-gap coverage otherwise look aligned with the Trainer Workbench backlog.
