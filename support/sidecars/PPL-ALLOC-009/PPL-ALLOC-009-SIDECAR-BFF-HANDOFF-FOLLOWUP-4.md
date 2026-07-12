# PPL-ALLOC-009 BFF/Frontend Closeout Gap Handoff

- **Sidecar Task**: `PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`
- **Parent Task**: `PPL-ALLOC-009`
- **Owner / Reviewer**: `Codex` / `Antigravity`
- **Helper Kind**: `bff_handoff_packet`
- **Scope**: support-only; no canonical, runtime, registry, governance,
  frontend, deployment, or capital-state mutation
- **Date**: 2026-07-12

## Handoff Intent

This follow-up converts the earlier query manifests and stop/go checklist into
an owner-assigned gap register for the parent closeout. It does not claim that
the current BFF code is deployed, that the frontend consumes every route, or
that any capital command succeeded. The parent owner must replace every
`unproven` verdict with evidence from the recorded deployment or keep the
parent blocked.

## Closeout Gap Register

| Gap | Repository-level surface available | Required closeout evidence | Owner to resolve | Recheck condition |
| --- | --- | --- | --- | --- |
| Paper identity and isolation | `POST /bff/management/personas/create-paper-bundle` plus Fleet/Capital projections | One hosted creation response joined to authoritative reads with the same persona, paper ledger, runtime binding, `paper_running`, and no real-capital side effect | Parent backend/BFF smoke owner | Recorded deployed Pantheon SHA; response-derived IDs agree across all reads |
| Promotion review linkage | Quarterly recommendation submit and promotion-review get/decision routes | Recommendation, review, decision, target stage, evidence, actor, correlation, approval, and audit references form one deterministic chain | Parent BFF smoke owner | No label/time-based join; sanitized request/response artifacts retained |
| Allocation universe completeness | Allocation-policy evaluation and rebalance proposal/detail routes | Ranking snapshot retains eligible and excluded rows, cap reasons, target lines with `applied: false`, simulation, constraints, and rollback target | Parent allocation acceptance owner | Totals and exclusions reconcile against one named snapshot |
| Apply execution truth | Rebalance apply route returns command admission | No-approval increase fails; approved command reaches terminal success; authoritative Fleet/Capital/binding readback matches the intended identities and weights | Parent command/runtime owner | Command-status route and post-command reads are captured; `202` alone is not a pass |
| Emergency safety boundary | Emergency containment validation and governed command path | Reduce/freeze/suspend is admitted for a safe dev fixture; promotion and allocation-increase variants fail closed; refreshed reads show no unsafe side effect | Parent risk/containment owner | Positive containment plus both negative probes run against the same deployment |
| Hosted operator truth | Separate `execute-plans` frontend is the intended operator surface | Exact frontend/BFF deployed SHAs, authenticated desktop and mobile traces, live BFF mode, strict fallback, and no required-request mock/fixture substitution | Parent frontend/dev-host owner | Both viewports complete; failures remain visible; deployed bundle identity recorded |

All rows begin as `unproven` for this support packet. Code existence and local
contract tests can establish readiness to probe, but cannot change those
verdicts to `pass`.

## Minimum Evidence Bundle

The parent archive should contain:

1. A deployment manifest naming Pantheon PR/merge SHA, Execute Plans PR/merge
   SHA, deployed BFF SHA, deployed frontend bundle identity, origins, timestamp,
   authenticated role, and strict/live configuration.
2. A sanitized API ledger with one row per command or query, retaining
   idempotency key, correlation/trace ID, persona/ledger/binding,
   recommendation/review/decision, ranking snapshot, rebalance, command,
   approval, and audit identities where applicable.
3. Separate verdicts for command admission, terminal execution, and
   authoritative readback. None may be inferred from another.
4. Negative-probe bodies for live intent during paper creation, direct
   full-live promotion intent, unapproved live allocation increase, emergency
   promotion, emergency increase, and frontend fallback substitution.
5. Authenticated desktop and mobile traces or screenshots tied to the same
   deployed frontend/BFF identities, with required-request failures preserved.
6. A residual-risk table giving severity, blocking status, owner, and an
   objective expiry or recheck condition for every missing or conflicting fact.

## Frontend Consumption Guardrails

- Use `execute-plans` from its own repository and Pantheon-owned dev hosting;
  do not copy frontend artifacts into this repository.
- Build with `VITE_BFF_MODE=live`, the deployed Pantheon BFF base URL, and
  `VITE_BFF_FALLBACK=strict`.
- Preserve response IDs and the original idempotency key. After an ambiguous
  write, query the original intent before attempting another mutation.
- Render admission, pending execution, terminal failure/success, and refreshed
  authoritative state separately.
- Treat missing ledger/binding, stage, evidence, exclusion/cap reason,
  approval, or freshness as blocked/unknown. Do not synthesize zero weights,
  eligibility, linkage, or success.
- Distinguish `401`, `403`, `409`, `422`, network/unavailable, stale/degraded,
  and genuine empty results.

## Reviewer Gate

- [ ] The packet remains support-only and introduces no new contract truth.
- [ ] Every route claim is phrased as repository readiness, not hosted proof.
- [ ] Every unproven gap has a named resolving lane and objective recheck.
- [ ] Admission, terminal command state, and authoritative readback remain
      independent evidence gates.
- [ ] Desktop/mobile strict-mode proof and all required negative probes remain
      mandatory for parent closeout.
- [ ] The parent cannot convert missing acceptance evidence into a pass merely
      by recording a residual risk.

## Composition Boundary

Owned layer: task-scoped BFF query-gap ownership, operator evidence sequence,
frontend handoff guardrails, and reviewer gate.

Not changing: L1 truth, API/schema contracts, BFF or frontend implementation,
runtime/registry/governance behavior, deployment configuration, or capital
state.

Composes with: the original `PPL-ALLOC-009` handoff, follow-up-2 readiness
gate, follow-up-3 stop/go matrix, and the parent owner's closeout archive.

## References

- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-009-closeout-dev-publish.md`
