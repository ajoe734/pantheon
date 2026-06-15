# E2E-R14 — Telemetry pipeline health (backpressure / buffer / errors)

**Round:** E2E-R14 (second campaign)
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r14-backpressure
**Business flow:** paper fills + heartbeats → telemetry ingest buffer → writer →
runtime summary. A saturating buffer / critical backpressure / dropped events
silently lose telemetry before it is projected.

## Verification program

`scripts/verify_e2e_telemetry_pipeline_health.py` (+ unit test). Evaluates the
telemetry `/api/telemetry/stats` object and FAILs on buffer saturation, critical
backpressure, rejected events, an unbounded enqueue/dequeue backlog, or a
critical error rate.

## Live result (dev, 2026-06-15)

```
telemetry pipeline health:
  buffer util%=0.0 size=0 rejected=0 | pressure=normal errors=0
  total_enqueued=932 total_dequeued=932 (no backlog)
OK: telemetry pipeline healthy
```

## Finding

Good-news round: the telemetry ingest pipeline is healthy — buffer fully drained
(0% of 100k capacity), zero rejected events, pressure level normal, zero recent
errors, and enqueued == dequeued (no backlog). The right-half data path that
E2E-R2/V11 proved live is not silently dropping telemetry under the current load.

## Disposition

- **Shipped (code/CI):** the pipeline-health verifier + logic test — a regression
  gate against telemetry buffer saturation / backpressure / event loss.
- CI wiring for this and recent script verifiers is consolidated in E2E-R20.

## Next round

E2E-R15: research → experiment → artifact sealing (left-half provenance, deeper).
