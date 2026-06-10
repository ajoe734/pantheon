# BFF-B6-002 NL Audit and Evidence Grounding

Task: BFF-B6-002
Owner: Claude
Reviewer: Codex
Depends-on: BFF-B6-001 (done)
Date: 2026-05-23 (updated after reviewer reopen)

## Scope

Implement and verify the NL audit and evidence grounding fields required by
the reviewer on POST /bff/management/nl/ask. The reviewer (Codex) reopened
the task requiring:

- `data.auditRef` on every NL call
- `data.evidenceRefs` with hrefs pointing to `/api/v1/knowledge/evidence/{ref_id}`
- `meta.redactedEvidenceCount` as a non-negative integer

## Implementation Under Audit

- Route: `POST /bff/management/nl/ask`
- Implementation: `services/control-plane/bff/main.py`
- Tests: `services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py`
- Spec section: `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md#b6--p2-management-natural-language-api`

## Changes from BFF-B6-001 Baseline

### New fields in `bff_management_nl_ask` response

**`data.auditRef` / `data.audit_ref`**

A synthetic audit reference for this NL exchange:
```json
{
  "targetType": "ManagementNLExchange",
  "target_type": "ManagementNLExchange",
  "targetId": "<message_id>",
  "target_id": "<message_id>",
  "href": "/bff/audit/entities/ManagementNLExchange/<message_id>"
}
```

**`data.evidenceRefs` / `data.evidence_refs`**

Evidence refs fetched from `read_store.list_evidence_refs()`, with each ref
having an `href` field set to `/api/v1/knowledge/evidence/{ref_id}` (via
`setdefault`). The list is passed through `redact_evidence_refs` with
capabilities derived from `_capabilities_for_identity(identity)`.

**`meta.redactedEvidenceCount` / `meta.redacted_evidence_count`**

The count of evidence refs redacted by `redact_evidence_refs` due to
insufficient capabilities. Zero when the operator has access to all evidence
or when no evidence refs are present.

### Implementation location

The new fields are computed between `source_keys = list(snippets.keys())` and
the `session = read_store.get_agora_session(session_id)` line in
`bff_management_nl_ask`.

## Verification Run

Command:

```bash
cd services/control-plane/bff && python3 -m pytest tests/test_bff_b6_management_nl_ask.py -v
```

Result:

```
11 passed
```

(8 original AC tests + 3 new AC#7/8/9 tests for auditRef, evidenceRefs, redactedEvidenceCount)

Syntax check:

```bash
python3 -m py_compile services/control-plane/bff/main.py
```

Result: `syntax OK`

## Acceptance Criteria Audit

| # | Criterion | Test | Result |
|---|---|---|---|
| 1 | Authenticated POST returns HTTP 202 with required data fields | `test_nl_ask_authenticated_returns_202_with_data_fields` | ✅ PASS |
| 2 | Anonymous POST returns HTTP 401 | `test_nl_ask_anonymous_returns_401` | ✅ PASS |
| 3 | focus=trading_pulse restricts sources | `test_nl_ask_focus_trading_pulse_restricts_sources` | ✅ PASS |
| 4 | Idempotency replay cached | `test_nl_ask_idempotency_replay_returns_cached` | ✅ PASS |
| 5 | Missing question returns 422 | `test_nl_ask_missing_question_returns_422` | ✅ PASS |
| 6a | session_id echoed when supplied | `test_nl_ask_session_id_echoed_when_supplied` | ✅ PASS |
| 6b | session_id generated when omitted | `test_nl_ask_session_id_generated_when_omitted` | ✅ PASS |
| R1 | focus=persona_fleet populates context | `test_nl_ask_focus_persona_fleet_populates_context` | ✅ PASS |
| 7 | data.auditRef present with targetType/targetId/href | `test_nl_ask_response_includes_audit_ref` | ✅ PASS |
| 8 | data.evidenceRefs list; seeded refs have /api/v1/knowledge/evidence href | `test_nl_ask_response_includes_evidence_refs_with_api_href` | ✅ PASS |
| 9 | meta.redactedEvidenceCount is non-negative integer | `test_nl_ask_response_includes_redacted_evidence_count` | ✅ PASS |

## Audit Conclusion

All original acceptance criteria continue to pass. The three reviewer-required
grounding fields (auditRef, evidenceRefs, meta.redactedEvidenceCount) are now
implemented, tested, and verified. Evidence refs carry canonical
`/api/v1/knowledge/evidence/{ref_id}` hrefs. The redacted evidence count flows
from `redact_evidence_refs` via `_capabilities_for_identity`.

## Closeout Verification (2026-05-23)

- PR #490 merged into dev at c1bcfb88
- Codex re-review approved: 18 focused tests (B6-002 + B6-003 combined) and py_compile verified
- Owner closeout: 11 focused B6-002 tests pass, py_compile clean
- Worktree clean; no unrelated dirty files folded into closeout commit
- Status: ready for `done`
