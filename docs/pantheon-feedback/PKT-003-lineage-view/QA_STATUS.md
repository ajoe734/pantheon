# PKT-003 Lineage View — QA Status

## Status

Static verification complete with one pre-existing unrelated build blocker in the working tree.

## Checks completed

- `./node_modules/.bin/tsc --noEmit --pretty false` passed for the PKT-003 lineage-view files.
- Targeted ESLint passed for the touched PKT-003 files:
  - `src/pages/evolution/LineageView.tsx`
  - `src/pages/evolution/LineageEdgeDetail.tsx`
  - `src/pages/evolution/types.ts`
  - `src/lib/bffClient.ts`
- The three BFF endpoints (LN-01, LN-02, LN-03) are consumed through the shared BFF client only. No raw `fetch` or `axios` in component files.
- List-panel row click, graph-panel edge click, and edge detail drawer trigger sequence verified against the published interaction rules.
- The `root_type` no-op constraint, `depth` passthrough, empty-graph copy, and staleness banner path were cross-checked against `docs/screens/PKT-003-lineage-view.md` and `docs/bff/PKT-003-lineage-view.md`.
- BFF-gap field resolution verified against `docs/examples/PKT-003-lineage-view.json` — corrected shapes (items[], flat LN-02 root, top-level nodes[]/edges[]) confirmed matching.

## Not completed in this cycle

- Full `npm run build` completion. The current working tree contains pre-existing unrelated missing imports in `src/App.tsx` for `./pages/persona/BindingDetail` and `./pages/persona/BindingList` so Vite exits before the PKT-003 route can finish a production bundle.
- Live browser QA against a running Pantheon BFF.
- Full repo-wide ESLint conformance outside the touched files.

## Risk note

The remaining risk is runtime verification only. The production-build blocker is pre-existing and unrelated to the lineage-view files; it must be cleared elsewhere in the working tree before a full bundle can be produced. The lineage-view implementation itself is statically clean against the PKT-003 BFF contract.
