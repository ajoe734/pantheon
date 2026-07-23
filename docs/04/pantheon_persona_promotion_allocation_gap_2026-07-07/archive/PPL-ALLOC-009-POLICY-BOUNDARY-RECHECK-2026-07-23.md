# PPL-ALLOC-009 policy-boundary recheck — 2026-07-23

## Decision

`PPL-ALLOC-009` is not ready for B5 review or `done`. The stale credential and
deployment-lifecycle blockers are cleared, but B1 cannot currently be executed
on the hosted dev stack without violating the canonical canary/live activation
policy. B3 is consequently blocked because the acceptance addendum requires the
same B1 identity and chain on desktop and 393px mobile.

This is not a request to reprovision the five dev-login clients or control
passphrase. Those values exist, B2 remains cleared, and no raw credential value
was read or recorded during this recheck.

## Current accepted prerequisites

| Surface | Observation | Result |
| --- | --- | --- |
| PPL delivery | Distinct-identity commit `f18f7ad224b9787e30ad594eb372bf9023dca0f4` and atomic-stage-promotion commit `3b9336b0dc2929128b17b924921d63e0e5ed7911` are ancestors of the hosted BFF source SHA. | Pass |
| Dispatch/lifecycle dependencies | The functional commits from `OPS-DISPATCH-LEASE-SYNC-001` and `PAN-LIFECYCLE-RECOVERY-001` are ancestors of the hosted BFF source SHA. The final lifecycle PR after that release changed task evidence only. | Pass |
| Hosted BFF | Public `/bff/version` reports `c555a14ebbcc2a7504076eeba3d381b016231833`, `source_commit_known=true`, strict auth, dev login enabled, MFA required, and assistant kernel enabled. `/healthz` and `/readyz` returned HTTP 200. | Pass |
| BFF release | Pantheon Dev deploy run [29968941919](https://github.com/ajoe734/pantheon/actions/runs/29968941919) completed successfully, including the auth floor, shared lease, paper baseline, exact version proof, persistence smoke, and lease release. | Pass |
| Hosted frontend before reconciliation | The accepted read-only manifest reports execute-plans `0cfc3058b1b20bf850b0d5132c250f13cf88421d` and BFF `5004450c5493aa8aef284cf42439c9b27ef54235`, while the live BFF is `c555a14e...`. | Not an accepted exact pair |
| Frontend reconciliation | execute-plans integration run [29964393757](https://github.com/ajoe734/execute-plans/actions/runs/29964393757) was rerun against the stable live BFF. | In progress at capture time |

The frontend manifest remains safe while reconciliation runs:
`VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, real writes false,
dev-stub writes false, embedded bearer false, deployment profile `read-only`,
and deployment state `accepted`.

## Why B1 is fail-closed

The 2026-07-22 acceptance addendum requires one governance-produced identity
to move from `paper_running`, through a promotion review and human decision,
into real-ranking eligibility before target weights, proposal, approval/apply,
and Capital readback. A stub, direct store edit, or uncorrelated row is
explicitly forbidden.

The product contract makes the remaining boundary unambiguous:

1. Positive real allocation accepts only `canary_running` or `live_running`.
   A paper row receives `stage_not_real_allocation_eligible` in
   `services/control-plane/bff/persona_allocation_policy.py`.
2. The BFF promotion-review decision is intentionally a command/receipt with
   `runtime_mutation=false` and `live_capital_mutation=false`; it does not make
   a paper Persona canary/live eligible.
3. The canonical stage change is Runtime Manager
   `POST /api/runtime-bindings/{binding_id}/promote`. It re-reads the exact
   DeploymentPlan, Registry entry, artifact approval, CapitalPool,
   PersonaCapitalBinding, admissibility result, and target-bound human gate,
   and permits adjacent `paper -> canary` or `canary -> live` cutover only.
4. That route returns HTTP 409 `STAGE_EXECUTION_DISABLED` while the applicable
   execution switch is false.
5. The dev deploy contract explicitly sets both
   `PANTHEON_CANARY_EXECUTION_ENABLED=false` and
   `PANTHEON_LIVE_BROKER_ENABLED=false` in both deployment paths.
6. The canonical activation runbook states that, while real broker and capital
   evidence is missing, both switches must remain false. The acceptance
   addendum independently requires real/live capital to remain disabled.

Therefore there is no authorized hosted transition that can produce the
required same-identity real-ranking row today. Enabling canary/live execution,
using the legacy Persona lifecycle mutation as a substitute, directly editing
the read store, or joining an existing real row to the new paper Persona would
all exceed this task's authority or violate acceptance.

No B1 write was sent, no execution switch was changed, and no live/capital
side effect was produced during this recheck.

## Acceptance impact

| Gate | Status | Evidence / next condition |
| --- | --- | --- |
| B1 | Blocked | No policy-valid same-identity `paper -> canary/live-eligible -> allocation` chain can be produced while the mandated execution switches remain false. |
| B2 | Cleared | Dedicated clients, strict BFF, MFA, and prior write/read-only restore proof are already provisioned. Do not reopen the credential request. |
| B3 | Blocked by B1 | The addendum requires the exact B1 chain through the accepted FE/BFF pair on desktop and 393px mobile; an infrastructure-only or PINT paper proof is insufficient. |
| B4 | Cleared | Prior dependency delivery remains valid; current dispatch/lifecycle functional commits are included in the hosted BFF. |
| B5 | Not requested | Reviewer `Codex2` must decide IA only after B1/B3 evidence exists. |

## Required external decision

Human/Ops together with the Risk, Capital, and Execution owners must choose one
policy-valid route before this task can continue:

- provide a bounded dev canary activation packet containing the required
  broker-sandbox/entitlement, capital authorization, reconciliation,
  kill-switch, rollback, and four distinct claim-bound MFA authorities, and
  explicitly authorize the temporary canary execution switch; or
- amend B1 so that a governance-approved paper-only eligibility simulation is
  accepted without claiming `canary_running`/`live_running` or real allocation.

Until one of those choices is recorded, the worker must preserve the
fail-closed flags and must not manufacture the missing transition.

## Validation record

- Public FE manifest, BFF `/bff/version`, `/healthz`, and `/readyz` rechecked on
  2026-07-23 UTC.
- Pantheon deploy run `29968941919` conclusion and step outcomes checked through
  GitHub Actions.
- Git ancestry verified for the two PPL commits and all functional lifecycle
  repair commits against `c555a14e...`.
- Canonical policy, Runtime Manager gate, dev deploy flags, and allocation-stage
  admission were inspected directly in the repository.
- The two focused pytest cases for the disabled stage gate could not run in
  this worktree because the system Python has no `pytest` module; their source
  assertions and the successful release CI remain recorded. No package install
  was performed for this documentation-only recheck.

