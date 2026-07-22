# AG-FE-DYNUI-001 Claude2 Review

**Task:** AG-FE-DYNUI-001 — V10 Strategy Workshop dynamic runtime
**Reviewer:** Claude2
**Owner:** Codex
**Review date:** 2026-06-29
**Verdict:** APPROVED

---

## Scope reviewed

Anchor commit `2160f8be` on branch `task/AG-FE-DYNUI-001`, merged to `dev` via PR #2569 (merge `70a8d1cf`).
PR CI checks: Commit trailers ✅ Runtime mirror guard ✅ Smoke acceptance ✅ Forward to orchestrator ✅

Files reviewed:
- `execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx`
- `execute-plans/src/agora/components/StrategyCompletenessRail.tsx`
- `execute-plans/src/agora/components/StrategyReconstructionCard.tsx`
- `execute-plans/src/agora/components/WorkshopCardRenderer.tsx`
- `execute-plans/src/lib/bff-v1/agora/workshops.ts`
- Tests: `StrategyWorkshopPage.test.tsx`, `StrategyCompletenessRail.test.tsx`, `WorkshopCardRenderer.test.tsx`, `workshops.test.ts`

---

## Acceptance checklist (per sidecar packet)

| # | Criterion | Verdict | Notes |
|---|---|---|---|
| 2 | Workshop is dynamic, not a chat/form/static page | ✅ PASS | Card stream + rail + composer + readiness CTA all present and event-driven |
| 3 | First servant response is reconstruction | ✅ PASS | `orderWorkshopCardsForV10()` places `servant_reconstruction` before next questions after first long description; test verifies ordering |
| 4 | Strategy Reconstruction Card covers V10 content | ✅ PASS | `StrategyReconstructionCard` shows core text, research subquestions (≤7), recognized components (≤12), limitations (≤4 incl. compliance note), confidence %, servant inferences; old plain causal-chain renderer replaced |
| 5 | Card reducer preserves sequence truth | ✅ PASS | `cardReducer` upserts by `card_id`, sorts by `sequence_no`; `orderWorkshopCardsForV10` operates on `slice()` copy; stream-refresh test covers multiple load cycles |
| 6 | SSE event handling complete for this slice | ✅ PASS | `workshop.next_question.updated` and `workshop.message.accepted` added to card-refresh switch; `workshop.version.selected` added; completeness/readiness events remain on separate refresh paths |
| 7 | BFF boundary strict | ✅ PASS | No direct `fetch()` in `StrategyWorkshopPage.tsx`; all API calls via `src/lib/bff-v1/agora/workshops.ts` functions; no Management/RuntimeBinding/broker terminology |
| 8 | V10 12-block rail is data-derived | ✅ PASS | 12 blocks defined with dimension mappings and hint arrays; state derivation: conflict > missing > weak (partial+notes) > inferred (partial, no notes) > confirmed (complete); weighted percent display |
| 9 | No fake 12-block mapping from old schema | ✅ PASS | Block states derived from dimension grades + gap/notes hints, not by pretending 7 generic dimensions satisfy V10 |
| 10 | Readiness controls Trading Room handoff | ✅ PASS | CTA disabled until `highest_ready_gate === "trading_room"` AND handler exists; disabled reason shown; tests verify both enabled and disabled states |
| 11 | No arbitrary frontend code path | ✅ PASS | No `eval()`, `dangerouslySetInnerHTML`, `innerHTML`, `script` injection, or iframes |
| 12 | Design language moves toward V10 | ✅ PASS | Dark shell palette (#11151c, #171b22), IBM Plex Mono for metrics, Noto Sans TC for composer; no static HTML clone |
| 13 | Regression coverage exists | ✅ PASS | 28 Vitest tests: ordering, reconstruction card, 12-block rail states, stream-refresh, readiness CTA, BFF-only calls |
| 14 | Screenshot/Playwright evidence attached | ⚠️ FOLLOW-UP | Deferred to owner closeout — not a blocker for review approval |

Criterion 1 (design-pack source citation) is an owner closeout obligation per sidecar spec, not a reviewer gate.

---

## Key implementation observations

**`postWorkshopMessage` with ETag/idempotency** — correct two-step pattern: GET to obtain `ETag`, POST with `If-Match` and `Idempotency-Key` headers. Idempotency key uses `crypto.randomUUID()` with graceful fallback. Tests verify both headers and the missing-ETag error path.

**`orderWorkshopCardsForV10`** — operates on `cards.slice()` copy, uses `splice()` mutation on the copy only. Handles the case where no long description or no reconstruction card exists by returning the original sorted order.

**`stateForBlock`** — clean priority chain: conflict (any conflict text) → missing (dimension missing or grade=missing) → weak (grade=partial with relevant gap notes) → inferred (grade=partial, no notes) → confirmed (grade=complete). Hint-based note matching avoids hardcoding individual card IDs.

**Scope discipline** — anchor commit correctly notes "Not changing: V11 TradingRoomWorkspaceProposal backend contracts, widget revision lifecycle, grid editor persistence, Management/runtime/broker/order surfaces, or arbitrary widget generation." No scope creep observed.

---

## Minor notes (not blocking)

- The anchor commit trailers still carry `Reviewer: Claude` — this reflects the pre-reassignment state; purely administrative.
- `idempotencyKey()` uses `Math.random()` and `Date.now()` as fallback — appropriate for frontend HTTP client code; the workflow-script prohibition on non-determinism does not apply here.
- Criterion 14 (screenshot/Playwright evidence) should be documented in the owner closeout commit body or a follow-up evidence note.

---

## Verdict

Implementation meets all blocking acceptance criteria. CI is green. 28 tests pass. BFF boundary is strict. No security concerns. Review APPROVED; returning to owner (Codex) for closeout.
