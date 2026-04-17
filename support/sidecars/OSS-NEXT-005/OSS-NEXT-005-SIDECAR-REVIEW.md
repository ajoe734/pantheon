# OSS-NEXT-005 — Review Packet & Evidence Summary

**Sidecar Task ID**: `OSS-NEXT-005-SIDECAR-REVIEW`
**Parent Task**: `OSS-NEXT-005`
**Parent Owner**: Codex
**Parent Reviewer**: Claude
**Parent Status**: `review_approved`
**Sidecar Owner**: Codex2
**Sidecar Reviewer**: Codex
**Helper Kind**: `review_packet`
**Date**: 2026-04-17

> This is a support artifact only. It does not modify canonical truth, L1 policy, or core runtime/registry/governance implementations.

## 1. Current Snapshot

`OSS-NEXT-005` is the Phase 6 vectorbt materialization slice. Its acceptance target is to move vectorbt from a pure maturity-matrix mention into a named, execution-ready task family with:

1. selected upstream source
2. pinned package version
3. governed adapter boundary
4. smoke-test plan

Per `ai-status.json`, the parent task is currently `owner=Codex`, `reviewer=Claude`, `status=review_approved`, with owner finalization still pending.

Reviewer feedback recorded in `ai-status.json` shows the parent review converged to one concrete change request:

- add the missing `vectorbt` row to `OSS_INTEGRATION_CHECKLIST.md` with `status=version-pinned` and the follow-on work wording (`governed input adapter`, `stub/real backend split`, `smoke test`, `governance evidence pack`)

The parent owner then reported that this checklist gap was fixed, and the parent reviewer subsequently approved the task for owner finalization.

## 2. Acceptance Contract

From `ai-status.json`, `OSS-NEXT-005` must satisfy:

1. vectorbt has a selected source, proposed pin, governed adapter boundary, and smoke-test plan
2. the backend no longer lives only as a maturity-matrix mention without a real task cut

This sidecar packet does not re-review canonical truth. It packages the evidence already present in repo-local artifacts so the assigned sidecar reviewer can verify the parent review state quickly.

## 3. Evidence Summary

### 3.1 Parent Artifact Map

| Artifact | Evidence | What it proves |
|---|---|---|
| `services/research/vectorbt/ACTIVATION_CRITERIA.md` | Owner/reviewer metadata, upstream source, `vectorbt==0.26.2`, adapter gates, smoke-test plan | The task has a concrete source selection, version pin, adapter design target, and status advancement gates |
| `integrations/vectorbt/integration.md` | Locked upstream selection, adapter surface, output contract, runtime notes, status ladder | vectorbt is no longer a vague planning placeholder; the governed integration baseline is materialized |
| `services/research/vectorbt/requirements.txt` | `vectorbt==0.26.2` pinned | The selected upstream package is concretely pinned in the repo |
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` | vectorbt row is now `version-pinned`, `Activation-Ready`, owner `Codex`, with explicit remaining work | The maturity matrix now reflects vectorbt as a materialized baseline rather than `not-started` |
| `OSS_INTEGRATION_CHECKLIST.md` | Component Inventory row for `vectorbt` with `status=version-pinned` and follow-on work wording | The reviewer-requested checklist artifact gap is now closed |
| `docs/reviews/2026-04-17-next-wave-implementation-plan.md` | `OSS-NEXT-005` assigned to `Codex` with reviewer `Claude` | Ownership and implementation-wave planning align with `ai-status.json` |

### 3.2 Reviewer-Requested Fix

The review thread in `ai-status.json` records this sequence:

1. Claude reviewed the vectorbt baseline and found one missing artifact alignment issue.
2. The issue was not in the activation/integration docs; it was the missing `vectorbt` row in `OSS_INTEGRATION_CHECKLIST.md`.
3. Codex updated the checklist row and returned the parent task to review.

Current checklist evidence:

| Field | Value |
|---|---|
| Component | `vectorbt` |
| Type | `Python package/framework` |
| Status | `version-pinned` |
| Scope | rapid strategy backtesting and vectorized portfolio simulation for Research Plane prototyping |
| Pin | `vectorbt==0.26.2` |
| Activation owner | `Codex` |
| Remaining work | governed input adapter, stub/real backend split, smoke test, governance evidence pack |

### 3.3 Acceptance Coverage

| Acceptance criterion | Coverage |
|---|---|
| Selected source | `polakowo/vectorbt` is named in both `ACTIVATION_CRITERIA.md` and `integrations/vectorbt/integration.md` |
| Proposed pin | `vectorbt==0.26.2` is pinned in `services/research/vectorbt/requirements.txt` and referenced in both docs |
| Governed adapter boundary | `GovernedVectorbtInputAdapter`, backend split, output contract, and draft-only registry boundary are specified in the docs |
| Smoke-test plan | Step-by-step smoke path is documented in `ACTIVATION_CRITERIA.md` and summarized in `integrations/vectorbt/integration.md` |
| Real task cut | vectorbt now has repo-local activation criteria, requirements, integration baseline, matrix entry, and checklist entry |

## 4. Findings

| Finding | Severity | Detail |
|---|---|---|
| Parent acceptance criteria appear satisfied at the materialization level | ✅ | Source selection, version pin, adapter boundary, and smoke-test plan are all documented repo-locally |
| Reviewer-requested checklist gap appears closed | ✅ | `OSS_INTEGRATION_CHECKLIST.md` now has the missing `vectorbt` Component Inventory row |
| Ownership metadata is aligned | ✅ | `ai-status.json`, `ACTIVATION_CRITERIA.md`, and next-wave planning now all point to Codex/Claude for the parent task |
| vectorbt remains materialized, not implemented | Follow-up | Adapter code, smoke execution, and governance evidence are explicitly still future work; this is consistent with parent scope |

## 5. Recommended Reviewer Disposition

For the parent task `OSS-NEXT-005`, the evidence matches the current `review_approved` state already recorded in `ai-status.json`:

1. the baseline docs exist
2. the package pin exists
3. the maturity matrix reflects the new status
4. the checklist artifact gap called out by the reviewer has been fixed
5. the remaining unimplemented work is explicitly framed as follow-on implementation, not hidden scope

This sidecar packet does not recommend any further canonical edits. It exists to preserve the exact reason the task briefly returned to review, and to make any future re-check of the approved baseline faster.

## 6. Handoff Briefing for Codex

Please verify that this packet accurately summarizes the current vectorbt materialization evidence and the parent review thread.

If accurate, approve `OSS-NEXT-005-SIDECAR-REVIEW` and keep the packet as support-only review context for the already-approved parent task.

Suggested approval command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/OSS-NEXT-005/OSS-NEXT-005-SIDECAR-REVIEW.md \
  REVIEW_NOTES_ZH="審查通過||review packet 已核對；vectorbt baseline 證據齊全；checklist 缺口已補上；後續 adapter/smoke/governance evidence 仍屬 follow-on scope" \
  bash scripts/ai-status.sh approve OSS-NEXT-005-SIDECAR-REVIEW \
  "Review packet verified: vectorbt materialization evidence matches the parent review-approved state, and the checklist gap called out in review is closed."
```

After sidecar approval, no further sidecar work is required unless the parent owner or reviewer requests a new packet revision.

---

This packet is intentionally limited to support material and reviewer handoff. Parent-task absorption into the main execution lane remains the parent owner's decision.
