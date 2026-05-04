# EXEC-OSS-STATSMODELS-001 Re-review

Reviewer: `Codex`
Date: `2026-04-21`
Disposition: `approved`

## Findings

No blocking findings.

## Verification

- Confirmed the governed input adapter now enforces numeric-only observations, equal-length alignment, non-finite rejection, and `metadata.governed=True` in [services/research/statsmodels/adapter/statsmodels_adapter.py](/home/lupin/code/pantheon/services/research/statsmodels/adapter/statsmodels_adapter.py:48).
- Confirmed the regression coverage for the prior reviewer repros is present in [services/research/statsmodels/test_adapter.py](/home/lupin/code/pantheon/services/research/statsmodels/test_adapter.py:95).
- Confirmed the maturity and inventory surfaces now describe statsmodels as a governed production research path in [RESEARCH_BACKEND_MATURITY_MATRIX.md](/home/lupin/code/pantheon/RESEARCH_BACKEND_MATURITY_MATRIX.md:177) and [docs/reviews/2026-04-20-development-progress-and-next-work-inventory.md](/home/lupin/code/pantheon/docs/reviews/2026-04-20-development-progress-and-next-work-inventory.md:320).
- Re-ran `python3 -m pytest services/research/statsmodels/test_adapter.py -q` and confirmed `24 passed`.
- Re-ran `python3 services/research/statsmodels/smoke_test.py` and confirmed `SMOKE TEST PASSED`.
- Re-ran `python3 services/research/statsmodels/worker.py` and confirmed the sample-dataset fallback still emits a draft `regime_report` envelope.
- Replayed the three original reviewer repro commands and confirmed each now fails with `StatsmodelsWorkflowError` instead of silently validating malformed or ungoverned input.
