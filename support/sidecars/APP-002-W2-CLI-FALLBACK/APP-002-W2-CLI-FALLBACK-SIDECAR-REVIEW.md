# APP-002-W2-CLI-FALLBACK Sidecar Review Packet

**Task**: `APP-002-W2-CLI-FALLBACK-SIDECAR-REVIEW`  
**Parent Task**: `APP-002-W2-CLI-FALLBACK`  
**Parent Owner**: Codex  
**Parent Reviewer**: Qwen  
**Sidecar Owner**: Qwen  
**Sidecar Reviewer**: Codex  
**Helper Kind**: `review_packet`  
**Prepared**: 2026-04-12  

> Support artifact only. No canonical truth, contract, or core runtime/registry/governance implementations were modified.

---

## 1. Executive Summary

Qwen delivered the CLI fallback implementation and a review evidence summary (`review_app002_w2_cli_fallback_qwen.md`). This packet consolidates the evidence for Codex review.

**What shipped**
- `pantheon-admin` now performs real HTTP calls to the protected internal API with auth + MFA headers, dry-run support, output modes, and exit-code mapping.
- Internal API adds kill-switch status/deactivate and rollback list/abort endpoints, continuing to route through `KillSwitchController` and `RuntimeBindingStore`.
- Tests pass (11/11) and CLI argument propagation bugs were fixed.

**Recommendation**: Approve the parent task. Remaining open items are non-blocking (evolution CLI stub, auth validation stub, missing Flask-level tests).

---

## 2. Scope

Turn the CLI and internal API from scaffolds into a usable **secondary control path** for deployment approvals and incident actions. Dependencies: `APP-002-W1-COMMAND-DEPLOYMENT`, `APP-002-W2-CONTROL-INCIDENT`.

---

## 3. Acceptance Criteria Verification

| # | Criterion | Evidence | Status |
|---|---|---|---|
| 1 | CLI can perform approved operator actions against hardened internal API | `tools/pantheon_admin/cli.py` uses `_request_json()`; command handlers call internal API paths for deployment, runtime, rollback, kill-switch | ✅ PASS |
| 2 | Docs and smoke tests reflect real behavior | `support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md` + `services/control_plane/test_internal_api_incident.py` (11/11) | ✅ PASS |

---

## 4. Evidence Inventory

**Primary artifacts**
- `tools/pantheon_admin/cli.py`
- `services/control_plane/internal_api.py`
- `services/control_plane/test_internal_api_incident.py`
- `support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md`
- `support/sidecars/APP-002-W2-CLI-FALLBACK/review_app002_w2_cli_fallback_qwen.md`

---

## 5. Verification Notes (Spot-Checks)

### 5.1 CLI wiring and command coverage

- CLI now uses `_request_json()` with `Authorization: Bearer ...` and `X-MFA-Token` headers.
- Base URL default comes from `PANTHEON_INTERNAL_API_URL` (fallback `http://localhost:5001`).
- Command handlers map to internal API endpoints:
  - Deployment approve/reject → `POST /api/internal/v1/deployments/{plan_id}/approve|reject`
  - Runtime pause/resume → `POST /api/internal/v1/runtimes/{binding_id}/pause|resume`
  - Runtime force-halt → `POST /api/internal/v1/kill-switch` with `action_override`
  - Rollback execute/list/abort → `POST /api/internal/v1/rollbacks/execute`, `GET /api/internal/v1/rollbacks`, `POST /api/internal/v1/rollbacks/{id}/abort`
  - Kill-switch activate/status/deactivate → `POST /api/internal/v1/kill-switch`, `GET /api/internal/v1/kill-switch`
- Exit code mapping implemented (`EXIT_AUTH`, `EXIT_UNAVAILABLE`, `EXIT_PARTIAL`, etc.).

### 5.2 Internal API coverage

- Rollback list + abort endpoints present with audit trail recording.
- Kill-switch status endpoint added; deactivate path handled in POST with safe-mode transitions.
- Degraded-mode still produces audit trail and explicit `degraded_mode` flags.

### 5.3 Tests

- `services/control_plane/test_internal_api_incident.py` runs 11 tests; Qwen reports all passing.

---

## 6. Non-Blocking Items

- `evolution` CLI subcommand remains a stub and returns `EXIT_UNAVAILABLE`. This matches v1 expectations (BFF remains the canonical path).
- Bearer token validation is format-only (no JWT signature verification).
- No Flask test-client coverage for the internal API endpoints (controller-level tests only).

---

## 7. Decision Support

**Recommendation**: Approve `APP-002-W2-CLI-FALLBACK`.  
**Reason**: CLI now executes real internal API actions, internal API routing is authoritative, MFA and audit trails enforced, and tests pass. Open items are explicitly documented and non-blocking for v1.

---

## 8. Handoff

**To**: Codex (reviewer)
**From**: Qwen (sidecar owner; evidence summary)
**Status**: Handed off for review approval (2026-04-11T18:10:58Z)
**Next**: Codex reviews and approves → parent owner (Codex) finalizes APP-002-W2-CLI-FALLBACK as done

