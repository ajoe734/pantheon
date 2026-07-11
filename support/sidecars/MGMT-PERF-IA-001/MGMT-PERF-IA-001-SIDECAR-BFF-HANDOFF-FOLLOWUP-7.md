# MGMT-PERF-IA-001 Sidecar BFF / Frontend Handoff Follow-Up 7

Date: 2026-07-11
Owner: Codex2
Reviewer: Claude
Parent task: `MGMT-PERF-IA-001`
Helper kind: `bff_handoff_packet`
Scope: support-only delivery-state refresh. This packet changes no canonical
truth, BFF contract/runtime, frontend implementation, route registry, schema,
or governance behavior. The parent owner decides what to absorb.

## Purpose

Record the parent task's reviewer-approved state without mistaking approval for
delivery. Follow-up 6 remains the BFF/frontend boundary basis; this packet only
updates the lifecycle gate and the current PR evidence.

## State Observed

| Surface | Observed state on 2026-07-11 | Handoff consequence |
|---|---|---|
| `MGMT-PERF-IA-001` | `review_approved`, owned by Claude. Antigravity's review note approves the work while explicitly recording execute-plans PR #250 as awaiting merge. | The reviewer gate has passed. Only the parent owner may now complete merge evidence and formal closeout; this sidecar must not finalize the parent. |
| execute-plans PR #250 | `OPEN`, `CLEAN`, and `MERGEABLE` at head `92ca2951b008bef35cde389bd5f6e65d7e80e333`; `integration-gate` run `29159974506` completed `SUCCESS`. `mergedAt` and `mergeCommit` remain null. | Approval is not merge evidence. The parent cannot truthfully claim its acceptance criterion or move to `done` until the PR is merged and the actual merge SHA is recorded. |
| BFF read model | `MGMT-PERF-IA-002` is archived `done` with Pantheon merge commit `cec3627bbaa6b565c9d27211783d570375671dca`. | No BFF repair is requested. Frontend composition may consume the delivered typed query and source-confidence envelope without duplicating its authority. |
| Compatibility boundary | The reviewed frontend head retains the narrow `emergency-actions` compatibility exception described by follow-up 6. | Parent closeout should verify the merged result preserves this exception without restoring a second canonical navigation owner. |

## Parent Owner Closeout Handoff

Claude should complete the parent through its own repository workflow:

1. merge execute-plans PR #250 into `dev` through an authorized path;
2. record the resulting merge commit SHA, distinct from head SHA `92ca295`;
3. confirm the merged ancestry contains the reviewed head and the successful
   required check still applies, rerunning checks if the head changes;
4. retain the one-owner route manifest, typed query preservation, and narrow
   `emergency-actions` compatibility exception; and
5. perform the parent task's owner-only `review_approved -> done` closeout.

If PR #250 changes or ceases to be clean before merge, the evidence in this
packet becomes historical only. Revalidation belongs to the parent lane, not
this support sidecar.

## Fail-Closed BFF / Frontend Boundary

- Redirects may preserve documented identity, period/quarter, and `asOf`
  context, but must not synthesize performance or governance facts.
- Performance, ranking, recommendation, review, decision, and apply-receipt
  identities remain separate lifecycle records.
- `meta.surfaces` and partial, fallback, degraded, or unavailable source states
  remain operator-visible; missing data must not become a fresh zero.
- Multi-read tabs must not imply an atomic snapshot unless the BFF supplies one.
- The browser must not calculate shadow rankings, source confidence, governance
  decisions, or capital mutations.

## Reviewer Checklist

Claude should verify that this packet:

- changes only the designated support artifact;
- accurately distinguishes `review_approved` from merged and `done`;
- records PR #250 as open, clean, mergeable, and green without claiming a merge;
- leaves merge and parent closeout with the parent owner;
- preserves the BFF/frontend ownership and degradation boundaries; and
- introduces no canonical, runtime, schema, registry, governance, or frontend
  change.

Recommended review handoff:

```bash
AI_NAME=Claude \
REVIEW_FILE=support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md \
REVIEW_NOTES_ZH="Follow-up 7 approved: it accurately distinguishes reviewer approval from the still-open frontend PR and routes merge evidence and closeout to the parent owner." \
./scripts/ai-status.sh approve MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7 \
  "Support-only reviewer-approved/open-PR handoff approved for parent-owner composition."
```

## Validation

```bash
AI_NAME=Codex2 python3 scripts/ai_status.py show MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7
AI_NAME=Codex2 python3 scripts/ai_status.py show MGMT-PERF-IA-001
AI_NAME=Codex2 python3 scripts/ai_status.py show MGMT-PERF-IA-002
gh pr view 250 --repo ajoe734/execute-plans \
  --json state,mergeStateStatus,mergeable,statusCheckRollup,mergedAt,mergeCommit,headRefOid
git diff --check -- \
  support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md
```

No runtime test is required because this follow-up adds only a support packet.
