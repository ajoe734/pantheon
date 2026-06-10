# Review: BFF-B6-001-SEC-FIX

Reviewer: Claude
Task: BFF-B6-001-SEC-FIX — Tenant scope on NL retrieval + evidence filter + classifier hardening + happy-path audit
Owner: Codex2
Date: 2026-05-25

## Verdict: APPROVED

All acceptance criteria are satisfied. Implementation PRs #587 and #598 are merged into dev. Independent reviewer verification passes.

## Verification Evidence

```
python3 -m py_compile services/control-plane/bff/main.py   → OK
python3 -m py_compile services/control-plane/bff/read_store.py → OK
pytest tests/test_bff_b6_management_nl_ask.py tests/test_bff_b6_003_nl_high_risk_refusal.py -v
→ 18 passed in 7.84s
```

## Security Properties Verified

### 1. Tenant Scope on NL Retrieval (BFF-B6-001-SEC-FIX)

- `_mgmt_nl_caller_tenant()` resolves effective tenant from identity JWT claims, validated against `allowed_tenants` via `_bff_me_tenant_payload()`. Unauthorized tenant access raises 403 before any retrieval.
- `_mgmt_nl_collect_context()` accepts `tenant_id` and routes it through `_mgmt_nl_filter_tenant_records()` for every management surface: alerts, inbox items, anomalies, runtime bindings, capital pools, personas, incidents, evolution decisions.
- Tenant-agnostic records (no tenant annotation) are included by design; records with explicit tenant annotations are strictly matched.
- The endpoint resolves `caller_tenant_id` at line 29075, before idempotency check and before any data retrieval.

### 2. Evidence Filter (BFF-B6-002)

- `list_evidence_refs()` in `read_store.py` filters by `tenant_id` via `_record_matches_tenant()` (with `include_tenant_agnostic=True`), and by `linked_entities` + `source_types` via `_evidence_matches_scope()`.
- Each evidence ref gets `href` set to `/api/v1/knowledge/evidence/{ref_id}` before capability-based redaction.
- `redact_evidence_refs()` strips unauthorized refs; `redactedEvidenceCount` is surfaced in `meta`.
- `list_evidence_refs()` called with `tenant_id=caller_tenant_id` at line 29113.

### 3. High-Risk Classifier Hardening (BFF-B6-003)

- `_mgmt_nl_high_risk_classify()` runs at line 29045, immediately after question validation and before idempotency check, surface collection, session creation, or SSE emission. Verified by test `test_refusal_does_not_create_session_idempotency_record_or_sse` which asserts `_mgmt_nl_collect_context` is never called on a high-risk question.
- Evasion stripping: `_mgmt_nl_evasion_stripped_variants()` strips 15 common prefix forms (including zh-TW variants 請/麻煩/幫我) and creates normalized variant list before matching.
- Word-boundary regex matching prevents partial-word false positives.
- Refusal records a narrow `management.nl.high_risk_refused` audit event with matched category, pattern, and actor ID. The audit ID is returned in the 403 response for traceability.
- Refusal audit write failure is caught and logged; the 403 response is still returned without the `audit_id`.

### 4. Happy-Path Audit

- `management.nl.ask.accepted` audit event includes `tenantId`, `focus`, `confidence`, and `sourceSurfaces`.
- Audit write failure raises 503 `DEPENDENCY_UNAVAILABLE` — fail-closed behavior is correct for a security-sensitive audit trail.

## Minor Observations (Non-blocking)

- `_mgmt_nl_record_matches_tenant()` returns `True` when `tenant_id` is empty, allowing all records. Upstream `_mgmt_nl_caller_tenant()` always produces a non-empty ID (fallback to `"pantheon-dev"`), so this edge is unreachable in the happy path.
- `include_tenant_agnostic=True` in `list_evidence_refs` means global evidence refs are visible to all tenants. This appears intentional for shared knowledge assets. No security regression, but worth monitoring if global refs gain sensitive content.

## Test Coverage Assessment

| Test | AC | Result |
|---|---|---|
| `test_nl_ask_authenticated_returns_202_with_data_fields` | AC#1 | PASS |
| `test_nl_ask_anonymous_returns_401` | AC#2 | PASS |
| `test_nl_ask_focus_trading_pulse_restricts_sources` | AC#3 | PASS |
| `test_nl_ask_idempotency_replay_returns_cached` | AC#4 | PASS |
| `test_nl_ask_missing_question_returns_422` | AC#5 | PASS |
| `test_nl_ask_session_id_echoed_when_supplied` | AC#6 | PASS |
| `test_nl_ask_session_id_generated_when_omitted` | AC#6 | PASS |
| `test_nl_ask_focus_persona_fleet_populates_context` | regression | PASS |
| `test_nl_ask_response_includes_audit_ref` | AC#7 | PASS |
| `test_nl_ask_response_includes_evidence_refs_with_api_href` | AC#8 | PASS |
| `test_nl_ask_response_includes_redacted_evidence_count` | AC#9 | PASS |
| `test_high_risk_questions_return_typed_403` (×5) | B6-003 AC#1-5 | PASS |
| `test_read_only_question_still_returns_202` | B6-003 AC#6 | PASS |
| `test_refusal_does_not_create_session_idempotency_record_or_sse` | B6-003 AC#7 | PASS |

All 18 tests passed. Implementation is ready for owner done finalization.
