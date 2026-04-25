# STATE-REBASE-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `STATE-REBASE-001` - Rebaseline canonical state trackers to one truthful execution picture  
**Parent Owner**: `Codex`  
**Parent Reviewer**: `Claude`  
**Parent Status**: `review_approved` (waiting for finalization to `done`)  
**Sidecar Task**: `STATE-REBASE-001-SIDECAR-ACCEPTANCE`  
**Sidecar Owner**: `Gemini`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `acceptance_packet`  
**Generated**: `2026-04-19`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance implementations. It
> provides the acceptance verification and dependency mapping for the
> `STATE-REBASE-001` rebaseline.

---

## 1. Executive Summary

`STATE-REBASE-001` was triggered to eliminate tracking drift across the
Pantheon collaboration layer. Over time, machine-readable state
(`ai-status.json`), human-readable summaries (`current-work.md`), and the
productization backlog (`WORKBENCH_DELIVERY_BACKLOG.md`) had diverged,
specifically regarding the status of already-reviewed operator and governance
loops.

This rebaseline successfully:
1. Cleared stale dispatch messages from idle agents in the status system.
2. Synchronized the workbench backlog to reflect that `PKT-001` through
   `PKT-014` are now part of the landed baseline.
3. Separated "productization gaps" from "closeout bookkeeping," moving the latter
   into dedicated execution tasks (`APP-003-CLOSEOUT-001`).

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Canonical task board; now unblocked and cleared of stale idle-agent text |
| `docs/reviews/2026-04-19-state-rebaseline-001.md` | Record of the drift findings and resolutions applied during the rebaseline |
| `WORKBENCH_DELIVERY_BACKLOG.md` | Canonical backlog; now shows `PKT-001` to `PKT-014` as landed |
| `scripts/ai_status.py` | Implementation fix at `recompute_agents()` to prevent future stale-text drift |
| `.orchestrator/task-briefs/state_rebase_001_sidecar_acceptance.md` | Sidecar scope and support-only constraint |

---

## 3. Acceptance Checklist Verification

The following resolutions from `STATE-REBASE-001` have been verified:

| Resolution Item | Verification Method | Status |
|---|---|---|
| **Stale Dispatch Clearance** | Checked `ai_status.py:1057` logic; verified `recompute_agents()` clears `next` field for idle agents with no queued work. | PASS |
| **Backlog Synchronization** | Verified `WORKBENCH_DELIVERY_BACKLOG.md` moved reviewed governance and Operator Wave 2 surfaces (PKT-001 to PKT-014) to "Already Landed Baselines." | PASS |
| **Backlog Rule Enforcement** | Verified the new rule: "Closure-record sync by itself does not keep a module on the remaining backlog." | PASS |
| **Closeout Materialization** | Verified `APP-003-CLOSEOUT-001` exists in `ai-status.json` to handle residual bookkeeping for landed surfaces. | PASS |

---

## 4. Dependency Map

The successful rebaseline unblocks or provides the truthful anchor for the following Wave 1 and Wave 2 tasks:

### 4.1 Direct Execution Dependencies

| Task ID | Title | Relationship to Rebaseline |
|---|---|---|
| `APP-003-CLOSEOUT-001` | Close out APP-003 delivery truth | Inherits the residual work for landed surfaces (PKT-001-014) |
| `DEPTH-REBASE-001` | Reconcile canonical deep-task backlog | Depends on the clean state board to begin reconciling deep-work gaps |
| `RW-01-FOUNDATION-001` | Publish Research Ticket identity | Depends on the updated workbench backlog to anchor the new Research family |
| `KW-01-FOUNDATION-001` | Publish Institutional Memory browse | Depends on the updated workbench backlog to anchor the new Knowledge family |
| `CW-01-FOUNDATION-001` | Publish Consult Request identity | Depends on the updated workbench backlog to anchor the new Consultation family |
| `TW-01-FOUNDATION-001` | Publish Trainer session lifecycle | Depends on the updated workbench backlog to anchor the new Trainer family |

### 4.2 Indirect / Downstream Impact

| Task Family | Impact |
|---|---|
| **Evolution Workbench** | `EW-04` and `EW-05` activation depends on the completion of `DEPTH-REBASE-001` and `APP-003-CLOSEOUT-001`. |
| **Governance Waves** | Future governance packets will now be tracked against a clean baseline starting from `PKT-015`. |

---

## 5. Wave 1/2 Support Packet

For agents picking up the unblocked tasks, the following "Truth Snapshot" should be used:

1. **Landed Status**: `PKT-001` to `PKT-014` are DONE from a product/UI perspective. Any remaining issues found in these surfaces should be treated as NEW bugs or explicit FOLLOW-UPS, not as "missing original implementation."
2. **Backlog Integrity**: `WORKBENCH_DELIVERY_BACKLOG.md` is now the single truthful source for remaining module delivery. If a module is not in "Remaining Backlog," it is either landed or deferred.
3. **Status Truth**: `ai-status.json` is the sole source for task ownership and lifecycle. `current-work.md` is a derived view and should not be used as a primary decision source if it appears to lag behind `ai-status.json`.

---

## 6. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only `support/sidecars/STATE-REBASE-001/STATE-REBASE-001-SIDECAR-ACCEPTANCE.md` is created. |
| No canonical truth edited | PASS | No L0/L1 policy or coordination files were modified by this sidecar. |
| Accuracy vs. Parent | PASS | Content is anchored to `docs/reviews/2026-04-19-state-rebaseline-001.md` and repo reality. |
| Dependency Completeness | PASS | All tasks listing `STATE-REBASE-001` as a dependency in `ai-status.json` are mapped. |

---

## 7. Handoff to Reviewer (`Claude`)

This sidecar is ready for review as the acceptance packet for `STATE-REBASE-001`.

What it gives you:
1. Verification that the rebaseline resolutions (backlog sync and code fix) are correctly implemented.
2. A map of the 6+ tasks that were unblocked by this work.
3. A "Truth Snapshot" to ensure subsequent agents stay aligned with the new baseline.

Recommended reviewer stance:
1. Approve this sidecar if it accurately reflects the rebaseline state and dependency reality.
2. Ensure the parent task (`STATE-REBASE-001`) is finalized to `done` by the owner (`Codex`) to formally close the rebaseline loop.

---
*Generated by Gemini as a sidecar `acceptance_packet` helper for `STATE-REBASE-001`. This file is a support artifact and does not modify canonical truth.*
