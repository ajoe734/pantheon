# FE-INT-GATE-B08 - Sidecar Review Packet

**Packet type:** review_packet (sidecar support artifact)  
**Sidecar task:** FE-INT-GATE-B08-SIDECAR-REVIEW  
**Parent task:** FE-INT-GATE-B08  
**Helper kind:** review_packet  
**Prepared by:** Codex  
**Reviewer:** Codex2  
**Date:** 2026-05-14  
**Mutates canonical:** false  
**Parent disposition at packet creation:** done and archived  
**Final disposition:** Approved by Codex2; owner closeout finalized by Codex

---

## Purpose

This sidecar packet summarizes the existing review and evidence for FE-INT-GATE-B08. It is a support artifact only. It does not change L1 canonical truth, core contract truth, runtime implementation, registry behavior, governance policy, or the parent test artifact.

The parent task is already terminal. This packet is therefore a reviewer handoff and evidence index, not a request to modify or re-open the parent implementation.

---

## 1. Parent Task Summary

| Field | Value |
|---|---|
| Task ID | FE-INT-GATE-B08 |
| Title | F15 deepen - 5xx injection strict vs hybrid |
| Phase | Pantheon FE Integration Gate 2026-05-13 |
| Owner | Codex |
| Reviewer | Claude |
| Terminal status | done |
| Archived at | 2026-05-14T00:45:45Z |
| Task commit | `ddb8d006c43e66a8cf82c3b1639b0f0fde639164` |
| Commit subject | `FE-INT-GATE-B08: finalize strict hybrid fallback spec` |
| Primary artifact | `execute-plans/e2e/09-strict-vs-hybrid.spec.ts` |
| Review file | `.orchestrator/reviews/FE-INT-GATE-B08-review-claude.md` |
| Archive snapshot | `ai-task-archive/tasks/FE-INT-GATE-B08.json` |

**Scope (summary_zh):** F15 strict vs hybrid upgrade: use `page.route` to inject 5xx; strict mode fails closed; hybrid mode shows `LiveBffBanner`; 4xx `BffError` does not fallback; strict mode shows no mock data.

---

## 2. Artifact Under Review

**Primary artifact:** `execute-plans/e2e/09-strict-vs-hybrid.spec.ts`

The file contains a Playwright E2E suite, `F15 strict vs hybrid fallback`, with three tests:

1. `hybrid 5xx injection falls back to mock with a visible live-BFF banner`
2. `strict 5xx injection fails closed without showing mock data`
3. `4xx BffError envelope never falls back to mock`

The suite uses exact-path BFF route injection for `/bff/strategies`, stable shell route stubs, an EventSource quiet stub, CORS-aware route fulfillment, and strict/hybrid branch selection from `VITE_BFF_FALLBACK`, `BFF_FALLBACK`, or `PANTHEON_E2E_STRICT`.

---

## 3. Acceptance Criteria Assessment

| Criterion | Status | Evidence |
|---|---|---|
| strict 5xx injection fails closed | PASS | The strict branch injects HTTP 503 on `/bff/strategies`, waits for that response, asserts visible `STRICT_ERROR_TEXT`, and calls `expectNoSeedFallback(page)`. The strict branch is selected by `VITE_BFF_FALLBACK=strict`, `BFF_FALLBACK=strict`, or `PANTHEON_E2E_STRICT=1`. |
| hybrid 5xx injection shows `LiveBffBanner` | PASS | The hybrid branch injects HTTP 503, waits for the injected response, asserts a visible role `status` matching `FALLBACK_ACTIVE_TEXT`, and verifies the seed strategy `Momentum Quant Alpha` is visible. |
| 4xx `BffError` does not fallback | PASS | The 4xx test injects HTTP 409 with a `STATE_CONFLICT` envelope, waits for the 409 response, asserts no `STRICT_ERROR_TEXT`, and calls `expectNoSeedFallback(page)`. |
| strict mode shows no mock data | PASS | `expectNoSeedFallback(page)` asserts both the fallback banner text and the known seed strategy text have count 0. Claude's review also called out this criterion as covered by the strict test. |

**Overall evidence verdict:** PASS. The original reviewer approved the parent artifact, and the parent task was finalized as done.

---

## 4. Technical Evidence Detail

### 4.1 Exact Route Injection

`exactPath(path)` matches `url.pathname === path`, so the injected behavior is scoped to the intended BFF route. The strategies assertion route is `STRATEGIES_ROUTE = "/bff/strategies"`.

### 4.2 CORS and Preflight Handling

`corsHeaders(route)` echoes the request origin, enables credentialed browser requests, exposes `X-BFF-Api-Version`, `X-Correlation-Id`, and `X-Request-Id`, and supports `OPTIONS` preflight through HTTP 204 fulfillment.

### 4.3 Stable Shell Routes

`installStableShellRoutes(page)` stubs `/health`, `/bff/me`, `/bff/approvals`, `/bff/alerts`, `/bff/jobs`, and `/bff/search`, preventing unrelated shell fetches from determining the test result.

### 4.4 Ancillary Route Isolation

`isolateOtherAncillaryBffRoutes(page)` returns typed HTTP 409 `STATE_CONFLICT` envelopes for `/bff/incidents` and `/bff/audit`. This keeps non-strategy BFF calls from activating mock fallback while the test focuses on the injected strategies route.

### 4.5 EventSource Noise Control

`installQuietEventSource(page)` installs a minimal browser-side `EventSource` replacement before app code runs. This prevents SSE connection noise from interfering with strict/hybrid fallback assertions.

### 4.6 Response Synchronization

`gotoStrategiesAndWaitForInjectedStatus(page, expectedStatus)` starts `page.waitForResponse(...)` before navigation, then waits for the injected status and a short render settle period before assertions.

### 4.7 Error Envelope Shape

`bffErrorEnvelope()` emits a structured `error` object with `code`, `i18nKey`, `message`, `retryable`, `userActionable`, and `correlationId`. The suite uses `BACKEND_UNAVAILABLE` for injected 5xx and `STATE_CONFLICT` for governed 4xx.

---

## 5. Execution and Review Evidence

### 5.1 Parent Closeout Evidence

The parent archive records the final closeout message:

```text
Closed out FE-INT-GATE-B08 with task commit ddb8d006. Verification: Playwright discovery listed 3 tests; esbuild bundle passed; hybrid live run passed 2/3 with 1 strict skip; strict live run passed 2/3 with 1 hybrid skip.
```

The task-scoped commit is:

```text
ddb8d006 FE-INT-GATE-B08: finalize strict hybrid fallback spec
```

It added:

```text
.orchestrator/reviews/FE-INT-GATE-B08-review-claude.md
execute-plans/e2e/09-strict-vs-hybrid.spec.ts
```

### 5.2 Original Reviewer Verdict

Claude's parent review verdict was approved. The review states that the spec covers all four acceptance criteria and specifically calls out:

- exact-path injection
- CORS handling
- EventSource stub
- mode branching
- 4xx no-fallback behavior
- strict no-seed assertion

### 5.3 Delivery Metadata Note

At the time of parent closeout, the archive recorded `push_status: ahead` on branch `backend-dev-publish-20260429`. This packet does not attempt to resolve parent publication state; it only records the delivery metadata visible in the parent archive snapshot.

---

## 6. Sidecar Validation Performed

Commands run while preparing this packet:

```bash
AI_NAME=Codex ./scripts/ai-status.sh show FE-INT-GATE-B08-SIDECAR-REVIEW
sed -n '1,260p' ai-task-archive/tasks/FE-INT-GATE-B08.json
sed -n '1,260p' .orchestrator/reviews/FE-INT-GATE-B08-review-claude.md
sed -n '1,380p' execute-plans/e2e/09-strict-vs-hybrid.spec.ts
git show --stat --oneline --decorate --no-renames ddb8d006
```

No runtime, registry, governance, or canonical contract files were edited.

Owner closeout validation after Codex2 approval:

```bash
jq '.tasks[] | select(.id=="FE-INT-GATE-B08-SIDECAR-REVIEW")' ai-status.json
sed -n '1,260p' support/sidecars/FE-INT-GATE-B08/FE-INT-GATE-B08-SIDECAR-REVIEW.md
git status --short support/sidecars/FE-INT-GATE-B08/FE-INT-GATE-B08-SIDECAR-REVIEW.md
git status --short
```

The task remained `review_approved` with Codex as owner and Codex2 as reviewer before finalization. The only task-owned repo artifact for this sidecar remains this support packet.

---

## 7. Scope Boundary

This sidecar changed only the support packet path:

```text
support/sidecars/FE-INT-GATE-B08/FE-INT-GATE-B08-SIDECAR-REVIEW.md
```

This packet intentionally does not:

- modify `execute-plans/e2e/09-strict-vs-hybrid.spec.ts`
- modify L1 canonical policy or contract documents
- modify service runtime code
- modify registry or governance implementation
- re-open, supersede, or reinterpret the parent task

---

## 8. Review Result And Closeout Note

Codex2 approved this sidecar on 2026-05-14T01:13:55Z:

```text
Approved: sidecar review packet summarizes FE-INT-GATE-B08 evidence and preserves support-only scope. Owner Codex should finalize to done.
```

No reviewer follow-up was requested. Owner closeout is limited to preserving this support artifact, creating a task-scoped commit, and moving `FE-INT-GATE-B08-SIDECAR-REVIEW` from `review_approved` to `done`.
