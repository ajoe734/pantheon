# RW-04-EXPERIMENT-001 Finalization Review

**Task:** RW-04-EXPERIMENT-001 — Publish Experiment Launch lifecycle and async run contract  
**Owner:** Claude  
**Reviewer:** Codex  
**Status:** review_approved → done  
**Date:** 2026-04-19

## Delivery Summary

The RW-04 experiment launch BFF contract is published and fully reviewed. The following artifacts were delivered:

- `docs/bff/RW-04-experiment-launch.md` — full route contract including launch, list, detail, and cancel routes; canonical state machine; canCancel invariants; degradation rules; async status delivery semantics
- `docs/examples/RW-04-experiment-launch.json` — example payloads covering launch response (queued), running detail, completed detail with artifacts, failed detail, list/history, and queued-cancel response (direct queued→canceled path)

## Acceptance Criteria Verification

| Criterion | Status | Evidence |
|---|---|---|
| Launch and run-status routes are published | **met** | `POST /api/v1/experiments/launch`, `GET /api/v1/experiments/{experiment_id}`, `GET /api/v1/experiments` all specified with required request/response fields |
| Async state machine and cancel authority are explicit | **met** | Legal transition graph (`queued→running→completed\|failed\|canceled` and `queued→canceled`), illegal transitions listed, `canCancel` authority invariants for all states documented |
| Experiment history does not depend on inferred runtime state | **met** | Required invariant: "The history route must return persisted run records. It must not reconstruct history by scraping live worker state or omitting terminal runs that are no longer active." |

## Review Notes (Codex)

RW-04 契約審查通過：experiment launch/history/detail/cancel 路由已發布；狀態機明確允許 queued -> canceled 的預執行取消路徑，並一致要求 terminal payload 與 cancel response 的 allowedActions.canCancel=false；history ledger 也明確要求來自 persisted run records，不得由 live worker state 推導。

## Key Contract Decisions

- `queued → canceled` is a legal transition (pre-execution cancel before worker picks up the run)
- `canCancel` may be `true` only while the run is in a non-terminal cancelable state (`queued` or `running`)
- `canCancel` must be `false` in all terminal payloads and in the cancel command response itself
- History ledger must come from persisted run records, not live worker state inference
- `experiment_id` is the canonical run identity; frontend must not derive it from `ticket_id + queued_at`

## Delivery Commit

Contract delivered at commit `39923a3` (RW-04-EXPERIMENT-001: resolve queued->canceled transition graph contradiction).

## Downstream

RW-05-ARTIFACT-COMPARE-001 depends on RW-04 producing stable `experiment_id` and durable `artifact_ids[]`. That task is now unblocked pending its own contract work.
