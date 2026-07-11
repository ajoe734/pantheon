# MGMT-PERF-IA-001 Sidecar BFF / Frontend Handoff Follow-Up 4

Date: 2026-07-11
Owner: Codex2
Reviewer: Claude
Parent task: `MGMT-PERF-IA-001`
Helper kind: `bff_handoff_packet`
Scope: support-only state and absorption refresh. This packet changes no
canonical truth, BFF contract/runtime, frontend implementation, route registry,
schema, or governance behavior. The parent owner decides what to absorb.

## Purpose

Refresh the handoff after the BFF read-model task completed and the parent was
re-dispatched. This packet supersedes only the transient state observations in
follow-up 3; it does not replace the original route map, operator journey, or
negative boundaries.

## State Observed

| Surface | Observed state | Handoff consequence |
|---|---|---|
| `MGMT-PERF-IA-001` | `in_progress`, owned by Claude; prior review notes remain attached. | The parent must reconcile and revalidate its frontend branch before requesting a new review/closeout. |
| execute-plans PR #250 | `OPEN`, merge state `DIRTY`; integration gate run `29158597228` failed; no merge commit. | The parent is not delivered. A current-`dev` reconciliation and passing gate are required before merge. |
| `PPL-ALLOC-006` | Its Pantheon coordination work has advanced and the formerly concurrent frontend surface is now part of the current integration baseline. | Do not reuse follow-up 3's Human/Ops-precedence blocker verbatim. Resolve the concrete branch conflict against current execute-plans `dev`, preserving the live Promotion & Allocation owner rather than redirecting it away accidentally. |
| `MGMT-PERF-IA-002` | Archived `done`; Pantheon delivery records merge commit `cec3627bbaa6b565c9d27211783d570375671dca`. | Shared query/source-confidence work is no longer a future dependency. The parent and center pages should consume the merged contract, without claiming that the route manifest implemented it. |

## BFF Query Envelope Now Available

The merged read model supports common typed filters across Performance
Attribution, Persona League Rankings, Quarterly Ranking, and quarterly
recommendations: persona, runtime, strategy, capital pool, sleeve, artifact,
broker, stage, period, and `asOf` (with quarter where applicable). Responses
carry explicit surface status, observed time, freshness, coverage, and missing
binding metadata.

Frontend absorption remains fail-closed:

- send documented typed keys; do not forward opaque legacy query strings;
- retain `meta.surfaces` and distinguish partial, fallback, degraded, and
  unavailable evidence from a fresh zero result;
- preserve ranking evidence, recommendation, Human Review, and apply receipt
  as separate lifecycle identities; and
- do not imply atomicity when Performance tabs fan out to portfolio,
  attribution, exposure, holdings, or positions reads.

The parent owns navigation and redirect composition only. It must not claim
ownership of BFF filtering, source confidence, ranking calculation, governance
decisions, or live capital mutation.

## Parent Reconciliation Gate

Before the parent can close:

1. update/rebase PR #250 onto current execute-plans `dev` and resolve the
   `PromotionAllocation.tsx` ownership conflict without converting a live
   workbench into a legacy redirect;
2. confirm each sidebar, command-palette, breadcrumb, center, and compatibility
   alias has exactly one typed manifest owner;
3. rerun redirect tests for allowlisted query preservation, history replace,
   refresh, and loop prevention;
4. verify center links use the merged BFF vocabulary and preserve source-state
   rendering rather than constructing client-owned truth;
5. obtain passing required checks and merge PR #250 to `dev`;
6. record the actual merge SHA in parent evidence; and
7. have the parent owner perform formal closeout. This sidecar cannot finalize
   the parent.

## Reviewer Checklist

Claude should verify that this packet:

- changes only the designated support artifact;
- accurately records the parent as `in_progress` and PR #250 as open, dirty,
  failed, and unmerged;
- recognizes `MGMT-PERF-IA-002` as completed rather than a remaining gap;
- converts the earlier abstract precedence blocker into a concrete current-dev
  reconciliation gate;
- preserves the original operator journey and fail-closed boundaries; and
- makes no canonical, runtime, schema, registry, governance, or frontend edit.

Recommended review handoff:

```bash
AI_NAME=Claude \
REVIEW_FILE=support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md \
REVIEW_NOTES_ZH="Follow-up 4 approved: current parent/PR state, completed BFF read-model absorption, and current-dev reconciliation gate are accurate and support-only." \
./scripts/ai-status.sh approve MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Support-only BFF/frontend absorption refresh approved for parent-owner composition."
```

## Validation

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-002
gh pr view 250 --repo ajoe734/execute-plans \
  --json state,mergeStateStatus,statusCheckRollup,mergedAt,mergeCommit
git diff --check -- \
  support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md
```

No runtime test is required because this follow-up adds only a support packet.
