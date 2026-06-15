# E2E-R15 — Deployment-plan lifecycle coherence

**Round:** E2E-R15 (second campaign)
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r15-deployment-lifecycle
**Business flow:** plan approved → activated → runtime binding active at the
target stage → the plan's `current_stage` must reflect the running stage.

## Verification program

`scripts/verify_e2e_deployment_lifecycle.py` (+ unit test). For each plan with an
ACTIVE runtime binding, asserts `current_stage` is not in the not-deployed set
(none/empty) and matches the binding's deployment stage.

## Live result (dev, 2026-06-15)

```
deployment-plan lifecycle coherence over 15 plans:
  plans with an active binding: 16
  incoherent current_stage: 15
FAIL: every plan reports current_stage='none' while its binding is active at 'paper'
```

All 15 plans share the state `stage=paper, current_stage=none, target_stage=paper,
status=approved, transition_type=activate` — and each has an active paper binding.

## Finding

The deployment-plan **`current_stage` lifecycle field never advances**. Every
plan is `approved` + `activate→paper` and HAS an active paper runtime binding,
yet still reports `current_stage='none'` ("not deployed"). The plan's declared
lifecycle state contradicts the running reality: the binding is live at paper,
but the plan says it has never reached any stage. A consumer trusting
`current_stage` would conclude these plans are not deployed.

## Disposition

- **Shipped (code/CI):** the lifecycle-coherence verifier + logic test — catches
  plans whose `current_stage` does not reflect their active binding.
- **Flagged (build):** advance `current_stage` to the binding's stage when a plan
  is activated (the activation path updates the binding but not the plan
  lifecycle field). Confirms the long-standing note that current_stage never
  advances.

## Next round

E2E-R16: auth / capability boundary, then a persist-dedup fix + consolidation.
