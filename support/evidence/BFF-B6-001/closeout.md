# BFF-B6-001 Closeout Evidence

Task: BFF-B6-001
Owner: Claude
Reviewer: Codex
Status before closeout: review_approved

## Reviewed Delivery

- Backend route: `POST /bff/management/nl/ask`
- Backend implementation: `services/control-plane/bff/main.py`
- Backend contract tests: `services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py`
- Integration spec: `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md#b6--p2-management-natural-language-api`

Implementation merged to `dev` through PR #479.

## Acceptance Evidence

- Endpoint requires read-role auth; anonymous requests return typed 401 envelope.
- Missing `question` field returns 422.
- Optional `focus`, `session_id`, and `context` fields accepted.
- Collects management surface summaries (cockpit, trading_pulse, portfolio,
  persona_fleet) filtered by the `focus` hint.
- Synthesises a plain-text answer grounded in collected snippets.
- Stores each exchange as an agora_session record.
- Emits `management.nl.ask.accepted` SSE event on the `ask` channel.
- Supports Idempotency-Key / X-Idempotency-Key replay semantics
  (replayed results get `meta.idempotency.replayed = true`).
- Returns HTTP 202 with `data.answer`, `session_id`, `message_id`,
  `sources`, `confidence`, and `meta.surfaces`.
- Regression test confirms persona_fleet context collection filters by focus.

## Verification

Command run during owner finalization:

```bash
python3 -m pytest services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py -v
```

Result: `8 passed in 4.66s`.

Review approval: Codex approved at HEAD 409eed29 (2026-05-23T11:39:35Z).
Review notes: contract matches B6 spec question/focus/data.answer shape; all 8 pytest cases pass.

## Closeout Notes

This closeout evidence does not broaden canonical architecture. It records the
review-approved BFF-B6-001 delivery and the focused verification used before
owner finalization.
