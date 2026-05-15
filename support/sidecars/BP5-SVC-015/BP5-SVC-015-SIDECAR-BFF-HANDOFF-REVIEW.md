# BP5-SVC-015-SIDECAR-BFF-HANDOFF Review

Reviewer: Codex
Date: 2026-04-15
Disposition: changes_requested

## Findings

1. `support/sidecars/BP5-SVC-015/BP5-SVC-015-SIDECAR-BFF-HANDOFF.md:108-115` overstates the command path as a fully implemented internal-API reference model. `services/control-plane/bff/command_executor.py:157-183` still handles `ApproveEvolutionDecision` and `ExecuteEvolutionAction` with local stub executors rather than dispatching to `PANTHEON_INTERNAL_API_URL`. Please narrow this section so it only claims the explicit-failure pattern for the commands that actually use the internal API, or explicitly call out the two remaining stubbed evolution commands as exceptions.

2. `support/sidecars/BP5-SVC-015/BP5-SVC-015-SIDECAR-BFF-HANDOFF.md:195-202` gives unsafe frontend guidance by saying `POST /api/v1/commands` is explicit and that any `4xx/5xx` means the backend is unavailable. `services/control-plane/bff/main.py:238-283` returns `403` and `422` for role/MFA/parameter validation failures before downstream availability is even involved. Please rewrite the guidance so only transport/downstream failures are treated as availability signals, while `4xx` remains request/auth/authorization dependent unless the response body specifically says otherwise.

## Non-blocking Notes

- The packet's seed-fallback analysis in `read_store.py` and the `snapshot` no-op callout in `main.py` both match the current code and should stay as the core of the handoff.
