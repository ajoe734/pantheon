# Review: BFF-PM12-006 — GET /bff/management/quarterly-ranking

Reviewer: Claude2
Reviewed at: 2026-05-23
Status: **APPROVED**

## Acceptance Criteria Verification

| # | Criterion | Result |
|---|---|---|
| 1 | Authenticated GET /bff/management/quarterly-ranking returns ranked persona items, formula, quarter window, evidence refs, summary, page info, and source metadata | PASS — route returns `data`, `items`, `rankings`, `formula`, `quarterWindow`, `quarter_window`, `evidenceRefs`, `evidence_refs`, `summary`, `page_info`, `meta`; test asserts all required top-level keys |
| 2 | quarter=YYYY-Qn parsed into UTC quarter window; invalid quarters return HTTP 422 | PASS — `_PM12_QUARTER_PATTERN` validates format; `_pm12_quarter_window` raises HTTP 422 with `detail.error=invalid_quarter`; test_pm12_quarterly_ranking_rejects_invalid_quarter confirms 422 for "2026-05" |
| 3 | Missing auth returns HTTP 401 | PASS — `_require_read_role` called at route entry; test_pm12_persona_league_requires_auth confirms 401 for unauthenticated request |
| 4 | Route is registered in execute-plans final live wiring route inventory | PASS — ("GET", "/bff/management/quarterly-ranking") present at lines 66 and 179 in test_execute_plans_final_live_wiring_contract.py |
| 5 | execute-plans exposes typed path and fetch helpers for quarterly ranking | PASS — `managementQuarterlyRanking()` in paths.ts (line 117); `managementQuarterlyRankingPath()` and `fetchManagementQuarterlyRanking()` in management.ts; full typed query/response contracts defined |

## Test Results

```
services/control-plane/bff/tests/test_bff_pm12_persona_league.py::test_pm12_quarterly_ranking_returns_formula_window_and_evidence — PASS
services/control-plane/bff/tests/test_bff_pm12_persona_league.py::test_pm12_quarterly_ranking_rejects_invalid_quarter — PASS
services/control-plane/bff/tests/test_bff_pm12_persona_league.py::test_pm12_persona_league_requires_auth (covers /bff/management/quarterly-ranking) — PASS
services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py — route inventory entry confirmed
```

## Code Notes

- `_PM12_QUARTER_PATTERN = re.compile(r"^(?P<year>\d{4})-Q(?P<quarter>[1-4])$", re.IGNORECASE)` — correct; accepts Q1–Q4 only.
- `_pm12_quarter_window` correctly computes UTC start/end dates for all four quarters including Q4 year-rollover (`year+1, 1, 1`).
- `_pm12_quarterly_ranking_items` correctly reuses `_pm12_persona_league_ranking_item` and adds `rank`, `score`, `quarter`, `quarterWindow`, `formulaVersion`, `basis` — consistent with spec.
- Evidence filtering by quarter window using `_pm12_quarter_evidence_refs` is sound; graceful no-op when evidence dataset is missing.
- Evidence redaction via `redact_evidence_refs` is applied before serialization.
- `meta.surfaces.quarterly_ranking` aggregated from persona-league source surfaces + formula surface + evidence surface — correct degraded-mode propagation.
- `meta.composition_sources` matches spec: persona-league, persona-league/rankings, persona-league/tiers, knowledge/evidence.
- `meta.policy = "read_only_governance_advisory"` present.
- TypeScript types `ManagementQuarterlyRankingQuery`, `ManagementQuarterlyRankingResponse`, `ManagementQuarterlyRankingData`, `ManagementQuarterlyRankingItem`, `ManagementQuarterlyRankingFormula`, `ManagementQuarterlyRankingWindow`, `ManagementQuarterlyRankingSummary` are complete and correctly extend / reference existing management types.

## Verdict

Implementation satisfies all 5 acceptance criteria per §B3.4 PM-12 Quarterly Ranking. No changes required. Returning to Codex2 for closeout.
