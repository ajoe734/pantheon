# MGMT-PERF-IA-001 Sidecar BFF / Frontend Handoff Follow-Up 3

Date: 2026-07-11
Owner: Codex2
Reviewer: Claude
Parent task: `MGMT-PERF-IA-001`
Helper kind: `bff_handoff_packet`
Scope: support-only closeout-gate refresh. This packet changes no canonical
truth, BFF contract/runtime, frontend implementation, route registry, schema,
or governance behavior. The parent owner decides what to absorb.

## Purpose

The original handoff and follow-up 2 are merged. This third packet records the
remaining parent closeout gate without reopening their design conclusions or
misstating the sibling read-model work as complete.

## Observed State

| Surface | State observed on 2026-07-11 | Required interpretation |
|---|---|---|
| `MGMT-PERF-IA-001` | `blocked`, owned by Claude and waiting for `Human/Ops`; execute-plans PR #250 remains open. | Prior reviewer approval does not remove the current blocker. A human/product IA-precedence decision is required before the parent can re-attempt delivery or closeout. |
| execute-plans PR #250 | `OPEN`, merge state `UNSTABLE`; `integration-gate` run 29143828567 completed with `FAILURE`; no merge commit exists. | This is not a pending-CI state. The gate exposes a genuine conflict with `PPL-ALLOC-006`, which is concurrently expanding `src/management/pages/oversight/PromotionAllocation.tsx` as a live operator workbench while PR #250 redirects that route as legacy. |
| Original sidecar | Archived `done`; Pantheon PR #3096 merged. | Its BFF route map and operator journey remain the base support handoff. |
| Follow-up 2 | Archived `done`; Pantheon PR #3132 merged at `f0ede51bdd44eca1bb45e51aa91396c4a2726252`. | Its absorption matrix and residual-gap list remain valid; this packet does not replace them. |
| This follow-up | `in_progress`, owned by Codex2 and reviewed by Claude. | Review only this support artifact and its closeout-gate accuracy. |

## Parent Closeout Gate

The parent owner cannot proceed by merely waiting for or rerunning CI. Closeout
requires all of the following, in order:

1. Human/Ops or the responsible product authority decides IA precedence:
   sequence `MGMT-PERF-IA-001` behind `PPL-ALLOC-006`, or amend the Route
   Migration Matrix so the live Promotion & Allocation workbench is not
   simultaneously treated as a legacy redirect;
2. the parent implementation and PR are updated or sequenced according to that
   decision, then PR #250 is re-attempted;
3. execute-plans PR #250 has passed its required checks and has actually merged
   to its target branch;
4. the merge commit SHA is recorded in the parent closeout evidence;
5. the merged manifest still gives each management entry one canonical owner,
   preserves allowlisted redirect context, replaces browser history, and does
   not loop;
6. parent acceptance is limited to route/menu/redirect ownership and does not
   claim BFF read-model completion; and
7. the owner, not this sidecar, performs the formal approved-state-to-`done`
   transition.

An open PR, a rerun request, reviewer approval, or merged support material is
not a substitute for the precedence decision, a passing re-attempt, and the
parent merge. The current failure is a cross-task product/IA conflict, not a
flake or ordinary stale-branch condition.

## BFF / Frontend Absorption Boundary

Safe parent claims remain narrow:

- the typed frontend manifest may map Performance tabs to existing
  portfolio-book, attribution, exposure, holdings, and positions reads;
- rolling persona league and quarterly ranking remain separate datasets under
  a common Rankings center;
- Governance links consume immutable ranking evidence and keep recommendation,
  review, decision, and apply receipt distinct; and
- degraded reads retain the canonical shell while showing BFF-owned source
  state and failing dependent actions closed.

The parent must not claim that navigation work provides an atomic exposure /
holdings / positions snapshot, shared identity and time semantics, supported
filter discovery, cross-center snapshot matching, typed evidence lineage, or a
uniform partial/degraded contract. Those remain `MGMT-PERF-IA-002` or later
center/read-model responsibilities.

## Reviewer Checklist

Claude should verify:

- only this support artifact changed;
- PR #250 is described as open with a failed gate, not pending or merged;
- the `PPL-ALLOC-006` PromotionAllocation conflict and Human/Ops precedence
  decision are explicit;
- the packet supplements the two merged sidecars without redefining them;
- the parent closeout gate and owner responsibility are explicit;
- `MGMT-PERF-IA-002` residual contract gaps remain outside the parent claim;
- no canonical, BFF runtime, schema, registry, governance, or frontend
  implementation changed.

Recommended approval command:

```bash
AI_NAME=Claude \
REVIEW_FILE=support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
REVIEW_NOTES_ZH="Follow-up 3 approved: it accurately records the blocked parent, failed PR gate, required IA-precedence decision, and support-only read-model boundary." \
./scripts/ai-status.sh approve MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Support-only blocked parent and IA-precedence gate refresh approved for owner absorption."
```

## Validation

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF
AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
gh pr view 250 --repo ajoe734/execute-plans \
  --json state,mergeStateStatus,statusCheckRollup,mergedAt,mergeCommit
git diff --check -- \
  support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
```

No runtime tests are required because this follow-up changes only a support
artifact.
