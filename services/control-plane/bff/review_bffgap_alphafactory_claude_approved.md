# Review: BFFGAP-ALPHAFACTORY — GET /bff/alpha-factory

Reviewer: Claude
Date: 2026-06-15
Status: APPROVED

## Summary

Implementation of `GET /bff/alpha-factory` is complete and correct. All acceptance criteria are met.

## Checklist

- [x] Endpoint returns 200 canonical list envelope `{data, items, page_info, meta}`
- [x] Degraded envelope: when `dataset_source` returns `"missing"`, response has `status:unavailable`, `source:missing`, explicit `items:[]`, `page_info.total:0`; never bare `[]`
- [x] Lane filter: `?lane=ideas|strategies|experiments` correctly filters items and reflected in `meta.filters.lane`
- [x] Auth guard: unauthenticated request returns 401
- [x] No route shadowing (route resolution test passes)
- [x] BFF_API_CONTRACT.md updated with §9.9 AF-01 surface, request params, happy-path and degraded shapes, design notes
- [x] All 4 contract tests pass: `tests/test_bff_alpha_factory_contract.py`
- [x] PR #1633 open with auto-merge enabled; all 3 CI checks green (Commit trailers ✓, Runtime mirror guard ✓, Smoke acceptance ✓)
- [x] Router registered via `include_router` in `main.py` (single clean line, no route conflicts)

## Implementation Notes

- `console_gap/alpha_factory.py` — clean APIRouter factory pattern. Auth delegates to `extract_identity` + `require_read_role`. `utc_now` injected for deterministic snapshot timestamps.
- `_build_surface` correctly distinguishes three states: `missing` → unavailable-envelope; `local_snapshot` → degraded-ok; any other → ok.
- Both `snapshotAt` (camelCase) and `snapshot_at` (snake_case) present in `data` — matches contract doc. Intentional dual-key for FE compatibility.
- `list_alpha_factory_cards` called only when surface is not `unavailable` — correct guard against calling a missing store.

## Verification

```
python3 -m pytest tests/test_bff_alpha_factory_contract.py -v
# 4 passed in 3.05s

python3 -m pytest test_route_resolution_no_shadowing.py -v
# 2 passed in 2.59s
```

## Decision

**APPROVED.** Task returned to Claude2 for closeout finalization (PR merge wait + `done`).
