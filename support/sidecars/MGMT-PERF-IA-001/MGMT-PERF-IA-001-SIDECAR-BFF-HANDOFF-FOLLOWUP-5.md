# MGMT-PERF-IA-001 Sidecar BFF / Frontend Handoff Follow-Up 5

Date: 2026-07-11
Owner: Codex2
Reviewer: Claude
Parent task: `MGMT-PERF-IA-001`
Helper kind: `bff_handoff_packet`
Scope: support-only delivery-state refresh. This packet changes no canonical
truth, BFF contract/runtime, frontend implementation, route registry, schema,
or governance behavior. The parent owner decides what to absorb.

## Purpose

Record the parent's latest reconciliation attempt without prematurely calling
the frontend delivery complete. Follow-up 4 remains the BFF absorption basis;
this packet updates only the transient parent/PR state and the final merge gate.

## State Observed

| Surface | Observed state on 2026-07-11 | Handoff consequence |
|---|---|---|
| `MGMT-PERF-IA-001` | `in_progress`, owned by Claude. Its checkpoint records a second rebase onto current execute-plans `dev`, conflict resolution, local verification, and pushed commit `92ca295`. | The parent has implemented its reconciliation, but remains responsible for the running integration gate, merge, merge-SHA evidence, and formal closeout. |
| execute-plans PR #250 | `OPEN`, merge state `UNSTABLE`; `integration-gate` run `29159974506` is in progress; no merge commit exists. | This is pending validation, not delivered state. Do not close the parent unless the required check passes and the PR actually merges to `dev`. |
| Promotion & Allocation composition | The parent checkpoint preserves the live `PPL-ALLOC-006` page for its `emergency-actions` tab through `PromotionAllocationLegacyGate`; other tabs and the bare legacy route follow the chosen canonical redirects. | Final review should ensure the exception stays narrow and does not recreate two canonical owners for ordinary Promotion & Allocation navigation. |
| `MGMT-PERF-IA-002` | Archived `done`; its Pantheon delivery records merge commit `cec3627bbaa6b565c9d27211783d570375671dca`. | Center links may consume its typed query/source-confidence envelope, but the route-manifest task must not claim ownership of that BFF implementation. |

## Final Parent Merge Gate

The parent can close only after all of these facts are true:

1. integration run `29159974506`, or a newer superseding required run, finishes
   successfully;
2. PR #250 actually merges into execute-plans `dev`;
3. the parent evidence records the resulting merge commit SHA rather than the
   task-branch commit alone;
4. the merged manifest still assigns each normal sidebar, command-palette,
   breadcrumb, center, tab, and compatibility alias to exactly one owner;
5. the `emergency-actions` compatibility gate is limited to the live workbench
   capability that lacks a canonical-center replacement and does not intercept
   the bare route or other tabs;
6. redirect tests still prove allowlisted context preservation, history
   replacement, refresh stability, and loop prevention; and
7. the parent owner performs the reviewed-state closeout. This sidecar cannot
   merge or finalize the parent.

If the running gate fails, the failure must be assessed from its current logs;
earlier conflict and stale-test diagnoses are evidence of prior attempts, not a
reason to waive the current required check.

## BFF / Frontend Boundary Retained

- Frontend navigation may pass the documented typed identity, period/quarter,
  and `asOf` context supported by the merged read model.
- Performance, ranking, recommendation, review, decision, and apply receipt
  identities remain distinct; redirect code must not synthesize lifecycle
  truth.
- `meta.surfaces` and partial, fallback, degraded, or unavailable states must
  remain visible and must not be rendered as a fresh zero result.
- Performance tabs may compose several existing BFF reads, but neither the
  manifest nor redirects may imply an atomic snapshot that the BFF does not
  provide.
- No browser-side shadow ranking, source-confidence calculation, governance
  decision, or capital mutation is introduced by this handoff.

## Reviewer Checklist

Claude should verify that this packet:

- changes only the designated support artifact;
- records PR #250 as open with a running check and no merge commit;
- distinguishes task-branch commit `92ca295` from final merge evidence;
- accurately preserves the narrow `emergency-actions` live-workbench exception;
- keeps BFF read-model ownership outside the route-manifest parent; and
- makes no canonical, runtime, schema, registry, governance, or frontend edit.

Recommended review handoff:

```bash
AI_NAME=Claude \
REVIEW_FILE=support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md \
REVIEW_NOTES_ZH="Follow-up 5 approved: it accurately records the reconciled parent branch, running PR gate, narrow emergency-actions exception, and final merge boundary." \
./scripts/ai-status.sh approve MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Support-only final PR-gate refresh approved for parent-owner composition."
```

## Validation

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-002
gh pr view 250 --repo ajoe734/execute-plans \
  --json state,mergeStateStatus,statusCheckRollup,mergedAt,mergeCommit
git diff --check -- \
  support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
```

No runtime test is required because this follow-up adds only a support packet.
