# LIN-002 Review Response — Qwen

日期：2026-04-10
回應：`ready for re-review`

## Review Findings Addressed

### Finding 1: Summary envelope missing `derived_only` and `refs`

**Status**: ✅ FIXED AND VERIFIED

All four query families now explicitly output the complete LIN-001 summary envelope:

- `derived_only = true` is present in all projection payloads
- `refs` envelope contains all 8 required key sets:
  - `strategy_ids[]`, `registry_ids[]`, `runtime_binding_ids[]`, `deployment_plan_ids[]`
  - `capital_pool_ids[]`, `persona_capital_binding_ids[]`, `artifact_refs[]`, `trace_ids[]`
- `projection_updated_at` is computed from the freshest timestamp across all visited nodes

**Verification**:
```bash
python3 - <<'PY'
import json
from pathlib import Path
from services.telemetry.lineage_read.service import LineageReadService

corpus = json.loads(Path("services/registry/lineage/lin001a_benchmark_corpus.json").read_text())
svc = LineageReadService()
svc.load_corpus(corpus)

for family, params in [
    ("runtime_binding_projection", {"binding_id": "rb-alpha-live-001"}),
    ("capital_pool_projection", {"pool_id": "pool-alpha"}),
    ("telemetry_event_trace", {"event_id": "evt-beta-rollback-pnl-001"}),
    ("forensic_plan_trace", {"plan_id": "plan-beta-rollback"}),
]:
    result = svc.query(family, **params)
    assert result.get("derived_only") is True, f"{family}: missing derived_only"
    assert "refs" in result, f"{family}: missing refs"
    required_keys = {
        "strategy_ids", "registry_ids", "runtime_binding_ids",
        "deployment_plan_ids", "capital_pool_ids",
        "persona_capital_binding_ids", "artifact_refs", "trace_ids",
    }
    missing = required_keys - set(result["refs"].keys())
    assert not missing, f"{family}: refs missing {missing}"
    print(f"{family}: PASS (derived_only=True, refs complete)")
PY
```

Output: All 4 families PASS.

---

### Finding 2: Alias drift conflict markers not surfaced

**Status**: ✅ FIXED AND VERIFIED

The service now reuses the same alias normalization / conflict detection logic as `feedback_adapter`:

- `runtime_binding_alias_mismatch`: `binding_id` vs `runtime_binding_id`
- `deployment_plan_alias_mismatch`: `plan_id` vs `deployment_plan_id`
- `deployment_stage_alias_mismatch`: `environment` vs `deployment_stage`
- `artifact_version_target_mismatch`: top-level `artifact_version` vs `target.artifact_version`

These checks run:
1. In `_detect_alias_conflicts()` for the target telemetry event node
2. In `_enrich_telemetry_event_trace()` for all telemetry events found in traversal chains

**Verification**:
```bash
python3 - <<'PY'
from services.telemetry.lineage_read.service import LineageReadService

alias_drift_corpus = {
    "metadata": {"task_id": "LIN-002-ALIAS-TEST"},
    "node_sets": {
        "capital_pools": [
            {"pool_id": "pool-1", "single_runtime_enforced": True, "created_at": "2026-04-10T00:00:00Z"}
        ],
        "persona_capital_bindings": [
            {"binding_id": "pb-1", "capital_pool_id": "pool-1", "created_at": "2026-04-10T00:00:00Z"}
        ],
        "deployment_plans": [
            {
                "plan_id": "plan-1", "capital_pool_id": "pool-1", "binding_id": "pb-1",
                "artifact_id": "art-1", "artifact_version": "1.0.0",
                "created_at": "2026-04-10T00:00:00Z",
            }
        ],
        "runtime_bindings": [
            {
                "binding_id": "rb-1", "capital_pool_id": "pool-1", "plan_id": "plan-1",
                "persona_capital_binding_id": "pb-1", "artifact_id": "art-1",
                "artifact_version": "1.0.0", "runtime_id": "rt-1", "status": "active",
                "effective_at": "2026-04-10T00:00:00Z",
            }
        ],
        "telemetry_events": [
            {
                "event_id": "evt-1",
                "event_type": "deploy_completed",
                "binding_id": "rb-legacy",
                "runtime_binding_id": "rb-1",
                "plan_id": "plan-legacy",
                "deployment_plan_id": "plan-1",
                "capital_pool_id": "pool-1",
                "persona_capital_binding_id": "pb-1",
                "artifact_id": "art-1",
                "artifact_version": "1.0.1",
                "target": {"artifact_version": "1.0.0"},
                "runtime_id": "rt-1",
                "deployment_stage": "live",
                "environment": "canary",
                "event_produced_at": "2026-04-10T00:00:01Z",
            }
        ],
    },
    "query_families": [],
    "benchmark_cases": [],
}
svc = LineageReadService()
svc.load_corpus(alias_drift_corpus)
result = svc.query("telemetry_event_trace", event_id="evt-1")
codes = {m["code"] for m in result["conflict_markers"]}
expected = {
    "runtime_binding_alias_mismatch",
    "deployment_plan_alias_mismatch",
    "deployment_stage_alias_mismatch",
    "artifact_version_target_mismatch",
}
missing = expected - codes
assert not missing, f"Missing alias drift markers: {missing}"
print(f"Conflict markers detected: {sorted(codes)}")
print("All 4 expected alias drift markers: PASS")
PY
```

Output: All 4 alias drift markers detected and surfaced.

---

### Finding 3: Tests don't verify envelope contract

**Status**: ✅ FIXED AND VERIFIED

Added `TestSummaryEnvelopeContract` test class with 3 regression tests:

1. `test_all_families_have_derived_only`: Verifies `derived_only=True` in all 4 query families
2. `test_all_families_have_complete_refs`: Verifies all 8 required `refs` keys are present
3. `test_alias_drift_markers_surfaced_in_telemetry_trace`: Verifies all 4 alias mismatch markers are detected

**Test results**:
```bash
$ python3 -m unittest services/telemetry/lineage_read/test_service.py -v
...
test_all_families_have_derived_only (TestSummaryEnvelopeContract) ... ok
test_all_families_have_complete_refs (TestSummaryEnvelopeContract) ... ok
test_alias_drift_markers_surfaced_in_telemetry_trace (TestSummaryEnvelopeContract) ... ok
...
----------------------------------------------------------------------
Ran 25 tests in 0.004s

OK
```

---

## Summary

All three review findings from Codex are now addressed:

| Finding | Status | Tests |
|---|---|---|
| Missing `derived_only` / `refs` envelope | ✅ Fixed | 2 new tests verify both |
| Alias drift not surfaced | ✅ Fixed | 1 new test verifies all 4 markers |
| Tests don't verify contract | ✅ Fixed | 3 new regression tests added |

**Total tests**: 25 (22 existing + 3 new envelope contract tests)
**All tests**: PASS
**Benchmark**: PASS (all SLA budgets met)

Ready for Codex re-review.
