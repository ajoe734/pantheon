# APP-003-TRUTH-SYNC-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-003-TRUTH-SYNC-001` - rebaseline workbench and progress truth against current implementation  
**Parent Owner**: `Codex`  
**Parent Reviewer**: `Claude2`  
**Parent Status**: `done`  
**Sidecar Task**: `APP-003-TRUTH-SYNC-001-SIDECAR-BFF-HANDOFF`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: `2026-04-22`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not change canonical truth, L1
> policy, core contracts, runtime behavior, registry/governance
> implementations, or the parent task's execution record. It packages the
> current `APP-003-TRUTH-SYNC-001` handoff boundary for reviewer use: current
> worktree route truth, task-lifecycle truth in `ai-status.json`, and the
> remaining frontend summary drift that still needs a deliberate sync pass.

## 1. Executive Summary

`APP-003-TRUTH-SYNC-001` was opened to remove stale "pending BFF" claims after
the audited repo had already moved ahead. The current worktree now splits into
three distinct truth clusters:

- Consultation-side worktree truth is already forward-synced to `CW-04`
  route-live. The BFF overview and `CW-008` family packet now say memo routes
  are live and the remaining work is frontend packetization.
- Trainer-side worktree truth has also moved ahead: `TW-02` controls read and
  patch routes are mounted in `services/control-plane/bff/main.py`, dedicated
  contract tests exist, and `APP-003-TW02-IMPL-001` is already in `review`.
- Several high-level frontend/navigation summaries still lag and continue to
  describe `CW-04` and `TW-02` as pending-BFF or blocked shell-only.

For reviewer handoff purposes, the honest current state is:

- `CW-04` is not a missing BFF route family anymore.
- `TW-02` is not an honest "missing route family" description for the current
  worktree, but it is also not owner-finalized truth yet because its parent
  implementation task is still in `review`.
- `CW-04` still has no module-local `FRONTEND_CHANGE_SPEC.md`.
- `TW-02` now has a module-local `FRONTEND_CHANGE_SPEC.md` in the current
  worktree, but it is not yet committed/absorbed into the reviewed truth path,
  so route-live still does not by itself mean "frontend handoff complete."

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable owner/reviewer/lifecycle truth for `APP-003-TRUTH-SYNC-001`, `APP-003-CW04-IMPL-001`, and `APP-003-TW02-IMPL-001` |
| `.orchestrator/task-briefs/app_003_truth_sync_001.md` | Parent scope, current review state, and acceptance framing |
| `.orchestrator/task-briefs/app_003_truth_sync_001_sidecar_bff_handoff.md` | Sidecar scope and artifact target |
| `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` | Execution-origin record that still frames `TW-02` as the remaining implementation gap |
| `services/control-plane/bff/main.py` | Current worktree route truth for `CW-04`, `TW-02`, and the Consultation overview |
| `services/control-plane/bff/test_tw02_parameter_controls_contract.py` | Current worktree proof that `TW-02` read/patch behavior is mounted and regression-covered |
| `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` | Consultation family packet already synced to `CW-04` route-live truth |
| `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md` | Trainer family packet still lags on `TW-02` readiness wording |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | High-level frontend summary still underreports `CW-02`, `CW-04`, and `TW-02` readiness |
| `docs/pantheon-handoffs/LOVABLE_MASTER_SA.md` | Global frontend summary still keeps `CW-04` and `TW-02` in pending-BFF prose |

## 3. Truth-Sync Divergence Map

### 3.1 Consultation surfaces are already forward-synced to `CW-04` live truth

The current worktree already says the following:

- `services/control-plane/bff/main.py` mounts:
  - `GET /api/v1/consult/memos`
  - `GET /api/v1/consult/memos/{memo_id}`
- the same file's Consultation overview payload now says:
  - `CW-01` through `CW-04` are live in the BFF
  - the remaining follow-up is frontend packetization
- `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`
  now marks:
  - `CW-02` route-live
  - `CW-04` route-live; frontend handoff pending

Current consultation-side gap classification:

| Item | Current truth |
|---|---|
| `CW-04` BFF query gap | closed in the current worktree |
| `CW-04` frontend handoff bundle | still missing |
| `CW-04` production UI activation | still gated on packet publication / owner decision |

This means `APP-003-TRUTH-SYNC-001` should not reopen `CW-04` as if memo routes
were still absent.

### 3.2 `TW-02` is worktree-live, but review and packetization are still open

The current worktree now exposes:

- `GET /api/v1/trainer/sessions/{session_id}/controls`
- `POST /api/v1/trainer/sessions/{session_id}/patch`

The current dedicated regression file
`services/control-plane/bff/test_tw02_parameter_controls_contract.py` covers:

- degraded controls read behavior
- accepted patch behavior
- rejected patch behavior
- patch rejection when `allowedActions.canPatchControls = false`
- patch rejection when the trainer session is not `active`

At the task-board level, `ai-status.json` currently says:

- `APP-003-TW02-IMPL-001` is `review`
- its `next` field says the TW-02 routes are live and dedicated tests were added

At the packetization level, the current worktree now contains:

- `docs/pantheon-handoffs/TW-02-parameter-controls/FRONTEND_CHANGE_SPEC.md`

That spec is currently untracked, so the safest support-side reading is:

- present in the worktree
- not yet committed/published in git history
- still not enough by itself to call `TW-02` frontend handoff-complete while
  `APP-003-TW02-IMPL-001` remains in `review`

The safest `TW-02` handoff classification is therefore:

| Layer | Honest status |
|---|---|
| Current worktree route behavior | live |
| Task lifecycle truth | implementation review pending |
| Module-local frontend handoff | present in worktree; not yet committed / parent-absorbed |
| Safe frontend dispatch status | not ready to claim handoff-complete |

### 3.3 High-level frontend summaries still lag current worktree truth

These surfaces still read as if the older gap report were current:

| Surface | Current wording | Safer reviewer interpretation |
|---|---|---|
| `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` | `TW-02` still listed under real implementation gaps | treat as the task's execution-origin record, not as a fresh re-audit of the current worktree |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | `CW-02` blocked, `CW-04` blocked shell-only, `TW-02` contract-published/pending-bff | treat as a lagging navigation summary, not as the authoritative go/no-go source for live route existence |
| `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md` | `TW-02` pending BFF implementation with older diff semantics | treat as a lagging family packet until `TW-02` review closes and a follow-up sync lands |
| `docs/pantheon-handoffs/LOVABLE_MASTER_SA.md` | `CW-04` and `TW-02` still pending BFF | treat as a lagging global overview, not as the current route-truth source |

This sidecar does not change those files. It records that they are no longer
uniform with the current worktree and task-review state.

## 4. Frontend Truth Boundary

When these surfaces disagree, frontend and reviewer decisions should follow
this ordering.

| Topic | Prefer this truth source | Do not key off this alone |
|---|---|---|
| Consultation readiness | `GET /api/v1/workbench/consultation`, `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`, `ai-status.json` | blocked rows in `docs/lovable/PANTHEON_FRONTEND_SA.md` |
| `CW-04` query shape | live memo routes plus `docs/bff/CW-04-redteam-memo.md` and `docs/examples/CW-04-redteam-memo.json` | older pending-BFF summary prose |
| Trainer controls query/mutation shape | live `TW-02` routes in `services/control-plane/bff/main.py`, `docs/bff/TW-02-parameter-controls.md`, and dedicated test coverage | stale trainer-family or master-summary wording |
| Production UI dispatch gate | module-local `FRONTEND_CHANGE_SPEC.md` once published, plus parent-owner approval after review closes | route existence alone |
| Task lifecycle truth | `ai-status.json` status for the parent implementation task | worktree code alone when deciding whether a module is formally closed |

Practical rule:

- use current routes and module contracts to understand payload truth
- use `ai-status.json` to understand whether that implementation is still under
  review
- use module-local frontend handoff bundles to decide whether a production UI
  loop is formally open

## 5. Truthful Operator / Frontend Journey

This is the bounded journey the parent reviewer should preserve while
`APP-003-TRUTH-SYNC-001` closes.

1. Read Consultation module readiness from the backend-owned Consultation
   overview or `CW-008` packet family, not from stale blocked-summary rows.
2. For `CW-04`, query `GET /api/v1/consult/memos` and
   `GET /api/v1/consult/memos/{memo_id}` directly when validating payload
   readiness.
3. For `TW-02`, enter from a live `TW-01` trainer session and validate
   `GET /api/v1/trainer/sessions/{session_id}/controls` plus
   `POST /api/v1/trainer/sessions/{session_id}/patch`.
4. Honor backend-owned authority gates such as
   `allowedActions.canInitiateGovernanceReview` and
   `allowedActions.canPatchControls`; do not infer readiness from stale summary
   tables.
5. If a route exists but the module-local frontend handoff bundle does not,
   classify the surface as route-live / packetization-pending rather than as
   blocked by a missing BFF.
6. If an implementation task is still in `review`, do not call the module fully
   closed even when the worktree already exposes the route family.

## 6. Recommended Parent-Owner Absorption

This sidecar does not ask the parent owner to edit canonical files directly.
It gives a clean absorption rule for the next sync pass:

- do not regress `CW-04` back to pending-BFF wording
- do not keep describing `TW-02` as a missing route family once the reviewer
  accepts the live worktree evidence
- do not treat route-live as equivalent to "frontend handoff bundle published"

If `APP-003-TW02-IMPL-001` is approved, the next bounded sync target should be
the summary surfaces that still lag the worktree:

- `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md`
- `docs/lovable/PANTHEON_FRONTEND_SA.md`
- `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`
- `docs/pantheon-handoffs/LOVABLE_MASTER_SA.md`

If `APP-003-TW02-IMPL-001` is **not** approved, the summary surfaces may keep
their conservative `TW-02` wording, but that should still not be used to
reclassify `CW-04` as a missing route family again.

## 7. Reviewer Checklist

For reviewer / parent-owner consumption:

- confirm the packet stays support-only and does not modify canonical truth
- confirm it distinguishes current worktree truth from final lifecycle truth
- confirm it does not overclaim `TW-02` as owner-finalized simply because the
  routes are mounted
- confirm it records the missing `CW-04` module-local handoff bundle and the
  untracked-in-worktree `TW-02` spec state clearly enough for the parent owner
- confirm it redirects frontend/reviewer attention away from stale high-level
  summaries and toward the correct truth chain

## 8. Sidecar Scope Check

| Check | Result |
|---|---|
| Support artifact only | pass |
| Canonical truth untouched | pass |
| No runtime / registry / governance implementation edits | pass |
| Focus stays on BFF/frontend handoff boundary | pass |
| Parent owner keeps absorption discretion | pass |
