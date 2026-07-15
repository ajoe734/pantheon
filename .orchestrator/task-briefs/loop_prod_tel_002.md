# Task Brief: LOOP-PROD-TEL-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Canonical loop-run and Trade Journey lifecycle projector
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Branch: `task/LOOP-PROD-TEL-002` from `dev`
- Next: merge the implementation PR, deploy its exact merge SHA to dev, capture
  authoritative readback, then publish the checksummed evidence manifest for
  independent review and owner closeout.

## Summary
從真實 signal/decision/order/fill/position/reconciliation append events 投影 canonical loop-run 與 Trade Journey；維持單一 identity chain，manual/cron rebuild 只能標示 backfill，不能成為 live truth。

## Owned delivery boundary

- Canonical committed-telemetry cursor and ingest ordering.
- Real paper signal/decision/order/fill/position and scheduled reconciliation
  lifecycle producers.
- Crash-safe exact-payload lifecycle append outbox after local admission.
- One atomic projector generation for Trade Journey and loop-run read models.
- BFF truth gates that reject backfill/recovery/degraded controller state as
  canonical live truth.

The task does not claim a cross-media transaction across Redis signal claim,
the paper ledger, and lifecycle outbox. RPO=0 applies to lifecycle append
commands after durable local outbox admission. Live broker and live capital
effects remain outside scope and disabled.
