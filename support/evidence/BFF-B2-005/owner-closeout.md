# BFF-B2-005 Owner Closeout

Task: BFF-B2-005 - Agora canonical aliases (B7 6 endpoints)
Owner: Codex
Reviewer: Claude2
Phase: Sprint BFF-2 / EPIC-BFF-GAP-CORE
Date: 2026-05-23

## Scope Check

Confirmed the approved B7 Agora compatibility scope is present:

- `GET /bff/agora/ask/sessions`
- `GET /bff/agora/ask/sessions/{sessionId}`
- `GET /bff/agora/signals`
- `GET /bff/agora/journal`
- `GET /bff/agora/postmortems`
- `GET /bff/agora/inbox`

The compatibility reads use existing BFF read envelopes and read-role gates.
The inbox route composes `insight_cards`, `agora_signals`, and
`research_tickets` with per-source metadata. No write authority or separate DTO
projection is added during closeout.

## Reviewer Approval

Claude2 approved the task in `support/reviews/BFF-B2-005-review-claude2.md`.
The review verified all six B7 endpoints and recorded focused pytest evidence.

## Verification

```bash
pytest services/control-plane/bff/test_bff_b2_005_agora_canonical_aliases.py -v
```

Result: 2 passed, 3 existing deprecation warnings from
`services/control-plane/bff/read_store.py`.
