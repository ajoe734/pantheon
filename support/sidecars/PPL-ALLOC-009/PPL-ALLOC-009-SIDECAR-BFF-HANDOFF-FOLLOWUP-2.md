# PPL-ALLOC-009 Closeout Readiness Follow-Up

- **Sidecar Task**: `PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
- **Parent Task**: `PPL-ALLOC-009`
- **Owner / Reviewer**: `Codex` / `Antigravity`
- **Helper Kind**: `bff_handoff_packet`
- **Scope**: support-only; no canonical, runtime, registry, governance, or
  frontend contract mutation
- **Date**: 2026-07-12

## Purpose

This follow-up turns the original handoff packet into a fail-closed readiness
gate for the parent closeout. It does not assert that hosted behavior exists.
The parent owner decides whether to absorb this checklist and must supply the
actual merged, deployed, authenticated evidence.

## Readiness Delta

The repository exposes individual persona, promotion-review, allocation,
rebalance, command, and containment surfaces. That is sufficient to design an
operator journey, but it is not by itself end-to-end proof. Closeout remains
open until the following three boundaries have named evidence:

| Boundary | Evidence required from parent | Fail-closed interpretation |
| --- | --- | --- |
| Cross-surface correlation | One ledger row preserving `persona_id`, `paper_ledger_id` or pool/sleeve identity, recommendation/review IDs, `ranking_snapshot_id`, `rebalance_id`, command ID, approval ref, audit ref, correlation/trace ID, and timestamps from actual responses. | If a join key is absent, report that segment unproven. The frontend and smoke harness must not synthesize identity or infer linkage from labels or timing. |
| Accepted command versus applied state | Apply response plus the owning command/status read reaching a terminal success state, followed by authoritative capital/binding readback of the intended weights and identities. | `202`, a decision receipt, proposal state, elapsed time, or a success toast proves admission only. Keep the UI at `apply submitted` when terminal status or readback is missing. |
| Hosted frontend truth | Exact Pantheon and Execute Plans deployed SHAs, strict live BFF configuration, authenticated desktop and mobile browser traces, and zero required-request fallback/mock substitutions. | A local test, code-level route, fixture, cached seed, or one viewport cannot close hosted acceptance. Surface auth, network, stale, and unavailable states distinctly. |

No new aggregate endpoint is required merely for closeout. Deterministic joins
across authoritative responses are acceptable. If they cannot be demonstrated,
record the missing query as a blocking residual risk rather than creating a
client-owned source of truth.

## Parent Evidence Sequence

1. Record repository PRs and merge SHAs, deployed BFF/frontend SHAs, origins,
   timestamp, operator role, and strict fallback posture.
2. Create or select a safe paper persona. Prove `paper_running`, isolated paper
   ledger identity, runtime binding, and no live-capital side effect.
3. Submit a paper promotion recommendation and complete the authorized Human
   Inbox decision. Preserve recommendation, review, decision, evidence, and
   audit identities without claiming capital application.
4. Capture the real ranking snapshot and full eligible/excluded universe.
   Evaluate target weights and verify the result remains advisory
   (`applied: false`).
5. Create a rebalance proposal. Preserve current/target/delta, caps,
   exclusions, simulation, rollback target, approval, and audit references.
6. Prove a live increase without `approval_ref` fails. Apply the approved
   proposal once with a stable idempotency key; poll terminal command status;
   then refresh authoritative allocation/binding reads.
7. Prove emergency containment accepts a risk-decreasing action while rejecting
   promotion and allocation increase. Use only safe dev fixtures and redact
   credentials.
8. Repeat the action surfaces in authenticated desktop and mobile browsers.
   Retain request failures and degraded states; do not convert them to empty or
   successful evidence.

## Evidence Ledger Template

Use one row per step so reviewers can distinguish admission, execution, and
readback:

| Step | Deployed SHAs | Route / UI | Role | Idempotency + trace | Domain IDs | HTTP / command state | Authoritative readback | Safety class | Artifact | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Example only | `<bff>` / `<fe>` | `<route>` | `<role>` | `<keys>` | `<ids>` | `<status>` | `<query + fields>` | read / proposal / approved apply / containment | `<sanitized path>` | pass / fail / blocked |

Every failure or missing field needs a residual-risk row containing severity,
blocking status, owner, and an expiry or recheck condition. `None observed` is
valid only after the corresponding positive and negative probes ran against
the recorded deployment.

## Frontend Handoff Rules

- Use the separate `execute-plans` repository and Pantheon-owned dev hosting;
  do not materialize frontend source in this repository.
- Build with `VITE_BFF_MODE=live`, the deployed dev BFF base URL, and
  `VITE_BFF_FALLBACK=strict`; verify the deployed bundle identity.
- Browser requests terminate at the BFF. Do not add direct internal-service,
  registry, runtime, or broker fallbacks.
- Preserve server IDs, evidence, cap/exclusion reasons, and idempotency keys.
  Missing truth remains visibly blocked or unknown; do not manufacture zero
  weights, eligibility, stage, or completion.
- Handle `401`, `403`, `409`, `422`, network failure, stale data, and terminal
  command failure separately. A retry after an ambiguous write first queries
  the original intent; it does not mint a new mutation identity.

## Reviewer Gate

- [ ] This artifact remains support-only and makes no new contract claim.
- [ ] Each journey segment has deterministic response-derived linkage.
- [ ] Command admission, terminal execution, and allocation readback are three
      separately evidenced states.
- [ ] Approval-negative and containment-negative probes fail closed.
- [ ] Hosted proof names exact deployed SHAs and includes authenticated desktop
      and mobile strict-mode traces.
- [ ] Residual risks have an owner and recheck condition.

If any box is unchecked, return concrete changes to the parent closeout; do not
reinterpret this sidecar as implementation or deployment evidence.

## Composition Boundary

Owned layer: task-scoped closeout query, operator-journey, frontend handoff, and
evidence-review support.

Not changing: L1 truth, BFF/runtime contracts, registry/governance semantics,
frontend code, deployment, or capital state.

Composes with: the original `PPL-ALLOC-009` handoff packet, `PPL-ALLOC-003`
binding reads, `PPL-ALLOC-004` allocation policy, `PPL-ALLOC-006` workbench,
`PPL-ALLOC-008` containment, and the parent owner's closeout archive.

## References

- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-009-closeout-dev-publish.md`
- `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md`
- `services/control-plane/bff/persona_allocation_policy.py`
- `services/control-plane/bff/emergency_containment_policy.py`
