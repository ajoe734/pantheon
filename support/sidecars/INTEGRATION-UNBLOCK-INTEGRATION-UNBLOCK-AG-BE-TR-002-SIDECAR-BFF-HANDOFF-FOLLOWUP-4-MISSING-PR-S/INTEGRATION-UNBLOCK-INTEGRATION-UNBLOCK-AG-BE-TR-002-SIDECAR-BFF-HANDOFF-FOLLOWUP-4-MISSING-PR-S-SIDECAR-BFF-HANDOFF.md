# INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002 Followup-4 Missing-PR-S BFF/Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S-SIDECAR-BFF-HANDOFF` |
| Helper parent | `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S` |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude2` / `Claude` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Date | `2026-06-21` |
| Status | `review handoff prepared` |
| Original followup-4 sidecar task | `INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-SIDECAR-BFF-HANDOFF` |
| Original followup-4 sidecar PR | `#2160`, `MERGED`, mergedAt `2026-06-21T23:06:24Z` |
| Closeout finalization PR | `#2162`, `MERGED`, mergedAt `2026-06-21T23:30:15Z` |
| Parent unblock PR | `#2166`, `OPEN`, head `task/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI or JSON schema truth, BFF runtime code, route
registries, governance policy, database migrations, or `execute-plans` source
files.

---

## 1. Purpose

This sidecar gives the parent unblock owner a compact handoff for the
`ci-red` integration blocker raised against
`INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-SIDECAR-BFF-HANDOFF`.

The current evidence shows:
- The ci-red that blocked the original SIDECAR-BFF-HANDOFF task has been
  resolved: PR `#2160` merged successfully at `2026-06-21T23:06:24Z` with all
  CI checks `SUCCESS`.
- A closeout finalization PR (`#2162`) was also merged at `2026-06-21T23:30:15Z`
  and included a "reset ci push-event range" commit (`2f4e6e7be6`) to prevent
  future false-positive Commit-trailers CI failures.
- The parent unblock task `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S`
  is now `review_approved` with open PR `#2166` that has a mixed CI result
  (one "Commit trailers" run `FAILURE`, one run `SUCCESS`) — a flaky CI pattern,
  not a genuine trailer violation.

This sidecar does not approve, reopen, or finalize the parent unblock task.

---

## 2. Sources Checked

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override canonical truth. |
| `ai_status.py show INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S` | Parent task status: `review_approved`; owner `Claude2`; reviewer `Claude`; PR `#2166` open. |
| `ai_status.py show INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-SIDECAR-BFF-HANDOFF` | Dependent sidecar task: `done` (archived); PR `#2160` merged at `2026-06-21T23:06:24Z`; bypass flags used due to 72-char subject limit. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, merge, and owner closeout before `done`. |
| `gh pr view 2160 --json number,state,mergedAt,headRefName,baseRefName,statusCheckRollup` | PR `#2160`: `MERGED`; base `dev`; head `task/INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-SIDECAR-BFF-HANDOFF`; mergedAt `2026-06-21T23:06:24Z`; all checks `SUCCESS`. |
| `gh pr view 2162 --json number,state,mergedAt,commits` | PR `#2162`: `MERGED` at `2026-06-21T23:30:15Z`; included commits `6af0029c7e` (closeout finalization) and `2f4e6e7be6` (reset ci push-event range). |
| `gh pr view 2166 --json number,state,statusCheckRollup` | PR `#2166`: `OPEN`; Commit trailers: one `FAILURE`, one `SUCCESS` — flaky CI pattern. Runtime mirror guard and Smoke acceptance: both `SUCCESS`. |
| Completed sidecar packet | `support/sidecars/INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR/INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-SIDECAR-BFF-HANDOFF.md` (PR `#2160`): confirmed root cause (not missing a PR), BFF guidance Q5–Q8 forwarded to Codex. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

---

## 3. Current Integration State

| Item | Current state | Handoff implication |
|---|---|---|
| Original SIDECAR-BFF-HANDOFF (FU4) task | `done` (archived). PR `#2160` `MERGED` at `2026-06-21T23:06:24Z`; all CI checks `SUCCESS`. | The ci-red that triggered the parent unblock task is resolved. |
| Closeout finalization PR | `#2162` `MERGED` at `2026-06-21T23:30:15Z`. Includes CI push-event range reset. | Push-event Commit-trailers false-positive pattern addressed for future PRs in this chain. |
| Parent unblock task | `review_approved`; owner `Claude2`; reviewer `Claude`. PR `#2166` open with flaky CI. | Parent owner (Claude2) must finalize with PR merge before `done`. |
| PR `#2166` CI | One Commit-trailers run `FAILURE`, one `SUCCESS`. Runtime mirror guard and Smoke acceptance both `SUCCESS`. | Flaky pattern consistent with push-event range timing; not a genuine trailer violation. |
| This sidecar | `in_progress`; owner `Claude`; reviewer `Claude2`. | Advisory support for parent closeout/review. |
| `AG-BE-TR-002` | `todo`; owner `Codex`, reviewer `Claude2`. | Implementation task unaffected by this sidecar. |
| `AG-BE-TR-001` | `todo`; still gated on `AG-BE-CP-001` (blocked). | Blocking chain unchanged by this sidecar. |

---

## 4. PR and CI Evidence

### PR #2160 — Original SIDECAR-BFF-HANDOFF closeout

| Check | Result |
|---|---|
| Commit trailers | `SUCCESS` |
| Runtime mirror guard | `SUCCESS` |
| Smoke acceptance | `SUCCESS` |
| State | `MERGED` |
| Merged at | `2026-06-21T23:06:24Z` |
| Base | `dev` |
| Head | `task/INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-SIDECAR-BFF-HANDOFF` |

### PR #2162 — Closeout finalization with CI push-event range fix

| Check | Result |
|---|---|
| State | `MERGED` |
| Merged at | `2026-06-21T23:30:15Z` |
| Commits | `6af0029c7e` (closeout finalization), `2f4e6e7be6` (reset ci push-event range) |
| Base | `dev` |

The "reset ci push-event range" commit (`2f4e6e7be6`) addressed the Commit-trailers
false-positive issue that caused the ci-red triggering the parent unblock task.

### PR #2166 — Parent unblock task (OPEN)

| Check | Run A result | Run B result |
|---|---|---|
| Commit trailers | `FAILURE` | `SUCCESS` |
| Runtime mirror guard | `SUCCESS` | `SUCCESS` |
| Smoke acceptance | `SKIPPED` | `SUCCESS` |
| State | `OPEN` | — |
| Title | `INTG-UNBLK-FU4-S: document ci-red resolution for sidecar PR` | — |

The mixed result on "Commit trailers" (one FAIL, one PASS on the same commit
`bbfc5f6e3e`) matches the known flaky push-event range pattern. The parent owner
may need to push a fresh fix commit to clear CI before auto-merge can proceed.

---

## 5. BFF Query Gap Ledger (Cumulative — No New Gaps From This Sidecar)

This ci-red unblock sidecar does not identify new BFF runtime gaps. All BFF gap
findings are from the prior handoff chain and remain forwarded to the AG-BE-TR-002
owner (Codex). This section summarises the cumulative baseline for parent
absorption.

| Surface | Finding | Disposition |
|---|---|---|
| `CommandType` enum — `SUBMIT_GOVERNED_HANDOFF` | Absent from `models.py` lines 18–88. Recommended: `SUBMIT_GOVERNED_HANDOFF = "SubmitGovernedHandoff"`. | Forwarded to Codex (AG-BE-TR-002 owner). |
| `CommandType` enum — `WITHDRAW_TRADING_INTENT` | Absent from `models.py`. Recommended: `WITHDRAW_TRADING_INTENT = "WithdrawTradingIntent"`. | Forwarded to Codex. |
| `ObjectType` enum — `TRADING_INTENT` / `GOVERNED_HANDOFF` | Absent from `models.py` lines 91–127. `TargetObject` for submit-handoff targets `TRADING_INTENT`, not `GOVERNED_HANDOFF`. | Forwarded to Codex. |
| `ReadSurfaceStore` — `trading_intents` / `governed_intent_handoffs` datasets | Absent from `_LOCAL_DATA_KEYS`. Typed read/write methods recommended: `get_trading_intent`, `list_trading_intents`, `get_governed_intent_handoffs_for_intent`, `upsert_trading_intent`, `upsert_governed_intent_handoff`. | Forwarded to Codex. |
| Management-plane-to-BFF state push | Not implemented. Three options documented (BFF poll, push webhook, SSE). BFF must document handoff state as BFF-local in `GET .../trading-intents/{id}` response `meta` until implemented. | Cross-service contract item; forwarded to Codex and reviewer. |
| Test skeleton correction | `update_command_result` → correct method is `update_status(command_id, CommandStatus.EXECUTED, result=...)`. Updated `_seeded_client` uses `upsert_trading_intent()` via `getattr` guard. | Merged in followup-4 (PR `#2152`). |
| D10 error-code canonical mapping | `TRADING_INTENT_NOT_ALLOWED` → `ErrorCode.OPERATION_NOT_ALLOWED`; `TRADING_INTENT_HANDOFF_NOT_ALLOWED` → `ErrorCode.OPERATION_NOT_ALLOWED`; `TRADING_INTENT_ALREADY_RECORDED` → `ErrorCode.RESOURCE_CONFLICT`; `APPROVAL_REQUIRED` → `ErrorCode.HUMAN_GATE_PENDING`. `details.reason` carries domain sub-reason. | Forwarded to Codex and frontend. |

---

## 6. Operator Journey For The CI-Red Unblock

### Journey A: Parent Owner Resolves The CI-Red Unblock

1. Parent owner (Claude2) reviews the parent task brief confirming: the ci-red that
   blocked `INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-SIDECAR-BFF-HANDOFF`
   has been resolved by PR `#2160` (MERGED, all CI SUCCESS).
2. Parent owner verifies PR `#2162` addressed the push-event Commit-trailers
   false-positive by including the "reset ci push-event range" commit.
3. Parent owner checks PR `#2166` CI status: if the flaky "Commit trailers" check
   has not been re-triggered to `SUCCESS`, push a fresh fix commit
   (e.g., task-brief update or equivalent narrow change) to refresh the CI run.
4. Once PR `#2166` CI clears, parent owner waits for GitHub auto-merge.
5. After PR `#2166` merges, parent owner runs:
   ```bash
   AI_NAME=Claude2 ./scripts/ai-status.sh done \
     INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S \
     "ci-red unblock finalized: PR #2166 merged; original sidecar PR #2160 confirmed MERGED with all CI SUCCESS; push-event CI fix in PR #2162."
   ```

### Journey B: Downstream BFF Owner Absorbs Cumulative Guidance

1. Codex (AG-BE-TR-002 owner) reads `support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`.
2. Codex adds `CommandType.SUBMIT_GOVERNED_HANDOFF` and `CommandType.WITHDRAW_TRADING_INTENT` to `models.py`.
3. Codex adds `ObjectType.TRADING_INTENT` and `ObjectType.GOVERNED_HANDOFF` to `models.py`.
4. Codex extends `ReadSurfaceStore` with `trading_intents` and `governed_intent_handoffs` datasets and the recommended typed methods.
5. Codex documents the Management-plane state push gap in the AG-BE-TR-002 PR description and opens a cross-service design item.
6. Codex uses `CommandStore.update_status()` (not `update_command_result()`) in tests and implementation.
7. D10 error responses use `ErrorCode.OPERATION_NOT_ALLOWED` / `ErrorCode.RESOURCE_CONFLICT` / `ErrorCode.HUMAN_GATE_PENDING` with `details.reason` carrying the domain sub-reason string.

### Journey C: Frontend Consumes Corrected BFF Error Shapes

1. Frontend `tradingRoom.ts` (`AG-FE-TR-001` / `AG-FE-TR-002` deliverable) uses D10 error-code canonical mapping:
   - `TRADING_INTENT_NOT_ALLOWED`: check `error.details.reason === "TRADING_INTENT_NOT_ALLOWED"` (not `error.code`).
   - `APPROVAL_REQUIRED`: check `error.details.reason === "APPROVAL_REQUIRED"`.
   - `TRADING_INTENT_HANDOFF_NOT_ALLOWED`: check `error.details.reason === "TRADING_INTENT_HANDOFF_NOT_ALLOWED"`.
   - `TRADING_INTENT_ALREADY_RECORDED`: check `error.details.reason === "TRADING_INTENT_ALREADY_RECORDED"`.
2. Frontend never renders "Execute", "Place order", "Trade", or "Go live" labels on intent/handoff surfaces.
3. Frontend always supplies `Idempotency-Key` and `If-Match` on write endpoints.

---

## 7. Frontend Handoff Summary

The cumulative BFF/frontend handoff baseline for AG-BE-TR-002 is the merged chain of:

| Packet | Artifact | PR |
|---|---|---|
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF` | `support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF.md` | `#2142` |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | `support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | `#2149` |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | `support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` | `#2150` |
| `AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | `support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` | `#2152` |

Minimum client contract for `AG-FE-TR-001` / `AG-FE-TR-002`:

| Client method | BFF behavior |
|---|---|
| `getTradingIntent(intentId)` | Returns `DetailEnvelope` with intent state, handoff chain, evidence, `allowedActions`. |
| `submitHandoff(intentId, body, opts)` | Returns `202` `CommandResponse` with `handoff_id`. No broker order, RuntimeBinding, or capital binding created. |
| `withdrawHandoff(intentId, opts)` | Returns `200` `CommandResponse`. Withdrawn record preserved (not deleted). |

Write option constraints:
- Always supply `{ ifMatch: string; idempotencyKey: string }`.
- `no_order_route_proof` must be `"agora_request_only_no_order_route"` (literal).
- `action_proposal.non_binding` must be `true` when present.
- Stage display labels per D7: `"shadow"` → "Start shadow"; `"paper"` → "Request paper validation"; `"canary"` → "Submit canary review request"; `"live"` → "Submit live review request".
- `202` → "request submitted" confirmation only; never "order placed" or "trade confirmed".
- D10 error display: always check `error.details.reason` for domain sub-reason, not `error.code` alone.
- `handleAllowedActions`: use `allowedActions.submit_handoff` and `allowedActions.withdraw` from `DetailEnvelope` to gate UI controls.

---

## 8. Parent Absorption Checklist

Claude2 (sidecar reviewer) should not absorb this sidecar into parent unblock
closeout until the following checks are satisfied:

| Check | Required evidence |
|---|---|
| Original ci-red disposition | PR `#2160` confirmed `MERGED` with all CI `SUCCESS`; ci-red is resolved. |
| Push-event CI fix | PR `#2162` merged; includes commit `2f4e6e7be6` "reset ci push-event range". |
| Parent unblock PR state | PR `#2166` merged into `dev` before running `done`. If CI still flaky, push a fresh fix commit to refresh CI. |
| No runtime expansion | This sidecar and parent unblock do not edit BFF runtime, OpenAPI, schemas, canonical docs, registry/governance, or `execute-plans`. |
| Followup-4 guidance forwarded | BFF implementation gaps (Q5–Q8) and error-code corrections remain forwarded to `Codex` (AG-BE-TR-002 owner) for absorption. This sidecar does not implement them. |
| Dependency honesty | `AG-BE-TR-001` and `AG-BE-TR-002` remain `todo` and blocked on the existing dependency chain; this sidecar does not declare them unblocked. |
| Status honesty | This sidecar packet is not a review approval of the parent unblock task; it is advisory support. |

---

## 9. Verification Performed

| Command | Result |
|---|---|
| `git branch --show-current` | `task/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S-SIDECAR-BFF-HANDOFF` |
| `git status --short` | Only `.orchestrator/task-briefs/...` untracked before packet creation. |
| `AI_NAME=Claude python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S-SIDECAR-BFF-HANDOFF` | Status `in_progress`; owner `Claude`; reviewer `Claude2`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S` | Parent task status `review_approved`; owner `Claude2`; reviewer `Claude`; PR `#2166` open. |
| `AI_NAME=Claude python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-SIDECAR-BFF-HANDOFF` | Dependent sidecar: `done` (archived); PR `#2160` MERGED at `2026-06-21T23:06:24Z`; all CI SUCCESS. |
| `gh pr view 2160 --json number,state,mergedAt,statusCheckRollup` | PR `#2160`: `MERGED`; all checks `SUCCESS`. |
| `gh pr view 2162 --json number,state,mergedAt,commits` | PR `#2162`: `MERGED` at `2026-06-21T23:30:15Z`; includes CI push-event range reset commit. |
| `gh pr view 2166 --json number,state,statusCheckRollup` | PR `#2166`: `OPEN`; Commit trailers: one `FAILURE`, one `SUCCESS` (flaky). |

---

## 10. Handoff To Reviewer

Reviewer `Claude2`: please review this support-only packet for factual accuracy
and scope discipline. The recommended disposition is to approve the sidecar if
the PR/status facts match current state, while keeping parent unblock closeout
with the parent owner (Claude2).

Suggested reviewer command after approval:

```bash
AI_NAME=Claude2 \
  REVIEW_FILE=support/sidecars/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff approved: original ci-red for INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-SIDECAR-BFF-HANDOFF is resolved; PR #2160 MERGED into dev at 2026-06-21T23:06:24Z with all CI SUCCESS; CI push-event range false-positive fixed in PR #2162 (merged 2026-06-21T23:30:15Z); parent unblock PR #2166 open with flaky Commit-trailers CI (one FAIL, one PASS on same commit - not a genuine trailer violation); downstream BFF guidance (Q5-Q8: CommandType/ObjectType enum gaps, ReadSurfaceStore trading_intents gap, Management-plane push gap, test skeleton correction, D10 error-code mapping) remains forwarded to Codex as AG-BE-TR-002 owner without changing BFF runtime or canonical truth." \
  ./scripts/ai-status.sh approve INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S-SIDECAR-BFF-HANDOFF \
  "Support-only ci-red BFF/frontend handoff packet approved for parent unblock owner."
```

Suggested reviewer command if changes are required:

```bash
AI_NAME=Claude2 \
  ./scripts/ai-status.sh reopen INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-MISSING-PR-S-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, missing PR evidence, or handoff boundary issue required before approval."
```
