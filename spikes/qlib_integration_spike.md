# Qlib Integration Spike

## Goal

Select and pin the upstream Qlib integration path for governed alpha research.

## Decision Summary

- selected upstream source: `https://github.com/microsoft/qlib`
- package source of record: `https://pypi.org/project/pyqlib/`
- recommended first pin: `pyqlib==0.9.7`
- integration mode: separate research worker dependency, not vendored source
- first supported workflow: supervised forecast/ranking workflow, not RL
- first governed output: normalized `StrategySpec` plus registry-ready research metadata

## Why Qlib Fits This Layer

Qlib is the closest match to the alpha-policy box in the target architecture because it
already covers:

- dataset preparation
- model training
- workflow orchestration
- backtest and evaluation
- experiment-style run tracking inside the research loop

That makes it the right first research framework to integrate before adding RL-specific stacks.

## Required Decisions

- upstream package source: `microsoft/qlib` with `pyqlib` as the installable package
- version pin: `0.9.7` for the first integration spike
- worker/runtime packaging approach: isolated research image under `services/research/qlib/`
- adapter from Qlib outputs into local `StrategySpec` or registry artifacts
- smoke-test plan for one supervised research run

## Packaging Strategy

Use Qlib as a pinned Python dependency inside a dedicated research worker image.

Do not:

- vendor Qlib source into this repo
- let Qlib write directly into live execution paths
- treat Qlib's internal run outputs as governed artifacts by default

Do:

1. install `pyqlib==0.9.7` in a research-only environment
2. keep Qlib data and experiments behind a repo-local adapter
3. emit normalized outputs through local governed contracts

## First Workflow Scope

For v1, prove the supervised path first.

Chosen first workflow:

- daily-frequency dataset
- one benchmark-style ranking or forecast model
- one evaluation pass that emits scores and candidate metadata

Deferred:

- Qlib RL workflow integration
- nested execution agents
- direct Qlib executor integration with live brokerage behavior

## Governed Adapter Boundary

The adapter seam should be:

`Qlib run output -> governed research summary -> StrategySpec + registry hints`

Minimum adapter responsibilities:

- map dataset and run identifiers into governed lineage fields
- extract evaluation metrics needed by replication and promotion gates
- normalize alpha intent into a local `StrategySpec`
- keep raw notebooks or benchmark output outside the live path

## Minimal Smoke Test

The first smoke test should prove:

1. `pyqlib==0.9.7` installs cleanly in the research image
2. one small supervised workflow can run on a fixed dataset slice
3. the run emits a normalized governed summary
4. that summary can be transformed into a local `StrategySpec`
5. the normalized output can be represented in `REG-001`

Suggested scope:

- one simple benchmark config
- one fixed public dataset slice
- one adapter script that emits:
  - `strategy_spec.json`
  - `research_summary.json`
  - registry linkage hints

## Follow-up Deliverables

When implementation begins, create:

- `services/research/qlib/`
- `integrations/qlib/integration.md`
- `integrations/qlib/smoke_test.md`

## Remaining Open Questions

- Which benchmark config gives the smallest stable smoke test on this repo's target Python stack?
- Should Qlib summaries enter the system first as `StrategySpec` only, or as both `StrategySpec` and `candidate` registry drafts?
