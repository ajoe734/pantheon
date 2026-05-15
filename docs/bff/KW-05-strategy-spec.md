# KW-05 Strategy Spec BFF Contract

## Status

**Route-live.** The `2026-04-22` follow-up architecture response closes the
minimum versioning, lifecycle, and compare-semantics questions for `KW-05`.
The module is no longer blocked on system design. The browse/detail/history and
compare route family is now implemented in the current BFF, and the frontend
activation packet is published at
`docs/pantheon-handoffs/KW-05-strategy-spec/FRONTEND_CHANGE_SPEC.md`.
Remaining work is UI activation against the live versioned-spec contract.

Task: `KW-05-STRATEGY-SPEC-001`

## Purpose

Provide the versioned Strategy Spec surface for the Knowledge Workbench so
operators can browse strategy families, inspect immutable spec versions, follow
citations and memory anchors, and compare two versions without the frontend
diffing arbitrary JSON or inventing version lineage.

## Upstream dependencies

- `KW-01` for institutional-memory anchors
- `KW-03` for evidence-reference read truth

## Canonical version model

### Version identity

Each strategy-spec family and version must expose:

| Field | Meaning |
|---|---|
| `strategy_id` | logical strategy identity shared across versions |
| `spec_version_id` | immutable version identity |
| `spec_version` | human-readable version label |

Optional backend convenience fields such as `version_seq` may exist, but
canonical identity is anchored on `strategy_id + spec_version_id`.

### Ancestry

Each version must expose:

- `parent_spec_version_id`
- `derived_from_source_refs[]`

The frontend must not reconstruct ancestry from timestamps or guessed sequence
numbers.

### Lifecycle

Canonical lifecycle states:

- `draft`
- `candidate`
- `approved`
- `retired`

### Immutability

Once a version reaches `candidate` or higher, it is immutable.

Any change to a `candidate`, `approved`, or `retired` version must create a new
`spec_version_id`.

## Read routes

### List strategy specs

- `GET /api/v1/knowledge/strategy-specs`

Supported query params:

- `lifecycle_state` — `draft | candidate | approved | retired | all`
- `source_kind`
- `persona_id`
- `include_retired`
- `page_token`
- `page_size`

Required response fields:

- `items[]`
  - `object_ref`
  - `strategy_id`
  - `current_spec_version_id`
  - `current_spec_version`
  - `title`
  - `lifecycle_state`
  - `source_kind`
  - `hypothesis_excerpt`
  - `version_count`
  - `last_modified_at`
  - `route_href`
- `page_info.next_page_token`
- `page_info.page_size`
- `page_info.has_more`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.strategy_spec_list.state` — `ok | degraded | unavailable`

### Get versioned strategy spec detail

- `GET /api/v1/knowledge/strategy-specs/{strategy_id}`

Supported query params:

- `version = current | {spec_version_id} | {spec_version}`

Required response fields:

- `object_ref`
  - `type = "StrategySpec"`
  - `id = spec_version_id`
- `strategy_id`
- `spec_version_id`
- `spec_version`
- `parent_spec_version_id`
- `derived_from_source_refs[]`
- `lifecycle_state`
- `title`
- `hypothesis`
- `objective`
- `market_scope`
- `execution_profile`
- `evaluation_plan`
- `governance`
- `citation_bundle`
- `allowedActions.canSubmitForApproval`
- `allowedActions.canRetire`
- `allowedActions.canCompare`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.strategy_spec_detail.state`
- `meta.surfaces.citation_bundle.state`
- `meta.surfaces.version_ancestry.state`

### List version history

- `GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions`

Required response fields:

- `strategy_id`
- `versions[]`
  - `spec_version_id`
  - `spec_version`
  - `lifecycle_state`
  - `created_at`
  - `created_by`
  - `parent_spec_version_id`
  - `route_href`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.version_history.state`

### Compare two versions

- `GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare`

Accepted query aliases:

- `left_version` / `right_version`
- `base_version` / `target_version`

Canonical response fields:

- `strategy_id`
- `left_spec_version_id`
- `right_spec_version_id`
- `changed_sections[]`
- `breaking_changes[]`
- `evidence_refs[]`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.strategy_spec_compare.state`

Canonical compare output must be backend-generated.
Frontend must not diff arbitrary JSON.

## Citation bundle

The citation bundle remains backend-composed and may include:

- `evidence_refs[]`
- `memory_anchors[]`
- `insight_citations[]`

The frontend must use BFF-resolved link metadata and must not construct
strategy-spec citation navigation paths from raw refs.

## Degradation rules

| Surface | Allowed states |
|---|---|
| `strategy_spec_list` | `ok | degraded | unavailable` |
| `strategy_spec_detail` | `ok | degraded | unavailable` |
| `citation_bundle` | `ok | partial | degraded | unavailable` |
| `version_ancestry` | `ok | degraded | unavailable` |
| `strategy_spec_compare` | `ok | degraded | unavailable` |

`partial` is only valid for the citation bundle or other non-authoritative read
sub-surfaces where enrichment is incomplete.

Freshness must be represented through `meta.staleness`.

## Non-goals

- The frontend must not infer lifecycle state from unrelated provenance fields.
- The frontend must not derive version ancestry from timestamps or version-label
  sorting.
- The frontend must not diff raw spec JSON locally.
- This contract does not define spec-authoring write routes beyond the read /
  compare surfaces needed for the workbench.

## Example Payload

- `docs/examples/KW-05-strategy-spec.json`
