# EXEC-OSS-WANDB-001 Sidecar Review Packet

Date: `2026-04-21`
Sidecar task: `EXEC-OSS-WANDB-001-SIDECAR-REVIEW`
Parent task: `EXEC-OSS-WANDB-001`
Helper kind: `review_packet`
Sidecar owner / reviewer: `Codex2` / `Codex`
Parent owner / reviewer: `Codex` / `Copilot`
Boundary: support artifact only; no canonical truth, runtime, registry, or governance implementation changes

## Parent Status Snapshot

- `ai-status.json` shows `EXEC-OSS-WANDB-001` is currently `review`.
- Parent acceptance stays narrow:
  - reopen conditions must match current blocker truth
  - the execution slice must not overrun the existing deferral gate
  - the output must leave a reviewable next-step recommendation
- The parent artifacts are:
  - `services/registry/experiments/WANDB_ACTIVATION.md`
  - `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`
  - `docs/reviews/2026-04-20-development-progress-and-next-work-inventory.md`

## What This Sidecar Is For

- This packet does not reopen W&B work and does not authorize implementation.
- It gives the sidecar reviewer a compact evidence crosswalk for the parent claim that the next legitimate W&B step is a reopen packet, not adapter or SDK work.
- The durable truth remains in the parent artifacts above; this document is only a reviewer aid.

## Execution-Slice Truth To Preserve

The parent slice is defensible only if review keeps these boundaries intact:

1. W&B remains formally `DEFER` for the current wave.
2. The repo already has the config-selector stub, so the blocker is no longer "missing toggle."
3. The real blockers are the unmet re-entry conditions plus the still-MLflow-first adapter surface.
4. No W&B adapter-generalization, SDK pin, or smoke-test implementation task should open from this slice alone.
5. The first executable follow-up is a reopen packet after all gate conditions are met.

This is stated directly in `services/registry/experiments/WANDB_ACTIVATION.md §7.5`.

## Evidence Crosswalk

### 1. Gate doc already closes the ambiguity

- `services/registry/experiments/WANDB_ACTIVATION.md §7.5` says `EXEC-OSS-WANDB-001` does not authorize implementation.
- The same section says the reviewer-ready next step is a reopen packet that cites all six re-entry proofs.
- The document status line already says the 2026-04-21 execution-slice truth was refreshed for review handoff.

### 2. Deferred activation map matches the same conclusion

- `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §5` records W&B as still deferred.
- It marks the `EXPERIMENT_BACKEND` selector stub as done, but keeps adapter generalization, canonical-state migration, SDK pin, and infrastructure readiness unresolved.
- It explicitly says `EXEC-OSS-WANDB-001` closes the execution-slice ambiguity by making the reopen packet, not implementation, the first reviewable follow-up.

### 3. Repo-local implementation evidence still shows MLflow-first truth

- `services/registry/experiments/config.py`:
  - defaults `EXPERIMENT_BACKEND` to `"mlflow"`
  - keeps `_SUPPORTED_BACKENDS = ("mlflow",)`
  - raises an error for unsupported backends and points back to `WANDB_ACTIVATION.md`
- `services/registry/experiments/adapter.py`:
  - still declares `PRIMARY_BACKEND = "mlflow"`
  - exposes only MLflow-backed runtime behavior in the current adapter surface
- `services/registry/experiments/README.md` still says W&B remains deferred while Pantheon stabilizes the MLflow-first path.

### 4. Planning and inventory docs stay aligned with the defer truth

- `docs/reviews/2026-04-17-next-wave-implementation-plan.md` says W&B work is not scheduled before `2026-05-15`.
- `docs/reviews/2026-04-20-development-progress-and-next-work-inventory.md` says the current W&B truth is not backend implementation; it is defer closeout plus reopen-packet definition.
- `docs/reviews/2026-04-16-oss-ecosystem-gap-analysis.md` still classifies W&B as `criteria-defined`, not integrated.

## Reopen Gate Snapshot

The parent should remain in review unless its wording stays consistent with this six-part gate:

| Re-entry condition | Current truth on 2026-04-21 |
|---|---|
| MLflow 30-day governed history | Not met; earliest eligible date is `2026-05-15` |
| Operator preference on file | Not met |
| Adapter generalization completed | Not met |
| Canonical `artifact_state` / `deployment_stage` migration completed | Not met |
| `wandb>=0.16.0` compatibility verified | Not met |
| Network / infrastructure readiness confirmed | Not verified |

If any parent wording weakens one of these into a soft suggestion instead of a hard gate, that is the key review failure to call out.

## Reviewer Focus For Codex

- Confirm the parent slice does not accidentally imply W&B implementation is now open.
- Confirm the parent keeps the config stub framed as already-landed context, not as the missing blocker.
- Confirm the earliest reopen date is stated concretely as `2026-05-15`, not as an imprecise "after MLflow stabilizes."
- Confirm the parent recommendation requires all six reopen conditions together, not piecemeal.
- Treat any suggestion to open adapter-generalization or SDK work immediately as scope drift against the approved defer gate.

## Suggested Reviewer Disposition

- Approve this sidecar if it is sufficient as a compact review aid for the active parent review.
- For the parent task, approve only if its final wording preserves the hard defer boundary and makes the reopen packet the only legitimate next execution slice.
- Request changes if the parent blurs the line between "reviewable reopen criteria" and "implementation-ready W&B backend work."

## Sidecar Acceptance Check

- Support artifact created only: yes
- Canonical truth modified: no
- Reviewer handoff ready: yes
- Owner finalized: yes (2026-04-21, Claude)
- Review approved by: Codex
- Terminal outcome: done — defer boundary confirmed, reopen gate documented, no implementation scope opened
