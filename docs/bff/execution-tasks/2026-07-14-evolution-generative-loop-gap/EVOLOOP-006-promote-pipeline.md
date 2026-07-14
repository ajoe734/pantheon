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
| `python3 -m pytest services/deployment -q` | 108 passed |
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

### Live dev proof

Implementation PR `#3629` merged as
`1e9882f2a7ff08be51a0f93a2c647b818137fd2b`. A clean detached worktree at
that exact SHA built and recreated only `registry`, `deployment`, and
`runtime-manager` in the `pantheon` dev Compose project. The shared deploy
checkout and its unrelated orchestrator changes were not modified. The
runtime-manager image reports the exact merge SHA; all three selected services
were healthy before the transition. Deployment retained `PANTHEON_ENV=dev`,
`PANTHEON_LIVE_BROKER_ENABLED=false`, and `BROKER_PAPER_ENABLED=true`.

Governance decision
`apv-evoloop-006-promote-20260714-0756` was proposed, reviewed, and decided
through the Governance API for
`artifact-tw-session-momentum-v1@1.0.0`, `pool-tw-equity-paper`, and
`persona-tw-equity`. The pipeline then advanced the candidate through the
Registry API and ran this sequence:

| Operation | Previous binding/artifact | Result binding/artifact | Deployment readback | Fleet readback |
|---|---|---|---|---|
| promote | `rb-abb82fd3538b4014bb7e7d3186a58c58` / `artifact-tw-equity-session-v1@1.0.0` | `rb-9d952eb0b7cc4cc9b33d2ea3220ac006` / `artifact-tw-session-momentum-v1@1.0.0` | `plan-evoloop-006-promote-20260714a` executed; saga completed at sequence 3 | converged after 6 polls; old binding absent |
| rollback | `rb-9d952eb0b7cc4cc9b33d2ea3220ac006` / promoted artifact | `rb-1e1182eb4ec74d179b8eab194f55af63` / exact rescue artifact | restored `plan-tw-equity-paper`; `rollback_parent` and action `replace` verified | converged after 5 polls; promoted binding absent |
| re-promote | `rb-1e1182eb4ec74d179b8eab194f55af63` / restored rescue artifact | `rb-f13ece22967b4f7baf1329c17d0f4cef` / promoted artifact | `plan-evoloop-006-promote-20260714b` executed; saga completed at sequence 3 | converged after 8 polls; rollback binding absent |

At each pause, runtime-manager's active-pool readback, the fleet worker record,
the worker `/readyz` response, and `/proc/<worker-pid>/environ` agreed on
`runtime-tw-equity-paper`. They also agreed on the stage-specific binding,
plan, artifact id/version, and `pool-tw-equity-paper`. During rollback,
`/readyz` and the process environment both showed the exact historical
`artifact-tw-equity-session-v1@1.0.0` and `plan-tw-equity-paper`, not a
synthetic substitute.

The final authoritative state is:

- RuntimeBinding `rb-f13ece22967b4f7baf1329c17d0f4cef` is active for
  `runtime-tw-equity-paper` and its predecessor is retired;
- DeploymentPlan `plan-evoloop-006-promote-20260714b` is `executed`, with
  `transition_type=replace`, `runtime_action=replace_binding`, and exact
  rollback target `artifact-tw-equity-session-v1@1.0.0`;
- deployment saga `deployment-saga-plan-evoloop-006-promote-20260714b` is
  `completed` at `runtime_active` with no failure;
- Registry entry `artifact-tw-session-momentum-v1` is `approved`, linked to
  the exact approval decision, and projects the final plan and binding; and
- the final worker is `ready`, `live`, and `paper_execution_ready`, with its
  binding lookup resolved from runtime-manager.

The redacted structured readbacks are archived in
[`EVOLOOP-006-live-evidence.json`](./EVOLOOP-006-live-evidence.json). All
mutations in this proof used Governance, Registry, Deployment, Runtime Manager,
and fleet service APIs; no service store was edited directly.

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
