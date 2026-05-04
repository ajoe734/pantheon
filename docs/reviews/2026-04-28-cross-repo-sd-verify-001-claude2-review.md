---
task_id: CROSS-REPO-SD-VERIFY-001
owner: Codex2
reviewer: Claude2
status: review_approved
reviewed_at: 2026-04-28
source_packet: docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md
owner_handoff: docs/reviews/2026-04-28-cross-repo-sd-verify-001-codex2-handoff.md
sidecar_packet: support/sidecars/CROSS-REPO-SD-VERIFY-001/CROSS-REPO-SD-VERIFY-001-SIDECAR-ACCEPTANCE.md
mutates_canonical: false
---

# CROSS-REPO-SD-VERIFY-001 Reviewer Notes (Claude2)

## Disposition

APPROVED. The owner handoff packet at
`docs/reviews/2026-04-28-cross-repo-sd-verify-001-codex2-handoff.md` covers each
parent acceptance target with concrete file references and replayable commands;
the cited dependencies (`SD-FND-002`, `SD-LIN-TRACE-001`) are both archived
`done`; and the rerun tests pass cleanly on the current working tree.

This approval covers the **boundary verification** scope only. It does not
authorize EP5 live / canary execution, broader SD-09 reconciliation closure
beyond what is already approved under `SD-RECON-001`, or any production
activation of Qlib / TRL.

## Evidence Re-checked By Reviewer

| Claim | Method | Result |
|---|---|---|
| BFF `POST /api/v1/operator/commands` accepts trace/correlation/request/idempotency headers and builds foundation command context | Read `services/control-plane/bff/main.py:11098-11220` | Confirmed. Route signature accepts `X-Trace-Id`, `X-Correlation-Id`, `X-Request-Id`, `X-Idempotency-Key`; `_build_foundation_command_context` populates idempotency / audit / envelope; `_foundation_bff_error` wraps validation failures into the foundation error envelope. |
| Telemetry `source-runtime-telemetry` route is the operator-facing derived trace | Read `services/telemetry/main.py:49` (docstring) and `:533` (route) | Confirmed. Route is registered, backed by `source_runtime_telemetry_trace`, and documented as derived-only. |
| Lineage read model is explicitly derived-only with missing-edge semantics | Read `services/registry/lineage/read_model_contract.md:212-228` | Confirmed. `source_runtime_telemetry_trace` listed under query families, derived-only invariant restated, missing nodes captured in `missing_edges[] / conflict_markers[] / reconciliation_closure.proof_gaps[]`. |
| Frontend `BffError` preserves structured BFF error code / message | Read `../front-ai-trading-system/src/lib/bffClient.ts:178-208` | Confirmed. `parseErrorResponse` reads `body.detail.error.code` / `message` first; falls back to `body.error` string and `body.status === 'error'` shapes; never silently collapses to a generic success. The `detail.foundation_error` payload is not yet a typed field — see Caveat below. |
| Frontend lineage UI consumes BFF lineage routes | Read `../front-ai-trading-system/src/lib/bffClient.ts:622-679` | Confirmed. `lineageApi.list / getGraph / getEdgeDetail / getInspirationGraph` call `/api/v1/lineage*` BFF routes only; no client-side graph reconstruction in this client. |
| All governed write paths converge on BFF `/api/v1/operator/commands` | Read `../front-ai-trading-system/src/lib/bffClient.ts:1235-1299` | Confirmed. `sendCommand`, `sendIncidentActionCommand`, `escalateDeploymentDiff`, `approveRollback`, `rejectRollback`, `approveMutation`, `rejectMutation` all `postJson('/api/v1/operator/commands', ...)`. No alternate runtime / broker / LEAN endpoint is invoked. |
| LEAN bridge is execution-side only | Read `lean/Algorithm.Python/pantheon_algo/base.py:1-90` | Confirmed. Bootstraps `SignalConsumer` via `SignalStoreClient`, schedules `SignalConsumer.drain()` every minute, exposes only `flush_rebalance(run_id)`. No governance / rollback / kill-switch / broker authority surface. |

## Tests (rerun by reviewer)

```bash
PYTHONPATH=/home/lupin/.local/lib/python3.12/site-packages \
  python3.12 -m pytest \
  services/control-plane/bff/test_governance_command_submission.py \
  services/runtime-manager/test_runtime_manager.py \
  services/foundation/tests -q
# 59 passed in 2.97s
```

```bash
PYTHONPATH=/home/lupin/.local/lib/python3.12/site-packages \
  python3.12 -m pytest \
  services/telemetry/lineage_read/test_service.py \
  services/telemetry/test_main_routes.py -q
# 40 passed in 0.69s
```

The owner handoff cited 39 lineage / telemetry tests; the working tree now
shows 40, which matches the SD-RECON-001 reviewer note (`source_runtime_telemetry_trace`
treats telemetry-only `position_snapshot` events as derived position-snapshot
proof). This is consistent with the dependency state and not a regression.

## Caveat (carried forward, not blocking)

`BffError` does not yet expose `detail.foundation_error` as a typed field. The
operator-visible `code` and `message` are preserved, and foundation envelope
behavior is validated at the BFF boundary by
`test_governance_command_submission.py`. A later frontend UX hardening task may
add typed access if operators need trace / foundation metadata surfaced
directly; this is bounded and outside the current parent acceptance.

## Boundary Reaffirmed

- This review does not promote `EP5-002-RUNTIME-LIVE-PROOF-001`; that task
  remains gated by `EP5-002-PACKET-PREP-001` and explicit human approval.
- This review does not change `SD-RECON-001` reconciliation scope; deeper
  fill / cancel / position / drift / alert work continues there.
- This review does not touch L1 canonical truth or runtime implementation.

## Decision

`approve` — return `CROSS-REPO-SD-VERIFY-001` to owner `Codex2` for
finalization to `done`.
