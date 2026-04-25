# PKT-014 Operator Paper / Live Drift — Frontend Change Spec

## Feature

- Feature ID: `PKT-014-paper-live-drift`
- Screen ID: `screen-operator-paper-live-drift`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the **Operator Paper / Live Drift** screen inside `front-ai-trading-system`. This screen gives operators one backend-owned comparison object for paper baseline, observed live state, drift groups, threshold evaluation, evidence refs, and recommended follow-up actions.

## Files to Create or Modify

```
src/pages/operator/OperatorPaperLiveDrift.tsx    — new drift-review screen
src/pages/operator/types.ts                      — add paper-live-drift response types
src/lib/bffClient.ts                             — add paper-live-drift fetch helper
```

## API Integration

Use the shared BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch paper/live drift

```
GET /api/v1/operator/paper-live-drift/{runtime_id}
```

Expected response shape (see `docs/examples/PKT-014-paper-live-drift.json` for a full example):

```typescript
interface OperatorPaperLiveDriftResponse {
  runtime_id: string;
  plan_ref: { plan_id: string; href: string } | null;
  artifact_ref: { artifact_id: string; artifact_version: string } | null;
  paper_baseline: Record<string, unknown> | null;
  observed_state: Record<string, unknown> | null;
  drift_groups: Array<{
    group_id: string;
    label: string;
    status: "ok" | "watch" | "breached" | "unavailable";
    metrics: Array<{
      metric_id: string;
      label: string;
      baseline_value: unknown;
      observed_value: unknown;
      delta: unknown;
      threshold: string;
      status: "ok" | "watch" | "breached" | "unavailable";
      unit: string;
    }>;
  }>;
  threshold_evaluation: {
    overall_status: string;
    summary: string;
    breached_metric_ids: string[];
  };
  evidence_refs: Array<{ ref_id: string; type: string; href: string | null }>;
  recommended_actions: Array<{
    action_id: string;
    label: string;
    reason: string;
    target_ref: { surface_id: string; label: string; href: string; target_id?: string };
  }>;
  meta: {
    snapshot_at: string;
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable"; source?: string }>;
  };
}
```

## Component Structure

### `OperatorPaperLiveDrift.tsx`

- Fetches `GET /api/v1/operator/paper-live-drift/{runtime_id}` on mount.
- Renders the comparison header from `runtime_id`, `plan_ref`, `artifact_ref`, `paper_baseline`, and `observed_state`.
- Renders `threshold_evaluation` prominently above the drift groups.
- Renders one drift group per `drift_groups[]` entry in backend-owned order.
- Renders the evidence drawer from `evidence_refs[]`.
- Renders `recommended_actions[]` exactly as supplied.

## Constraints

- Do not derive drift metrics from raw policy text, approval decisions, incidents, or telemetry primitives in the browser.
- Do not infer promotion, rollback, or evolution actions from raw metric values.
- Do not reorder drift groups or recommended actions.
- If any required field or `meta.surfaces.*` entry is missing, write `.coordination/requests/PKT-014-paper-live-drift-bff-gap.yaml` and stop implementation.

## Degradation Handling

- `meta.surfaces.paper_live_drift = "degraded"` keeps the view visible and read-only.
- `meta.surfaces.paper_live_drift = "unavailable"` renders the explicit unavailable state.
- `paper_baseline = null` and `observed_state = null` are only valid when the drift report is unavailable.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-014-paper-live-drift-ui-done.yaml` and sync it back so Pantheon can review the return.

## References

- BFF contract: `docs/bff/PKT-014-paper-live-drift.md`
- Screen spec: `docs/screens/PKT-014-paper-live-drift.md`
- Example payload: `docs/examples/PKT-014-paper-live-drift.json`
- Packet family: `docs/pantheon-handoffs/OC-002-operator-console-wave2/PACKET_FAMILY.md`
