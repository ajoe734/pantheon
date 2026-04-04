# OSS Integration Audit

Last updated: 2026-04-02
Status: corrected interpretation of external open-source components referenced by the target architecture

## 1. Conclusion

Yes: several parts of the current planning were too conceptual.

When the architecture names a real upstream project such as `OpenClaw`, `DSPy`, `Qlib`, `FinRL`, `TRL`, `imitation`, `RLlib`, `Tune`, `MLflow`, or `W&B`, the default assumption should be:

- integrate the upstream repo or package
- pin a version
- add adapters, contracts, and governance wrappers around it
- do not treat the named project as a placeholder that we are silently re-implementing from scratch

The biggest correction is `OpenClaw`.

`OpenClaw` should be treated as an upstream orchestration/runtime project to integrate or extend, not as a label for a brand-new local orchestration framework unless we explicitly choose to replace it.

## 2. Current Repo Reality

What is actually present right now:

- `LEAN` is already the host repo and execution engine
- local collaboration/status/dashboard tooling is present
- local contracts and schemas are present
- `tools/openclaw-local/` exists, but this is a **local desktop automation helper**, not the upstream OpenClaw repo

What is **not** present right now:

- upstream `OpenClaw` source tree or package integration
- installed or vendored `DSPy`
- installed or vendored `TRL`
- installed or vendored `Qlib`
- installed or vendored `FinRL`
- installed or vendored `imitation`
- installed or vendored `MLflow`
- installed or vendored `W&B`
- actual integration code for `RLlib` / `Tune`

Local scan summary:

- no git submodules are configured
- no upstream vendor directories for those projects exist in this repo
- only one partial signal exists for learning infra: `DockerfileLeanFoundationARM` already pins `ray[tune]` and `ray[rllib]`, but no governed adapter path has been built on top of them

## 3. Correct Classification

| Component | What it really is | Current repo state | Correct dev model |
|---|---|---|---|
| `LEAN` | execution engine, already present in this repo | present | build local execution adapters and governed loaders on top of existing repo |
| `OpenClaw` | upstream orchestration/runtime project | not integrated | integrate upstream project, then add local governance/config/adapters |
| `DSPy` | upstream Python framework | not integrated | add dependency, version pin, adapter layer, optimizer/eval glue |
| `TRL` | upstream Python framework | not integrated | add dependency only when preference-learning loop is ready |
| `Qlib` | upstream research framework | not integrated | add dependency or external worker image, then normalize outputs into local contracts |
| `FinRL` | upstream RL framework | not integrated | integrate only after governed RL path exists |
| `Ray RLlib / Tune` | upstream RL/search stack | partially pinned in one Dockerfile only | build explicit governed adapter path before considering it integrated |
| `imitation` | upstream imitation-learning library | not integrated | add dependency and map governed trajectory schema into its training inputs |
| `MLflow` | upstream experiment/registry tooling | not integrated | connect local registry metadata into MLflow runs/artifacts, not replace local governance |
| `W&B` | upstream experiment/registry tooling and SaaS | not integrated | optional backend for experiment lifecycle metadata |

## 4. What This Means for Existing Work

Some completed tasks are still useful, but their meaning must be corrected.

### Useful but misinterpreted

- `OC-001`
- `OC-003`
- `REG-001`
- `FB-001`
- `LP-001` contract draft

These are still valuable as **local contracts, adapter boundaries, and governance constraints**.
They are **not** proof that the upstream framework integration is done.

### Most important correction

`OC-*` tasks should now be read as:

- `OC-001`: define how local governance rules map onto upstream OpenClaw tool permissions
- `OC-002`: configure or extend upstream OpenClaw workflows for ingest/review/retrain/deploy
- `OC-003`: define the adapter objects that map upstream OpenClaw outputs into local `StrategySpec` and handoff objects

They should **not** be read as:

- re-implement the full OpenClaw orchestration stack from scratch inside this repo

### Learning tasks also need correction

`LP-*`, `RS-*`, and part of `EV-*` must begin with actual upstream integration work:

- install or package the framework
- pin versions
- add adapter code
- define governed I/O boundaries
- add smoke tests proving the upstream project is reachable and producing expected artifacts

Writing only contracts is not enough to call those tasks integrated.

## 5. New Rule for This Repo

When a planning document names a real upstream OSS project, assume one of these paths explicitly:

1. **Integrate upstream**
   - clone/package/install upstream
   - adapt it
   - wrap it in local governance

2. **Deliberately replace upstream**
   - only if we say so clearly in writing
   - the replacement must be intentional, not accidental drift

If neither is written down, the task is underspecified.

## 6. Immediate Corrections to the Roadmap

Before more feature work continues, the roadmap should be interpreted this way:

1. `OpenClaw` work is upstream integration work, not greenfield orchestration implementation
2. `DSPy`, `Qlib`, `FinRL`, `TRL`, `imitation`, `MLflow`, `W&B`, `RLlib`, and `Tune` are external framework integrations, not conceptual placeholders
3. completed contract tasks remain prerequisites, but they do not satisfy the upstream integration milestone
4. `tools/openclaw-local/` must be treated as a local helper runtime only; it is not the upstream OpenClaw project

## 7. Recommended Next Task Shape

Future tasks for named OSS components should use wording like this:

- `Integrate upstream OpenClaw runtime and map its workflow outputs into local StrategySpec contracts`
- `Pin and integrate DSPy with a governed persona optimization adapter`
- `Package Qlib in a research worker image and normalize experiment outputs into registry-ready artifacts`
- `Add imitation training adapter from FB-001 trajectory schema`
- `Connect REG-001 lifecycle metadata to MLflow or W&B backend`

This wording makes it much harder to accidentally re-implement a framework we intended to consume.

