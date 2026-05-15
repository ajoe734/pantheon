# APP-003-RLLIB-DEFERRED-PREP-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `APP-003-RLLIB-DEFERRED-PREP-001`
**Parent Owner**: `Codex`
**Parent Reviewer**: `Codex2`
**Parent Status**: `done`
**Sidecar Task**: `APP-003-RLLIB-DEFERRED-PREP-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-25`
**Refreshed**: `2026-04-27`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 policy, canonical
> deferred truth, runtime behavior, registry/governance implementations, or the
> parent execution record. It packages a reviewer-facing acceptance snapshot,
> dependency map, and scope boundary for the landed RLlib deferred-prep review
> task.

## 1. Executive Summary

`APP-003-RLLIB-DEFERRED-PREP-001` is now archived as `done` after Codex2 review
approval and Codex owner finalization. The live repo contains the deferred-prep
RLlib scaffold under `services/research/rllib/`: a governed train/eval adapter,
rollout/result schema, explicit non-default backend selector, worker entrypoint,
sample input, unit coverage, and smoke path. Revalidation on 2026-04-27 UTC
still shows the lane is draft-only and gate-closed.

This sidecar therefore now acts as a reviewer-facing closeout support packet
for the landed prep-only implementation and the already-finalized parent task.
It preserves the canonical statement that `RLlib` remains `version-pinned`, RL
stays gate-closed, and the broader RLlib + Ray Tune lane is still follow-on work
after the FinRL first-lane proof.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable truth for parent/sidecar ownership, lifecycle, and dependency edges |
| `.orchestrator/task-briefs/app_003_rllib_deferred_prep_001_sidecar_acceptance.md` | Confirms this helper slice is support-only and limited to acceptance material |
| `docs/reviews/2026-04-25-deferred-prep-execution-packet.md` | Authorizes the narrow deferred-prep exception for RLlib in this wave |
| `docs/reviews/2026-04-25-app-003-rllib-deferred-prep-001-codex-handoff.md` | Main review handoff packet with the implementation surface and executed verification commands |
| `OSS_INTEGRATION_CHECKLIST.md` | Canonical row truth remains `version-pinned` |
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Cross-backend truth that RLlib now has a repo-local prep scaffold but still remains activation-ready on paper, not activated |
| `services/learning/rl/README.md` | Records the RL closed-state, the deferred-prep exception, and the FinRL-first sequencing rule |
| `services/learning/rl/RL_PATH_APPROVAL_GATE.md` | Defines the real RL reopen gate and states RLlib/Tune remain follow-on after FinRL proof |
| `services/learning/rl/PATH_DEFINITION.md` | Defines the intended RLlib train/eval and Ray Tune workflow contract |
| `services/learning/rl/ENV_CONTRACT.md` | Documents the environment/state/action contract the future RLlib lane must instantiate repo-locally |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | Summarizes what RLlib has now versus what still blocks it from leaving `version-pinned` |
| `services/research/rllib/requirements.txt` | Shows the actual RLlib and Ray Tune package pins |
| `services/research/rllib/Dockerfile` | Shows the deferred-prep container remains prep-only and non-activating |
| `services/evaluation/optimizers/contract.md` | Confirms downstream optimizer vocabulary already reserves `rllib_ppo` for `rl_policy` outputs |

## 3. Repo-Current Truth Snapshot

| Truth item | Repo evidence | Review implication |
|---|---|---|
| Parent task is archived as `done` | `python3 scripts/ai_status.py show APP-003-RLLIB-DEFERRED-PREP-001` resolves to `ai-task-archive/tasks/APP-003-RLLIB-DEFERRED-PREP-001.json` with terminal status `done` | This packet now supports sidecar review/closeout, not parent implementation review |
| FinRL prerequisite is already closed out | `python3 scripts/ai_status.py show APP-003-FINRL-DEFERRED-PREP-001` resolves to the archive with terminal status `done` | RLlib no longer waits on FinRL execution, but it still must honor the FinRL-first sequencing and boundary language |
| RLlib row remains `version-pinned` | `OSS_INTEGRATION_CHECKLIST.md` and `RESEARCH_BACKEND_MATURITY_MATRIX.md` keep RLlib formally deferred at `version-pinned` even after the scaffold landed | Parent work must not upgrade canonical maturity or claim activation |
| Deferred-prep work is explicitly bounded | `docs/reviews/2026-04-25-deferred-prep-execution-packet.md` allows train/eval abstraction, rollout/result schema, local smoke scaffold, adapter boundary, and non-default backend wiring only | Review must reject any RL gate reopen, governed production train loop, or active-backend claim |
| RLlib and Ray Tune pins are present | `services/research/rllib/requirements.txt` pins `ray[rllib]>=2.9.0,<3.0.0` and `ray[tune]>=2.9.0,<3.0.0` | The version-pin baseline remains explicit after the scaffold landed |
| Repo-local adapter, worker, and smoke path now exist | `find services/research/rllib -maxdepth 2 -type f | sort` now returns adapter, config, worker, smoke, tests, README, sample input, Dockerfile, and requirements | Parent acceptance criteria 1 and 2 now have executable evidence to review |
| Revalidation remains draft-only and gate-closed | `python3 -m pytest services/research/rllib/test_adapter.py -q`, `python3 services/research/rllib/smoke_test.py --enable-deferred-prep`, and `PANTHEON_RLLIB_PREP_ENABLED=1 python3 services/research/rllib/worker.py` all pass on 2026-04-27 UTC | Reviewer can validate the landed scaffold without inferring RL activation |
| RLlib environment and search contracts are now partially instantiated repo-locally | `services/learning/rl/ENV_CONTRACT.md` and `PATH_DEFINITION.md` remain the design contract, while `services/research/rllib/adapter/` materializes the governed train/eval and artifact vocabulary | Parent no longer relies on prose-only design evidence |
| Downstream optimizer vocabulary already exists | `services/evaluation/optimizers/contract.md` defines RLlib / FinRL output semantics and optimizer method `rllib_ppo` | Parent should align prep surfaces to existing governed artifact language |
| Ray Tune remains coupled to the same follow-on lane | `RESEARCH_BACKEND_MATURITY_MATRIX.md` and `DEFERRED_OSS_ACTIVATION_MAP.md` describe Ray Tune as the RLlib-coupled search path | RLlib wording must stay careful because downstream Ray Tune sequencing inherits it |

## 4. Parent Acceptance Checklist

Review the parent against the repo state that exists today after implementation
and revalidation, while preserving the deferred-prep and non-activation
boundary.

| Parent acceptance target | Reviewer should require | Status now |
|---|---|---|
| RLlib deferred prep scaffold lands behind a non-default gate | A repo-local RLlib adapter/train-eval scaffold exists behind explicit opt-in or deferred gating; the RL path remains closed by default; no default production training path is exposed | PASS |
| Train eval schema and local smoke coverage land without reopening RL | Concrete rollout/result schema surfaces plus local smoke and tests prove non-activating, repo-local behavior without turning RLlib into an approved governed train loop | PASS |
| Canonical docs and packet preserve version-pinned deferred truth | Implementation packet, README wording, and status updates keep RLlib `version-pinned`, gate-closed, and downstream to the FinRL first-lane proof | PASS |

### Verification Notes

This sidecar refresh is documentation-only, but it revalidates the landed
RLlib deferred-prep evidence surface.

Checks used for this packet:

1. `python3 scripts/ai_status.py show APP-003-RLLIB-DEFERRED-PREP-001`
2. `python3 scripts/ai_status.py show APP-003-FINRL-DEFERRED-PREP-001`
3. `python3 -m pytest services/research/rllib/test_adapter.py -q`
4. `python3 services/research/rllib/smoke_test.py --enable-deferred-prep`
5. `PANTHEON_RLLIB_PREP_ENABLED=1 python3 services/research/rllib/worker.py`
6. `find services/research/rllib -maxdepth 2 -type f | sort`
7. `rg -n "RLlib|rllib|Ray Tune|rllib_ppo|rl_policy" services OSS_INTEGRATION_CHECKLIST.md RESEARCH_BACKEND_MATURITY_MATRIX.md`

## 5. Dependency Map

### 5.1 Durable Task Dependencies

| Task | Relationship | Current read |
|---|---|---|
| `APP-003-FINRL-DEFERRED-PREP-001` | upstream dependency | Archived `done`; first deferred-prep RL lane and explicit prerequisite for the RLlib parent |
| `APP-003-RLLIB-DEFERRED-PREP-001` | parent task | Mainline RLlib deferred-prep implementation task, archived `done` after review approval and owner finalization |
| `APP-003-RLLIB-DEFERRED-PREP-001-SIDECAR-ACCEPTANCE` | support helper | Reviewer-facing packet only; does not change canonical truth |
| `APP-003-RAYTUNE-DEFERRED-PREP-001` | direct downstream task | Explicitly depends on the RLlib parent and inherits its wording/risk boundary |

### 5.2 Semantic Dependency Chain

| Dependency | Source | Why it matters |
|---|---|---|
| Deferred-prep execution exception | `docs/reviews/2026-04-25-deferred-prep-execution-packet.md` | Authorizes narrow scaffold work while keeping RL non-activated |
| RL closed-state governance | `services/learning/rl/README.md`, `RL_PATH_APPROVAL_GATE.md` | Keeps RLlib behind the RL gate and under the FinRL-first sequencing rule |
| FinRL first-lane proof | Archived `APP-003-FINRL-DEFERRED-PREP-001` task plus `services/learning/rl/README.md` | RLlib is a follow-on lane, not the first RL execution proof |
| RLlib version-pinned baseline | `OSS_INTEGRATION_CHECKLIST.md`, `RESEARCH_BACKEND_MATURITY_MATRIX.md`, `DEFERRED_OSS_ACTIVATION_MAP.md` | Defines the truthful current state and the remaining gap to leave `version-pinned` |
| Future environment and search contract | `ENV_CONTRACT.md`, `PATH_DEFINITION.md` | Parent implementation should instantiate these contracts as repo-local artifacts rather than re-argue them in prose |
| Existing downstream consumer vocabulary | `services/evaluation/optimizers/contract.md` | Confirms `rl_policy` plus `rllib_ppo` naming already exists and should not drift |
| Ray Tune follow-on coupling | `ai-status.json`, `DEFERRED_OSS_ACTIVATION_MAP.md` | Ray Tune depends on RLlib and magnifies any wording drift or overclaim |

## 6. Open Cautions for Review

| Caution | Why it matters |
|---|---|
| Parent implementation is not RL reopen approval | The deferred-prep packet authorizes prep-only work; it does not satisfy the formal RL gate |
| `version-pinned` must remain the RLlib row truth after parent review | Prep-complete does not equal governed or activated RLlib |
| FinRL being done is sequencing permission, not activation permission | FinRL completion only clears the dependency edge; RLlib still must stay truthful about the closed RL gate |
| Local smoke must stay local and non-activating | A smoke scaffold should prove repo-local prep semantics, not become a governed production training claim |
| Adapter/schema/test surfaces must stay draft-only | Repo-local implementation now exists, but reviewer should still reject any wording that turns it into activation proof |
| Ray Tune should not be accelerated by RLlib wording drift | The downstream search lane inherits RLlib's boundary and should not be pulled forward by overclaim |

## 7. Scope Boundary - What Reviewer Should Reject

| Problematic move | Why it is wrong |
|---|---|
| Treating this sidecar packet as proof that RLlib prep work is already implemented | This helper only records acceptance and dependency framing |
| Claiming RLlib is now an active or governed production backend | Canonical truth still says deferred / `version-pinned` |
| Bypassing the FinRL-first sequencing rule | RLlib remains the follow-on lane even after FinRL closes its prep-only parent |
| Interpreting local smoke or schema work as RL gate reopen | Deferred prep does not supersede `RL_PATH_APPROVAL_GATE.md` |
| Using `ENV_CONTRACT.md` prose as a substitute for repo-local artifacts | Parent acceptance requires concrete adapter/schema/test surfaces |
| Using this helper task to rewrite canonical RL maturity truth | Sidecar scope is support material only |

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar adds only `support/sidecars/APP-003-RLLIB-DEFERRED-PREP-001/APP-003-RLLIB-DEFERRED-PREP-001-SIDECAR-ACCEPTANCE.md` |
| No canonical/runtime edits by sidecar | PASS | No L1 docs, runtime code, registry code, or governance files were changed in this helper slice |
| Parent acceptance is mapped to the current repo baseline | PASS | Sections 3 and 4 now reflect the landed scaffold plus current revalidation evidence |
| Dependency chain is explicit | PASS | Section 5 covers FinRL upstream truth, RLlib parent status, and Ray Tune downstream coupling |
| Deferred boundary is preserved | PASS | Sections 1, 3, 4, 6, and 7 keep RLlib prep-only, gate-closed, and `version-pinned` |

## 9. Handoff to Reviewer (`Codex2`)

This sidecar is ready for reviewer use as the acceptance/dependency packet for
`APP-003-RLLIB-DEFERRED-PREP-001`, which is now archived as `done`.

What it gives you now:

1. an acceptance map that distinguishes the landed prep-only scaffold from any
   governed or activated RLlib backend claim
2. an explicit dependency chain tying the parent task back to the closed RL
   gate, the completed FinRL prerequisite, and the downstream Ray Tune lane
3. wording guardrails that preserve RLlib as `version-pinned`, prep-only, and
   non-activating

Recommended reviewer stance now:

1. approve only repo-local deferred-prep scaffolding
2. require explicit non-default gating plus concrete local schema/smoke proof
3. reject any summary that turns prep work into RL gate reopen, active backend,
   or governed production training

---
*Generated by Codex as a sidecar `acceptance_packet` helper for
`APP-003-RLLIB-DEFERRED-PREP-001`. This file is a support artifact and does not
modify canonical truth.*
