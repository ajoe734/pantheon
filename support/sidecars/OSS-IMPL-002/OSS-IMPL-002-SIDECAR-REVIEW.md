# OSS-IMPL-002 Review Packet

Last updated: 2026-04-17
Sidecar task: `OSS-IMPL-002-SIDECAR-REVIEW`
Parent task: `OSS-IMPL-002`
Parent owner: `Codex2`
Parent reviewer: `Codex`
Packet author: `Codex2`
Packet reviewer: `Codex`
Status: reviewer-refreshed against the archived parent outcome

> Scope declaration: support artifact only. This packet does not change L1 canonical truth, parent implementation, or parent task state. It summarizes the final blocker and evidence history after the parent task closed, correcting the earlier mid-review snapshot that was later overtaken by additional reopens and a final live rerun.

Owner finalize note:

- 2026-04-17: owner re-checked that this packet remains support-only, matches the archived parent outcome, and is suitable for terminal handoff closure.

## 1. Parent Snapshot

From `python3 scripts/ai_status.py show OSS-IMPL-002` on 2026-04-17:

- Parent now resolves to the archive snapshot at [ai-task-archive/tasks/OSS-IMPL-002.json](/home/lupin/code/pantheon/ai-task-archive/tasks/OSS-IMPL-002.json:1)
- `archived_at = 2026-04-17T19:15:15Z`
- `terminal_status = done`
- `terminal_outcome = completed`
- Final parent `next` says QuantLib smoke coverage, governed output path, revised American regression, and checklist promotion are closed as done
- Archived `review_notes_zh` say the reviewer re-ran both the stub-path evidence and the pinned live rerun, and found the QuantLib adapter, American binomial regression, and smoke evidence consistent

This means the earlier packet claim that parent `OSS-IMPL-002` was "currently `status=review`" is no longer true. The sidecar packet now needs to serve as a historical blocker and evidence summary for a parent that already finished, not as a recommendation on an active parent review.

## 2. Blocker Timeline

### Blocker 1: American-path Greek scaling was materially wrong

The first parent reopen found American-option Greek scaling errors in the real `QuantLibBackend` finite-difference path at [services/research/quantlib/adapter/quantlib_adapter.py](/home/lupin/code/pantheon/services/research/quantlib/adapter/quantlib_adapter.py:340).

Archive summary:

- `vega` was materially understated relative to the governed per-1% convention
- `rho` was materially understated because the 1bp finite-difference bump was not converted back to the per-100bp reporting convention

That finding invalidated the earlier sidecar claim that no acceptance-blocking issue existed.

### Blocker 2: American analytics mixed two different models

The second parent reopen found that American options were still inconsistent even after the scaling fix:

- NPV came from the American binomial CRR path
- `delta` / `gamma` / `vega` / `theta` / `rho` still came from a European Black-Scholes proxy instead of bumped American-engine values
- The first regression only compared against the same proxy, so it could not catch the mismatch

That meant the "fix" was still acceptance-blocking until the backend used one consistent American pricing path for both NPV and Greeks.

### Blocker 3: the new live regression assertion was brittle

The third parent reopen came from the pinned live rerun, not the default workspace:

- `PYTHONPATH=/tmp/oss-impl-002-site python3 -m pytest services/research/quantlib/test_adapter.py -q`
- Failure was at [services/research/quantlib/test_adapter.py](/home/lupin/code/pantheon/services/research/quantlib/test_adapter.py:286)
- The unstable condition was a hard `max(divergences.values()) > 0.01` threshold, where live QuantLib produced `abs(expected['vega'] - baseline['vega']) = 0.009012`

The backend output itself matched the bumped American engine expectation. The blocker was the regression's brittle proof shape, not the pricing path.

### Final accepted state

The archived parent snapshot shows all three blockers were resolved before closeout:

- American options now price NPV with `_american_npv()` and derive finite-difference Greeks from the same bumped American path at [services/research/quantlib/adapter/quantlib_adapter.py](/home/lupin/code/pantheon/services/research/quantlib/adapter/quantlib_adapter.py:272) and [services/research/quantlib/adapter/quantlib_adapter.py](/home/lupin/code/pantheon/services/research/quantlib/adapter/quantlib_adapter.py:340)
- `vega` now reports the per-1% volatility delta and `rho` converts the 1bp bump back to the governed per-100bp convention at [services/research/quantlib/adapter/quantlib_adapter.py](/home/lupin/code/pantheon/services/research/quantlib/adapter/quantlib_adapter.py:382) and [services/research/quantlib/adapter/quantlib_adapter.py](/home/lupin/code/pantheon/services/research/quantlib/adapter/quantlib_adapter.py:384)
- The final QuantLib-backed regression at [services/research/quantlib/test_adapter.py](/home/lupin/code/pantheon/services/research/quantlib/test_adapter.py:269) checks parity with the bumped American engine and verifies non-proxy behavior without relying on the brittle fixed threshold

## 3. Acceptance Criteria Check

| # | Criterion | Status | Evidence |
|---|---|---|---|
| AC-1 | Local workspace evidence is represented accurately | PASS | Fresh rerun in this workspace is `17 passed, 1 skipped` plus `python3 services/research/quantlib/smoke_test.py => assertions: OK`. |
| AC-2 | Final parent evidence is represented accurately | PASS | Archived parent snapshot records the additional pinned live rerun: `PYTHONPATH=/tmp/oss-impl-002-site python3 -m pytest ... => 18 passed` and `... smoke_test.py --backend real => OK`. |
| AC-3 | Checklist row moves to `smoke-tested` only after proof exists | PASS | [OSS_INTEGRATION_CHECKLIST.md](/home/lupin/code/pantheon/OSS_INTEGRATION_CHECKLIST.md:45) records `QuantLib` as `smoke-tested` and cites both local and pinned live evidence. |
| AC-4 | Parent blocker history is complete and current | PASS | This packet now includes all three parent reopens: scaling error, mixed-model American analytics, and the brittle live-regression threshold. |
| AC-5 | Parent terminal state is stated correctly | PASS | The packet now records that parent `OSS-IMPL-002` is archived `done`, not currently `review`. |

## 4. Fresh Evidence

### 4.1 Local workspace rerun

Commands rerun:

```bash
python3 services/research/quantlib/smoke_test.py
python3 -m pytest services/research/quantlib/test_adapter.py -q
```

Observed result:

- `artifact_family: pricing_report`
- `framework: quantlib`
- `artifact_state: draft`
- `deployment_stage: none`
- `direct_influence: False`
- `lean_consumption: research_only_not_direct_action`
- `option_count: 1`
- `bond_count: 1`
- `assertions: OK`
- `17 passed, 1 skipped in 0.09s`

### 4.2 Archived pinned live rerun

From [ai-task-archive/tasks/OSS-IMPL-002.json](/home/lupin/code/pantheon/ai-task-archive/tasks/OSS-IMPL-002.json:1):

- `PYTHONPATH=/tmp/oss-impl-002-site python3 -m pytest services/research/quantlib/test_adapter.py -q => 18 passed`
- `PYTHONPATH=/tmp/oss-impl-002-site python3 services/research/quantlib/smoke_test.py --backend real => OK`

Interpretation:

- The current workspace still cannot independently prove the real backend because `QuantLib` is unavailable here, so the real-backed regression remains skipped locally.
- The archived parent snapshot fills that gap with the pinned live rerun that the reviewer accepted before final closeout.

## 5. Artifact Review Map

| Artifact | Review relevance |
|---|---|
| [services/research/quantlib/adapter/quantlib_adapter.py](/home/lupin/code/pantheon/services/research/quantlib/adapter/quantlib_adapter.py:272) | Real backend lives here. Final American-path pricing now keeps NPV and Greeks on the same binomial CRR path and preserves governed scaling. |
| [services/research/quantlib/test_adapter.py](/home/lupin/code/pantheon/services/research/quantlib/test_adapter.py:269) | Final conditional `QuantLibBackend` regression compares backend output against bumped American-engine expectations and stable non-proxy behavior. |
| [services/research/quantlib/smoke_test.py](/home/lupin/code/pantheon/services/research/quantlib/smoke_test.py:23) | Confirms the governed smoke path and draft artifact envelope. |
| [OSS_INTEGRATION_CHECKLIST.md](/home/lupin/code/pantheon/OSS_INTEGRATION_CHECKLIST.md:45) | Shows `QuantLib` promoted to `smoke-tested` with both local and pinned live evidence. |
| [services/research/quantlib/ACTIVATION_CRITERIA.md](/home/lupin/code/pantheon/services/research/quantlib/ACTIVATION_CRITERIA.md:102) | Still contains Gate 1 drift referencing `worker.py` and `examples/pricing_dataset_sample.json`; this remains a non-blocking support-doc mismatch. |
| [ai-task-archive/tasks/OSS-IMPL-002.json](/home/lupin/code/pantheon/ai-task-archive/tasks/OSS-IMPL-002.json:1) | Canonical final parent snapshot for the task this sidecar supports. |

## 6. Reviewer Findings

### Finding 1

Severity: resolved in parent, but required packet refresh

The earlier packet was materially wrong because it omitted the actual parent blocker. The intermediate replacement packet then became stale again because the parent continued through two more reopens and a final live rerun before it was archived done. This refresh fixes both problems by aligning the packet with the archived parent history instead of freezing it at the first correction point.

### Finding 2

Severity: non-blocking

`ACTIVATION_CRITERIA.md` still lists `worker.py` and `examples/pricing_dataset_sample.json` as Gate 1 prerequisites for `smoke-tested`, but those files are outside the delivered materialized parent slice and not present in `services/research/quantlib/`. This is documentation drift, not the parent review blocker.

## 7. Handoff Guidance

Recommended reviewer disposition for `OSS-IMPL-002-SIDECAR-REVIEW`:

- approve this sidecar if the goal is to keep a truthful support packet for the finished parent task
- use it as historical reviewer context for archived parent `OSS-IMPL-002`, not as an active parent gate
- distinguish the two evidence tiers clearly:
  - local workspace: `17 passed, 1 skipped` plus stub smoke test OK
  - archived live rerun: `18 passed` plus real-backend smoke test OK
- keep the `ACTIVATION_CRITERIA.md` Gate 1 drift as a non-blocking follow-up note only

Suggested approval command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/OSS-IMPL-002/OSS-IMPL-002-SIDECAR-REVIEW.md REVIEW_NOTES_ZH="Review packet 已更新為最終 archived parent 快照：不再把 OSS-IMPL-002 寫成仍在 review，而是明確記錄三次 reopen（vega/rho mis-scaling、American mixed-model analytics、brittle live regression threshold）、本地 17 passed/1 skipped 與 stub smoke OK，以及 archive 中已被 reviewer 接受的 pinned live rerun 18 passed 與 real-backend smoke OK。" python3 scripts/ai_status.py approve OSS-IMPL-002-SIDECAR-REVIEW "Review packet refreshed against the archived parent outcome: it now records the full QuantLib blocker timeline, the final done status, the local 17 passed / 1 skipped evidence, and the archived pinned live rerun that closed the parent."
```
