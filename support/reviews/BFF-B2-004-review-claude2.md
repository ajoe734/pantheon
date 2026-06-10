# BFF-B2-004 Review — Claude2

Date: 2026-05-23
Reviewer: Claude2
Owner: Codex2
Task: BFF-B2-004 — Research and search facade: /bff/research-experiments and /bff/search

## Verdict: APPROVED

## Scope Reviewed

- `services/control-plane/bff/main.py` — four dedicated handlers added in the
  BFF-B2-004 block; dead catch-all decorators removed.
- `services/control-plane/bff/tests/test_bff_b2_004_research_search.py` — 17
  focused integration tests.
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`
  §B2.3 acceptance criteria reviewed.

## Acceptance Criteria — All Passed

| # | Criterion | Result |
|---|---|---|
| 1 | GET /bff/research-experiments returns data + items + page_info.total + meta.surfaces.research_experiments | ✅ |
| 2 | GET /bff/research-experiments accepts ?status= filter | ✅ |
| 3 | GET /bff/research-experiments/{id} for existing id returns data with experiment_id | ✅ |
| 4 | GET /bff/research-experiments/{id} for unknown id returns HTTP 404 typed BFF error | ✅ |
| 5 | GET /bff/search returns data, items, page_info, and meta envelope | ✅ |
| 6 | GET /bff/search?types=strategy returns only strategy-typed results | ✅ |
| 7 | GET /bff/capabilities returns data.feature_flags with executePlansBff and sessionAuthMe | ✅ |
| 8 | All 4 endpoints return HTTP 401 when no Authorization header is provided | ✅ |
| 9 | Dead catch-all entries removed: /bff/research-experiments from sem_final_generic_read_alias; /bff/research-experiments/{id} from sem_final_id_named_read_alias | ✅ |
| 10 | pytest test_bff_b2_004_research_search.py passes all 17 cases | ✅ |
| 11 | GET /bff/search?limit=N is backward-compat alias for page_size | ✅ |

## Verification Run

```
pytest services/control-plane/bff/tests/test_bff_b2_004_research_search.py -v
# 17 passed in 4.87s

pytest services/control-plane/bff/tests/ -q
# 231 passed, 3 warnings in 84.91s
```

No regressions in compose suites. The 3 warnings are pre-existing
`datetime.utcnow()` deprecation notices unrelated to this task.

## Implementation Notes

- `bff_list_research_experiments` correctly returns both `data` and `items` as
  required by spec. Pagination via `_page_slice` is consistent with other list
  endpoints. The `page_info.total` reflects pre-pagination count.
- `bff_get_research_experiment` delegates to `_sem_final_read_model_detail` with
  proper 404 guard and surface key `research_experiment_detail`.
- `bff_search` correctly implements `limit` as a precedence-over-`page_size` alias
  (line 26911: `limit if limit is not None else page_size`). Cross-entity types
  (strategy, persona, capital_pool) are properly filtered.
- `sem_bff_capabilities` correctly exposes `executePlansBff` and `sessionAuthMe`
  feature flags. PATCH for research-experiments remains in the generic handler per
  spec — this is correct.
- Dead catch-all decorators confirmed removed from `sem_final_generic_read_alias`
  and `sem_final_id_named_read_alias`; NOTEs at lines 34724–34728 document the
  intentional exclusion.

## Conclusion

Implementation satisfies all spec requirements. PR #466 merged at ac911bcb.
Owner (Codex2) may proceed to closeout.
