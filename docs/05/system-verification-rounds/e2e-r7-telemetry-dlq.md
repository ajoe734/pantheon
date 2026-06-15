# E2E-R7 — Telemetry ingest validation + DLQ health

**Round:** E2E-R7 of the e2e business-flow verification campaign
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r7-telemetry-ingest
**Business flow:** paper fill → telemetry ingest (validated against the
RuntimeBinding store) → accepted, or → rejected to the dead-letter queue (DLQ).

## Live result (dev, 2026-06-15)

**Ingest validation works (safety holds).** An event with an unknown binding_id
is rejected:

```
POST /api/telemetry/ingest {binding_id: rb-DOES-NOT-EXIST} -> HTTP 400
  {"detail":"Event failed validation; see DLQ for details","status":"rejected"}
```

Events that cannot be attributed to an authoritative RuntimeBinding are refused
(Evidence contract E-1) — the telemetry surface cannot be poisoned by
unattributable events.

**DLQ is pinned at the incident threshold by unreplayable entries.**

```
telemetry DLQ health: count=100 threshold=100 unreplayable=99 pinned=True
FAIL: incident alert latched on permanently-stuck events
```

## Finding

The telemetry DLQ holds **100 entries (= the incident threshold), 99 of which are
binding-mismatch rejections** (`binding_id '…' not found in RuntimeBinding store`).
`replay_dlq()` only re-enqueues *write-failure* entries, so binding-mismatch
rejections **never drain** — they pin the DLQ at the incident threshold
indefinitely and keep the alert latched.

Notably, the stuck entries reference binding ids that are **now active**
(rb-016ccb04…, rb-f5c8e502…, the current fleet). They are the historical loss
from the 2026-06-12 interruption window (events emitted while the binding store
had not yet registered those bindings). They cannot be re-attributed now and are
designed to be unreplayable.

## Disposition

- **Shipped (code/CI):** a DLQ-health verifier + logic test + CI gate that FAILs
  when the DLQ is pinned at/over the incident threshold with unreplayable
  (binding-mismatch) entries. The live run currently FAILs (reporting the real
  stuck-DLQ condition).
- **Flagged (ops, not auto-purged):** provide a way to acknowledge / purge
  permanently-unreplayable binding-mismatch DLQ entries so the incident threshold
  reflects live failures, not historical loss. Not auto-purged here — the entries
  carry audit value and purging shared-infra DLQ state is an operator decision.

## Next round

E2E-R8: operator BFF read-surface cross-consistency (the same runtime/persona
reported consistently across /bff/runtimes, runtime-state, persona-health), or
deepen R7 by implementing a binding-mismatch DLQ acknowledge/purge path.
