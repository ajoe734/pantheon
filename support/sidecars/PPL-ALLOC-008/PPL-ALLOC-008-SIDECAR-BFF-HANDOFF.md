# PPL-ALLOC-008 BFF / Frontend Handoff Packet

Task: `PPL-ALLOC-008-SIDECAR-BFF-HANDOFF`  
Parent: `PPL-ALLOC-008`  
Owner: Codex2  
Reviewer: Codex  
Kind: support-only `bff_handoff_packet`

## Purpose And Boundary

This packet maps the existing emergency-containment BFF guard to the operator
journey and identifies the remaining query/composition gaps for the parent
owner. It does not change canonical policy, runtime behavior, registry or
governance truth, BFF implementation, or `execute-plans` source.

Containment is a risk-decreasing workflow. A recommendation, accepted command,
or adapter receipt must not be presented as proof that a live runtime or
capital binding changed.

## Existing Contract Inventory

| Need | Existing surface | Truth boundary |
|---|---|---|
| Allowed triggers/actions | `services/control-plane/bff/emergency_containment_policy.py` | Triggers are drawdown/daily-loss breach, forced kill, binding or reconciliation mismatch, unresolved incident, hard-risk breach, and stale live telemetry. Actions are freeze, reduce capital, suspend, risk-off, flatten, allocation rollback, and retire. |
| Policy validation | governed `EmergencyContainment` command validator | Requires an authorized operator/reviewer/approver/admin role and rejects unsupported or risk-increasing shapes. Normal ops-console preconditions still apply. |
| Evidence guard | `evidence_refs` in command params | At least one non-empty evidence reference is required. Do not manufacture evidence from UI copy. |
| Capital guard | `current_weight` and `target_weight` for `reduce_capital` | Target must be strictly lower than current. Preserve server rejection rather than reproducing policy only in the client. |
| Rollback guard | `rollback_ref` for `rollback_allocation` | Required for allocation rollback; it identifies the requested rollback basis, not proof of completion. |
| Forbidden direction | emergency policy validator and emergency rebalance validation | Promotion, canary/live creation, allocation increase, or a canary/live target stage is rejected. |
| Proposal projection | `POST /bff/rebalances` with `emergency: true`; `GET /bff/rebalances/{id}` | Produces an `emergency_containment` proposal and keeps proposal state separate from apply/execution state. Emergency proposal lines cannot increase weight or recommend promotion. |
| Apply/action path | `POST /bff/rebalances/{rebalance_id}/apply` and `POST /bff/rebalances/{rebalance_id}/actions/{action_id}` where applicable | Governed submission only; the returned command/receipt must be followed by refreshed authoritative reads. |
| Audit projection | command adapter receipt | Includes containment action, trigger, evidence refs, rollback ref, `risk_direction=decrease_only`, and `live_capital_side_effects=false`. The last field explicitly prevents claiming direct live mutation. |

All mutations must preserve the BFF's role, confirmation, idempotency, and
precondition behavior. The frontend may preflight required fields for usability
but must render the BFF's 4xx policy response as authoritative.

## Operator Journey

1. **Detect**: show the breach/anomaly with severity, affected persona,
   runtime/binding/capital identity, observed time, source freshness, and stable
   evidence references.
2. **Diagnose**: let the operator open Sentinel/Risk Center and the affected
   Persona Fleet, runtime, binding, incident, or reconciliation detail without
   losing the containment context.
3. **Choose containment**: offer only freeze, reduce capital, suspend,
   risk-off, flatten, rollback allocation, or retire. Never place promote,
   create-canary/live, or increase-allocation controls in this mode.
4. **Review impact**: display current and target weight for reduction, the
   rollback reference when applicable, reason, evidence, and the explicit
   `decrease only` policy label before confirmation.
5. **Submit governed action**: send the stable trigger/action values and
   required evidence through the installed BFF command/proposal helper with an
   idempotency key. Do not optimistic-update stage, allocation, or runtime.
6. **Track**: render proposal, command, and audit receipt identifiers
   separately. An accepted request remains `containment submitted`.
7. **Confirm**: refresh the relevant incident, runtime/binding, allocation, and
   rebalance read models. Render `contained` only when an authoritative read
   proves the risk-decreasing state; otherwise retain pending/degraded copy.

Recommended state separation:

```text
trigger/evidence -> containment proposal -> command submitted -> receipt
                 -> authoritative read confirms reduced/frozen/stopped state
```

`receipt` is not interchangeable with `confirmed`. `live_capital_side_effects`
being false means the adapter receipt itself makes no live-mutation claim.

## Required UI Payload And Copy

Preserve these fields without client-side semantic rewriting:

- `action` and `trigger` using the server enum values;
- affected `persona_id`, `runtime_id`, binding/capital identifiers when known;
- operator `reason` and one or more `evidence_refs`;
- `current_weight` and strictly lower `target_weight` for capital reduction;
- `rollback_ref` for allocation rollback;
- proposal, command, receipt, and incident/review identifiers independently;
- server validation code/detail and degraded/unavailable source metadata.

Use labels such as **Emergency containment**, **Decrease only**, **Containment
submitted**, and **Containment confirmed**. Avoid **promotion**, **upgrade**,
**capital approved**, or **applied** until the corresponding authoritative read
supports that exact state. Destructive actions need explicit confirmation and
must remain accessible by text, not color alone.

## BFF Query Gaps And Parent Decisions

These are handoff findings, not authorization for this sidecar to define or
implement new canonical routes.

1. **No dedicated containment inbox query was found.** The policy and command
   guard exist, but the frontend still needs a stable source for the collection
   of active breach, stale-telemetry, reconciliation, binding-mismatch, and
   unresolved-incident candidates. The parent should compose existing
   Sentinel/Risk/incident reads if they expose stable evidence refs; otherwise
   raise a narrow BFF aggregate-query task rather than infer triggers locally.
2. **No single post-command confirmation read exists.** Confirmation may span
   rebalance detail, runtime/binding state, capital allocation, and incident
   state. Until the parent identifies the authoritative read for each action,
   the UI must stop at `submitted` and retain the receipt link.
3. **The governed command helper must be confirmed in `execute-plans`.** Use the
   installed action-catalog/command transport for `EmergencyContainment`; do
   not invent a page-specific mutation path or call an adapter directly.
4. **Cross-surface identifiers may be incomplete.** Missing persona, runtime,
   binding, pool, incident, or evidence identity is itself a degraded safety
   condition. Keep the event visible, disable only the unsafe action, and route
   to diagnosis rather than synthesizing an identifier.
5. **Proposal and immediate action are distinct concepts.** If the parent uses
   an emergency rebalance proposal for capital rollback/reduction, keep its
   proposal/apply lifecycle visible. Freeze/suspend/risk-off flows should use
   the governed action appropriate to their target and must not be represented
   as a completed rebalance.

## Frontend Acceptance Handoff

- Cover every server trigger and allowed action with human-readable copy.
- Prove no emergency UI path offers promotion, canary/live creation, or an
  allocation increase; tampered payloads must surface the BFF rejection.
- Require evidence for every action, a strictly lower target for reduction,
  and a rollback reference for allocation rollback.
- Keep trigger, proposal, command, receipt, and confirmed state distinct.
- Preserve idempotency across retry/double-click and display the returned
  stable identifiers.
- Test role denial, missing confirmation/precondition, validation failure,
  degraded reads, missing identity, request failure, and unknown enum values.
- Test that a successful adapter receipt does not optimistic-update live
  capital or claim containment confirmation.
- Deep-link between Promotion & Allocation emergency actions and Sentinel/Risk
  Center while retaining the affected target and evidence context.

## Composition Notes For Parent Owner

Owned here: support-only BFF inventory, query-gap analysis, operator journey,
and fail-closed frontend handoff guidance.  
Not changing: L1 canonical truth, core contracts, runtime/registry/governance
implementation, BFF routes, action policy, or frontend code.  
Composes with: `PPL-ALLOC-004` allocation/rebalance policy, `PPL-ALLOC-006`
Promotion & Allocation workbench, the parent `PPL-ALLOC-008` implementation,
and `PPL-ALLOC-009` hosted closeout evidence.

## Source Evidence Reviewed

- `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md`
- `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PPL-ALLOC-001-CURRENT-STATE-AUDIT.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-008-emergency-containment.md`
- `services/control-plane/bff/emergency_containment_policy.py`
- `services/control-plane/bff/persona_allocation_policy.py`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/command_executor.py`
- `services/control-plane/bff/tests/test_bff_emergency_containment.py`
- `services/control-plane/bff/tests/test_bff_rebalance_proposals.py`

This packet is advisory support material. The parent owner decides whether and
how to compose it into the canonical implementation.
