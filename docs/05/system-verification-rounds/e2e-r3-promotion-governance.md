# E2E-R3 — Governance promotion-gate integrity (paper → canary → live)

**Round:** E2E-R3 of the e2e business-flow verification campaign
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r3-promotion-gates
**Business flow:** deployment-plan → approval decision → stage promotion
(paper → canary → live), each transition gated by an authorization/audit record.

## Plan

1. Enumerate deployment-plans and their promotion/approval fields.
2. Assert the governance safety invariant: any plan that is `approved` (or
   promoted to canary/live) must reference a **resolvable** approval decision.
3. Ship the check as a CI-gated verifier; document; flag data gaps.

## Verification program

`scripts/verify_e2e_promotion_governance.py` (+ unit test), wired into
`run-acceptance.sh` full mode as `e2e-promotion-governance-verifier`. FAILs on a
**phantom approval** — a plan that claims approval against a non-existent
approval decision — and on approved/promoted plans with no approval reference.

## Live result (dev, 2026-06-15)

```
governance integrity over 15 deployment plans (15 need approval):
  phantom approvals: 15 / 15
  e.g. plan-rescue-0260531-1715d8d2: status=approved stage=paper
       approval=approval-rescue-0260531-1715d8d2 -> 404
```

## Finding

**Every deployment-plan is `status: approved` referencing an approval decision
that does not exist.** `/bff/approvals` lists 0 records and each plan's
`approval_decision_id` / `approval_ref.href` resolves to 404. So all 15 plans
assert an authorization that **cannot be audited** — a governance/authorization
integrity violation. (All plans are `stage: paper`, `current_stage: none`,
`transition_type: activate`; none has actually been promoted to canary/live, so
the immediate blast radius is limited, but the approval-provenance is broken.)

**Root cause:** same rescue-placeholder origin as E2E-R1/R2 — the plans were
created with placeholder `approval-rescue-*` ids that were never persisted to the
approvals store. Not a BFF code bug (the approvals detail endpoint correctly
404s); a governance data-provenance gap.

## Disposition

- **Shipped (code/CI):** the governance integrity verifier + logic test + CI gate
  — phantom approvals are now caught (currently FAILs against dev, reporting the
  real gap).
- **Flagged (upstream build, not faked):** materialize the approval-decision
  records for approved plans (or stop marking rescue plans `approved`). Creating
  placeholder approval rows to silence the gate would forge an audit trail — the
  exact thing this check exists to prevent — so it was deliberately not done.

## Next round

E2E-R4: evolution loop (telemetry/incident → evolution decision → new artifact),
or deepen R3 by adding canary-gate / two-man-sign / PromotionReadinessPacket
invariants for promoted plans.
