# CW-04 Red-team Memo — Frontend Change Spec

## Feature

- Feature ID: `CW-04-redteam-memo`
- Screen IDs: `screen-consultation-redteam-memo-list`, `screen-consultation-redteam-memo-detail`
- Workbench: Consultation Workbench
- Packet status: route-live — UI implementation may proceed against the live memo routes
- Task: `CW-04-REDTEAM-MEMO-001`

## Readiness Gate

Pantheon has confirmed **both** of the following routes are live and returning the
published field shape:

1. `GET /api/v1/consult/memos` — returns the memo list envelope with
   pagination, `meta.staleness`, backend-owned
   `meta.surfaces.redteam_memo.state`, and BFF-provided `route_href` values for
   list-to-detail navigation.
2. `GET /api/v1/consult/memos/{memo_id}` — returns memo detail with the
   backend-owned `session_to_memo_mapping`, `evidence_refs[].link`, and
   `allowedActions.canInitiateGovernanceReview`.

Build the production pages against these live surfaces. If any required field is
absent or diverges from the synced contract, emit
`.coordination/requests/CW-04-redteam-memo-bff-gap.yaml` instead of inventing
memo lifecycle, mapping, or governance authority locally.

## Summary

Build the **Red-team Memo** list and detail screens inside
`front-ai-trading-system`. This slice renders finalized memo summaries,
recommendations, memo-level evidence links, and the backend-gated governance
handoff CTA. All memo lifecycle, mapping, evidence navigation, and review
authority come from the Pantheon BFF. The frontend must not turn this surface
into a client-authored publish workflow or reconstruct the memo from raw
request, transcript, or committee data.

## Files to Create or Modify

```text
src/pages/consultation/RedTeamMemoList.tsx    — new memo list page
src/pages/consultation/RedTeamMemoDetail.tsx  — new memo detail page
src/pages/consultation/types.ts               — add memo list/detail response types
src/lib/bffClient.ts                          — add CW-04 memo list/detail fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch`
or `axios` calls in component files.

### List red-team memos

```http
GET /api/v1/consult/memos
```

Query parameters (all optional):

| Param | Type | Notes |
|---|---|---|
| `status` | string | `draft` or `published` |
| `page_token` | string | Opaque pagination cursor |
| `page_size` | number | Default 20 |

Expected response shape (see `docs/examples/CW-04-redteam-memo.json` for full
examples):

```typescript
interface RedTeamMemoListResponse {
  items: RedTeamMemoSummary[];
  page_info: {
    next_page_token: string | null;
    page_size: number;
    total?: number;
  };
  meta: {
    snapshot_at: string;
    staleness: {
      status: "fresh" | "stale";
      as_of: string;
      max_age_seconds?: number;
      served_from?: string;
    };
    surfaces: {
      redteam_memo: {
        state: "ok" | "degraded" | "unavailable";
      };
    };
  };
}

interface RedTeamMemoSummary {
  object_ref: {
    type: "ConsultMemo";
    id: string;
  };
  memo_id: string;
  memo_type: "red_team";
  status: "draft" | "published";
  linked_request_id: string | null;
  recommendation_count: number;
  published_at: string | null;
  created_at: string;
  route_href: string;
}
```

### Get red-team memo detail

```http
GET /api/v1/consult/memos/{memo_id}
Path param: memo_id (required)
```

Expected response shape:

```typescript
interface RedTeamMemoDetail {
  object_ref: {
    type: "ConsultMemo";
    id: string;
  };
  memo_id: string;
  memo_type: "red_team";
  status: "draft" | "published";
  lifecycle_state: "draft" | "published";
  author_ref: string | null;
  linked_request_id: string | null;
  linked_session_id: string | null;
  session_to_memo_mapping: {
    mapping_id: string;
    source_session_id: string;
    transcript_id: string;
    transcript_version: string | null;
    memo_id: string;
    memo_type: "red_team";
    created_by: {
      actor_type: string;
      actor_id: string;
    };
    evidence_refs: string[];
    mapping_status: string;
    created_at: string;
  };
  summary: string | null;
  recommendations: string[];
  evidence_refs: Array<{
    id: string;
    evidence_type: string;
    artifact_ref: string | null;
    description: string | null;
    link: string;
  }>;
  published_at: string | null;
  created_at: string;
  supersedes_memo_id: string | null;
  superseded_by_memo_id: string | null;
  allowedActions: {
    canInitiateGovernanceReview: boolean;
  };
  meta: {
    snapshot_at: string;
    staleness: {
      status: "fresh" | "stale";
      as_of: string;
      max_age_seconds?: number;
      served_from?: string;
    };
    surfaces: {
      redteam_memo: {
        state: "ok" | "degraded" | "unavailable";
      };
    };
  };
}
```

## Component Structure

### `RedTeamMemoList.tsx`

- Route: `/consultation/memos`
- Fetches `GET /api/v1/consult/memos` on mount and supports the published
  `status`, `page_token`, and `page_size` query params only.
- Each row renders `memo_id`, `status`, `linked_request_id`,
  `recommendation_count`, `published_at`, and `created_at`.
- Row navigation must use `route_href` from the BFF response. Do not construct
  memo routes from `memo_id`.
- `recommendation_count` is the authoritative list summary. Do not fetch detail
  rows to compute counts client-side.
- Use `meta.staleness.status` to render freshness copy and
  `meta.surfaces.redteam_memo.state` to control degraded/unavailable behavior.
  Do not treat `stale` as a surface-state enum.

### `RedTeamMemoDetail.tsx`

- Route: `/consultation/memos/:memo_id`
- Fetches `GET /api/v1/consult/memos/{memo_id}` on mount.
- Header renders `memo_id`, `memo_type`, `status`, `author_ref`,
  `linked_request_id`, `linked_session_id`, `published_at`, and optional
  supersession metadata.
- Recommendations render as a plain ordered list from `recommendations[]`.
  Do not add severity chips, workflow states, or pagination.
- Evidence drawer is memo-level and renders from top-level `evidence_refs[]`.
  Use only `evidence_refs[].link` for navigation. Do not construct URLs from
  `id` or `artifact_ref`.
- Mapping panel renders fields from `session_to_memo_mapping` verbatim:
  `mapping_id`, `source_session_id`, `transcript_id`, `transcript_version`,
  `mapping_status`, `created_at`, and `created_by`.
- Governance CTA is visible only when
  `allowedActions.canInitiateGovernanceReview === true`. Do not infer the CTA
  from `status`, `published_at`, or `meta.staleness`.

## Degradation Handling

| `meta.staleness.status` | Required behavior |
|---|---|
| `fresh` | Normal freshness treatment |
| `stale` | Show a non-dismissable staleness banner; data may still be visible depending on the surface state |

| `meta.surfaces.redteam_memo.state` | Required behavior |
|---|---|
| `ok` | Normal memo rendering |
| `degraded` | Show the last-known memo content with a non-dismissable degraded banner; never force the governance CTA on |
| `unavailable` | Replace the list or detail content with the canonical unavailable notice |

Degraded memo-detail responses keep the published required-field shape. Treat
missing required detail fields as a real contract gap even when the surface is
degraded; only the unavailable branch suppresses memo content.

## Constraints

- Use the existing BFF client only. Do not add raw network calls in component
  files.
- Do not invent a publish workflow, review workflow state, or memo lifecycle
  beyond the BFF fields.
- Do not derive `session_to_memo_mapping` from raw request, transcript, or
  committee payloads.
- Do not construct evidence or memo links from opaque ids when the BFF provides
  `route_href` or `link`.
- Do not assume list rows contain `author_ref`; that field is detail-only.
- Do not conflate `meta.staleness.status` with
  `meta.surfaces.redteam_memo.state`.
- Do not add per-recommendation severity, workflow status, or approval state in
  v1.

## Completion Handoff

When the UI implementation is ready, write
`.coordination/requests/CW-04-redteam-memo-ui-done.yaml` using
`.coordination/requests/CW-04-redteam-memo-ui-done.example.yaml` as the
template.

## References

- BFF contract: `docs/bff/CW-04-redteam-memo.md`
- Screen spec: `docs/screens/CW-04-redteam-memo.md`
- Example payload: `docs/examples/CW-04-redteam-memo.json`
- Contract-ready: `.coordination/responses/CW-04-redteam-memo-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/CW-04-redteam-memo-lovable-ui-task.yaml`
- Lovable prompt: `.coordination/responses/CW-04-redteam-memo-lovable-prompt.md`
- BFF-gap template: `.coordination/requests/CW-04-redteam-memo-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/CW-04-redteam-memo-ui-done.example.yaml`
- Packet family: `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`
