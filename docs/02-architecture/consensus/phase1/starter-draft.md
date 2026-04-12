# Starter Draft

Current rule: only the baton owner edits this file directly.

## Shared Draft

- Objective: turn the backend completion checklist into an execution-ready Pantheon plan that can drive BFF delivery, front-end integration, and the Lovable handoff lane without reopening settled ownership boundaries.
- Scope boundary: Pantheon backend completion, BFF/API readiness, authoritative command/control path, SSE timing, CLI fallback, and cross-repo front-end handoff. Do not reopen unrelated runtime or product expansion work unless it directly blocks these surfaces.
- Current truth: Pantheon has real governance domain depth and real coordination infrastructure, but its front-end-facing BFF remains thin. Only `GET /health`, `POST /api/v1/operator/commands`, and `GET /api/v1/operator/commands/{command_id}` are executable today; the 33 read surfaces, 4 composed views, and 3 SSE feeds remain mostly contractual.
- Rebaseline correction: `APP-002-IMPL-CLI` and `APP-002-IMPL-FE` should be interpreted as scaffold-complete, not production-complete. Planning and task slicing must reflect the remaining hardening work rather than the task-board label alone.
- Accepted architecture: Pantheon owns all `/api/*` read contracts, allowed actions, command authority, and page-shaped responses; `front-ai-trading-system` owns pages/components/UX; Lovable is a human-triggered UI accelerator; GitHub remains the delivery/handoff bus rather than the runtime control plane.
- Accepted proving target: the first end-to-end proving page is `F-042 Promotion Review`, because it exercises page-shaped read composition, backend-driven CTAs, command submission, and front-end/Lovable handoff with the smallest credible slice.
- Accepted write-path decision: Wave 1 keeps generic `POST /api/v1/operator/commands`; resource-shaped write routes are explicitly deferred until after the first credible UI integration lands.
- Accepted wave order:
  - Wave 0: rebaseline task-board truth and execution guardrails
  - Wave 1: Promotion Review vertical slice
  - Wave 2: Incident Response vertical slice
  - Wave 3: Post-Incident Review + Evolution
  - Wave 4: Persona Management + remaining list/detail surfaces
  - Wave 5: SSE + production front-end/Lovable rollout
- Accepted slicing principle: implement Pantheon by operator workflow vertical slice, not by attempting all 33 read surfaces in a single flat batch.
- Must-have before Pantheon is genuinely front-end ready: real Wave 1 read surfaces and composed view, real command execution and status, hardened internal API, working `pantheon-admin` fallback, and Lovable/front-end packet generation tied to executable contracts.
- Tracking risks: Gemini planning worker failures and the stale Claude approval-suspended lane should be recorded as orchestration issues, but they should not block drafting the execution packet because current code evidence plus submitted readouts already converge on the same plan.
