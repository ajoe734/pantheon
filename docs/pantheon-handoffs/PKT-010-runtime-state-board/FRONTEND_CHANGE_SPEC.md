# PKT-010 Operator Runtime State Board — Frontend Change Spec

## Feature

- Feature ID: `PKT-010-runtime-state-board`
- Screen ID: `screen-operator-runtime-state-board`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the **Operator Runtime State Board** inside `front-ai-trading-system`. This screen gives operators one truthful roster for runtime stage, runtime status, telemetry summary, rollback history entry points, and freshness. All roster composition must come from Pantheon BFF; the UI must not stitch rows together from lower-level runtime or telemetry routes.

## Files to Create or Modify

```
src/pages/operator/OperatorRuntimeStateBoard.tsx   — new runtime-state board screen
src/pages/operator/types.ts                        — add runtime-state response types
src/lib/bffClient.ts                               — add runtime-state fetch helper
```

## API Integration

Use the shared BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch runtime-state board

```
GET /api/v1/operator/runtime-state
Query params:
  deployment_stage=paper,canary,live,frozen
  status=idle,running,paused,degraded
  sort_by=last_updated_at|runtime_id|deployment_stage|status
  sort_order=asc|desc
  page_token
  page_size
```

Expected response shape (see `docs/examples/PKT-010-runtime-state-board.json` for a full example):

```typescript
interface RuntimeStateBoardResponse {
  runtimes: RuntimeStateRow[];
  page_info: {
    next_page_token: string | null;
  };
  meta: {
    snapshot_at: string;
    total: number;
    sort: {
      sort_by: "last_updated_at" | "runtime_id" | "deployment_stage" | "status";
      sort_order: "asc" | "desc";
    };
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable"; source?: string }>;
  };
}

interface RuntimeStateRow {
  runtime_id: string;
  runtime_binding_id: string;
  deployment_stage: string;
  status: string;
  capital_pool_id: string | null;
  plan_ref: { plan_id: string; href: string } | null;
  artifact_ref: { artifact_id: string; artifact_version: string | null } | null;
  telemetry_summary: {
    window: string | null;
    collected_at: string | null;
    metrics: {
      pnl: number | null;
      drawdown: number | null;
      sharpe_ratio: number | null;
      fill_rate: number | null;
      avg_slippage_bps: number | null;
      total_trades: number | null;
    };
  } | null;
  rollback_summary: {
    count: number;
    latest: {
      rollback_id: string;
      action_type: string | null;
      status: string | null;
      from_version: string | null;
      to_version: string | null;
      initiated_at: string | null;
      completed_at: string | null;
    } | null;
    href: string;
  };
  last_updated_at: string | null;
}
```

## Component Structure

### `OperatorRuntimeStateBoard.tsx`

- Fetches `GET /api/v1/operator/runtime-state` on mount.
- Keeps filter state for `deployment_stage`, `status`, `sort_by`, and `sort_order`, but always refreshes the displayed roster from the BFF response.
- Renders the header from `meta.snapshot_at`.
- Renders one table row per `RuntimeStateRow`.
- Shows plan and rollback navigation using `plan_ref.href` and `rollback_summary.href` only.
- Renders explicit unavailable or degraded states from `meta.surfaces.*`.
- Supports server-backed pagination using `page_info.next_page_token`.

## Constraints

- Do not construct rows by calling `RT-03`, `RT-04`, or `TL-02` per runtime.
- Do not reorder, regroup, or repair the roster client-side after the BFF returns it.
- Do not invent write CTAs for rollback, pause, promotion, or runtime intervention.
- If a required row field or `meta.surfaces.*` entry is absent, write `.coordination/requests/PKT-010-runtime-state-board-bff-gap.yaml` and stop implementation.
- Keep degraded and unavailable states visually explicit.

## Degradation Handling

- When `meta.surfaces.runtime_state = "unavailable"`, replace the table with the unavailable-state panel.
- When any other `meta.surfaces` entry is `"degraded"` or `"unavailable"`, show the shared degradation banner and keep the board read-only.
- When `telemetry_summary = null` for a row, render the telemetry-unavailable cell state.
- When `rollback_summary.latest = null` and `count = 0`, render `No recorded rollbacks`.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-010-runtime-state-board-ui-done.yaml` and sync it back so Pantheon can review the return.

## References

- BFF contract: `docs/bff/PKT-010-runtime-state-board.md`
- Screen spec: `docs/screens/PKT-010-runtime-state-board.md`
- Example payload: `docs/examples/PKT-010-runtime-state-board.json`
- Packet family: `docs/pantheon-handoffs/OC-002-operator-console-wave2/PACKET_FAMILY.md`
