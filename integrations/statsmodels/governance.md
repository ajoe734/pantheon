# statsmodels Integration — Governance Overlay

Last updated: 2026-04-21
Owner: OSS-GATE2-001 (Codex2)
Reviewer: Codex
Status: governed runtime boundary documented
Related task: `OSS-GATE2-001`

## 1. Governance Principle

> statsmodels may analyze governed historical datasets. It may not bypass Pantheon's registry, approval, or execution gates.

The statsmodels adapter is a research-time path for econometrics and regime diagnostics only. It does not own
deployment stage, runtime execution, position sizing, or live replacement semantics.

## 2. Input Governance

The input adapter validates every governed dataset before any upstream model call runs.

Mandatory constraints:

- `GovernedDataset` must provide at least two price series for pair or multivariate analysis
- all price and factor series must be numeric, aligned, and long enough for the requested analysis path
- metadata must represent governed historical inputs rather than ad-hoc live side channels
- malformed, NaN-heavy, or insufficient-length series are rejected before statsmodels executes

Validation failures raise `StatsmodelsWorkflowError` and halt the workflow instead of widening scope.

## 3. Output Governance

The statsmodels workflow emits a governed artifact bundle and a registry-ready research entry.

Governed output rules:

- `artifact_family` is `regime_report`
- `artifact_state` starts at `draft`
- `deployment_summary.current_stage` is `none`
- `governance.direct_live_influence` is `false`
- `governance.lean_consumption` is `research_only_not_direct_action`
- lineage remains descriptive and reviewable before any downstream use

That keeps econometrics output inside the registry-first research path rather than letting it steer execution.

## 4. Scope Guardrails

Only the currently implemented baseline is governed in this wave:

- cointegration screening
- VAR/VECM diagnostics
- Markov-switching regime diagnostics

Explicitly out of scope for this governed baseline:

- direct signal emission into LEAN
- automatic deployment-stage changes
- unsupervised live retraining loops
- execution-routing or capital-allocation authority

Any future expansion needs fresh smoke evidence and a governance refresh.

## 5. Promotion and Permissions

statsmodels artifacts follow Pantheon's standard research promotion path:

1. workflow output enters the registry as `draft`
2. reviewers may inspect lineage, results summary, and governed metadata
3. any later promotion remains a Pantheon registry decision, not a statsmodels decision
4. even after review, the artifact stays research-only unless a separate downstream contract explicitly consumes it

statsmodels never receives permission to:

- write registry truth directly
- mark artifacts `paper`, `canary`, or `live`
- open OpenClaw sessions
- place, route, or roll back orders

## 6. Rollback Semantics

Because statsmodels emits non-executable research artifacts only, rollback is metadata- and registry-scoped:

- rollback means superseding or retiring the affected research artifact
- no market position changes are triggered by statsmodels rollback alone
- downstream consumers must stop referencing the superseded artifact via normal registry controls
- smoke reruns are required after version-pin or adapter-behavior changes before governance status is retained

## 7. Upgrade Rules

When changing the version pin, adapter behavior, or governed analysis scope:

1. update `services/research/statsmodels/requirements.txt`
2. rerun `python3 services/research/statsmodels/smoke_test.py`
3. rerun `python3 -m pytest services/research/statsmodels/test_adapter.py -q`
4. update `integration.md`, this governance file, `smoke_test.md`, and `OSS_INTEGRATION_CHECKLIST.md`

Any future real-backend execution must preserve the same dataset validation, draft-only lifecycle,
and registry-first authority boundary.
