# PKT-004 Persona Management Composed Screen

## Classification

- Workbench: Persona Workbench
- Screen ID: `screen-persona-management`
- Feature ID: `PKT-004-persona-management`
- Packet status: ready

## User Goal

Give an operator one composed page per persona showing lifecycle state, active bindings with capital pool metadata, current sessions, and teaching history — without requiring client-side joins across multiple endpoints.

## Page Sections

- **Persona summary**: `lifecycle_state`, `mandate`, `strategy_family`, `created_at`, `last_active_at`. Read-only identity block.
- **Bindings panel**: list of bindings with embedded `capital_pool` metadata (`status`, `id`). Displays `binding.validity`, `binding.status`, and `binding.allowed_deployment_scope`. Shows a degraded-panel placeholder when `meta.surfaces.capital_pool_bindings` is `degraded` or `unavailable`.
- **Active sessions panel**: list of current sessions with `status`, `started_at`, `last_heartbeat_at`, `tools_enabled`, `pool_scope`. Shows a degraded-panel placeholder when `meta.surfaces.persona_sessions` is not `ok`.
- **Teaching history panel**: list of teaching sessions with `status`, `started_at`, `completed_at`, `topic`, `operator_id`, `outcomes`. Shows a degraded-panel placeholder when `meta.surfaces.teaching_sessions` is not `ok`.
- **Action rail**: CTAs rendered only when the corresponding `allowedActions.*` field is `true`. Covered actions: `canEdit`, `canRetire`, `canPause`, `canActivate`, `canDelete`, `canTerminateSession`, `canPauseSession`, `canViewTeachingHistory`.
- **Degradation banner**: non-dismissable banner when any `meta.surfaces` entry is `degraded` or `unavailable`. Does not hide content — shows panels read-only with the banner visible.
- **Loading, empty, and error states**: explicit and visually distinct; no mock fallback.

## Interaction Rules

- All data comes from `GET /api/v1/operator/persona-management/{persona_id}`.
- CTA visibility comes exclusively from `allowedActions` in the BFF response. Do not derive persona actions locally.
- If any required `allowedActions` field is missing, emit a `bff-gap` handoff.
- `meta.surfaces.*` degradation must suppress CTA rail on affected panels; the degradation banner must appear.
- Mutation commands use `POST /api/v1/operator/commands`. No direct resource mutations.
- Do not supplement the BFF response with client-derived values.

## Non-Blocking BFF Caveats (carry forward into implementation)

- `snapshot=preferred` is accepted but not enforced: `meta.snapshot_at` is returned but surface timestamps are not aligned in v1.
- Read-surface staleness is not tied to `BFF_READ_SURFACE_STATE`: degradation flags only when a sub-surface returns `None` or empty results.
- `viewer` role tokens are rejected: requires `operator`, `approver`, `admin`, or `reviewer` token.

## Wave 2 Deferred Items (must remain visible — not implemented in this packet)

- Standalone persona list shell (`PS-01` Persona Catalog)
- Standalone persona detail shell (`PS-02` Persona Detail)
- Tool profile panel (no BFF route yet)
- Consult policy panel (no BFF route yet)

## Acceptance

- Persona summary, bindings, sessions, and teaching history render with real BFF data and no mock rows.
- CTA rail is backed by `allowedActions` only — no local eligibility derivation.
- Degraded or unavailable surfaces show the degradation banner and disable CTAs on affected panels.
- Missing `allowedActions` triggers a `bff-gap` handoff file instead of silent omission.
- Loading, empty, degraded, and error states are explicit and visually distinct.
