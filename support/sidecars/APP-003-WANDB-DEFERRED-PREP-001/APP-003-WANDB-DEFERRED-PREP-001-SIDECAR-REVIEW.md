# APP-003-WANDB-DEFERRED-PREP-001 Review Packet

**Sidecar kind:** `review_packet`
**Sidecar task:** `APP-003-WANDB-DEFERRED-PREP-001-SIDECAR-REVIEW`
**Helper parent:** `APP-003-WANDB-DEFERRED-PREP-001`
**Parent owner:** `Codex`
**Parent reviewer:** `Codex2`
**Prepared by:** `Codex`
**Intended reviewer:** `Codex2`
**Date:** `2026-04-25`
**Status:** `ready_for_handoff`

> Scope constraint: support artifact only. This packet does not modify L1
> canonical truth, runtime behavior, registry semantics, or governance
> implementation. It gives `Codex2` a compact review surface for the already
> closed W&B deferred-prep parent and the companion acceptance evidence.
>
> Worker note: the earlier orchestrator restart for this sidecar was preempted
> before any review artifact was produced. This file is the fresh Codex packet
> for the same support-only scope.

## 1. Review Target

Confirm that the W&B deferred-prep parent was closed truthfully as repo-local
scaffold work only, and that the support materials do not drift into W&B
activation, canonical maturity promotion, or default-backend claims.

This sidecar should help `Codex2` verify four things quickly:

1. the parent task is actually closed and archived as `done`
2. the review-time evidence remains reproducible in the current repo
3. the selector and smoke surfaces still enforce a non-default, offline-only
   W&B prep path
4. all reviewer-facing wording still preserves `W&B = criteria-defined` and
   `activation boundary = does_not_activate_wandb_backend`

## 2. Parent Task Status Snapshot

`python3 scripts/ai_status.py show APP-003-WANDB-DEFERRED-PREP-001` now
resolves to the archive snapshot rather than a live task row.

| Field | Value |
|---|---|
| Parent task | `APP-003-WANDB-DEFERRED-PREP-001` |
| Terminal status | `done` |
| Terminal outcome | `completed` |
| Archived at | `2026-04-25T06:17:21Z` |
| Owner | `Codex` |
| Reviewer | `Codex2` |
| Scope boundary | `prep_only` |
| Activation boundary | `does_not_activate_wandb_backend` |
| Canonical status expected | `criteria-defined` |
| Delivery commit | `6097ce8a021902e83d60c3aabf94b32e9cf04a56` |

Parent closeout summary from the archive snapshot:

- `mlflow` remains the default experiment backend and `wandb` stays
  feature-flagged
- the deferred-prep W&B path remains offline-only and does not claim SDK or
  network readiness
- `promoted_metadata` and `artifact_handoff.json` key shape stay aligned with
  the MLflow path while backend-specific refs/tags switch to `wandb`
- canonical docs still keep W&B at `criteria-defined` and blocked on the
  standing reopen gate

## 3. Evidence Anchors

| Evidence | Path | Why it matters |
|---|---|---|
| Parent archive snapshot | `ai-task-archive/tasks/APP-003-WANDB-DEFERRED-PREP-001.json` | Durable terminal truth for status, review notes, and final handoffs |
| Parent review writeup | `docs/reviews/2026-04-25-app-003-wandb-deferred-prep-001-codex2-review.md` | Records the reviewer-approved basis and the exact verification surface |
| Parent owner handoff | `docs/reviews/2026-04-25-app-003-wandb-deferred-prep-001-codex-handoff.md` | Captures intended implementation scope and reviewer focus |
| Deferred-prep execution packet | `docs/reviews/2026-04-25-deferred-prep-execution-packet.md` | Canonical execution boundary for this wave: prep-only scaffold, no activation claim |
| Acceptance sidecar | `support/sidecars/APP-003-WANDB-DEFERRED-PREP-001/APP-003-WANDB-DEFERRED-PREP-001-SIDECAR-ACCEPTANCE.md` | Companion support packet with acceptance mapping and dependency chain |
| W&B gate doc | `services/registry/experiments/WANDB_ACTIVATION.md` | Preserves the six re-entry conditions and the deferred boundary |
| W&B lane README | `services/registry/experiments/README.md` | Repo-local summary of the landed prep-only surfaces |
| Deferred activation map | `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | Consolidated truth that scaffold landed but reopen blockers remain |
| Canonical maturity docs | `OSS_INTEGRATION_CHECKLIST.md`, `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Keep W&B explicitly at `criteria-defined` after scaffold landing |

Task-brief note on the phase7 planning session:

- `docs/02-architecture/consensus/sessions/phase7-2026-04-18-ep4-ep5-execution-proof/planning-session.json`
  was checked for a direct `APP-003-WANDB-DEFERRED-PREP-001` or `W&B` task
  anchor and no task-specific entry was found
- the operative scope boundary for this sidecar is therefore the explicit
  `2026-04-25` deferred-prep execution packet and the archived parent record

## 4. Fresh Verification Re-Run

These commands were re-run during this sidecar creation against the current repo
state on `2026-04-25` UTC.

| Command | Result | What it proves |
|---|---|---|
| `python3 -m pytest services/registry/experiments/test_adapter.py -q` | `7 passed in 0.19s` | Adapter coverage still passes, including W&B prep metadata-shape parity and selector gate tests |
| `python3 services/registry/experiments/smoke_test.py` | pass | Default smoke path still succeeds on the MLflow-style in-memory backend |
| `python3 services/registry/experiments/smoke_test.py --backend wandb` | pass | Offline W&B prep smoke still maps registry metadata into experiment metadata |
| `python3 -c "from config import selected_backend; print(selected_backend())"` | `mlflow` | Default selector still resolves to `mlflow` |
| `EXPERIMENT_BACKEND=wandb python3 -c "from config import selected_backend; print(selected_backend())"` | raises `EnvironmentError` | `wandb` is still rejected unless the deferred-prep flag is set |
| `EXPERIMENT_BACKEND=wandb PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1 PANTHEON_WANDB_MODE=dryrun python3 -c "from config import selected_backend, selected_wandb_mode; print(selected_backend(), selected_wandb_mode())"` | `wandb dryrun` | The opt-in W&B prep path is still accepted only behind the explicit flag and offline-only mode |

Boundary-critical facts reinforced by the rerun:

- the positive W&B path still uses offline-only modes (`offline`, `dryrun`)
- the gate test remains encoded in `test_selected_backend_rejects_wandb_without_feature_flag`
- metadata-shape parity remains encoded in
  `test_wandb_prep_backend_preserves_promoted_metadata_shape`

### 2026-04-27 Revalidation

Codex revalidated the packet surface again on `2026-04-27` UTC before
refreshing the reviewer handoff. Results remain unchanged:

| Command | Result |
|---|---|
| `python3 -m pytest services/registry/experiments/test_adapter.py -q` | `7 passed in 0.11s` |
| `python3 services/registry/experiments/smoke_test.py` | pass, `backend=memory` |
| `python3 services/registry/experiments/smoke_test.py --backend wandb` | pass, `backend=wandb` |
| `python3 -c "from config import selected_backend; print(selected_backend())"` from `services/registry/experiments` | `mlflow` |
| `EXPERIMENT_BACKEND=wandb python3 -c "from config import selected_backend; print(selected_backend())"` from `services/registry/experiments` | raises `OSError`; deferred-prep flag is still required |
| `EXPERIMENT_BACKEND=wandb PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1 PANTHEON_WANDB_MODE=dryrun python3 -c "from config import selected_backend, selected_wandb_mode; print(selected_backend(), selected_wandb_mode())"` from `services/registry/experiments` | `wandb dryrun` |

No new activation claim is implied by this refresh; it only confirms the
existing packet remains reviewable as support-only evidence.

## 5. Review Read

The archived parent, the live repo, and the companion sidecar still align on
the points that matter for a support-side review:

1. The landed W&B lane is still prep-only.
   - The execution packet authorizes scaffold work only.
   - The archive snapshot still records `activation_boundary =
     does_not_activate_wandb_backend`.
2. The selector remains explicitly non-default.
   - `selected_backend()` still resolves to `mlflow` by default.
   - `EXPERIMENT_BACKEND=wandb` still fails without
     `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`.
3. The successful path still does not overclaim runtime maturity.
   - The W&B smoke path is offline-only and local.
   - No `wandb` SDK pin, network readiness, or production support claim is
     introduced by the parent or this sidecar.
4. Registry-facing semantics still stay aligned with the MLflow reference path.
   - Fresh pytest coverage still passes for metadata-shape parity.
   - Reviewer approval basis remains consistent with the archived review notes.
5. Canonical maturity wording remains truthful.
   - `OSS_INTEGRATION_CHECKLIST.md` and
     `RESEARCH_BACKEND_MATURITY_MATRIX.md` still keep W&B at
     `criteria-defined`.
   - `WANDB_ACTIVATION.md` and the deferred activation map still preserve the
     remaining reopen blockers.

## 6. Reviewer Checklist for `Codex2`

1. Confirm this packet accurately reflects the archived parent snapshot in
   `ai-task-archive/tasks/APP-003-WANDB-DEFERRED-PREP-001.json`.
2. Confirm the fresh verification rerun is consistent with the approved review
   writeup and does not rely on stale handoff claims.
3. Confirm the sidecar language never upgrades the parent into W&B activation,
   production support, or infrastructure readiness.
4. Confirm the companion acceptance packet and this review packet tell the same
   story: prep-only scaffold landed, review evidence exists, and deferred truth
   remains intact.
5. Reject the packet only if you find a concrete truth mismatch in the archive
   snapshot, the live verification surface, or the boundary wording.

## 7. Recommended Disposition

Move `APP-003-WANDB-DEFERRED-PREP-001-SIDECAR-REVIEW` to
`review_approved` once the reviewer confirms:

- the parent is already truthfully closed as deferred-prep only
- the selector and smoke evidence remain reproducible
- the packet does not alter canonical truth or invent activation claims

After approval, the parent owner may decide whether this support packet should
be absorbed into the main review trail. This sidecar itself should then be
closed as a support-only artifact.

## 8. Sidecar Scope Declaration

- No canonical L1 or L2 document was modified by this slice
- No runtime, registry, or governance implementation was changed by this slice
- No parent task status or archive record was edited manually
- The only artifact created by this sidecar is this review packet
