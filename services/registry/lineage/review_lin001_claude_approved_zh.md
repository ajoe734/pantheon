# LIN-001 Review — Approved by Claude

**Task:** LIN-001 — Normalize lineage edges and define read-model aggregation contract
**Reviewer:** Claude
**Owner:** Codex
**Result:** APPROVED

---

## Review Summary

Both acceptance criteria are met. The normalized edge inventory is fully enumerated in two
complementary artifacts (L1 policy doc and a new read-model contract doc), and the derived-only
constraint is enforced in both the contract text and the Python implementation. All validation
passes.

---

### Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Normalized edges are enumerated | PASS | 19 semantic edges defined in `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md §8` and mirrored in `read_model_contract.md §4` with owner assignments |
| Read model is marked derived-only | PASS | `derived_only = True` is explicit in `build_lineage_record()` and `build_lineage_summary()`; both contract docs state the read model must never become a write authority |

---

### Artifact Quality

#### 1. `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` (L1 policy updates)

`§5.3.1` (Raw-to-Semantic normalization table) and `§5.3.2` (Derived Record/Summary Contract)
are the critical LIN-001 additions and both are solid:

- The five normalization rules (`DeploymentPlan.binding_id` → `persona_capital_binding_id`,
  `TelemetryEvent.binding_id` → `runtime_binding_id`, etc.) match what is implemented in
  `feedback_adapter.py`.
- The derived record envelope (`derived_only`, `projection_updated_at`, `conflict_markers[]`,
  `refs`) is well-specified and correctly carried into `build_lineage_summary()`.
- §8 semantic edge table is complete and consistent with `read_model_contract.md §4`.

#### 2. `services/registry/lineage/read_model_contract.md`

Well-structured. Key design decisions checked:

- §5 normalization rules match the static helper methods in `feedback_adapter.py` exactly.
- §9 Derived-Only Invariants (5 rules) are clear and actionable for LIN-002.
- §10 downstream consumers correctly points INC-001, LIN-002, EVO-003, and APP-001 to this
  as the single contract they must consume.
- The distinction between §6 (per-record shape) and §7 (summary projection shape) maps cleanly
  to `build_lineage_record()` vs `build_lineage_summary()`.

#### 3. `services/telemetry/feedback_adapter.py`

LIN-001 additions reviewed:

- `LINEAGE_TARGET_FIELDS` mapping is correct and complete for the telemetry slice.
- `_runtime_binding_id()`, `_deployment_plan_id()`, `_persona_capital_binding_id()`,
  `_deployment_stage()`, `_artifact_version()`, `_artifact_ref()` — all correctly implement
  the raw-to-semantic alias resolution with proper fallback priority.
- `_lineage_conflict_markers()` correctly surfaces all four alias-drift scenarios:
  `runtime_binding_alias_mismatch`, `deployment_plan_alias_mismatch`,
  `deployment_stage_alias_mismatch`, `artifact_version_target_mismatch`. Conflicts are not
  silently suppressed — they are carried into the record.
- `build_lineage_record()` — `derived_only=True` is explicit; all semantic field names are
  correctly mapped from raw telemetry fields.
- `build_lineage_summary()` — `derived_only=True` explicit; `projection_updated_at` derived
  from max `created_at` across records; full `refs` dict matches §7 envelope.
- `query_lineage_records()` — input validation for mismatched `target_type`/`target_id` is
  correct; unsupported target_type raises `ValueError` with actionable message.
- `_iter_telemetry_events()` family boundary is applied consistently in both shared-store mode
  and local-buffer mode.

---

### Validation Results

```
python3 -m py_compile feedback_adapter.py test_feedback_adapter.py  → PASS
python3 -m unittest test_feedback_adapter  → 28/28 PASS (0.371s)
benchmark_harness.py --enforce-budgets     → 4/4 families within SLA budgets
```

| Query Family | p95 (ms) | Budget (ms) | Result |
|---|---|---|---|
| runtime_binding_projection | 0.009 | 500 | PASS |
| capital_pool_projection | 0.011 | 500 | PASS |
| telemetry_event_trace | 0.004 | 500 | PASS |
| forensic_plan_trace | 0.010 | 5000 | PASS |

---

### Non-Blocking Notes

1. **`build_lineage_summary()` omits `upstream_chain[]` / `downstream_chain[]`** — The
   telemetry-slice implementation emits the full `refs` dict but not the chain arrays. This is
   expected: assembling a true upstream/downstream chain across all domains requires cross-plane
   traversal that belongs to LIN-002, not the telemetry adapter. LIN-002 must wire the full
   chain shape from normalized edges.

2. **`artifact` target_type matches on `artifact_ref` (`id@version`)** — Querying by `artifact`
   target_type compares against the composite `artifact_ref` field, not bare `artifact_id`. If
   callers need to match by `artifact_id` alone, LIN-002 should consider adding `artifact_id`
   as a separate supported target_type.

---

### Conclusion

Approved. LIN-001 successfully locks the normalized cross-plane edge inventory and the
derived-only read-model contract. INC-001, LIN-002, EVO-003, and APP-001 can now proceed
against a stable lineage semantics foundation.
