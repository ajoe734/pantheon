# Review Packet: ASK-006

**Sidecar Kind:** review_packet
**Sidecar Task:** ASK-006-SIDECAR-REVIEW
**Parent Task:** ASK-006
**Prepared by:** Claude
**Prepared at:** 2026-05-17
**Reviewer:** Codex
**Parent Task Terminal Status:** done (archived 2026-05-17T01:53:14Z)

---

## Parent Task Summary

**Title:** Consult -> Committee -> Memo -> Review e2e test

**Problem solved:** ASK-001..005 established the consult/committee flow endpoints and SSE publishing. ASK-006 closes the loop by delivering a single-file integration test that walks the full end-to-end path: create an ask session → invoke a committee session → submit and publish a committee memo → verify the `consult_memo_published` SSE event is emitted → verify the handoff lands in the management review queue.

**Solution delivered:**
- `services/consultation/test_e2e_consult_review.py` — the e2e test (1 test function, pytest-compatible)
- `services/consultation/e2e_fixtures.py` — `ConsultReviewE2E` dataclass and `consult_review_e2e()` context manager that wires a FastAPI `TestClient` against an isolated in-memory BFF with temp-dir stores
- `services/control-plane/bff/main.py` — added `consult_memo_published` SSE event emission on memo publish, carrying `correlation_id`, `memo_id`, `session_id`, and `handoff_id`

**Owner:** Codex
**Reviewer:** Claude2 (chair-reassigned from Codex2 due to quota pause)
**Phase:** Sprint 7 / EPIC-CONSULT-ADVANCED
**Branch:** `bff-luv-fe-006-dev-deploy`
**Delivery commit:** `014ee6e121eaa44e3d947774e2a25c5c08f23df8`
**Commit subject:** `ASK-006: add consult review e2e`

---

## Artifacts Delivered

| Artifact | Path | Status |
|---|---|---|
| E2E test | `services/consultation/test_e2e_consult_review.py` | Verified present (65 lines) |
| E2E fixture harness | `services/consultation/e2e_fixtures.py` | Verified present (190 lines) |
| BFF SSE publish extension | `services/control-plane/bff/main.py` | Modified (+38 lines; scoped to `consult_memo_published` event) |

---

## Acceptance Criteria — Verification Summary

| Criterion | Result | Evidence |
|---|---|---|
| Test exercises `POST /bff/agora/ask/sessions` → committee invoke → memo publish → handoff queue arrival | **PASS** | `test_ask_006_consult_committee_memo_reaches_management_review_handoff` calls all four steps in sequence; each step asserted |
| Test verifies SSE event `consult_memo_published` is emitted with correct `correlation_id` | **PASS** | Lines 45–51 of test: filters `ask_events()` for `type == "consult_memo_published"`, asserts `data.correlation_id`, `data.memo_id`, `data.session_id` |
| Test uses `pytest -q -x` and exits 0 | **PASS** | Verified: `pytest -q -x services/consultation/test_e2e_consult_review.py` → **1 passed**; full consultation suite → **15 passed**; ASK-004/ASK-005 regression → **43 passed** |
| No other `services/consultation` files modified | **PASS** | Commit stat: only `e2e_fixtures.py` and `test_e2e_consult_review.py` added in `services/consultation/`; `main.py` change is in `services/control-plane/bff/` (outside the boundary) |

All acceptance criteria: **PASS**

---

## Code Quality Notes (from reviewer Claude2)

- `ConsultReviewE2E` isolates state correctly: temp-dir stores, idempotency key cache, and SSE buffers are all cleared and restored in the `finally` block — no test-to-test state leakage.
- Auth stub (`PANTHEON_BFF_AUTH_STUB=true`, `PANTHEON_BFF_AUTH_MODE=permissive`) correctly bypasses credential checks without modifying BFF auth logic.
- The handoff assertion covers `handoffType`, `destination.app`, `destination.queue`, and three `payload` fields — comprehensive payload verification.
- `idempotency_key()` uses `uuid4` to prevent key collisions across repeated test runs.
- `e2e_fixtures.py` uses `sys.path.insert` to resolve BFF imports; this is an accepted pattern for service-local e2e harnesses in this repo.
- No blocking findings.

---

## Delivery Metadata

| Field | Value |
|---|---|
| Repository | `ajoe734/pantheon` |
| Branch | `bff-luv-fe-006-dev-deploy` |
| Commit | `014ee6e121eaa44e3d947774e2a25c5c08f23df8` |
| Commit author | Codex |
| Push status | `ahead` (2 commits ahead of `origin/bff-luv-fe-006-dev-deploy` at archive time) |
| Dirty worktree at closeout | Yes — 34 unrelated entries; task-owned files cleanly staged and committed |
| Verified commands | `pytest -q -x services/consultation/test_e2e_consult_review.py` → 1 passed; `pytest -q services/consultation` → 15 passed; `pytest -q services/control-plane/bff/test_ask_004_memo_publish_contract.py services/control-plane/bff/test_ask005_sse_event_publishing_contract.py` → 43 passed |
| `git diff --cached --check` | Clean |

---

## Review Decision

**Decision:** APPROVED

**Reviewer:** Claude2

**Review notes (zh):**
- ASK-006 驗證通過。e2e test 覆蓋完整流程：POST /bff/agora/ask/sessions → committee invoke → memo publish → management handoff queue。
- SSE event `consult_memo_published` 正確攜帶 `correlation_id`、`memo_id`、`session_id`、`handoff_id`；handoff 驗證 `handoffType=consult_memo_to_management_review`、`destination.app=management`、`destination.queue=consult_memo_review`、payload 欄位齊全。
- `pytest -q -x services/consultation/test_e2e_consult_review.py` → 1 passed；consultation suite → 15 passed；ASK-004/ASK-005 regression → 43 passed。
- `diff --check` scoped files → clean；無額外 `services/consultation` 檔案被修改。

---

## Handoff Note to Codex (sidecar reviewer)

This packet summarizes the completed and archived parent task ASK-006. The parent task has been finalized (`done`) by Codex with commit `014ee6e1`. All four acceptance criteria passed.

**No action on canonical truth is required.** This sidecar is a support artifact only — it records the review evidence, delivery metadata, and reviewer decision for parent task ASK-006. The reviewer's role here is to confirm the packet is accurate and complete.

If the parent task needs a follow-up push (delivery metadata shows `push_status: ahead`), that is a separate publication step owned by the parent task owner (Codex) or chair-review — not within this sidecar's scope.
