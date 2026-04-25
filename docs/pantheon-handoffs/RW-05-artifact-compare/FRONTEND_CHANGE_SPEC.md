# RW-05 Artifact Compare — Frontend Change Spec

## Feature

- Feature ID: `RW-05-artifact-compare`
- Screen ID: `screen-artifact-compare`
- Workbench: Research Workbench
- Packet status: route-live — UI implementation may proceed against the live BFF routes
- Task: `RW-05-ARTIFACT-COMPARE-001`

## Readiness Gate

Pantheon has confirmed **all three** of the following routes are live and returning the published field shape:

1. `GET /api/v1/artifacts` — returns the paginated artifact registry list with `experiment_id`/`ticket_id`/`lineage_id`/`status` filters, `meta.surfaces.artifact_list`, and `allowedActions.canCompare`.
2. `GET /api/v1/artifacts/{artifact_id}` — returns the full artifact detail with `version_chain`, provenance, full metrics, lineage refs, `allowedActions.canCompare`, and `meta.surfaces.artifact_detail`.
3. `GET /api/v1/artifacts/compare` — accepts two to four `artifact_id` params and returns backend-composed `field_pairs`, `change_summary`, and `provenance_pairs`. BFF owns all comparison computation.

Build the production pages against these live surfaces. If any required field is absent or diverges from the synced contract, emit `.coordination/requests/RW-05-artifact-compare-bff-gap.yaml` instead of inventing state or deriving diffs client-side.

## Summary

Build the **Artifact Compare** screens inside `front-ai-trading-system`. This slice lets a researcher select two to four sealed or superseded artifacts from the registry, view a backend-composed side-by-side diff, and navigate to provenance detail. All comparison computation, version ancestry, and CTA authority come from the Pantheon BFF — no client-side diff logic, no artifact list construction from experiment run data.

## Files to Create or Modify

```
src/pages/research/ArtifactCompare.tsx        — new compare view page (artifact selector + diff surface)
src/pages/research/ArtifactRegistry.tsx       — new artifact registry list page
src/pages/research/ArtifactDetail.tsx         — new artifact detail page (or drawer)
src/pages/research/ArtifactTypes.ts           — add artifact and compare types
src/lib/bffClient.ts                          — add RW-05 artifact calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### List artifacts

```
GET /api/v1/artifacts
Query params: experiment_id, ticket_id, lineage_id, status, page_token, page_size (default 20, max 100)
```

Expected response shape (see `docs/examples/RW-05-artifact-compare.json` for full example):

```typescript
interface ArtifactListResponse {
  artifacts: ArtifactSummary[];
  next_page_token: string | null;
  total_count: number;
  meta: {
    surfaces: { artifact_list: "ok" | "degraded" | "unavailable" };
    snapshot_at: string;
  };
}

interface ArtifactSummary {
  artifact_id: string;
  lineage_id: string;
  version: number;
  status: "pending" | "sealed" | "superseded" | "failed";
  name: string;
  artifact_type: string;
  produced_by_experiment_id: string;
  linked_ticket_id: string;
  created_at: string;
  metric_summary: {
    sharpe_ratio: number | null;
    max_drawdown: number | null;
    annualized_return: number | null;
  };
  is_current_version: boolean;
  allowedActions: { canCompare: boolean };
}
```

### Get artifact detail

```
GET /api/v1/artifacts/{artifact_id}
Path param: artifact_id (required)
```

Expected response shape:

```typescript
interface ArtifactDetail {
  artifact_id: string;
  lineage_id: string;
  version: number;
  parent_artifact_id: string | null;
  status: "pending" | "sealed" | "superseded" | "failed";
  name: string;
  artifact_type: string;
  description: string;
  produced_by_experiment_id: string;
  linked_ticket_id: string;
  created_at: string;
  sealed_at: string | null;
  is_current_version: boolean;
  version_chain: ArtifactVersionRef[];
  metrics: Record<string, number>;
  parameters: Record<string, unknown>;
  provenance: {
    linked_experiment: { experiment_id: string; display_label: string };
    linked_ticket: { ticket_id: string; title: string };
    lineage_refs: Array<{
      ref_type: string;
      target_artifact_id: string;
      resolved_link: string;
    }>;
  };
  allowedActions: { canCompare: boolean; canViewDetail: boolean };
  meta: {
    surfaces: { artifact_detail: "ok" | "degraded" | "unavailable" };
    snapshot_at: string;
  };
}

interface ArtifactVersionRef {
  artifact_id: string;
  version: number;
  status: "pending" | "sealed" | "superseded" | "failed";
  produced_by_experiment_id: string;
  created_at: string;
}
```

### Compare artifacts

```
GET /api/v1/artifacts/compare
Query param: artifact_ids (comma-separated, 2–4 artifact_id values; required)
```

Expected response shape:

```typescript
interface ArtifactCompareResponse {
  comparison_id: string;
  artifacts: ArtifactCompareSummary[];
  field_pairs: FieldPair[];
  change_summary: {
    total_fields_compared: number;
    fields_changed: number;
    fields_unchanged: number;
    dominant_change_label: "improved" | "degraded" | "changed" | "unchanged";
  };
  provenance_pairs: ProvenancePair[];
  meta: {
    surfaces: { artifact_compare: "ok" | "degraded" | "unavailable" };
    snapshot_at: string;
    computed_at: string;
  };
}

interface ArtifactCompareSummary {
  artifact_id: string;
  version: number;
  name: string;
  status: "sealed" | "superseded";
}

interface FieldPair {
  field_key: string;
  display_label: string;
  group: "performance" | "risk" | "parameters" | "metadata";
  values: Array<{ artifact_id: string; value: unknown }>;
  change_label: "improved" | "degraded" | "changed" | "unchanged";
  delta_magnitude: number;
  delta_direction: "up" | "down" | "none";
  delta_display: string;
}

interface ProvenancePair {
  artifact_id: string;
  linked_experiment: { experiment_id: string; display_label: string };
  linked_ticket: { ticket_id: string; title: string };
}
```

## Component Structure

### `ArtifactRegistry.tsx`

- Route: `/research/artifacts` (or linked from `/research/compare` as the artifact selector).
- Fetches `GET /api/v1/artifacts` on mount; supports `experiment_id`, `ticket_id`, `lineage_id`, and `status` filter params.
- Each row: `name`, `artifact_type`, `version`, `status` badge, `created_at`, `metric_summary`, `is_current_version`.
- Row click opens artifact detail or navigates to detail page.
- Pagination via `next_page_token`.
- Multi-select for compare: only allow selection when `allowedActions.canCompare === true`. Do not derive selectability from `status` alone.
- `pending` and `failed` artifacts must be rendered with a non-selectable indicator; do not hide them.

### `ArtifactDetail.tsx`

- Route: `/research/artifacts/:artifact_id` (or rendered as a drawer).
- Fetches `GET /api/v1/artifacts/{artifact_id}` on mount.
- Renders `version_chain[]` as a timeline — do not reconstruct the chain by calling the list API multiple times.
- Renders `provenance.*` fields using BFF-provided `display_label` values. Do not resolve labels from raw ids.
- Renders `lineage_refs[]` using `resolved_link` only — do not construct artifact comparison URLs from raw ids.
- Shows Compare CTA only when `allowedActions.canCompare === true`.

### `ArtifactCompare.tsx`

- Route: `/research/compare`.
- Accepts `artifact_ids` query param (comma-separated) for deep-link support.
- Fetches `GET /api/v1/artifacts/compare?artifact_ids=...` when two or more selections are confirmed.
- Renders `field_pairs[]` grouped by the BFF-provided `group` key (`performance`, `risk`, `parameters`, `metadata`). Do not infer group from field key naming.
- Renders `change_label` and `delta_display` as provided — do not recalculate deltas from raw metric values.
- Renders `provenance_pairs[]` as an evidence drawer or provenance rail per artifact column.
- Renders `change_summary` as a header strip.

## Degradation Handling

| `meta.surfaces.artifact_list` | Required behavior |
|---|---|
| `ok` | Normal display |
| `degraded` | Non-dismissable staleness banner; results visible |
| `unavailable` | Replace list content with unavailable notice |

| `meta.surfaces.artifact_detail` | Required behavior |
|---|---|
| `ok` | Normal display |
| `degraded` | Non-dismissable staleness banner; detail visible |
| `unavailable` | Replace detail panel with unavailable notice |

| `meta.surfaces.artifact_compare` | Required behavior |
|---|---|
| `ok` | Render comparison normally |
| `degraded` | Show last-known comparison data with non-dismissable staleness banner |
| `unavailable` | Replace comparison panels with unavailable notice; suppress compare CTA |

## Artifact Lifecycle States

| State | Selectable for compare | Display |
|---|---|---|
| `pending` | No — non-selectable indicator | Show with `pending` badge; do not hide |
| `sealed` | Yes — when `allowedActions.canCompare` is `true` | Normal display |
| `superseded` | Yes — when `allowedActions.canCompare` is `true` | Show with `superseded` badge |
| `failed` | No — non-selectable indicator | Show with `failed` badge; do not hide |

`allowedActions.canCompare` is the sole source of compare selection authority. Do not derive it from `status` alone.

## State Requirements

Each data panel must handle:

- `loading`: skeleton or spinner
- `empty`: explicit empty copy (no blank panels)
- `degraded`: staleness banner with available data
- `unavailable`: degradation placeholder
- `error`: error copy with retry option

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Do not construct artifact lists from `artifact_ids` arrays on experiment run records.
- Do not reconstruct `version_chain` by issuing multiple paginated artifact list calls filtered by `lineage_id`.
- Do not resolve `provenance.lineage_refs` from raw storage refs.
- Do not compute field diffs by comparing two raw artifact JSON payloads.
- Do not derive `change_label`, `delta_magnitude`, or `group` assignments client-side.
- `group` keys for `field_pairs` are BFF-defined — do not hard-code group labels from field key naming conventions.
- If any required field is absent from the BFF response, write `.coordination/requests/RW-05-artifact-compare-bff-gap.yaml` and stop implementation.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/RW-05-artifact-compare-ui-done.yaml` using the same pattern as `.coordination/requests/RW-04-experiment-launch-ui-done.yaml`.

## References

- BFF contract: `docs/bff/RW-05-artifact-compare.md`
- Example payload: `docs/examples/RW-05-artifact-compare.json`
- Packet family: `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`
