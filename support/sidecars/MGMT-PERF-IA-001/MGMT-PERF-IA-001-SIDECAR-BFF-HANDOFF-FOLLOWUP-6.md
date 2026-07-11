# MGMT-PERF-IA-001 Sidecar BFF / Frontend Handoff Follow-Up 6

Date: 2026-07-11
Owner: Codex2
Reviewer: Claude
Parent task: `MGMT-PERF-IA-001`
Helper kind: `bff_handoff_packet`
Scope: support-only delivery-state refresh. This packet changes no canonical
truth, BFF contract/runtime, frontend implementation, route registry, schema,
or governance behavior. The parent owner decides what to absorb.

## Purpose

Replace follow-up 5's transient running-check observation with the completed
integration result and the remaining human merge gate. Follow-up 4 remains the
BFF absorption basis; this packet does not redefine its query envelope or the
original operator journey.

## State Observed

| Surface | Observed state on 2026-07-11 | Handoff consequence |
|---|---|---|
| `MGMT-PERF-IA-001` | `review`, owned by Claude. Its checkpoint records the reconciled execute-plans branch at `92ca295`, successful focused verification, and a failed self-merge attempt due to the auto-mode classifier. | Implementation and reviewer gating are distinct from delivery. The parent remains open until an authorized human merges PR #250 and the owner records merge evidence and closes it. |
| execute-plans PR #250 | `OPEN`, `CLEAN`, and `MERGEABLE`; required `integration-gate` run `29159974506` completed `SUCCESS` at `2026-07-11T16:49:25Z`. `mergeCommit` and `mergedAt` are still null. | No code repair is requested by this packet. The concrete blocker is the authorized human merge, not CI, BFF readiness, or frontend hosting. |
| Promotion & Allocation composition | Branch `92ca295` retains the live `PPL-ALLOC-006` page only for the `emergency-actions` tab through `PromotionAllocationLegacyGate`; ordinary tabs and the bare legacy route follow the selected manifest redirects. | Merge review must keep this exception narrow so it does not recreate a second canonical navigation owner. |
| `MGMT-PERF-IA-002` | Archived `done`; its Pantheon delivery records merge commit `cec3627bbaa6b565c9d27211783d570375671dca`. | The route manifest may consume its typed query/source-confidence envelope, but does not own or reimplement that BFF read model. |

## Remaining Parent Closeout Gate

The parent owner should not rerun or rewrite already-green work solely to
produce another sidecar refresh. Completion now requires:

1. an authorized human merges execute-plans PR #250 into `dev`;
2. the parent records the actual merge commit SHA, not only head commit
   `92ca2951b008bef35cde389bd5f6e65d7e80e333`;
3. the merged result retains the one-owner manifest and the narrow
   `emergency-actions` compatibility exception;
4. the required integration result remains associated with the merged head, or
   any superseding required run passes after a head change; and
5. Claude performs the normal reviewed-state finalization for the parent.

If PR #250's head changes before merge, `CLEAN`, `MERGEABLE`, and the successful
run recorded here are historical evidence only; the new head must satisfy the
repository's required checks.

## BFF / Frontend Boundary Retained

- Frontend navigation may pass only the documented typed identity,
  period/quarter, and `asOf` context supported by the merged read model.
- Performance, ranking, recommendation, review, decision, and apply receipt
  identities remain distinct; redirects must not synthesize lifecycle truth.
- `meta.surfaces` and partial, fallback, degraded, or unavailable states remain
  visible and must not be rendered as a fresh zero result.
- Tabs may compose several BFF reads, but the manifest must not imply an atomic
  snapshot that the BFF does not provide.
- This handoff introduces no browser-side shadow ranking, source-confidence
  calculation, governance decision, or capital mutation.

## Reviewer Checklist

Claude should verify that this packet:

- changes only the designated support artifact;
- records PR #250 as open, clean, mergeable, and green without calling it
  merged;
- identifies authorized human merge as the remaining delivery blocker;
- distinguishes head commit `92ca295` from the required merge SHA;
- retains the narrow `emergency-actions` exception and BFF ownership boundary;
  and
- makes no canonical, runtime, schema, registry, governance, or frontend edit.

Recommended review handoff:

```bash
AI_NAME=Claude \
REVIEW_FILE=support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md \
REVIEW_NOTES_ZH="Follow-up 6 approved: it accurately records the green, clean, mergeable but still-open PR, human merge gate, and retained BFF/frontend ownership boundary." \
./scripts/ai-status.sh approve MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Support-only green-PR human-merge handoff approved for parent-owner composition."
```

## Validation

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-002
gh pr view 250 --repo ajoe734/execute-plans \
  --json state,mergeStateStatus,mergeable,statusCheckRollup,mergedAt,mergeCommit,headRefOid
git diff --check -- \
  support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
```

No runtime test is required because this follow-up adds only a support packet.
