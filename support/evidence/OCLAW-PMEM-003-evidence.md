# Evidence: OCLAW-PMEM-003 — Canonical Memory Bridge to OpenClaw Workspace

Owner: Antigravity
Reviewer: Claude
Date: 2026-07-11

## 1. Scope and Implementation Summary

The task was previously implemented and merged into `dev` under PR #3026. The implementation contains the following features:
- **Retrieval and Materialization Bridge**: Implemented in `integrations/openclaw/persona_memory_bridge.py` (`materialize_persona_memory_from_api`). It retrieves canonical memory from the Memory Plane and writes it as prompt context files (`MEMORY.md` and `memory/context.json`) in the OpenClaw workspace directory.
- **Traceable Source IDs**: Materialized entries contain `source_id`, `canonical_ref`, relevance scores, written timestamps, retrieval query/scope, and generation timestamp.
- **Private Memory Isolation**: Mismatched persona IDs are rejected in `normalize_retrieval_hits` and logged in `rejected_hits`.
- **Governed Writeback Candidates**: Turn outcomes are staged under `{workspace}/memory/writeback-candidates/` as candidate JSONs and do not directly mutate canonical memory.
- **Sync Integration**: The deploy script `scripts/openclaw-sync-persona-agents.py` calls the memory bridge during reconciliation.

## 2. Local Validation

All unit and integration tests under `integrations/openclaw` pass successfully:
- `integrations/openclaw/test_persona_memory_bridge.py` (3 passed)
  - `test_materializes_canonical_memory_with_traceable_source_ids` — PASSED
  - `test_rejects_private_persona_memory_from_other_persona` — PASSED
  - `test_writeback_candidate_does_not_mutate_canonical_store` — PASSED
- `integrations/openclaw/test_persona_agent_sync.py` (9 passed) — PASSED
- `integrations/openclaw/test_persona_ooda_runtime.py` (4 passed) — PASSED

Total 121 tests passed successfully in the `integrations/openclaw` directory.

## 3. Source-Of-Truth Boundaries

| Concern | Source of truth | Materialized view / Cache |
|---|---|---|
| Persona Identity, Mandate, Traits | Persona Registry | OpenClaw `SOUL.md` (read-only) |
| Long-term Persona Memory | Canonical Memory Plane (`services/memory`) | OpenClaw `MEMORY.md` / `memory/context.json` cache |
| Shared / Institutional Lessons | Canonical Memory Plane (`services/memory`) | Bounded turn context / `context.json` cache |
| LLM Provider Auth & Quota | OpenClaw Provider Pool | Management LLM Auth panel |

Workspace local memory files (`MEMORY.md`, `memory/context.json`) are strictly a cached/materialized prompt context. Turn outputs proposing memory updates are staged as candidates only, and must go through `POST /api/memory/writebacks/persona` to become canonical.

## 4. Residual Risks

- **Risk**: Out-of-band modifications to `MEMORY.md` or `context.json` by the agent or operator.
- **Mitigation**: Any such modifications are ephemeral as they will be overwritten on the next agent sync / workspace materialization cycle.
- **Owner**: Antigravity
- **Expiry**: 2026-08-11
