# services/learning/imitation

**Purpose**: Integrate governed trader-behavior cloning workflows into Pantheon using the upstream `imitation` library.

**Task**: LP-002  
**Owner**: Codex  
**Reviewer**: Copilot  
**Status**: IMPLEMENTING

## What ships in v1

LP-002 now has a concrete local adapter path instead of only a spike:

- governed trajectory filtering from FB-001-style linkage metadata
- BC-first training abstraction with a deterministic stub backend for CI
- optional upstream `imitation` backend for real worker execution
- registry-ready `behavior_policy` packaging with lineage and checksum metadata
- smoke test and unit tests under the same service directory

This service is intentionally BC-only in v1. `DAgger`, `GAIL`, and `AIRL` remain deferred.

## Input boundary

The adapter consumes a governed trajectory dataset shaped like:

```json
{
  "dataset_id": "traj-approved-2026-04-06",
  "strategy_id": "alpha-mean-reversion",
  "source_dataset_refs": ["dataset://feedback/approved/2026-04-06"],
  "source_strategy_spec_id": "strat-alpha-mean-reversion-v2",
  "sessions": [
    {
      "trajectory_id": "traj-001",
      "actor_id": "trader-01",
      "actor_role": "operator",
      "decision": "approve",
      "target": {
        "registry_id": "reg-alpha-1",
        "strategy_id": "alpha-mean-reversion",
        "artifact_version": "1.2.0",
        "artifact_type": "strategy_spec",
        "promotion_state": "candidate"
      },
      "steps": [
        {
          "observation": [0.9, 0.1, -0.2],
          "action": "buy_small",
          "reward": 0.3,
          "feedback_event_id": "evt-001"
        }
      ]
    }
  ]
}
```

Governance filters are strict:

- `actor_role` must be `operator` or `approver`
- `decision` must be `approve` or `edit`
- `target.promotion_state` must be `candidate` or `paper`
- every step must include a numeric observation vector and action label

Rejected, ambiguous, or malformed sessions are excluded and recorded in the artifact metadata.

## Output boundary

`run_imitation_workflow()` emits two governed objects:

1. `artifact_bundle`
   - `artifact_family=imitation_policy`
   - `algorithm=behavior_cloning`
   - dataset summary, governance filters, policy payload, evaluation summary
2. `registry_entry`
   - `artifact_type=behavior_policy`
   - `artifact_state=draft`
   - `metadata.model_family=imitation_policy`
   - lineage back to `source_dataset_refs` and the generated training run id

The default artifact state is `draft`; `lifecycle_state=draft` is still emitted as a legacy
compatibility hint during the registry migration window.

That keeps LP-002 aligned with REG-001 and avoids bypassing promotion gates. A future worker can
promote the artifact to `candidate` only after automated evaluation passes.

## Backends

- `StubBehaviorCloningBackend`
  - deterministic nearest-centroid learner
  - used by tests and default smoke flow
  - proves packaging, lineage, and governance logic without optional dependencies
- `ImitationBehaviorCloningBackend`
  - optional real backend built on `HumanCompatibleAI/imitation`
  - requires `services/learning/imitation/requirements.txt`
  - meant for dedicated worker/runtime use

## Commands

Local smoke path:

```bash
python3 services/learning/imitation/smoke_test.py
```

Optional upstream backend smoke path after installing dependencies:

```bash
python3 services/learning/imitation/smoke_test.py --backend imitation
```

Unit tests:

```bash
python3 -m unittest discover -s services/learning/imitation -p 'test_*.py'
```

## Why this satisfies the LP-002 audit gap

`AUD-CODEX-001` called out three missing pieces for LP-002:

1. package pin
2. adapter from governed trajectories
3. BC smoke test

This directory now provides all three in concrete form, while keeping live execution isolated from
learned imitation artifacts until the registry/promotion path approves them.
