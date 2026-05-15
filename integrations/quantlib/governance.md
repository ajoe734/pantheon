# QuantLib Integration — Governance Overlay

Last updated: 2026-04-21
Owner: EXEC-OSS-QUANTLIB-001 (Codex)
Reviewer: Claude
Status: governed runtime boundary documented
Related task: `EXEC-OSS-QUANTLIB-001`

## 1. Governance Principle

> QuantLib may price governed instruments and compute governed risk analytics. It may not bypass Pantheon's registry, approval, or execution gates.

The QuantLib adapter is a research-time pricing and fixed-income analytics path only. It does not own
deployment stage, runtime execution, trade placement, or live hedging semantics.

## 2. Input Governance

The adapter accepts only governed market snapshots and governed instrument specs.

Mandatory constraints:

- `dataset_id` and `source_dataset_refs` must be present for lineage
- option inputs must provide style, type, spot, strike, volatility, rates, maturity, and quantity
- bond inputs must provide face value, coupon, market rate, maturity, and payment frequency
- valuation inputs must be numeric, finite, and shaped for the selected pricing path

Validation failures raise adapter errors and stop the workflow before upstream pricing runs.

## 3. Output Governance

The QuantLib workflow emits a governed artifact bundle and a registry-ready research entry.

Governed output rules:

- `artifact_family` is `pricing_report`
- `artifact_state` starts at `draft`
- `deployment_summary.current_stage` is `none`
- `governance.direct_live_influence` is `false`
- `governance.lean_consumption` is `research_only_not_direct_action`
- pricing and risk results remain descriptive until reviewed and consumed by a downstream governed workflow

## 4. Scope Guardrails

Only the currently implemented governed baseline is in scope:

- option pricing
- Greeks computation
- fixed-income analytics

Explicitly out of scope for this governed baseline:

- direct order generation from pricing outputs
- automatic hedge execution
- runtime-owned portfolio replacement
- live authority over capital or rollback controls

Any broader derivative strategy path requires separate evidence and policy review.

## 5. Promotion and Permissions

QuantLib artifacts follow Pantheon's standard research promotion path:

1. workflow output enters the registry as `draft`
2. reviewers inspect lineage, pricing summary, and governed metadata
3. later promotion, if any, is decided by Pantheon registry controls and downstream policy
4. QuantLib output stays research-only unless another governed component explicitly consumes it

QuantLib never receives permission to:

- mutate registry truth outside the governed output contract
- self-promote into executable deployment stages
- control OpenClaw runtime actions
- issue or cancel trades directly

## 6. Rollback Semantics

Because QuantLib emits non-executable research artifacts, rollback stays at the artifact and consumer layer:

- rollback means superseding, retiring, or de-referencing the affected pricing artifact
- rollback does not liquidate positions or change execution state by itself
- downstream consumers must be routed away through normal registry controls
- any version-pin or pricing-model change requires a fresh smoke run before governed status remains valid

## 7. Upgrade Rules

When changing the version pin, backend behavior, or governed pricing scope:

1. update `services/research/quantlib/requirements.txt`
2. rerun `python3 services/research/quantlib/smoke_test.py`
3. rerun `python3 -m pytest services/research/quantlib/test_adapter.py -q`
4. update `integration.md`, this governance file, `smoke_test.md`, and `OSS_INTEGRATION_CHECKLIST.md`

Any future real-backend run must preserve the same input validation, draft-only lifecycle,
and registry-first authority boundary.
