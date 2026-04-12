# APP-002-W2-CLI-FALLBACK — Acceptance Packet & Dependency Map

**Sidecar Task ID**: `APP-002-W2-CLI-FALLBACK-SIDECAR-ACCEPTANCE`
**Parent Task**: `APP-002-W2-CLI-FALLBACK`
**Parent Owner**: Codex
**Parent Reviewer**: Qwen
**Sidecar Owner**: Qwen
**Sidecar Reviewer**: Codex
**Helper Kind**: `acceptance_packet`
**Date**: 2026-04-11

> This is a **support artifact only**. It does not modify canonical truth, core contracts, or runtime/registry/governance implementations. It provides an acceptance checklist and dependency map so the parent owner (Codex) and reviewer (Qwen) can efficiently verify and absorb the work.

---

## 1. Parent Task Summary

| Field | Value |
|-------|-------|
| **ID** | `APP-002-W2-CLI-FALLBACK` |
| **Title** | Turn pantheon-admin and internal API into usable fallback path |
| **Phase** | Phase 5: APP-002 Execution Wave 2 |
| **Owner** | Codex |
| **Reviewer** | Qwen |
| **Status** | `done` |
| **Summary (zh)** | 把 pantheon-admin 與 internal API 從 scaffold 提升為可用 secondary control path，確保 UI 掛掉時仍可安全操作。 |

### 1.1 Parent Acceptance Criteria

| # | Criterion | Key Question |
|---|-----------|-------------|
| AC-1 | `cli_executes_real_actions` | Does `pantheon-admin` execute real actions on control-plane components, not just print stubs? |
| AC-2 | `internal_api_not_placeholder` | Is the internal API wired to real backends (runtime-manager, kill-switch controller, command store), not placeholder endpoints? |
| AC-3 | `operator_fallback_documented` | Is there clear guidance for operators on when and how to use the fallback path? |

---

## 2. Dependency Map

### 2.1 Direct Dependencies (both `done`)

| Dependency | Status | Summary | Artifacts Contributed |
|------------|--------|---------|----------------------|
| `APP-002-W1-COMMAND-DEPLOYMENT` | **done** (Qwen → Codex review approved) | Hardened deployment command execution; Promotion Review operations are authoritative with real command status and audit trails | `services/control-plane/bff/main.py`, `services/control_plane/internal_api.py`, `tools/pantheon_admin/cli.py` |
| `APP-002-W2-CONTROL-INCIDENT` | **done** (Qwen → Codex review approved) | Hardened incident control-path; pause/rollback/kill-switch execute through authoritative paths with full audit trails and degraded-mode guidance | `services/control-plane/bff/main.py`, `services/control_plane/internal_api.py`, `services/execution/runtime-manager/` |

### 2.2 Sibling Support Artifacts (already exist)

| Artifact | Kind | Relevance |
|----------|------|-----------|
| `support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md` | Design spec | Defines CLI command surface, internal API endpoints, MFA rules, fallback UX guidance, and reconciliation semantics |
| `support/sidecars/APP-002/APP-002-OPERATOR-ACTION-CONTRACT.md` | Contract | Operator action validation and idempotency rules |
| `support/sidecars/APP-002/APP-002-SIDECAR-BFF-HANDOFF.md` | BFF handoff | Gap analysis for BFF-to-secondary-path coordination |
| `support/sidecars/APP-002/APP-002-FRONTEND-STATE-MATRIX.md` | Frontend matrix | State mapping for UI fallback rendering |

### 2.3 Wave 2 Downstream Tasks (blocked on this parent)

| Task | Owner | Depends On |
|------|-------|------------|
| `APP-002-W3-POSTINCIDENT-EVOLUTION` | Qwen | `APP-002-W2-CONTROL-INCIDENT` (already done; Qwen completed) |
| `APP-002-W2-CLI-FALLBACK-SIDECAR-ACCEPTANCE` (this task) | Qwen | `APP-002-W1-COMMAND-DEPLOYMENT`, `APP-002-W2-CONTROL-INCIDENT` |

---

## 3. Current Artifact Inventory

### 3.1 `tools/pantheon_admin/cli.py` — Current State

| Aspect | Status | Details |
|--------|--------|---------|
| **Command surface** | ✅ Implemented | `deployment`, `runtime`, `rollback`, `kill-switch`, `evolution` subcommands all have argparse definitions |
| **Exit codes** | ✅ Defined | 0=success, 1=failure, 2=auth, 3=usage, 4=unavailable, 5=partial — matches secondary control path spec |
| **Execution path** | ✅ Real | Each handler issues HTTP calls to the internal API (`/api/internal/v1/...`) via `urllib`. Non-2xx responses map to exit codes and error output. |
| **MFA support** | ✅ Wired | `--mfa-token` forwarded as `X-MFA-Token`. High-risk actions enforce MFA locally (rollback execute/abort, kill-switch activate/deactivate, runtime force-halt). |
| **Config / logging** | ✅ Present | `--config`, `--base-url`, `--output`, `--timeout`, `--dry-run`, `--verbose`, `--log-level` supported; config defaults read from `~/.pantheon/cli.conf`. |
| **Alignment with spec** | ✅ Structure matches | Command hierarchy in `APP-002-SECONDARY-CONTROL-PATH.md §3.3` mirrored in argparse structure. Evolution commands intentionally return `EXIT_UNAVAILABLE` until controller API is exposed. |

**AC-1 status**: ✅ **Satisfied** — CLI handlers now execute real HTTP calls to the internal API (default base URL `http://localhost:5001` or `PANTHEON_INTERNAL_API_URL`). Evolution control remains a guarded `EXIT_UNAVAILABLE` path and is out-of-scope for this acceptance criterion.

### 3.2 `services/control_plane/internal_api.py` — Current State

| Aspect | Status | Details |
|--------|--------|---------|
| **Framework** | ✅ Flask app | Routes defined for all operator actions |
| **Deployment approve** | ✅ Real | Creates approval decision records, persists to command state store, returns 202 |
| **Runtime pause/resume** | ✅ Real | Integrates with `RuntimeBindingStore` state machine; transitions active→pending_pause→paused and paused→active |
| **Rollback execute** | ✅ Real | Integrates with `RuntimeBindingStore`; supports replace/pause_then_replace/liquidate_then_replace action matrix |
| **Kill-switch** | ✅ Real | Integrates with `KillSwitchController` fast path; returns safe-mode state and audit entry |
| **Command status polling** | ✅ Real | `GET /api/internal/v1/commands/{command_id}` returns persisted command records |
| **Auth** | ✅ Stub + structure | Bearer token validation (structure check, not JWT signature); MFA validation (6-digit regex) |
| **Degraded-mode fallback** | ✅ Implemented | When `RuntimeBindingStore` is unreachable, commands still execute with full audit trail and `degraded_mode: true` flags |
| **Audit persistence** | ✅ Real | JSON file-based command store at `/tmp/pantheon/internal_api/commands.json` (configurable via env) |
| **Alignment with spec** | ✅ Endpoints match | Endpoints from `APP-002-SECONDARY-CONTROL-PATH.md §4.3` implemented, including rollback list/abort and kill-switch status/deactivate |

**Verdict for AC-2**: ✅ **Satisfied**. The internal API is NOT a placeholder — it executes real actions through runtime-manager components with audit trails.

### 3.3 `support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md` — Current State

| Aspect | Status |
|--------|--------|
| **CLI command spec** | ✅ Complete — all 5 command families documented with examples, output, error cases, MFA rules |
| **Internal API spec** | ✅ Complete — all 5 endpoint families documented with request/response schemas |
| **Fallback UX guidance** | ✅ Complete — 2 scenarios with operator-facing copy and escalation text |
| **Reconciliation** | ✅ Complete — idempotency, audit consistency, SSE-as-source-of-truth |
| **Security** | ✅ Complete — MFA enforcement matrix, IP whitelisting, audit logging requirements |

**Verdict for AC-3**: ✅ **Satisfied** at the spec level. Implementation needs to ensure CLI output matches the documented UX patterns once AC-1 is closed.

---

## 4. Acceptance Checklist for Parent Owner (Copilot)

### AC-1: `cli_executes_real_actions`

- [x] **HTTP wiring**: CLI command handlers POST/GET to the internal API (`services/control_plane/internal_api.py`) using stdlib `urllib`
- [x] **Base URL**: `--base-url` + `PANTHEON_INTERNAL_API_URL` supported (default: `http://localhost:5001`)
- [x] **Auth/MFA**: Bearer token + `X-MFA-Token` headers forwarded; high-risk actions enforce MFA locally
- [x] **Response handling**: JSON parsed and emitted in text or `--output json` format
- [x] **Error mapping**: HTTP status → CLI exit codes (401/403 auth, 5xx unavailable, etc.)
- [ ] **Test (recommended)**: Add integration test that starts internal API + sends CLI command → verifies command recorded in store

### AC-2: `internal_api_not_placeholder`

- [x] **Verified**: Internal API routes execute real actions through runtime-manager components
- [x] **Verified**: Command state persisted to JSON file store with audit trails
- [x] **Verified**: Degraded-mode fallback preserves audit trail with explicit flags
- [ ] **Recommendation**: Parent owner should verify Flask app can be deployed as standalone (not just imported); add `gunicorn` or production WSGI server config if not already present

### AC-3: `operator_fallback_documented`

- [x] **Verified**: Secondary control path spec (§5) provides actionable fallback UX copy
- [x] **Verified**: Escalation paths documented with concrete CLI and curl examples
- [ ] **Recommendation**: Once CLI executes real actions, add a "quick reference card" section to the spec showing the minimal operator journey for each critical action

---

## 5. Risk & Blocker Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| CLI evolution subcommands still return `EXIT_UNAVAILABLE` | **Low** | Out of scope for APP-002-W2-CLI-FALLBACK acceptance; keep explicitly documented |
| Internal API uses Flask dev server | **Medium** | Add production WSGI config; not a blocker for v1 contract |
| Command store is file-based JSON | **Low** | Acceptable for v1; production would migrate to Redis/DB later |
| Limited integration coverage for CLI→API path | **Medium** | Recommend adding pytest integration test to guard CLI wiring |
| MFA validation is regex-only (no TOTP verification) | **Low** | Documented as stub; real TOTP verification is infra-level work |

---

## 6. Recommended Execution Plan for Parent Owner

```
Phase 1: Wire CLI → Internal API
  1a. Add HTTP client to cli.py (requests or urllib)
  1b. Wire each command handler to its internal API endpoint
  1c. Forward bearer token + MFA token from CLI args to HTTP headers
  1d. Parse responses and format output (text + json modes)
  1e. Map HTTP errors to CLI exit codes

Phase 2: Harden & Test
  2a. Add unit tests for CLI command parsing
  2b. Add integration test: CLI → internal API → command store
  2c. Verify degraded-mode path produces correct audit flags
  2d. Verify idempotency (same command twice = same result)

Phase 3: Document & Hand Off
  3a. Update SECONDARY-CONTROL-PATH.md with real CLI output examples
  3b. Add operator quick-reference card
  3c. Hand off to Claude for review
```

---

## 7. Final Verification & Handoff

### 7.1 Verification Timestamp

| Field | Value |
|-------|-------|
| **Verified by** | Codex (sidecar reviewer) |
| **Verification date** | 2026-04-12 |
| **Parent task status** | `done` |
| **Dependencies** | Both `APP-002-W1-COMMAND-DEPLOYMENT` and `APP-002-W2-CONTROL-INCIDENT` are `done` |

### 7.2 AC Status at Verification Time

| AC | Status | Evidence |
|----|--------|----------|
| AC-1 `cli_executes_real_actions` | ✅ **Satisfied** | `tools/pantheon_admin/cli.py` issues real HTTP calls via `urllib`, supports base URL/auth/MFA/output, and maps HTTP errors to CLI exit codes. |
| AC-2 `internal_api_not_placeholder` | ✅ **Satisfied** | `services/control_plane/internal_api.py` executes real actions through `KillSwitchController`, `RuntimeBindingStore`, and JSON command store. All 5 endpoint families implemented with audit trails and degraded-mode fallback. |
| AC-3 `operator_fallback_documented` | ✅ **Satisfied** | `APP-002-SECONDARY-CONTROL-PATH.md` provides complete CLI command spec, internal API spec, fallback UX guidance, reconciliation semantics, and security rules. |

### 7.3 Handoff

- **To**: Codex (parent owner)
- **From**: Qwen (sidecar owner)
- **Reviewer**: Codex (sidecar reviewer)
- **Message**: Acceptance packet verified and complete. AC-1/AC-2/AC-3 are satisfied. CLI now calls the internal API via HTTP, forwards auth/MFA, and maps errors to exit codes; internal API executes real actions with audit trails; fallback guidance is documented. Dependencies are `done`; parent task already finalized.
- **Reviewer note for Qwen** (when reviewing the parent task): Spot-check CLI→API wiring (headers, status mapping) and confirm degraded-mode audit flags are surfaced in operator-facing output.
- **Review update (Codex, 2026-04-12)**: Verified CLI is fully wired (`tools/pantheon_admin/cli.py` uses `urllib`), internal API endpoints cover rollback list/abort + kill-switch status/deactivate, and packet now matches parent `done` status.

### 7.4 Finalization

| Field | Value |
|-------|-------|
| **Handoff completed** | 2026-04-11T18:08:16Z |
| **Handoff via** | `ai_status.py handoff` |
| **Review approved** | 2026-04-11T18:16:09Z via `ai_status.py approve` (Codex) |
| **Task status** | `done` — finalized 2026-04-11T18:16:09Z via `ai_status.py done` (Qwen) |
| **All acceptance criteria met** | ✅ Yes (support artifact complete, no canonical truth modified, reviewer approval recorded) |

---

*Generated by Qwen as sidecar acceptance packet for APP-002-W2-CLI-FALLBACK. This is a support artifact — it does not modify canonical truth.*
