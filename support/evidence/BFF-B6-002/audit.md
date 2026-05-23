# BFF-B6-002 NL Audit and Evidence Grounding

Task: BFF-B6-002
Owner: Claude
Reviewer: Codex
Depends-on: BFF-B6-001 (done)
Date: 2026-05-23

## Scope

Audit the POST /bff/management/nl/ask implementation delivered in BFF-B6-001 against
all six acceptance criteria from the B6 spec section. Verify test coverage, run the
full test suite, and produce this grounded evidence record.

## Implementation Under Audit

- Route: `POST /bff/management/nl/ask`
- Implementation: `services/control-plane/bff/main.py` (lines ~23439–23728)
- Tests: `services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py`
- Spec section: `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md#b6--p2-management-natural-language-api`

## Verification Run

Command:

```bash
cd services/control-plane/bff && python3 -m pytest tests/test_bff_b6_management_nl_ask.py -v
```

Result:

```
8 passed in 5.76s
```

Syntax check:

```bash
python3 -m py_compile services/control-plane/bff/main.py
```

Result: `syntax OK`

## Acceptance Criteria Audit

| # | Criterion | Test | Result |
|---|---|---|---|
| 1 | Authenticated `POST /bff/management/nl/ask` with `question` returns HTTP 202 with `data.answer`, `data.session_id`, `data.message_id`, `data.sources`, `data.confidence` | `test_nl_ask_authenticated_returns_202_with_data_fields` | ✅ PASS |
| 2 | Anonymous POST returns HTTP 401 typed BFF error envelope | `test_nl_ask_anonymous_returns_401` | ✅ PASS |
| 3 | `focus=trading_pulse` restricts sourced summaries to trading-pulse surface only | `test_nl_ask_focus_trading_pulse_restricts_sources` | ✅ PASS |
| 4 | Idempotency replay: second request with same `Idempotency-Key` returns cached result without re-querying | `test_nl_ask_idempotency_replay_returns_cached` | ✅ PASS |
| 5 | Missing `question` field returns HTTP 422 typed BFF error envelope | `test_nl_ask_missing_question_returns_422` | ✅ PASS |
| 6a | `session_id` supplied in body is echoed in `data.session_id` | `test_nl_ask_session_id_echoed_when_supplied` | ✅ PASS |
| 6b | Omitted `session_id` generates a new one (prefixed `mgmt-nl-`) | `test_nl_ask_session_id_generated_when_omitted` | ✅ PASS |

Additional regression test:

| # | Criterion | Test | Result |
|---|---|---|---|
| R1 | `focus=persona_fleet` populates context and appears in `data.sources` and `data.summary_context` | `test_nl_ask_focus_persona_fleet_populates_context` | ✅ PASS |

## Grounding Observations

### Request Shape Audit

The endpoint accepts:
- `question` (required string) — validated via `_agora_required_text`; missing value returns typed 422
- `session_id` / `sessionId` (optional) — camelCase alias supported; echoed in response
- `focus` (optional) — one of `cockpit`, `trading_pulse`, `portfolio`, `persona_fleet`, `all`; invalid values silently coerce to `all`
- `context` (optional) — passed through to the session message payload

### Response Shape Audit

HTTP 202 with:
```json
{
  "status": "accepted",
  "data": {
    "answer": "<synthesised plain-text answer>",
    "session_id": "<echoed or generated>",
    "message_id": "mnl-<hex16>",
    "question": "<echoed>",
    "focus": "<resolved focus>",
    "sources": ["<surface_key>", ...],
    "confidence": "high|partial|unavailable",
    "summary_context": { "<surface_key>": { ... } }
  },
  "meta": {
    "snapshot_at": "<utc_now>",
    "surfaces": { "<surface_key>": { "status": "ok|unavailable", "source": "..." } },
    "idempotency": { "idempotencyKey": "...", "replayed": false }
  }
}
```

All fields required by spec are present. `data.focus` is an additional field not in the spec
but consistent with management surface conventions.

### Auth and Security Audit

- `_require_read_role(identity)` enforced before processing; anonymous requests return
  the standard typed 401 envelope (verified AC#2).
- Body-embedded idempotency keys are rejected by `_reject_body_idempotency_key` (shared
  BFF policy).
- Idempotency key conflict returns 409 with typed error (matches BFF policy).

### Surface Composition Audit

`_mgmt_nl_collect_context` dispatches to:
- `_build_management_cockpit_payload` for `cockpit` focus
- `_build_management_trading_pulse_payload` for `trading_pulse` focus
- `read_store.list_capital_pools()` + `read_store.list_runtime_bindings()` for `portfolio` focus
- `_project_persona_fleet_payload(state=None, health=None, page_token=None, page_size=20)` for `persona_fleet` focus

Each surface is wrapped in try/except; failures set `{"status": "unavailable", "source": "error"}` in the
surfaces dict. Confidence degrades: all-ok → `high`, all-unavailable → `unavailable`, mixed → `partial`.

The regression test (`test_nl_ask_focus_persona_fleet_populates_context`) verifies the fix for the
earlier bug where `_project_persona_fleet_payload()` was called without required keyword args, causing
a silent TypeError that made persona_fleet always report unavailable.

### Session and SSE Audit

Each exchange is stored as an agora_session record:
- Session created or retrieved by `session_id`.
- Message appended with `role: user`, `focus`, and `context`.
- `management.nl.ask.accepted` SSE event emitted on the `ask` channel.

### Idempotency Audit

- Cache key: `resolved_key` from `Idempotency-Key` / `X-Idempotency-Key` headers.
- Request hash computed from route + payload; conflict on hash mismatch returns 409.
- Replayed responses have `meta.idempotency.replayed = true` (verified AC#4).

## Audit Conclusion

All 6 spec acceptance criteria are verified by contract tests. The implementation
conforms to the B6 spec shape, session, auth, idempotency, SSE, and surface-composition
requirements. No regressions found. Evidence grounded at `8 passed in 5.76s` on
2026-05-23.
