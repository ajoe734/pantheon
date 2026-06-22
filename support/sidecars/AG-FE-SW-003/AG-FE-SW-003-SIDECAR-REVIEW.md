# AG-FE-SW-003 Review Packet

Prepared by: Claude (sidecar AG-FE-SW-003-SIDECAR-REVIEW)
Parent task: AG-FE-SW-003 — Version comparison and readiness UI
Reviewer target: Claude2 (per ai-status.json)
Date: 2026-06-22

---

## 1. Task Summary

AG-FE-SW-003 implements:

1. **`VersionCompareCard`** — multi-version field/metric/risk/readiness diff display card for the Strategy Workshop conversation stream.
2. **`ReadinessGateCard` enhancement** — per-gate missing requirements with hard/soft hardness indicator inside `WorkshopCardRenderer`.
3. **`StrategyWorkshopPage` CTA gate** — "Add to Trading Room" button disabled until `readiness.highest_ready_gate === "trading_room"`.

Owner: Claude2  
Reviewer: Codex  
PR: #2257 (branch `task/AG-FE-SW-003` → `dev`)

---

## 2. PR and CI Status

| Check | Status |
|---|---|
| Commit trailers | SUCCESS |
| Runtime mirror guard | SUCCESS |
| Smoke acceptance | SUCCESS |
| PR state | OPEN (auto-merge enabled) |
| Commits on branch | 1 (`0a970fe0` — "AG-FE-SW-003: anchor version-compare-card and readiness UI") |

All three required CI gates pass as of 2026-06-22T11:24Z.

---

## 3. Changed Files

| File | Change | Lines |
|---|---|---|
| `execute-plans/src/agora/components/VersionCompareCard.tsx` | New | +468 |
| `execute-plans/src/agora/components/WorkshopCardRenderer.tsx` | Modified | +89 / -53 |
| `execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx` | Modified | +67 / -8 |

No canonical truth files were modified. No L1 policy documents, schemas, or BFF contracts were altered.

---

## 4. Spec Compliance Walk-Through

### 4.1 VersionCompareCard — spec ref: A5, E12

**A5 rule: predicted must never be visually treated as an observed metric.**

Implementation renders `predicted` metrics last and with visually distinct treatment:
- Amber/dashed border (`1px dashed #fbbf24`, background `#fffbeb`)
- Italic font
- Sub-header "Predicted effects (not observed — subject to uncertainty)" separates them from observed rows

This exactly matches the A5 constraint. `backtested_in_sample`, `backtested_oos`, and `paper_observed` use solid borders with distinct hues.

**A5 rule: servant may recommend a version, but `decision_authority` is always `trader`.**

Implementation includes a `data-testid="version-compare-card-{card_id}-decision-authority"` element with text "Decision authority: Trader". This is rendered within the recommendation block with `italic` style. Correct.

**E12 payload field coverage:**

| Spec field | Type implementation | Present? |
|---|---|---|
| `base_version` | `VersionRef` | ✓ |
| `candidate_versions[]` | `VersionRef[]` | ✓ |
| `field_diffs[]` | `FieldDiff` | ✓ |
| `metric_diffs[]` | `MetricDiff` w/ `evidence_class` | ✓ |
| `risk_diffs[]` | `RiskDiff` (optional) | ✓ |
| `readiness_diffs[]` | `ReadinessDiff` | ✓ |
| `recommendation` | optional obj w/ `rationale`, `confidence`, `limitations` | ✓ |

No invented fields. No routes added.

### 4.2 ReadinessGateCard enhancement — spec ref: A6, E13

**Three gates rendered:** `preliminary_research`, `full_validation`, `trading_room` per `GATE_LABELS` map.

**Gate state colors:** `ready` → green, `conditional` → amber, `blocked` → red, `not_assessed` → grey, `stale` → amber/orange. Matches A6 state machine.

**Missing requirements display:** filters `state === "missing" || "partial"`, shows:
- `✗` (hard) or `○` (soft) prefix, with per-requirement title and optional summary.
- `conditional_assumptions` shown in italic amber below the gate row.

This directly satisfies the A6 requirement table format (hard/soft per requirement).

**E13 payload field coverage:**

| Spec field | Type implementation | Present? |
|---|---|---|
| `gates[3]` (typed tuple) | `[ReadinessGateEntry, ...]` | ✓ |
| `hard_blockers[]` | present in type | ✓ (not currently rendered in card, see §5.1) |
| `temporary_assumptions[]` | present in type | ✓ (not rendered in card) |
| `highest_ready_gate` | used in Page CTA | ✓ |
| `assessed_at` | present in type | ✓ (not rendered in card) |

### 4.3 StrategyWorkshopPage CTA gate — spec ref: A6 §trading_room

The page gates the "Add to Trading Room" button on:

```ts
const tradingRoomReady = readiness?.highest_ready_gate === "trading_room";
```

- When `readiness` is null (not yet assessed): button is disabled with reason "Readiness not yet assessed".
- When `highest_ready_gate` is "preliminary_research" or "full_validation": button is disabled with reason text showing the current gate.
- When `highest_ready_gate === "trading_room"`: button is enabled (blue, pointer cursor).

`aria-disabled`, `disabled`, and `title` are all set. A reason text is rendered below the button via `data-testid="add-to-trading-room-reason"` when disabled. Correct semantics.

SSE integration: the page subscribes to `workshop.readiness.updated` events and calls `refreshReadiness()`, keeping the button state live without a full page reload.

---

## 5. Gaps and Findings

### 5.1 Missing: no `VersionCompareCard.test.tsx`

The new `VersionCompareCard.tsx` (468 lines) has no dedicated test file. Every other card-type component has one (e.g. `ConsultResultCard.test.tsx`, `ResearchPlanCard.test.tsx`).

The acceptance criterion says "附 UI 測試". The owner noted "15 pass, build clean" but the 15 tests in `StrategyWorkshopPage.test.tsx` do not exercise `VersionCompareCard` directly. There are no tests for:
- predicted metric visual distinction (the critical A5 rule)
- decision-authority label
- field/metric/risk/readiness diff rows

**Severity: medium.** The component works at build/smoke level, but the explicit A5 invariant (predicted ≠ observed) is not tested. The reviewer may require a test file before approval, or may accept a follow-up sidecar task to add tests.

### 5.2 Missing: StrategyWorkshopPage tests do not cover CTA gate states

The 15 tests in `StrategyWorkshopPage.test.tsx` do not include:
- Button disabled when `readiness` is null
- Button disabled when `highest_ready_gate` is "full_validation"
- Button enabled when `highest_ready_gate === "trading_room"`
- Reason text content

**Severity: low–medium.** The button logic is straightforward and the smoke acceptance passes, but the spec explicitly requires that the gate enforces trading-room readiness; test coverage makes this auditable.

### 5.3 Unrendered `PayloadReadinessGate` fields

`hard_blockers`, `temporary_assumptions`, `assessed_at`, and `valid_until` are present in the type but not rendered by `ReadinessGateCard`. The spec (E13) lists them as payload fields, but does not explicitly require rendering all of them in the card.

This may be intentional (minimal card surface), but the reviewer should confirm that omitting `hard_blockers` from the card display is acceptable given A6's emphasis on blockers.

**Severity: low** — depends on design intent. No spec violation found.

### 5.4 No issues found

- No invented schema fields or enum values.
- No new BFF routes added outside the declared contract.
- No `RuntimeBinding` writes from the frontend.
- No order-routing paths.
- No capability allowlist expansions.
- No canonical L1/L2 document mutations.
- No localStorage usage for card payloads.

---

## 6. Acceptance Criteria Checklist

| Criterion | Status |
|---|---|
| 可比較策略版本與套用建議版本 (version comparison + recommendation) | ✓ Implemented |
| 三 readiness gate 狀態正確顯示 (three gate states displayed) | ✓ Implemented |
| 未達 readiness 時無法加入交易作戰室 (CTA disabled when not ready) | ✓ Implemented |
| 附 UI 測試 (UI tests) | ⚠ Partial — no VersionCompareCard.test.tsx; no CTA gate tests |
| 實作與引用 spec/schema 逐欄位一致 (spec field alignment) | ✓ Verified |
| 無自創欄位/route/enum (no invented fields/routes/enums) | ✓ Verified |
| 遇疑問須先開 blocker 澄清 (blockers opened for ambiguities) | N/A — no ambiguities reported |
| 自行臆測或偏離設計稿一律不通過 (no spec deviations) | ✓ No deviations found |

---

## 7. Reviewer Handoff Notes

This packet is prepared for **Claude2** as assigned reviewer.

The two test gaps (§5.1 and §5.2) are the primary items requiring a reviewer decision:

- **Option A**: Request the owner (Claude2) add `VersionCompareCard.test.tsx` and CTA gate tests before approval. Given that the acceptance criterion explicitly lists "附 UI 測試", this is the stricter but correct read.
- **Option B**: Approve with a follow-up sidecar task for tests, if the reviewer judges the smoke/build CI plus the component logic review sufficient for this slice.

The `hard_blockers` rendering gap (§5.3) is advisory; recommend the reviewer confirm the design intent with the spec author before requiring a change.

No blocking correctness defects were found. The A5 invariant (predicted vs observed) is correctly implemented in the render logic even without a dedicated test.

---

## 8. Evidence Summary

| Evidence | Value |
|---|---|
| PR | #2257 — task/AG-FE-SW-003 → dev |
| PR commit | `0a970fe0` |
| CI checks | All 3 SUCCESS as of 2026-06-22T11:24Z |
| Spec refs reviewed | A5, A6, E12, E13 (design-closure-round2) |
| Schema reviewed | `workshop-card-types.ts` field-for-field vs spec |
| Test count (passing per owner) | 15 |
| New test file for VersionCompareCard | None found |
| L1 canonical files modified | None |
