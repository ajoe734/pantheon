# AG-BE-DB-001 Sidecar Follow-up 3: BFF/FE Join Handoff

| Field | Value |
|---|---|
| Task ID | `AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-DB-001` - DashboardRecipe/WidgetSpec persistence and validator |
| Parent owner / reviewer | Claude / Claude2 |
| Prepared by | Codex |
| Reviewer | Claude |
| Date | 2026-06-20 |
| Mutates canonical truth | false |
| Status | Review approved; ready for parent absorption |

## Purpose

This packet is a support-only join handoff for the BFF and execute-plans
dashboard lanes. It does not replace the approved follow-up 2 packet. It narrows
the current state into the exact backend decisions that must land before the
frontend can safely add strict live DashboardRecipe and WidgetSpec behavior.

This packet does not define canonical routes, promote schemas, edit the Agora
bundle, alter BFF runtime code, change validator behavior, or modify
execute-plans source.

## Closeout State

The closeout status check reports this sidecar as `review_approved` with
Claude as reviewer. The review notes say the evidence snapshot is grounded, the
BFF/FE boundary is clear, canonical truth was not changed, and the sidecar scope
was respected.

Owner closeout keeps the approved packet support-only. No additional backend
route, OpenAPI, schema, validator, registry, governance, or execute-plans source
change is part of this handoff.

## Current Parent State

| Task | Current state | Consequence |
|---|---|---|
| `AG-BE-DB-001` | `blocked`, waiting for Claude2. The active blocker lists missing SD `section 9`, missing `section 9.6` validator source, missing `section 17.5` endpoint/concurrency text, and conflicting WidgetSpec schemas. | Backend persistence and validator implementation must remain stopped. |
| `AG-FE-DB-001` | `blocked`, waiting for Claude2. The active blocker calls out missing UI/layout sections, missing canonical registry/chart schema bundle, missing target widget files, and chart-library uncertainty from its baseline. | Frontend WidgetRegistry/Renderer work must not claim backend parity or route readiness. |
| `AG-FE-DB-001-SIDECAR-ACCEPTANCE` | `done` as a support packet. It documents A3 acceptance criteria and the parent blocker distinction. | Useful for frontend review, but not a backend route/schema authorization. |

## Evidence Snapshot

| Surface | Observed fact |
|---|---|
| SD route/source text | `SD_2026-06-20.md` has sections `0` through `8`, then `section 17` route list and `section 22.1`; no DashboardRecipe `section 9`, `section 9.6`, or `section 17.5` text is present. |
| BFF dashboard router | `services/control-plane/bff/agora/dashboard/router.py` is a placeholder and only lists routes still implemented in `main.py`. |
| OpenAPI | `services/control-plane/openapi/agora_v1.openapi.yaml` includes current daily, markets, watchlist, market-notes, postmortems, alerts triage, decision-journal, journal, and notes routes. It does not include dashboard recipe, widget spec, registry/checksum, validation, version-history, rollback, `ETag`, `If-Match`, or `DASHBOARD_RECIPE_VERSION_CONFLICT` routes. |
| Capability manifest | `agora.dashboard.v1` lists `dashboard_recipe.schema.json` and `widget_spec.schema.json` plus existing dashboard path prefixes. It does not expose `widget_registry.v1.json`, `chart_spec.schema.json`, data-source field catalog hash, or registry checksum parity. |
| A3 registry | `widget_registry.v1.json` has `registry_version: widget_registry.v1`, `schema_version: 1.0.0`, and 42 `entries`. |
| Frozen control-plane WidgetSpec | `services/control-plane/specs/agora/widget_spec.schema.json` has `$id` `https://pantheon/agora/widget_spec/v1` and requires `spec_version`, `widget_id`, `widget_type`, `data_source`, and `created_at`. |
| A3 design-closure WidgetSpec | `design-closure/widget_spec.schema.json` has `$id` `https://pantheon.local/schemas/agora/widget_spec.v1.json` and requires `widget_id`, `widget_type`, `title`, `data_source`, `query`, `chart_spec`, `interactions`, `sensitivity`, and `can_export`. |
| ChartSpec | `design-closure/chart_spec.schema.json` exists outside the frozen control-plane bundle and requires `kind` and `encodings`. |
| execute-plans path helpers | `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` has Agora helpers for signals, inbox, journal, postmortems, and ask sessions only. |
| execute-plans live adapter | `/home/lupin/code/execute-plans/src/lib/bff/agora.ts` exposes daily, signals, inbox, journal, and ask session reads; no DashboardRecipe, WidgetSpec, registry, validation, history, or rollback methods are present. |
| execute-plans chart dependency | The local checkout currently has `recharts` in `package.json` and existing Recharts usages, but that checkout is `main...origin/main [ahead 2, behind 467]`. Treat this as local evidence only, not as proof that AG-FE-DB-001 has an approved chart-renderer path. |

## Backend Decisions That Unblock Frontend Work

| Decision needed from parent owner/reviewer | Why frontend is blocked without it |
|---|---|
| Schema authority: frozen bundle v1, A3 as a promoted v2 bundle, or another versioned bridge. | Frontend cannot generate stable types or validate registry parity while the two WidgetSpec schemas require different fields. |
| Registry/checksum handshake route or capability extension. | Frontend cannot fail closed on registry/schema mismatch without a backend source for registry version, schema hashes, chart schema hash, and data-source catalog hash. |
| Data-source field catalog ownership and exposure. | A3 validator rule 4 requires every encoded field to exist in the data-source field catalog; frontend cannot preview or prevalidate fields without that catalog. |
| DashboardRecipe load and empty-state contract. | Frontend cannot distinguish no recipe, denied access, degraded backend, stale version, and unsupported surface states. |
| DashboardRecipe save/update contract. | Frontend cannot implement strict live save without request body, response envelope, audit/changelog fields, idempotency behavior, and write authority. |
| Optimistic concurrency mechanism. | Frontend cannot implement conflict UI until the parent freezes `version` vs `ETag`/`If-Match`, status code, error code, conflict payload, and latest-version pointer. |
| Validation contract. | Frontend cannot map field-level errors until the BFF freezes whether validation is a standalone route, dry-run save mode, or mandatory save step, plus `errors[]` path format and codes. |
| Version history, rollback, and replay route family. | AG-FE downstream lanes for revision drawer, rollback, and replay need accepted backend paths and authority boundaries. |
| OpenClaw proposal admission path. | `agora-dashboard-compose` outputs proposals. Frontend needs a governed conversion path from proposal to validated recipe version, not an implicit deploy path. |
| Router placement. | Backend implementers need a decision on adding new handlers in `services/control-plane/bff/agora/dashboard/router.py`, extending `main.py`, or separating route migration from persistence. |

## Operator Journey Boundary

### Safe current journey

```text
Operator opens Agora
  -> frontend may call current read surfaces:
     daily, markets, watchlist, market-notes, notes, postmortems,
     alerts triage, decision journal, journal, signals, inbox, ask sessions
  -> frontend must keep custom dashboard recipe save/load, WidgetSpec
     validation, registry checksum parity, version history, rollback, replay,
     and OpenClaw dashboard-compose success paths disabled or backend-not-ready
```

### Contract-ready journey after backend decisions

```text
Operator opens a dashboard workspace
  -> frontend fetches accepted capability plus registry/checksum handshake
  -> frontend refuses customization if local schema/registry hashes differ
  -> frontend loads the active DashboardRecipe and associated WidgetSpecs
  -> operator or OpenClaw proposes a recipe change against a base version
  -> BFF validates registry, chart grammar, data-source fields, scope,
     sensitivity, interactions, resource limits, and no-code/no-broker rules
  -> BFF writes a new recipe version only with the accepted concurrency token
  -> frontend updates from returned version, changelog, and audit metadata
```

### Failure behavior to freeze

| Failure | Required frontend behavior | Backend contract needed |
|---|---|---|
| Registry/schema mismatch | Fail closed for customization and keep any existing read-only dashboard view. | Hash mismatch payload with backend registry version and schema hash set. |
| Version conflict | Offer reload/compare/merge; never overwrite blindly. | `DASHBOARD_RECIPE_VERSION_CONFLICT` or approved equivalent with latest version pointer. |
| Widget validation error | Keep proposal unsaved and show field-level errors. | Stable `errors[]` with `code`, `path`, `message`, registry version, and schema hash. |
| Data source missing or degraded | Mark affected widgets degraded; do not silently fall back to mock data in strict live mode. | Per data-source status, freshness, PIT semantics, and sensitivity metadata. |
| Unsupported widget need | Create or route a `WidgetPluginProposal`; do not deploy ad hoc renderer code. | Rejection/proposal path tied to governance, not runtime execution. |
| OpenClaw proposal conflict | Present it as a proposal requiring operator/reviewer handling. | Base version plus JSON Patch or typed delta contract. |

## Frontend Handoff Notes

When the backend contract is accepted, the execute-plans follow-up should be a
strict adapter and renderer integration slice:

| Area | Add only after backend acceptance |
|---|---|
| Path helpers | Accepted helpers for registry/checksum, recipe load/save, validation, version history, rollback, and proposal admission. |
| Live adapter | Typed methods with strict live failure handling and no mock fallback for save/validation paths. |
| Types | Generate or hand-code from the accepted schema authority, not from the stale control-plane WidgetSpec and not directly from unpromoted design-closure files unless the parent explicitly authorizes that path. |
| Renderer gates | Refuse inactive/unregistered widgets, unallowed chart kinds, unallowed interactions, sensitivity downgrade, forbidden data-source fields, and arbitrary HTML/JS/iframe/script inputs. |
| Tests | Cover path strings, checksum mismatch fail-closed behavior, conflict adaptation, validation error mapping, unsupported widget proposal routing, and no-code/no-broker guards. |

## Parent Absorption Checklist

Claude and Claude2 should settle these before `AG-BE-DB-001` or
`AG-FE-DB-001` resumes implementation:

| Item | Expected output |
|---|---|
| SD replacement | State whether A3/C1 fully replace the missing SD sections or provide the missing DashboardRecipe persistence text. |
| Schema promotion | Pick the authoritative WidgetSpec/ChartSpec/registry bundle path and whether this is v1, v2, or a separate schema-freeze task. |
| Route catalog | Freeze exact paths, methods, request/response envelopes, auth scope, and error codes. |
| Storage ownership | Name the DB/table/repository/write owner for recipes, widget specs, versions, changelog, audit, rollback, and replay evidence. |
| Concurrency | Freeze the token mechanism and conflict payload. |
| Validation | Freeze validator scope across standalone WidgetSpecs, full DashboardRecipe, embedded widget refs, and proposed recipe deltas. |
| Data-source catalog | Publish field catalog, freshness, PIT, sensitivity, allowed aggregates, and owner-service metadata. |
| Frontend baseline | Confirm the execute-plans branch/commit and approved chart dependency baseline before accepting renderer implementation. |

## Verification Notes

Commands run by Codex:

```bash
git status -sb
git branch --show-current
git remote -v
git merge --ff-only origin/dev
git fetch origin dev
git merge --no-edit origin/dev
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DB-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001-SIDECAR-ACCEPTANCE
rg -n "^##? [0-9]|section 9|section 17|17\\.5|DashboardRecipe|WidgetSpec|dashboard-recipes|widget-specs|optimistic|concurrency|version|ETag|If-Match" docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md
rg -n "dashboard-recipes|widget-specs|widget-registry|dashboard/registry|DASHBOARD_RECIPE_VERSION_CONFLICT|ETag|If-Match" services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/bff/main.py services/control-plane/bff/agora/router.py services/control-plane/bff/agora/dashboard/router.py
jq '{"id": .["$id"], "required": .required, "properties": (.properties | keys)}' services/control-plane/specs/agora/widget_spec.schema.json
jq '{"id": .["$id"], "required": .required, "properties": (.properties | keys)}' docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_spec.schema.json
jq '{registry_version, schema_version, entry_count: (.entries | length), first_entry: .entries[0]}' docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_registry.v1.json
sha256sum services/control-plane/specs/agora/dashboard_recipe.schema.json services/control-plane/specs/agora/widget_spec.schema.json services/control-plane/specs/agora/capability_manifest.json services/control-plane/openapi/agora_v1.openapi.yaml docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_registry.v1.json docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_spec.schema.json docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/chart_spec.schema.json
git status -sb
sed -n '1,220p' /home/lupin/code/execute-plans/package.json
rg -n "recharts|echarts|chart\\.js|victory|visx|nivo|d3" /home/lupin/code/execute-plans/package.json /home/lupin/code/execute-plans/package-lock.json /home/lupin/code/execute-plans/src -g '!**/node_modules/**'
sed -n '1,260p' /home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts
sed -n '1,320p' /home/lupin/code/execute-plans/src/lib/bff/agora.ts
```

## Support Boundary

- Primary packet artifact:
  `support/sidecars/AG-BE-DB-001/AG-BE-DB-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`.
- No L1 canonical policy, OpenAPI, schema bundle, BFF runtime code, registry,
  validator, governance implementation, or execute-plans source file was
  changed.
- Parent absorption remains a Claude/Claude2 decision. This sidecar only
  documents the handoff boundary and current evidence.
