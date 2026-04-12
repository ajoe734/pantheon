# LIN-001A Review — Approved by Qwen

**Task:** LIN-001A — Prepare lineage edge benchmark corpus for capital and runtime objects
**Reviewer:** Qwen
**Owner:** Codex
**Result:** APPROVED

---

## Review Summary

All three acceptance criteria are met. The prep packet is clean, deliberate in scope, and ready for LIN-001 consumption.

### Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Candidate lineage edges across capital/deployment/runtime/telemetry are enumerated | PASS | Section 5 of LIN_001A_PREP_PACKET.md defines 13 semantic edges; `semantic_edges` in corpus JSON mirrors them exactly |
| Benchmark corpus shape and p95 measurement method are documented | PASS | Sections 6–8 of the packet define corpus shape, query families, SLA buckets, and measurement method; `benchmark_harness.py` implements nearest-rank p50/p95 with configurable warmup/measurement iterations |
| LIN-001 can consume the prep package without redefining read-model scope | PASS | Sections 3, 4, and 9 explicitly bound scope to capital/deployment/runtime/telemetry, publish semantic edge IDs with alias tables for naming drift, and provide a clear consumption guide for LIN-001 |

### Artifact Quality

1. **LIN_001A_PREP_PACKET.md** — Well-structured. The naming-drift boundary (§4) is the right call: semantic edge IDs stay stable while physical field names can be decided later by LIN-001. The scope boundary (§3) is deliberate — excluding upstream research/registry lineage is correct for this prep slice. Section 10's open-gaps table shows good engineering discipline.

2. **lin001a_benchmark_corpus.json** — 22 fixture records across 5 object families. All foreign-key references are internally consistent (verified). The rollback scenario (pool-beta) covers the critical pause_then_replace path with proper marker propagation across runtime bindings and telemetry events. The naming alias table correctly captures both drift pairs.

3. **benchmark_harness.py** — Clean in-memory traversal implementation. Key design decisions reviewed:
   - `_event_binding_id()` correctly handles `runtime_binding_id` / `binding_id` alias drift
   - `_plan_persona_binding_id()` correctly handles `persona_capital_binding_id` / `binding_id` alias drift
   - `flatten_result_ids()` correctly unions upstream_chain, downstream_chain, and conflict_markers for case validation
   - `validate_case()` checks both expected_ids and expected_marker_ids
   - SLA budget enforcement via `--enforce-budgets` exit code works correctly

### Benchmark Results (20 iterations, 3 warmup)

| Query Family | p95 (ms) | Budget (ms) | Within Budget |
|---|---|---|---|
| runtime_binding_projection | 0.006 | 500 | PASS |
| capital_pool_projection | 0.011 | 500 | PASS |
| telemetry_event_trace | 0.003 | 500 | PASS |
| forensic_plan_trace | 0.007 | 5000 | PASS |

All 5 benchmark cases validated their expected_ids and expected_marker_ids correctly.

### Minor Observations (Non-Blocking)

1. The harness measures in-memory traversal only — this is explicitly documented and correct for a prep slice. LIN-002 must swap to real storage paths.
2. The `denormalized_guard` edges (`artifact_guard`, `runtime_guard`) target `RuntimeBindingSnapshot` as the `to_type` in the corpus JSON, while the packet document describes them as "runtime snapshot carried by RuntimeBinding." This is a cosmetic type-name difference; the semantic intent is identical and not a blocker.
3. The corpus fixture is small by design (22 records). This is appropriate for contract validation but LIN-002 will need larger datasets for real service-level benchmarking.

### Conclusion

Approved. The prep packet is a solid foundation for LIN-001 to begin the read-model contract without re-litigating the capital/runtime/telemetry slice.
