# Review: P2-OSS-ACTIVATE-001 - Research OSS production data posture and activation

Reviewer: Codex
Task owner: Codex2
Date: 2026-05-01
Status: **APPROVED**

## Artifacts Reviewed

- `OSS_INTEGRATION_CHECKLIST.md`
- `services/learning/OSS_ACTIVATION_NOTES.md`
- `services/source_ingestion/external_sources.py`
- `services/search/gateway.py`
- `services/search/filters.py`
- `integrations/openclaw/search_gateway.py`
- `services/openclaw-gateway-adapter/tool_workflow_bridge.py`
- `services/openclaw-gateway-adapter/paper_broker_adapter.py`
- `services/openclaw-gateway-adapter/live_gate_adapter.py`

## Acceptance Criteria Evaluation

### A1 - Production data posture, not blanket live-data ban

**PASS.**

`services/learning/OSS_ACTIVATION_NOTES.md` explicitly states this is not a
blanket live-data ban. Production research data is allowed only after durable
storage, entitlement, license/PIT, rate-limit, freshness, and audit posture are
complete. `OSS_INTEGRATION_CHECKLIST.md` now points to that packet and keeps
Qlib and TRL at `smoke-tested` with `draft` / `none` output posture.

### A2 - Source/search/OpenClaw paths cannot bypass governed controls

**PASS.**

The reviewed source ingestion path requires entitlement/license/PIT fields,
content hash, and a `SourceRecord/EvidenceBundle` governance sink for external
research feeds, and rejects direct Lean, broker, runtime, order-routing, or
execution destinations. Search applies ACL/license/workspace/environment
filters, requires citations for governed OpenClaw retrieval, and filters future
`available_time` evidence. The OpenClaw search facade returns sanitized
`evidence_bundle_id` and citation refs only, not raw payloads or answer context.

### A3 - Remaining prerequisites are explicit without enabling execution

**PASS.**

The activation notes list the remaining credential reference, entitlement,
license/PIT, durable storage, rate-limit, audit, and downstream consumer
evidence required before production data consumption. They also keep OpenClaw
broker/live behavior fail-closed: the tool/workflow bridge blocks broker,
paper, live, canary, Lean, and capital-prefixed tools/workflows; the paper
broker adapter is opt-in and marks real order/capital false; the live gate is a
dry-handoff harness with live execution disabled.

## Verification

- `python3 -m pytest services/source_ingestion/tests/test_external_source_connectors.py services/source_ingestion/tests/test_bounded_ingestion.py services/search/tests/test_governed_search.py services/openclaw-gateway-adapter/test_tool_workflow_bridge.py services/openclaw-gateway-adapter/test_live_gate_adapter.py -q` -> `111 passed in 29.07s`
- `git diff --check -- OSS_INTEGRATION_CHECKLIST.md` -> passed

## Notes

- The worktree contains unrelated dirty files from other tasks and generated
  orchestration artifacts. This review only approves the P2 OSS activation
  posture artifacts and the existing source/search/OpenClaw guard behavior
  listed above.

## Verdict

All three acceptance criteria pass. Approved and returned to Codex2 for owner
closeout.
