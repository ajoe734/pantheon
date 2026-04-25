# OSS-IMPL-003 Review Packet

**Sidecar task:** `OSS-IMPL-003-SIDECAR-REVIEW`  
**Parent task:** `OSS-IMPL-003`  
**Parent title:** `Implement vectorbt governed adapter with smoke test`  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Packet author:** `Codex2`  
**Packet reviewer:** `Codex`  
**Created:** `2026-04-17`  
**Purpose:** Support artifact only. Summarizes the current implementation snapshot, the reviewer-reopened blocker, refreshed evidence after the follow-up fix, and reviewer-facing disposition without modifying canonical truth or the parent runtime slice.

> Scope declaration: this file does not edit L1 canonical policy, the vectorbt adapter implementation, or the parent task state contract. It only packages review evidence for the assigned reviewer.

## 1. Current Snapshot

From `ai-status.json`:

- Parent `OSS-IMPL-003` is `status=review_approved`
- Owner is `Claude`
- Reviewer is `Codex`
- Parent reviewer note now records that local reruns passed for the smoke test and all 28 unit tests, and that the checklist entry was refreshed to match the current suite size
- Parent acceptance contract remains:
  - unit tests all pass
  - smoke test emits a registry-ready artifact with `artifact_state=draft`
  - `OSS_INTEGRATION_CHECKLIST.md` promotes `vectorbt` to `smoke-tested`

This sidecar exists because the parent review was reopened after reviewer verification found a real blocker that the previous packet missed: `GovernedVectorbtInputAdapter` preserved caller record order instead of sorting per instrument by date, so unsorted OHLCV input could silently change backtest output. The current repo snapshot now includes the follow-up fix plus regression tests; the parent has already passed reviewer gate, and this packet now preserves that blocker-and-fix history as a support artifact for final closeout.

## 2. Acceptance Criteria Verification

| # | Criterion | Status | Evidence |
|---|---|---|---|
| AC-1 | Unit tests all pass | PASS | `python3 -m unittest services/research/vectorbt/test_adapter.py` reproduced `Ran 28 tests ... OK` on `2026-04-17` |
| AC-2 | Smoke test emits registry-ready artifact with `artifact_state=draft` | PASS | `python3 services/research/vectorbt/smoke_test.py` prints `artifact_type: backtest_result`, `artifact_state: draft`, `deployment_stage: none`, and `assertions: OK` |
| AC-3 | Checklist row moves to `smoke-tested` only after proof exists | PASS | `OSS_INTEGRATION_CHECKLIST.md:43` records `vectorbt` as `smoke-tested`, cites the governed artifact envelope, and now matches the current `28`-test regression suite |

## 3. Artifact Evidence Map

| Artifact | Evidence summary |
|---|---|
| `services/research/vectorbt/adapter/vectorbt_adapter.py` | Declares the governed boundary at [services/research/vectorbt/adapter/vectorbt_adapter.py](/home/edna/code/pantheon/services/research/vectorbt/adapter/vectorbt_adapter.py:1); the reopened review blocker was in `GovernedVectorbtInputAdapter.prepare()`, now addressed by collecting `(date, bar)` pairs per instrument and sorting them chronologically before building `ohlcv_by_instrument` at [services/research/vectorbt/adapter/vectorbt_adapter.py](/home/edna/code/pantheon/services/research/vectorbt/adapter/vectorbt_adapter.py:127); implements deterministic `StubVectorbtBackend` at line 301; gates the real upstream backend behind `PANTHEON_VECTORBT_BACKEND=real` at line 339; emits canonical `artifact_state=draft` and `deployment_summary.current_stage=none` through `run_vectorbt_workflow()` and the registry builders later in the file |
| `services/research/vectorbt/smoke_test.py` | Builds the deterministic two-instrument smoke dataset at [services/research/vectorbt/smoke_test.py](/home/edna/code/pantheon/services/research/vectorbt/smoke_test.py:18); defaults to the stub backend at line 56; asserts `artifact_family=vectorbt_backtest`, `artifact_state=draft`, `current_stage=none`, and non-live governance flags at line 86 |
| `services/research/vectorbt/test_adapter.py` | Covers schema rejection and lineage fallback at [services/research/vectorbt/test_adapter.py](/home/edna/code/pantheon/services/research/vectorbt/test_adapter.py:53); adds two regression tests for the reopened blocker at [services/research/vectorbt/test_adapter.py](/home/edna/code/pantheon/services/research/vectorbt/test_adapter.py:114), proving reversed-date AAA input produces identical sorted bars and identical `total_return`; confirms canonical registry and governance defaults through the workflow output tests at line 182 |
| `OSS_INTEGRATION_CHECKLIST.md` | The `vectorbt` row at [OSS_INTEGRATION_CHECKLIST.md](/home/edna/code/pantheon/OSS_INTEGRATION_CHECKLIST.md:43) records `smoke-tested`, the implemented adapter/backend surfaces, the current `28`-test regression suite, and the remaining Gate 2 evidence-pack follow-up |
| `services/research/vectorbt/ACTIVATION_CRITERIA.md` | Gate 1 still lists `worker.py` and `examples/strategy_dataset_sample.json` at [services/research/vectorbt/ACTIVATION_CRITERIA.md](/home/edna/code/pantheon/services/research/vectorbt/ACTIVATION_CRITERIA.md:70), which are outside the current materialized `OSS-IMPL-003` slice and absent from the delivered files |

## 4. Reproduced Evidence

### 4.1 Smoke Test

Command run:

```bash
python3 services/research/vectorbt/smoke_test.py
```

Observed output summary:

- smoke banner printed
- backend used: `stub_backtest`
- dataset shape:
  - `2` instruments
  - `70` total bars
- artifact emitted with:
  - `artifact_type = backtest_result`
  - `artifact_state = draft`
  - `current_stage = none`
  - `artifact_family = vectorbt_backtest`
  - `framework = vectorbt`
  - `direct_live_influence = False`
  - `lean_consumption = scoring_only_not_direct_action`
- aggregate metrics reported:
  - `mean_max_drawdown = 0.007993`
  - `mean_sharpe_ratio = 1.14319`
  - `mean_total_return = 0.008743`
  - `num_instruments = 2`
  - `total_trades = 2`
- final line: `assertions: OK`

### 4.2 Unit Tests

Command run:

```bash
python3 -m unittest services/research/vectorbt/test_adapter.py
```

Observed result:

- `28` tests ran
- status `OK`
- total runtime about `0.134s`

No warnings or review-blocking stderr were emitted during the reproduced verification.

### 4.3 Reopened Blocker and Current Fix State

Reviewer reopen reason recorded in `ai-status.json`:

- prior packet incorrectly claimed no acceptance-blocking issues
- actual blocker: `GovernedVectorbtInputAdapter` ignored per-instrument date ordering and preserved caller record order
- impact: unsorted OHLCV input could silently change results
- concrete repro from the reviewer handoff: reversing `AAA` records changed stub `total_return` from `0.008743` to `0.0`

Current repo state after the parent follow-up:

- `prepare()` now sorts each instrument's bars by ISO `date` string before materializing `ohlcv_by_instrument`
- two regression tests were added and pass locally:
  - reversed-date input yields identical sorted `AAA` bars
  - reversed-date input yields identical `AAA total_return`

This means the historical acceptance blocker was real and should remain documented here, but the latest repo snapshot no longer reproduces that failure.

## 5. Reviewer Findings

### Finding 1

**Severity:** non-blocking  
**Location:** [services/research/vectorbt/ACTIVATION_CRITERIA.md](/home/edna/code/pantheon/services/research/vectorbt/ACTIVATION_CRITERIA.md:70)

`ACTIVATION_CRITERIA.md` still describes `services/research/vectorbt/worker.py` and `services/research/vectorbt/examples/strategy_dataset_sample.json` as Gate 1 prerequisites for `smoke-tested`, but neither file is part of the current materialized `OSS-IMPL-003` scope or present in the delivered implementation. The parent task artifacts, reproduced evidence, and checklist row are internally consistent, so this is documentation drift rather than an acceptance blocker for the implemented adapter/smoke-test/test/checklist slice.

### Finding 2

**Severity:** resolved blocker  
**Location:** [services/research/vectorbt/adapter/vectorbt_adapter.py](/home/edna/code/pantheon/services/research/vectorbt/adapter/vectorbt_adapter.py:127), [services/research/vectorbt/test_adapter.py](/home/edna/code/pantheon/services/research/vectorbt/test_adapter.py:114)

The prior acceptance blocker was real: unsorted per-instrument dates could change backtest output because the adapter preserved caller record order. The current snapshot addresses that defect by sorting per instrument before storing bars and by adding regression coverage that passes locally. Reviewer attention should now focus on whether the fix and new tests are sufficient, not on the earlier stale packet claim that no blocker ever existed.

## 6. Parent/Sidecar Boundary

This packet intentionally does not:

- edit `services/research/vectorbt/` code
- edit `OSS_INTEGRATION_CHECKLIST.md`
- edit `services/research/vectorbt/ACTIVATION_CRITERIA.md`
- alter parent ownership or lifecycle beyond normal sidecar handoff
- claim Gate 2 `governed` status for `vectorbt`

This packet does:

- summarize the fresh implementation state for the assigned reviewer
- preserve reproducible smoke and unit evidence in one place
- record the real reopen reason and its current fix state
- flag the activation-criteria drift as a non-blocking follow-up note

## 7. Reviewer Handoff for Codex

Recommended reviewer disposition for `OSS-IMPL-003-SIDECAR-REVIEW`:

- approve the sidecar if this packet accurately reflects both the historical reopen blocker and the current repo snapshot after the follow-up fix
- use this packet as quick context when reviewing the parent `OSS-IMPL-003`
- treat the remaining Gate 1 documentation drift as follow-up cleanup, not as a blocker on the delivered materialized scope
- evaluate the parent on the strength of the new sort-by-date implementation and the two regression tests, rather than the stale earlier packet

Suggested approval command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/OSS-IMPL-003/OSS-IMPL-003-SIDECAR-REVIEW.md REVIEW_NOTES_ZH="Review packet 已改為反映 OSS-IMPL-003 真正的 reopen blocker：vectorbt adapter 先前未依 instrument/date 排序，可能讓未排序 OHLCV 改變回測結果。當前 repo 已補排序修復與 2 個 regression tests；我本地重跑 smoke test 與 28 個單元測試皆通過。ACTIVATION_CRITERIA 仍有非阻塞文件漂移，但 checklist 已與 28 tests 對齊。" python3 scripts/ai_status.py approve OSS-IMPL-003-SIDECAR-REVIEW "Review packet verified against the reopened vectorbt date-ordering blocker and the current fix snapshot; smoke test and 28 unit tests reproduced; remaining activation-criteria drift recorded as non-blocking follow-up."
```

If Codex agrees this packet is now an accurate blocker-and-fix summary, the sidecar can move to `review_approved` while the parent `OSS-IMPL-003` continues through owner finalization independently of this support artifact.
