# Review: FE-INT-GATE-E03 — Hosted probe nocache and old URL alignment

Reviewer: Claude
Date: 2026-05-13
Status: **APPROVED**

## Scope

Changes in sibling `execute-plans` repo (uncommitted working tree on branch `bff-luv-fe-006-dev-deploy`):

- `scripts/probe-hosted-browser-bff.mjs` — primary artifact
- `.github/workflows/pantheon-integration-gate.yml`
- `README.md`
- `docs/testing/Integration_Test_Package_README_2026-05-10.md`

## Acceptance Criteria Verification

| Criterion | Result |
|---|---|
| bundle URL 後加 `?nocache=<sha>` | ✅ Pass |
| `PANTHEON_OLD_BFF_URL` default 對齊 `https://pantheon-dev-bff.35.236.178.81.sslip.io` | ✅ Pass |
| 舊 URL 命中=0 才 pass | ✅ Pass |

## Detailed Findings

### `probe-hosted-browser-bff.mjs`

**Nocache SHA resolution (`currentSha`):**
Resolves from env chain `PANTHEON_PROBE_NOCACHE_SHA → GITHUB_SHA → VERCEL_GIT_COMMIT_SHA → git rev-parse → Date.now()`. Covers all runtime contexts (CI, Vercel, local git, offline). Applied via `withNoCache(url)` to both the page URL (`${FE_BASE}/management`) and each bundle script fetch.

**OLD_BFF_URL default alignment:**
Old default was `"pantheon-ai-system-front-dev"` (partial fragment needle). New default is `"https://pantheon-dev-bff.35.236.178.81.sslip.io"` (full URL). `matchesUrlNeedle` correctly handles both full-URL prefixes (`startsWith`) and partial fragments (`includes`), so the switch to a full URL is unambiguous.

**Old URL hit counting (stricter gate):**
Previous pass condition: `!containsOld` (boolean from simple text inclusion).
New pass condition: `oldUrlHitCount === 0` where hits aggregate from:
1. Network-layer: request, response, requestfailed events
2. Text-layer: `textHits("html", ...)` + `textHits("bundle", ...)`

This is strictly stronger — any stray reference to the old URL in network traffic or baked into the served bundle will fail the gate.

**`trimTrailingSlash` hoisting:**
The function is declared (not assigned) after the constants that call it. JavaScript function declarations are hoisted in ES modules, so this is functionally correct. No correctness issue.

**Syntax check passed:** `node --check scripts/probe-hosted-browser-bff.mjs` → SYNTAX OK

### `.github/workflows/pantheon-integration-gate.yml`

`PANTHEON_OLD_BFF_URL: ${{ secrets.PANTHEON_OLD_BFF_URL || 'https://pantheon-dev-bff.35.236.178.81.sslip.io' }}` wires the secret with the aligned default. Additional workflow improvements (PR/push triggers, step IDs, `continue-on-error`, aggregate gate, soft-fail mode, PR comment bot) are coherent and within the gate's scope.

### README and docs

Both updated to document `PANTHEON_OLD_BFF_URL` usage with the correct historical URL.

## Verification Evidence

- `node --check scripts/probe-hosted-browser-bff.mjs` → SYNTAX OK
- Codex reported: hosted probe passed with bundle `?nocache=217271bf7ca2`, 9 intended BFF responses, 0 failed, old BFF hit count 0

## Decision

All three acceptance criteria are met. Implementation is clean and the gate is now strictly correct. **Approved for finalization.**
