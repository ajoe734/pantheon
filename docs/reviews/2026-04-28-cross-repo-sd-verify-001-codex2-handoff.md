---
task_id: CROSS-REPO-SD-VERIFY-001
owner: Codex2
reviewer: Claude2
status: ready_for_review
created_at: 2026-04-28
source_packet: docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md
sidecar_packet: support/sidecars/CROSS-REPO-SD-VERIFY-001/CROSS-REPO-SD-VERIFY-001-SIDECAR-ACCEPTANCE.md
mutates_canonical: false
---

# CROSS-REPO-SD-VERIFY-001 Handoff

## Scope

Verified the current SD boundary across:

- `pantheon`
- `front-ai-trading-system`
- LEAN bridge under `pantheon/lean/Algorithm.Python/pantheon_algo`

This review checks command authority, trace / error UX, runtime telemetry hooks,
and absence of frontend / LEAN parallel authority paths. It does not claim EP5
live / canary execution proof, broader reconciliation closure, or production
activation.

## Result

PASS with one bounded caveat: the frontend preserves structured BFF error
`code` and `message` from `detail.error`; foundation-specific metadata remains
backend-owned and is covered by BFF tests, but the frontend `BffError` class does
not expose the full `detail.foundation_error` object as a typed field.

## Evidence

| Target | Result | Evidence |
|---|---|---|
| Frontend governed command authority | PASS | `../front-ai-trading-system/src/lib/bffClient.ts:1235` sends generic operator commands to `POST /api/v1/operator/commands`; incident, deployment diff, rollback, and mutation helpers at `:1242`, `:1254`, `:1263`, `:1272`, `:1281`, and `:1293` all use the same BFF route. |
| Frontend error UX | PASS with caveat | `../front-ai-trading-system/src/lib/bffClient.ts:178` defines `BffError`; `:190` parses BFF `detail.error.code` and `detail.error.message`, preserving structured operator-facing error code / message. It does not type the full `detail.foundation_error` payload. |
| Frontend lineage / trace UX | PASS | `../front-ai-trading-system/src/lib/bffClient.ts:622` defines `lineageApi`; `:627`, `:646`, and `:665` read BFF lineage list, graph, and edge detail routes. `../front-ai-trading-system/src/pages/lineage/Lineage.tsx:161`, `:183`, and `:205` consume those APIs rather than reconstructing lineage locally. |
| BFF command authority | PASS | `services/control-plane/bff/main.py:11098` defines `POST /api/v1/operator/commands`; `:11104` through `:11107` accept trace, correlation, request, and idempotency headers; `:11119` builds the foundation command context; `:11211` persists command and audit / foundation context; `:11238` exposes command receipt polling. |
| Runtime telemetry hook | PASS | `services/telemetry/main.py:49` documents the source-runtime-telemetry trace route; `:533` defines `GET /api/telemetry/lineage/traces/<trace_id>/source-runtime-telemetry`, backed by `source_runtime_telemetry_trace`. |
| Derived lineage boundary | PASS | `services/registry/lineage/read_model_contract.md:212` names `source_runtime_telemetry_trace` as an operator-facing read model; `:218` keeps it derived and explicit about missing edges. |
| LEAN bridge authority boundary | PASS | `lean/Algorithm.Python/pantheon_algo/base.py:4` describes signal consumer wiring; `:50` schedules `SignalConsumer.drain`; `:62` exposes only `flush_rebalance`; `:71` imports `SignalConsumer` and `SignalStoreClient`. No governance command, rollback, kill-switch, deployment approval, or broker authority API is exposed from the bridge. |
| No frontend / LEAN bypass path found | PASS | `rg` across `../front-ai-trading-system/src` and `lean/Algorithm.Python/pantheon_algo` found governed write helpers converging on BFF `postJson('/api/v1/operator/commands', ...)`; LEAN search found only signal-consumer execution wiring. Textual mentions of runtime, broker, rollback, and kill-switch are read surfaces, UI labels, or BFF route references. |

## Commands Run

```bash
rg -n "POST /api/v1/operator/commands|/api/v1/operator/commands|operatorApi" \
  ../front-ai-trading-system/src ../front-ai-trading-system/.coordination

rg -n "source-runtime-telemetry|lineageApi|BffError|foundation_error|trace" \
  ../front-ai-trading-system/src

rg -n "operator/commands|foundation_error|X-Trace-Id|X-Correlation-Id|X-Idempotency-Key" \
  services/control-plane/bff/main.py services/control-plane/bff/test_governance_command_submission.py

rg -n "source_runtime_telemetry_trace|source-runtime-telemetry|LINEAGE_TARGET_NOT_FOUND" \
  services/telemetry services/registry/lineage

rg -n "SignalConsumer|SignalStoreClient|broker|order|kill|rollback|operator/commands|authority" \
  lean/Algorithm.Python/pantheon_algo

rg -n "runtime-manager|broker|ibkr|InteractiveBrokers|kill-switch|rollback|operator/commands|/api/v1/operator/commands|fetch\(|axios|postJson" \
  ../front-ai-trading-system/src lean/Algorithm.Python/pantheon_algo
```

## Tests

```bash
pytest services/control-plane/bff/test_governance_command_submission.py \
  services/runtime-manager/test_runtime_manager.py \
  services/foundation/tests -q
# 59 passed in 4.41s

pytest services/telemetry/lineage_read/test_service.py \
  services/telemetry/test_main_routes.py -q
# 39 passed in 1.04s
```

## Reviewer Notes

- The parent acceptance criteria are satisfied for current repo state.
- No canonical policy or runtime implementation changes were required.
- The frontend `BffError` caveat is not a blocker for this verification because
  operator-visible code / message are preserved and foundation envelope behavior
  is validated at the BFF boundary. A later frontend UX hardening task could add
  typed access to `detail.foundation_error` if operators need trace details
  surfaced directly in the browser.
- This handoff should not be interpreted as live / canary readiness. EP5 live
  proof remains separately gated by the EP5 packet and explicit human approval.
