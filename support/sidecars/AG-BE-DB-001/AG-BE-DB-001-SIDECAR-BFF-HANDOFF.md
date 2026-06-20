# AG-BE-DB-001 Sidecar: BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-DB-001-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-DB-001` - DashboardRecipe/WidgetSpec persistence and validator |
| Parent owner / reviewer | Claude / Claude2 |
| Prepared by | Codex |
| Reviewer | Claude |
| Date | 2026-06-20 |
| Mutates canonical truth | false |
| Status | Review approved in active task state; ready for owner closeout |

## Purpose

This support-only packet gives the parent owner the current BFF query gap,
operator journey, and execute-plans handoff notes for DashboardRecipe and
WidgetSpec persistence. It does not modify canonical truth, OpenAPI, frozen
schema bundles, BFF runtime code, registry state, validator behavior, or
execute-plans source.

The parent task is blocked in active task state because the implementation brief
points at unresolved design surfaces:

- `SD_2026-06-20.md` has no section 9 DashboardRecipe persistence section.
- The task refers to section 9.6 validator rules, while the concrete design text
  found in this checkout is A3 WidgetRegistry / ChartSpec validator guidance.
- Section 17.5 endpoint + optimistic concurrency is not defined in the SD route
  catalog or Agora OpenAPI.
- `services/control-plane/specs/agora/widget_spec.schema.json` and the A3
  design-closure `widget_spec.schema.json` have different field models and
  different checksums.

## Current BFF Truth

| Surface | Current state | Evidence |
|---|---|---|
| Agora package router | `create_agora_router()` mounts the dashboard sub-router, but the dashboard sub-router is currently a placeholder with no handlers of its own. | `services/control-plane/bff/agora/router.py`; `services/control-plane/bff/agora/dashboard/router.py` |
| Existing dashboard route family | Twelve dashboard-facing operations exist in `main.py`: `GET /bff/agora/daily`, `GET /bff/agora/markets`, `GET /bff/agora/watchlist`, `GET /bff/agora/market-notes`, `GET /bff/agora/postmortems`, `GET /bff/agora/alerts/triage`, `GET /bff/agora/decision-journal`, `GET /bff/agora/journal`, `POST /bff/agora/journal`, `PATCH /bff/agora/journal/{entry_id}`, `GET /bff/agora/notes`, and `POST /bff/agora/notes`. | `services/control-plane/bff/main.py`; `services/control-plane/openapi/agora_v1.openapi.yaml` |
| Route migration status | The dashboard sub-router comments list those route families as still in `main.py`; no `dashboard-recipes`, `widget-specs`, or registry checksum routes are mounted there. | `services/control-plane/bff/agora/dashboard/router.py` |
| OpenAPI route catalog | Agora OpenAPI includes the existing dashboard list/write routes above, but has no `/bff/agora/dashboard-recipes`, `/bff/agora/widget-specs`, `/bff/agora/widget-registry`, or `/bff/agora/dashboard/registry` path. | `services/control-plane/openapi/agora_v1.openapi.yaml` |
| Capability manifest | `agora.dashboard.v1` lists `dashboard_recipe.schema.json` and `widget_spec.schema.json` and prefixes for daily, market, watchlist, postmortem, alerts, journal, notes, and decision-journal routes. It does not list `widget_registry.v1.json` or `chart_spec.schema.json` as frozen bundle schemas. | `services/control-plane/specs/agora/capability_manifest.json` |
| Schema bundle | The frozen AG-XR-001 bundle verifies cleanly for `dashboard_recipe.schema.json`, `widget_spec.schema.json`, `capability_manifest.json`, and `openapi/agora_v1.openapi.yaml`. | `services/control-plane/specs/agora/bundle_index.json`; `python3 scripts/agora_schema_bundle.py --verify` |
| Duplicate journal patch shape | `main.py` also has a later semantic alias `PATCH /bff/agora/journal/{id}`. The canonical decision should settle whether this remains a compatibility alias or is retired when dashboard routes migrate. | `services/control-plane/bff/main.py` |

## OpenAPI and Schema Gap Analysis

| Gap | Impact | Parent-owner decision needed |
|---|---|---|
| Missing DashboardRecipe persistence endpoints | Frontend cannot implement strict live save/load/version flows without inventing paths. | Define exact paths, methods, response envelopes, and error codes before implementation. |
| Missing optimistic concurrency contract | The task asks for optimistic concurrency, but no route defines `version`, `ETag`, `If-Match`, or conflict payload semantics. | Pick one concurrency mechanism and freeze the conflict error shape, including `DASHBOARD_RECIPE_VERSION_CONFLICT` if that remains required. |
| Missing registry checksum handshake route | A3 requires backend validator, frontend renderer, and OpenClaw skill to use the same registry version and schema checksum. No BFF route exposes the active registry/checksum set. | Decide whether the handshake belongs in `/bff/agora/capabilities`, a new dashboard registry route, or another governed route. |
| Frozen bundle does not include A3 registry/chart schemas | `widget_registry.v1.json` and `chart_spec.schema.json` live under design-closure, not the frozen `services/control-plane/specs/agora` bundle. | Decide whether AG-BE-DB-001 is authorized to promote those files into the frozen bundle or should wait for an AG-XR/schema-freeze slice. |
| WidgetSpec schema conflict | Frozen `specs/agora/widget_spec.schema.json` requires `spec_version`, `widget_id`, `widget_type`, `data_source`, and `created_at`; A3 design-closure requires `title`, `query`, `chart_spec`, `interactions`, `sensitivity`, and `can_export`. | Choose the authoritative schema before writing validators or persistence. |
| Validator source ambiguity | Parent brief says `section 9.6 validator`, but the available detailed validator guidance is A3. | Confirm whether A3 validator rules are the required validator source for this task. |
| Storage ownership is undefined | Parent brief does not identify the database/table/repository owner for recipes, widgets, versions, changelog, or rollback. | Define storage backend and write owner before coding persistence. |
| Dashboard route migration ambiguity | Existing dashboard surfaces live in `main.py`, while the Agora dashboard router is placeholder. | Decide whether AG-BE-DB-001 should add new handlers in the dashboard sub-router only, migrate existing handlers, or leave route migration to another slice. |

## Operator Journey

### Current safe journey

```text
Operator opens Agora dashboard
  -> frontend may call existing read surfaces such as daily, markets,
     watchlist, notes, journal, postmortems, and alerts triage
  -> BFF returns current list/detail envelopes from main.py handlers
  -> frontend must not claim DashboardRecipe persistence, registry checksum
     parity, WidgetSpec validation, rollback, or replay support
  -> any custom dashboard save/composition CTA must remain disabled, read-only,
     or clearly blocked until backend contract decisions are frozen
```

### Proposed journey after parent unblocks implementation

```text
Operator opens a dashboard workspace
  -> frontend reads the active registry/checksum bundle from a frozen BFF route
  -> frontend loads the operator's DashboardRecipe for a surface
  -> BFF returns recipe data, widget specs, registry_version, schema checksums,
     version token, and snapshot metadata
  -> operator or OpenClaw agora-dashboard-compose proposes a recipe update
  -> BFF validates every WidgetSpec against the frozen registry, chart grammar,
     allowed data sources, sensitivity rules, and no-code constraints
  -> BFF writes a new recipe version with changelog and optimistic concurrency
  -> frontend updates from the returned version token and handles conflict by
     reloading and showing before/after differences
```

### Failure and degraded journey

```text
Missing registry/checksum route
  -> render dashboard customization as backend-not-ready; do not guess registry

Version conflict
  -> render reload/merge flow using the frozen conflict payload; do not overwrite

Widget validation error
  -> surface field-level validation details; do not accept arbitrary code,
     unmanaged data sources, iframe, JS/HTML, or sensitivity downgrade

OpenClaw proposal cannot fit registry
  -> create a WidgetPluginProposal for governance, not a deployed renderer
```

## Frontend Handoff Notes

Current execute-plans facts checked in `/home/lupin/code/execute-plans`:

| Area | Current frontend state | Handoff note |
|---|---|---|
| Path helpers | `src/lib/bff-v1/paths.ts` has Agora helpers for signals, inbox, journal, postmortems, and ask sessions. | Add dashboard recipe, widget spec, and registry/checksum helpers only after backend paths are accepted. |
| Agora live adapter | `src/lib/bff/agora.ts` adapts daily, signals, inbox, journal, and ask session list behavior; it does not expose DashboardRecipe or WidgetSpec persistence. | Add strict live adapter methods only after response envelopes, conflict behavior, and validation errors are frozen. |
| Existing Agora pages | Dashboard-adjacent pages exist for daily, markets, watchlist, triage, notebook, journal, Ask, committee, trainer, memory, skill coaching, persona lab, and evaluations. | These pages can keep consuming current read surfaces; do not add recipe save/custom-widget success paths before AG-BE-DB-001 unblocks. |
| Tests | Existing live adapter tests cover signals/list behavior and path construction for current helpers. | Add tests for recipe path construction, checksum mismatch handling, conflict handling, validator error display, and strict no-seed fallback when backend contract exists. |

Recommended frontend DTO shape after backend contract decisions:

```ts
type DashboardRecipeEnvelope = {
  data: {
    recipe: DashboardRecipe;
    widgetSpecs: WidgetSpec[];
    registryVersion: "widget_registry.v1";
    schemaChecksums: Record<string, string>;
    version: string | number;
  };
  meta: {
    snapshot_at: string;
    capability: "agora.dashboard.v1";
    audience?: string;
    concurrency?: { etag?: string; version?: string | number };
  };
};
```

## Parent Absorption Checklist

Claude and Claude2 should resolve these before turning the parent
implementation loose:

| Check | Expected parent outcome |
|---|---|
| SD source | Identify the real replacement for missing `SD section 9` or update the task brief to point at A3/C1/design-closure sections only. |
| Endpoint contract | Freeze DashboardRecipe and WidgetSpec paths, methods, request bodies, response envelopes, and auth scope. |
| Concurrency | Freeze `version`/`ETag`/`If-Match` behavior and the `DASHBOARD_RECIPE_VERSION_CONFLICT` payload. |
| Schema authority | Decide whether frozen `services/control-plane/specs/agora/widget_spec.schema.json` or A3 design-closure schema is authoritative. |
| Registry bundle | Decide where `widget_registry.v1.json` and `chart_spec.schema.json` live for backend validation and frontend checksum parity. |
| Validator scope | Confirm validator applies to full DashboardRecipe plus embedded/referenced WidgetSpecs, not just standalone widgets, if that is intended. |
| Route migration | Decide whether new work belongs in `services/control-plane/bff/agora/dashboard/router.py` and whether existing `main.py` dashboard routes should stay in place. |
| Safety boundary | Preserve no arbitrary JS/HTML, no unapproved data source, no other-user/management-only/broker leakage, and no runtime/broker/capital authority. |

## Verification Notes

Closeout evidence gathered by Codex:

```bash
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DB-001-SIDECAR-BFF-HANDOFF
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DB-001
python3 scripts/agora_schema_bundle.py --verify
sha256sum services/control-plane/specs/agora/dashboard_recipe.schema.json \
  services/control-plane/specs/agora/widget_spec.schema.json \
  services/control-plane/specs/agora/capability_manifest.json \
  services/control-plane/openapi/agora_v1.openapi.yaml \
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_registry.v1.json \
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_spec.schema.json \
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/chart_spec.schema.json
```

Expected scope check:

- Only this sidecar support artifact is authored by the task.
- No L1 canonical docs, OpenAPI, schema bundle, BFF runtime implementation,
  registry code, validator code, governance code, or execute-plans files are
  changed.
- The packet does not claim AG-BE-DB-001 is implementable without parent design
  clarification.

## Handoff

This packet is support material for the blocked parent discussion and for a
future frontend/BFF implementation slice. Parent absorption remains a
Claude/Claude2 decision; this sidecar does not promote design-closure material
into canonical runtime truth.
