# E2E-R1 — Binding provenance integrity (left half of the loop)

**Round:** E2E-R1 of the 10-round e2e business-flow verification campaign
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r1-binding-provenance
**Business flow:** strategy → research/experiment → artifact → deployment-plan →
capital-pool → runtime-binding (active, paper). Every active binding must trace
back to live provenance objects.

## Plan

1. Enumerate active runtime-bindings from `/bff/runtimes`.
2. For each binding, resolve its provenance refs (artifact_id, strategy_id,
   plan_id, capital_pool_id) via the corresponding BFF detail endpoints.
3. Flag dangling refs (404/5xx **or 200 graceful-degradation envelope**) and
   list/detail inconsistencies.
4. Ship the checker as a CI-gated verification program; document the finding;
   flag data gaps for the upstream build side.

## Verification program

`scripts/verify_e2e_binding_provenance.py` (+ `scripts/test_verify_e2e_binding_provenance.py`).
Wired into `scripts/run-acceptance.sh` `full` mode as the `e2e-provenance-verifier`
gate (the live run against a deployed BFF is a post-deploy smoke check; the unit
test gates the checker's decision logic in CI).

**Key checker subtlety found the hard way:** several BFF detail endpoints return
HTTP **200 with a graceful-degradation envelope** (`data.status == "degraded"`,
`readSurface.status == "unavailable"`) for ANY id when the read-model source is
down — e.g. `/bff/artifacts/{anything}` returns 200-degraded. A naive status-only
check counts these as healthy. The verifier (and its tests) treat a degraded
envelope as **unresolved**.

## Live result (dev, 2026-06-15)

```
provenance integrity over 16 active bindings:
  artifact      ok= 0  dangling=16  (all 200-degraded; read-model source unavailable)
  strategy      ok= 0  dangling=16  (all 404)
  deployment    ok=15  dangling= 1  (plan-devloop-l0-001 -> 404)
  capital_pool  ok=15  dangling= 1  (pool-devloop-l0-001 -> 404)
FAIL: 34 dangling provenance references
```

## Findings

1. **Strategy provenance is entirely broken.** All 16 active bindings reference a
   `strategy-*` id whose `/bff/strategies/{id}` returns 404 — the strategies were
   never materialized. (List `/bff/strategies` also returns 0.)
2. **Artifact read-model source is unavailable.** `/bff/artifacts` lists 0 and
   `/bff/artifacts/{id}` returns a 200 "degraded / readSurface unavailable"
   envelope for any id. The dataset is bound to env
   `PANTHEON_BFF_RESEARCH_ARTIFACT_STORE`, which is **unset** on the deployed BFF,
   and no research-artifact HTTP source is wired — so artifacts never surface.
3. **One binding's whole chain is missing.** `rb-bf09c882…` (the `devloop-l0-001`
   chain) has strategy + plan + capital-pool all 404.

**Root cause:** the 16 active bindings are **rescue/placeholder bindings**
(`metadata.source = 20260603-live-rescue`) created directly as runtime bindings
without their upstream strategy/artifact provenance ever being persisted into the
read surfaces. This is a data/provenance gap (build/seed side), not a BFF code
bug — confirmed by reading the BFF handlers: `/bff/strategies/{id}` correctly
404s on missing data, and the artifact detail's 200-degraded is intentional
graceful degradation when the read-model source is down.

## Disposition

- **Shipped (code/CI):** the provenance verifier + logic test + CI gate, so this
  integrity violation is caught going forward (currently FAILs against dev, by
  design — it is reporting a real broken chain).
- **Flagged (upstream build, not hacked here):** materialize real strategy +
  artifact records for active bindings (or retire the rescue placeholders), and
  wire `PANTHEON_BFF_RESEARCH_ARTIFACT_STORE` / a research-artifact read source on
  the deployed BFF. Fabricating placeholder strategy/artifact rows to make the
  checker pass would hide the real gap, so it was deliberately not done.

## Next round

E2E-R2: telemetry → reconciliation → paper-live-drift flow (right-half integrity),
or deepen R1 by extending the verifier to the persona ↔ persona-capital-binding ↔
capital-pool sub-chain.
