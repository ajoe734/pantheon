# Review: SVC-BFF-HA-LB-DEFER-TRACKING

**Reviewer:** Claude
**Date:** 2026-05-04
**Task:** Record BFF HA/LB as explicit deferred topology item
**Owner:** Codex
**Outcome:** Approved

## Artifacts Reviewed

- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`
- `TARGET_ARCHITECTURE.md`
- `docker-compose.control.yml`
- `docs/deployment/staging-live-topology.md`

## Acceptance Criteria Verdict

| Criterion | Status | Evidence |
|---|---|---|
| documents state BFF HA is intentionally deferred | PASS | `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` Section 0; `docs/deployment/staging-live-topology.md` "BFF HA/LB Boundary" section |
| compose remains single BFF instance for current staging baseline | PASS | `docker-compose.control.yml` `operator_bff_instances: 1`; operator-bff service comment explicitly states single-instance intent |
| no LB or replica implementation is added | PASS | `bff_load_balancer: none`; `bff_ha_lb_topology: deferred`; no `deploy.replicas`, no second BFF service, no LB tier in compose |
| decision explains low operator concurrency and future trigger conditions | PASS | Section 0 rationale: low concurrent human usage; re-entry gate enumerates: operator concurrency, availability SLOs, external customer access, audit requirements, emergency-control latency |

## Review Notes

**BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md Section 0** is the primary record. It:
- declares the deferral explicitly as a canonical L1 policy statement
- explains the rationale (low operator concurrency, not worth the implementation cost)
- defines the re-entry conditions so future agents know exactly when to un-defer

**docker-compose.control.yml** machine-readable contract block (`x-pantheon-compose-contract`) surfaces
`bff_ha_lb_topology: deferred`, `operator_bff_instances: 1`, `bff_load_balancer: none`. These are
parseable by validation scripts and prevent silent drift.

**docs/deployment/staging-live-topology.md** "BFF HA/LB Boundary" section clearly disambiguates the
dual-VM topology (control/exec plane separation) from BFF HA, which is the main confusion the task
was designed to prevent.

**TARGET_ARCHITECTURE.md** lines 178–179 and 188–189 register the deferral at the north-star level
and point back to `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` as the canonical gate authority.

No implementation was added — confirmed by absence of `deploy.replicas`, second BFF service, or
load-balancer tier in any compose file covered by this task.

## Summary

All acceptance criteria are met. The work is narrow, policy-correct, and appropriately references
the canonical L1 authority. No changes requested.
