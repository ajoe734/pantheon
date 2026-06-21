# B — Research Facade, Stage Routing and Run Projection

## B1. Boundary

Agora exposes a BFF facade. It does not become the research truth owner.

```text
Agora BFF
  -> existing research orchestrator / worker gateway
  -> governed framework adapter
  -> Experiment/Artifact Registry
  -> Agora read projection
```

Research output is research-only/draft until existing Registry and Governance advance it.

## B2. Plan-first rule

Every run must have a persisted ResearchPlan.

Canonical plan lifecycle remains compatible with the frozen v1 schema:

```text
draft -> approved -> running -> completed
draft -> cancelled
approved -> cancelled
running -> cancelled
```

A rejected approval is represented as:

```text
status = cancelled
terminal_reason = approval_rejected
```

The new projection may expose `approval.state=rejected`; it must not introduce an incompatible base lifecycle value.

## B3. API

```text
GET  /bff/agora/workshops/{workshop_id}/research-plans
POST /bff/agora/workshops/{workshop_id}/research-plans

GET  /bff/agora/research-plans/{plan_id}
POST /bff/agora/research-plans/{plan_id}/approve
POST /bff/agora/research-plans/{plan_id}/cancel
GET  /bff/agora/research-plans/{plan_id}/runs
POST /bff/agora/research-plans/{plan_id}/runs

GET  /bff/agora/research-runs/{run_id}
POST /bff/agora/research-runs/{run_id}/cancel
GET  /bff/agora/research-runs/{run_id}/artifacts
```

The older `POST /bff/agora/workshops/{workshop_id}/research-runs` route may remain for compatibility, but it must materialize or reference a ResearchPlan and must not bypass approval.

## B4. Plan approval

A servant-generated plan starts in `draft`.

Trader acceptance is required before dispatch for workshop-driven research. Additional governance approval is required when the plan:

- uses private or restricted data;
- requests paid/external data access;
- uses heavy compute;
- invokes policy training/RL;
- exceeds configured runtime or budget limits;
- crosses tenant policy boundaries.

## B5. Typed research stages

| Stage type | Default route | Notes |
|---|---|---|
| `source_discovery` | governed source ingestion / allowlisted search | no unrestricted agent crawling |
| `data_validation` | data source registry/validator | mandatory before dependent stages |
| `prototype_backtest` | vectorbt | quick rules and candidate scans |
| `alpha_training` | Qlib | cross-sectional alpha/ranking |
| `rolling_oos` | Qlib | rolling/walk-forward |
| `econometric_validation` | statsmodels | cointegration/regime/VAR-VECM |
| `derivatives_pricing_risk` | QuantLib | options/rates/Greeks |
| `policy_training` | FinRL or RLlib | activation-gated |
| `parameter_search` | Ray Tune | research-only optimizer output |
| `portfolio_synthesis` | existing optimizer-svc | weights/constraints; not a new service |
| `robustness_stress` | orchestrated framework set | selected by strategy family |
| `evidence_synthesis` | OpenClaw result-synthesis skill | last stage, no truth ownership |

The LLM proposes stage intent, not raw tool names. Route policy resolves the effective backend.

## B6. Fallback rules

- Production/dev integration does not silently fall back from a missing real backend to a stub.
- `backend_mode=fixture|stub` must be explicitly requested for smoke/CI.
- Fixture/stub runs are visibly labelled and cannot satisfy full-validation readiness.
- Capability unavailable returns a typed blocked stage, not a synthetic successful result.

## B7. DAG and concurrency

- Stages form a DAG through `dependencies[]`.
- Default maximum parallel stages: 2.
- Hard platform maximum: 4.
- A failed hard dependency blocks downstream stages.
- Optional stages may be skipped with a recorded reason.
- Cancellation is idempotent and propagates only to currently queued/running descendants.

## B8. Run projection

The Agora projection must expose:

```text
identity and lineage
strategy/workshop/version refs
plan/stage refs
requested/effective backend
backend mode and version
execution status
outcome
progress
metrics grouped by domain
findings
warnings/blockers/failure
artifact/evidence/lineage refs
data cutoff
no-order-route proof
timestamps
```

Metric categories:

```text
performance
risk
cost
capacity
robustness
calibration
data_quality
```

Every metric carries its unit, direction, gate result, optional threshold/baseline/delta/confidence interval and source ref.

## B9. Progress semantics

```text
queued
dispatching
running
succeeded
failed
cancelled
timed_out
```

Progress percentage must be monotonic within a run attempt. A retry creates a new attempt ID; it must not rewind the old attempt.

## B10. No-order-route rules

- Research plans cannot request canary/live.
- Framework adapters produce research artifacts only.
- A candidate artifact must go through existing Registry/Governance.
- A research completion event cannot create RuntimeBinding, capital binding or broker order.
- UI must show whether the backend was real, fixture or stub.
