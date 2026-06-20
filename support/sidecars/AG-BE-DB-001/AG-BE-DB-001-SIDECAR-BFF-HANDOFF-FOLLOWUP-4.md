# AG-BE-DB-001 Sidecar Follow-up 4: Post-Review BFF/FE Handoff

| Field | Value |
|---|---|
| Task ID | `AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-DB-001` - DashboardRecipe/WidgetSpec persistence and validator |
| Parent owner / reviewer | Claude / Claude2 |
| Prepared by | Codex |
| Reviewer | Claude2 |
| Date | 2026-06-20 |
| Mutates canonical truth | false |
| Status | Ready for reviewer handoff |

## Purpose

This support-only packet updates the earlier BFF/frontend handoff after the
Agora dashboard v2 contract landed on `dev` and the parent BFF implementation
merged through PR #1847.

It does not edit canonical truth, OpenAPI, schema bundles, BFF runtime code,
registry files, validator behavior, or execute-plans source. It records the
current join state so Claude/Claude2 can decide what to absorb before frontend
work starts.

## Current Join State

| Surface | Observed state | Handoff consequence |
|---|---|---|
| `AG-XR-DASH-001` | Done. `services/control-plane/openapi/agora_v1_1.openapi.yaml` declares `agora.dashboard.v2` and 11 dashboard recipe/widget routes. `bundle_index.v1_1.json` includes v2 recipe/widget/chart schemas and the v1.1 capability manifest. | Frontend path/helper work may reference the v1.1 route catalog, but browser activation still needs deployed BFF smoke evidence. |
| `AG-BE-DB-001` status | Archived `done`. PR #1847 merged into `dev` at merge commit `8df64009ab2f0ec2b983c884ae328163fd6cafe0`; delivery commit was `050da1809a783e3f793259ad118f77bb48d82ecf`. | The BFF implementation is now dev truth. Execute-plans can plan a strict adapter slice against the merged routes. |
| `origin/dev` BFF state | `services/control-plane/bff/agora/dashboard/router.py` now implements the 11 dashboard recipe/widget routes, and `services/control-plane/specs/agora/widget_registry.v1.json` is present. | Browser smoke tests should target dev BFF after the service is refreshed to this merge. |
| Artifact path mismatch | The archived parent task artifact still lists `services/control-plane/bff/agora/dashboard.py`, but the implementation, review, and handoff record identify `services/control-plane/bff/agora/dashboard/router.py`. | Future workers should inspect `dashboard/router.py`; do not look for a sibling `dashboard.py`. |
| Registry bundle state | `widget_registry.v1.json` is now present in `services/control-plane/specs/agora/`, but it is not listed in the frozen v1 bundle index or `bundle_index.v1_1.json`. | Registry parity cannot be proven from bundle indexes alone. FE needs the merged registry file, a BFF handshake route, or validator responses to establish parity. |

## Implemented BFF Surface On Dev

The merged dev implementation provides these 11 routes in
`services/control-plane/bff/agora/dashboard/router.py`:

| Route | Frontend use |
|---|---|
| `GET /bff/agora/strategies/{strategy_id}/dashboard-recipes` | List recipe summaries for a strategy, filtered by `workspace` and `phase`. |
| `POST /bff/agora/strategies/{strategy_id}/dashboard-recipes/proposals` | Create an AI/operator proposal version. Requires `Idempotency-Key`. |
| `GET /bff/agora/dashboard-recipes/{recipe_id}` | Read active recipe detail and capture the response `ETag`. |
| `POST /bff/agora/dashboard-recipes/{recipe_id}/accept` | Promote proposal to active version. Requires `If-Match`, `Idempotency-Key`, and body `expected_version`. |
| `PATCH /bff/agora/dashboard-recipes/{recipe_id}/layout` | Apply allowed layout/widget operations and create a new append-only version. Requires `If-Match`, `Idempotency-Key`, and body `expected_version`. |
| `POST /bff/agora/dashboard-recipes/{recipe_id}/rollback` | Create a new version from a historical target. Requires `If-Match`, `Idempotency-Key`, body `expected_version`, and `target_version`. |
| `POST /bff/agora/dashboard-recipes/{recipe_id}/feedback` | Append recipe feedback. Does not require `If-Match`. |
| `GET /bff/agora/dashboard-recipes/{recipe_id}/versions` | List immutable version history. |
| `POST /bff/agora/widgets/validate` | Validate WidgetSpec v2 against the registry and safety rules. |
| `POST /bff/agora/widgets/{widget_id}/feedback` | Append widget feedback. |
| `POST /bff/agora/widgets/propose-plugin` | Record a widget plugin proposal; does not activate a plugin. |

## Contract Details Frontend Must Preserve

| Contract point | Required frontend behavior |
|---|---|
| ETag capture | Store the exact `ETag` response header from `GET /dashboard-recipes/{recipe_id}` and from successful mutations. Do not synthesize it from `version`. |
| Mutating headers | `accept`, `layout`, and `rollback` need both `If-Match` and `Idempotency-Key`; body must include integer `expected_version`. Feedback/proposal routes still need idempotency but do not use `If-Match`. |
| Conflict handling | The implementation returns BFF-wide `RESOURCE_CONFLICT` with reason `etag_mismatch`; details include `expected_version`, `current_version`, `current_etag`, and `latest_href`. FE should map this to the dashboard conflict UI even though the literal old name `DASHBOARD_RECIPE_VERSION_CONFLICT` is not used. |
| Append-only versions | `accept`, `layout`, and `rollback` create new versions. Rollback does not rewind or delete history. |
| Validator result | Successful widget validation returns `registry_version` and `schema_hash`. Validation errors return structured `errors[]` and `registry_version`; do not assume every error payload carries the hash. |
| Registry scope | The merged registry has `registry_version: widget_registry.v1`, `schema_version: 1.0.0`, and 42 entries. FE must not render inactive, unregistered, or plugin-proposed widgets as live components. |
| Forbidden authority | FE must continue blocking broker/capital/runtime actions. The BFF validator rejects `place_order`, `enable_live`, `change_capital_binding`, `invoke_broker`, `write_runtime_binding`, and `open_management_route`. |

## Deferred Items To Keep Visible

| Deferred item | Why it matters for FE |
|---|---|
| A3 rule 4 field catalog validation | Server-side validation does not yet verify every encoded field against a data-source field catalog. FE should not claim full field-catalog parity until a catalog source exists. |
| A3 rule 7 widget-scope validation | Review accepts request-level identity enforcement, but widget-level `tenant_id`/`user_id` scope validation is not threaded into `_validate_widget_spec`. FE should keep user/tenant scoping from the authenticated BFF context and avoid editable scope fields. |
| Durable storage | The implementation stores recipes, versions, feedback, plugin proposals, and idempotency keys in module-local in-memory collections. FE can smoke-test route behavior, but should not claim durable persistence across BFF restart unless the parent owner explicitly promotes storage. |
| Registry handshake route | There is no dedicated route that returns the full registry/checksum bundle before editing. FE can use `POST /widgets/validate` as the authoritative per-widget validator, but a pre-render checksum handshake still needs parent guidance if strict parity is required before opening customization. |
| Response body typing | OpenAPI v1.1 leaves several `200` bodies as generic `{type: object}`. FE should type adapters from accepted schemas plus observed envelopes, and add contract tests once the parent merge is visible. |

## Execute-Plans Handoff

Current execute-plans checkout facts:

| Area | Current state |
|---|---|
| Branch | `/home/lupin/code/execute-plans` is on `main` at `6346300647251322a05ae9991d633c1c53135117`, with local divergence from `origin/main`. Treat as read-only local evidence. |
| Path helpers | `src/lib/bff-v1/paths.ts` has Agora helpers for signals, inbox, journal, postmortems, and ask sessions only. No dashboard recipe/widget helpers exist. |
| Live adapter | `src/lib/bff/agora.ts` exposes daily, signals, inbox, journal, and ask sessions. No DashboardRecipe, WidgetSpec, registry, validation, version history, rollback, or proposal methods exist. |
| Headers client | `src/lib/bff-v1/client.ts` and `headers.ts` can add idempotency headers. The current `ifMatchVersion` helper quotes values; dashboard adapters should either pass an unquoted ETag payload deliberately or override raw `If-Match` through `headers` to preserve the exact server ETag. |
| Renderer library | `recharts` is installed and used in existing UI components, but that only proves renderer availability. It is not proof of backend registry/checksum parity. |

The next frontend slice should add only a strict adapter layer first:

| Frontend addition | Notes |
|---|---|
| Path helpers | Add helpers for the 11 accepted routes with URL encoding and query support for `workspace`, `phase`, `cursor`, and `limit`. |
| Adapter methods | Add `listRecipes`, `proposeRecipe`, `getRecipe`, `acceptRecipe`, `patchLayout`, `rollbackRecipe`, `submitRecipeFeedback`, `listVersions`, `validateWidget`, `submitWidgetFeedback`, and `proposeWidgetPlugin`. |
| Raw ETag handling | Capture response headers in live mode or extend the BFF client so adapter methods can read returned `ETag`; current `bffFetch` only returns parsed JSON. |
| Strict failure mapping | Map 409 `RESOURCE_CONFLICT` / `etag_mismatch` to reload/compare/merge UI. Map 422 widget errors to field-level messages. Keep backend-missing 404/501/unknown route states as backend-not-ready, not mock success. |
| No speculative UI activation | Keep recipe customization, rollback, and plugin proposal execution hidden or disabled until the deployed dev BFF is refreshed to PR #1847 and browser smoke confirms the routes. |

Suggested path helper names:

```ts
agoraDashboardRecipes: (strategyId: string) =>
  `/bff/agora/strategies/${enc(strategyId)}/dashboard-recipes`
agoraDashboardRecipeProposals: (strategyId: string) =>
  `/bff/agora/strategies/${enc(strategyId)}/dashboard-recipes/proposals`
agoraDashboardRecipe: (recipeId: string) =>
  `/bff/agora/dashboard-recipes/${enc(recipeId)}`
agoraDashboardRecipeAccept: (recipeId: string) =>
  `/bff/agora/dashboard-recipes/${enc(recipeId)}/accept`
agoraDashboardRecipeLayout: (recipeId: string) =>
  `/bff/agora/dashboard-recipes/${enc(recipeId)}/layout`
agoraDashboardRecipeRollback: (recipeId: string) =>
  `/bff/agora/dashboard-recipes/${enc(recipeId)}/rollback`
agoraDashboardRecipeFeedback: (recipeId: string) =>
  `/bff/agora/dashboard-recipes/${enc(recipeId)}/feedback`
agoraDashboardRecipeVersions: (recipeId: string) =>
  `/bff/agora/dashboard-recipes/${enc(recipeId)}/versions`
agoraWidgetValidate: () => `/bff/agora/widgets/validate`
agoraWidgetFeedback: (widgetId: string) =>
  `/bff/agora/widgets/${enc(widgetId)}/feedback`
agoraWidgetProposePlugin: () => `/bff/agora/widgets/propose-plugin`
```

## Downstream Activation Checklist

Claude/Claude2 should keep these visible before downstream frontend activation:

| Item | Expected outcome |
|---|---|
| Deployment smoke | Confirm the dev BFF process is running the PR #1847 merge SHA or a descendant before execute-plans enables strict live dashboard recipe calls. |
| Artifact path | Treat `services/control-plane/bff/agora/dashboard/router.py` as the implementation path despite the archived parent artifact listing `dashboard.py`. |
| Registry parity | Decide whether `widget_registry.v1.json` becomes part of the v1.1 bundle index, is served by a BFF handshake route, or remains a backend-only validator input. |
| Durable persistence | State whether the in-memory implementation is acceptable for this phase or whether DB-backed storage is still required before product-facing FE activation. |
| Deferred rules | Track A3 rule 4 field catalog validation and rule 7 widget-scope validation as explicit follow-ups. |
| FE baseline | Assign execute-plans work from a clean current branch/worktree; the local evidence checkout is not suitable as a merge base without refresh. |

## Verification Notes

Commands run by Codex:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin dev
git merge --ff-only origin/dev
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DB-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-DASH-001
git branch -r | rg "AG-BE-DB-001|ag-be-db-001|BE-DB"
git diff --name-status origin/dev..origin/task/AG-BE-DB-001
git show origin/task/AG-BE-DB-001:.orchestrator/reviews/ag_be_db_001_review.md
git show origin/task/AG-BE-DB-001:services/control-plane/bff/agora/dashboard/router.py
git show origin/task/AG-BE-DB-001:services/control-plane/specs/agora/widget_registry.v1.json
git log --oneline --decorate -5
sed -n '1,220p' .orchestrator/reviews/ag_be_db_001_review.md
jq '{registry_version, schema_version, entry_count: (.entries | length)}' \
  services/control-plane/specs/agora/widget_registry.v1.json
jq "." services/control-plane/specs/agora/bundle_index.v1_1.json
jq "." services/control-plane/specs/agora/v2/capability_manifest_v1_1.json
rg -n "dashboard-recipes|agora.dashboard.v2|widgets/validate" \
  services/control-plane/openapi/agora_v1_1.openapi.yaml \
  services/control-plane/specs/agora \
  services/control-plane/specs/agora/v2
git diff --check -- support/sidecars/AG-BE-DB-001/AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md
python3 scripts/agora_schema_bundle.py --verify
git status -sb
sed -n '1,320p' /home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts
sed -n '1,380p' /home/lupin/code/execute-plans/src/lib/bff/agora.ts
sed -n '1,240p' /home/lupin/code/execute-plans/src/lib/bff-v1/client.ts
sed -n '1,240p' /home/lupin/code/execute-plans/src/lib/bff-v1/headers.ts
rg -n "dashboard-recipes|agora.dashboard.v2|widgets/validate|WidgetSpec|DashboardRecipe" \
  /home/lupin/code/execute-plans/src \
  -g "!**/node_modules/**"
```

## Support Boundary

- Primary packet artifact:
  `support/sidecars/AG-BE-DB-001/AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`.
- No L1 canonical policy, OpenAPI, schema bundle, BFF runtime code, registry,
  validator, governance implementation, or execute-plans source file was
  changed.
- Downstream absorption remains a Claude/Claude2 decision. This sidecar only
  documents the current BFF/FE handoff boundary and activation risks.
