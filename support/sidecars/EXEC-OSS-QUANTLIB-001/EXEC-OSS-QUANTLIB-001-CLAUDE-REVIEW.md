# EXEC-OSS-QUANTLIB-001 — Claude Review

Date: `2026-04-21`
Task: `EXEC-OSS-QUANTLIB-001` — Advance QuantLib next-wave execution readiness
Reviewer: Claude (auto-reassigned from Copilot after quota terminal 402)
Owner: Codex
Status at review start: `review`

---

## Acceptance Criteria Check

| Criterion | Result |
|---|---|
| QuantLib next-wave gap 與第一個可執行 slice 明確 | **Met** |
| adapter / smoke-test / governed I/O 邊界清楚 | **Met** |
| 留下可 review 的 execution-ready plan 或 patch set | **Met** |

---

## Evidence Review

### 1. Next-wave gap and executable slice

`services/research/quantlib/ACTIVATION_CRITERIA.md` documents status as `governed / evidence-complete`.
The task's original scope — source selection, adapter boundary, smoke-test next step — is now fully closed.

The remaining follow-on item (CI matrix wiring, `quantlib-smoke` job addition) is explicitly
acknowledged in `ACTIVATION_CRITERIA.md §Smoke-Test Plan` as a post-governed step and does not
block the governed baseline claim.

### 2. Adapter / smoke-test / governed I/O boundary

Verified in `services/research/quantlib/adapter/quantlib_adapter.py`:

- `GovernedQuantLibInputAdapter.validate()` enforces required fields (`dataset_id`, `source_dataset_refs`,
  at least one option spec, at least one bond spec) and raises `QuantLibWorkflowError` before any backend runs.
- `StubQuantLibBackend` provides deterministic CI-safe output with no external market-data dependency.
- `QuantLibBackend` (real backend) is gated behind `PANTHEON_QUANTLIB_BACKEND=real`.
- `run_quantlib_workflow()` is the single governed entry point.

Output invariants enforced at code level:
- `artifact_family = "pricing_report"`
- `artifact_state = "draft"`
- `deployment_summary.current_stage = "none"`
- `governance.direct_live_influence = false`
- `governance.lean_consumption = "research_only_not_direct_action"`

`integrations/quantlib/governance.md` confirms the research-only boundary and rejection of
direct writes to live execution, signal-routing, or trade placement.

### 3. Execution-ready implementation

Full governed surface present in repo:

- `services/research/quantlib/adapter/quantlib_adapter.py` — adapter + backends + entrypoint
- `services/research/quantlib/smoke_test.py` — governed smoke path (assertions: OK, 2026-04-21)
- `services/research/quantlib/test_adapter.py` — 17 passed, 1 skipped (expected: real-backend skip)
- `services/research/quantlib/worker.py` — container entrypoint with sample dataset fallback
- `services/research/quantlib/examples/pricing_dataset_sample.json` — governed sample
- `integrations/quantlib/{integration,governance,smoke_test}.md` — complete evidence pack
- `OSS_INTEGRATION_CHECKLIST.md` QuantLib row — `governed` with full verification summary
- `RESEARCH_BACKEND_MATURITY_MATRIX.md` — classifies QuantLib as Production Research Path

### 4. OSS Integration Checklist consistency

`OSS_INTEGRATION_CHECKLIST.md` QuantLib entry (line 45) records:
- Version pin: `QuantLib-Python==1.18` ✓
- Adapter components: named ✓
- Worker and sample dataset: confirmed ✓
- Smoke revalidated 2026-04-21: `17 passed, 1 skipped` ✓
- Real-backend evidence from 2026-04-17: `18 passed` ✓
- Canonical evidence pack: `integrations/quantlib/` ✓

### 5. Cross-check: RESEARCH_BACKEND_MATURITY_MATRIX.md

QuantLib listed in the Production Research Path tier (§ Research Backend Maturity Matrix).
Missing proof column reads: "Keep smoke, worker entrypoint, and evidence pack refreshed when the
pin or backend changes." — This is a maintenance note, not an unmet gate.

---

## Reviewer Notes

- All three acceptance criteria are met.
- The governed baseline (adapter, smoke, unit coverage, worker, sample dataset, evidence pack)
  is complete and verified.
- CI matrix wiring is the only remaining item; it is a follow-on that does not affect the
  governed status gate.
- Doc metadata showing `Reviewer: Claude` in QuantLib evidence files is now consistent with the
  current reviewer assignment (Claude, after Copilot quota terminal reassignment).
- The sidecar review (`EXEC-OSS-QUANTLIB-001-SIDECAR-REVIEW.md`) was separately approved by Claude
  and is awaiting Codex finalization; it does not affect this parent review disposition.

---

## Disposition

**Approved.** Returning to owner Codex for finalization.

Next step for owner:
- Run `ai-status.sh done EXEC-OSS-QUANTLIB-001` with a checkpoint message.
- No additional implementation is required to close this task.
- CI matrix wiring (`quantlib-smoke` job) may be tracked separately as a maintenance follow-on.
