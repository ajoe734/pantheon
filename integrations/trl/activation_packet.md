# TRL Production Activation Packet

Last updated: 2026-04-24
Owner: APP-003-TRL-ACTIVATION-001 (Codex2)
Reviewer: Codex
Status: prepared for first governed DPO activation; runtime-gated

## 1. Purpose

This packet is the reviewable activation surface for the `TRL` row after the
adapter and smoke baseline landed under `OSS-NEXT-002`.

It does two things:

1. records the current activation truth in one place
2. defines the exact evidence bundle required before the first governed
   production DPO run may start

This packet does **not** claim that TRL is production-activated today. It
formalizes that the remaining blockers are runtime-data and downstream-readiness
gates, not missing repo-local adapter code.

## 2. Current Disposition

Current row status remains `smoke-tested`.

Repo-local truth as of 2026-04-29:

- the governed TRL adapter exists at `services/learning/trl/adapter/trl_adapter.py`
- the non-writing pre-activation preflight scaffold exists at
  `services/learning/trl/preflight.py`
- the default smoke path still passes via `python3 services/learning/trl/smoke_test.py`
- unit coverage still passes via
  `python3 -m unittest discover -s services/learning/trl -p 'test_*.py'`
- production activation is still blocked on runtime gates from
  `services/learning/trl/ACTIVATION_CRITERIA.md §1`

The gate is therefore cleared only in the truthful sense:

- all repo-local code gates are closed
- the first governed DPO activation packet is now prepared
- the actual production DPO run must wait until the runtime gates are proven

## 3. Activation Gate Read

| Activation criterion | Current read | Evidence | Gap to close |
|---|---|---|---|
| ≥200 governed FB-002 events spanning ≥2 strategy families and all 3 action types | blocked | `services/learning/trl/ACTIVATION_CRITERIA.md §1.1`; `services/feedback/store.py` only provides a generic append-only in-memory store and this repo contains no runtime evidence file proving the threshold | attach a governed FB-002 evidence snapshot with event counts, strategy-family split, and action distribution |
| ≥100 valid preference pairs constructable from those events | blocked | the smoke path in `integrations/trl/smoke_test.md` proves only a 5-pair synthetic dataset; no repo-local runtime pair-volume evidence exists | attach a pair-construction summary derived from real governed FB-002 events, including dedup and linkage checks |
| LP-002 imitation baseline active with approved artifacts | blocked | `services/learning/imitation/README.md` proves the imitation adapter baseline exists, but the current repo evidence does not name a live `artifact_state=approved` imitation artifact that TRL can depend on | cite the approved LP-002 registry artifact(s) and the activation evidence that keeps them active |
| Baseline preference-model performance documented before DPO activation | blocked | `services/learning/trl/ACTIVATION_CRITERIA.md §1.4` requires a logistic/GBT baseline at accuracy ≥0.65 and AUC-ROC ≥0.70; the smoke result is only a stub proof (`accuracy: 0.6`) and is not the required runtime baseline | attach baseline experiment evidence with holdout metrics and source dataset window |
| At least one downstream consumer ready (EV-001, LP-005, or LP-001) | blocked | `services/learning/trl/EV-001_INTEGRATION.md` and evaluator contracts prove the intended integration shape, but they do not prove an active runtime consumer wired to load a preference model today; `services/learning/rl/README.md` keeps RL deferred | cite one concrete consumer lane, the consuming contract path, and the readiness evidence for that lane |
| No upstream dependency conflicts | satisfied | `services/learning/trl/requirements.txt`, `integrations/trl/integration.md`, and the passing smoke/unit baselines show the pinned package path is compatible with the current governed stack | keep this verified when dependency pins change |

## 4. First Governed DPO Activation Bundle

Before the first production TRL run starts, the owner should attach all of the
following evidence to the execution/review lane:

1. FB-002 evidence snapshot
   - total governed event count
   - strategy-family coverage
   - approve/edit/reject distribution
   - source query window
2. preference-pair dataset summary
   - number of valid pairs after governance filtering
   - dedup rule applied
   - artifact-linkage completeness
   - operator count
3. imitation prerequisite proof
   - approved LP-002 artifact registry ID(s)
   - activation date / active stage proof
4. baseline-model proof
   - baseline model type
   - holdout accuracy
   - AUC-ROC
   - dataset window and strategy-family coverage
5. downstream consumer proof
   - chosen consumer lane (`EV-001`, `LP-001`, or `LP-005`)
   - exact consuming contract or worker path
   - why that consumer is ready to ingest a `preference_model`

The owner should run `run_trl_preflight()` from `services/learning/trl/preflight.py`
against the evidence bundle before invoking `TRLDPOBackend`. The preflight only
reports gate state; it does not import the DPO adapter and does not write registry,
governance, or canonical collaboration state.

The governed output target remains unchanged:

- workflow entrypoint: `run_trl_dpo_workflow()`
- artifact family: `trl_preference_model`
- registry artifact type: `model_artifact`
- initial registry state: `artifact_state=draft`
- deployment stage: `deployment_summary.current_stage=none`
- lifecycle for preference models: `draft` → `candidate` → `approved` → `retired`

## 5. Verification Snapshot

Revalidated in this session on 2026-04-29:

1. `python3 services/learning/trl/smoke_test.py`
   - Result: passed
   - Dataset: 5 synthetic preference pairs, 2 strategy families, 2 operators
   - Output confirms `artifact_state=draft`, `deployment_stage=none`, and
     governed storage under `learning/trl/`
2. `python3 -m unittest discover -s services/learning/trl -p 'test_*.py'`
   - Result: 29 tests passed

These checks prove the adapter is still runnable and governance-safe. They do
not satisfy the runtime activation thresholds by themselves.

## 6. Disposition

`TRL` should remain `smoke-tested` in `OSS_INTEGRATION_CHECKLIST.md`.

The truthful next action is:

1. accumulate and prove the FB-002 + preference-pair thresholds
2. cite an active approved imitation baseline
3. attach a baseline-model evidence bundle
4. name one ready downstream consumer
5. run the first governed DPO activation through `TRLDPOBackend`

Until those five items exist, the row is prepared for activation but still
blocked from production use.
