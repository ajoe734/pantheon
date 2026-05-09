# BFF-LUV-FE-006 Sidecar BFF Handoff Packet

Task ID: BFF-LUV-FE-006-SIDECAR-BFF-HANDOFF
Parent Task: BFF-LUV-FE-006
Helper kind: bff_handoff_packet
Owner: Codex2
Reviewer: Codex
Prepared: 2026-05-09T18:03:03Z

## Scope

Support-only sidecar for BFF-LUV-FE-006. This packet does not define canonical
architecture, change route truth, or modify runtime/frontend implementation. It
organizes the final deploy/E2E closure dependency gate, remaining BFF query
gaps, operator journey, and frontend handoff notes for the parent owner to
absorb or ignore.

Current parent state at packet time:

- Parent owner: Codex.
- Parent reviewer: Claude.
- Parent status: `todo`.
- Parent artifact:
  `docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-006-dev-deploy-e2e-closure.md`.
- Closure dependency rule: do not deploy until all dependencies are `done` or
  have an explicit approved blocker disposition.

## Dependency Gate Snapshot

| Dependency | Current state | Closure impact | Source |
|---|---|---|---|
| `BFF-LUV-FE-001` | `done` | Transport/session foundation is available: `VITE_BFF_MODE`, `VITE_BFF_BASE_URL`, `VITE_BFF_FALLBACK`, `VITE_BFF_REAL_WRITES`, bearer-token storage, `/bff/me`, health, and live-status reporting. | `support/sidecars/BFF-LUV-FE-001/BFF-LUV-FE-001-SIDECAR-BFF-HANDOFF.md` |
| `BFF-LUV-FE-002` | `done` | Management Console read adapters cover 20 list families plus detail readers except audit list-only behavior. Authenticated DTO evidence is still not proven by FE-002 itself. | `support/sidecars/BFF-LUV-FE-002/BFF-LUV-FE-002-SIDECAR-BFF-HANDOFF.md` |
| `BFF-LUV-FE-003` | `done` | Agora/v5 strict live reads and EventSource SSE bridge are wired. Review note says `/bff/events/stream` is currently liveness-only for browser EventSource until cookie-backed privileged SSE auth exists. | `docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-003-agora-v5-realtime.md` |
| `BFF-LUV-FE-004` | `in_progress` | Safe write flows are not yet a closure dependency that FE-006 can assume complete. Latest artifact records rev4 live adapters and tests, but task state remains active. | `docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-004-safe-write-flow.md` |
| `BFF-LUV-FE-005` | `todo` | Lovable cutover smoke has not started. FE-006 should not treat final hosted frontend evidence as already published. | `docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-005-lovable-cutover-smoke.md` |
| `BFF-LUV-AUTHED-LIVE-001` | `blocked` | Authenticated live DTO/write smoke is the hard gate. Current blocker is missing valid lupin-dev JWT Bearer token or approved auth-stub window. | `docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-blocker-20260509.md` |

## Source Snapshot For Parent Closure

| Surface | Current state | Source |
|---|---|---|
| Execute-plans env docs | README documents `live + auto` shared/dev fallback, `live + strict` staging-live, browser bearer-token keys, and `VITE_BFF_REAL_WRITES=false` default. | `/home/lupin/code/execute-plans/README.md` |
| Live transport | `withLiveOrMock` propagates 4xx, falls back only on network/5xx in auto mode, and throws typed errors in strict mode. | `/home/lupin/code/execute-plans/src/lib/bff-v1/liveTransport.ts` |
| Live status | `liveStatus` records configured mode, effective mode, fallback reason/time, API version mismatch, request id, and correlation id. | `/home/lupin/code/execute-plans/src/lib/bff-v1/liveStatus.ts` |
| Management reads | `managementClient` exposes list readers for `MANAGEMENT_FAMILIES` and detail readers for non-audit families. | `/home/lupin/code/execute-plans/src/lib/bff/client.ts` |
| Management list semantics | List classes distinguish exact entity/governance counts from audit/realtime estimated feeds. | `/home/lupin/code/execute-plans/src/lib/bff-v1/lists.ts` |
| Agora reads | `bffAgora` uses strict live-or-mock adapters for daily, signals, inbox, journal, and ask sessions. | `/home/lupin/code/execute-plans/src/lib/bff/agora.ts` |
| v5 reads | `bffV5` uses strict live-or-mock adapters for control room, loop runs, persona health, strategy health, sentinel findings, and interventions. | `/home/lupin/code/execute-plans/src/lib/bff/v5.ts` |
| SSE bridge | `connectLiveSse` opens EventSource to `/bff/events/stream`, sends `lastEventId` as query replay, uses `withCredentials: true`, and bridges typed events onto the legacy realtime bus. | `/home/lupin/code/execute-plans/src/lib/bff-v1/sse/liveSse.ts` |
| Safe write seam | `runAction.ts` gates live writes on `VITE_BFF_REAL_WRITES=true` plus browser auth, then normalizes action, confirm-token, approval, alert, and intervention writes into frontend envelopes. | `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts` |
| Public BFF registration evidence | Lupin dev BFF route smoke proved `/openapi.json` and 113 anonymous contract routes avoid 404/500, but 111 protected routes returned 401. | `docs/bff/evidence/BFF-LUV-SEM-006-lupin-dev-live-probe-20260509T113136Z.json` |
| Auth blocker | Stub token is rejected by strict auth (`AUTH_TOKEN_FORMAT`); GCP reauth fails non-interactively. | `docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-blocker-20260509.md` |

## BFF Query Gap Matrix

These are the gaps FE-006 should explicitly disposition before deployment
closure. They are not new canonical contract claims.

| Gap | Current evidence | Why it matters for FE-006 | Suggested absorption |
|---|---|---|---|
| Authenticated DTO proof | Anonymous route registration exists; protected routes mostly return 401. `BFF-LUV-AUTHED-LIVE-001` is blocked on a valid JWT Bearer token or auth-stub window. | FE-006 acceptance requires deployed frontend requests to reach intended dev BFF and return expected `2xx` or governed auth outcomes. Registration-only 401 evidence is insufficient for live DTO cutover. | Treat as a hard preflight gate. If auth remains blocked, publish one closure blocker naming owner/action instead of deploying as complete. |
| Write-flow proof | FE-004 artifact records rev4 normalization tests, but task state is still `in_progress`. | FE-006 cannot claim `VITE_BFF_REAL_WRITES=true` is allowed until FE-004 and AUTHED-LIVE finish or receive approved blocker disposition. | Keep `VITE_BFF_REAL_WRITES=false` in deployment unless FE-004 and authenticated non-capital write smoke are approved. |
| Lovable cutover evidence | FE-005 is `todo`. | FE-006 depends on hosted Lovable/live smoke evidence and exact commit/env handoff. | Do not duplicate FE-005. Consume its evidence once available; otherwise list it as an explicit predecessor blocker. |
| Hybrid fallback ambiguity | Shared/dev envs default to `VITE_BFF_FALLBACK=auto`, which can return mock data after transport/5xx failure while `liveStatus` records fallback. | Operators may read seed data as live data if FE-006 only checks UI row presence. | For closure evidence, record `mode`, `effective`, `lastError`, and network status. Run at least one strict-mode route smoke so "real" means no silent mock. |
| Management detail breadth | FE-002 covers detail path construction, but authenticated live detail DTO shape across all non-audit families is not proven. | Deployed UI detail drawers can fail even if list pages render. | In the FE-006 smoke, choose IDs from live lists and call representative detail drawers or client calls. Label empty-list families separately. |
| Realtime/SSE auth | FE-003 wires EventSource with cookies and replay query; review notes browser EventSource is liveness-only until cookie-backed privileged SSE auth exists. | SSE can show open/error behavior without proving privileged event payload access. | Capture EventSource network state. If auth blocks payloads, record governed 401/close behavior as fallback rather than marking realtime fully live. |
| Public target selection | README names shared dev and staging-live URLs; AUTHED-LIVE evidence targets `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`. | FE-006 acceptance needs the deployed frontend target URL and intended BFF URL to match. | Record exact `VITE_BFF_BASE_URL` from the deployed environment and compare it to the BFF smoke target in the evidence packet. |
| Dirty worktrees | At packet time, `execute-plans` has modified `src/lib/bff-v1/writes.ts` and `src/lib/bff-v1/__tests__/writes.test.ts`; `pantheon` has unrelated generated state/archive dirtiness. | FE-006 acceptance requires clean, committed, pushed branches in both repos. | Parent owner should separate task-owned changes from unrelated generated state, then record exact commit hashes and push status before deploy. |

## Operator E2E Journey For FE-006

Use this as a closure smoke script after dependency gates are satisfied or
explicitly dispositioned.

1. Preflight task board:
   - Confirm `BFF-LUV-FE-001`, `BFF-LUV-FE-002`, `BFF-LUV-FE-003`, `BFF-LUV-FE-004`,
     `BFF-LUV-FE-005`, and `BFF-LUV-AUTHED-LIVE-001` are `done`, or record the
     exact approved blocker disposition.
   - Confirm no active BFF-LUV frontend/live tasks remain except approved blockers.
2. Repo closure:
   - In `pantheon`, record branch, `git rev-parse HEAD`, `git status --short`,
     upstream, ahead/behind, and push result.
   - In `execute-plans`, record branch, `git rev-parse HEAD`, `git status --short`,
     upstream, ahead/behind, and push result.
3. Dev BFF probe:
   - Probe `/health` and `/openapi.json` on the intended `VITE_BFF_BASE_URL`.
   - Probe one protected route anonymously, preferably `/bff/me`, and record the
     governed auth outcome. A 401 is acceptable only as anonymous-auth evidence,
     not as authenticated DTO evidence.
4. Deploy env confirmation:
   - Record deployment target URL.
   - Record `VITE_BFF_MODE`, `VITE_BFF_BASE_URL`, `VITE_BFF_FALLBACK`,
     `VITE_BFF_REAL_WRITES`, and auth/session configuration.
   - For final live evidence, prefer `VITE_BFF_MODE=live` and
     `VITE_BFF_FALLBACK=strict` where the operator must prove no silent mock.
5. Session bootstrap smoke:
   - Use a valid operator browser session or set
     `sessionStorage["pantheon.bff.bearerToken"]` with a redacted source.
   - Load the deployed frontend and verify `GET /bff/me` reaches the intended BFF.
   - Record status, response shape fields only, and `liveStatus` snapshot. Do not
     store tokens, cookies, PII, or full payloads.
6. Management Console read smoke:
   - Navigate to a Management Console read page backed by `managementClient`.
   - Capture network calls for `/bff/strategies`, `/bff/personas`,
     `/bff/capital-pools`, `/bff/approvals`, `/bff/alerts`, `/bff/incidents`,
     `/bff/audit`, and one catalog family such as `/bff/mcp-tools` or `/bff/skills`.
   - For one non-audit family with items, open a detail drawer and record the
     detail route/status.
7. Agora/v5 read smoke:
   - Navigate to an Agora page backed by `bffAgora` and capture
     `/bff/agora/signals`, `/bff/agora/inbox`, or `/bff/agora/ask/sessions`.
   - Navigate to v5/control surfaces and capture `/bff/v5/loop-runs`,
     `/bff/v5/sentinel/findings`, `/bff/v5/interventions`, and one health route.
8. Realtime/SSE smoke:
   - Capture the EventSource request to `/bff/events/stream`.
   - Record whether it opens, errors, or returns a governed auth failure.
   - If it opens, record only event ids/channels/schema fields, not sensitive
     payloads.
9. Safe write smoke:
   - Keep `VITE_BFF_REAL_WRITES=false` unless FE-004 and AUTHED-LIVE explicitly
     approve a non-capital write fixture.
   - With writes disabled, verify attempted write paths stay in frontend
     mock/overlay and do not emit live network mutations.
   - If approved writes are enabled, use only non-capital fixtures such as
     confirm-token lifecycle, alert acknowledge, or an approved intervention
     decision. Record idempotency/correlation headers and normalized envelope.
10. Fallback-negative smoke:
   - Run one representative route with an unreachable BFF in auto mode and
     verify fallback is visible through `liveStatus`.
   - Run the same route in strict mode and verify typed error behavior instead
     of seeded mock data.

Do not run deployed smoke against live-capital side-effect routes:

- strategy deploy/promote/pause/resume/rollback/emergency-kill actions;
- deployment create/patch;
- capital allocation or rebalance mutations;
- any route that can emit a broker order or change real capital exposure.

## Frontend Handoff Notes

- Treat `/home/lupin/code/execute-plans/README.md` as the current env handoff
  for Lovable/Pantheon BFF mode selection.
- Treat `/home/lupin/code/execute-plans/src/lib/bff/client.ts` as the
  Management Console read seam. Do not make page components fetch BFF directly.
- Treat `/home/lupin/code/execute-plans/src/lib/bff/agora.ts` and
  `/home/lupin/code/execute-plans/src/lib/bff/v5.ts` as the Agora/v5 read seams.
- Treat `/home/lupin/code/execute-plans/src/lib/bff-v1/sse/liveSse.ts` as the
  browser EventSource seam. It uses cookie credentials but cannot attach bearer
  headers because browser EventSource does not support arbitrary headers.
- Treat `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts` as the safe
  write seam. Deployment should leave writes off unless the parent owner has
  reviewed auth/write smoke evidence.
- UI verification should capture the live-status banner or equivalent
  `liveStatus` snapshot. Row rendering alone does not prove BFF live mode.
- Any `4xx` from BFF should be recorded as a governed backend outcome, not
  converted to mock fallback. Auto fallback should only mask network/5xx
  failures and must be visibly labeled.
- Evidence should record field names and status codes, not full protected DTOs.

## Evidence Packet Skeleton

Recommended evidence file under `docs/bff/evidence/`:

```markdown
# BFF-LUV-FE-006 Dev Deploy E2E Closure Evidence

- Generated at:
- Pantheon branch / commit / push status:
- Execute-plans branch / commit / push status:
- Deployed frontend URL:
- BFF target URL:
- Env:
  - VITE_BFF_MODE:
  - VITE_BFF_BASE_URL:
  - VITE_BFF_FALLBACK:
  - VITE_BFF_REAL_WRITES:
- Auth source: redacted bearer / cookie / approved blocker
- Smoke commands:
- Browser/network route summary:

| Surface | Route | Expected | Actual status | Shape fields | Decision |
|---|---|---|---|---|---|
| health | /health | 200 | | | |
| openapi | /openapi.json | 200 | | | |
| session | /bff/me | 2xx with auth or governed 401 anonymous | | | |
| management | /bff/strategies | 2xx with auth | | | |
| management | /bff/approvals | 2xx with auth | | | |
| agora | /bff/agora/signals | 2xx with auth | | | |
| v5 | /bff/v5/loop-runs | 2xx with auth | | | |
| realtime | /bff/events/stream | open or governed auth disposition | | | |
| write gate | no mutation when REAL_WRITES=false | no live POST | | | |

- Pass/fail decision for VITE_BFF_MODE=live:
- Pass/fail decision for VITE_BFF_REAL_WRITES=true:
- Remaining blockers:
```

## Parent Absorption Checklist

Before BFF-LUV-FE-006 can move to review, parent owner should confirm:

- All dependencies are `done` or have one consolidated approved blocker
  disposition.
- `BFF-LUV-AUTHED-LIVE-001` authenticated DTO/write evidence is present, or the
  closure blocker explicitly says live cutover cannot be completed.
- `BFF-LUV-FE-004` safe write task is not assumed complete while still
  `in_progress`.
- `BFF-LUV-FE-005` Lovable cutover smoke evidence is either consumed or named as
  a blocker.
- Both repos have exact commit hashes, clean-status disposition, and push status.
- Deployed frontend evidence proves requests go to the intended BFF URL.
- `VITE_BFF_MODE=live` and `VITE_BFF_REAL_WRITES=true` each have independent
  pass/fail decisions.
- No L1 canonical truth or backend route registry changes are inferred from this
  sidecar packet.

## Verification Notes For This Sidecar

No runtime, canonical, or frontend implementation was changed by this sidecar.
Verification for the packet consisted of source inspection only:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show BFF-LUV-FE-006-SIDECAR-BFF-HANDOFF
AI_NAME=Codex2 ./scripts/ai-status.sh show BFF-LUV-FE-006
AI_NAME=Codex2 ./scripts/ai-status.sh show BFF-LUV-AUTHED-LIVE-001
AI_NAME=Codex2 ./scripts/ai-status.sh show BFF-LUV-FE-003
AI_NAME=Codex2 ./scripts/ai-status.sh show BFF-LUV-FE-004
AI_NAME=Codex2 ./scripts/ai-status.sh show BFF-LUV-FE-005
sed -n '1,260p' docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-006-dev-deploy-e2e-closure.md
sed -n '1,280p' docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-AUTHED-LIVE-001-authenticated-dto-write-smoke.md
sed -n '1,220p' docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-blocker-20260509.md
sed -n '1,220p' /home/lupin/code/execute-plans/README.md
sed -n '1,280p' /home/lupin/code/execute-plans/src/lib/bff-v1/liveTransport.ts
sed -n '1,220p' /home/lupin/code/execute-plans/src/lib/bff-v1/liveStatus.ts
sed -n '1,280p' /home/lupin/code/execute-plans/src/lib/bff/client.ts
sed -n '1,320p' /home/lupin/code/execute-plans/src/lib/bff/agora.ts
sed -n '1,760p' /home/lupin/code/execute-plans/src/lib/bff/v5.ts
sed -n '1,760p' /home/lupin/code/execute-plans/src/lib/bff/runAction.ts
sed -n '1,180p' /home/lupin/code/execute-plans/src/lib/bff-v1/sse/liveSse.ts
git status --short
git -C /home/lupin/code/execute-plans status --short
git diff --check -- support/sidecars/BFF-LUV-FE-006/BFF-LUV-FE-006-SIDECAR-BFF-HANDOFF.md
```

## Reviewer Handoff

Reviewer (Codex) should verify:

1. This packet is support-only and does not modify canonical truth, runtime
   implementation, registry state, or frontend implementation.
2. The dependency gate snapshot matches current task state, especially
   AUTHED-LIVE blocked, FE-004 in progress, and FE-005 todo.
3. The query gap matrix frames evidence and deployment blockers without
   redefining route truth.
4. The operator journey excludes live-capital side-effect smoke and distinguishes
   live DTO proof from route-registration 401 evidence.
5. Parent owner can use this packet as advisory input without treating it as an
   approved replacement for the BFF-LUV-FE-006 implementation record.

This packet is ready for Codex review and parent-owner absorption decision.
