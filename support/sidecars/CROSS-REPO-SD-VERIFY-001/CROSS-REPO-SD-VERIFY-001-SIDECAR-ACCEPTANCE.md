# CROSS-REPO-SD-VERIFY-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `CROSS-REPO-SD-VERIFY-001` - Verify multi-repo SD boundary  
**Parent Owner**: `Codex`  
**Parent Reviewer**: `Codex2`  
**Parent Status**: `todo`  
**Sidecar Task**: `CROSS-REPO-SD-VERIFY-001-SIDECAR-ACCEPTANCE`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Codex`  
**Helper Kind**: `acceptance_packet`  
**Generated**: `2026-04-27`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 canonical truth, core
> contract truth, runtime / registry / governance implementation, or any
> cross-repo runtime surface. It gives the parent owner a concrete acceptance
> packet for verifying the SD boundary across `pantheon`,
> `front-ai-trading-system`, and the LEAN bridge in `pantheon-lean`.

## 1. Executive Summary

`CROSS-REPO-SD-VERIFY-001` should verify that the current multi-repo surface has
one command authority path and one trace / error / telemetry interpretation:

1. the frontend submits operator commands only through Pantheon BFF authority,
2. the BFF emits stable command receipts, shared foundation error envelopes, and
   audit / idempotency context,
3. telemetry lineage is consumed as a derived read model, not recomputed by the
   frontend or LEAN bridge,
4. LEAN remains an execution-side signal consumer bridge and does not become a
   parallel governance or command authority,
5. no frontend, LEAN, or auxiliary path bypasses runtime-manager / BFF authority
   for live / canary, kill-switch, rollback, or deployment actions.

The two named dependencies are complete. `SD-FND-002` gives the parent a tested
BFF command pilot and runtime-manager kill-switch pilot using shared foundation
envelopes. `SD-LIN-TRACE-001` gives the parent a tested
`source_runtime_telemetry_trace` read-model route and derived-only lineage
contract.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable board entry for parent and sidecar owner / reviewer / dependency truth |
| `.orchestrator/task-briefs/cross_repo_sd_verify_001_sidecar_acceptance.md` | Confirms this helper is support-only and must hand off to `Codex` |
| `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Defines parent task scope and the SD acceptance shape |
| `ai-task-archive/tasks/SD-FND-002.json` | Confirms foundation adoption dependency is `done` and reviewed |
| `docs/reviews/2026-04-27-sd-fnd-002-codex-handoff.md` | Names the BFF and runtime-manager pilot paths and owner verification |
| `docs/reviews/2026-04-27-sd-fnd-002-review.md` | Reviewer approval and rerun evidence for the foundation envelope dependency |
| `ai-task-archive/tasks/SD-LIN-TRACE-001.json` | Confirms lineage trace dependency is `done` and reviewed |
| `support/sidecars/SD-LIN-TRACE-001/SD-LIN-TRACE-001-SIDECAR-ACCEPTANCE.md` | Dependency acceptance packet for the source-to-runtime-to-telemetry trace |
| `../front-ai-trading-system/src/lib/bffClient.ts` | Frontend API client surface for operator commands and lineage reads |
| `../front-ai-trading-system/src/pages/workbench/GovernanceQueue.tsx` | Frontend governance queue references BFF operator command path |
| `../front-ai-trading-system/src/pages/evolution/MutationReview.tsx` | Frontend mutation approval / rejection command submission surface |
| `../front-ai-trading-system/src/pages/lineage/Lineage.tsx` | Frontend lineage read surface and BFF error handling |
| `lean/Algorithm.Python/pantheon_algo/base.py` | LEAN bridge surface; schedules `SignalConsumer.drain()` and imports Pantheon execution runtime modules |
| `services/control-plane/bff/main.py` | BFF operator command, runtime-state, lineage, telemetry, kill-switch, and degraded-control routes |
| `services/telemetry/main.py` | Telemetry lineage route for `source-runtime-telemetry` |

## 3. Dependency Map

| Dependency | Current state | What the parent can rely on | Parent caution |
|---|---|---|---|
| `SD-FND-002` | `done`, archived at `2026-04-27T16:03:25Z` | BFF `POST /api/v1/operator/commands` and runtime-manager `execute_kill_switch` pilot paths propagate trace context, idempotency, policy decision, audit action, and foundation error envelopes; reviewer reran 59 passing tests | This is a pilot adoption, not all command paths or durable cross-service idempotency |
| `SD-LIN-TRACE-001` | `done`, archived at `2026-04-27T14:35:50Z` | Telemetry exposes `source_runtime_telemetry_trace` through `GET /api/telemetry/lineage/traces/<trace_id>/source-runtime-telemetry`; missing edges stay explicit; reviewer accepted 38-test targeted suite | This is a derived read model, not source / runtime / broker truth ownership |

Downstream tasks that should not be absorbed into this parent:

| Task | Boundary |
|---|---|
| `SD-RECON-001` | Deeper order / fill / cancel / position / drift / alert lifecycle reconciliation |
| `EP5-002-PACKET-PREP-001` | Runtime-manager-originated live / canary dry-run packet prep without broker side effects |
| `EP5-002-RUNTIME-LIVE-PROOF-001` | Human-gated live / canary execution proof only after explicit approval |
| `SD-SRC-EVIDENCE-001` | Governed source connector, evidence bundle, knowledge object, and search gateway work |

## 4. Parent Acceptance Checklist

| Parent acceptance target | Evidence to gather during parent run | Pass condition |
|---|---|---|
| Frontend command authority routes to BFF only | Inspect `front-ai-trading-system` command submitters and `operatorApi` methods; verify command actions post to `/api/v1/operator/commands` and use BFF command receipts | No direct runtime-manager, broker, LEAN, or alternate command endpoint is used for governed operator actions |
| Command trace / idempotency headers remain BFF-owned | Verify frontend does not invent authority state; BFF accepts `X-Trace-Id`, `X-Correlation-Id`, `X-Request-Id`, and `X-Idempotency-Key` on the pilot command route | BFF remains the authority boundary for trace, policy, idempotency, audit, and receipt semantics |
| Stable error UX can surface foundation errors | Confirm frontend `BffError` parsing can preserve BFF error codes and messages for `detail.error`, `detail.foundation_error`, or equivalent response detail | Policy denial and validation failures do not collapse into generic success/failure states that hide traceable BFF errors |
| Lineage / trace UX remains read-model consumption | Verify frontend lineage screens use BFF / lineage APIs and do not reconstruct the source-runtime-telemetry graph from raw local data | UI displays backend-derived trace / missing-edge / staleness semantics without creating a parallel truth source |
| Runtime telemetry hooks are reachable through Pantheon services | Verify parent can identify the BFF runtime / telemetry routes and telemetry service `source-runtime-telemetry` route that SD-LIN-TRACE-001 landed | Runtime state and traceability are read from Pantheon service routes, not from frontend or LEAN-local projections |
| LEAN bridge is execution-side only | Inspect `lean/Algorithm.Python/pantheon_algo/base.py`; confirm it schedules `SignalConsumer.drain()` and imports Pantheon execution modules without governance command APIs | LEAN consumes signals / execution runtime modules but does not submit approval, rollback, kill-switch, deployment, or broker authority commands |
| No parallel authority path exists | Search `front-ai-trading-system`, `lean/Algorithm.Python/pantheon_algo`, and Pantheon service surfaces for direct broker / runtime-manager / admin command bypasses | Any write path for governed actions is either the BFF operator command path or an explicitly documented backend-internal path, not a frontend or LEAN bypass |
| Cross-repo evidence is archived | Parent review packet lists repo, path, command / search used, and observed result for frontend, Pantheon, and LEAN bridge checks | Reviewer can replay or inspect each claim from concrete file paths and command output |

## 5. Suggested Parent Verification Commands

Run from `/home/lupin/code/pantheon` unless noted.

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
```

Optional targeted dependency confidence checks, if the parent owner wants fresh
reruns rather than relying on archived review evidence:

```bash
pytest services/control-plane/bff/test_governance_command_submission.py \
  services/runtime-manager/test_runtime_manager.py \
  services/foundation/tests -q

pytest services/telemetry/lineage_read/test_service.py \
  services/telemetry/test_main_routes.py -q
```

## 6. Review Guardrails

| Reviewer should reject | Reason |
|---|---|
| Treating this sidecar as the parent verification itself | This packet is a checklist and handoff, not the actual parent evidence archive |
| Editing L1 canonical truth or runtime implementation from this sidecar | The helper scope is support-only |
| Requiring full EP5 live / canary proof here | Live / canary execution remains behind separate packet prep and human approval |
| Treating LEAN as a governance authority | Current LEAN bridge surface is execution-side signal consumption |
| Accepting frontend-only optimistic authority | Governed actions must return BFF receipts and refresh backend-owned projections |
| Accepting raw local lineage reconstruction in the frontend | SD-LIN-TRACE-001 explicitly makes lineage a derived service read model |

## 7. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar creates only `support/sidecars/CROSS-REPO-SD-VERIFY-001/CROSS-REPO-SD-VERIFY-001-SIDECAR-ACCEPTANCE.md` |
| No canonical truth edited by sidecar | PASS | No L1 policy docs, contract docs, runtime registry, governance implementation, frontend source, or LEAN bridge files were modified |
| Dependencies mapped | PASS | `SD-FND-002` and `SD-LIN-TRACE-001` are both archived `done` with reviewer approval and test evidence |
| Parent acceptance is concrete | PASS | Section 4 maps each parent acceptance target to inspectable repo surfaces and pass conditions |
| Cross-repo boundary is explicit | PASS | Section 5 gives replayable search commands for Pantheon, frontend, and LEAN bridge surfaces |
| Scope caveats are bounded | PASS | Sections 3 and 6 keep reconciliation, EP5 proof, source evidence governance, and canonical changes outside this helper |

## 8. Handoff to Reviewer (`Codex`)

This sidecar is ready for reviewer use as the acceptance / dependency packet for
`CROSS-REPO-SD-VERIFY-001`.

What it gives you now:

1. a dependency map showing both parent prerequisites are complete and what each
   one contributes to the cross-repo verification,
2. a parent acceptance checklist for command authority, trace / error UX,
   runtime telemetry hooks, LEAN bridge boundaries, and no parallel authority,
3. replayable search commands that the parent owner can use to produce the
   actual evidence packet,
4. explicit review guardrails so this helper does not mutate canonical truth or
   absorb downstream EP5 / reconciliation / source-evidence work.

Recommended reviewer stance:

1. approve this sidecar if it accurately reflects the support-only boundary and
   gives the parent owner a usable cross-repo verification checklist,
2. keep the parent task responsible for collecting the final evidence and
   deciding whether any frontend, LEAN, or BFF follow-up is needed,
3. reject any attempt to treat this sidecar as proof of live / canary readiness
   or as a substitute for the parent cross-repo inspection.

---
*Generated by Codex2 as a sidecar `acceptance_packet` helper for
`CROSS-REPO-SD-VERIFY-001`. This file is a support artifact and does not modify
canonical truth.*
