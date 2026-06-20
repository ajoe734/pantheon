# AG-BE-DB-001 Sidecar Follow-up 2: BFF Query and FE Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-DB-001` - DashboardRecipe/WidgetSpec persistence and validator |
| Parent owner / reviewer | Claude / Claude2 |
| Prepared by | Codex2 |
| Reviewer | Claude |
| Date | 2026-06-20 |
| Mutates canonical truth | false |
| Status | Support packet ready for review |

## Purpose

This support-only packet extends the earlier BFF handoff by separating three
surfaces that are currently conflated in the parent blocker:

- frozen design intent in A3/C1 under `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/`;
- frozen control-plane Agora bundle truth under `services/control-plane/specs/agora/`
  plus `services/control-plane/openapi/agora_v1.openapi.yaml`;
- execute-plans adapter/path-helper work that must wait for accepted BFF paths.

It does not define canonical routes, schema authority, storage tables, runtime
validators, OpenClaw tool behavior, or frontend implementation. The parent owner
and reviewer still own whether and how this material is absorbed.

## Read Sources

| Source | Relevant finding |
|---|---|
| `ai-status.json` via `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DB-001` | Parent remains `blocked`, waiting on Claude2 for SD section, route, concurrency, and schema-authority clarification. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` | Contains sections `0` through `8` and a normative `§17` route list; no `§9`, `§9.6`, or `§17.5` DashboardRecipe endpoint text was found. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/00_design_closure_decision.md` | Says A3 Widget/Chart is design-frozen and maps `AG-BE-DB-001` as dispatchable. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/A3_widget_registry_and_chart_grammar_spec.md` | Defines registry/checksum parity, data source allowlist, validator ordering, interaction allowlist, versioned recipe changes, and no arbitrary code. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/C1_agora_openclaw_skills_master_spec.md` and `skills/agora/dashboard-compose/SPEC.md` | Dashboard compose output is a proposal with `DashboardRecipe`, `WidgetSpec[]`, validation result, and before/after data; skills must not write runtime/capital/broker authority. |
| `services/control-plane/specs/agora/*.schema.json` and `capability_manifest.json` | Frozen bundle lists `dashboard_recipe.schema.json` and the older `widget_spec.schema.json`, but not A3 `widget_registry.v1.json` or `chart_spec.schema.json`. |
| `services/control-plane/bff/agora/dashboard/router.py` | Dashboard sub-router is still a placeholder; existing dashboard routes remain in `main.py`. |
| `services/control-plane/openapi/agora_v1.openapi.yaml` | Agora dashboard OpenAPI currently includes daily, markets, market-notes, watchlist, postmortems, alerts triage, decision journal, journal, and notes. It does not include DashboardRecipe, WidgetSpec, registry, checksum, validation, version-history, or rollback paths. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | Agora path helpers currently cover signals, inbox, journal, postmortems, and ask sessions; no recipe/widget/registry helpers exist. |
| `/home/lupin/code/execute-plans/src/lib/bff/agora.ts` | Strict live adapter currently exposes daily, signals, inbox, journal, and ask session reads; no DashboardRecipe/WidgetSpec adapter exists. |
| `/home/lupin/code/execute-plans/package.json` | `recharts` is currently present, but renderer acceptance remains separate from BFF contract acceptance. |

## Current Contract State

| Surface | Current state | BFF/FE consequence |
|---|---|---|
| Design closure | A3/C1 provide enough product intent for registry-based dashboards. | Useful as design input, but not yet a mounted BFF contract. |
| Frozen Agora bundle | `python3 scripts/agora_schema_bundle.py --verify` passes against the existing bundle. | A direct replacement of frozen `widget_spec.schema.json` with A3 shape would be a bundle/versioning change, not a sidecar detail. |
| Capability manifest | `agora.dashboard.v1` advertises dashboard recipe and widget schemas plus existing dashboard read/write route prefixes. | It does not expose A3 registry/checksum parity, data source field catalog, or ChartSpec schema identity to the frontend. |
| BFF dashboard router | `create_dashboard_router()` returns an empty `APIRouter`; routes are still in `main.py`. | New implementation needs an explicit owner decision on whether to add in the sub-router, migrate existing handlers, or defer migration. |
| OpenAPI route catalog | No recipe/widget/registry/checksum/validation/version route exists. | Frontend must not add strict live helpers for unaccepted paths. |
| execute-plans adapter | Current adapter can consume existing Agora live read routes only. | Recipe save/load, checksum mismatch handling, and conflict handling should wait for backend path and error contract acceptance. |

## BFF Query Gap Matrix

| Gap | Why it matters | Minimum decision needed before coding |
|---|---|---|
| Registry/checksum handshake | A3 requires backend validator, frontend renderer, and OpenClaw skill to use the same registry version and schema checksum. Current `/bff/agora/capabilities` does not expose A3 registry/chart hashes. | Decide whether handshake data is added to `/bff/agora/capabilities` or served by a new dashboard registry route. Include exact fields for `registry_version`, schema hashes, data source catalog hash, and freshness. |
| DashboardRecipe load | Frontend needs a strict way to load the active recipe for a tenant/user/surface/workspace/strategy phase. No path or query shape exists. | Freeze path, method, scope dimensions, response envelope, empty-state behavior, and whether WidgetSpecs are embedded or separately referenced. |
| DashboardRecipe save/update | A3 says trader edits create a new recipe version and changelog. No route defines request body, version token, audit metadata, or write authority. | Freeze path, method, request body, canonical write owner, audit fields, idempotency behavior, and changelog semantics. |
| Optimistic concurrency | Parent acceptance requires `DASHBOARD_RECIPE_VERSION_CONFLICT`; no contract defines version vs ETag vs `If-Match`. | Pick one concurrency mechanism and freeze status code, error code, error payload, client retry guidance, and returned server version. |
| WidgetSpec validation | A3 provides validator rules and output shape, but no BFF route or save-time contract says how a proposal is validated. | Decide whether validation is a separate route, a dry-run save mode, or a mandatory save step. Freeze error codes and field-path format. |
| Data source field catalog | A3 requires every data source to define owner service, scope predicate, field catalog, freshness, PIT semantics, sensitivity, and allowed aggregates. No BFF route exposes this catalog. | Decide where the data source catalog lives and how frontend/validator/OpenClaw confirm field-level legality. |
| WidgetSpec authority | Frozen bundle WidgetSpec uses `spec_version`, enum `widget_type`, object `data_source.bff_path`, and display options. A3 WidgetSpec uses registry-backed `widget_type`, string `data_source`, `query`, `chart_spec`, `interactions`, `sensitivity`, and `can_export`. | Decide whether A3 becomes `widget_spec.v2`, replaces the frozen bundle through an AG-XR/schema-freeze task, or remains design-closure only for now. |
| Version history / rollback / replay | A3 DoD requires versioning, rollback, and replay, but no route or storage model exists. | Freeze read/write paths, retention, rollback authority, and replay evidence model. |
| OpenClaw proposal admission | `agora-dashboard-compose` outputs a proposal; it must not silently become deployed UI/runtime truth. | Define how a proposal is persisted, reviewed, validated, and converted into a versioned recipe without bypassing user confirmation. |

## Operator Journey Handoff

### Current safe journey

```text
Operator opens Agora
  -> frontend calls existing read surfaces:
     daily, markets/watchlist, market-notes/notes, postmortems, alerts triage,
     decision-journal/journal, signals, inbox, ask sessions
  -> BFF returns current list/detail envelopes from main.py handlers
  -> frontend may display existing dashboards/pages
  -> custom recipe save, WidgetSpec validation, registry checksum parity,
     rollback, replay, and dashboard compose success paths remain disabled or
     backend-not-ready
```

### Contract-ready journey after parent decisions

```text
Operator opens a dashboard workspace
  -> frontend fetches accepted capability + registry/checksum handshake
  -> frontend refuses customization if local registry/schema hashes differ
  -> frontend loads active DashboardRecipe and associated WidgetSpecs
  -> operator or OpenClaw dashboard-compose proposes a change
  -> BFF validates WidgetSpecs against registry, ChartSpec grammar, data source
     field catalog, scope, sensitivity, and no-code/no-broker rules
  -> BFF writes a new recipe version only with the accepted concurrency token
  -> frontend refreshes from returned version metadata and audit/changelog refs
```

### Required failure handling

| Failure | Frontend behavior | BFF contract need |
|---|---|---|
| Registry/schema mismatch | Fail closed for customization; keep current read-only view if available. | Explicit mismatch payload including backend hash set and expected registry version. |
| Version conflict | Show reload/compare/merge flow; never overwrite blindly. | `DASHBOARD_RECIPE_VERSION_CONFLICT` or approved equivalent with latest version pointer. |
| Validation failure | Show field-level errors and keep proposal unsaved. | Stable `errors[]` with `code`, `path`, and message; include registry/schema version. |
| Unsupported widget need | Create WidgetPluginProposal review material, not deployed renderer code. | Clear rejection/proposal path for unregistered widget types. |
| Data source unavailable/degraded | Mark affected widgets degraded and avoid silent fallback to mock data in strict live mode. | Per-widget/source surface status and freshness metadata. |

## Frontend Handoff Notes

When the backend contract is accepted, the execute-plans follow-up should be a
strict adapter slice, not a speculative UI slice:

| Area | Current fact | Add only after BFF/OpenAPI acceptance |
|---|---|---|
| Path helpers | `paths.ts` has Agora helpers for signals, inbox, journal, postmortems, and ask sessions. | Add accepted helpers for registry/checksum, recipe load/save, validation, version history, and rollback. |
| Live adapter | `bffAgora` exposes daily, signals, inbox, journal, and ask sessions. | Add typed recipe/validation methods with strict live failure handling and no mock fallback in strict mode. |
| Contract tests | Existing tests assert current Agora path construction and live adapters. | Add tests for accepted path strings, checksum mismatch fail-closed behavior, conflict payload adaptation, and validation error mapping. |
| Renderer work | `recharts` exists in package dependencies, but renderer acceptance is owned by the AG-FE-DB-001 lane. | Do not use renderer availability as proof that backend registry/checksum or persistence is done. |
| DTO generation | No `src/lib/bff-v1/agora/types.ts` file exists in this checkout. | Generate or hand-code types from the accepted OpenAPI/schema bundle path chosen by the parent, not from the stale file path. |

Suggested adapter-level DTO shape, contingent on parent acceptance:

```ts
type DashboardRecipeEnvelope = {
  data: {
    recipe: unknown;
    widgetSpecs: unknown[];
    registryVersion: string;
    schemaHashes: Record<string, string>;
    version: string | number;
    changelogRef?: string;
  };
  meta: {
    snapshot_at: string;
    capability: "agora.dashboard.v1";
    audience: string;
    concurrency?: { etag?: string; version?: string | number };
    surfaces?: Record<string, unknown>;
  };
};
```

This is an adapter handoff shape only. The parent must replace `unknown` with
the accepted schema authority before implementation.

## Parent Decision Checklist

Claude/Claude2 should settle these before `AG-BE-DB-001` implementation resumes:

| Decision | Needed output |
|---|---|
| SD source | State whether A3/C1 fully replace missing SD `§9`, `§9.6`, and `§17.5`, or provide the missing sections. |
| Schema authority | Choose frozen bundle v1, A3 as a new v2 bundle, or another versioned promotion path. |
| Route authority | Add exact OpenAPI paths/methods/envelopes for registry/checksum, recipe load/save, validation, history, and rollback. |
| Storage authority | Name the DB/table/repository owner for recipes, widget specs, versions, changelog, audit, and rollback. |
| Concurrency | Freeze token mechanism and conflict payload. |
| Validator scope | Confirm whether validation covers standalone WidgetSpecs, full DashboardRecipe, embedded widget references, or all of them. |
| Data source catalog | Publish the data-source field catalog and sensitivity metadata required by A3 validator rule 4. |
| Router placement | Decide whether implementation belongs in `services/control-plane/bff/agora/dashboard/router.py`, `main.py`, or a migration slice. |
| Frontend activation | Confirm the exact hash/handshake condition under which execute-plans can enable recipe customization. |

## Verification Notes

Commands run by Codex2:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DB-001
rg -n "^##? [0-9]|§9|§17|17\\.5|DashboardRecipe|WidgetSpec|dashboard-recipes|widget-specs|optimistic|concurrency|version|ETag|If-Match" docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md
python3 scripts/agora_schema_bundle.py --verify
sha256sum services/control-plane/specs/agora/dashboard_recipe.schema.json \
  services/control-plane/specs/agora/widget_spec.schema.json \
  services/control-plane/specs/agora/capability_manifest.json \
  services/control-plane/openapi/agora_v1.openapi.yaml \
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_registry.v1.json \
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_spec.schema.json \
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/chart_spec.schema.json
rg -n "recharts|echarts|chart\\.js|victory|visx|nivo|d3" /home/lupin/code/execute-plans/package.json /home/lupin/code/execute-plans/src -g '!**/node_modules/**'
```

Bundle verification output was clean for the current frozen Agora bundle. The
recorded hashes were:

| File | sha256 |
|---|---|
| `services/control-plane/specs/agora/dashboard_recipe.schema.json` | `5b9c33653eb8c85b001b5f6f6a802e83e58276a25f6c00e0a030a7094c78a8f6` |
| `services/control-plane/specs/agora/widget_spec.schema.json` | `0749275943dc155afa08dbb8736c336d613daf18b99b42f6c10aec15d2eabedb` |
| `services/control-plane/specs/agora/capability_manifest.json` | `5988cac6d8ca38fc0c51922086c1cc2564b1bb31b2b36ee276e6d363249e9e3e` |
| `services/control-plane/openapi/agora_v1.openapi.yaml` | `4da5ea91923e40c13a9118ee4f784a5d6627e6cb91e4d4712d8fac244912118f` |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_registry.v1.json` | `add7f379f4ff1f3c0c0930a566a269897cd497fb22ef53bbdfecb2b1d85c34d4` |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_spec.schema.json` | `b0ae282fa8b79d7c168a1ec0d4ff83361e46854025bfd92a8b182858c147573a` |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/chart_spec.schema.json` | `8f1dba23ebdf78c2fb7bca43e25c85b2097d6a566930d5b6236da5c0611faaf0` |

## Support Boundary

- Changed artifact: `support/sidecars/AG-BE-DB-001/AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`.
- No L1 canonical policy, OpenAPI, schema bundle, BFF runtime code, registry,
  validator, governance implementation, or execute-plans file was changed.
- This packet should be reviewed by Claude and then considered by the parent
  owner/reviewer before any implementation resumes.
