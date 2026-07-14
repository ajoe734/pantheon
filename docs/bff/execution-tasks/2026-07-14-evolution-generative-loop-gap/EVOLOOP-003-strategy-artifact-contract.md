# EVOLOOP-003 — Minimal Evolvable Strategy Artifact Contract

Status: implementation contract; reviewer gate pending

Owner: Codex  
Reviewer: Claude  
Target branch: `dev`

## Outcome

EVOLOOP-003 defines an additive, machine-readable payload for an executable
strategy registered through the existing registry lifecycle. The payload
supplies four inputs that the generic registry envelope does not define:

1. a pinned LEAN-compatible algorithm reference;
2. a named parameter set;
3. an explicit, bounded mutation surface; and
4. version plus normalized registry lineage fields.

The schema is `services/registry/strategy_artifact.schema.json`. The first
real registration request is
`services/registry/strategy-artifacts/tw-session-momentum-v1.registration.json`
with registry/artifact id `artifact-tw-session-momentum-v1` and semantic
version `1.0.0`.

This is not a new artifact lifecycle or artifact-type enum. StrategyArtifact
maps to the already supported executable type `artifact_type=execution_bundle`,
enters as `candidate`, and continues to use the existing
`draft -> candidate -> approved -> retired` state machine. The full payload is
stored inline at `metadata.strategy_artifact`. This keeps existing registry,
LEAN-loader, telemetry, and deployment consumers compatible.

## Contract

| Field | Requirement | Meaning |
| --- | --- | --- |
| `artifact_schema_version` | required, `1.0` | Version of this overlay contract. |
| `artifact_id` | required | Stable registry id and future `DeploymentPlan.artifact_id`. |
| `strategy_id` | required | Stable strategy family id across v1, v2, and later mutations. |
| `version` | required semver | Artifact version; a mutation creates a new version and id. |
| `algorithm_ref` | required | Pinned LEAN repo, commit, path, entrypoint, signal interface, and deterministic logic interpreter. |
| `strategy_logic` | required | Machine-readable decision-rule kind and parameter/action bindings. |
| `parameters` | required | Complete named inputs used to evaluate the strategy. |
| `mutation_surface.controls` | required | Only parameters a retrain/optimizer may change, using trainer-compatible `parameter_key`, `current_value`, `allowed_range`, and `step`. |
| `mutation_surface.immutable_parameters` | required | Every parameter not listed as mutable. |
| `lineage` | required, non-empty source edge | Registry-normalized parent/run/dataset/StrategySpec refs. |
| `binding_intent` | optional | Non-authoritative observation of the binding targeted by a later deployment plan. |
| `provenance_refs` | optional | Evidence for the source logic and observed binding. |

Every parameter must appear exactly once in either the mutable or immutable
set. A mutation must reject unknown parameters, type mismatches, out-of-range
values, and values that do not align with the declared step. Mutation never
changes `strategy_id`, `algorithm_ref`, immutable parameters, or an existing
artifact in place. The new artifact must carry a new `artifact_id`, a greater
semantic version, and `lineage.parent_registry_ids` containing its parent.

## v1: TW close-to-close momentum

The v1 parameterizes the real host logic already used by
`/home/lupin/paper-loop/tw_signal_producer.py`:

```text
momentum = close[-1] / close[-lookback_bars] - 1
BUY/LONG  when momentum > momentum_threshold
SELL/SHORT otherwise
```

The LEAN execution bridge is pinned to
`ajoe734/pantheon-lean@5ad0249432459c119f26718007e083808ef7995d`,
`Algorithm.Python/pantheon_algo/base.py:PantheonAlgoBase`. That entrypoint
consumes Pantheon signals; it does not implement the momentum rule. The signal
interface is the typing contract
`services.execution.lean_runtime.paper_signal_producer:Strategy`, while the
machine-executable v1 rule is
`services.registry.strategy_artifact:evaluate_strategy_action`. EVOLOOP-007
still owns materializing that rule as the binding's signal producer.

Named v1 inputs:

| Parameter | v1 | Mutable bounds |
| --- | --- | --- |
| `symbols` | `2330.TW`, `2317.TW`, `2454.TW` | immutable |
| `bar_frequency` | `1d` | immutable |
| `data_source` | normalized FinMind TaiwanStockPrice | immutable |
| `lookback_bars` | `2` | integer `2..60`, step `1` |
| `momentum_threshold` | `0.0` | number `0.0..0.05`, step `0.001` |
| `order_quantity` | `1` | immutable (`SHARES`) |
| `quantity_type` | `SHARES` | immutable |
| `zero_momentum_action` | `SELL` | immutable |

Alpha quality is deliberately not claimed. This v1 exists to give
EVOLOOP-004 a genuine, deterministic parameter surface on which to produce a
different v2.

## Existing binding intent

The selected persona is the dedicated existing Taiwan Equity persona:

| Identity | Observed value |
| --- | --- |
| persona | `persona-tw-equity` (`Taiwan Equity Persona`) |
| PersonaCapitalBinding | `binding-tw-equity-paper` |
| RuntimeBinding | `rb-abb82fd3538b4014bb7e7d3186a58c58` |
| runtime | `runtime-tw-equity-paper` |
| deployment plan | `plan-tw-equity-paper` |
| current placeholder artifact | `artifact-tw-equity-session-v1@1.0.0` |
| desired artifact | `artifact-tw-session-momentum-v1` |

The observation is pinned to
`docs/deployment/evidence/mgmt-ops-003-gap/gap-002/20260711T151356Z/reconciliation-snapshot.json`.
It was re-read from the dev Runtime Manager at `2026-07-14T04:33:51Z`:
the binding was `active`, `paper`, and carried
`metadata.strategy_id=tw_session_momentum`. The BFF persona read model joined
the runtime identity to `persona-tw-equity` at `2026-07-14T04:34:11Z`.
`binding_intent` is evidence and future intent only. It is not a
`RuntimeBinding` write and cannot be interpreted as deployment truth.

## Write-authority boundary

EVOLOOP-003 registers the candidate and records its intended binding. It does
not edit the runtime store or the old binding. The required cutover remains:

```text
registry candidate
  -> ApprovalDecision
  -> DeploymentPlan(binding + approved artifact)
  -> Runtime Manager replaces RuntimeBinding
```

EVOLOOP-006 owns that governed replacement and rollback proof. This preserves
`BINDING_AND_DEPLOYMENT_SEMANTICS.md`: Runtime Manager is the only
`RuntimeBinding` writer, and a binding intent is not a deployment trigger.

## Downstream composition

- `EVOLOOP-004` must mutate only the declared surface, issue a new id/version,
  and record v1 in `parent_registry_ids` plus decision/work-item/session ids in
  `source_run_ids`.
- `EVOLOOP-005` adds governed expected-drawdown evidence without changing the
  strategy parameter contract.
- `EVOLOOP-006` approves/promotes through service APIs and replaces the
  placeholder binding with rollback available.
- `EVOLOOP-007` makes the strategy callable the binding's signal origin; the
  v1 registration alone does not claim strategy-driven trades.
- `LOOP-PROD-DIST-001` and `LOOP-PROD-ALPHA-001` consume and generalize this
  overlay rather than inventing a competing contract.

## Acceptance evidence

Focused validation commands are recorded after implementation. The required
checks are:

1. JSON Schema plus semantic validation passes for v1.
2. Registry startup exposes v1 as a candidate with deterministic checksum and
   normalized lineage.
3. A permitted parameter mutation produces a schema-valid child with a real
   parameter delta and parent lineage.
4. Invalid, immutable, unbounded, or off-step mutation attempts fail closed.
5. Existing generic and StrategySpec registry tests remain green.

## Residual risk

- The registry store remains the service's existing in-memory v1 backend; the
  built-in artifact is re-registered deterministically at startup. Durable DB
  migration is not introduced by this task.
- Binding ids are an observation, not a lease. EVOLOOP-006 must re-read the
  authoritative capital, deployment, and runtime services before cutover.
- LEAN consumes Pantheon signals today; EVOLOOP-007 still owns turning this
  declarative TW momentum artifact into the live per-binding signal producer.
