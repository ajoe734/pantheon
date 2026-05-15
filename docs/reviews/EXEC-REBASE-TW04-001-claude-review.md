# Review: EXEC-REBASE-TW04-001

**Reviewer:** Claude
**Date:** 2026-04-21
**Outcome:** Approved

## Acceptance Criteria

1. **TW-04 frontend handoff bundle 補齊** ✅
   Complete bundle present:
   - `docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md` ✓
   - `.coordination/responses/TW-04-teaching-replay-contract-ready.yaml` (`status: live`, all 4 routes listed) ✓
   - `.coordination/responses/TW-04-teaching-replay-lovable-ui-task.yaml` (`status: ready`) ✓
   - `.coordination/responses/TW-04-teaching-replay-frontend-feedback.yaml` (Codex review artifact) ✓
   - `.coordination/requests/TW-04-teaching-replay-bff-gap.example.yaml` (gap template) ✓
   - `.coordination/requests/TW-04-teaching-replay-ui-done.example.yaml` (completion template) ✓
   - `.coordination/requests/TW-04-teaching-replay-bff-gap.yaml` (gap tracking, resolved) ✓
   - `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml` (runtime tracking) ✓

2. **authority / evidence-link semantics 在 handoff 中正確表達** ✅
   `FRONTEND_CHANGE_SPEC.md` correctly expresses:
   - Commit/discard CTA authority from `allowedActions.canCommit` / `allowedActions.canDiscard` (detail response) only — not from `status`, event count, or `replay_resolution.state` ✓
   - Evidence links strictly from BFF-provided `event.evidence_ref` objects; no client-side route construction permitted ✓
   - Degradation surface rules (`ok` / `stale` / `degraded` / `unavailable`) mapped per contract, including stale banner semantics and CTA suppression ✓
   - Commit/discard submissions routed exclusively through the existing BFF client action layer; no raw component-level fetch ✓
   - Non-goals section explicitly prohibits Persona teaching-history substitution, client-side evidence construction, and status-only CTA inference ✓
   The evidence route topology gap (`/telemetry/drawdown/:id` → `/operator/paper-live-drift/runtime-042`) was identified in `bff-gap.yaml` and resolved in the Pantheon workspace (`read_store.py` + `ReadSurfaceStore` startup backfill) at `2026-04-21T18:20:00Z`.

3. **coordination / backlog truth 同步完成** ✅
   - `WORKBENCH_DELIVERY_BACKLOG.md` TW-04 row: `route-live — frontend handoff ready` (correct; handoff bundle published, Lovable UI task ready) ✓
   - `contract-ready.yaml`: `status: live`, all four routes, `bff_route_live_at: 2026-04-20T12:45:00Z`, `front_repo_receives_handoff_bundle: true` ✓
   - `lovable-ui-task.yaml`: `status: ready`, delivery dependencies wired to contract-ready ✓
   - Runtime confirmed at `2026-04-21T17:33:00Z`: all 4 routes passing (`live_verification_result` in needs-runtime.yaml) ✓
   - Bff-gap resolved in workspace (`resolved: true`, `resolved_at: 2026-04-21T18:20:00Z`) ✓

## Pending Operational Item (Non-Blocking)

**OPS-TW04-001** — `needs-runtime.yaml` still carries `status: pending`: the corrected evidence target (`/operator/paper-live-drift/runtime-042`) was fixed in the Pantheon workspace but still requires one final runtime refresh cycle to be observable at `http://127.0.0.1:18001`. This is a runtime freshness concern, not a handoff bundle or coordination truth concern. The bff-gap resolution is committed in code; the pending runtime refresh does not block review approval or frontend lane activation.

## Summary

All three acceptance criteria are met. The TW-04 handoff bundle is complete and ready, authority/evidence-link semantics are correctly and fully expressed in the frontend change spec, and coordination/backlog truth is synchronized. The evidence route topology gap was correctly surfaced as a bff-gap handoff and resolved in the Pantheon workspace. Returning to Codex for finalization.
