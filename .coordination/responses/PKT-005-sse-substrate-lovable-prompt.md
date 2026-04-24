## PKT-005-sse-substrate — Lovable follow-up patch (cycle 2)

The base SSE substrate implementation at commit
`c08acb3ea59f4c56ced578820aa6a5129a309de1` has been reviewed by Pantheon.
Five concrete fixes are required before this loop can be closed.

**Do not re-implement from scratch.** Patch only the five items below.
All other code (reconnect manager, reconciler deduplication, kill-switch gating,
banner sourcing) already passes review and must not be changed.

---

### Fix 1 — Store `last_event_id` only after successful reconciler apply

**File:** `src/lib/sseClient.ts`

The `message` event handler currently advances the replay cursor on
receipt, before the reconciler has validated the event. The PKT-005 contract
requires the cursor to advance only when the event is **successfully applied**.

Step 1 — Remove the auto-update block inside the `message` listener (around
lines 111-114):

```typescript
// REMOVE these lines:
if (typeof event['id'] === 'string' && event['id']) {
  this.lastEventId = event['id'];
}
```

Step 2 — Add a public `markApplied` method to `SseClient`:

```typescript
/** Advance the replay cursor. Call this after the reconciler accepts and applies an event. */
markApplied(id: string): void {
  if (id) {
    this.lastEventId = id;
  }
}
```

Step 3 — In every SSE event handler in every screen, after
`reconciler.accept(rawEvent)` returns `{ type: 'accepted' }`, call
`sseClientRef.current?.markApplied(result.event.id)` (or the appropriate
client ref) before applying any state mutation.

For `IncidentDetail.tsx` which has three clients stored in `sseClientsRef.current`
as `[runtimeClient, incidentClient, killSwitchClient]`, use
`sseClientsRef.current[0]`, `[1]`, and `[2]` respectively.

---

### Fix 2 — Add 60-second "Real-time updates may be delayed" footer note

**Files:** `src/pages/operator/DeploymentReviewConsole.tsx`,
`src/pages/operator/IncidentDetail.tsx`,
`src/pages/operator/PostIncidentReviewConsole.tsx`

The PKT-005 screen spec requires a "Real-time updates may be delayed" note
after 60 seconds of no SSE events on a connected surface.

In each of the three screen files:

Step 1 — Add state and ref at the component level (with the other `useState` / `useRef` declarations):

```typescript
const [updatesMayBeDelayed, setUpdatesMayBeDelayed] = useState(false);
const delayTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
```

Step 2 — Inside the SSE `useEffect`, define a local helper:

```typescript
function resetDelayTimer() {
  setUpdatesMayBeDelayed(false);
  if (delayTimerRef.current) clearTimeout(delayTimerRef.current);
  delayTimerRef.current = setTimeout(() => setUpdatesMayBeDelayed(true), 60000);
}
```

Step 3 — Call `resetDelayTimer()` at the top of each SSE event handler
(before calling `reconciler.accept`).

Step 4 — In the `onStateChange` callback:
- When `state === 'connected'`: call `resetDelayTimer()`
- Otherwise: call `setUpdatesMayBeDelayed(false)` and clear the timer

For `IncidentDetail.tsx` which has three clients and a shared
`updateConnectionState` helper, call `resetDelayTimer()` when the dominant
state transitions to `'connected'` and clear it when it transitions away from
`'connected'`.

Step 5 — In the effect cleanup, clear and null the timer:
```typescript
if (delayTimerRef.current) clearTimeout(delayTimerRef.current);
delayTimerRef.current = null;
```

Step 6 — In the footer of each screen, after the connection-state text, add:

```tsx
{sseConnectionState === 'connected' && updatesMayBeDelayed && (
  <span className="text-yellow-600">Real-time updates may be delayed</span>
)}
```

---

### Fix 3 — Apply accepted events to visible host state (not informational no-ops)

#### `src/pages/operator/DeploymentReviewConsole.tsx`

When `reconciler.accept()` returns
`{ type: 'accepted', event: { type: 'runtime_state_changed' } }`
and `event.data.runtime_id === runtimeBindingId`, trigger a detail refresh:

```typescript
setDetailRefreshKey(k => k + 1);
```

(The `setDetailRefreshKey` setter is already in scope via the outer closure.)

#### `src/pages/operator/IncidentDetail.tsx`

**For `runtimeClient` handler** — when `runtime_state_changed` is accepted
and `event.data.runtime_id === runtimeId`, call `setRefreshKey(k => k + 1)`
to trigger a BFF re-fetch of the incident detail.

**For `incidentClient` handler** — when `incident_updated` is accepted
and `event.data.incident_id === incidentId` (the local const from the effect,
which equals `response.data.incident.incident_id`), apply the status update
directly without a full refresh:

```typescript
const ev = result.event; // TypeScript: IncidentUpdatedEvent
setResponse(prev => {
  if (!prev?.data?.incident) return prev;
  return {
    ...prev,
    data: {
      ...prev.data,
      incident: {
        ...prev.data.incident,
        status: ev.data.status as typeof prev.data.incident.status,
      },
    },
  };
});
```

#### `src/pages/operator/PostIncidentReviewConsole.tsx`

The existing handler already updates the `incidents` list row. In addition,
when `incident_updated` is accepted and
`ev.data.incident_id === selectedIncidentId`, also update `detail`:

```typescript
setDetail(prev => {
  if (!prev?.incident) return prev;
  return {
    ...prev,
    incident: {
      ...prev.incident,
      status: ev.data.status as typeof prev.incident.status,
    },
  };
});
```

---

### Fix 4 — Surface `bff-gap` instead of silently dropping it

**Files:** `src/pages/operator/DeploymentReviewConsole.tsx`,
`src/pages/operator/IncidentDetail.tsx`,
`src/pages/operator/PostIncidentReviewConsole.tsx`,
`src/pages/operator/IncidentActionDrawerPage.tsx`

In each of the four files:

Step 1 — Add state (alongside the other `useState` declarations):

```typescript
const [sseBffGapFields, setSseBffGapFields] = useState<string[]>([]);
```

Step 2 — In each SSE event handler, handle the `bff-gap` result instead of
falling through silently:

```typescript
if (result.type === 'bff-gap') {
  setSseBffGapFields(prev => {
    const next = result.missingFields.filter(f => !prev.includes(f));
    return next.length > 0 ? [...prev, ...next] : prev;
  });
  return;
}
```

Step 3 — Show the gap in the UI. In the footer of
`DeploymentReviewConsole.tsx`, `IncidentDetail.tsx`, and
`PostIncidentReviewConsole.tsx`, add after the connection-state span:

```tsx
{sseBffGapFields.length > 0 && (
  <span className="ml-2 text-red-600 text-xs font-medium">
    SSE BFF gap: {sseBffGapFields.join(', ')}
  </span>
)}
```

For `IncidentActionDrawerPage.tsx`, add a small alert inside the existing
`Card` content area (after the query-field grid), visible only when
`canOpenDrawer` is true and `sseBffGapFields.length > 0`:

```tsx
{canOpenDrawer && sseBffGapFields.length > 0 && (
  <Alert variant="destructive" className="mt-2">
    <AlertTriangle className="h-4 w-4" />
    <AlertTitle>SSE BFF gap detected</AlertTitle>
    <AlertDescription className="font-mono text-xs">
      Missing: {sseBffGapFields.join(', ')}
    </AlertDescription>
  </Alert>
)}
```

---

### Fix 5 — Publish the paired `frontend-feedback` request

Create the file
`.coordination/requests/PKT-005-sse-substrate-frontend-feedback.yaml`
in the front repo. Use
`.coordination/requests/PKT-005-degradation-banner-frontend-feedback.yaml`
as the exact structural template.

Content (replace `<FINAL_PUBLICATION_COMMIT>` with the final pushed front-repo
commit that contains the code fixes, the QA update, and both request files.
Do **not** use an earlier code-only commit. The publisher and receiver validate
that the payload `source_commit` matches the dispatch envelope commit and that
`payload_path` exists at that same commit):

```yaml
feature_id: PKT-005-sse-substrate
type: frontend-feedback
source_repo: ajoe734/front-ai-trading-system
source_branch: main
source_commit: <FINAL_PUBLICATION_COMMIT>
workbench: operator-console
screen_id: surface-operator-sse-reconciliation
status: completed
feedback_bundle_dir: docs/pantheon-feedback/PKT-005-sse-substrate
feedback_path: docs/pantheon-feedback/PKT-005-sse-substrate/LOVABLE_CHANGE_FEEDBACK.md
api_gap_requests_path: docs/pantheon-feedback/PKT-005-sse-substrate/API_GAP_REQUESTS.json
ui_decisions_path: docs/pantheon-feedback/PKT-005-sse-substrate/UI_DECISIONS.md
qa_status_path: docs/pantheon-feedback/PKT-005-sse-substrate/QA_STATUS.md
blocking_summary: ""
changed_files:
  - src/lib/sseClient.ts
  - src/pages/operator/DeploymentReviewConsole.tsx
  - src/pages/operator/IncidentDetail.tsx
  - src/pages/operator/PostIncidentReviewConsole.tsx
  - src/pages/operator/IncidentActionDrawerPage.tsx
pantheon_review_hint: review-ui
summary: >-
  PKT-005 SSE substrate cycle-2 patch: last_event_id advances only after
  successful reconciler apply; 60-second delayed-update footer note added to
  all three live surfaces; accepted runtime_state_changed and incident_updated
  events applied to visible host state; bff-gap results surfaced in footers
  instead of silently dropped; paired frontend-feedback request published.
```

Also update `docs/pantheon-feedback/PKT-005-sse-substrate/QA_STATUS.md` to
document the cycle-2 fixes and confirm the five new acceptance checks pass.

---

### Completion handoff

When publishing the completion handoff:

1. Create `.coordination/requests/PKT-005-sse-substrate-frontend-feedback.yaml`
   as described in Fix 5 above.
2. Update `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml`:
   - Set `source_commit` to the same final publication commit used in the
     `frontend-feedback` file.
   - Update `summary` to mention the cycle-2 patch.
   - Add the new `frontend-feedback` file to `changed_files`.
3. Update `docs/pantheon-feedback/PKT-005-sse-substrate/QA_STATUS.md` as
   described above.
4. Publish the code fixes, the QA update, and both request files in the same
   final front-repo commit. That commit must contain:
   - `.coordination/requests/PKT-005-sse-substrate-frontend-feedback.yaml`
   - `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml`
   - `docs/pantheon-feedback/PKT-005-sse-substrate/QA_STATUS.md`
5. Ensure both request payloads use that same final publication commit as
   `source_commit`; the cycle is not valid if either payload points at an
   earlier code-only commit that does not contain the request file.
6. Sync both request files back to GitHub and stop.
   Pantheon supervisor will pick up the review loop automatically.

---

### Unchanged constraints (do not alter)

- All stream wiring stays through `SseClient`; no raw `EventSource` in components.
- Reconnect backoff and replay deduplication: do not change `sseReconnectManager.ts`
  or the `appliedIds` logic in `sseReconciler.ts`.
- Kill-switch CTA gating: do not change the existing `killSwitchActivatedViaSse`
  or `killSwitchActivated` logic.
- Degradation banner: still derived from BFF `meta` snapshots, not SSE payloads.
- No new BFF endpoints; no shadow state; no contract expansion.

---

### References

- `docs/screens/PKT-005-sse-substrate.md`
- `docs/bff/PKT-005-sse-substrate.md`
- `docs/pantheon-handoffs/PKT-005-sse-substrate/FRONTEND_CHANGE_SPEC.md`
- `docs/examples/PKT-005-sse-substrate.json`
- `.coordination/reviews/BP5-LUV-010-review.md` (full review findings)
- `docs/pantheon-delivery/PKT-005-sse-substrate/DELIVERY_NOTE.md`
