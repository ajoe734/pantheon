# AG-GAP-012: 12-block completeness additive contract (bundle v1_6)

## Scope

`strategy_completeness.schema.json` (v1) models 7 generic dimensions
(hypothesis/data/market scope/eval/risk/execution/governance). The V10 Expert
Strategy Dialogue requirements and the 2026-06-28 design pack gap map require
12 named Winner Branch blocks. The FE completeness rail renders whatever the
BFF returns, so this is a contract + backend projection gap, not an FE
rebuild.

## Work

1. Author the 12-block completeness contract as an additive bundle extension
   (v1_6 following the established pattern: new specs dir, new
   `bundle_index.v1_6.json` with `extends` + exact-byte hashes, updated
   capability manifest). Do not mutate the frozen v1 schema.
2. Map the existing 7 dimensions onto the 12 blocks in the workshop
   completeness/readiness projection so existing workshops keep working
   (compatibility mapping documented in the spec).
3. Update the readiness gate computation only if the design pack requires it;
   otherwise keep gates unchanged and note that explicitly.
4. Regenerate frontend types from the new bundle (execute-plans follow-up PR
   if type output changes).

## Acceptance

- `bundle_index.v1_6.json` verifies via `scripts/agora_schema_bundle.py`-family
  tooling; prior bundles untouched (byte-identical).
- BFF completeness/readiness responses expose the 12 blocks for a new
  workshop and a mapped view for a pre-existing workshop.
- Contract tests cover both shapes; live dev proof for one workshop.
- Evidence under `docs/deployment/evidence/ag-gap-012/`.

## References

- `services/control-plane/specs/agora/strategy_completeness.schema.json`
- `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md`
- `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/`
