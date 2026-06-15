# E2E-R19 — Telemetry coverage (no runtime running blind)

**Round:** E2E-R19 (second campaign)
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r19-telemetry-coverage
**Business flow:** every active runtime must emit telemetry that projects into a
runtime summary; a runtime with no summary is running blind.

## Verification program

`scripts/verify_e2e_telemetry_coverage.py` (+ unit test). For every ACTIVE
runtime-binding, asserts `/api/v1/telemetry/{runtime_id}/summary` resolves to a
real summary (trade count, heartbeat, or artifact id).

## Live result (dev, 2026-06-15)

```
telemetry coverage over 16 active runtimes:
  with summary: 16  missing: 0
OK: every active runtime reports a telemetry summary
```

## Finding

Good-news round: 100% telemetry coverage — all 16 active runtimes report a
runtime summary; none is running blind. Combined with E2E-R14 (pipeline healthy)
and E2E-R2 (real fills flow), the right-half telemetry path has full coverage of
the active fleet.

## Disposition

- **Shipped (code/CI):** the telemetry-coverage verifier + logic test — a
  regression gate that fails if an active runtime ever runs without a telemetry
  summary.
- CI wiring consolidated in E2E-R20.

## Next round

E2E-R20: consolidation — master index for R11–R20, single glob CI wiring for the
script verifiers, and the R16 worker-image rollout.
