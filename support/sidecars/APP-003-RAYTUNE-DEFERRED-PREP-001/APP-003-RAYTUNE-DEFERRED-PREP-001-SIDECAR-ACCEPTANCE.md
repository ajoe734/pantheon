# APP-003-RAYTUNE-DEFERRED-PREP-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `APP-003-RAYTUNE-DEFERRED-PREP-001`
**Parent Owner**: `Codex`
**Parent Reviewer**: `Codex2`
**Parent Status**: `done` (archived 2026-04-25; terminal outcome `completed`)
**Sidecar Task**: `APP-003-RAYTUNE-DEFERRED-PREP-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-25`
**Refresh State**: `re-verified on 2026-04-27 UTC after review_ready_dispatch; local proof commands re-run before sidecar reviewer closeout`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 policy, canonical
> deferred truth, runtime behavior, registry/governance implementations, or the
> parent execution record. It packages a reviewer-facing acceptance snapshot,
> dependency map, and scope boundary for the landed Ray Tune deferred-prep
> review task.

## 1. Executive Summary

`APP-003-RAYTUNE-DEFERRED-PREP-001` has been archived as `done` with `Codex`
as owner and `Codex2` as reviewer. This sidecar acceptance packet remains in
`review` with `Codex2` and now refreshes the support-only closeout evidence
against the archived parent. The live repo contains the deferred-prep Ray Tune
search-output lane under `services/research/rllib/`: a governed search-space
schema, result adapter, explicit non-default gate, offline `optimizer_result`
draft workflow, projected candidate outputs, worker entrypoint, smoke path, and
unit coverage.

Revalidation on 2026-04-27 UTC still shows the lane is prep-only,
non-default, draft-only, and gate-closed. This sidecar therefore acts as the
reviewer-facing acceptance and dependency packet for sidecar closeout. It
preserves the canonical statement that `Ray Tune` remains
`version-pinned`, the RL path remains closed, and the broader RLlib + Tune lane
still follows the FinRL-first activation sequence.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable truth for parent/sidecar ownership, lifecycle, and dependency edges |
| `.orchestrator/task-briefs/app_003_raytune_deferred_prep_001_sidecar_acceptance.md` | Confirms this helper slice is support-only and limited to acceptance material |
| `docs/reviews/2026-04-25-deferred-prep-execution-packet.md` | Authorizes the narrow deferred-prep exception for Ray Tune in this wave |
| `docs/reviews/2026-04-25-app-003-raytune-deferred-prep-001-codex-handoff.md` | Main review handoff packet with implementation surface and declared verification |
| `OSS_INTEGRATION_CHECKLIST.md` | Canonical Ray Tune row truth remains `version-pinned` |
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Cross-backend truth that Ray Tune now has a repo-local prep scaffold but remains gate-closed and non-activated |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | Summarizes what RLlib + Ray Tune now have repo-locally versus what still blocks activation |
| `services/learning/rl/README.md` | Records the RL closed-state, the deferred-prep exception, and the FinRL-first sequencing rule |
| `services/learning/rl/RL_PATH_APPROVAL_GATE.md` | Defines the real RL reopen gate and states Ray Tune remains prep-only beyond repo-local deferred prep |
| `services/research/rllib/README.md` | Repo-local summary of the landed RLlib + Ray Tune prep-only lane |
| `services/research/rllib/adapter/ray_tune_adapter.py` | Governed Ray Tune search-space/result adapter, draft-only workflow, and gate enforcement |
| `services/research/rllib/config.py` | Shows Ray Tune backend selection remains explicit, offline-safe, and non-default |
| `services/research/rllib/ray_tune_smoke_test.py` | Explicit CLI-gated smoke path proving draft-only output semantics |
| `services/research/rllib/ray_tune_worker.py` | Deferred-prep worker entrypoint gated by `PANTHEON_RAYTUNE_PREP_ENABLED` |
| `services/research/rllib/test_ray_tune_adapter.py` | Unit coverage for search config validation, draft workflow, gate, and import-boundary behavior |
| `services/research/rllib/requirements.txt` | Shows the actual Ray Tune and RLlib package pins |
| `services/research/rllib/Dockerfile` | Shows the shared prep-only research container remains non-activating |

## 3. Repo-Current Truth Snapshot

| Truth item | Repo evidence | Review implication |
|---|---|---|
| Parent task is archived `done` | `python3 scripts/ai_status.py show APP-003-RAYTUNE-DEFERRED-PREP-001` resolves to `ai-task-archive/tasks/APP-003-RAYTUNE-DEFERRED-PREP-001.json` with terminal status `done`, owner `Codex`, reviewer `Codex2`, and terminal outcome `completed` | This packet now supports sidecar reviewer closeout against the archived parent rather than changing parent execution truth |
| RLlib prerequisite is already closed out | `python3 scripts/ai_status.py show APP-003-RLLIB-DEFERRED-PREP-001` resolves to the archive with terminal status `done` | Ray Tune no longer waits on RLlib execution, but it still inherits the RL closed-state and FinRL-first boundary |
| Ray Tune row remains `version-pinned` | `OSS_INTEGRATION_CHECKLIST.md` and `RESEARCH_BACKEND_MATURITY_MATRIX.md` keep Ray Tune formally deferred at `version-pinned` even after the scaffold landed | Parent work must not upgrade canonical maturity or imply activation |
| Deferred-prep work is explicitly bounded | `docs/reviews/2026-04-25-deferred-prep-execution-packet.md` allows search-space schema, result adapter, offline artifact format, and local smoke fixtures only | Review must reject production optimization claims or RL gate reopen |
| Ray Tune and RLlib pins are present | `services/research/rllib/requirements.txt` pins `ray[tune]>=2.9.0,<3.0.0` and `ray[rllib]>=2.9.0,<3.0.0` | The version-pin baseline remains explicit after the scaffold landed |
| Repo-local adapter, worker, and smoke path now exist | `services/research/rllib/` now contains `adapter/ray_tune_adapter.py`, `ray_tune_worker.py`, `ray_tune_smoke_test.py`, `test_ray_tune_adapter.py`, config, README, sample input, Dockerfile, and requirements | Parent acceptance criteria 1 and 2 now have executable evidence to review |
| Revalidation remains draft-only and gate-closed | `python3 -m pytest services/research/rllib/test_ray_tune_adapter.py -q`, `python3 services/research/rllib/ray_tune_smoke_test.py --enable-deferred-prep`, `PANTHEON_RAYTUNE_PREP_ENABLED=1 python3 services/research/rllib/ray_tune_worker.py`, and `python3 services/research/rllib/ray_tune_worker.py` all reproduce the expected prep-only behavior on 2026-04-25 UTC | Reviewer can validate the landed scaffold without inferring activation |
| Search-output artifact vocabulary is now instantiated repo-locally | `services/research/rllib/adapter/ray_tune_adapter.py` emits canonical repo-local `optimizer_result` draft envelopes plus projected candidate outputs | Parent no longer relies on prose-only design evidence for the Ray Tune path |
| RL README preserves the search boundary | `services/learning/rl/README.md` still frames Ray Tune as the follow-on search path after FinRL proof and RL reopen | Parent wording must stay careful because it inherits the broader RL sequencing rule |
| Backend selection stays offline-safe | `services/research/rllib/config.py` restricts `PANTHEON_RAYTUNE_BACKEND` to `stub` or `tune` and explicitly says the lane stays offline and non-default | No default runtime path was silently changed |

## 4. Parent Acceptance Checklist

Review the parent against the repo state that exists today after implementation
and revalidation, while preserving the deferred-prep and non-activation
boundary.

| Parent acceptance target | Reviewer should require | Status now |
|---|---|---|
| Ray Tune deferred prep scaffold lands behind the RL gate | A repo-local Ray Tune search-output adapter and worker/smoke scaffold exist behind explicit opt-in gating; the RL path remains closed by default; no default production optimization path is exposed | PASS |
| Search output schema and offline tuning fixtures land without activation claim | Concrete search-space schema, result adapter, draft-only `optimizer_result` workflow, candidate packet projection, smoke path, and unit tests prove non-activating local behavior | PASS |
| Canonical docs and packet preserve version-pinned deferred truth | Implementation packet, RL docs, and canonical status docs keep Ray Tune `version-pinned`, gate-closed, and sequenced after the FinRL-first proof | PASS |

### Verification Notes

This sidecar refresh is documentation-only, but it revalidates the landed
Ray Tune deferred-prep evidence surface.

Checks re-run for this packet on `2026-04-27 UTC`:

1. `python3 scripts/ai_status.py show APP-003-RAYTUNE-DEFERRED-PREP-001`
2. `python3 scripts/ai_status.py show APP-003-RLLIB-DEFERRED-PREP-001`
3. `python3 -m pytest services/research/rllib/test_ray_tune_adapter.py -q`
4. `python3 -m pytest services/research/rllib/test_adapter.py -q`
5. `python3 services/research/rllib/ray_tune_smoke_test.py --enable-deferred-prep`
6. `PANTHEON_RAYTUNE_PREP_ENABLED=1 python3 services/research/rllib/ray_tune_worker.py`
7. `python3 services/research/rllib/ray_tune_worker.py`
8. `rg -n "Ray Tune|ray tune|ray_tune|version-pinned|optimizer_result|does_not_reopen_rl_gate|PANTHEON_RAYTUNE_PREP_ENABLED" OSS_INTEGRATION_CHECKLIST.md RESEARCH_BACKEND_MATURITY_MATRIX.md services/learning/DEFERRED_OSS_ACTIVATION_MAP.md services/learning/rl/README.md services/learning/rl/RL_PATH_APPROVAL_GATE.md services/research/rllib/README.md services/research/rllib/config.py services/research/rllib/Dockerfile services/research/rllib/requirements.txt`

Observed outputs on 2026-04-27:

- `pytest services/research/rllib/test_ray_tune_adapter.py -q` => `12 passed`
- `pytest services/research/rllib/test_adapter.py -q` => `13 passed`
- smoke path reports `backend = stub_ray_tune`, `artifact_type = optimizer_result`,
  `artifact_state = draft`, `deployment_stage = none`, `candidate_next_state = candidate`,
  `gate_state = closed`, `output_artifacts = 3`
- enabled worker emits `backend = stub_ray_tune`, `search_strategy = pbt`, `num_trials = 16`,
  `best_trial_id = trial-016`
- disabled worker exits with the expected explicit gate failure

## 5. Dependency Map

### 5.1 Durable Task Dependencies

| Task | Relationship | Current read |
|---|---|---|
| `APP-003-RLLIB-DEFERRED-PREP-001` | upstream dependency | Archived `done`; RLlib deferred-prep parent lane that Ray Tune explicitly depends on |
| `APP-003-RAYTUNE-DEFERRED-PREP-001` | parent task | Mainline Ray Tune deferred-prep implementation task, archived `done` on 2026-04-25 |
| `APP-003-RAYTUNE-DEFERRED-PREP-001-SIDECAR-ACCEPTANCE` | support helper | Reviewer-facing packet only; still in `review`; does not change canonical truth |

### 5.2 Semantic Dependency Chain

| Dependency | Source | Why it matters |
|---|---|---|
| Deferred-prep execution exception | `docs/reviews/2026-04-25-deferred-prep-execution-packet.md` | Authorizes narrow scaffold work while keeping RL non-activated |
| RL closed-state governance | `services/learning/rl/README.md`, `RL_PATH_APPROVAL_GATE.md` | Keeps Ray Tune behind the RL gate and under the FinRL-first sequencing rule |
| FinRL first-lane proof | `services/learning/rl/README.md`, `RL_PATH_APPROVAL_GATE.md`, `DEFERRED_OSS_ACTIVATION_MAP.md` | Ray Tune remains a follow-on lane, not the first RL execution proof |
| RLlib prerequisite lane | Archived `APP-003-RLLIB-DEFERRED-PREP-001` task plus `services/research/rllib/` | Ray Tune inherits the shared container, dataset, and RLlib-coupled deferred wording |
| Ray Tune version-pinned baseline | `OSS_INTEGRATION_CHECKLIST.md`, `RESEARCH_BACKEND_MATURITY_MATRIX.md`, `DEFERRED_OSS_ACTIVATION_MAP.md` | Defines the truthful current state and the remaining gap to leave `version-pinned` |
| Search-space and artifact contract | `services/learning/rl/README.md`, `PATH_DEFINITION.md`, `adapter/ray_tune_adapter.py` | Parent implementation should instantiate the search-output contract as repo-local artifacts rather than re-argue it in prose |

## 6. Open Cautions for Review

| Caution | Why it matters |
|---|---|
| Parent implementation is not RL reopen approval | The deferred-prep packet authorizes prep-only work; it does not satisfy the formal RL gate |
| `version-pinned` must remain the Ray Tune row truth after parent review | Prep-complete does not equal governed or activated Ray Tune |
| Search output must stay `optimizer_result` + `artifact_state = draft` only | Draft artifact semantics are the key evidence that this is repo-local prep rather than an activation claim |
| Candidate packet projection is still offline-only | `candidate_next_state = candidate` is a projection packet, not an approved promotion |
| Backend selection must stay non-default | `stub` remains the safe default; import-ready `tune` is a boundary check, not a production backend claim |
| Shared RLlib + Ray Tune wording must not drift | Ray Tune shares the same deferred container/path and could overclaim activation if the reviewer relaxes the RL boundary language |

## 7. Scope Boundary - What Reviewer Should Reject

| Problematic move | Why it is wrong |
|---|---|
| Treating this sidecar packet as proof that the real governed optimization lane is open | This helper only records acceptance and dependency framing |
| Claiming Ray Tune is now an active or governed production backend | Canonical truth still says deferred / `version-pinned` |
| Translating smoke or worker success into RL gate reopen | Deferred prep does not supersede `RL_PATH_APPROVAL_GATE.md` |
| Promoting draft `optimizer_result` evidence into candidate/paper/live semantics | The current packet proves only local draft generation plus candidate projection |
| Assuming RLlib completion makes Ray Tune activation-ready in production terms | RLlib closeout clears the dependency edge, not the RL approval gate |
| Using this helper task to rewrite canonical RL maturity truth | Sidecar scope is support material only |

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar adds only `support/sidecars/APP-003-RAYTUNE-DEFERRED-PREP-001/APP-003-RAYTUNE-DEFERRED-PREP-001-SIDECAR-ACCEPTANCE.md` |
| No canonical/runtime edits by sidecar | PASS | No L1 docs, runtime code, registry code, or governance files were changed in this helper slice |
| Parent acceptance is mapped to the current repo baseline | PASS | Sections 3 and 4 reflect the landed scaffold plus current revalidation evidence |
| Dependency chain is explicit | PASS | Section 5 covers RLlib upstream truth and the broader RL closed-state sequencing |
| Deferred boundary is preserved | PASS | Sections 1, 3, 4, 6, and 7 keep Ray Tune prep-only, gate-closed, and `version-pinned` |

## 9. Handoff to Reviewer (`Codex2`)

This sidecar is ready for `Codex2` reviewer closeout as the
acceptance/dependency packet for the archived parent
`APP-003-RAYTUNE-DEFERRED-PREP-001`.

What it gives you now:

1. an acceptance map that separates the landed prep-only scaffold from any
   governed or activated Ray Tune backend claim
2. an explicit dependency chain tying the parent task back to the closed RL
   gate, the completed RLlib prerequisite, and the FinRL-first sequencing rule
3. fresh verification notes showing the search-output path is still
   `optimizer_result`, `draft`, non-default, and gate-closed

Recommended reviewer stance now:

1. approve only repo-local deferred-prep scaffolding
2. require explicit non-default gating plus concrete local schema/smoke proof
3. reject any summary that turns prep work into RL gate reopen, active backend,
   or governed production optimization

---
*Generated by Codex as a sidecar `acceptance_packet` helper for
`APP-003-RAYTUNE-DEFERRED-PREP-001`. This file is a support artifact and does
not modify canonical truth.*
