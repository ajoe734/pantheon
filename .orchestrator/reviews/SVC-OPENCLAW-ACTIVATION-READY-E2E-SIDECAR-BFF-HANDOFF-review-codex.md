# Review: SVC-OPENCLAW-ACTIVATION-READY-E2E-SIDECAR-BFF-HANDOFF

Reviewer: Codex
Owner: Claude
Date: 2026-04-30
Decision: approve

## Findings

No blocking findings remain.

## Resolved Reopen Findings

1. `viewer` access is now documented as denied for all OpenClaw BFF routes.
   The read routes list the implemented roles only:
   `operator`, `approver`, `admin`, and `reviewer`. This matches
   `_require_read_role` in `services/control-plane/bff/main.py`.

2. Section 4.1 now uses the implemented `gate_state` projection:
   `state`, `enabled`, `activation_gate`, `allowed_scope`, and
   `bff_activation_command`. This matches `_project_openclaw_gate_state` in
   `services/control-plane/bff/read_store.py`.

3. Section 4.1 now uses the BFF-projected session row fields
   `context_keys`, `audit_count`, and `latest_audit_event` instead of raw
   `context_bundle` / `audit_log`. This matches `_project_openclaw_session`.

4. Section 2.1 now explicitly dispositions both the legacy adapter session
   facade (`GET /api/openclaw-adapter/sessions*`) and the conditional
   effective-tools read (`GET /api/openclaw-adapter/tools`) as intentionally
   not exposed by the BFF.

The reviewed commit is `1447adabddaaaee00e8a491014843c9abcad5b40`, which
touches only:

- `support/sidecars/SVC-OPENCLAW-ACTIVATION-READY-E2E/SVC-OPENCLAW-ACTIVATION-READY-E2E-SIDECAR-BFF-HANDOFF.md`

## Verification

- `git show --stat --oneline --decorate --name-only 1447ada` -> only the
  sidecar handoff packet was changed.
- `pytest services/control-plane/bff/test_openclaw_ops_surface.py` -> 4 passed
- `pytest services/openclaw-gateway-adapter/test_compose_activation.py services/openclaw-gateway-adapter/test_main.py services/openclaw-gateway-adapter/test_session_lifecycle.py services/openclaw-gateway-adapter/test_live_gate_adapter.py` -> 103 passed

Note: running the BFF and adapter pytest files in one Python process caused
`services/openclaw-gateway-adapter/test_main.py` to import the already-loaded
BFF `main` module. The separately scoped commands above avoid that test-module
name collision and match the relevant service boundaries.

No canonical truth files were reviewed as mutation targets, and no runtime or
contract implementation changes are requested in this review.
