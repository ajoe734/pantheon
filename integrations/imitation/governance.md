# imitation Integration — Governance Overlay

Last updated: 2026-04-15
Owner: BP5-OSS-003 (Codex)
Reviewer: Claude
Status: governed runtime boundary documented
Related task: `LP-002`

## 1. Governance Principle

> imitation may learn from governed trajectories. It may not bypass Pantheon's registry, approval, or execution gates.

The imitation adapter is a training-time path for behavior cloning only. It does not own
deployment stage, runtime execution, or live replacement semantics.

## 2. Input Governance

The trajectory adapter filters candidate sessions before any training happens.

Mandatory constraints:

- `actor_role` must be `operator` or `approver`
- `decision` must be `approve` or `edit`
- `target.promotion_state` must be `candidate` or `paper`
- each step must include a numeric observation vector plus an action label
- observations must keep consistent dimensionality across the governed dataset

Malformed or ineligible sessions are excluded and recorded in artifact metadata.

## 3. Output Governance

The imitation workflow emits a governed artifact bundle and a registry-ready behavior-policy entry.

Governed output rules:

- artifact type is `behavior_policy`
- artifact state starts at `draft`
- artifact family is `imitation_policy`
- lineage must include `source_dataset_refs`
- the registry entry remains descriptive until later promotion review
- emitted governance metadata marks `direct_live_influence` as false

That keeps LP-002 aligned with the registry gate instead of allowing direct live rollout.

## 4. Scope Guardrails

Only `Behavioral Cloning` is in scope for this governed baseline.

Explicitly deferred:

- `DAgger`
- `GAIL`
- `AIRL`
- preference-comparison workflows

If Pantheon later enables those algorithms, they need separate smoke evidence and a governance refresh.

## 5. Authority Boundary

The imitation integration never receives authority over:

- registry truth
- deployment-stage changes
- runtime-manager actions
- OpenClaw runtime orchestration
- LEAN execution
- rollback semantics

Its responsibility ends at packaging a governed learned-policy artifact and evaluation summary.

## 6. Upgrade Rules

When changing the version pin, backend behavior, or algorithm scope:

1. update `services/learning/imitation/requirements.txt` and `IMITATION_VERSION_PIN`
2. rerun `python3 services/learning/imitation/smoke_test.py`
3. rerun `python3 -m unittest discover -s services/learning/imitation -p 'test_*.py'`
4. update `integration.md`, this governance file, and `OSS_INTEGRATION_CHECKLIST.md`

Any future upstream backend run must preserve the same dataset filtering, draft-only lifecycle,
and registry-first authority boundary.
