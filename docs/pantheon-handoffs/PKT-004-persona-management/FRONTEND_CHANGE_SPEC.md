# PKT-004 Persona Management — Frontend Change Spec

## Feature

- Feature ID: `PKT-004-persona-management`
- Screen ID: `screen-persona-management`
- Workbench: Persona Workbench
- Packet status: ready

## Summary

Build the **Persona Management** composed screen inside `front-ai-trading-system`. This screen gives operators a single surface to inspect a persona's lifecycle state, active bindings with capital pool metadata, current sessions, and teaching history. All data and CTA authority must come from the Pantheon BFF — no local derivation.

## Files to Create or Modify

```
src/pages/persona/PersonaManagement.tsx        — new composed page
src/pages/persona/types.ts                     — add persona-management types
src/lib/bffClient.ts                           — add persona-management fetch call
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch composed persona management view

```
GET /api/v1/operator/persona-management/{persona_id}
Query params: snapshot (optional: preferred)
```

Expected response shape (see `docs/examples/PKT-004-persona-management.json` for a full example):

```typescript
interface PersonaManagementResponse {
  data: {
    persona: {
      id: string;
      name: string;
      lifecycle_state: string;
      mandate: string;
      strategy_family: string;
      created_at: string;
      last_active_at: string;
    };
    bindings: PersonaBinding[];
    sessions: PersonaSession[];
    teaching_sessions: TeachingSession[];
    allowedActions: {
      canActivate: boolean;
      canEdit: boolean;
      canDelete: boolean;
      canRetire: boolean;
      canPause: boolean;
      canTerminateSession: boolean;
      canPauseSession: boolean;
      canViewTeachingHistory: boolean;
    };
  };
  meta: {
    snapshot_at: string;
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable" }>;
  };
}
```

### Submit action

```
POST /api/v1/operator/commands
```

Edit payload:
```json
{
  "command": "EditPersona",
  "target": { "type": "Persona", "id": "{persona_id}" },
  "action": "edit",
  "params": { "persona_id": "{persona_id}", "updates": {} },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

Retire payload: same structure with `"command": "RetirePersona"` and `"action": "retire"`.

## Component Structure

### `PersonaManagement.tsx`

- Receives `persona_id` as a route param.
- Fetches from `GET /api/v1/operator/persona-management/{persona_id}` on mount.
- Renders `persona` summary block, `bindings` panel, `sessions` panel, and `teaching_sessions` panel.
- Renders CTAs only when the corresponding `allowedActions` field is `true`.
- When any `meta.surfaces` entry is `degraded` or `unavailable`: shows the non-dismissable degradation banner, shows the affected panel read-only with a degraded-panel placeholder, and disables CTAs on that panel.
- On CTA click, calls `POST /api/v1/operator/commands` with the appropriate payload.
- Renders loading, empty, degraded, and error states as distinct visual states.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- CTA visibility must come from `allowedActions` in the BFF response. Do not derive eligibility locally.
- If a required `allowedActions` field is absent from the BFF response, write `.coordination/requests/PKT-004-persona-management-bff-gap.yaml` using `.coordination/requests/PKT-004-persona-management-bff-gap.example.yaml` as the template and stop implementation.
- Do not invent fields or supplement the BFF response with client-derived values.

## Degradation Handling

When `meta.surfaces` contains any entry with status `"degraded"` or `"unavailable"`:

- Show a non-dismissable degradation banner at the top of the screen.
- Show the affected panel with a degraded-panel placeholder (not hidden).
- Disable CTAs on affected panels.

## Non-Blocking BFF Caveats

- `snapshot=preferred` is accepted but does not enforce surface-timestamp alignment in v1.
- Degradation flags only when a sub-surface returns `None` or empty — not tied to `BFF_READ_SURFACE_STATE`.
- `viewer` role tokens are rejected; an `operator`, `approver`, `admin`, or `reviewer` token is required.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-004-persona-management-ui-done.yaml` using `.coordination/requests/PKT-004-persona-management-ui-done.example.yaml` as the template. Sync the file back to GitHub so the Pantheon supervisor can pick up the next integration step automatically.

## References

- BFF contract: `docs/bff/PKT-004-persona-management.md`
- Screen spec: `docs/screens/PKT-004-persona-management.md`
- Example payload: `docs/examples/PKT-004-persona-management.json`
- Contract-ready: `.coordination/responses/PKT-004-persona-management-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-004-persona-management-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-004-persona-management-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-004-persona-management-ui-done.example.yaml`
