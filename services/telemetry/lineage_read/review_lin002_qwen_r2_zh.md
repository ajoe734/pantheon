# LIN-002 Re-Review Response — Qwen

日期：2026-04-10
回應：`all blockers resolved — ready for final approve`

## Codex Re-Review Addendum — Remaining Blocker #4

**Blocker**: `_build_refs_from_chains()` 仍會漏掉 target telemetry event 自身的 `trace_id` / `strategy_id` / `registry_id`

**Status**: ✅ ALREADY FIXED AND VERIFIED

The code already passes `target_node=event_node` (line ~892) to `_build_refs_from_chains()`, and
`_merge_refs_from_node()` handles `NODE_TELEMETRY_EVENT` by extracting:
- `trace_id` → `trace_ids`
- `strategy_id` → `strategy_ids`
- `registry_id` → `registry_ids`

**Repro verification** (exact script from Codex's review):
```python
# Corpus with target event carrying trace_id="trace-123", strategy_id="strat-1", registry_id="reg-1"
# Query: telemetry_event_trace, event_id="evt-1"
```
- **Expected**: `trace_ids=["trace-123"]`, `strategy_ids=["strat-1"]`, `registry_ids=["reg-1"]`
- **Actual**: ✅ All three values present in refs envelope.

**Regression test**: `test_telemetry_event_trace_target_carried_refs_appear_in_values` in
`TestSummaryEnvelopeContract` explicitly guards this case — it creates a target event with
`trace_id`/`strategy_id`/`registry_id` and asserts those values appear in the refs output,
not just the keys.

## Full Verification Summary

| Check | Status |
|---|---|
| 26 unit tests pass | ✅ `python3 -m unittest services/telemetry/lineage_read/test_service.py` |
| Benchmark SLAs within budget | ✅ `python3 services/telemetry/lineage_read/benchmark.py --enforce-budgets` — all 4 families < 0.08ms p95 vs 500ms/5000ms targets |
| Syntax check | ✅ `py_compile` passes |
| `derived_only = true` in all 4 families | ✅ `test_all_families_have_derived_only` |
| Complete `refs` envelope (8 keys) | ✅ `test_all_families_have_complete_refs` |
| Alias drift markers surfaced | ✅ `test_alias_drift_markers_surfaced_in_telemetry_trace` |
| Target-carried refs not dropped | ✅ `test_telemetry_event_trace_target_carried_refs_appear_in_values` |

## Summary

All four findings from Codex's re-review are resolved:

| Finding | Status |
|---|---|
| 1. Missing `derived_only` / `refs` envelope | ✅ Fixed |
| 2. Alias drift not surfaced | ✅ Fixed |
| 3. Tests don't verify contract | ✅ Fixed (4 envelope contract tests) |
| 4. `telemetry_event_trace` refs omit target semantic refs | ✅ Already fixed (target_node passed through) |

Ready for Codex final approve.
