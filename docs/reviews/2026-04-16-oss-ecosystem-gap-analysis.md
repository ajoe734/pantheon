# OSS Ecosystem Gap Analysis

Last updated: 2026-04-16
Prepared by: Codex
Scope: compare Pantheon's current OSS integration maturity against the full research/learning ecosystem implied by the canonical blueprint and current OSS integration documents

## 1. Executive Summary

The honest answer is: not all OSS ecosystems are fully integrated.

Pantheon has completed the canonical Phase 6 backlog rows:

- `OSS-001`
- `OSS-002`
- `OSS-003`

Phase5 also closed the follow-on execution wave:

- `BP5-OSS-001`
- `BP5-OSS-002`
- `BP5-OSS-003`
- `BP5-OSS-004`

That means the repo has already done the blueprint-required work for:

- source selection and pinning
- integration regrade
- deferred-path activation criteria
- first-wave executable follow-on closure

But that does not mean every named OSS backend is already a fully governed, smoke-tested, live-integrated execution path.

Current maturity split:

- Fully integrated / governed: `OpenClaw`, `DSPy`, `imitation`, `MLflow`
- Activation-ready but not fully integrated: `Qlib`, `TRL`, `FinRL`, `RLlib`, `Ray Tune`, `W&B`
- Not integrated / not started: `vectorbt`, `statsmodels`, `QuantLib`

So the gap for the next round is no longer "Phase 6 backlog missing." It is:

1. advance deferred frameworks from criteria/pin state into runnable governed adapters
2. materialize missing research-tool backends that still have no execution task at all
3. decide which optional ecosystems are truly in-scope for the next delivery wave, versus explicitly deferred again

## 2. Source Set Used

Primary sources:

- `ROADMAP.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md`
- `ai-task-archive/tasks/OSS-001.json`
- `ai-task-archive/tasks/OSS-002.json`
- `ai-task-archive/tasks/OSS-003.json`
- `ai-task-archive/tasks/BP5-OSS-001.json`
- `ai-task-archive/tasks/BP5-OSS-002.json`
- `ai-task-archive/tasks/BP5-OSS-003.json`
- `ai-task-archive/tasks/BP5-OSS-004.json`

Supporting implementation/evidence references:

- `integrations/openclaw/`
- `integrations/dspy/`
- `integrations/imitation/`
- `integrations/mlflow/`
- `services/learning/qlib/ACTIVATION_CRITERIA.md`
- `services/learning/trl/ACTIVATION_CRITERIA.md`
- `services/learning/rl/PATH_DEFINITION.md`
- `services/learning/rl/RL_PATH_APPROVAL_GATE.md`
- `services/registry/experiments/WANDB_ACTIVATION.md`

## 3. Canonical Phase 6 Truth

Canonical blueprint requirement in `ROADMAP.md` Phase 6:

- `OSS-001`: OpenClaw integration
- `OSS-002`: integration regrade
- `OSS-003`: deferred path criteria

Verification result:

- all three canonical Phase 6 rows exist in archive
- all three are terminal `done`
- phase5 also completed its four OSS follow-on tasks

So the correct statement is:

- **Phase 6 backlog completion: yes**
- **Full OSS ecosystem realization: no**

This distinction matters because the canonical blueprint required governance and entry-criteria honesty, not immediate production realization of every named framework.

## 4. Current OSS Maturity Map

### 4.1 Fully Integrated / Governed

These components now satisfy the practical bar for "real integration" inside the repo: pinned source/version, local adapter boundary, governed I/O, and smoke-tested or governed evidence.

| Component | Current Status | Why it counts as fully integrated |
|---|---|---|
| `OpenClaw` | `governed` | upstream source and runtime image pinned; adapter exists in `integrations/openclaw/adapter/`; compose/runtime path exists; baseline and live gateway smoke both refreshed on 2026-04-17 |
| `DSPy` | `governed` | pinned, runnable adapter in `services/learning/dspy/`, evidence pack and smoke coverage refreshed on 2026-04-17 |
| `imitation` | `governed` | pinned, runnable adapter in `services/learning/imitation/`, evidence pack and smoke coverage refreshed on 2026-04-17 |
| `MLflow` | `governed` | pinned, registry/experiment adapter exists, evidence pack and smoke coverage refreshed on 2026-04-17 |

Gap call:

- no major repo-side implementation gap remains for this group
- only maintenance and regression-proof refresh remain

### 4.2 Activation-Ready But Not Fully Integrated

These components are documented honestly, but they are not yet fully realized as governed executable backends.

| Component | Current Status | What is still missing |
|---|---|---|
| `Qlib` | `smoke-tested` | production activation requires ≥50 instruments, 2+ years data, and RS-003 replication gate pass; governed adapter and LightGBM smoke path completed in OSS-NEXT-001 (2026-04-17) |
| `TRL` | `smoke-tested` | production activation blocked on runtime data gates (≥200 FB-002 events, ≥100 preference pairs, active LP-002, ready downstream consumer); governed pair-construction adapter and DPO smoke path completed in OSS-NEXT-002 (2026-04-17) |
| `FinRL` | `criteria-defined` | RL approval gate passage; single-agent policy-output adapter; smoke test |
| `RLlib` | `version-pinned` / activation-gated | governed training/eval loop; adapter; smoke test |
| `Ray Tune` | `version-pinned` | governed search output adapter; smoke test coupled to selected RL path |
| `W&B` | `criteria-defined` | backend generalization beyond MLflow-first path; SDK pin; implementation and metadata-equivalence proof |

Gap call:

- this is the biggest real maturity gap in the OSS stack
- these rows are no longer vague, but they are still mostly "ready to start," not "already integrated"

### 4.3 Not Integrated / Not Started

These components are named in the research maturity picture but have no real task execution baseline yet.

| Component | Current Status | Missing baseline |
|---|---|---|
| `vectorbt` | `not-started` | source selection, version pin, adapter boundary, governed I/O, smoke test, task materialization |
| `statsmodels` | `not-started` | source selection, version pin, adapter boundary, governed I/O, smoke test, task materialization |
| `QuantLib` | `not-started` | source selection, version pin, adapter boundary, governed I/O, smoke test, task materialization |

Gap call:

- these are not execution gaps from an already-started lane
- these are planning/materialization gaps, because the next-wave tasks do not yet exist

## 5. Difference Analysis Against "Fully Integrated OSS Ecosystem"

If the target state is "all named OSS ecosystems are fully integrated," Pantheon is still short in four specific ways.

### 5.1 Deferred Frameworks Are Honest, But Still Deferred

`Qlib`, `TRL`, `FinRL`, `RLlib`, `Ray Tune`, and `W&B` now have explicit gates and better vocabulary, but that is still one maturity layer below full integration.

Difference:

- current state: criteria, pin, or activation map exists
- target state: runnable adapter, governed output path, smoke-tested evidence

### 5.2 Research Coverage Is Still Uneven

Pantheon has strong governed coverage for:

- persona optimization
- behavior cloning
- experiment registry
- orchestration

But still lacks equivalent realized coverage for:

- supervised alpha discovery through `Qlib`
- preference learning through `TRL`
- RL policy training through `FinRL` / `RLlib`
- governed hyperparameter search through `Ray Tune`
- optional experiment backend parity through `W&B`
- rapid backtesting through `vectorbt`
- econometrics / regime analysis through `statsmodels`
- derivatives analytics through `QuantLib`

Difference:

- current research path is real but narrow
- full blueprint ecosystem implies a wider research tool envelope

### 5.3 Some Frameworks Have Criteria But No Next-Wave Closure

For several rows, the repo already knows the gate but has no explicit next-wave task carrying them forward.

Most obvious cases:

- `vectorbt`
- `statsmodels`
- `QuantLib`

Difference:

- current state: recognized in maturity matrix
- target state: named backlog rows or explicit next-wave tasks exist

### 5.4 Optional vs Required Scope Is Still Under-Specified

Some ecosystems are clearly optional or gated behind operator need:

- `W&B`
- some RL stack work

But the repo does not yet clearly freeze which of these belong in the very next wave versus a later research-expansion wave.

Difference:

- current state: technically described, but prioritization still soft
- target state: explicit next-wave inclusion/defer decision per ecosystem

## 6. Recommended Next-Wave Task Inventory

The cleanest way to carry this into the next development round is to split the work into three buckets.

### 6.1 Wave A: Advance Activation-Ready Frameworks Into Real Integration

These are the best candidates for the immediate next round because they already have criteria and partial setup.

1. `OSS-NEXT-001 Qlib adapter realization` — **DONE** (2026-04-17)
   delivered: governed data-handler adapter, registry-compatible output shape, LightGBM-first smoke test (13 unit tests + smoke assertions OK)
2. `OSS-NEXT-002 TRL activation baseline` — **DONE** (2026-04-17)
   delivered: package pin (`trl>=0.8.0,<0.10.0`), preference-pair pipeline (`GovernedPreferencePairAdapter`), DPO smoke test (16 unit tests + smoke assertions OK), governed artifact boundary (`artifact_state=draft`, `deployment_stage=none`)
3. `OSS-NEXT-003 RL path activation gate closure`
   deliver: explicit decision whether RL work enters this wave; if yes, pass approval gate and pick `FinRL` or `RLlib` as the first concrete lane
4. `OSS-NEXT-004 W&B backend parity decision`
   deliver: either explicit defer with reason and re-entry gate, or backend generalization task plus metadata-equivalence proof plan

### 6.2 Wave B: Materialize Missing Research Backends

These are planning gaps first, implementation gaps second.

1. `OSS-NEXT-005 vectorbt task materialization`
   deliver: source selection, version pin, governed adapter design, smoke-test plan
2. `OSS-NEXT-006 statsmodels task materialization`
   deliver: source selection, regime/econometrics use-case binding, governed adapter design, smoke-test plan
3. `OSS-NEXT-007 QuantLib task materialization`
   deliver: source selection, derivatives pricing scope, governed adapter design, smoke-test plan

### 6.3 Wave C: Maintenance / Regression-Proof Refresh For Governed Paths

These should not be confused with major new integration work, but they still belong in the next-round quality bar.

1. `OSS-NEXT-008 governed-path regression refresh`
   scope: `OpenClaw`, `DSPy`, `imitation`, `MLflow`
   deliver: refreshed smoke evidence and no-regression verification after recent repo changes

## 7. Suggested Prioritization

If the next round cannot do everything at once, the recommended order is:

1. `Qlib`
2. `TRL`
3. `vectorbt / statsmodels / QuantLib` task materialization
4. RL stack decision (`FinRL` / `RLlib` / `Ray Tune`)
5. `W&B`

Rationale:

- `Qlib` and `TRL` are already the closest to activation and expand the real research surface fastest
- `vectorbt`, `statsmodels`, and `QuantLib` are currently pure planning debt and should stop being invisible
- RL and W&B both have stronger conditionality and should not accidentally balloon the next wave unless explicitly approved

## 8. Acceptance Bar For Calling The OSS Ecosystem "Fully Integrated"

Pantheon should only claim "all OSS ecosystems are fully integrated" after all named components satisfy:

1. upstream source selected
2. version pinned
3. dependency/repo path added
4. local adapter implemented
5. governed I/O boundary proven
6. smoke test passed
7. evidence pack committed

By that bar, the current repo is not there yet.

## 9. Final Call

The next development round should not reopen canonical Phase 6 semantics. That work is done.

What it should do is:

- convert the deferred-but-defined frameworks into real executable integrations
- materialize the still-unstarted research-tool ecosystems into named tasks
- explicitly decide which optional ecosystems remain out-of-wave

That is the real OSS difference still left between "Phase 6 complete" and "OSS ecosystem fully integrated."

## 2026-04-17 Follow-On Note

`OSS-NEXT-006` has now materialized the `statsmodels` backend into a named,
repo-local execution-ready baseline:

- use-case binding: econometrics and regime research only
- source selected: `statsmodels/statsmodels`
- version pinned: `statsmodels==0.14.2`
- local baseline docs: `services/research/statsmodels/ACTIVATION_CRITERIA.md`,
  `services/research/statsmodels/requirements.txt`,
  `integrations/statsmodels/integration.md`

This closes the "invisible planning debt" portion for `statsmodels`, but does
not claim a runnable adapter or smoke-tested integration yet.

`OSS-NEXT-007` has now materialized the `QuantLib` backend into a named,
repo-local execution-ready baseline:

- use-case binding: derivatives pricing and risk analytics (options pricing via
  Black-Scholes/Heston, Greeks, yield curve construction, fixed income analytics)
- source selected: `lballabio/QuantLib` (Python bindings: `QuantLib-Python`)
- version pinned: `QuantLib-Python==1.18`
- local baseline docs: `services/research/quantlib/ACTIVATION_CRITERIA.md`,
  `services/research/quantlib/requirements.txt`,
  `integrations/quantlib/integration.md`

This closes the "invisible planning debt" portion for `QuantLib`, but does
not claim a runnable adapter or smoke-tested integration yet.
