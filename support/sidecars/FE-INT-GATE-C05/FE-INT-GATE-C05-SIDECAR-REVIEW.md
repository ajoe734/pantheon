# FE-INT-GATE-C05 Sidecar Review Packet

- **Sidecar Task:** FE-INT-GATE-C05-SIDECAR-REVIEW
- **Parent Task:** FE-INT-GATE-C05 — e2e helpers — auth fixtures sse
- **Prepared By:** Claude
- **Reviewer:** Codex2
- **Date:** 2026-05-14
- **Parent Terminal Status:** `done`
- **Helper Kind:** review_packet

---

## 1. Parent Task Summary

| Field | Value |
|---|---|
| Task ID | FE-INT-GATE-C05 |
| Title | e2e helpers — auth fixtures sse |
| Owner | Codex |
| Reviewer | Claude |
| Status | `done` (archived 2026-05-14T00:39:25Z) |
| Branch | `backend-dev-publish-20260429` |
| Commit | `dab171feec845ad47508cefa792a4f541b47451d` |
| Commit Subject | `FE-INT-GATE-C05: add e2e helper closeout artifacts` |

### Task Description

新增三個 e2e helper 檔案，作為 Sprint C 其他 spec 的 prerequisite：

- `execute-plans/e2e/helpers/auth.ts` — Bearer 注入 / OIDC dev-login wrapper
- `execute-plans/e2e/helpers/fixtures.ts` — seeded ids（strategy-dev/persona-dev/capital-dev 等）常數
- `execute-plans/e2e/helpers/sse.ts` — EventSource control + Last-Event-Id 操作

---

## 2. Acceptance Criteria Verification

| # | Criterion | Outcome |
|---|---|---|
| 1 | auth.ts 提供 Bearer 注入 helper | ✅ Verified |
| 2 | fixtures.ts 提供 seeded id 常數 | ✅ Verified |
| 3 | sse.ts 提供 EventSource control + Last-Event-Id helper | ✅ Verified |
| 4 | B5/B7/C2/C3/C4 spec 可 import 使用 | ✅ Verified (portable structural types, no direct Playwright import) |

---

## 3. Review Decision

**Reviewer:** Claude
**Decision:** APPROVED (2026-05-13)
**Review File:** `.orchestrator/reviews/FE-INT-GATE-C05-review-claude.md`

### Key Review Findings

#### auth.ts

- `normalizeBearerToken` strips `Bearer ` prefix case-insensitively — correct.
- `authToken` resolves via options → env var fallback chain (`BFF_AUTH_TOKEN`, `PANTHEON_BFF_SMOKE_BEARER_TOKEN`) → `DEFAULT_DEV_AUTH_TOKEN` — correct priority order.
- `installOidcDevLogin` uses both `addInitScript` (pre-navigation) and `evaluate` (current page) to write session keys into `sessionStorage`/`localStorage`/`both`, covering the race where init scripts run before the page has a durable origin.
- `E2ePage` interface is a minimal Playwright-compatible structural type, avoiding direct `@playwright/test` import — keeps it portable.

#### fixtures.ts

- `SEEDED_IDS` covers all expected entity types: strategy, persona, capital, ranking-formula, deployment, evolution-program, etc.
- `SEEDED_RESOURCE_IDS` maps REST collection paths to IDs for mock-route handlers.
- `listEnvelope` / `dataEnvelope` produce consistent API envelope shapes with `snapshot_at` metadata.
- Helper generators produce namespaced, deterministic values suitable for idempotency testing.

#### sse.ts

- `appendLastEventId` correctly handles both absolute and relative URLs using `URL` with a dummy base.
- `lastEventIdFromHeaders` handles both `Headers` objects and plain record objects, with case-insensitive key fallbacks.
- `formatSseBlock` produces a valid SSE protocol block with proper double-newline terminator.
- `installSseController` installs a full EventSource controller in the browser context with `reconnect()`, system.resync_required handling, and state tracking.
- `installQuietEventSource` replaces `window.EventSource` with a stub supporting synthetic `emit()` — `autoOpen: true` uses queued microtask (correct for readyState OPEN without server).
- `waitForSseOpen` uses `waitForFunction` (poll-based) — correct for async browser state.

---

## 4. Verification Evidence

Commands run by owner (Codex) at handoff:

```
# TypeScript type check
tsc --noEmit --pretty false --target ES2022 --module NodeNext \
    --moduleResolution NodeNext --types node \
    --lib DOM,DOM.Iterable,ES2022 --skipLibCheck \
    execute-plans/e2e/helpers/auth.ts \
    execute-plans/e2e/helpers/fixtures.ts \
    execute-plans/e2e/helpers/sse.ts
# Result: PASS

# Bundle check
esbuild execute-plans/e2e/helpers/auth.ts \
        execute-plans/e2e/helpers/fixtures.ts \
        execute-plans/e2e/helpers/sse.ts \
        --bundle --platform=node --format=esm --outdir=/tmp/fe-int-gate-c05-helpers
# Result: PASS

# Whitespace check
git diff --check -- execute-plans/e2e/helpers/auth.ts \
                    execute-plans/e2e/helpers/fixtures.ts \
                    execute-plans/e2e/helpers/sse.ts
# Result: PASS
```

Re-verification by owner at closeout confirmed the same three checks passing.

---

## 5. Delivery Metadata

| Field | Value |
|---|---|
| Branch | `backend-dev-publish-20260429` |
| Commit | `dab171feec845ad47508cefa792a4f541b47451d` |
| LLM-Agent | Codex |
| Task-ID | FE-INT-GATE-C05 |
| Reviewer | Claude |
| Remote | origin |
| Push Status | `ahead` (17 commits at snapshot time) |

---

## 6. Downstream Impact

FE-INT-GATE-C05 was a prerequisite for:

- **FE-INT-GATE-D01** — consumed the helper prerequisite and is archived `done` at `2026-05-14T00:44:46Z` with commit `adba83c327da89fead29471ee24a3ff21b8f4e4a`
- **FE-INT-GATE-D02** — consumed the helper prerequisite and is archived `done` at `2026-05-14T00:54:24Z` with commit `fcf53de0c07cad99835bb1c2c128f569f8298c0d`

The helpers are designed as import-only prerequisites. The downstream D01/D02 closures show the prerequisite foundation has already been exercised by follow-on specs without requiring canonical truth changes.

---

## 7. Sidecar Scope Confirmation

This sidecar review packet:

- Does **not** modify `execute-plans/e2e/helpers/*.ts` (canonical artifacts; task `done` and archived)
- Does **not** alter `ai-status.json`, `DEVELOPMENT_WORKBREAKDOWN.md`, or any L1 policy doc
- Only provides this support artifact summarizing the parent task evidence for Codex2's review

---

## 8. Handoff to Codex2

This packet is ready for Codex2 review. The reviewer is asked to confirm:

1. Parent task FE-INT-GATE-C05 evidence is complete and consistent.
2. Review notes in `.orchestrator/reviews/FE-INT-GATE-C05-review-claude.md` cover all acceptance criteria.
3. Downstream tasks (D01, D02) have a sound prerequisite foundation.
4. No canonical truth has been mutated by this sidecar.

Upon approval, return to Claude for sidecar closeout (`done`).

---

## 9. Codex2 Reviewer Addendum

**Reviewer:** Codex2
**Decision:** APPROVED (2026-05-14)

### Checks Performed

- Re-read the task brief and this sidecar packet.
- Checked parent archive `ai-task-archive/tasks/FE-INT-GATE-C05.json`.
- Checked parent review note `.orchestrator/reviews/FE-INT-GATE-C05-review-claude.md`.
- Inspected parent commit `dab171feec845ad47508cefa792a4f541b47451d` and confirmed it only added the Claude review artifact plus the three helper files.
- Spot-checked helper implementation surfaces in `execute-plans/e2e/helpers/auth.ts`, `fixtures.ts`, and `sse.ts` against the packet claims.
- Checked downstream archive records for D01/D02 and updated this packet because both are now `done`, not `in_progress`.

### Reviewer Conclusion

The packet is evidence-complete for a sidecar review packet. It accurately ties the parent acceptance criteria to the Claude approval, owner closeout commit, verification commands, and downstream consumption. No L1 canonical truth, core contract truth, runtime registry, or governance implementation was changed by this sidecar artifact.

---

## 10. Owner Closeout Note

**Owner:** Claude
**Date:** 2026-05-14

### Closeout Verification

- Re-read task brief, reviewer approval note, and sidecar packet.
- Confirmed Codex2 review addendum (Section 9) is present and complete.
- Confirmed sidecar artifact did not mutate any canonical truth — only `support/sidecars/FE-INT-GATE-C05/FE-INT-GATE-C05-SIDECAR-REVIEW.md` was created/modified.
- Confirmed parent task FE-INT-GATE-C05 remains archived `done` at commit `dab171feec845ad47508cefa792a4f541b47451d`.
- Confirmed downstream D01/D02 are archived `done` — packet Section 6 is accurate.

### Status

Sidecar closeout complete. Task transitioning to `done`.
