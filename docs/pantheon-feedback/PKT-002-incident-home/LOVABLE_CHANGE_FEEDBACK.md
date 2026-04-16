# PKT-002 Incident Home — Lovable Change Feedback

Feature ID: `PKT-002-incident-home`
Screen: `incident-home`
Workbench: `operator-console`
Loop status: **dispatch-complete / awaiting-lovable-pickup**

## Dispatch Summary

Pantheon completed the full pre-implementation loop:

1. **BFF gap filed** — All blocking envelope divergences documented in
   `.coordination/requests/PKT-002-incident-home-bff-gap.yaml`.
2. **Backend delivery confirmed** — Both `GET /api/v1/incidents` and
   `GET /api/v1/kill-switch/status` are aligned to the published contract
   (commit `2782e5021243cca958974059dbf2ceeaac16fdfb`).
3. **Contract-ready published** — `.coordination/responses/PKT-002-incident-home-contract-ready.yaml`
4. **Lovable UI task dispatched** — `.coordination/responses/PKT-002-incident-home-lovable-ui-task.yaml`
   and `.coordination/responses/PKT-002-incident-home-lovable-prompt.md` are ready for pickup.

## Required Changes for Lovable

Per `.coordination/responses/PKT-002-incident-home-lovable-ui-task.yaml`:

- Build the **Incident Home list panel** (`src/pages/operator/IncidentHome.tsx`)
- Build the **kill switch control rail badge** (`src/components/operator/KillSwitchBadge.tsx`)
- Add incident-home types to `src/pages/operator/types.ts`
- Add BFF fetch calls to `src/lib/bffClient.ts` (no raw fetch in components)

## Constraints Confirmed

- Use existing BFF client only
- Do not add raw `fetch` or `axios` calls in component files
- Do not import demo providers
- Do not invent fields beyond the handoff packet
- Render kill switch badge from `GET /api/v1/kill-switch/status` only
- Render non-dismissable warning banner when `meta.surfaces.kill_switch` is degraded or unavailable
- Display degradation banner when any `meta.surfaces` entry is degraded or unavailable

## Follow-up

- BP5-SVC-015 (`Remove BFF snapshot and default fallback from normal integration path`) is still
  `todo` — this is a cleanup task and does not block the UI implementation loop.
- Once Lovable implements the screen, it should write
  `.coordination/requests/PKT-002-incident-home-ui-done.yaml` to signal completion.
