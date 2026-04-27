# APP-003-FINRL-DEFERRED-PREP-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `APP-003-FINRL-DEFERRED-PREP-001`  
**Parent Owner**: `Codex2`  
**Parent Reviewer**: `Codex`  
**Parent Status**: `done`  
**Sidecar Task**: `APP-003-FINRL-DEFERRED-PREP-001-SIDECAR-ACCEPTANCE`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Codex2`  
**Helper Kind**: `acceptance_packet`  
**Generated**: `2026-04-25`  
**Refresh State**: `re-verified at 2026-04-27T12:24:50Z for the current Codex2 review_ready_dispatch; local proof commands re-run and the support packet refreshed against live repo evidence`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 policy, canonical
> deferred-activation truth, runtime behavior, registry/governance
> implementations, or the parent execution record. It packages a current
> reviewer-facing acceptance snapshot, dependency map, and scope boundary for
> `APP-003-FINRL-DEFERRED-PREP-001`.

## 1. Executive Summary

`APP-003-FINRL-DEFERRED-PREP-001` has moved past the earlier package-only stub
state and is now `done`. The live repo contains a prep-only FinRL lane
with:

1. a governed input adapter and explicit non-default deferred-prep gate
2. an offline workflow that emits only draft `rl_policy` artifacts
3. a worker entrypoint, sample dataset, unit tests, and smoke coverage
4. documentation that keeps FinRL at `criteria-defined` and preserves the RL
   gate boundary

This is still not an activation packet. Review may confirm only that the
deferred-prep scaffold landed truthfully and did not overclaim runtime or
governance maturity.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable truth for sidecar ownership, lifecycle, and downstream dependencies |
| `.orchestrator/task-briefs/app_003_finrl_deferred_prep_001_sidecar_acceptance.md` | Confirms this helper slice is support-only and limited to acceptance material |
| `docs/reviews/2026-04-25-deferred-prep-execution-packet.md` | Defines the allowed scope for this deferred-prep wave |
| `docs/reviews/2026-04-25-app-003-finrl-deferred-prep-001-codex2-handoff.md` | Archived owner handoff that summarizes the completed FinRL prep-only implementation and verification surface |
| `docs/reviews/2026-04-25-app-003-finrl-deferred-prep-001-codex-review.md` | Archived reviewer closeout showing the enforced gate correction and truthful deferred boundary |
| `OSS_INTEGRATION_CHECKLIST.md` | Canonical FinRL row truth remains `criteria-defined` |
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Cross-backend truth that FinRL remains activation-ready on paper, not activated |
| `services/learning/rl/README.md` | Preserves the closed RL gate and FinRL-first future lane framing |
| `services/learning/rl/PATH_DEFINITION.md` | Defines the intended `rl_policy` path and artifact envelope |
| `services/learning/rl/RL_PATH_APPROVAL_GATE.md` | States that deferred prep does not itself reopen the RL gate |
| `services/research/finrl/README.md` | Local repo summary of the landed prep-only FinRL lane |
| `services/research/finrl/adapter/finrl_adapter.py` | Governed adapter, gate enforcement, draft artifact and candidate packet path |
| `services/research/finrl/worker.py` | Container/CLI entrypoint for the deferred-prep lane |
| `services/research/finrl/smoke_test.py` | Explicit-gate smoke path proving draft-only output semantics |
| `services/research/finrl/test_adapter.py` | Unit coverage for adapter, gate, and workflow behavior |
| `services/control-plane/skills/skills.yaml` | Shows `run_rl_train` maps to `FinRLTool` |
| `services/control-plane/permissions/contract.md` | Shows `FinRLTool` is already part of the `research` tool class |
| `services/control-plane/router/contract.md` | Shows `research.*` intent may route to `FinRLTool` |
| `services/evaluation/optimizers/contract.md` | Shows downstream optimizer vocabulary already includes `finrl_ppo` |

## 3. Repo-Current Truth Snapshot

| Truth item | Repo evidence | Review implication |
|---|---|---|
| Parent task is `done` | `python3 scripts/ai_status.py show APP-003-FINRL-DEFERRED-PREP-001` resolves to the archived snapshot with `terminal_status = done` at `2026-04-25T05:00:33Z` | This packet now serves as a post-close acceptance support record and reviewer reference, not a pre-implementation stub summary |
| Deferred-prep scope is explicit | `docs/reviews/2026-04-25-deferred-prep-execution-packet.md` authorizes scaffold/adapter/offline/smoke work and forbids RL gate reopen or activation claims | Review must stay bounded to prep-only acceptance |
| Canonical FinRL status remains `criteria-defined` | `OSS_INTEGRATION_CHECKLIST.md`, `RESEARCH_BACKEND_MATURITY_MATRIX.md`, and `services/research/finrl/README.md` keep FinRL deferred/non-activated | Approval must not mutate the canonical maturity claim |
| Governed adapter and gate now exist | `services/research/finrl/adapter/finrl_adapter.py` defines `DeferredPrepGate`, `GovernedFinRLInputAdapter`, and the draft-only workflow | Acceptance criterion 1 now has concrete code surfaces to review |
| Worker entrypoint is implemented | `services/research/finrl/worker.py` resolves dataset input, enforces explicit enablement, and emits a draft-only summary | The Docker/worker surface is now truthful instead of a broken stub |
| Offline workflow and proof surfaces now exist | `services/research/finrl/smoke_test.py`, `services/research/finrl/test_adapter.py`, and `examples/policy_dataset_sample.json` are present | Acceptance criterion 2 is reviewable from live repo evidence |
| Smoke path preserves draft-only boundary | Smoke output reports `artifact_state = draft`, `deployment_stage = none`, `candidate_next_state = candidate`, and `gate_state = closed` | The prep lane does not overclaim activation |
| Unit coverage passes | `python3 -m pytest services/research/finrl/test_adapter.py -q` returns `14 passed` | Core adapter/workflow assertions are verified locally |
| Downstream contract names already exist | `skills.yaml`, router/permission contracts, and optimizer contract reserve `FinRLTool` and `finrl_ppo` | The parent aligns with existing governed vocabulary |
| RLlib is directly blocked on this parent | `APP-003-RLLIB-DEFERRED-PREP-001` depends on `APP-003-FINRL-DEFERRED-PREP-001` in `ai-status.json` | FinRL closeout wording still needs to be precise because downstream sequencing is real |
| Ray Tune is indirectly blocked on this parent | `APP-003-RAYTUNE-DEFERRED-PREP-001` depends on `APP-003-RLLIB-DEFERRED-PREP-001` | Any FinRL overclaim would propagate into the broader RL deferred wave |

## 4. Parent Acceptance Checklist

Review the parent against the current repo state, not against production
activation expectations.

| Parent acceptance target | Verification | Status now |
|---|---|---|
| FinRL deferred prep scaffold lands behind a non-default gate | `DeferredPrepGate` exists; smoke requires `--enable-deferred-prep`; worker requires `PANTHEON_FINRL_PREP_ENABLED`; repo contains governed adapter and worker surfaces | PASS |
| Offline workflow and smoke tests land without production overclaim | Workflow emits draft-only artifacts while keeping `deployment_stage = none`, `gate_state = closed`, and candidate progression bounded to offline prep output; unit and smoke coverage are present and passed locally | PASS |
| Canonical docs and packet preserve criteria-defined deferred truth | Deferred-prep packet, RL docs, and FinRL README keep FinRL non-activated and explicitly outside RL gate reopen | PASS |

### Verification Notes

Local verification run for this sidecar refresh (`2026-04-27T12:24:50Z`,
re-run for the current review dispatch):

1. `python3 -m pytest services/research/finrl/test_adapter.py -q`
2. `python3 services/research/finrl/smoke_test.py --enable-deferred-prep`

Observed command results from the latest refresh:

- `python3 -m pytest services/research/finrl/test_adapter.py -q` -> `14 passed in 0.14s`
- `python3 services/research/finrl/smoke_test.py --enable-deferred-prep` -> assertions OK

Observed smoke outputs:

- `artifact_state = draft`
- `deployment_stage = none`
- `candidate_next_state = candidate`
- `gate_state = closed`
- `artifact_family = rl_policy`
- `backend = stub_finrl`

## 5. Dependency Map

### 5.1 Durable Task Dependencies

| Task | Relationship | Current read |
|---|---|---|
| `APP-003-FINRL-DEFERRED-PREP-001` | parent task | First task in the deferred RL prep chain; now `done` |
| `APP-003-FINRL-DEFERRED-PREP-001-SIDECAR-ACCEPTANCE` | support helper | Reviewer-facing support only; does not change canonical truth |
| `APP-003-RLLIB-DEFERRED-PREP-001` | direct downstream task | Explicitly depends on the FinRL parent |
| `APP-003-RAYTUNE-DEFERRED-PREP-001` | indirect downstream task | Depends on RLlib and therefore inherits FinRL truth transitively |

### 5.2 Semantic Dependency Chain

| Dependency | Source | Why it matters |
|---|---|---|
| Deferred-prep execution packet | `docs/reviews/2026-04-25-deferred-prep-execution-packet.md` | Defines the allowed scope: scaffold only, no RL gate reopen, no activation claims |
| RL closed-state governance | `services/learning/rl/README.md`, `PATH_DEFINITION.md`, `RL_PATH_APPROVAL_GATE.md` | Keeps FinRL as the first future RL lane while preserving the reopen requirement |
| FinRL canonical row truth | `OSS_INTEGRATION_CHECKLIST.md`, `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Keeps checklist truth at `criteria-defined` even after prep scaffold lands |
| Repo-local prep implementation | `services/research/finrl/README.md`, `worker.py`, `adapter/finrl_adapter.py`, `smoke_test.py`, `test_adapter.py` | Provides the concrete surfaces the completed parent task accepted |
| Existing control-plane contract reservation | `services/control-plane/skills/skills.yaml`, `permissions/contract.md`, `router/contract.md` | Confirms the landed prep lane fits the established `FinRLTool` vocabulary |
| Existing downstream registry/optimizer vocabulary | `services/evaluation/optimizers/contract.md` | Confirms `rl_policy` / `finrl_ppo` naming stays aligned with existing contracts |
| Follow-on RL stack sequencing | `ai-status.json`, `services/learning/rl/README.md` | RLlib remains downstream of FinRL, and Ray Tune remains downstream of RLlib |

## 6. Open Cautions for Review

| Caution | Why it matters |
|---|---|
| Review approval is not RL activation approval | The deferred-prep packet explicitly forbids reopening the RL gate or claiming runtime execution proof |
| `criteria-defined` must remain the checklist truth after parent closeout | The implementation may be prep-complete while canonical maturity still stays deferred |
| Draft artifact semantics must remain intact | `artifact_state = draft` and `deployment_stage = none` are the evidence that the lane stays repo-local and non-activated |
| The real-backend path is still boundary validation, not production proof | `FinRLDeferredBackend` validates import/version boundary; it is not a live trading or canary claim |
| Downstream RLlib/Ray Tune sequencing should not be accelerated by wording drift | FinRL closeout should unblock sequencing truthfully, not by overstating maturity |

## 7. Scope Boundary - What Reviewer Should Reject

| Problematic move | Why it is wrong |
|---|---|
| Treating sidecar or completed parent closeout as RL gate reopen | Neither the helper task nor the parent changes `RL_PATH_APPROVAL_GATE.md` |
| Promoting FinRL from `criteria-defined` to `smoke-tested`, `governed`, or activated canonical truth based on this prep lane | Current evidence proves deferred prep, not canonical activation |
| Translating local smoke success into execution proof or canary/live readiness | Smoke coverage validates prep-only surfaces and artifact semantics only |
| Ignoring RLlib and Ray Tune dependency truth | These tasks inherit FinRL disposition and must not be fed inflated claims |
| Asking this helper task to reconcile canonical RL policy wording | This sidecar may flag boundaries, but it does not edit L1/L2 truth |

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar updates only `support/sidecars/APP-003-FINRL-DEFERRED-PREP-001/APP-003-FINRL-DEFERRED-PREP-001-SIDECAR-ACCEPTANCE.md` |
| No canonical/runtime edits by sidecar | PASS | No L1 docs, runtime code, registry files, or governance files were modified in this helper refresh |
| Parent acceptance is mapped to current repo state | PASS | Section 4 now reflects the live closeout-time FinRL surfaces and verification results |
| Downstream dependency chain is explicit | PASS | Section 5 includes both direct (`RLlib`) and indirect (`Ray Tune`) follow-on tasks |
| Deferred boundary is preserved | PASS | Sections 1, 3, 4, 6, and 7 all keep FinRL prep-only and non-activated |

## 9. Handoff to Reviewer (`Codex2`)

This sidecar is ready for reviewer use as the updated acceptance packet for
`APP-003-FINRL-DEFERRED-PREP-001`.

Review dispatch note:

- Reviewer routing is currently `Codex2`. Earlier Claude review attempts failed
  on local authentication, and the latest task-scoped reroute at
  `2026-04-25T16:30:52Z` kept the reassigned reviewer in place without changing
  scope or acceptance criteria.
- Latest proof refresh at `2026-04-27T12:24:50Z` re-ran the declared local
  commands with unchanged semantics:
  `python3 -m pytest services/research/finrl/test_adapter.py -q` ->
  `14 passed in 0.14s`
  `python3 services/research/finrl/smoke_test.py --enable-deferred-prep` ->
  assertions OK with `backend = stub_finrl`, `artifact_state = draft`,
  `deployment_stage = none`, `candidate_next_state = candidate`,
  `gate_state = closed`, and `artifact_family = rl_policy`
- Prior wake-up and redispatch loops all converged on the same result, so this
  packet now carries only the latest repo-current proof instead of the full
  dispatch churn log.

What it gives you now:

1. a closeout-time acceptance snapshot aligned with the live repo, not the older
   package-only stub state
2. explicit verification notes showing the landed unit and smoke proof
3. a dependency map and reviewer cautions that keep the deferred boundary and
   downstream RL chain truthful

Recommended reviewer stance:

1. approve the parent only as deferred-prep scaffold work
2. keep canonical FinRL truth at `criteria-defined`
3. reject any summary that turns this closeout into RL activation, execution
   proof, or canary/live readiness

---
*Generated by Codex as a sidecar `acceptance_packet` helper for
`APP-003-FINRL-DEFERRED-PREP-001`. This file is a support artifact and does not
modify canonical truth.*
