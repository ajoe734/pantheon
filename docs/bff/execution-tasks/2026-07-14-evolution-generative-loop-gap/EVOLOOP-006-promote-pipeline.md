# EVOLOOP-006 — Registry-to-LEAN Promote Pipeline

Task: `EVOLOOP-006`  
Owner: `Codex2`  
Reviewer: `Claude`  
Target: Pantheon dev paper runtime only

## Outcome

This task replaces one paper rescue binding through service APIs:

```text
approved StrategyArtifact
  -> approved same-stage DeploymentPlan
  -> deployment saga dispatch
  -> runtime-manager forward replace
  -> RuntimeBinding and fleet worker readback
  -> registry deployment-summary projection
```

The normal promote operation does not call the incident rollback API. It uses
`POST /api/runtimes/{runtime_id}/replace`; only an actual reversal calls
`POST /api/rollback`.

## Owned boundary

The task owns a deliberately narrow vertical slice:

- retain the governance decision id when a candidate registry artifact becomes
  approved;
- permit an explicit `replace` DeploymentPlan when `current_stage` equals
  `target_stage` and the plan names the current `binding_id`;
- forward-replace one RuntimeBinding through runtime-manager while preserving
  its runtime, capital pool, stage, and PersonaCapitalBinding identities;
- advance the existing deployment saga with binding-created and runtime-active
  service events;
- project the successful paper binding through the registry API; and
- emit a redacted, JSON-safe receipt that can drive an exact rollback.

This task does not add the generic durable outbox dispatcher, retry/DLQ worker,
registry persistence, or strategy-driven signal producer. Those compose with
`LOOP-PROD-DEP-001` and `EVOLOOP-007`.

## Preconditions and fail-closed checks

The pipeline requires all of the following before runtime replacement:

1. `GET /api/registry/strategy-artifacts/{registry_id}` resolves the expected
   artifact and its canonical id/version.
2. `GET /api/governance/approvals/{approval_decision_id}` is `decided` and
   `approved` (or `approved_with_conditions`) for that exact id/version and
   capital pool.
3. Candidate approval is performed only through the StrategyArtifact advance
   API and records the decision id; an already-approved entry must carry the
   same decision id.
4. `GET /api/runtime-bindings/{current_binding_id}` returns a non-terminal
   binding whose runtime equals `expected_runtime_id`, and whose pool, paper
   stage, and PersonaCapitalBinding match the request.
5. The DeploymentPlan explicitly names the current binding and carries the
   exact previous artifact id/version as its rollback reference.

Any identity mismatch stops the run before the forward replace call. No code in
this path reads or writes the RuntimeBinding store directly.

## Forward replace semantics

Runtime-manager constructs a new active binding first, using its narrowly
scoped single-runtime cutover bypass, and then retires the previous binding.
The replacement records:

- `metadata.replacement_kind = forward`;
- `metadata.replacement_parent_binding_id = <previous binding>`; and
- the promoted strategy id.

It deliberately leaves `rollback_parent` and `rollback_action_type` unset, so
normal promotion cannot manufacture rollback history. The returned binding's
`runtime_id` must exactly match both the route runtime id and the old binding's
runtime id.

## Runtime identity proof

For the paper fleet reconciler, the active RuntimeBinding is the source of the
worker environment. A successful proof requires all of these values to agree:

```text
RuntimeBinding.runtime_id
  == fleet worker runtime_id
  == worker /readyz runtime_id
  == /proc/<worker-pid>/environ PANTHEON_RUNTIME_ID
```

The same readback also records the new `binding_id`, `plan_id`, `artifact_id`,
and `artifact_version`. A healthy static paper container with a different
runtime id is not evidence for this task; the target is the fleet subprocess
owned by the replaced binding.

## Rollback procedure

The promote receipt retains only the identifiers and binding descriptors needed
for reversal. `rollback(receipt)` calls runtime-manager's canonical
`POST /api/rollback` with action `replace` and re-binds the exact prior artifact
id/version, pool, PersonaCapitalBinding, stage, and runtime identity. It then
verifies the replacement binding through runtime-manager, waits for the fleet
worker, and clears the promoted artifact's deployment-summary projection.

Rollback evidence must show:

- the promoted binding is retired;
- a new active binding points to the previous artifact id/version;
- `rollback_parent` points to the promoted binding;
- `rollback_action_type` is `replace`;
- runtime identity remains unchanged; and
- the fleet worker readback agrees with the rollback binding.

After demonstrating rollback, the pipeline may be run once more against the
rollback binding so the final dev state is the promoted artifact.

## Validation and evidence

Implementation validation on the task branch after merging current `dev`:

| Validation | Result |
|---|---|
| `python3 -m pytest services/deployment -q` | 103 passed |
| `python3 -m pytest services/registry -q` | 139 passed |
| `python3 -m pytest services/runtime-manager/test_runtime_manager.py services/execution/runtime-manager/test_paper_fleet_reconciler.py -q` | 102 passed |
| `python3 -m pytest services/control-plane/governance/test_deployment_plan.py -q` | 33 passed, 3 subtests passed |
| focused Registry/Deployment cross-set | 105 passed |
| Python compile and `git diff --check` | passed |

The focused pipeline proof contains six cases:

1. real Registry + Deployment + Runtime Manager API promote, rollback, and
   re-promote with fleet worker environment construction;
2. wrong runtime identity fails before runtime replacement;
3. an approved registry entry without the canonical decision link fails;
4. a partial post-cutover saga never returns a synthetic success receipt;
5. a lost rollback projection response replays from authoritative state; and
6. a receipt-tampered rollback target is rejected.

The live dev identifiers, `/readyz`, process environment, rollback, and final
re-promote readbacks will be appended after the implementation merge SHA is
deployed. Until then, this section claims local service-API acceptance only.

## Residual risks

- The registry service is currently in-memory. Rebuilding or restarting it
  re-seeds the checked-in StrategyArtifact as `candidate`, so approval and its
  deployment-summary projection must be re-established. Durable registry
  storage is outside this task and remains a productization requirement.
- The rescue artifact used as the first rollback target predates the governed
  StrategyArtifact registry. The rollback is nevertheless exact and
  authoritative through the prior RuntimeBinding plus DeploymentPlan rollback
  reference; this task does not fabricate a registry entry for historical
  state.
- The task-scoped orchestrator closes one vertical slice. Generic unattended
  outbox delivery, retry policy, DLQ handling, and reconciliation remain owned
  by `LOOP-PROD-DEP-001`.
