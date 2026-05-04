# EXEC-OSS-STATSMODELS-001 Review

Review date: 2026-04-21
Reviewer: Codex
Status: changes requested

## Findings

1. The governed input boundary is still not enforced, so the new `governed` status overstates what the adapter actually guarantees.

- The governance overlay says the adapter rejects non-numeric, unaligned, malformed, NaN-heavy, and non-governed inputs in [integrations/statsmodels/governance.md](/home/lupin/code/pantheon/integrations/statsmodels/governance.md:16).
- The actual validator only checks dataset type, minimum series counts, and minimum observation count in [services/research/statsmodels/adapter/statsmodels_adapter.py](/home/lupin/code/pantheon/services/research/statsmodels/adapter/statsmodels_adapter.py:47). There is no numeric validation, no equal-length/alignment check, no NaN rejection, and no metadata governance check.
- Reproduced locally with:
  `python3 -c 'from services.research.statsmodels.adapter.statsmodels_adapter import GovernedDataset, GovernedStatsmodelsInputAdapter; ds=GovernedDataset(price_series={"A":[1.0]*10,"B":["x"]*10}, factor_series={"X":[0.1]*10}); GovernedStatsmodelsInputAdapter().validate(ds); print("validated")'`
  `python3 -c 'from services.research.statsmodels.adapter.statsmodels_adapter import GovernedDataset, GovernedStatsmodelsInputAdapter; ds=GovernedDataset(price_series={"A":[1.0]*12,"B":[2.0]*10}, factor_series={"X":[0.1]*10}); GovernedStatsmodelsInputAdapter().validate(ds); print("validated")'`
  `python3 -c 'from services.research.statsmodels.adapter.statsmodels_adapter import GovernedDataset, GovernedStatsmodelsInputAdapter; ds=GovernedDataset(price_series={"A":[1.0]*10,"B":[2.0]*10}, factor_series={"X":[float("nan")]*10}, metadata={"governed": False}); GovernedStatsmodelsInputAdapter().validate(ds); print("validated")'`
- All three commands printed `validated`, which means the current adapter accepts precisely the inputs the governance doc says it rejects.
- Required fix: either implement the documented governed checks and add regression tests for them, or narrow the governance claims to match the current implementation before this row is marked `governed`.

2. The maturity and inventory truth is not actually synced to the governed closeout yet.

- The task handoff says activation, maturity, and inventory truth were synced, but the matrix summary still lists statsmodels among activation-ready backends instead of the governed production path in [RESEARCH_BACKEND_MATURITY_MATRIX.md](/home/lupin/code/pantheon/RESEARCH_BACKEND_MATURITY_MATRIX.md:177).
- The development inventory still lists `statsmodels` under "OSS next-wave" as `task materialization` work in [docs/reviews/2026-04-20-development-progress-and-next-work-inventory.md](/home/lupin/code/pantheon/docs/reviews/2026-04-20-development-progress-and-next-work-inventory.md:320).
- The local dependency header also still claims "no governed adapter implemented yet" in [services/research/statsmodels/requirements.txt](/home/lupin/code/pantheon/services/research/statsmodels/requirements.txt:1).
- Result: the task acceptance around "adapter / smoke-test / governed I/O boundary clear" and the claimed sync to governed status are not met yet because the repo still carries conflicting statements about statsmodels readiness.
- Required fix: update the remaining stale maturity/inventory/evidence text so every referenced surface tells the same governed-status story.

## Verification

- Re-ran `python3 services/research/statsmodels/smoke_test.py` with `SMOKE TEST PASSED`.
- Re-ran `python3 -m pytest services/research/statsmodels/test_adapter.py -q` with `20 passed in 0.26s`.
- Re-ran `python3 services/research/statsmodels/worker.py` and confirmed the sample-dataset fallback still emits a draft `regime_report` envelope.
