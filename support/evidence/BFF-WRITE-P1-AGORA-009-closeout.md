# BFF-WRITE-P1-AGORA-009 Closeout Evidence

Task: BFF-WRITE-P1-AGORA-009 — POST /bff/runtimes (method add — GET only existed)
Owner: Claude
Reviewer: Claude2
Phase: Sprint BFF-WRITE-GAP / EPIC-WRITE-GAP-P1-AGORA

Finalization owner note: implementation was authored by Codex2 and merged via
PR #618; Claude2 recorded the review approval; Claude (task owner) performed the
owner closeout under `owned_finalize_dispatch`.

## Delivery

- Implementation PR #618 merged to `dev` on 2026-05-29T08:57:11Z
  (merge commit `314e3379`), title `BFF-WRITE-P1-AGORA-009: add runtime create route`.
- Review evidence PR #657 (`BFF-WRITE-P1-AGORA-009: review approval by Claude2`)
  carries the reviewer packet `support/reviews/BFF-WRITE-P1-AGORA-009-review-claude2.md`
  and this closeout note into `dev`.

## Scope Delivered (Card P1-9)

`POST /bff/runtimes` entity-create endpoint, creating a runtime binding in the
`stopped` state.

- Handler: `services/control-plane/bff/main.py:37934`
  (`@app.post("/bff/runtimes", status_code=201)`).
- Body schema: `name`, `persona_id`, `binding_id`, `deployment_plan_id`,
  `runtime_kind` (`paper|live`) plus optional params.
- Validation: required-string fields via `_runtime_create_required_string`;
  `runtime_kind` restricted to `paper|live` (422 `VALIDATION_FAILED` otherwise).
- Conflict: binding that already owns a runtime → 409 `RESOURCE_CONFLICT`
  via `_raise_if_runtime_binding_conflict`.
- Response: 201 with `id`, `name`, `state="stopped"`, `persona_id`,
  `binding_id`, `deployment_plan_id`, `runtime_kind`, `created_at`;
  `meta.evidenceKind = "runtime.create"`.
- SSE: `runtime.created` + `management.runtime-status` published to the runtime
  buffer.
- Idempotency: replay returns the original record; same key + different payload
  → 409 `IDEMPOTENCY_CONFLICT` (`_GOV_BFF_IDEMPOTENCY`).
- Permission: operator role enforced via `_require_operator_role`.

FE follow-up (re-enable management-agent `create_runtime` tool) is out of scope
for this task, as noted in the acceptance criteria.

## Files

- `services/control-plane/bff/main.py` — POST handler + helpers
  (merged via PR #618).
- `services/control-plane/bff/test_bff_write_gap_2026_05_28.py` — runtime
  create tests (merged via PR #618).
- `docs/04/pantheon_bff_write_gap_2026-05-28/BFF_WRITE_GAP_SPEC.md` /
  `execute-plans/.lovable/specs/be-requirements/BE_WRITE_GAP_SPEC_2026-05-28.md`
  — spec references.
- `support/reviews/BFF-WRITE-P1-AGORA-009-review-claude2.md` — reviewer approval
  (PR #657).
- `support/evidence/BFF-WRITE-P1-AGORA-009-closeout.md` — this packet (PR #657).

## Reviewer Approval

Claude2 verdict: APPROVED. "審查通過：POST /bff/runtimes 實作符合 P1-9 規格，
3 項 pytest 全部通過，回應格式、SSE 事件、idempotency 與衝突處理均正確."
Full checklist in `support/reviews/BFF-WRITE-P1-AGORA-009-review-claude2.md`.

## Verification

Command (run at closeout from `services/control-plane/bff/`):

```
python3 -m pytest test_bff_write_gap_2026_05_28.py -q
6 passed in 2.48s
```

Runtime-create coverage:

- `test_post_bff_runtimes_creates_stopped_runtime_and_replays_idempotently` ✅
- `test_post_bff_runtimes_rejects_binding_that_already_has_runtime` ✅
- `test_post_bff_runtimes_validates_runtime_kind` ✅
