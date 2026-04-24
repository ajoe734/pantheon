# Qlib Integration — Governance Overlay

Last updated: 2026-04-17
Owner: OSS-NEXT-001 (Claude)
Reviewer: Codex
Status: governed runtime boundary documented
Related task: `LP-003`

## 1. Governance Principle

> Qlib may learn from governed OHLCV data. It may not bypass Pantheon's registry, approval, or execution gates.

The Qlib adapter is a research-time path for supervised alpha signal discovery only. It does not own
deployment stage, runtime execution, or live replacement semantics.

## 2. Input Governance

The data handler validates every input record before any feature engineering happens.

Mandatory constraints:

- `dataset_id` and `strategy_id` must be non-empty strings
- `source_dataset_refs` must reference at least one governed dataset ref (no ad-hoc files or live side channels)
- every record must have numeric `open`, `high`, `low`, `close`, `volume` fields
- at least 2 instruments required (production bar: ≥50 per ACTIVATION_CRITERIA §1)
- at least 5 periods per instrument (production bar: 2+ years daily per ACTIVATION_CRITERIA §1.3)

Records that fail validation raise `QlibWorkflowError` and halt the workflow — no partial processing.

## 3. Output Governance

The Qlib workflow emits a governed artifact bundle and a registry-ready model entry.

Governed output rules:

- `artifact_state` starts at `draft`
- `artifact_family` is `qlib_alpha`
- `deployment_summary.current_stage` is `none`
- lineage must include `source_dataset_refs` and `source_run_ids`
- the registry entry remains descriptive until later promotion review
- `governance.direct_live_influence` is `false`
- `governance.lean_consumption` is `scoring_only_not_direct_action`

That keeps LP-003 aligned with the registry gate instead of allowing direct live rollout.

## 4. Signal Consumption Contract

Qlib produces **scores** (predicted alpha), not **actions** (buy/sell/hold).

- LEAN consumes Qlib output as an input factor to its portfolio optimizer
- Qlib does not control position sizing, order generation, or execution routing
- The score feeds into the optimizer; the optimizer decides allocations
- This contrasts with RL policies (LP-005), which produce direct actions from state

## 5. Scope Guardrails

Only LightGBM (v1) supervised alpha is in scope for this governed baseline.

Explicitly deferred:

- LSTM/GRU sequence models
- Transformer-based models (TCTS, HIST)
- Reinforcement learning integration within Qlib
- Intraday data (requires v1.5+ activation)

If Pantheon later enables those paths, they need separate smoke evidence and a governance refresh.

## 6. Authority Boundary

The Qlib integration never receives authority over:

- registry truth (write-owner is registry service)
- deployment-stage changes
- runtime-manager actions
- OpenClaw runtime orchestration
- LEAN execution decisions
- rollback semantics

Its responsibility ends at packaging a governed `draft` alpha artifact and evaluation summary.

## 7. Upgrade Rules

When changing the version pin, backend behavior, or feature-engineering scope:

1. update `services/research/qlib/requirements.txt` and `QLIB_VERSION_PIN`
2. rerun `python3 services/research/qlib/smoke_test.py`
3. rerun `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`
4. update `integration.md`, this governance file, and `OSS_INTEGRATION_CHECKLIST.md`

Any future upstream backend run must preserve the same data-validation, draft-only lifecycle,
and registry-first authority boundary.
