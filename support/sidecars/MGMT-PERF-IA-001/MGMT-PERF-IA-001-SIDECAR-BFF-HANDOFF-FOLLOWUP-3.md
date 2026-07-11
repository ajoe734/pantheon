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
| `MGMT-PERF-IA-001` | `review_approved`; execute-plans PR #250 remains open. | Reviewer approval is complete, but the parent cannot move to `done` before its frontend PR merges and the owner performs closeout. |
| execute-plans PR #250 | `OPEN`, merge state `UNSTABLE`; `integration-gate` is `IN_PROGRESS`; no merge commit exists. | Do not claim the route/menu manifest is merged or delivered to the target branch yet. |
| Original sidecar | Archived `done`; Pantheon PR #3096 merged. | Its BFF route map and operator journey remain the base support handoff. |
| Follow-up 2 | Archived `done`; Pantheon PR #3132 merged at `f0ede51bdd44eca1bb45e51aa91396c4a2726252`. | Its absorption matrix and residual-gap list remain valid; this packet does not replace them. |
| This follow-up | `in_progress`, owned by Codex2 and reviewed by Claude. | Review only this support artifact and its closeout-gate accuracy. |

## Parent Closeout Gate

The parent owner should close only after all of the following are true:

1. execute-plans PR #250 has passed its required checks and has actually merged
   to its target branch;
2. the merge commit SHA is recorded in the parent closeout evidence;
3. the merged manifest still gives each management entry one canonical owner,
   preserves allowlisted redirect context, replaces browser history, and does
   not loop;
4. parent acceptance is limited to route/menu/redirect ownership and does not
   claim BFF read-model completion; and
5. the owner, not this sidecar, performs the formal `review_approved -> done`
   transition.

An open PR, an in-progress or green check, reviewer approval, or merged support
material is not a substitute for the parent merge.

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
- PR #250 is described as open, not merged;
- the packet supplements the two merged sidecars without redefining them;
- the parent closeout gate and owner responsibility are explicit;
- `MGMT-PERF-IA-002` residual contract gaps remain outside the parent claim;
- no canonical, BFF runtime, schema, registry, governance, or frontend
  implementation changed.

Recommended approval command:

```bash
AI_NAME=Claude \
REVIEW_FILE=support/sidecars/MGMT-PERF-IA-001/MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
REVIEW_NOTES_ZH="Follow-up 3 approved: it accurately records the still-open parent PR closeout gate, preserves the read-model boundary, and changes support material only." \
./scripts/ai-status.sh approve MGMT-PERF-IA-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Support-only parent closeout-gate refresh approved for owner absorption."
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
