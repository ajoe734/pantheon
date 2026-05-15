# vectorbt Integration — Governance Overlay

Last updated: 2026-04-18
Owner: OSS-GATE2-001 (Codex2)
Reviewer: Codex
Status: governed runtime boundary documented
Related task: `OSS-GATE2-001`

## 1. Governance Principle

> vectorbt may backtest governed strategy specs. It may not bypass Pantheon's registry, approval, or execution gates.

The vectorbt adapter is a research-time backtesting path only. It does not own deployment stage,
runtime execution, live portfolio authority, or order-routing semantics.

## 2. Input Governance

The input adapter validates the governed backtest dataset before any vectorbt portfolio call runs.

Mandatory constraints:

- `dataset_id`, `strategy_id`, and `source_dataset_refs` must be present
- at least one governed instrument is required, with the current smoke baseline using two instruments
- each record must include numeric `open`, `high`, `low`, `close`, and `volume` fields
- date index, bar counts, and instrument grouping must satisfy adapter requirements before execution

Malformed or out-of-scope datasets are rejected before vectorbt runs.

## 3. Output Governance

The vectorbt workflow emits a governed artifact bundle and a registry-ready backtest entry.

Governed output rules:

- `artifact_family` is `vectorbt_backtest`
- `artifact_state` starts at `draft`
- `deployment_summary.current_stage` is `none`
- `governance.direct_live_influence` is `false`
- `governance.lean_consumption` is `scoring_only_not_direct_action`
- output metrics stay evaluative; they do not become live execution authority on their own

## 4. Scope Guardrails

Only the governed rapid-backtesting baseline is in scope:

- signal-based backtesting through the adapter
- deterministic stub smoke execution in CI
- real backend execution behind an explicit environment gate

Explicitly out of scope for this governed baseline:

- direct order placement from vectorbt output
- registry bypasses
- live capital allocation authority
- LEAN execution control without separate downstream promotion

## 5. Promotion and Permissions

vectorbt artifacts follow Pantheon's standard research promotion path:

1. workflow output enters the registry as `draft`
2. reviewers inspect lineage, metrics, and governed metadata
3. any later promotion is decided by Pantheon registry policy, not vectorbt
4. downstream consumers may use the artifact only through governed read paths

vectorbt never receives permission to:

- write registry truth outside the adapter contract
- self-promote to executable stages
- open OpenClaw runtime actions
- issue live trades or rebalance orders

## 6. Rollback Semantics

Because vectorbt emits non-executable backtest artifacts, rollback stays registry-scoped:

- rollback means superseding or retiring the affected backtest artifact
- rollback does not alter live positions by itself
- downstream scorers or reviewers must stop referencing the superseded artifact through normal registry controls
- backend or version changes require a fresh smoke rerun before the governed claim remains valid

## 7. Upgrade Rules

When changing the version pin, adapter behavior, or governed backtest scope:

1. update `services/research/vectorbt/requirements.txt`
2. rerun `python3 services/research/vectorbt/smoke_test.py`
3. rerun `python3 -m pytest services/research/vectorbt/test_adapter.py -q`
4. update `integration.md`, this governance file, `smoke_test.md`, and `OSS_INTEGRATION_CHECKLIST.md`

Any future real-backend run must preserve the same dataset validation, draft-only lifecycle,
and registry-first authority boundary.
