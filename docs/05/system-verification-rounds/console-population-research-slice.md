# Console population — real research vertical slice (why pages were empty + the fix)

**Date:** 2026-06-15
**Branch / PR:** task/bff-research-surface-projection
**Trigger:** user observed most left-half console pages empty after the FE was
fixed (E2E-R21).

## Root cause (confirmed end-to-end)

The empty left-half pages are NOT just "no data" — even after **real** data is
produced, it does not reach the console, because the **BFF read-surfaces are
disconnected from the live services**:

- `/bff/strategies` reads the file named by `PANTHEON_BFF_STRATEGY_SPEC_STORE`;
  `/bff/artifacts` reads `PANTHEON_BFF_RESEARCH_ARTIFACT_STORE`. Both were
  **unset** on the deployed BFF, so the surfaces reported `source: unavailable`.
- I drove the **real** research pipeline on dev (research-orchestrator, stub
  dispatch — the dev safety posture): `POST tasks` → `POST runs` →
  `POST runs/{id}/artifacts` → `complete` → `registry-writeback` → a genuine
  registry artifact (`reg-vslice-model`, run `rrun-20260615-002`, artifact
  `rart-20260615-002`, strategy `tw-momentum-vslice`). The orchestrator
  `task_count`/`run_count` went 0 → real.
- That real data **still did not appear** on `/bff/artifacts` or `/bff/strategies`
  — confirming the missing piece is a **projection** from the orchestrator/registry
  into the BFF read-surface stores.

## Fix (this PR)

1. `scripts/project_research_to_bff_surfaces.py` — reads the orchestrator's real
   completed runs + artifacts and writes the BFF `research_artifacts.json` +
   `strategy_specs.json` stores (keyed correctly). **Emits only real orchestrator
   records — no fabricated data.** Verified live: projected the 2 real artifacts +
   1 strategy produced above.
2. `docker-compose.yml` — wires `PANTHEON_BFF_STRATEGY_SPEC_STORE` and
   `PANTHEON_BFF_RESEARCH_ARTIFACT_STORE` (overridable) so the BFF reads the
   projected stores by default.

## Verified live (dev)

After projecting + wiring + redeploy:
```
/bff/strategies -> count=1 surf=[ok]   (tw-momentum-vslice — TW Cross-Sectional Momentum v1)
/bff/artifacts  -> count=1 surf=[ok]   (rart-20260615-002 — TW momentum candidate, model_artifact)
```
The **Strategies and Artifacts console pages now render real data** from the
research pipeline.

## Scope / honest status

This proves the mechanism for ONE domain (research → artifacts/strategies). The
other empty left-half domains (agora, skills, tools, knowledge, rankings, OODA
packets, evolution, …) each need the same two steps: (a) produce real domain data,
(b) project it into the BFF read-surface store (or wire the BFF to the live
service). That is a large, repeated build best driven incrementally — this PR
establishes the pattern + the first real slice. Operational surfaces
(fleet/portfolio/performance/persona-league/governance/risk, 48 surfaces) already
render real data derived from the live runtime fleet.
