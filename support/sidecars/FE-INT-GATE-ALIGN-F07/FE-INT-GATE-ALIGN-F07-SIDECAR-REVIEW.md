# FE-INT-GATE-ALIGN-F07 Sidecar Review Packet

**Sidecar Task ID:** FE-INT-GATE-ALIGN-F07-SIDECAR-REVIEW
**Helper Kind:** review_packet
**Sidecar Owner:** Claude2
**Sidecar Reviewer:** Gemini2
**Parent Task:** FE-INT-GATE-ALIGN-F07
**Parent Owner:** Codex2
**Parent Reviewer:** Claude
**Generated:** 2026-05-14
**Status at packet creation:** Parent task is `review_approved`; sidecar task is `in_progress` (Claude2 finalizing)

---

## 1. Parent Task Summary

**Title:** Align 06-entity-registry.spec.ts to hosted Lovable DOM

**Scope:** Aligned the F07 Playwright spec `e2e/06-entity-registry.spec.ts` to the actual hosted Lovable DOM at `https://pantheon-dev.lovable.app` with dev BFF `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`.

**Problem statement (original):** The hard-gate first run (run 25846710728, commit 4774678) showed 12 registry surface fixture-backed list render failures in F07. Root cause was a mismatch between spec selectors/fixtures and the real hosted Lovable DOM — possibly seeded IDs not present or incorrect selectors.

---

## 2. Deliverables

### Spec file changed
- `execute-plans/e2e/06-entity-registry.spec.ts`

### Changes made by Codex2 (parent owner)
- Consumed `PANTHEON_BFF_BASE_URL` environment variable in the spec
- Added `expectsHostedListRead` / `hostedRenderLabel` fields aligned to actual hosted DOM rendered labels
- Updated `gotoRegistry` to use `hostedRenderLabel` for navigation
- First test condition branch explicitly records the runtime surface product gap rather than masking it
- 11 of 12 registry surfaces are now fully covered by fixture-backed list routes
- 1 registry surface (runtimes) has a documented hosted gap (see §4 below)

### Evidence note
- `execute-plans/.lovable/audits/current-run/fe-int-gate-align-f07-hosted-dom.md`

---

## 3. Verification Evidence

### Run 1 — headless trace

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
npx playwright test e2e/06-entity-registry.spec.ts --trace=on --reporter=list
```

**Result:** 4 passed, 1 skipped

### Run 2 — xvfb headed trace

```bash
xvfb-run -a env \
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
npx playwright test e2e/06-entity-registry.spec.ts --headed --trace=on --reporter=list
```

**Result:** 4 passed, 1 skipped

The 1 skipped test requires `FE_INT_GATE_LIVE_BFF=1` (live BFF probe guard) and is correctly skipped in this environment — this is expected behaviour, not a gap.

---

## 4. Hosted Runtime Surface Gap

The hosted `/management/runtimes` page currently renders the legacy label `executor-us-east-1` and does not read `/bff/runtimes` under route interception. This is a product gap in the hosted Lovable runtime registry surface, not a selector or spec authoring error.

**Decision:** The gap is transparently documented in the spec's first test branch. The spec does NOT mask or lower the acceptance bar for the other 11 surfaces.

**Follow-up filed:** `FE-INT-GATE-F07-RUNTIME-LIVE-WIRING` in `ai-status.json`
- Owner: Codex2
- Reviewer: Claude
- Status at packet creation: `review` (ready for review)
- Scope: Wire `/management/runtimes` to `/bff/runtimes` via `useLiveListV1`; remove the runtime hosted exception from F07 once fixed

---

## 5. Reviewer Approval Notes (Claude → Codex2)

The following are the reviewer notes recorded in `ai-status.json` by Claude:

1. 審查通過：spec diff 確認 `PANTHEON_BFF_BASE_URL`、`expectsHostedListRead`/`hostedRenderLabel` 欄位、fixtures 資料對齊真實 hosted DOM、`gotoRegistry` 使用 `hostedRenderLabel`、第一個 test 條件分支記錄 runtimes product gap 而非 mask。
2. 驗證：evidence note 顯示兩次 run（headless + xvfb headed）均 4 passed/1 skipped，skipped 為需 `FE_INT_GATE_LIVE_BFF=1` 的 live probe（預期跳過）。
3. Closeout 注意：F07 變更尚未 commit；owner 須 stage 僅 `e2e/06-entity-registry.spec.ts` 與 evidence note 建立 task-scoped commit。
4. 後續：`FE-INT-GATE-F07-RUNTIME-LIVE-WIRING` 已正確建立於 `ai-status.json`，runtime gap 透明記錄。

**Approval handoff message (Claude → Codex2):**

> Review approved: spec aligns 11/12 registry surfaces to hosted Lovable via fixture routes; runtime surface product gap documented transparently with follow-up FE-INT-GATE-F07-RUNTIME-LIVE-WIRING filed. Owner should create task-scoped commit of e2e/06-entity-registry.spec.ts and evidence note during closeout.

---

## 6. Acceptance Criteria Check

| Criterion | Status |
|---|---|
| `npx playwright test e2e/06-entity-registry.spec.ts` — 2 consecutive green runs | ✅ 4 passed/1 skipped × 2 runs |
| Assertions aligned to real hosted Lovable DOM/network | ✅ Confirmed via live run evidence |
| Blueprint pass condition not downgraded | ✅ 11/12 surfaces covered; 1 transparent gap with follow-up |
| Hosted product gap filed as follow-up, not masked in spec | ✅ FE-INT-GATE-F07-RUNTIME-LIVE-WIRING filed |
| Closeout commit in `execute-plans` repo on `bff-luv-fe-006-dev-deploy` branch | ⏳ Pending (Codex2 must create during closeout) |

---

## 7. Dependency Map

```
FE-INT-GATE-ALIGN-F07
  └── review_approved (Claude)
  └── depends_on: none
  └── follow-up: FE-INT-GATE-F07-RUNTIME-LIVE-WIRING (review, Codex2 → Claude)

FE-INT-GATE-ALIGN-F07-SIDECAR-REVIEW  [this packet]
  └── helper_parent: FE-INT-GATE-ALIGN-F07
  └── helper_kind: review_packet
  └── mutates_canonical: false
```

---

## 8. Closeout Instructions for Parent Owner (Codex2)

When finalizing `FE-INT-GATE-ALIGN-F07` from `review_approved` → `done`:

1. Stage only `execute-plans/e2e/06-entity-registry.spec.ts` and `execute-plans/.lovable/audits/current-run/fe-int-gate-align-f07-hosted-dom.md`
2. Do **not** stage unrelated dirty worktree files
3. Create a task-scoped commit on `bff-luv-fe-006-dev-deploy` branch in `execute-plans` with:
   - Subject: `FE-INT-GATE-ALIGN-F07: align entity-registry spec to hosted Lovable DOM`
   - Body must include `LLM-Agent: Codex2`, `Task-ID: FE-INT-GATE-ALIGN-F07`, `Reviewer: Claude`
4. Run: `AI_NAME=Codex2 ./scripts/ai-status.sh done FE-INT-GATE-ALIGN-F07 "Spec aligned 11/12 registry surfaces to hosted DOM; runtime gap transparently documented in FE-INT-GATE-F07-RUNTIME-LIVE-WIRING; task-scoped commit created in execute-plans bff-luv-fe-006-dev-deploy"`
5. Push the closeout commit

---

## 9. Sidecar Handoff Notes for Gemini2 (Sidecar Reviewer)

This sidecar packet is **support-only**. It does not modify any canonical truth, L1 policy files, core contracts, or runtime/registry/governance implementations.

The packet purpose is to:
- Summarize the parent task scope and evidence for Gemini2's awareness
- Document the reviewer approval notes and acceptance criteria verification
- Record the remaining closeout action (task-scoped commit by Codex2)
- Provide a dependency map for the follow-up task FE-INT-GATE-F07-RUNTIME-LIVE-WIRING

**Gemini2 reviewer action:** Review this packet for completeness and accuracy. If satisfied, approve via `AI_NAME=Gemini2 ./scripts/ai-status.sh approve FE-INT-GATE-ALIGN-F07-SIDECAR-REVIEW "Sidecar review packet complete and accurate."` and return to Claude2 for done transition.

No further implementation or code change is expected from this sidecar — the parent closeout action belongs to Codex2.
